#!/usr/bin/env python3
"""Real Grafana on an isolated Docker network, with synthetic Loki/Slack only."""
import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
PASSWORD = "fixture-only-admin"
IMAGE = "grafana/grafana:13.2.3"
HELPER = "python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8"
CHANNEL = "CFIXTURE1234"
BOT_TOKEN = "fixture-not-a-real-token"


def server():
    state = {"mode": "normal", "deliveries": [], "queries": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/state":
                return self.reply(state)
            if path in ("/loki/api/v1/query", "/loki/api/v1/query_range"):
                state["queries"] += 1
                if state["mode"] == "error":
                    return self.reply({"status": "error", "errorType": "internal",
                                       "error": "deliberate fixture query failure"}, 500)
                rows = [] if state["mode"] == "normal" else [{"metric": {}, "value": [time.time(), "1"]}]
                return self.reply({"status": "success", "data": {"resultType": "vector", "result": rows}})
            if path.startswith("/loki/"):
                return self.reply({"status": "success", "data": []})
            return self.reply({}, 404)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path == "/mode":
                mode = json.loads(body)["mode"]
                assert mode in ("normal", "firing", "error")
                state["mode"] = mode
                return self.reply({})
            if self.path.endswith("/chat.postMessage") or self.path.endswith("/chat.update"):
                assert self.headers.get("Authorization") == "Bearer " + BOT_TOKEN
                value = json.loads(body)
                attachments = value["attachments"]
                if isinstance(attachments, str):
                    attachments = json.loads(attachments)
                title = attachments[0]["title"]
                assert title in ("firing: OpenWrt security", "resolved: OpenWrt security")
                assert value["channel"] == CHANNEL
                state["deliveries"].append({"resolved": title.startswith("resolved:"), "channel": value["channel"]})
                return self.reply({"ok": True, "channel": CHANNEL, "ts": str(time.time())})
            return self.reply({}, 404)

    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


def client(*, muted=False):
    def request(base, path, value=None):
        headers = {}
        if base.startswith("http://grafana-fixture:"):
            headers["Authorization"] = "Basic " + base64.b64encode(("admin:" + PASSWORD).encode()).decode()
        data = None if value is None else json.dumps(value).encode()
        headers["Content-Type"] = "application/json"
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=10) as r:
            body = r.read()
            return json.loads(body) if body else None

    gf, backend = "http://grafana-fixture:3000", "http://fixture-backend:8080"

    def until(action, predicate, timeout=100):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = action()
                if predicate(last):
                    return last
            except (OSError, KeyError, IndexError, StopIteration):
                pass
            time.sleep(2)
        raise AssertionError("fixture condition timeout: " + str(last)[:500])

    until(lambda: request(gf, "/api/health"), lambda x: x["database"] == "ok")
    datasource = request(gf, "/api/datasources/uid/prod-loki")
    assert datasource["url"] == "http://fixture-backend:8080"
    assert request(gf, "/api/dashboards/uid/openwrt-security")["dashboard"]["uid"] == "openwrt-security"
    policy = request(gf, "/api/v1/provisioning/policies")
    exported = request(gf, "/api/v1/provisioning/alert-rules")
    assert len(exported) == 5
    assert all(r["notification_settings"]["receiver"] == "openwrt-security" for r in exported)

    def rule():
        result = request(gf, "/api/prometheus/grafana/api/v1/rules")
        return next(r for g in result["data"]["groups"] for r in g["rules"]
                    if r["name"] == "OpenWrt security heartbeat欠落")

    until(lambda: request(backend, "/state"), lambda x: x["queries"] > 0)
    until(rule, lambda x: x["state"] == "inactive" and x.get("health") == "ok")
    if not muted:
        assert request(backend, "/state")["deliveries"] == []
        request(backend, "/mode", {"mode": "firing"})
        until(rule, lambda x: x["state"] == "firing")
        first = until(lambda: request(backend, "/state"), lambda x: bool(x["deliveries"]))
        assert len(first["deliveries"]) == 1 and not first["deliveries"][0]["resolved"], json.dumps(first)
        assert first["deliveries"][0]["channel"] == CHANNEL
        time.sleep(22)  # More than two evaluations and the fixture group interval.
        assert len(request(backend, "/state")["deliveries"]) == 1, "duplicate firing notification"
        request(backend, "/mode", {"mode": "normal"})
        until(rule, lambda x: x["state"] == "inactive")
        resolved = until(lambda: request(backend, "/state"), lambda x: len(x["deliveries"]) == 2)
        assert resolved["deliveries"][-1]["resolved"]
        assert request(gf, "/api/v1/provisioning/policies") == policy
        print(json.dumps({"phase": "unmuted", "firing": 1, "resolved": 1, "duplicate_firing": 0}))
        return
    assert len(request(backend, "/state")["deliveries"]) == 2
    request(backend, "/mode", {"mode": "firing"})
    until(rule, lambda x: x["state"] == "firing")
    time.sleep(22)
    received = request(backend, "/state")
    assert len(received["deliveries"]) == 2, "muted firing was delivered: " + json.dumps(received)
    request(backend, "/mode", {"mode": "error"})
    until(rule, lambda x: x["state"] == "firing" and x.get("health") != "ok")
    assert request(gf, "/api/v1/provisioning/policies") == policy, "root notification policy changed"
    print(json.dumps({"status": "pass", "image": IMAGE, "notifier": "slack",
                      "token_environment_expansion": True, "empty_vector": "normal",
                      "firing": 1, "resolved": 1, "duplicate_firing": 0,
                      "muted_firing": 0, "query_error": "alerting", "root_policy_unchanged": True}))


