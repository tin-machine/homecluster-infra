#!/usr/bin/env python3
"""Render a private-site driven portal and two tailnet-only Ingress resources."""

from __future__ import annotations

import html
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

NAMESPACE = "observability-prod"
NAME = "prod-monitoring-portal"
HOST_LABEL = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _labels(name: str) -> dict:
    return {"app.kubernetes.io/name": name, "app.kubernetes.io/part-of": "home-monitoring"}


def _resource(api: str, kind: str, name: str, **values: object) -> dict:
    return {"apiVersion": api, "kind": kind,
            "metadata": {"name": name, "namespace": NAMESPACE}, **values}


def _tailnet_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("monitoring URL must be a string")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("monitoring URL has an invalid port") from error
    hostname = parsed.hostname or ""
    labels = hostname.split(".")
    if (parsed.scheme != "https" or len(labels) < 4 or labels[-2:] != ["ts", "net"] or
            any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                for part in labels) or port is not None or
            parsed.username or parsed.password or parsed.path not in ("", "/") or
            parsed.query or parsed.fragment):
        raise ValueError("monitoring URL must be a bare HTTPS tailnet hostname")
    return "https://" + hostname


def _link(base: str, uid: str, title: str) -> str:
    return (f'<a href="{html.escape(base, quote=True)}/d/{uid}">'
            f'{html.escape(title)}</a>')


def portal_html(prod_url: str, stg_url: str) -> str:
    prod = _tailnet_url(prod_url)
    stg = _tailnet_url(stg_url)
    prod_links = [
        _link(prod, "home-attention", "要対応・Warn以上ログ"),
        _link(prod, "ubuntu-desktop-host", "Ubuntu Desktop"),
        _link(prod, "typevt-host", "TypeVT"),
        _link(prod, "shared-rag-overview", "共有RAG"),
        _link(prod, "staging-outside-in", "Staging監視のprod集約"),
    ]
    stg_links = [
        f'<a href="{html.escape(stg, quote=True)}">Staging Grafana</a>',
    ]
    return "\n".join([
        "<!doctype html>", '<html lang="ja">', "<head>",
        '<meta charset="utf-8">', '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>家庭内モニタリング</title>",
        "<style>body{font:1rem system-ui,sans-serif;max-width:50rem;margin:2rem auto;padding:0 1rem;line-height:1.6;background:#101820;color:#f2f5f8}a{display:block;padding:1rem;margin:.5rem 0;border:1px solid #6a8299;border-radius:.5rem;color:#b8e5ff;text-decoration:none}a:focus,a:hover{background:#233746}h1,h2{line-height:1.3}</style>",
        "</head><body>", "<h1>家庭内モニタリング</h1>",
        "<p>リンク先の稼働状態は各Grafanaで確認してください。</p>",
        "<h2>Production</h2>", *prod_links,
        "<h2>Staging</h2>", *stg_links,
        "</body></html>", "",
    ])


def _ingress(name: str, host: str, service: str, port: int) -> dict:
    if not HOST_LABEL.fullmatch(host) or host.endswith("-"):
        raise ValueError("tailnet ingress host must be a DNS label")
    return _resource("networking.k8s.io/v1", "Ingress", name, spec={
        "ingressClassName": "tailscale", "tls": [{"hosts": [host]}],
        "rules": [{"http": {"paths": [{
            "path": "/", "pathType": "Prefix",
            "backend": {"service": {"name": service, "port": {"number": port}}},
        }]}}],
    })


