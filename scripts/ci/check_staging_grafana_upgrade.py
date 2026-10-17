#!/usr/bin/env python3
"""Render old/new charts and migrate a disposable SQLite DB without cluster access.

Requires Helm 3, Docker, and PyYAML. Only checked-in public base/staging values
are used; private site values, a live database, NFS, and sidecar/Kubernetes API
communication are deliberately outside this test's scope.
"""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "8.15.0"
APPS = ROOT / "clusters/homelab/apps"
SENTINEL = "grafana-upgrade-ci-sentinel"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def run(*args: str, timeout: int = 180) -> str:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr[-3000:]}")
    return result.stdout


def one(items: list[dict], kind: str) -> dict:
    matches = [item for item in items if item.get("kind") == kind]
    require(len(matches) == 1, f"expected one {kind}, found {len(matches)}")
    return matches[0]


def grafana(deployment: dict) -> dict:
    return next(c for c in deployment["spec"]["template"]["spec"]["containers"]
                if c["name"] == "grafana")


def provisioning(items: list[dict]) -> dict[str, str]:
    """Use rendered, mounted ConfigMap data, not an approximation of Helm merging."""
    deployment = one(items, "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    volumes = {v["name"]: v for v in pod["volumes"]}
    maps = {d["metadata"]["name"]: d["data"] for d in items
            if d.get("kind") == "ConfigMap" and "data" in d}
    files = {}
    for mount in grafana(deployment)["volumeMounts"]:
        volume = volumes[mount["name"]]
        if "configMap" not in volume:
            continue
        path = mount["mountPath"]
        require(path.startswith("/etc/grafana/"), f"unexpected config mount: {path}")
        data = maps[volume["configMap"]["name"]]
        if "subPath" in mount:
            files[path] = data[mount["subPath"]]
        else:
            for key, value in data.items():
                require(Path(key).name == key, "config key is not a basename")
                files[f"{path}/{key}"] = value
    require("/etc/grafana/grafana.ini" in files, "missing rendered grafana.ini")
    return files


def render(version: str, repository: str) -> list[dict]:
    result = run("helm", "template", "stg-grafana", "grafana", "--repo", repository,
                 "--version", version, "--namespace", "observability-stg",
                 "--kube-version", "1.36.0", "--skip-tests",
                 "-f", str(APPS / "base/values-grafana.yaml"),
                 "-f", str(APPS / "staging/values-grafana.yaml"))
    return [d for d in yaml.safe_load_all(result) if d]


def service_spec(items: list[dict]) -> dict:
    """Compare resolved ports: chart 10 replaces numeric targetPort with a name."""
    spec = copy.deepcopy(one(items, "Service")["spec"])
    declared = grafana(one(items, "Deployment")).get("ports", [])
    for port in spec.get("ports", []):
        target = port.get("targetPort", port["port"])
        protocol = port.get("protocol", "TCP")
        matches = [p for p in declared if p.get("protocol", "TCP") == protocol and
                   (p.get("name") == target if isinstance(target, str)
                    else p["containerPort"] == target)]
        require(len(matches) == 1, f"Service target does not resolve to Grafana: {target}")
        port["targetPort"] = matches[0]["containerPort"]
    return spec


def validate_contract(old: list[dict], new: list[dict]) -> None:
    old_dep, new_dep = one(old, "Deployment"), one(new, "Deployment")
    require(new_dep["spec"]["replicas"] == 1, "SQLite requires one Grafana replica")
    require(new_dep["spec"]["strategy"] == {"type": "Recreate"},
            "SQLite schema upgrades must not overlap old/new Grafana pods")
    require(old_dep["metadata"]["name"] == new_dep["metadata"]["name"], "Deployment renamed")
    require(old_dep["spec"]["selector"] == new_dep["spec"]["selector"], "Deployment selector changed")
    for kind in ("PersistentVolumeClaim", "Service"):
        before, after = one(old, kind), one(new, kind)
        require(before["metadata"]["name"] == after["metadata"]["name"], f"{kind} renamed")
        before_spec = service_spec(old) if kind == "Service" else before["spec"]
        after_spec = service_spec(new) if kind == "Service" else after["spec"]
        require(before_spec == after_spec, f"{kind} spec changed: {before_spec} -> {after_spec}")
    for deployment in (old_dep, new_dep):
        env = {e["name"]: e for e in grafana(deployment)["env"]}
        for suffix, key in (("USER", "admin-user"), ("PASSWORD", "admin-password")):
            require(env[f"GF_SECURITY_ADMIN_{suffix}"]["valueFrom"]["secretKeyRef"] ==
                    {"name": "stg-grafana-admin", "key": key}, "admin Secret reference changed")
        pod = deployment["spec"]["template"]["spec"]
        storage = [v["persistentVolumeClaim"]["claimName"] for v in pod["volumes"]
                   if "persistentVolumeClaim" in v]
        require(storage == [one(new, "PersistentVolumeClaim")["metadata"]["name"]],
                "Grafana no longer mounts the existing PVC")
    old_files, new_files = provisioning(old), provisioning(new)
    require(old_files.keys() == new_files.keys(), "provisioning mounts changed")
    for path in old_files:
        if path.endswith(".yaml"):
            require(yaml.safe_load(old_files[path]) == yaml.safe_load(new_files[path]),
                    f"provisioning changed during Helm rendering: {path}")
    sources = yaml.safe_load(new_files["/etc/grafana/provisioning/datasources/datasources.yaml"])["datasources"]
    require({s["uid"] for s in sources} == {"mimir", "prometheus", "loki", "tempo"}, "datasource set changed")
    require(all(re.fullmatch(r"[A-Za-z0-9_-]{1,40}", s["uid"]) for s in sources), "invalid datasource UID")
    require([s["uid"] for s in sources if s.get("isDefault")] == ["mimir"], "default datasource changed")


def panels(dashboard: dict):
    for panel in dashboard.get("panels", []):
        yield panel
        yield from panels(panel)


def dashboard_signature(dashboard: dict) -> dict:
    return {"uid": dashboard["uid"], "title": dashboard["title"],
            "panels": sorted((p["id"], p.get("title", ""), p["type"])
                             for p in panels(dashboard))}


def check_dashboards(directory: Path) -> dict[str, dict]:
    result = {}
    for path in sorted(directory.glob("*.json")):
        dashboard = json.loads(path.read_text())
        uid = dashboard.get("uid")
        require(bool(uid) and uid not in result, f"missing/duplicate UID: {path.name}")
        for panel in panels(dashboard):
            require(panel["type"] not in {"graph", "singlestat", "table-old"},
                    f"Angular panel needs migration: {path.name} / {panel.get('id')}")
        result[uid] = dashboard_signature(dashboard)
    require(bool(result), "no checked-in dashboards found")
    return result


def memory_bytes(quantity: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(Mi|Gi)", quantity)
    require(match is not None, "test requires an explicit Mi/Gi memory limit")
    return int(match[1]) * (1024 ** (2 if match[2] == "Mi" else 3))


class GrafanaProcess:
    def __init__(self, documents: list[dict], directory: Path, database: str,
                 dashboards: Path, password: str):
        self.name = f"grafana-upgrade-ci-{secrets.token_hex(6)}"
        self.auth = base64.b64encode(f"upgrade-ci:{password}".encode()).decode()
        self.documents = documents
        self.directory = directory
        self.database = database
        self.dashboards = dashboards
        self.password = password

    def start(self) -> None:
        container = grafana(one(self.documents, "Deployment"))
        image = container["image"]
        manifest = json.loads(run("docker", "manifest", "inspect", image))
        platforms = {(m.get("platform", {}).get("os"), m.get("platform", {}).get("architecture"))
                     for m in manifest.get("manifests", [])}
        require({("linux", "amd64"), ("linux", "arm64")} <= platforms,
                f"missing amd64/arm64 image: {image}")
        run("docker", "pull", image, timeout=240)
        args = ["docker", "run", "-d", "--name", self.name, "--network", "none",
                "--memory", str(memory_bytes(container["resources"]["limits"]["memory"])),
                "--memory-swap", str(memory_bytes(container["resources"]["limits"]["memory"])),
                "--volume", f"{self.database}:/var/lib/grafana"]
        files = provisioning(self.documents)
        dashboard_paths = []
        for index, (path, text) in enumerate(sorted(files.items())):
            host = self.directory / f"config-{index}"
            host.write_text(text)
            host.chmod(0o644)
            args += ["--volume", f"{host}:{path}:ro"]
            if path.endswith(".yaml"):
                config = yaml.safe_load(text)
                for provider in config.get("providers", []):
                    dashboard_paths.append(provider["options"]["path"])
        require(len(dashboard_paths) == 1, "expected one dashboard provider")
        pod = one(self.documents, "Deployment")["spec"]["template"]["spec"]
        volumes = {v["name"]: v for v in pod["volumes"]}
        # Mirror writable emptyDirs (notably Grafana 12's search index) using
        # disposable disk directories. Dashboard files are mounted separately.
        for index, mount in enumerate(container["volumeMounts"]):
            if "emptyDir" not in volumes[mount["name"]]:
                continue
            path = mount["mountPath"]
            if any(Path(path) == Path(p) or Path(path) in Path(p).parents for p in dashboard_paths):
                continue
            host = self.directory / f"emptydir-{index}"
            host.mkdir()
            host.chmod(0o777)  # Disposable public-data fixture; no privileged chown.
            args += ["--volume", f"{host}:{path}"]
        args += ["--volume", f"{self.dashboards}:{dashboard_paths[0]}:ro"]
        for env in container["env"]:
            if "value" in env:
                args += ["--env", f"{env['name']}={env['value']}"]
            elif "resourceFieldRef" in env.get("valueFrom", {}):
                field = env["valueFrom"]["resourceFieldRef"]
                require(field["resource"] == "limits.memory" and str(field.get("divisor", "1")) == "1",
                        "unsupported resourceFieldRef in CI")
                args += ["--env", f"{env['name']}={memory_bytes(container['resources']['limits']['memory'])}"]
            else:
                require(env["name"] in {"GF_SECURITY_ADMIN_USER", "GF_SECURITY_ADMIN_PASSWORD"},
                        "unexpected non-literal Grafana environment input")
        # The real datasource/alert definitions are still loaded, but networkless
        # CI must not evaluate alerts or fetch optional plugins from the internet.
        overrides = {"GF_SECURITY_ADMIN_USER": "upgrade-ci",
                     "GF_SECURITY_ADMIN_PASSWORD": self.password,
                     "GF_SERVER_HTTP_ADDR": "127.0.0.1",
                     "GF_UNIFIED_ALERTING_EXECUTE_ALERTS": "false",
                     "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
                     "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false",
                     "GF_PLUGINS_PREINSTALL_DISABLED": "true"}
        for key, value in overrides.items():
            args += ["--env", f"{key}={value}"]
        run(*args, image)
        print(f"Started {image}; amd64/arm64 manifests present; network=none", flush=True)
        self.wait_ready()

    def api(self, path: str, payload: dict | None = None) -> dict | list:
        args = ["docker", "exec", self.name, "wget", "-qO-", "-T", "5",
                "--header", f"Authorization: Basic {self.auth}"]
        if payload is not None:
            args += ["--header", "Content-Type: application/json", "--post-data", json.dumps(payload)]
        return json.loads(run(*args, f"http://127.0.0.1:3000{path}", timeout=10))

    def wait_ready(self) -> None:
        for _ in range(90):
            state = json.loads(run("docker", "inspect", self.name))[0]["State"]
            require(state["Running"], f"Grafana exited (OOMKilled={state['OOMKilled']})")
            try:
                if self.api("/api/health").get("database") == "ok":
                    return
            except (RuntimeError, subprocess.SubprocessError, json.JSONDecodeError):
                pass
            time.sleep(2)
        raise ValueError("Grafana did not become healthy within 180 seconds")

    def snapshot(self, expected: dict[str, dict]) -> dict:
        # File provisioning can finish shortly after /api/health becomes ready.
        for _ in range(30):
            found = {d["uid"] for d in self.api("/api/search?type=dash-db&limit=1000")}
            if set(expected) <= found:
                break
            time.sleep(1)
        require(set(expected) <= found, "provisioned dashboards are missing")
        for uid, signature in expected.items():
            actual = self.api(f"/api/dashboards/uid/{quote(uid, safe='')}")["dashboard"]
            require(dashboard_signature(actual) == signature, f"dashboard changed: {uid}")
        sources = self.api("/api/datasources")
        source_snapshot = sorted((d["uid"], d["name"], d["type"], d["url"], d["isDefault"])
                                 for d in sources)
        require({d["uid"] for d in sources} == {"mimir", "prometheus", "loki", "tempo"}, "datasources missing")
        rules = self.api("/api/v1/provisioning/alert-rules")
        require({r["uid"] for r in rules} == {"openwrt_syslog_silence"}, "alert rule missing")
        return {"datasources": source_snapshot,
                "rules": [(r["uid"], r["title"], r["condition"], r["data"], r["labels"], r["annotations"], r.get("for"), r.get("noDataState"), r.get("execErrState")) for r in rules],
                "policies": self.api("/api/v1/provisioning/policies"),
                "mute_timings": self.api("/api/v1/provisioning/mute-timings")}

    def stop(self) -> None:
        run("docker", "stop", "--time", "30", self.name, timeout=45)
        run("docker", "rm", self.name)

    def cleanup(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=45, check=False)


def main() -> None:
    source = (ROOT / "terraform/modules/observability_stack/main.tf").read_text()
    block = re.search(r'resource "helm_release" "grafana" \{(.*?)\n\}', source, re.S)
    require(block is not None, "Grafana Helm resource not found")
    version = re.search(r'\bversion\s*=\s*"([^"]+)"', block[1])[1]
    repository = re.search(r'\brepository\s*=\s*"([^"]+)"', block[1])[1]
    old, new = render(BASELINE, repository), render(version, repository)
    validate_contract(old, new)
    dashboard_dir = APPS / "base/grafana-dashboards"
    expected = check_dashboards(dashboard_dir)
    print(f"Helm contract PASS: {BASELINE} -> {version}; {len(expected)} dashboards", flush=True)
    with tempfile.TemporaryDirectory(prefix="grafana-upgrade-") as temp:
        root = Path(temp)
        database = f"grafana-upgrade-ci-{secrets.token_hex(8)}"
        run("docker", "volume", "create", database)
        password = secrets.token_urlsafe(24)
        snapshots = []
        try:
            for name, docs in (("baseline", old), ("candidate", new)):
                directory = root / name
                directory.mkdir()
                process = GrafanaProcess(docs, directory, database, dashboard_dir, password)
                try:
                    process.start()
                    snapshots.append(process.snapshot(expected))
                    if name == "baseline":
                        result = process.api("/api/dashboards/db", {"dashboard": {
                            "uid": SENTINEL, "title": "Grafana upgrade CI sentinel",
                            "schemaVersion": 39, "panels": []}, "overwrite": False})
                        require(result["status"] == "success", "could not seed persisted dashboard")
                    else:
                        require(snapshots[0] == snapshots[1], "provisioning changed after SQLite migration")
                        require(process.api(f"/api/dashboards/uid/{SENTINEL}")["dashboard"]["uid"] == SENTINEL,
                                "non-provisioned dashboard lost during migration")
                        run("docker", "restart", "--time", "30", process.name, timeout=60)
                        process.wait_ready()
                        require(process.snapshot(expected) == snapshots[1], "provisioning changed after restart")
                        process.api(f"/api/dashboards/uid/{SENTINEL}")
                    process.stop()
                except Exception:
                    subprocess.run(["docker", "logs", "--tail", "80", process.name], check=False)
                    raise
                finally:
                    process.cleanup()
        finally:
            run("docker", "volume", "rm", database)
        print("PASS: SQLite upgrade + restart; 4 datasources, all dashboards, alert/policy/mute timings retained")


if __name__ == "__main__":
    main()
