"""Source contracts and an optional real-Alloy UDP -> Loki push fixture."""
import http.server
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request

import prod_nas_syslog as logs
from prod_grafana_terraform_input import terraform_manifest
from test_prod_outside_in_render import fixture
from test_prod_nas_monitoring import index


def site():
    value = fixture()
    value["metrics_targets"] = [{"name": f"example-{i}", "target": f"192.0.2.{100 + i}:9100"}
                               for i in range(1, 5)]
    value["prod_metrics_target"] = {"name": "prod-example", "target": "192.0.2.50:9100"}
    return value


class SyslogTests(unittest.TestCase):
    def render(self):
        return index(terraform_manifest(prometheus_site=site(), nas_address="192.0.2.10",
                                        include_nas_syslog=True))

    def test_opt_in_adds_only_six_stateless_resources(self):
        old = index(terraform_manifest(prometheus_site=site(), nas_address="192.0.2.10"))
        new = self.render()
        self.assertEqual(set(new) - set(old), {
            "ConfigMap/prod-nas-syslog-config", "Deployment/prod-nas-syslog",
            "Service/prod-nas-syslog", "Service/prod-nas-syslog-metrics",
            "NetworkPolicy/prod-nas-syslog-restricted", "NetworkPolicy/prod-nas-loki-ingress"})
        self.assertFalse(any(k.startswith(("Secret/", "PersistentVolume")) for k in set(new) - set(old)))

    def test_nodeport_preserves_source_and_metrics_are_internal(self):
        data = self.render()
        service = data["Service/" + logs.NAME]["spec"]
        self.assertEqual(service["externalTrafficPolicy"], "Local")
        self.assertEqual(service["ports"], [{"name": "syslog", "port": 1514, "targetPort": "syslog",
                                             "nodePort": 31514, "protocol": "UDP"}])
        self.assertEqual(data["Service/" + logs.METRICS]["spec"]["type"], "ClusterIP")
        pod = data["Deployment/" + logs.NAME]["spec"]["template"]["spec"]
        self.assertEqual(pod["nodeSelector"]["kubernetes.io/hostname"], "prod-example")
        self.assertFalse(pod["automountServiceAccountToken"])

    def test_policy_allows_only_nas_syslog_and_internal_loki(self):
        data = self.render()
        policy = data["NetworkPolicy/prod-nas-syslog-restricted"]["spec"]
        self.assertEqual(policy["ingress"][0], {
            "from": [{"ipBlock": {"cidr": "192.0.2.10/32"}}],
            "ports": [{"port": 1514, "protocol": "UDP"}]})
        self.assertEqual(policy["egress"][0]["ports"], [{"port": 3100, "protocol": "TCP"}])
        self.assertTrue(all("ipBlock" not in peer for rule in policy["egress"] for peer in rule["to"]))
        self.assertEqual(len(data["NetworkPolicy/prod-nas-prometheus-egress"]["spec"]["egress"]), 2)
        loki = data["NetworkPolicy/prod-nas-loki-ingress"]["spec"]
        self.assertEqual(loki["policyTypes"], ["Ingress"])
        self.assertEqual(loki["ingress"][0]["from"][0]["podSelector"]["matchLabels"],
                         logs.labels(logs.NAME))

    def test_provisioning_and_collector_scrape(self):
        data = self.render()
        prom = json.loads(data["ConfigMap/prod-prometheus-config"]["data"]["prometheus.json"])
        job = next(x for x in prom["scrape_configs"] if x["job_name"] == logs.COLLECTOR_JOB)
        self.assertEqual(job["static_configs"][0]["targets"], [logs.METRICS + ":12345"])
        dash = json.loads(data["ConfigMap/prod-grafana-provisioning"]["data"]["qnap-nas.json"])
        panel = next(x for x in dash["panels"] if x["type"] == "logs")
        self.assertEqual(panel["datasource"]["uid"], "prod-loki")
        self.assertEqual(panel["targets"][0]["expr"], '{environment="production",source="qnap-syslog"}')

    def test_missing_nas_or_explicit_prod_node_rejected(self):
        for kwargs in [{"prometheus_site": site()},
                       {"prometheus_site": fixture(), "nas_address": "192.0.2.10"}]:
            with self.assertRaisesRegex(ValueError, "explicit production node"):
                terraform_manifest(include_nas_syslog=True, **kwargs)
        with self.assertRaises(ValueError):
            logs.resources("192.0.2.10", "bad/node")


def unsnappy(data):
    """Decode the raw Snappy block used by Loki's protobuf push, without packages."""
    pos = 0
    expected = shift = 0
    while True:
        byte = data[pos]; pos += 1
        expected |= (byte & 127) << shift
        if byte < 128:
            break
        shift += 7
    out = bytearray()
    while pos < len(data):
        tag = data[pos]; pos += 1
        kind = tag & 3
        if kind == 0:
            length = tag >> 2
            if length >= 60:
                count = length - 59
                length = int.from_bytes(data[pos:pos + count], "little"); pos += count
            length += 1
            out.extend(data[pos:pos + length]); pos += length
        else:
            if kind == 1:
                length = 4 + ((tag >> 2) & 7)
                offset = ((tag & 224) << 3) | data[pos]; pos += 1
            else:
                count = 2 if kind == 2 else 4
                length = 1 + (tag >> 2)
                offset = int.from_bytes(data[pos:pos + count], "little"); pos += count
            for _ in range(length):
                out.append(out[-offset])
    if len(out) != expected:
        raise ValueError("invalid Snappy fixture payload")
    return bytes(out)


