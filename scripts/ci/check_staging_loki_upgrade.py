#!/usr/bin/env python3
"""Verify Loki storage/identity contracts and an isolated Helm upgrade in kind."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml

from staging_upgrade_support import run_captured, stop_process

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "3c08c4cdb7e5074a9e457731a2a996f5489e5193"
TF = "terraform/modules/observability_stack/main.tf"
CONTEXT, NAMESPACE, RELEASE = "kind-loki-upgrade-ci", "observability-stg", "stg-loki"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def run(*args, data=None, timeout=300):
    result = run_captured(args, data=data, cwd=ROOT, timeout=timeout)
    require(result.returncode == 0, f"{args[0]} failed: {result.stdout[-1000:]}\n{result.stderr[-3000:]}")
    return result.stdout


def read(path, baseline=False):
    return run("git", "show", f"{BASELINE}:{path}") if baseline else (ROOT / path).read_text()


def credentials(source):
    result = {}
    bindings = re.findall(r'name\s*=\s*"(loki\.[^"]+)"\s*\n\s*value\s*=\s*local\.(loki_objectstore_\w+)', source)
    require(len(bindings) == 6, "Loki sensitive binding set changed; review fixture")
    for name, variable in bindings:
        value = {"loki_objectstore_access_key": "fixture-access", "loki_objectstore_secret_key": "fixture-secret"}[variable]
        target = result
        parts = name.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return result


def config(documents):
    entries = [d["data"]["config.yaml"] for d in documents if d.get("kind") == "ConfigMap" and "config.yaml" in d.get("data", {})]
    require(len(entries) == 1, "Loki config missing/duplicated")
    return yaml.safe_load(entries[0])


def contract(old, new):
    def resources(docs, kinds):
        return {(d["kind"], d["metadata"]["name"]): d for d in docs if d["kind"] in kinds}
    before, after = resources(old, {"Deployment", "StatefulSet"}), resources(new, {"Deployment", "StatefulSet"})
    require(before.keys() == after.keys(), "Loki topology or workload identity changed")
    for key in before:
        a, b = before[key]["spec"], after[key]["spec"]
        for field in ("selector", "replicas", "serviceName", "volumeClaimTemplates", "persistentVolumeClaimRetentionPolicy"):
            require(a.get(field) == b.get(field), f"{key}: {field} changed")
    services_before, services_after = resources(old, {"Service"}), resources(new, {"Service"})
    # The new chart prefixes its internal scheduler discovery service. Both
    # generated clients and the service move together; public routes stay fixed.
    scheduler = services_before.pop(("Service", "query-scheduler-discovery"))
    services_before[("Service", "loki-query-scheduler-discovery")] = scheduler
    require(services_before.keys() == services_after.keys(), "Service identity changed")
    memberlist_selector = services_after[("Service", "loki-memberlist")]["spec"]["selector"]
    for name, kind in (("loki-read", "Deployment"), ("loki-write", "StatefulSet"),
                       ("loki-backend", "StatefulSet")):
        labels = after[(kind, name)]["spec"]["template"]["metadata"]["labels"]
        require(all(labels.get(key) == value for key, value in memberlist_selector.items()),
                f"{name}: memberlist service excludes workload")
    for key, previous in services_before.items():
        for field in ("type", "selector", "ports", "clusterIP", "publishNotReadyAddresses"):
            a, b = copy.deepcopy(previous["spec"].get(field)), copy.deepcopy(services_after[key]["spec"].get(field))
            if field == "ports":
                for port in b:
                    if key[1] == "loki-backend-headless" and port["name"] == "grpc":
                        require(port.pop("appProtocol", None) == "tcp", "unexpected backend protocol")
                    if key[1] == "loki-gateway":
                        require(port["name"] == port["targetPort"] == "http-metrics", "unexpected gateway named port")
                        port["name"] = port["targetPort"] = "http"
            require(a == b, f"{key}: {field} changed")
    a, b = config(old), config(new)
    for field in ("schema_config", "auth_enabled"):
        require(a[field] == b[field], f"{field} changed")
    for storage in ("common", "aws", "ruler"):
        def entry(c):
            return c["common"]["storage"]["s3"] if storage == "common" else c["storage_config"]["aws"] if storage == "aws" else c["ruler"]["storage"]["s3"]
        require(entry(a) == entry(b), f"{storage} S3 bucket/endpoint/credential changed")
    require(a["limits_config"]["retention_period"] == b["limits_config"]["retention_period"] == "168h", "retention changed")
    require(b["limits_config"].get("allow_structured_metadata") is False, "v12 requires structured metadata disabled")
    require("shared_store" not in b["storage_config"]["boltdb_shipper"], "removed shared_store remains")
    for field in ("active_index_directory", "cache_location"):
        require(a["storage_config"]["boltdb_shipper"][field] == b["storage_config"]["boltdb_shipper"][field], f"index path changed: {field}")


def prepare(root):
    releases, rendered = [], []
    for label, baseline in (("baseline", True), ("candidate", False)):
        source = read(TF, baseline)
        block = re.search(r'resource "helm_release" "loki" \{(.*?)\n\}', source, re.S)[1]
        repository, version = [re.search(rf'\b{k}\s*=\s*"([^"]+)"', block)[1] for k in ("repository", "version")]
        chart = root / f"loki-{version}.tgz"
        if not chart.exists():
            run("helm", "pull", "loki", "--repo", repository, "--version", version, "--destination", str(root))
        paths = []
        for env in ("base", "staging"):
            path = root / f"{label}-{env}.yaml"
            path.write_text(read(f"clusters/homelab/apps/{env}/values-loki.yaml", baseline))
            paths.append(path)
        path = root / f"{label}-credentials.yaml"
        path.write_text(yaml.safe_dump(credentials(source)))
        paths.append(path)
        flags = [arg for p in paths for arg in ("-f", str(p))]
        docs = [d for d in yaml.safe_load_all(run("helm", "template", RELEASE, str(chart), "--namespace", NAMESPACE, "--kube-version", "1.36.0", *flags)) if d]
        rendered.append(docs)
        releases.append((label, str(chart), flags))
    contract(*rendered)
    excluded_read = copy.deepcopy(rendered[1])
    next(d for d in excluded_read if d.get("kind") == "Deployment"
         and d["metadata"]["name"] == "loki-read")["spec"]["template"]["metadata"]["labels"][
             "app.kubernetes.io/part-of"] = "observability"
    try:
        contract(rendered[0], excluded_read)
    except ValueError as error:
        require(str(error) == "loki-read: memberlist service excludes workload",
                "memberlist exclusion rejected for an unrelated reason")
    else:
        raise ValueError("Loki read memberlist exclusion was not rejected")
    print("PASS: topology, selectors, PVCs, public routes, S3 credentials/buckets and v12 schema retained", flush=True)
    return releases


def eventually(function, timeout=180):
    end, last = time.monotonic() + timeout, None
    while time.monotonic() < end:
        try:
            return function()
        except (ValueError, URLError, TimeoutError) as error:
            last = error
            time.sleep(3)
    raise ValueError(f"condition not met: {last}")


@contextmanager
def forward(kube, target, local, remote):
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen([*kube, "port-forward", target, f"{local}:{remote}", "--address=127.0.0.1"], stdout=log, stderr=log)
        try:
            yield
        finally:
            stop_process(proc)


def http(path, payload=None, port=19310):
    request = Request(f"http://127.0.0.1:{port}{path}", data=None if payload is None else json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        body = response.read().decode()
    return json.loads(body) if body.lstrip().startswith("{") else {}


def identity(kube):
    docs = json.loads(run(*kube, "get", "pvc,statefulset,deployment", "-o", "json"))["items"]
    return {(d["kind"], d["metadata"]["name"]): d["metadata"]["uid"] for d in docs}


def upgrade(root, releases, kubeconfig):
    kube = ["kubectl", "--kubeconfig", kubeconfig, "--context", CONTEXT, "-n", NAMESPACE]
    require(run(*kube, "config", "current-context").strip() == CONTEXT, "refusing non-CI context")
    helm = ["helm", "--kubeconfig", kubeconfig, "--kube-context", CONTEXT]
    # Preserve the production storageClass name while substituting only the
    # provisioner in this disposable cluster. No NFS or live cluster is used.
    sc = {"apiVersion": "storage.k8s.io/v1", "kind": "StorageClass", "metadata": {"name": "nfs-client"}, "provisioner": "rancher.io/local-path", "reclaimPolicy": "Retain", "volumeBindingMode": "WaitForFirstConsumer"}
    run(*kube, "apply", "-f", "-", data=yaml.safe_dump(sc))
    run(*kube, "create", "namespace", NAMESPACE)
    run(*kube, "create", "namespace", "object-storage-stg")
    run(*helm, "upgrade", "--install", "stg-minio", "minio", "--repo", "https://charts.min.io/", "--version", "5.4.0", "--namespace", "object-storage-stg", "-f", str(ROOT / "clusters/homelab/apps/base/values-minio.yaml"), "-f", str(ROOT / "clusters/homelab/apps/staging/values-minio.yaml"), "--set", "rootUser=fixture-access,rootPassword=fixture-secret", "--wait", "--timeout", "5m", timeout=330)
    old_identity = None
    expected = []
    start = time.time_ns() - 10_000_000_000
    for label, chart, flags in releases:
        run(*helm, "upgrade", "--install", RELEASE, chart, "--namespace", NAMESPACE, "--reset-values", "--wait", "--timeout", "7m", *flags, timeout=450)
        current = identity(kube)
        if old_identity is not None:
            require(old_identity == current, "workload or PVC UID changed across upgrade")
        old_identity = current
        marker = f"loki-upgrade-{label}-{time.time_ns()}"
        with forward(kube, "service/loki-gateway", 19310, 80):
            eventually(lambda: http("/"))
            http("/loki/api/v1/push", {"streams": [{"stream": {"job": "upgrade-fixture"}, "values": [[str(time.time_ns()), marker]]}]})
            expected.append(marker)
            def query():
                result = http("/loki/api/v1/query_range?" + urlencode({"query": '{job="upgrade-fixture"}', "start": str(start), "end": str(time.time_ns()), "limit": "100"}))
                require(result.get("status") == "success", "Loki query failed")
                lines = {v[1] for item in result["data"]["result"] for v in item["values"]}
                require(set(expected) <= lines, "old/new log markers missing")
            eventually(query)
        # Force chunks to object storage, then restart the reader and writer so
        # the following phase cannot pass solely from a surviving ingester.
        with forward(kube, "pod/loki-write-0", 19311, 3100):
            eventually(lambda: http("/flush", {}, port=19311))
        run(*kube, "rollout", "restart", "statefulset/loki-write", "deployment/loki-read")
        for target in ("statefulset/loki-write", "deployment/loki-read"):
            run(*kube, "rollout", "status", target, "--timeout=180s")
        with forward(kube, "service/loki-gateway", 19310, 80):
            eventually(query)
        require(old_identity == identity(kube), "restart replaced workload/PVC identity")
        print(f"PASS: {label}; old/new logs queried after flush/restart; workload and PVC UIDs retained", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig")
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    require(args.render_only or args.kubeconfig, "explicit disposable kubeconfig required")
    with tempfile.TemporaryDirectory(prefix="loki-upgrade-") as directory:
        root = Path(directory)
        releases = prepare(root)
        if not args.render_only:
            upgrade(root, releases, args.kubeconfig)


if __name__ == "__main__":
    main()
