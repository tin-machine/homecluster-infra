#!/usr/bin/env python3
"""Exercise the deployed Collector version with synthetic RFC3164 frames.

Only disposable containers on a new internal network are used; no cluster,
site inputs, ports on the host, or credentials are needed.
"""

import copy
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

import yaml


ROOT = Path(__file__).resolve().parents[2]


def docker(*args, check=True, input=None):
    return subprocess.run(["docker", *args], check=check, input=input,
                          text=True, capture_output=True, timeout=120)


def main():
    base = yaml.safe_load((ROOT / "clusters/homelab/apps/base/values-otel-collector.yaml").read_text())
    env = yaml.safe_load((ROOT / "clusters/homelab/apps/staging/values-otel-collector.yaml").read_text())["config"]
    overlay = yaml.safe_load((ROOT / "clusters/homelab/apps/staging/values-otel-collector-o4-prod-logs.yaml").read_text())["config"]
    security = yaml.safe_load((ROOT / "clusters/homelab/apps/staging/values-otel-collector-openwrt-security.yaml").read_text())["config"]

    def merge(left, right):
        if isinstance(left, dict) and isinstance(right, dict):
            result = copy.deepcopy(left)
            for key, value in right.items():
                result[key] = merge(result.get(key), value)
            return result
        return copy.deepcopy(right)

    active = merge(merge(env, overlay), security)
    image = base["image"]["repository"] + ":" + str(base["image"]["tag"])
    pipeline = copy.deepcopy(active["service"]["pipelines"]["logs/openwrt"])
    assert pipeline["exporters"] == ["otlphttp/prod-logs"]
    assert overlay["service"]["pipelines"]["logs"]["exporters"] == ["otlphttp/prod-logs"]
    # Use the actual receiver and ordered processors; only the final sink is
    # replaced by a local debug exporter so the fixture cannot reach a site.
    pipeline["exporters"] = ["debug"]
    processors = active["processors"]
    configuration = {
        "receivers": {"syslog/openwrt": env["receivers"]["syslog/openwrt"]},
        "processors": {name: processors[name] for name in pipeline["processors"]},
        "exporters": {"debug": {"verbosity": "detailed"}},
        "service": {"telemetry": {"metrics": {"address": "127.0.0.1:8888"}},
                    "pipelines": {"logs": pipeline}},
    }
    token = "security-otel-test-" + uuid.uuid4().hex[:12]
    network, collector = token + "-net", token + "-collector"
    helper_image = "python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8"
    for candidate in [image, helper_image]:
        if docker("image", "inspect", candidate, check=False).returncode:
            docker("pull", candidate)
    created = False
    try:
        with tempfile.TemporaryDirectory(prefix=token, dir=os.environ.get("HOMECLUSTER_TEST_SHARED_DIR")) as dirname:
            path = Path(dirname) / "collector.json"
            path.write_text(json.dumps(configuration))
            path.chmod(0o644)  # Non-secret synthetic config, readable by UID10001.
            docker("network", "create", "--internal", network)
            created = True
            common = ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                      "--user", "10001:10001", "--memory", "256m", "--cpus", "1",
                      "--mount", f"type=bind,src={path},dst=/etc/collector.json,readonly"]
            docker("run", "--rm", "--network", "none", *common, image,
                   "validate", "--config=/etc/collector.json")
            docker("run", "-d", "--name", collector, "--network", network,
                   *common, image, "--config=/etc/collector.json")
            record = {"schema": 1, "security_source": "openwrt", "sensor": "router-example",
                      "log_type": "heartbeat", "banip_status": "active",
                      "banip_run_age_seconds": 60, "banip_feed_verification": "unknown",
                      "fixture_case": "fixture-normal"}
            # Feed metadata comes from the real producer, not a parallel JSON
            # implementation in this consumer fixture.
            spec = importlib.util.spec_from_file_location("security_monitor_fixture", ROOT / "ansible/openwrt/roles/openwrt_security_monitor/files/security_monitor.py")
            producer = importlib.util.module_from_spec(spec); spec.loader.exec_module(producer)
            stamp = datetime(2026, 1, 1).timestamp()
            runtime = Path(dirname) / "runtime.json"
            runtime.write_text(json.dumps({"status": "active", "backend_ver": "fixture",
                                          "active_feeds": ["dshield.v4", "feodo.v4"],
                                          "last_run": "mode: reload, date / time: 01/01/2026 00:00:00, duration: 1s"}))
            proof = {"status": "verified", "age_seconds": 60, "feeds": [
                {"feed": name, "status": "verified", "verified_at": stamp, "age_seconds": 60, "last_verified_count": 1}
                for name in ("dshield.v4", "feodo.v4")]}
            feed_record = producer.heartbeat("router-example", runtime=runtime, now=stamp + 60,
                                             feed_verification=True, proof_reader=lambda: proof)
            assert feed_record["banip_feed_verification"] == "verified"
            feed_record["fixture_case"] = "fixture-feed-proof"
            invalid_feed = dict(feed_record, fixture_case="fixture-invalid-proof", banip_feed_max_age_seconds=True)
            prefix = "<30>Oct 31 00:00:00 router-example "
            frames = [prefix + "homecluster-security: " + json.dumps(record),
                      prefix + "homecluster-security: {fixture-invalid",
                      prefix + "homecluster-security: " + json.dumps({"valid": True, "sensor": "fixture-forged"}),
                      prefix + "kernel: fixture-kernel-record",
                      prefix + "homecluster-security: " + json.dumps(feed_record),
                      prefix + "homecluster-security: " + json.dumps(invalid_feed)]
            client = (
                "import socket,time,json\n"
                "deadline=time.monotonic()+30\n"
                "while True:\n"
                " try:\n"
                f"  s=socket.create_connection(({collector!r},6514),timeout=2);break\n"
                " except OSError:\n"
                "  if time.monotonic()>deadline:raise\n"
                "  time.sleep(.2)\n"
                f"s.sendall({(chr(10).join(frames)+chr(10)).encode()!r});s.close()\n"
            )
            docker("run", "--rm", "--name", token + "-sender", "--network", network, "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--user", "10001:10001",
                   "--memory", "64m", "--cpus", "0.25", "-e", "PYTHONDONTWRITEBYTECODE=1",
                   helper_image, "python", "-c", client)
            deadline = time.monotonic() + 30
            while True:
                # The debug exporter writes to stderr on some builds.
                logs = docker("logs", collector)
                output = logs.stdout + logs.stderr
                records = re.split(r"(?m)^LogRecord #\d+\s*$", output)[1:]
                if len(records) >= 6:
                    break
                if time.monotonic() > deadline:
                    raise AssertionError("six fixture records not exported\n" + output[-8000:])
                time.sleep(.2)
            normal = next(r for r in records if "fixture-normal" in r)
            invalid = next(r for r in records if "fixture-invalid" in r)
            forged = next(r for r in records if "fixture-forged" in r)
            kernel = next(r for r in records if "fixture-kernel-record" in r)
            feed = next(r for r in records if "fixture-feed-proof" in r)
            invalid_proof = next(r for r in records if "fixture-invalid-proof" in r)
            assert "service.name: Str(openwrt-security)" in output
            assert "sensor: Str(router-example)" in normal
            assert "log_type: Str(heartbeat)" in normal
            assert "security_parser_status: Str(ok)" in normal
            assert re.search(r"banip_run_age_seconds: (Int|Double)\(60\)", normal), normal
            assert "security_parser_status: Str(ok)" in feed
            assert "banip_feed_verification: Str(verified)" in feed
            assert re.search(r"banip_feed_max_age_seconds: (Int|Double)\(60\)", feed), feed
            assert "dshield.v4" in feed and "feodo.v4" in feed
            for record in [invalid, forged, invalid_proof]:
                assert "security_parser_status: Str(invalid)" in record
                assert "log_type: Str(heartbeat)" not in record
                assert "-> sensor:" not in record
            assert "security_parser_status" not in kernel
            assert "app: Str(openwrt-router)" in kernel
            print(json.dumps({"status": "pass", "image": image, "records": 6,
                              "normal": True, "malformed": True,
                              "forged_valid_rejected": True, "kernel_preserved": True,
                              "real_feed_producer": True, "invalid_verified_age_rejected": True}))
    finally:
        docker("rm", "-f", token + "-sender", check=False)
        docker("rm", "-f", collector, check=False)
        if created:
            docker("network", "rm", network)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(exc.stdout, exc.stderr)
        raise
