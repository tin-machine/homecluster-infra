#!/usr/bin/env python3
"""Render staging Helm upgrades and validate configs without cluster access.

Requires PyYAML, Helm, Terraform, downloaded chart archives and release binaries.
All values are public fixtures; no site values, state, or kubeconfig are read.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
APPS = ROOT / "clusters/homelab/apps"


def run(args, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, timeout=90, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-3000:]}")
    return result.stdout


def release_version(path, release):
    source = path.read_text()
    block = source.split(f'resource "helm_release" "{release}" {{', 1)[1]
    return re.search(r'version\s*=\s*"([^"]+)"', block)[1]


def metallb_values(directory):
    source = (ROOT / "terraform/env/common-crds/main.tf").read_text()
    expression = re.search(r"values = \[yamlencode\((.*?)\)\]", source, re.S)[1]
    fixtures = {
        "addon_node_selector": {"fixture": "node"},
        "metallb_tolerate_control_plane": True,
        "metallb_address_pool": ["198.51.100.100-198.51.100.110"],
    }
    for name, value in fixtures.items():
        expression = expression.replace("var." + name, json.dumps(value))
    terraform = directory / "terraform"
    terraform.mkdir()
    (terraform / "main.tf").write_text("locals {\nfixture_values = " + expression + "\n}\n")
    return json.loads(json.loads(run(
        ["terraform", "console"], cwd=terraform,
        input="jsonencode(local.fixture_values)\n",
    )))


def render(chart, version, release, namespace, values, args, directory):
    archive = args.charts_dir / f"{chart}-{version}.tgz"
    if not archive.is_file():
        raise RuntimeError(f"missing chart archive: {archive.name}")
    command = ["helm", "template", release, str(archive), "--namespace", namespace,
               "--kube-version", "1.34.0", "--include-crds"]
    for index, value in enumerate(values):
        path = directory / f"values-{index}.yaml"
        path.write_text(yaml.safe_dump(value))
        command += ["-f", str(path)]
    return [obj for obj in yaml.safe_load_all(run(command)) if obj]


def values_for(name):
    return [yaml.safe_load((APPS / env / f"values-{name}.yaml").read_text())
            for env in ("base", "staging")]


def config_from(objects, key):
    matches = [obj for obj in objects if key in obj.get("data", {})]
    assert len(matches) == 1, f"expected exactly one config: {key}"
    obj = matches[0]
    text = obj["data"][key]
    if obj["kind"] == "Secret":
        text = base64.b64decode(text).decode()
    return yaml.safe_load(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--charts-dir", type=Path, required=True)
    parser.add_argument("--collector-binary", type=Path, required=True)
    parser.add_argument("--loki-binary", type=Path, required=True)
    args = parser.parse_args()
    for key in ("charts_dir", "collector_binary", "loki_binary"):
        setattr(args, key, getattr(args, key).resolve())
    module = ROOT / "terraform/modules/observability_stack/main.tf"
    with tempfile.TemporaryDirectory(prefix="staging-helm-compat-") as temporary:
        directory = Path(temporary)
        values = metallb_values(directory)
        version = release_version(ROOT / "terraform/env/common-crds/main.tf", "metallb")
        objects = render("metallb", version, "metallb", "metallb-system",
                         [values], args, directory)
        speaker = next(o for o in objects if o["kind"] == "DaemonSet"
                       and o["metadata"]["name"] == "metallb-speaker")
        pod = speaker["spec"]["template"]["spec"]
        assert "frr" in {c["name"] for c in pod["containers"]}
        assert pod["nodeSelector"]["fixture"] == "node"
        assert pod["tolerations"], "control-plane toleration lost"
        assert not any("frr-k8s" in o["metadata"]["name"] for o in objects)
        negative = copy.deepcopy(values)
        negative.pop("frrk8s")
        try:
            render("metallb", version, "metallb", "metallb-system",
                   [negative], args, directory)
        except RuntimeError as error:
            assert "mutually exclusive" in str(error)
        else:
            raise AssertionError("missing FRR compatibility setting did not fail")
        print("PASS MetalLB: production Terraform values, FRR preserved, conflict rejected")

        values = values_for("loki")
        # Mirror all sensitive bindings using synthetic credentials only.
        bindings = re.findall(r'name\s*=\s*"(loki\.[^"]+)"\s*\n\s*value\s*=\s*local\.(loki_objectstore_\w+)', module.read_text())
        override = {}
        credentials = {"loki_objectstore_access_key": "fixture-access",
                       "loki_objectstore_secret_key": "fixture-secret"}
        for name, variable in bindings:
            target = override
            parts = name.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = credentials[variable]
        version = release_version(module, "loki")
        objects = render("loki", version, "stg-loki", "observability-stg",
                         [*values, override], args, directory)
        config = config_from(objects, "config.yaml")
        for storage in [config["common"]["storage"]["s3"], config["storage_config"]["aws"]]:
            assert storage["bucketnames"] == "stg-loki"
            assert storage["endpoint"] == "http://stg-minio.object-storage-stg.svc.cluster.local:9000"
            assert storage["s3forcepathstyle"] and storage["insecure"]
            assert storage["access_key_id"] == "fixture-access"
            assert storage["secret_access_key"] == "fixture-secret"
        assert config["ruler"]["storage"]["s3"]["bucketnames"] == "stg-loki-ruler"
        schema = config["schema_config"]["configs"]
        assert len(schema) == 1 and schema[0]["schema"] == "v12"
        assert schema[0]["store"] == "boltdb-shipper"
        assert schema[0]["index"]["prefix"] == "stg_index_"
        assert config["limits_config"]["retention_period"] == "168h"
        claims = {o["metadata"]["name"]: o["spec"]["volumeClaimTemplates"]
                  for o in objects if o["kind"] == "StatefulSet"}
        assert set(claims) == {"loki-backend", "loki-write"}
        for name, size in (("loki-backend", "20Gi"), ("loki-write", "10Gi")):
            assert len(claims[name]) == 1
            claim = claims[name][0]
            assert claim["metadata"]["name"] == "data"
            assert claim["spec"]["storageClassName"] == "nfs-client"
            assert claim["spec"]["resources"]["requests"]["storage"] == size
        path = directory / "loki.yaml"
        path.write_text(yaml.safe_dump(config))
        assert "2.9.6" in run([str(args.loki_binary), "--version"])
        run([str(args.loki_binary), f"-config.file={path}", "-verify-config=true"])
        negative = copy.deepcopy(values)
        for value in negative:
            value["loki"].pop("storage")
        try:
            render("loki", version, "stg-loki", "observability-stg",
                   negative, args, directory)
        except RuntimeError as error:
            assert "bucketNames" in str(error)
        else:
            raise AssertionError("missing bucket names did not fail")
        print("PASS Loki: existing S3 buckets/schema/credentials retained, binary config valid")

        assert "0.102.1" in run([str(args.collector_binary), "--version"])
        for o4 in (False, True):
            values = values_for("otel-collector")
            if o4:
                values.append(yaml.safe_load((APPS / "staging/values-otel-collector-o4-prod-logs.yaml").read_text()))
            objects = render("opentelemetry-collector", release_version(module, "otel_collector"),
                             "stg-otel-collector", "observability-stg", values, args, directory)
            daemon = next(o for o in objects if o["kind"] == "DaemonSet")
            image = daemon["spec"]["template"]["spec"]["containers"][0]["image"]
            assert image.endswith("/opentelemetry-collector-contrib:0.102.1")
            config = config_from(objects, "relay")
            assert "filelog/pods" in config["receivers"]
            assert "otlp/tempo" in config["exporters"]
            assert not config["service"]["telemetry"]["metrics"].get("readers")
            pipelines = config["service"]["pipelines"]
            assert pipelines["traces"]["exporters"] == ["otlp/tempo"]
            assert pipelines["metrics"]["exporters"] == ["prometheus"]
            for name in ("logs", "logs/openwrt"):
                assert pipelines[name]["exporters"] == (["otlphttp/prod-logs"] if o4 else ["loki"])
            if o4:
                assert pipelines["logs/journal"]["receivers"] == ["otlp"]
            env = {**os.environ, "MY_POD_IP": "127.0.0.1",
                   "PROD_LOKI_OTLP_BASE": "https://logs.example.invalid/otlp",
                   "PROD_LOKI_BASIC_B64": "Zml4dHVyZTpmaXh0dXJl"}
            path = directory / "otel.yaml"
            path.write_text(yaml.safe_dump(config))
            run([str(args.collector_binary), "validate", "--config", str(path)], env=env)
            # Prove that validation catches the actual chart/binary mismatch.
            config["receivers"]["file_log/pods"] = config["receivers"].pop("filelog/pods")
            path.write_text(yaml.safe_dump(config))
            try:
                run([str(args.collector_binary), "validate", "--config", str(path)], env=env)
            except RuntimeError as error:
                assert "file_log" in str(error)
            else:
                raise AssertionError("unsupported receiver name passed binary validation")
            print(f"PASS Collector: image config and negative mismatch fixture, O4={o4}")


if __name__ == "__main__":
    main()
