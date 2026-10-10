#!/usr/bin/env python3
"""Source-only O3 alert evaluation rules; no cluster, private site or notification I/O.

Produces one additive ConfigMap. A separate reviewed, operator-authorized
controller operation is required to mount the rules in the existing Prometheus.
No receiver/Alertmanager is configured by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

NAMESPACE = "observability-prod"
RULE_CONFIGMAP = "prod-staging-alert-rules"
RULE_KEY = "alerts.json"
RULE_MOUNT = "/etc/prometheus/rules"
RULE_PATH = f"{RULE_MOUNT}/{RULE_KEY}"
GROUP_NAME = "staging-outside-in"
EXPECTED_STAGING_NODES = 4


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _alert(name: str, expr: str, hold: str, severity: str, summary: str) -> dict:
    return {
        "alert": name,
        "expr": expr,
        "for": hold,
        "labels": {"severity": severity, "scope": "staging", "source": "prod-outside-in"},
        "annotations": {"summary": summary},
    }


def rules() -> dict:
    # 'probe_success' measures the probed API/TCP target, whereas the 'up'
    # of these jobs tests whether Prometheus can scrape the Blackbox exporter.
    # Direct node-exporter 'up' is a third, independent signal.
    #
    # A missing configured series doesn't match "up == 0". Explicit
    # vector(0) fallbacks cover loss of an entire discovery/target group.
    return {
        "groups": [{
            "name": GROUP_NAME,
            "interval": "30s",
            "rules": [
                _alert(
                    "StagingAPIProbeFailed",
                    'probe_success{job="staging-api"} == 0',
                    "2m", "critical",
                    "Staging Kubernetes API /readyz probe has failed",
                ),
                _alert(
                    "StagingAPIBlackboxScrapeFailed",
                    'up{job="staging-api"} == 0',
                    "2m", "warning",
                    "Prometheus cannot scrape the staging API Blackbox probe",
                ),
                _alert(
                    "StagingAPIProbeTargetsMissing",
                    '(count(up{job="staging-api"}) or vector(0)) < 1',
                    "5m", "warning",
                    "No staging API Blackbox scrape target is present",
                ),
                _alert(
                    "StagingTCPProbeFailed",
                    'probe_success{job="staging-tcp"} == 0',
                    "3m", "warning",
                    "Staging TCP probe has failed",
                ),
                _alert(
                    "StagingTCPBlackboxScrapeFailed",
                    'up{job="staging-tcp"} == 0',
                    "3m", "warning",
                    "Prometheus cannot scrape a staging TCP Blackbox probe",
                ),
                _alert(
                    "StagingNodeExporterScrapeFailed",
                    'up{job="staging-node-exporter"} == 0',
                    "3m", "warning",
                    "Prometheus cannot scrape a staging node-exporter",
                ),
                _alert(
                    "StagingNodeExporterTargetsMissing",
                    '(count(up{job="staging-node-exporter"}) or vector(0)) < 4',
                    "5m", "warning",
                    "Fewer than the expected four node-exporter scrape targets are present",
                ),
            ],
        }],
    }


def build_manifest() -> dict:
    return {
        "apiVersion": "v1", "kind": "List",
        "items": [{
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": RULE_CONFIGMAP, "namespace": NAMESPACE},
            "data": {RULE_KEY: _canonical(rules())},
        }],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="create new source-only JSON file; refuse overwrite")
    args = parser.parse_args()
    payload = (_canonical(build_manifest()) + "\n").encode("utf-8")
    if args.output is None:
        sys.stdout.buffer.write(payload)
    else:
        if args.output.exists() or args.output.is_symlink():
            parser.error("refusing to overwrite output")
        with args.output.open("xb") as handle:
            handle.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
