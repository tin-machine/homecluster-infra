#!/usr/bin/env python3
"""Fail-closed runtime acceptance for the staging CI-only ARC runner."""

from __future__ import annotations

import argparse
import ipaddress
import json
import platform
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

EXPECTED_RUNNER_VERSION = "2.337.0"
RUNNER_LISTENER = Path("/home/runner/bin/Runner.Listener")
SERVICE_ACCOUNT_TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
PUBLIC_URL = "https://github.com/"
PRIVATE_HOST = "kubernetes.default.svc"
PRIVATE_PORT = 443


def _normalized_machine(value: str) -> str:
    value = value.strip().lower()
    return {"arm64": "aarch64"}.get(value, value)


def _runner_version(path: Path) -> str:
    result = subprocess.run(
        [str(path), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _public_https(url: str, timeout: float) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "homecluster-arc-acceptance"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status < 200 or response.status >= 400:
            raise RuntimeError(f"unexpected public HTTPS status: {response.status}")


def _resolved_private_addresses(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = sorted({item[4][0] for item in infos})
    if not addresses:
        raise RuntimeError("private probe did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not (ip.is_private or ip.is_loopback or ip.is_link_local):
            raise RuntimeError("private probe resolved outside private/link-local space")
    return addresses


def _private_tcp_is_blocked(host: str, port: int, timeout: float) -> None:
    addresses = _resolved_private_addresses(host, port)
    failures = 0
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((address, port))
        except OSError:
            failures += 1
        else:
            raise RuntimeError("private endpoint is reachable from CI runner")
        finally:
            sock.close()
    if failures != len(addresses):
        raise RuntimeError("private endpoint deny result is incomplete")


def run_acceptance(timeout: float) -> dict[str, object]:
    machine = _normalized_machine(platform.machine())
    if machine != "aarch64":
        raise RuntimeError(f"unexpected runner architecture: {machine}")

    if SERVICE_ACCOUNT_TOKEN.exists():
        raise RuntimeError("service account token is mounted")

    if not RUNNER_LISTENER.is_file():
        raise RuntimeError("runner listener binary is missing")
    version = _runner_version(RUNNER_LISTENER)
    if version != EXPECTED_RUNNER_VERSION:
        raise RuntimeError(f"unexpected runner version: {version}")

    _public_https(PUBLIC_URL, timeout)
    private_addresses = _resolved_private_addresses(PRIVATE_HOST, PRIVATE_PORT)
    _private_tcp_is_blocked(PRIVATE_HOST, PRIVATE_PORT, timeout)

    return {
        "schema": 1,
        "status": "pass",
        "architecture": machine,
        "runner_version": version,
        "service_account_token_mounted": False,
        "public_https": "pass",
        "private_probe": "blocked",
        "private_probe_address_count": len(private_addresses),
    }


def self_test() -> None:
    assert _normalized_machine("arm64") == "aarch64"
    assert _normalized_machine("aarch64") == "aarch64"
    assert _normalized_machine("x86_64") == "x86_64"
    private_examples = (
        ".".join(("10", "43", "0", "1")),
        ".".join(("192", "168", "1", "1")),
        ".".join(("127", "0", "0", "1")),
        ".".join(("169", "254", "1", "1")),
    )
    for value in private_examples:
        ip = ipaddress.ip_address(value)
        assert ip.is_private or ip.is_loopback or ip.is_link_local


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("github actions runner acceptance self-test ok")
        return 0

    try:
        result = run_acceptance(args.timeout)
    except Exception as exc:
        print(json.dumps({"schema": 1, "status": "fail", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
