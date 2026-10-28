#!/usr/bin/env python3
"""Return existing public Grafana manifests for Terraform's external data source."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from prod_grafana_render import build_manifest
from prod_prometheus_config_render import build_manifest as prometheus_manifest


def terraform_manifest(*, include_rag: bool = False, folder_uid: str = "",
                       prometheus_site: dict | None = None, rag_address: str = "") -> dict:
    if folder_uid and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", folder_uid):
        raise ValueError("invalid folder UID")
    manifest = build_manifest(include_rag=include_rag, folder_uid=folder_uid)
    if rag_address and not include_rag:
        raise ValueError("RAG scrape requires the dashboard")
    if include_rag and (prometheus_site is None or not rag_address):
        raise ValueError("RAG requires both a Prometheus site and a scrape address")
    if prometheus_site is not None:
        manifest["items"].extend(prometheus_manifest(
            prometheus_site, rag_address=rag_address)["items"])
    for obj in manifest["items"]:
        # Kubernetes omits an empty ingress list on read. With policyTypes=Ingress,
        # absent and empty both mean deny all ingress; avoid an artificial import diff.
        if (obj["kind"] == "NetworkPolicy" and obj["spec"].get("ingress") == []
                and "Ingress" in obj["spec"].get("policyTypes", [])):
            del obj["spec"]["ingress"]
    return manifest


def main() -> None:
    query = json.load(sys.stdin)
    if set(query) - {"rag_enabled", "folder_uid", "prometheus_site_path", "rag_address"}:
        raise ValueError("unexpected renderer input")
    if query.get("rag_enabled", "false") not in ("true", "false"):
        raise ValueError("invalid rag_enabled")
    site_path = query.get("prometheus_site_path", "")
    site = None
    if site_path:
        path = Path(site_path)
        if not path.is_absolute() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("site must be an absolute, owner-only regular file")
        site = json.loads(path.read_text())
    manifest = terraform_manifest(include_rag=query.get("rag_enabled") == "true",
                                  folder_uid=query.get("folder_uid", ""),
                                  prometheus_site=site, rag_address=query.get("rag_address", ""))
    print(json.dumps({"manifest": json.dumps(manifest, sort_keys=True)}))


if __name__ == "__main__":
    main()
