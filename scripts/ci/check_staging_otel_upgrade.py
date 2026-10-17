#!/usr/bin/env python3
"""Chart-only Collector upgrade in a disposable kind cluster.

The application stays pinned: this is NOT a Collector 0.161 migration. Public
base/staging values are tested with O4 disabled/enabled. No household secrets,
endpoints, cluster, or inventory are used. Actual backend delivery is not tested.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "87f9e290d50ee5e4afac649227ee7d92ef8ca1c6"
VALUES = "clusters/homelab/apps"
TF = "terraform/modules/observability_stack/main.tf"
CONTEXT = "kind-otel-upgrade-ci"
NAMESPACE = "observability-stg"
RELEASE = "stg-otel-collector"
IMAGE = "ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-contrib:0.102.1"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def run(*args, data=None, timeout=300):
    result = subprocess.run(args, input=data, text=True, capture_output=True,
                            timeout=timeout, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stdout[-2000:]}\n{result.stderr[-3000:]}")
    return result.stdout


def one(docs, kind):
    matches = [d for d in docs if d.get("kind") == kind]
    require(len(matches) == 1, f"expected one {kind}")
    return matches[0]


def relay(docs):
    return yaml.safe_load(one(docs, "ConfigMap")["data"]["relay"])


def active_config(config):
    """Ignore only unused definitions, never referenced components or telemetry."""
    result = copy.deepcopy(config)
    pipelines = result["service"]["pipelines"]
    for category in ("receivers", "processors", "exporters"):
        used = {name for pipeline in pipelines.values() for name in pipeline[category]}
        require(used <= result[category].keys(), f"unresolved {category}")
        result[category] = {name: result[category][name] for name in sorted(used)}
    extensions = result["service"].get("extensions", [])
    require(set(extensions) <= result.get("extensions", {}).keys(), "unresolved extensions")
    result["extensions"] = {name: result["extensions"][name] for name in extensions}
    return result


def contract(before, after):
    a, b = one(before, "DaemonSet"), one(after, "DaemonSet")
    require(a["metadata"]["name"] == b["metadata"]["name"], "DaemonSet renamed")
    require(a["spec"]["selector"] == b["spec"]["selector"], "immutable selector changed")
    pods = [d["spec"]["template"]["spec"] for d in (a, b)]
    for field in ("serviceAccountName", "nodeSelector", "tolerations"):
        require(pods[0].get(field) == pods[1].get(field), f"Pod {field} changed")
    for field in ("hostNetwork", "hostPID"):
        require(pods[0].get(field, False) == pods[1].get(field, False), f"Pod {field} changed")
    for pod in pods:
        require(len(pod["containers"]) == 1, "unexpected sidecar")
    containers = [pod["containers"][0] for pod in pods]
    for container in containers:
        require(container["image"] == IMAGE, "Collector application pin changed")
    for field in ("command", "args", "resources", "securityContext", "volumeMounts", "ports"):
        require(containers[0].get(field) == containers[1].get(field), f"container {field} changed: {containers[0].get(field)!r} -> {containers[1].get(field)!r}")
    require(pods[0]["volumes"] == pods[1]["volumes"], "config/host/CA volume changed")
    old_env, new_env = ({e["name"]: e for e in c["env"]} for c in containers)
    require(all(new_env.get(k) == v for k, v in old_env.items()), f"existing environment/Secret changed: {old_env!r} -> {new_env!r}")
    # Additions are allowed only for the chart's Kubernetes downward API metadata.
    for name in new_env.keys() - old_env.keys():
        require(name.startswith("OTEL_K8S_") and "fieldRef" in new_env[name].get("valueFrom", {}),
                f"unexpected environment added: {name}")
    for kind in ("ConfigMap", "ServiceAccount", "Service"):
        require(one(before, kind)["metadata"]["name"] == one(after, kind)["metadata"]["name"], f"{kind} renamed")
    services = [copy.deepcopy(one(docs, "Service")["spec"]) for docs in (before, after)]
    for spec in services:
        for port in spec["ports"]:
            # Application protocol hints do not change traffic routing.
            protocol = port.pop("appProtocol", None)
            require(protocol in (None, "grpc", "http"), "unexpected appProtocol")
    require(services[0] == services[1], f"Service routing changed: {services!r}")
    old_config, new_config = active_config(relay(before)), active_config(relay(after))
    require(old_config == new_config,
            f"active Collector config changed:\n{yaml.safe_dump(old_config)}\n->\n{yaml.safe_dump(new_config)}")
    metrics = new_config["service"]["telemetry"]["metrics"]
    require(metrics.get("address") == "${env:MY_POD_IP}:8888" and not metrics.get("readers"),
            "legacy self-metrics endpoint changed")
    require("filelog/pods" in new_config["receivers"], "legacy filelog name lost")
    require("otlp/tempo" in new_config["exporters"], "legacy OTLP gRPC name lost")


# This runs inside a disposable client Pod so Pod-IP listeners are exercised,
# rather than replacing them with loopback for test convenience.
CLIENT = r'''
import json, os, time
from urllib.request import Request, urlopen
ip, marker = os.environ["COLLECTOR_IP"], os.environ["MARKER"]
def http(port, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    with urlopen(Request(f"http://{ip}:{port}{path}", data=data,
                         headers={"Content-Type": "application/json"}), timeout=5) as r:
        return r.read().decode()
assert http(13133, "/"), "health_check returned no response"
now = str(time.time_ns())
payload = {"resourceMetrics": [{"scopeMetrics": [{"scope": {"name": "chart-upgrade-ci"}, "metrics": [{
    "name": "chart_upgrade_probe", "gauge": {"dataPoints": [{"timeUnixNano": now, "asDouble": 42,
    "attributes": [{"key": "marker", "value": {"stringValue": marker}}]}]}}]}]}]}
reply = json.loads(http(4318, "/v1/metrics", payload))
assert not int(reply.get("partialSuccess", {}).get("rejectedDataPoints", 0)), reply
for _ in range(45):
    text = http(8889, "/metrics")
    lines = [line for line in text.splitlines() if line.startswith("chart_upgrade_probe{") and f'marker="{marker}"' in line]
    if len(lines) == 1:
        assert 'environment="staging"' in lines[0] and 'app="otel-collector"' in lines[0], lines
        assert float(lines[0].split("}", 1)[1].strip().split()[0]) == 42, lines
        break
    time.sleep(1)
else:
    raise AssertionError("metric never reached Prometheus exporter")
assert "otelcol_process_uptime" in http(8888, "/metrics"), "self metrics missing"
print("PASS: Pod-IP health, OTLP HTTP -> processors -> Prometheus with preserved labels, self metrics", flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    args = parser.parse_args()
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", CONTEXT, "-n", NAMESPACE]
    require(run(*kube, "config", "current-context").strip() == CONTEXT, "refusing non-CI context")
    helm = ["helm", "--kubeconfig", args.kubeconfig, "--kube-context", CONTEXT, "--namespace", NAMESPACE]
    with tempfile.TemporaryDirectory(prefix="otel-chart-upgrade-") as temp:
        root = Path(temp)
        run(*kube, "create", "namespace", NAMESPACE)
        # Public disposable certificate only. No production endpoint/credentials.
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=otel-upgrade.invalid", "-keyout", str(root / "key"), "-out", str(root / "ca.crt"))
        run(*kube, "create", "secret", "generic", "staging-prod-loki-push",
            "--from-literal=endpoint=https://127.0.0.1:9443/otlp",
            "--from-literal=basic_authorization_b64=Zml4dHVyZTpub3QtYS1zZWNyZXQ=",
            f"--from-file=ca.crt={root / 'ca.crt'}")
        manifests = json.loads(run("docker", "manifest", "inspect", IMAGE))
        platforms = {(v.get("platform", {}).get("os"), v.get("platform", {}).get("architecture"))
                     for v in manifests.get("manifests", [])}
        require({("linux", "amd64"), ("linux", "arm64")} <= platforms, "image architecture missing")
        for o4 in (False, True):
            versions, documents = [], []
            for name, ref in (("baseline", BASELINE), ("candidate", None)):
                def read(path):
                    return run("git", "show", f"{ref}:{path}") if ref else (ROOT / path).read_text()
                block = re.search(r'resource "helm_release" "otel_collector" \{(.*?)\n\}', read(TF), re.S)
                require(block is not None, "Collector release missing")
                repository, version = [re.search(rf'\b{k}\s*=\s*"([^"]+)"', block[1])[1]
                                       for k in ("repository", "version")]
                paths = [f"{VALUES}/base/values-otel-collector.yaml", f"{VALUES}/staging/values-otel-collector.yaml"]
                if o4:
                    paths.append(f"{VALUES}/staging/values-otel-collector-o4-prod-logs.yaml")
                values = []
                for i, path in enumerate(paths):
                    file = root / f"{name}-{o4}-{i}.yaml"
                    file.write_text(read(path))
                    values += ["-f", str(file)]
                chart = str(root / f"opentelemetry-collector-{version}.tgz")
                if not Path(chart).exists():
                    run("helm", "pull", "opentelemetry-collector", "--repo", repository,
                        "--version", version, "--destination", str(root))
                rendered = run("helm", "template", RELEASE, chart, "--namespace", NAMESPACE,
                               "--kube-version", "1.36.4", *values)
                documents.append([d for d in yaml.safe_load_all(rendered) if d])
                versions.append((name, chart, values))
            contract(*documents)
            print(f"PASS: O4={o4} active pipeline/Secret/host mount/image/routing contract", flush=True)
            identity = None
            daemonset = one(documents[0], "DaemonSet")["metadata"]["name"]
            for name, chart, values in versions:
                run(*helm, "upgrade", "--install", RELEASE, chart, "--reset-values",
                    "--wait", "--timeout", "4m", *values)
                run(*kube, "rollout", "status", f"daemonset/{daemonset}", "--timeout=180s")
                current = tuple(json.loads(run(*kube, "get", kind, one(documents[0], full)["metadata"]["name"],
                                                   "-o", "json"))["metadata"]["uid"]
                                for kind, full in (("daemonset", "DaemonSet"), ("service", "Service"),
                                                   ("serviceaccount", "ServiceAccount"), ("configmap", "ConfigMap")))
                require(identity is None or identity == current, "Helm upgrade recreated managed resource")
                identity = current
                pod_list = json.loads(run(*kube, "get", "pods", "-l", f"app.kubernetes.io/instance={RELEASE}", "-o", "json"))["items"]
                pods = [p for p in pod_list if not p["metadata"].get("deletionTimestamp")]
                require(len(pods) == 1, "expected one collector on the one-node kind cluster")
                client = f"otel-probe-{str(o4).lower()}-{name}"
                probe = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": client}, "spec": {
                    "restartPolicy": "Never", "automountServiceAccountToken": False,
                    "containers": [{"name": "probe", "image": "python:3.13.7-slim", "command": ["python", "-c", CLIENT],
                                    "env": [{"name": "COLLECTOR_IP", "value": pods[0]["status"]["podIP"]},
                                            {"name": "MARKER", "value": client}]}]}}
                run(*kube, "apply", "-f", "-", data=yaml.safe_dump(probe))
                try:
                    run(*kube, "wait", f"pod/{client}", "--for=jsonpath={.status.phase}=Succeeded", "--timeout=120s")
                finally:
                    print(run(*kube, "logs", client), flush=True)
                print(f"PASS: O4={o4} {name}; workload and Secret-backed Collector Ready", flush=True)
            # Test-only removal between two independent baseline->candidate upgrades.
            run(*helm, "uninstall", RELEASE, "--wait", "--timeout", "2m")
        print("PASS: chart 0.93.1 -> 0.175.0 in both modes; Collector remains 0.102.1", flush=True)


if __name__ == "__main__":
    main()
