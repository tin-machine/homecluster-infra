#!/usr/bin/env python3
"""Exercise the real Helm upgrade in disposable kind, never the household cluster.

Requires Helm 3, kubectl, kind, Docker, PyYAML and full Git history. The workflow
creates kind first. This script requires its explicit kubeconfig/context. The
synthetic nfs-client StorageClass uses kind's local-path provisioner, NOT NFS.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASELINE_REF = "f83cceaf4709b469690120b64ccfc63b9a085e66"
VALUES = ["clusters/homelab/apps/base/values-tempo.yaml",
          "clusters/homelab/apps/staging/values-tempo.yaml"]
TF = "terraform/modules/observability_stack/main.tf"
RELEASE, NAMESPACE = "stg-tempo", "observability-stg"
CONTEXT = "kind-tempo-upgrade-ci"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def run(*args: str, data: str | None = None, timeout: int = 300) -> str:
    result = subprocess.run(args, input=data, text=True, capture_output=True,
                            timeout=timeout, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stdout[-2000:]}\n{result.stderr[-3000:]}")
    return result.stdout


def release(source: str) -> tuple[str, str]:
    block = re.search(r'resource "helm_release" "tempo" \{(.*?)\n\}', source, re.S)
    require(block is not None, "Tempo Helm resource missing")
    return tuple(re.search(rf'\b{key}\s*=\s*"([^"]+)"', block[1])[1]
                 for key in ("repository", "version"))


def one(documents: list[dict], kind: str) -> dict:
    found = [d for d in documents if d.get("kind") == kind]
    require(len(found) == 1, f"expected one {kind}, found {len(found)}")
    return found[0]


def config(documents: list[dict]) -> dict:
    texts = [d["data"]["tempo.yaml"] for d in documents
             if d.get("kind") == "ConfigMap" and "tempo.yaml" in d.get("data", {})]
    require(len(texts) == 1, "rendered tempo.yaml missing/duplicated")
    return yaml.safe_load(texts[0])


def check_values(values: dict) -> None:
    require("resources" not in values, "resources must be nested under tempo")
    require("distributor" not in values["tempo"], "use tempo.receivers, not tempo.distributor")


def contract(before: list[dict], after: list[dict]) -> None:
    old, new = one(before, "StatefulSet"), one(after, "StatefulSet")
    require(old["metadata"]["name"] == new["metadata"]["name"] == RELEASE, "StatefulSet renamed")
    a, b = old["spec"], new["spec"]
    for key in ("selector", "serviceName", "replicas", "updateStrategy"):
        require(a.get(key) == b.get(key), f"StatefulSet {key} changed")
    require(b["replicas"] == 1, "local storage requires one replica")
    require(b.get("persistentVolumeClaimRetentionPolicy", {}) in
            ({}, {"whenDeleted": "Retain", "whenScaled": "Retain"}), "PVC auto-delete enabled")
    claims = []
    for spec in (a, b):
        items = copy.deepcopy(spec["volumeClaimTemplates"])
        for item in items:
            # Chart 1.24.3 adds these. The real API/helm upgrade below must also pass.
            require(item.pop("apiVersion", "v1") == "v1", "unexpected PVC apiVersion")
            require(item.pop("kind", "PersistentVolumeClaim") == "PersistentVolumeClaim", "unexpected claim kind")
        claims.append(items)
    require(claims[0] == claims[1], "PVC template changed")
    for docs in (before, after):
        pod = one(docs, "StatefulSet")["spec"]["template"]["spec"]
        require(len(pod["containers"]) == 1, "unexpected Tempo sidecar")
        require({"name": "storage", "mountPath": "/var/tempo"} in
                pod["containers"][0]["volumeMounts"], "persistent storage mount changed")
    services = lambda docs: {d["metadata"]["name"]: d["spec"] for d in docs if d.get("kind") == "Service"}
    require(services(before) == services(after), "Service routing changed")
    previous, current = config(before), config(after)
    storage = copy.deepcopy(current["storage"])
    require(storage["trace"].pop("block") == {"version": "vParquet4"}, "unstable block format")
    require(storage == previous["storage"], "existing trace/WAL storage changed")
    for key in ("compactor", "overrides", "multitenancy_enabled"):
        require(previous.get(key) == current.get(key), f"Tempo {key} changed")
    for protocol, port in (("grpc", 4317), ("http", 4318)):
        receiver = current["distributor"]["receivers"]["otlp"]["protocols"][protocol]
        require(receiver == {"endpoint": f"0.0.0.0:{port}"}, f"invalid {protocol} receiver")
    container = b["template"]["spec"]["containers"][0]
    require(container["resources"] == {"requests": {"cpu": "100m", "memory": "256Mi"},
                                       "limits": {"memory": "512Mi"}}, "resource budget not rendered")
    require("-mem-ballast-size-mbs=0" in container["args"], "legacy ballast is not disabled")
    require({"name": "GOMEMLIMIT", "value": "400MiB"} in container["env"], "Go memory limit not rendered")


def http(port: int, path: str, payload: dict | None = None) -> dict | str:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(f"http://127.0.0.1:{port}{path}", data=data,
                      headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        text = response.read().decode()
    return json.loads(text) if text.strip().startswith(("{", "[")) else text


def eventually(function, timeout: int = 150):
    deadline, last = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        try:
            return function()
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            last = error
            time.sleep(2)
    raise RuntimeError(f"condition not met within {timeout}s: {last}")


def send_trace(trace_id: str, label: str) -> dict:
    now = time.time_ns()
    span = {"traceId": trace_id, "spanId": trace_id[:16], "name": label,
            "kind": "SPAN_KIND_INTERNAL", "startTimeUnixNano": str(now - 1000000000),
            "endTimeUnixNano": str(now), "attributes": [{"key": "upgrade.marker", "value": {"stringValue": label}}]}
    payload = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "tempo-upgrade-ci"}}]},
                                  "scopeSpans": [{"scope": {"name": "upgrade-ci"}, "spans": [span]}]}]}
    result = http(14318, "/v1/traces", payload)
    require(isinstance(result, dict) and int(result.get("partialSuccess", {}).get("rejectedSpans", 0)) == 0, "trace ingestion rejected")
    return span


def assert_trace(span: dict, blocks: bool = False) -> None:
    # The debug querier endpoint, not query-frontend's /api/traces, supports mode.
    path = f"/querier/api/traces/{span['traceId']}?mode=blocks" if blocks else f"/api/traces/{span['traceId']}"
    document = http(13200, path)
    def walk(value):
        if isinstance(value, dict):
            if "spanId" in value:
                yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)
    # Tempo JSON may encode protobuf bytes as base64 instead of OTLP hex IDs.
    found = [v for v in walk(document) if v.get("name") == span["name"]]
    require(len(found) == 1, f"missing trace span: {span['name']}")
    for key in ("traceId", "spanId"):
        actual = found[0][key]
        if actual.lower() != span[key]:
            require(base64.b64decode(actual, validate=True).hex() == span[key], f"trace {key} changed")
    for key in ("startTimeUnixNano", "endTimeUnixNano", "attributes"):
        require(found[0].get(key) == span[key], f"trace {key} changed")


@contextmanager
def forward(kube: list[str]):
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen([*kube, "port-forward", f"service/{RELEASE}",
                                    "13200:3200", "14318:4318", "--address=127.0.0.1"],
                                   stdout=log, stderr=log)
        try:
            eventually(lambda: http(13200, "/ready"))
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    args = parser.parse_args()
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", CONTEXT, "-n", NAMESPACE]
    require(run(*kube, "config", "current-context").strip() == CONTEXT, "refusing non-CI cluster")
    helm = ["helm", "--kubeconfig", args.kubeconfig, "--kube-context", CONTEXT]
    with tempfile.TemporaryDirectory(prefix="tempo-upgrade-") as temp:
        directory, releases, manifests = Path(temp), [], []
        for name, ref in (("baseline", BASELINE_REF), ("candidate", None)):
            read = lambda path: run("git", "show", f"{ref}:{path}") if ref else (ROOT / path).read_text()
            repository, version = release(read(TF))
            # CI-only: disable anonymous reporting and shorten backend discovery.
            # PVC spec, OTLP receiver config and application memory are unchanged.
            value_args = ["--set", "tempo.reportingEnabled=false",
                          "--set", "tempo.storage.trace.blocklist_poll=2s"]
            for index, path in enumerate(VALUES):
                target = directory / f"{name}-{index}.yaml"
                target.write_text(read(path))
                value_args += ["-f", str(target)]
            if name == "candidate":
                check_values(yaml.safe_load(read(VALUES[1])))
            run("helm", "pull", "tempo", "--repo", repository, "--version", version,
                "--destination", str(directory))
            chart = str(directory / f"tempo-{version}.tgz")
            rendered = run("helm", "template", RELEASE, chart, "--namespace", NAMESPACE,
                           "--kube-version", "1.36.4", *value_args)
            documents = [d for d in yaml.safe_load_all(rendered) if d]
            image = one(documents, "StatefulSet")["spec"]["template"]["spec"]["containers"][0]["image"]
            manifest = json.loads(run("docker", "manifest", "inspect", image))
            require(any(m.get("platform", {}).get("architecture") == "arm64" and
                        m["platform"].get("os") == "linux" for m in manifest.get("manifests", [])), "arm64 image missing")
            releases.append((chart, value_args, image))
            manifests.append(documents)
        contract(*manifests)
        print("PASS: Helm contract, OTLP endpoints, stable block format and memory budget", flush=True)
        # Keep the actual PVC name, class, access mode and requested size. Only the
        # provisioner implementation is synthetic; this is not an NFS test.
        run(*kube, "create", "namespace", NAMESPACE)
        run(*kube, "apply", "-f", "-", data=yaml.safe_dump({"apiVersion": "storage.k8s.io/v1", "kind": "StorageClass",
            "metadata": {"name": "nfs-client"}, "provisioner": "rancher.io/local-path",
            "volumeBindingMode": "WaitForFirstConsumer", "reclaimPolicy": "Retain"}))
        def identity():
            claim = json.loads(run(*kube, "get", "pvc", "storage-stg-tempo-0", "-o", "json"))
            sts = json.loads(run(*kube, "get", "statefulset", RELEASE, "-o", "json"))
            pv = json.loads(run(*kube, "get", "pv", claim["spec"]["volumeName"], "-o", "json"))
            require(claim["status"]["phase"] == "Bound", "PVC not Bound")
            return (sts["metadata"]["uid"], claim["metadata"]["uid"], pv["metadata"]["uid"], claim["spec"])
        baseline_identity, old_span, new_span = None, None, None
        for index, (chart, values, image) in enumerate(releases):
            # No --force, uninstall, PVC recreation, or patched chart manifests.
            run(*helm, "upgrade", "--install", RELEASE, chart, "--namespace", NAMESPACE,
                "--wait", "--timeout", "5m", "--reset-values", *values, timeout=330)
            run(*kube, "rollout", "status", f"statefulset/{RELEASE}", "--timeout=180s")
            current = identity()
            if index == 0:
                baseline_identity = current
            else:
                require(current == baseline_identity, "StatefulSet/PVC/PV identity changed")
            with forward(kube):
                if index == 0:
                    old_span = send_trace("11223344556677889900aabbccddeeff", "before-upgrade")
                    eventually(lambda: assert_trace(old_span))
                    http(13200, "/flush", {})
                    eventually(lambda: assert_trace(old_span, blocks=True), timeout=150)
                else:
                    eventually(lambda: assert_trace(old_span, blocks=True), timeout=150)
                    new_span = send_trace("ffeeddccbbaa00998877665544332211", "after-upgrade")
                    eventually(lambda: assert_trace(new_span))
                    http(13200, "/flush", {})
                    eventually(lambda: assert_trace(new_span, blocks=True), timeout=150)
            print(f"PASS: {image}; persisted trace read via blocks-only endpoint", flush=True)
        run(*kube, "rollout", "restart", f"statefulset/{RELEASE}")
        run(*kube, "rollout", "status", f"statefulset/{RELEASE}", "--timeout=180s")
        require(identity() == baseline_identity, "restart changed storage identity")
        with forward(kube):
            for span in (old_span, new_span):
                eventually(lambda: assert_trace(span, blocks=True), timeout=150)
        print("PASS: real Helm upgrade + restart; StatefulSet/PVC/PV UIDs and old/new persisted spans retained", flush=True)


if __name__ == "__main__":
    main()