def docker(*args, check=True):
    return subprocess.run(["docker", *args], check=check, text=True, capture_output=True, timeout=300)


def main():
    from prod_openwrt_security_monitoring import provisioning, add_monitoring
    from prod_grafana_render import build_manifest
    token = "security-grafana-" + uuid.uuid4().hex[:12]
    network, gf, helper = token + "-net", token + "-gf", token + "-helper"
    created = False
    try:
        for image in [IMAGE, HELPER]:
            if docker("image", "inspect", image, check=False).returncode:
                docker("pull", image)
        with tempfile.TemporaryDirectory(prefix=token, dir=os.environ.get("HOMECLUSTER_TEST_SHARED_DIR")) as directory:
            root = Path(directory)
            for path in [root, root / "provision", root / "provision/alerting", root / "provision/datasources",
                         root / "provision/dashboards", root / "dashboards"]:
                path.mkdir(mode=0o755,exist_ok=True)
                path.chmod(0o755)
            value = provisioning(CHANNEL)
            value["groups"][0]["interval"] = "10s"
            for inactive in value["groups"][0]["rules"][1:]:
                inactive["isPaused"] = True
            value["groups"][0]["rules"][0]["for"] = "0s"
            value["groups"][0]["rules"][0]["notification_settings"].update(group_wait="1s", group_interval="10s")
            value["contactPoints"][0]["receivers"][0]["settings"]["endpointUrl"] = "http://fixture-backend:8080/api/chat.postMessage"
            # Fixture starts unmuted regardless of the real time. Later it is
            # hot-reloaded to all-day mute. Production Tokyo timing is unit-tested.
            now = datetime.now(timezone.utc).hour
            hour = (now + 12) % 24
            value["muteTimes"][0]["time_intervals"] = [{"times": [{
                "start_time": f"{hour:02d}:00", "end_time": f"{hour:02d}:01"}], "location": "UTC"}]
            alert = root / "provision/alerting/security.json"
            alert.write_text(json.dumps(value))
            alert.chmod(0o644)
            ds = root / "provision/datasources/datasource.yaml"
            ds.write_text(json.dumps({"apiVersion": 1, "datasources": [{
                "name": "prod-loki", "uid": "prod-loki", "type": "loki", "access": "proxy",
                "url": "http://fixture-backend:8080"}]}))
            ds.chmod(0o644)
            manifest = build_manifest()
            add_monitoring(manifest, CHANNEL)
            cm = next(o for o in manifest["items"] if o["kind"] == "ConfigMap")
            provider = json.loads(cm["data"]["dashboard-provider.yaml"])
            provider["providers"] = [p for p in provider["providers"] if p["name"] == "openwrt-security"]
            for target, text in [(root / "provision/dashboards/provider.yaml", json.dumps(provider)),
                                 (root / "dashboards/openwrt-security.json", cm["data"]["openwrt-security.json"])]:
                target.write_text(text)
                target.chmod(0o644)
            script = root / "scripts/ci/test_prod_security_grafana_image.py"
            script.parent.mkdir(parents=True, mode=0o755)
            script.parent.parent.chmod(0o755)
            script.write_bytes(Path(__file__).read_bytes())
            script.chmod(0o644)
            docker("network", "create", "--internal", network)
            created = True
            docker("run", "-d", "--name", helper, "--network", network, "--network-alias", "fixture-backend",
                   "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "10001:10001",
                   "--memory", "128m", "--cpus", "1", "--mount", f"type=bind,src={root},dst=/fixture,readonly",
                   HELPER, "python3", "/fixture/scripts/ci/test_prod_security_grafana_image.py", "--server")
            grafana_args = ["--name", gf, "--network", network, "--network-alias", "grafana-fixture",
                   "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "472:472",
                   "--memory", "512m", "--cpus", "1",
                   "--tmpfs", "/var/lib/grafana:uid=472,gid=472,mode=0700,size=128m",
                   "--tmpfs", "/tmp:uid=472,gid=472,mode=0700,size=32m",
                   "--mount", f"type=bind,src={root}/provision,dst=/etc/grafana/provisioning,readonly",
                   "--mount", f"type=bind,src={root}/dashboards,dst=/etc/grafana/security-dashboards,readonly",
                   "-e", "GF_SECURITY_ADMIN_PASSWORD=" + PASSWORD, "-e", "GF_LOG_LEVEL=error",
                   "-e", "GF_SECURITY_SLACK_TOKEN=" + BOT_TOKEN,
                   "-e", "GF_ANALYTICS_REPORTING_ENABLED=false", "-e", "GF_ANALYTICS_CHECK_FOR_UPDATES=false",
                   "-e", "GF_PLUGINS_PREINSTALL_DISABLED=true", "-e", "GF_PLUGINS_CHECK_FOR_UPDATES=false",
                   "-e", "GF_UNIFIED_ALERTING_MIN_INTERVAL=10s", IMAGE]
            for phase in ["--client", "--client-muted"]:
                if phase == "--client-muted":
                    # Production publishes a new file through a Recreate rollout.
                    # Test that same cold-start contract, not an asynchronous
                    # admin reload racing an already queued notification.
                    docker("rm", "-f", gf)
                    value["muteTimes"][0]["time_intervals"] = [{
                        "times": [{"start_time": "00:00", "end_time": "24:00"}], "location": "UTC"}]
                    alert.write_text(json.dumps(value))
                docker("run", "-d", *grafana_args)
                result = docker("exec", helper, "python3", "/fixture/scripts/ci/test_prod_security_grafana_image.py", phase, check=False)
                if result.returncode:
                    print(result.stdout + result.stderr)
                    print(docker("logs", gf, check=False).stdout[-5000:])
                    raise AssertionError("isolated native Grafana acceptance failed: " + phase)
                print(result.stdout.strip())
    finally:
        # Only this invocation's exact, random, fixed-name resources.
        for container in [gf, helper]:
            docker("rm", "-f", container, check=False)
        if created:
            docker("network", "rm", network, check=False)


if __name__ == "__main__":
    if sys.argv[1:] == ["--server"]:
        server()
    elif sys.argv[1:] == ["--client"]:
        client()
    elif sys.argv[1:] == ["--client-muted"]:
        client(muted=True)
    else:
        main()
