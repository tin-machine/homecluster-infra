#!/usr/bin/env python3
"""Render a bounded, opt-in prod outside-in monitoring MVP from private site input.

Uses only Python stdlib. No Kubernetes API access or apply; rendered manifest
contains site-local target URLs and MUST remain outside public source control.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


NAMESPACE = "observability-prod"
BLACKBOX_IMAGE = "quay.io/prometheus/blackbox-exporter:v0.28.0"
PROMETHEUS_IMAGE = "quay.io/prometheus/prometheus:v3.14.0"
NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,40}[a-z0-9]$")
DNS_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


def _names(items: object, key: str, maximum: int) -> list[dict]:
    if not isinstance(items, list) or len(items) > maximum:
        raise ValueError(f"{key} must be a list of at most {maximum} entries")
    if key == "api_targets" and not items:
        raise ValueError("at least one staging API /readyz target is required")
    result = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"name", "target"}:
            raise ValueError(f"{key} items must contain only name and target")
        name, target = item["name"], item["target"]
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ValueError("invalid target name")
        if not isinstance(target, str) or len(target) > 256 or any(c.isspace() for c in target):
            raise ValueError("invalid target")
        result.append({"name": name, "target": target})
    return result


def _valid_host(host: str | None) -> bool:
    if not host or len(host) > 253:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return bool(DNS_RE.fullmatch(host) and "." in host and ".." not in host)
    return isinstance(ip, ipaddress.IPv4Address) and not (
        ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
    )


def validate_site(document: object) -> dict:
    if not isinstance(document, dict) or set(document) != {
        "api_targets", "tcp_targets", "egress_cidrs", "api_ca_secret"
    }:
        raise ValueError("site input must declare exactly four documented keys")
    api = _names(document["api_targets"], "api_targets", 4)
    tcp = _names(document["tcp_targets"], "tcp_targets", 8)
    seen = set()
    for entry in api + tcp:
        if entry["name"] in seen:
            raise ValueError("target names must be globally unique")
        seen.add(entry["name"])
    for entry in api:
        url = urlsplit(entry["target"])
        if (
            url.scheme != "https" or not _valid_host(url.hostname)
            or url.username is not None or url.password is not None
            or url.query or url.fragment or url.path != "/readyz"
            or not url.netloc or url.port not in (443, 6443)
        ):
            raise ValueError("API targets must be HTTPS /readyz on an explicit port without credentials, query or fragment")
    for entry in tcp:
        # Validate as host:port, with no paths, schemes, userinfo or IPv6.
        parsed = urlsplit("tcp://" + entry["target"])
        if (
            not _valid_host(parsed.hostname) or not parsed.port
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
            or ":" not in entry["target"] or parsed.port != 9100
        ):
            raise ValueError("TCP target must be a host:port")
    cidrs = document["egress_cidrs"]
    if not isinstance(cidrs, list) or not cidrs or len(cidrs) > 12:
        raise ValueError("egress_cidrs must contain 1-12 explicitly scoped IPv4 CIDRs")
    result_cidrs = []
    for value in cidrs:
        if not isinstance(value, str):
            raise ValueError("egress CIDR must be a string")
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise ValueError("invalid egress CIDR") from exc
        if not isinstance(network, ipaddress.IPv4Network) or network.prefixlen < 24:
            raise ValueError("egress CIDR must be IPv4 with prefix length >= 24")
        if network.is_loopback or network.is_link_local or network.is_multicast or network.is_unspecified:
            raise ValueError("unsafe egress CIDR")
        result_cidrs.append(str(network))
    if len(set(result_cidrs)) != len(result_cidrs):
        raise ValueError("duplicate egress CIDR")
    secret = document["api_ca_secret"]
    if secret is not None and (
        not isinstance(secret, str) or not NAME_RE.fullmatch(secret)
    ):
        raise ValueError("api_ca_secret must be null or Kubernetes DNS name")
    return {"api_targets": api, "tcp_targets": tcp, "egress_cidrs": result_cidrs, "api_ca_secret": secret}


def _meta(name: str, labels: dict | None = None) -> dict:
    result = {"name": name, "namespace": NAMESPACE}
    if labels:
        result["labels"] = labels
    return result


def _obj(kind: str, api_version: str, name: str, **parts: object) -> dict:
    return {"apiVersion": api_version, "kind": kind, "metadata": _meta(name), **parts}


def _pod_labels(name: str) -> dict:
    return {"app.kubernetes.io/name": name, "app.kubernetes.io/part-of": "prod-outside-in"}


def _container(name: str, image: str, port: int, args: list[str], mounts: list[dict], resources: dict) -> dict:
    return {
        "name": name,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "args": args,
        "ports": [{"name": "http", "containerPort": port, "protocol": "TCP"}],
        "resources": resources,
        "securityContext": {
            "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"}
        },
        "volumeMounts": mounts,
    }


def _deployment(name: str, container: dict, volumes: list[dict], config_text: str, group: int = 65534) -> dict:
    labels = _pod_labels(name)
    # Changing the mounted ConfigMap does not reload these processes by default.
    # Tie Pod-template identity to the exact ConfigMap data to trigger a rollout.
    config_sha256 = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    container["readinessProbe"] = {"httpGet": {"path": "/-/ready" if name == "prod-prometheus" else "/", "port": "http"}, "periodSeconds": 10}
    container["livenessProbe"] = {"httpGet": {"path": "/-/healthy" if name == "prod-prometheus" else "/", "port": "http"}, "periodSeconds": 20}
    return _obj(
        "Deployment", "apps/v1", name,
        spec={
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels, "annotations": {"checksum/config": config_sha256}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {"runAsUser": 65534, "runAsGroup": 65534, "runAsNonRoot": True, "fsGroup": group},
                    "containers": [container],
                    "volumes": volumes,
                    "terminationGracePeriodSeconds": 30,
                },
            },
        },
    )


def _service(name: str, port: int) -> dict:
    return _obj("Service", "v1", name, spec={
        "type": "ClusterIP", "selector": _pod_labels(name),
        "ports": [{"name": "http", "port": port, "targetPort": "http", "protocol": "TCP"}],
    })


def _configmap(name: str, data: dict) -> dict:
    return _obj("ConfigMap", "v1", name, data=data)


def build_manifest(site: dict) -> dict:
    site = validate_site(site)
    secret = site["api_ca_secret"]
    # Prometheus YAML emitted as JSON: JSON is a valid YAML input for Prometheus.
    bb_modules = {
        "http_readyz": {
            "prober": "http", "timeout": "8s",
            "http": {
                "method": "GET", "valid_status_codes": [200],
                "follow_redirects": False, "preferred_ip_protocol": "ip4",
                "fail_if_body_not_matches_regexp": ["^ok\\s*$"],
            },
        },
        "tcp_connect": {"prober": "tcp", "timeout": "5s", "tcp": {"preferred_ip_protocol": "ip4"}},
    }
    if secret:
        bb_modules["http_readyz"]["http"]["tls_config"] = {"ca_file": "/etc/probe-ca/ca.crt"}

    def job(kind: str, targets: list[dict], module: str) -> dict:
        return {
            "job_name": "staging-" + kind,
            "metrics_path": "/probe",
            "params": {"module": [module]},
            "scrape_interval": "30s",
            "scrape_timeout": "10s",
            "static_configs": [
                {"targets": [x["target"]], "labels": {"probe_name": x["name"]}}
                for x in targets
            ],
            "relabel_configs": [
                {"source_labels": ["__address__"], "target_label": "__param_target"},
                {"source_labels": ["__param_target"], "target_label": "instance"},
                {"target_label": "__address__", "replacement": "prod-blackbox-exporter:9115"},
            ],
        }

    jobs = [
        {"job_name": "prod-prometheus-self", "static_configs": [{"targets": ["127.0.0.1:9090"]}]},
        job("api", site["api_targets"], "http_readyz"),
    ]
    if site["tcp_targets"]:
        jobs.append(job("tcp", site["tcp_targets"], "tcp_connect"))
    prometheus_cfg = {"global": {"scrape_interval": "30s", "evaluation_interval": "30s"}, "scrape_configs": jobs}
    blackbox_config_text = json.dumps({"modules": bb_modules}, sort_keys=True)
    prometheus_config_text = json.dumps(prometheus_cfg, sort_keys=True)

    bb_volumes = [{"name": "config", "configMap": {"name": "prod-blackbox-config"}}]
    bb_mounts = [{"name": "config", "mountPath": "/etc/blackbox", "readOnly": True}]
    if secret:
        bb_volumes.append({"name": "ca", "secret": {"secretName": secret, "items": [{"key": "ca.crt", "path": "ca.crt"}]}})
        bb_mounts.append({"name": "ca", "mountPath": "/etc/probe-ca", "readOnly": True})
    bb_container = _container(
        "blackbox", BLACKBOX_IMAGE, 9115,
        ["--config.file=/etc/blackbox/config.json", "--web.listen-address=:9115"],
        bb_mounts,
        {"requests": {"cpu": "25m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "128Mi"}},
    )
    prom_container = _container(
        "prometheus", PROMETHEUS_IMAGE, 9090,
        ["--config.file=/etc/prometheus/prometheus.json", "--storage.tsdb.path=/prometheus",
         "--storage.tsdb.retention.time=7d", "--storage.tsdb.retention.size=2GB",
         "--web.listen-address=:9090"],
        [{"name": "config", "mountPath": "/etc/prometheus", "readOnly": True},
         {"name": "data", "mountPath": "/prometheus"}],
        {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "350m", "memory": "512Mi"}},
    )
    blackbox = "prod-blackbox-exporter"
    prometheus = "prod-prometheus"
    dns_egress = [
        {"ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]},
    ]
    exporter_egress = dns_egress + [{
        "to": [{"ipBlock": {"cidr": network}} for network in site["egress_cidrs"]],
        "ports": [{"protocol": "TCP", "port": 443}, {"protocol": "TCP", "port": 6443}, {"protocol": "TCP", "port": 9100}],
    }]
    # egress ports are deliberately not arbitrary: only HTTPS/k3s API/node metric TCP.
    items = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE, "labels": {"app.kubernetes.io/part-of": "prod-outside-in"}}},
        _configmap("prod-blackbox-config", {"config.json": blackbox_config_text}),
        _configmap("prod-prometheus-config", {"prometheus.json": prometheus_config_text}),
        _obj("PersistentVolumeClaim", "v1", "prod-prometheus-data", spec={
            "accessModes": ["ReadWriteOnce"], "storageClassName": "local-path",
            "volumeMode": "Filesystem", "resources": {"requests": {"storage": "4Gi"}},
        }),
        _deployment(blackbox, bb_container, bb_volumes, blackbox_config_text),
        _deployment(prometheus, prom_container, [
            {"name": "config", "configMap": {"name": "prod-prometheus-config"}},
            {"name": "data", "persistentVolumeClaim": {"claimName": "prod-prometheus-data"}},
        ], prometheus_config_text),
        _service(blackbox, 9115),
        _service(prometheus, 9090),
        _obj("NetworkPolicy", "networking.k8s.io/v1", "prod-blackbox-restricted",
             spec={
                "podSelector": {"matchLabels": _pod_labels(blackbox)}, "policyTypes": ["Ingress", "Egress"],
                "ingress": [{"from": [{"podSelector": {"matchLabels": _pod_labels(prometheus)}}],
                             "ports": [{"protocol": "TCP", "port": 9115}]}],
                "egress": exporter_egress,
             }),
        _obj("NetworkPolicy", "networking.k8s.io/v1", "prod-prometheus-restricted",
             spec={
                "podSelector": {"matchLabels": _pod_labels(prometheus)}, "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": dns_egress + [{"to": [{"podSelector": {"matchLabels": _pod_labels(blackbox)}}],
                                         "ports": [{"protocol": "TCP", "port": 9115}]}],
             }),
    ]
    return {"apiVersion": "v1", "kind": "List", "items": items}


def _write_private(output: Path, document: dict) -> None:
    if output.is_symlink() or output.exists():
        raise ValueError("output must not exist (refuse overwrite/symlink)")
    parent = output.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("output parent must be an existing real directory")
    stat = parent.stat()
    if stat.st_uid != os.geteuid() or (stat.st_mode & 0o077) != 0:
        raise ValueError("output directory must be owner-only (mode 0700)")
    if output.suffix != ".json":
        raise ValueError("output must be a .json file")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as dest:
            json.dump(document, dest, indent=2)
            dest.write("\n")
            dest.flush()
            os.fsync(dest.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-config", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="new file in 0700 private directory")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only == bool(args.output):
        parser.error("specify exactly one of --validate-only or --output")
    try:
        if args.site_config.is_symlink():
            raise ValueError("site input must not be symlink")
        info = args.site_config.stat()
        if not args.site_config.is_file() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("site input must be a private regular file owned by the operator")
        site = json.loads(args.site_config.read_text(encoding="utf-8"))
        manifest = build_manifest(site)
        if args.output:
            _write_private(args.output, manifest)
        print("outside-in manifest validated" if args.validate_only else "outside-in manifest written")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        # Do not disclose local site endpoints or credential-bearing inputs.
        print(f"outside-in render refused: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
