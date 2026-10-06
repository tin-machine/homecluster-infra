#!/usr/bin/env python3
"""Source contract for ARC observability namespace wiring."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGING_TF = ROOT / "terraform/env/staging/github_actions_runner.tf"
COMMON_TF = ROOT / "terraform/env/common-crds/github_actions_arc.tf"
PROMETHEUS_VALUES = ROOT / "clusters/homelab/apps/staging/values-prometheus.yaml"


def _block(text: str, header: str) -> str:
    start = text.find(header)
    if start < 0:
        raise AssertionError(f"missing HCL block: {header}")
    opening = text.find("{", start + len(header))
    if opening < 0:
        raise AssertionError(f"missing opening brace: {header}")

    depth = 0
    for index in range(opening, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unterminated HCL block: {header}")


def main() -> int:
    staging = STAGING_TF.read_text(encoding="utf-8")
    common = COMMON_TF.read_text(encoding="utf-8")
    prometheus = PROMETHEUS_VALUES.read_text(encoding="utf-8")

    if 'github_actions_runner_controller_namespace = "arc-systems"' not in staging:
        raise AssertionError("staging ARC controller namespace constant is missing")

    listener_service = _block(
        staging,
        'resource "kubernetes_service_v1" "github_actions_runner_listener_metrics"',
    )
    if "namespace = local.github_actions_runner_controller_namespace" not in listener_service:
        raise AssertionError("listener metrics Service must live in ARC controller namespace")
    if (
        '"app.kubernetes.io/component"        = "runner-scale-set-listener"'
        not in listener_service
    ):
        raise AssertionError("listener metrics Service selector is missing ARC listener component")
    if (
        '"homelab.example.com/metrics-target" = "arc-stg-ci"'
        not in listener_service
    ):
        raise AssertionError("listener metrics Service selector is missing stable metrics label")

    runner_release = _block(
        staging,
        'resource "helm_release" "github_actions_runner"',
    )
    if "autoscalingListener" not in runner_release:
        raise AssertionError("runner release does not label AutoscalingListener")
    if (
        '"homelab.example.com/metrics-target" = "arc-stg-ci"'
        not in runner_release
    ):
        raise AssertionError("AutoscalingListener stable metrics label is missing")

    controller_release = _block(
        common,
        'resource "helm_release" "github_actions_runner_controller"',
    )
    for expected in (
        'controllerManagerAddr = ":8080"',
        'listenerAddr          = ":8080"',
        'listenerEndpoint      = "/metrics"',
    ):
        if expected not in controller_release:
            raise AssertionError(f"ARC metrics setting missing: {expected}")

    if "            - arc-systems" not in prometheus:
        raise AssertionError("Prometheus does not discover ARC controller namespace")
    if "            - arc-runners-stg" in prometheus:
        raise AssertionError(
            "Prometheus ARC metrics discovery should not depend on runner namespace"
        )

    print("GitHub Actions ARC observability contract ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