def protobuf_bytes(data):
    """Read length-delimited fields from the real Loki PushRequest fixture."""
    pos = 0
    def varint():
        nonlocal pos
        value = shift = 0
        while True:
            byte = data[pos]; pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
            shift += 7
    while pos < len(data):
        tag = varint()
        if tag & 7 == 2:
            length = varint()
            yield tag >> 3, data[pos:pos + length]
            pos += length
        elif tag & 7 == 0:
            varint()
        else:
            raise ValueError("unexpected fixture protobuf wire type")


@unittest.skipUnless(os.environ.get("ALLOY_BIN"), "set ALLOY_BIN for the native UDP/push contract")
class AlloyContractTests(unittest.TestCase):
    def test_real_parser_severity_delivery_and_malformed_input(self):
        received = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, unsnappy(body)))
                self.send_response(204); self.end_headers()
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0)); udp = probe.getsockname()[1]; probe.close()
        http_probe = socket.socket(); http_probe.bind(("127.0.0.1", 0))
        port = http_probe.getsockname()[1]; http_probe.close()
        try:
            with tempfile.TemporaryDirectory(prefix="nas-alloy-fixture-") as directory:
                config = logs.alloy_config().replace("0.0.0.0:1514", f"127.0.0.1:{udp}").replace(
                    "http://prod-loki:3100/loki/api/v1/push",
                    f"http://127.0.0.1:{server.server_port}/loki/api/v1/push")
                path = Path(directory) / "config.alloy"; path.write_text(config)
                with (Path(directory) / "alloy.log").open("w+") as output:
                    process = subprocess.Popen([
                        os.environ["ALLOY_BIN"], "run", "--disable-reporting", "--stability.level=experimental",
                        f"--server.http.listen-addr=127.0.0.1:{port}",
                        f"--storage.path={directory}/data", str(path)],
                        stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                    try:
                        for _ in range(100):
                            if process.poll() is not None:
                                output.seek(0)
                                self.fail("Alloy exited: " + output.read()[-4000:])
                            try:
                                urllib.request.urlopen(f"http://127.0.0.1:{port}/-/ready", timeout=1).close()
                                break
                            except Exception:
                                time.sleep(.1)
                        else:
                            self.fail("Alloy not ready")
                        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        try:
                            fixtures = [(12, "Oct 31", "fixture-warning"),
                                        (14, "Nov  1", "fixture-info"),
                                        (188, "Nov 01", "fixture-qnap-warning"),
                                        (190, "Nov 01", "fixture-qnap-info")]
                            fixtures += [(184 + i, "Nov 01", "fixture-level-" + level) for i, level in enumerate(
                                ["emerg", "alert", "critical", "error", "warn", "notice", "info", "debug"])]
                            for pri, stamp, marker in fixtures:
                                sender.sendto(f"<{pri}>{stamp} 12:00:00 fixture-nas qlogd[123]: {marker}".encode(),
                                              ("127.0.0.1", udp))
                                time.sleep(.01)
                            sender.sendto(b"fixture-malformed", ("127.0.0.1", udp))
                            sender.sendto(b"<bad>Nov 01 12:00:00 fixture-nas fixture-bad-priority", ("127.0.0.1", udp))
                            sender.sendto(b"<14>Bad 01 12:00:00 fixture-nas fixture-bad-stamp", ("127.0.0.1", udp))
                        finally:
                            sender.close()
                        for _ in range(100):
                            payload = b"".join(body for _, body in received)
                            if all(marker.encode() in payload for _, _, marker in fixtures):
                                break
                            time.sleep(.1)
                        for _, _, marker in fixtures:
                            self.assertIn(marker.encode(), payload)
                        streams = []
                        for _, body in received:
                            for field, stream in protobuf_bytes(body):
                                if field != 1:
                                    continue
                                fields = list(protobuf_bytes(stream))
                                labels = next(value.decode() for key, value in fields if key == 1)
                                lines = [line.decode() for key, entry in fields if key == 2
                                         for subkey, line in protobuf_bytes(entry) if subkey == 2]
                                streams.append((labels, lines))
                        for level in ["emerg", "alert", "critical", "error", "warn", "notice", "info", "debug"]:
                            # Each stream must pair its normalized label and marker.
                            self.assertTrue(any('level="' + level + '"' in labels and
                                                any('fixture-level-' + level in line for line in lines)
                                                for labels, lines in streams))
                        for marker, level in [("fixture-qnap-warning", "warn"), ("fixture-qnap-info", "info")]:
                            self.assertTrue(any('level="' + level + '"' in labels and
                                                any(marker in line for line in lines) for labels, lines in streams))
                        self.assertIn(b'level="warn"', payload)
                        self.assertIn(b'level="info"', payload)
                        self.assertIn(b'source="qnap-syslog"', payload)
                        self.assertNotIn(b"fixture-malformed", payload)
                        self.assertNotIn(b"fixture-bad-priority", payload)
                        self.assertNotIn(b"fixture-bad-stamp", payload)
                        self.assertNotIn(b'priority="', payload)
                        self.assertNotIn(b'nas_month="', payload)
                        self.assertTrue(all(path == "/loki/api/v1/push" for path, _ in received))
                        metrics = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=3).read().decode()
                        dropped = [float(line.split()[-1]) for line in metrics.splitlines()
                                   if line.startswith("loki_process_dropped_lines_total") and
                                   'reason="nas_invalid_header"' in line]
                        self.assertGreaterEqual(sum(dropped), 1)
                        entries = [float(line.split()[-1]) for line in metrics.splitlines()
                                   if line.startswith("loki_source_syslog_entries_total")]
                        # The UDP transport may reject malformed PRI before
                        # incrementing its counters. Verify delivery separately.
                        self.assertGreaterEqual(sum(entries), len(fixtures))
                    finally:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL); process.wait()
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
