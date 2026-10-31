#!/usr/bin/env python3
"""Validate rendered configs with pinned official binaries, without live targets."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from prod_nas_monitoring import snmp_config


def download(url: str) -> bytes:
    return urllib.request.urlopen(url, timeout=90).read()


def verified(base: str, sums: str, name: str) -> bytes:
    expected = next(line.split()[0] for line in download(base + sums).decode().splitlines()
                    if line.split()[-1].lstrip("*") == name)
    data = download(base + name)
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("official release checksum mismatch")
    return data


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="nas-native-config-") as tmp:
        directory = Path(tmp)
        base = "https://github.com/prometheus/snmp_exporter/releases/download/v0.31.0/"
        archive = directory / "snmp.tgz"
        archive.write_bytes(verified(base, "sha256sums.txt", "snmp_exporter-0.31.0.linux-amd64.tar.gz"))
        with tarfile.open(archive) as packed:
            data = packed.extractfile("snmp_exporter-0.31.0.linux-amd64/snmp_exporter").read()
        snmp = directory / "snmp_exporter"; snmp.write_bytes(data); snmp.chmod(0o700)
        archive = directory / "alloy.zip"
        archive.write_bytes(verified("https://github.com/grafana/alloy/releases/download/v1.21.3/",
                                    "SHA256SUMS", "alloy-linux-amd64.zip"))
        with zipfile.ZipFile(archive) as packed:
            data = packed.read("alloy-linux-amd64")
        alloy = directory / "alloy"; alloy.write_bytes(data); alloy.chmod(0o700)
        config = directory / "snmp.json"; config.write_text(json.dumps(snmp_config("monitor")))
        probe = socket.socket(); probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]; probe.close()
        with (directory / "snmp.log").open("w+") as output:
            process = subprocess.Popen([str(snmp), "--config.file=" + str(config),
                                        f"--web.listen-address=127.0.0.1:{port}"],
                                       stdout=output, stderr=subprocess.STDOUT)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        output.seek(0)
                        raise RuntimeError("SNMP config rejected: " + output.read()[-2000:])
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
                        break
                    except Exception:
                        time.sleep(.1)
                else:
                    raise RuntimeError("SNMP exporter did not become ready")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
        env = {**os.environ, "ALLOY_BIN": str(alloy), "PYTHONDONTWRITEBYTECODE": "1"}
        subprocess.run([sys.executable, str(ROOT / "scripts/test_prod_nas_syslog.py"),
                        "AlloyContractTests"], env=env, check=True, timeout=45)
        for image in ("prom/snmp-exporter:v0.31.0", "grafana/alloy:v1.21.3"):
            result = subprocess.run(["docker", "manifest", "inspect", image],
                                    text=True, capture_output=True, check=True, timeout=60)
            manifest = json.loads(result.stdout)
            if not any(x.get("platform", {}).get("architecture") == "arm64"
                       for x in manifest.get("manifests", [])):
                raise RuntimeError("official image lacks ARM64")
        print("Pinned config parsers, UDP severity/delivery/rejection fixture and ARM64 manifests: PASS")


if __name__ == "__main__":
    main()