def build_manifest(site: dict) -> dict:
    if set(site) != {"prod_grafana_url", "stg_grafana_url", "portal_hostname",
                     "prod_grafana_hostname"}:
        raise ValueError("portal site requires four exact fields")
    prod = _tailnet_url(site["prod_grafana_url"])
    stg = _tailnet_url(site["stg_grafana_url"])
    if urlsplit(prod).hostname.split(".")[0] != site["prod_grafana_hostname"]:
        raise ValueError("prod Grafana URL and Ingress name disagree")
    if (prod == stg or site["portal_hostname"] == site["prod_grafana_hostname"] or
            site["portal_hostname"] == urlsplit(stg).hostname.split(".")[0]):
        raise ValueError("portal, production and staging tailnet names must differ")
    config = _resource("v1", "ConfigMap", NAME + "-content", data={
        "index.html": portal_html(prod, stg),
        "nginx.conf": """pid /tmp/nginx.pid;
events { worker_connections 128; }
http {
  access_log /dev/stdout;
  error_log /dev/stderr;
  client_body_temp_path /tmp/client_temp;
  proxy_temp_path /tmp/proxy_temp;
  fastcgi_temp_path /tmp/fastcgi_temp;
  uwsgi_temp_path /tmp/uwsgi_temp;
  scgi_temp_path /tmp/scgi_temp;
  server {
    listen 8080;
    server_name _;
    root /usr/share/nginx/html;
    location = /healthz { add_header Content-Type text/plain; return 200 'ok'; }
    location / { try_files $uri $uri/ =404; }
  }
}
""",
    })
    deployment = _resource("apps/v1", "Deployment", NAME, spec={
        "replicas": 1, "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": _labels(NAME)},
        "template": {"metadata": {"labels": _labels(NAME),
                                  "annotations": {"checksum/content": hashlib.sha256(
                                      json.dumps(config["data"], sort_keys=True).encode()).hexdigest()}},
                     "spec": {
            "automountServiceAccountToken": False,
            "securityContext": {"runAsUser": 101, "runAsGroup": 101,
                                "runAsNonRoot": True, "fsGroup": 101},
            "containers": [{
                "name": "nginx", "image": "nginx:1.30.5-alpine3.24",
                "imagePullPolicy": "IfNotPresent",
                "command": ["nginx", "-g", "daemon off;"],
                "ports": [{"name": "http", "containerPort": 8080}],
                "securityContext": {"allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "resources": {"requests": {"cpu": "10m", "memory": "16Mi"},
                              "limits": {"cpu": "100m", "memory": "64Mi"}},
                "readinessProbe": {"httpGet": {"path": "/healthz", "port": "http"},
                                   "initialDelaySeconds": 3, "periodSeconds": 10},
                "volumeMounts": [
                    {"name": "config", "mountPath": "/etc/nginx/nginx.conf",
                     "subPath": "nginx.conf", "readOnly": True},
                    {"name": "config", "mountPath": "/usr/share/nginx/html/index.html",
                     "subPath": "index.html", "readOnly": True},
                    {"name": "tmp", "mountPath": "/tmp"},
                ],
            }],
            "volumes": [
                {"name": "config", "configMap": {"name": NAME + "-content"}},
                {"name": "tmp", "emptyDir": {"sizeLimit": "16Mi"}},
            ],
        }},
    })
    service = _resource("v1", "Service", NAME, spec={
        "type": "ClusterIP", "selector": _labels(NAME),
        "ports": [{"name": "http", "port": 8080, "targetPort": "http", "protocol": "TCP"}],
    })
    from_tailnet = [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "tailscale"}}}]
    portal_policy = _resource("networking.k8s.io/v1", "NetworkPolicy", NAME + "-ingress", spec={
        "podSelector": {"matchLabels": _labels(NAME)},
        "policyTypes": ["Ingress", "Egress"],
        "ingress": [{"from": from_tailnet,
                     "ports": [{"protocol": "TCP", "port": 8080}]}],
        # Kubernetes omits an empty egress list when it reads the policy back.
        # Egress in policyTypes still denies all outbound traffic.
    })
    grafana_policy = _resource("networking.k8s.io/v1", "NetworkPolicy",
                               "prod-grafana-tailnet-ingress", spec={
        "podSelector": {"matchLabels": {"app.kubernetes.io/name": "prod-grafana",
                                        "app.kubernetes.io/part-of": "prod-outside-in"}},
        "policyTypes": ["Ingress"],
        "ingress": [{"from": from_tailnet,
                     "ports": [{"protocol": "TCP", "port": 3000}]}],
    })
    return {"apiVersion": "v1", "kind": "List", "items": [
        config, deployment, service, portal_policy, grafana_policy,
        _ingress(NAME, site["portal_hostname"], NAME, 8080),
        _ingress("prod-grafana-tailnet", site["prod_grafana_hostname"], "prod-grafana", 3000),
    ]}


def main() -> None:
    query = json.load(sys.stdin)
    if set(query) != {"portal_site_path"}:
        raise ValueError("unexpected portal renderer input")
    path = Path(query["portal_site_path"])
    stat = path.stat()
    if (not path.is_absolute() or path.is_symlink() or path.resolve().is_relative_to(REPO_ROOT) or
            not path.is_file() or
            stat.st_uid != os.getuid() or stat.st_mode & 0o077):
        raise ValueError("portal site must be an owner-only regular file outside source")
    manifest = build_manifest(json.loads(path.read_text()))
    print(json.dumps({"manifest": json.dumps(manifest, sort_keys=True)}))


if __name__ == "__main__":
    main()
