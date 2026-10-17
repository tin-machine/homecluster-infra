#!/usr/bin/env python3
"""Test the actual Terraform Helm values and BGP manifests in disposable kind.

Upgrade on Kubernetes 1.35 (old CRDs fail new 1.36 validation); separately test
fresh installation on 1.36. No household cluster, inventory, state or router.
L2 on wlan0 is configuration-only: kind has no wireless interface.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "46cd5435fb99f9a9665b12bc8aea23fab9019c26"
CRDS = "terraform/env/common-crds/main.tf"
ADDONS = "terraform/env/common-addons/main.tf"
CONTEXT, NAMESPACE = "kind-metallb-upgrade-ci", "metallb-system"
PEER = "metallb-upgrade-peer"
VIP = "198.51.100.240"
FRR = "quay.io/frrouting/frr:10.5.3"
CLIENT = "python:3.13.7-slim"


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def run(*args: str, data: str | None = None, timeout: int = 300, cwd: Path = ROOT) -> str:
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=timeout, cwd=cwd)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stdout[-1500:]}\n{result.stderr[-3000:]}")
    return result.stdout


def eventually(function, timeout: int = 180):
    end, last = time.monotonic() + timeout, None
    while time.monotonic() < end:
        try:
            return function()
        except (RuntimeError, ValueError, URLError, TimeoutError) as error:
            last = error
            time.sleep(3)
    raise RuntimeError(f"condition not met: {last}")


def resource(source: str, kind: str, name: str) -> str:
    # Repository resources end at column zero. Fail on missing/duplicate matches.
    blocks = re.findall(rf'^resource "{re.escape(kind)}" "{re.escape(name)}" \{{(.*?)^\}}', source, re.M | re.S)
    require(len(blocks) == 1, f"missing/duplicate resource {kind}.{name}")
    return blocks[0]


def evaluate(expression: str, variables: dict, directory: Path):
    """Let Terraform evaluate real HCL; do not approximate conditionals in Python."""
    directory.mkdir()
    declarations = "\n".join(f'variable "{name}" {{}}' for name in variables)
    (directory / "main.tf").write_text(declarations + f"\nlocals {{\n  result = {expression}\n}}\n")
    (directory / "fixture.auto.tfvars.json").write_text(json.dumps(variables))
    output = run("terraform", "console", "-no-color", data="jsonencode(local.result)\n", cwd=directory)
    return json.loads(json.loads(output))


def one(docs: list[dict], kind: str) -> dict:
    matches = [d for d in docs if d.get("kind") == kind]
    require(len(matches) == 1, f"expected one {kind}, got {len(matches)}")
    return matches[0]


def contract(old: list[dict], new: list[dict]) -> None:
    for kind in ("Deployment", "DaemonSet"):
        a, b = one(old, kind), one(new, kind)
        require(a["metadata"]["name"] == b["metadata"]["name"], f"{kind} renamed")
        require(a["spec"]["selector"] == b["spec"]["selector"], f"{kind} selector changed")
        p, q = (x["spec"]["template"]["spec"] for x in (a, b))
        for field in ("nodeSelector", "tolerations", "serviceAccountName"):
            require(p.get(field) == q.get(field), f"{kind} {field} changed")
        require(p.get("hostNetwork", False) == q.get("hostNetwork", False), "host networking changed")
        for pod in (p, q):
            component = "speaker" if kind == "DaemonSet" else "controller"
            container = next(c for c in pod["containers"] if c["name"] == component)
            bgp = [e.get("value") for e in container.get("env", []) if e["name"] == "METALLB_BGP_TYPE"]
            require(bgp == ["frr"], f"{component} does not select exactly one FRR backend")
        if kind == "DaemonSet":
            require("frr" in {c["name"] for c in q["containers"]}, "FRR sidecar missing")
        else:
            controller = next(c for c in q["containers"] if c["name"] == "controller")
            require("--port=9120" in controller["args"], "expected native HTTPS metrics on 9120")
            require(controller["readinessProbe"]["httpGet"]["port"] == 17472, "health port regressed")
    require(not any("frr-k8s" in d.get("metadata", {}).get("name", "") for d in new), "FRR-K8s subchart appeared")
    for kind in ("Service", "ServiceAccount"):
        a = {d["metadata"]["name"]: d.get("spec") for d in old if d.get("kind") == kind}
        b = {d["metadata"]["name"]: d.get("spec") for d in new if d.get("kind") == kind}
        require(a == b, f"{kind} changed")
    crds = {d["metadata"]["name"]: d for d in new if d.get("kind") == "CustomResourceDefinition"}
    require({d["metadata"]["name"] for d in old if d.get("kind") == "CustomResourceDefinition"} <= crds.keys(), "CRD removed")
    for version in crds["bgppeers.metallb.io"]["spec"]["versions"]:
        props = version["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]
        for key in ("myASN", "peerASN", "localASN"):
            if key in props:
                require(props[key]["format"] == "int64", f"invalid ASN schema: {version['name']}/{key}")


def assert_route(document: dict, node_ip: str) -> None:
    routes = document.get("routes", {}).get(f"{VIP}/32", [])
    require(any(r.get("valid") and r.get("bestpath") and
                any(n.get("ip") == node_ip for n in r.get("nexthops", [])) for r in routes),
            "BGP best /32 route via the kind node is missing")


@contextmanager
def forward(kube: list[str], port: int):
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen([*kube, "port-forward", "deployment/metallb-controller", f"19120:{port}", "--address=127.0.0.1"], stdout=log, stderr=log)
        try:
            yield
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", CONTEXT, "-n", NAMESPACE]
    require(run(*kube, "config", "current-context").strip() == CONTEXT, "refusing non-CI context")
    helm = ["helm", "--kubeconfig", args.kubeconfig, "--kube-context", CONTEXT, "--namespace", NAMESPACE]
    import yaml
    with tempfile.TemporaryDirectory(prefix="metallb-upgrade-") as temp:
        root = Path(temp)
        def ip(container):
            return json.loads(run("docker", "inspect", container))[0]["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
        node_ip = ip("metallb-upgrade-ci-control-plane")
        (root / "daemons").write_text('zebra=yes\nbgpd=yes\nvtysh_enable=yes\nzebra_options=" -A 127.0.0.1 -s 90000000"\nbgpd_options=" -A 127.0.0.1"\n')
        (root / "frr.conf").write_text(f"frr defaults traditional\nlog stdout\nrouter bgp 64521\n bgp router-id 192.0.2.1\n no bgp ebgp-requires-policy\n neighbor {node_ip} remote-as 64520\n address-family ipv4 unicast\n  neighbor {node_ip} activate\n exit-address-family\n")
        run("docker", "run", "-d", "--name", PEER, "--network", "kind", "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW",
            "-v", f"{root / 'daemons'}:/etc/frr/daemons:ro", "-v", f"{root / 'frr.conf'}:/etc/frr/frr.conf:ro", FRR)
        try:
            variables = {"addon_node_selector": {}, "metallb_tolerate_control_plane": True,
                         "metallb_address_pool": ["192.0.2.240/32"], "metallb_namespace": NAMESPACE,
                         "metallb_bgp_address_pool": [f"{VIP}/32"], "metallb_bgp_my_asn": 64520,
                         "metallb_bgp_peer_asn": 64521, "metallb_bgp_peer_address": ip(PEER)}
            releases, rendered, configs = [], [], []
            for label, ref in (("baseline", BASELINE), ("candidate", None)):
                def read(path):
                    return run("git", "show", f"{ref}:{path}") if ref else (ROOT / path).read_text()
                block = resource(read(CRDS), "helm_release", "metallb")
                repository, version = [re.search(rf'\b{k}\s*=\s*"([^"]+)"', block)[1] for k in ("repository", "version")]
                values_expr = re.search(r'\bvalues\s*=\s*(\[yamlencode\(.*\)\])', block, re.S)
                require(values_expr is not None, "unsupported MetalLB values expression")
                set_expr = re.search(r'\bset\s*=\s*(\[.*?\n  \])', block, re.S)
                require(set_expr is not None, "CRD setting missing")
                expressions = {"values": values_expr[1], "sets": set_expr[1]}
                source = read(ADDONS)
                names = re.findall(r'^resource "kubernetes_manifest" "(metallb_[^"]+)"', source, re.M)
                require(len(names) == 5, "MetalLB configuration resources changed; review test")
                for name in names:
                    entry = resource(source, "kubernetes_manifest", name)
                    manifest = re.search(r'\bmanifest\s*=\s*(\{.*?\n  \})', entry, re.S)
                    require(manifest is not None, f"manifest not found: {name}")
                    expressions[name] = manifest[1]
                expression = "{\n" + "\n".join(f"{k} = {v}" for k, v in expressions.items()) + "\n}"
                result = evaluate(expression, variables, root / label)
                require(result["sets"] == [{"name": "crds.enabled", "value": "true"}], "CRD ownership changed")
                values = root / f"{label}.yaml"
                require(len(result["values"]) == 1, "unexpected Helm values layer")
                values.write_text(result["values"][0])
                charts = root / "charts"
                charts.mkdir(exist_ok=True)
                run("helm", "pull", "metallb", "--repo", repository, "--version", version, "--destination", str(charts))
                chart = str(charts / f"metallb-{version}.tgz")
                documents = list(yaml.safe_load_all(run("helm", "template", "metallb", chart, "--namespace", NAMESPACE, "-f", str(values), "--set", "crds.enabled=true")))
                rendered.append([d for d in documents if d])
                releases.append((label, chart, str(values)))
                configs.append([result[name] for name in sorted(names)])
            contract(*rendered)
            require(configs[0] == configs[1], "pool/BGPPeer/advertisement settings changed")
            for docs in rendered:
                for kind in ("Deployment", "DaemonSet"):
                    pod = one(docs, kind)["spec"]["template"]["spec"]
                    for image in {c["image"] for c in pod["containers"] + pod.get("initContainers", [])}:
                        platform = json.loads(run("docker", "manifest", "inspect", image))
                        require(any(m.get("platform", {}).get("architecture") == "arm64" and m["platform"].get("os") == "linux" for m in platform.get("manifests", [])), f"arm64 image absent: {image}")
            print("PASS: actual Terraform values and five CRs, FRR backend, identities, ASN schema, arm64 manifests", flush=True)
            run(*kube, "create", "namespace", NAMESPACE)
            run(*kube, "label", "node", "metallb-upgrade-ci-control-plane", "node.kubernetes.io/exclude-from-external-load-balancers-")
            selected = releases[1:] if args.fresh else releases
            identities = None
            def identity():
                targets = [("deployment", "metallb-controller"), ("daemonset", "metallb-speaker"), ("service", "upgrade-echo")]
                targets += [(d["kind"], d["metadata"]["name"]) for d in configs[0]]
                targets += [("crd", d["metadata"]["name"]) for d in rendered[0] if d.get("kind") == "CustomResourceDefinition"]
                result = {}
                for kind, name in targets:
                    obj = json.loads(run(*kube, "get", kind, name, "-o", "json"))
                    value = {"uid": obj["metadata"]["uid"]}
                    if kind in {d["kind"] for d in configs[0]}:
                        value["spec"] = obj["spec"]
                    result[f"{kind}/{name}"] = value
                return result
            def traffic():
                table = json.loads(run("docker", "exec", PEER, "vtysh", "-c", "show bgp ipv4 unicast json"))
                assert_route(table, node_ip)
                route = json.loads(run("docker", "exec", PEER, "ip", "-j", "route", "get", VIP))
                require(route[0].get("gateway") == node_ip, "kernel is not using the learned BGP route")
                output = run("docker", "run", "--rm", "--network", f"container:{PEER}", CLIENT, "python", "-c",
                             f"from urllib.request import urlopen; print(urlopen('http://{VIP}/', timeout=10).read().decode())", timeout=30)
                require("Directory listing" in output, "VIP HTTP response missing")
                svc = json.loads(run(*kube, "get", "service", "upgrade-echo", "-o", "json"))
                ingress = svc["status"]["loadBalancer"].get("ingress", [])
                require(len(ingress) == 1 and ingress[0].get("ip") == VIP and
                        ingress[0].get("ipMode", "VIP") == "VIP", "VIP allocation changed")
            for index, (label, chart, values) in enumerate(selected):
                run(*helm, "upgrade", "--install", "metallb", chart, "--reset-values", "--wait", "--timeout", "5m", "-f", values, "--set", "crds.enabled=true", timeout=330)
                for kind, name in (("deployment", "metallb-controller"), ("daemonset", "metallb-speaker")):
                    run(*kube, "rollout", "status", f"{kind}/{name}", "--timeout=180s")
                if index == 0:
                    run(*kube, "apply", "-f", "-", data=yaml.safe_dump_all(configs[0]))
                    app = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "upgrade-echo"}, "spec": {
                        "replicas": 1, "selector": {"matchLabels": {"app": "upgrade-echo"}}, "template": {"metadata": {"labels": {"app": "upgrade-echo"}}, "spec": {
                            "automountServiceAccountToken": False, "containers": [{"name": "echo", "image": CLIENT, "command": ["python", "-m", "http.server", "8080"], "ports": [{"containerPort": 8080}]}]}}}}
                    service = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "upgrade-echo", "annotations": {"metallb.universe.tf/address-pool": "vip-bgp-pool"}},
                               "spec": {"selector": {"app": "upgrade-echo"}, "type": "LoadBalancer", "loadBalancerIP": VIP, "externalTrafficPolicy": "Local", "ports": [{"port": 80, "targetPort": 8080}]}}
                    run(*kube, "apply", "-f", "-", data=yaml.safe_dump_all([app, service]))
                    run(*kube, "rollout", "status", "deployment/upgrade-echo", "--timeout=180s")
                eventually(traffic)
                current = identity()
                require(identities is None or current == identities, "upgrade recreated workloads/CRDs/CRs/Service")
                identities = current
                print(f"PASS: {label}; established BGP, learned /32, VIP HTTP, resource UIDs", flush=True)
            run(*kube, "create", "serviceaccount", "metrics-reader")
            run(*kube, "create", "clusterrole", "metallb-upgrade-metrics", "--verb=get", "--non-resource-url=/metrics")
            run(*kube, "create", "clusterrolebinding", "metallb-upgrade-metrics", "--clusterrole=metallb-upgrade-metrics", f"--serviceaccount={NAMESPACE}:metrics-reader")
            token = run(*kube, "create", "token", "metrics-reader", "--duration=10m").strip()
            # Only the disposable endpoint's autogenerated self-signed cert is skipped.
            context = ssl._create_unverified_context()
            with forward(kube, 9120):
                def metrics():
                    request = Request("https://127.0.0.1:19120/metrics", headers={"Authorization": f"Bearer {token}"})
                    with urlopen(request, context=context, timeout=5) as response:
                        require("metallb_" in response.read().decode(), "MetalLB metrics absent")
                eventually(metrics)
                try:
                    urlopen("https://127.0.0.1:19120/metrics", context=context, timeout=5)
                except HTTPError as error:
                    require(error.code in (401, 403), "unexpected unauthenticated status")
                else:
                    raise ValueError("metrics unexpectedly accessible without authentication")
            print("PASS: HTTPS metrics with scoped token; unauthenticated scrape denied", flush=True)
            run(*kube, "rollout", "restart", "daemonset/metallb-speaker")
            run(*kube, "rollout", "status", "daemonset/metallb-speaker", "--timeout=180s")
            eventually(traffic)
            require(identity() == identities, "restart changed identities")
            print("PASS: speaker restart reestablished BGP and VIP HTTP without reallocation", flush=True)
        except Exception:
            for command in (["docker", "logs", "--tail", "80", PEER],
                            ["docker", "exec", PEER, "vtysh", "-c", "show bgp summary json"],
                            ["docker", "exec", PEER, "vtysh", "-c", "show bgp ipv4 unicast json"]):
                subprocess.run(command, timeout=30, check=False)
            raise
        finally:
            subprocess.run(["docker", "rm", "-f", PEER], capture_output=True, timeout=30, check=False)


if __name__ == "__main__":
    main()
