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
                       prometheus_site: dict | None = None, rag_address: str = "",
                       llm_address: str = "",
                       desktop_host_address: str = "", typevt_host_address: str = "",
                       include_attention: bool = False,
                       include_prod_host: bool = False, nas_address: str = "",
                       nas_username: str = "prometheus", include_nas_syslog: bool = False,
                       include_security: bool = False, security_alerts_paused: bool = False,
                       security_channel_id: str = "") -> dict:
    if not isinstance(include_security, bool) or not isinstance(security_alerts_paused, bool):
        raise ValueError("invalid security monitoring opt-in")
    if security_alerts_paused and not include_security:
        raise ValueError("pausing alerts requires keeping their provisioning enabled")
    if include_security and prometheus_site is None:
        raise ValueError("security alerts require the existing production monitoring site")
    if not isinstance(nas_address, str):
        raise ValueError("invalid NAS address")
    if include_nas_syslog and (not nas_address or not prometheus_site
                              or not prometheus_site.get("prod_metrics_target")):
        raise ValueError("NAS syslog requires a NAS address and an explicit production node")
    if folder_uid and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", folder_uid):
        raise ValueError("invalid folder UID")
    host_addresses = {name: address for name, address in
                      (("desktop", desktop_host_address), ("typevt", typevt_host_address)) if address}
    manifest = build_manifest(include_rag=include_rag, folder_uid=folder_uid,
                              include_host_monitoring=bool(host_addresses),
                              include_attention=include_attention)
    if rag_address and not include_rag:
        raise ValueError("RAG scrape requires the dashboard")
    if include_rag and (prometheus_site is None or not rag_address):
        raise ValueError("RAG requires both a Prometheus site and a scrape address")
    if host_addresses and prometheus_site is None:
        raise ValueError("host monitoring requires the Prometheus site")
    if llm_address and prometheus_site is None:
        raise ValueError("LLM monitoring requires the Prometheus site")
    if include_prod_host and (prometheus_site is None or not prometheus_site.get("prod_metrics_target")):
        raise ValueError("production host exporter requires an explicit prod metrics target")
    if prometheus_site is not None:
        manifest["items"].extend(prometheus_manifest(
            prometheus_site, rag_address=rag_address, llm_address=llm_address,
            host_addresses=host_addresses)["items"])
    if include_prod_host:
        from prod_node_host_monitoring import add_dashboard, exporter
        add_dashboard(manifest)
        manifest["items"].append(exporter(prometheus_site["prod_metrics_target"]["name"]))
    if nas_address:
        if prometheus_site is None:
            raise ValueError("NAS monitoring requires the Prometheus site")
        from prod_nas_monitoring import add_monitoring
        add_monitoring(manifest, nas_address, nas_username)
    if include_nas_syslog:
        from prod_nas_syslog import add_syslog
        add_syslog(manifest, nas_address, prometheus_site["prod_metrics_target"]["name"])
    if include_security:
        from prod_openwrt_security_monitoring import add_monitoring as add_security
        add_security(manifest, security_channel_id, paused=security_alerts_paused)
    for obj in manifest["items"]:
        # Kubernetes omits an empty ingress list on read. With policyTypes=Ingress,
        # absent and empty both mean deny all ingress; avoid an artificial import diff.
        if (obj["kind"] == "NetworkPolicy" and obj["spec"].get("ingress") == []
                and "Ingress" in obj["spec"].get("policyTypes", [])):
            del obj["spec"]["ingress"]
    return manifest


def main() -> None:
    query = json.load(sys.stdin)
    if set(query) - {"rag_enabled", "folder_uid", "prometheus_site_path", "rag_address", "llm_address",
                      "desktop_host_address", "typevt_host_address", "attention_enabled", "prod_host_enabled",
                      "nas_address", "nas_username", "nas_syslog_enabled", "security_enabled", "security_alerts_paused", "security_channel_id"}:
        raise ValueError("unexpected renderer input")
    if query.get("rag_enabled", "false") not in ("true", "false"):
        raise ValueError("invalid rag_enabled")
    if query.get("attention_enabled", "false") not in ("true", "false"):
        raise ValueError("invalid attention_enabled")
    if query.get("prod_host_enabled", "false") not in ("true", "false"):
        raise ValueError("invalid prod_host_enabled")
    if query.get("nas_syslog_enabled", "false") not in ("true", "false"):
        raise ValueError("invalid nas_syslog_enabled")
    if query.get("security_enabled", "false") not in ("true", "false"):
        raise ValueError("invalid security_enabled")
    if query.get("security_alerts_paused", "false") not in ("true", "false"):
        raise ValueError("invalid security_alerts_paused")
    site_path = query.get("prometheus_site_path", "")
    site = None
    if site_path:
        path = Path(site_path)
        if not path.is_absolute() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("site must be an absolute, owner-only regular file")
        site = json.loads(path.read_text())
    manifest = terraform_manifest(include_rag=query.get("rag_enabled") == "true",
                                  folder_uid=query.get("folder_uid", ""),
                                  prometheus_site=site, rag_address=query.get("rag_address", ""),
                                  llm_address=query.get("llm_address", ""),
                                  desktop_host_address=query.get("desktop_host_address", ""),
                                  typevt_host_address=query.get("typevt_host_address", ""),
                                  include_attention=query.get("attention_enabled") == "true",
                                  include_prod_host=query.get("prod_host_enabled") == "true",
                                  nas_address=query.get("nas_address", ""),
                                  nas_username=query.get("nas_username", "prometheus"),
                                  include_nas_syslog=query.get("nas_syslog_enabled") == "true",
                                  include_security=query.get("security_enabled") == "true",
                                  security_alerts_paused=query.get("security_alerts_paused") == "true",
                                  security_channel_id=query.get("security_channel_id", ""))
    print(json.dumps({"manifest": json.dumps(manifest, sort_keys=True)}))


if __name__ == "__main__":
    main()
