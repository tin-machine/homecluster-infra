#!/usr/bin/env python3
"""No-network transfer, recovery, receipt and delivery safety regressions."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("security_transfer", ROOT / "scripts/security_lab_transfer.py")
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)

CLUSTER = "00000000-0000-0000-0000-000000000001"
NAMESPACE = "00000000-0000-0000-0000-000000000002"
PROD = "00000000-0000-0000-0000-000000000003"
CID = "20261031T000000Z-0123456789abcdef"
AID = "c" * 64


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.actions = []
        self.packet = b"synthetic transfer fixture: no real packet contents" * 2
        self.manifest = {"schema": 1, "capture_id": CID, "pcap_sha256": hashlib.sha256(self.packet).hexdigest(),
                         "size_bytes": len(self.packet), "packet_count": 1, "truncated_packet_count": 0,
                         "snaplen": 65535, "linktype": 1}
        self.nas_exists = False
        self.wrong_cluster = False
        self.bad_packet = False
        self.bad_receipt = False
        self.replaced = False
        self.ack_transport_loss_once = False
        self.acked = False
        self.query_error = False
        self.delivery = None
        self.source = None
        self.source_transport_loss_once = False
        self.queries = []
        self.on_ack = None
        self.counts = {"a" * 64: 1, "b" * 64: 1}
        self.rows = [{"analysis_id": AID, "capture_id": CID, "event_id": letter * 64} for letter in ("a", "b")]
        self.pod = {"metadata": {"name": "fixture-coordinator", "uid": "pod-original"},
                    "spec": {"nodeName": "fixture-worker", "securityContext": {"runAsUser": 10001},
                             "serviceAccountName": "security-lab-launcher",
                             "containers": [{"name": "coordinator", "image": transfer.HELPER,
                                             "command": ["python3", "/config/runtime.py", "coordinate"]}]},
                    "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}}

    def durable(self):
        return {"status": "durable", "capture_id": CID, "sha256": self.manifest["pcap_sha256"],
                "bytes": self.manifest["size_bytes"], "analysis_id": AID}

    def run(self, args, data=None, **kwargs):
        self.calls.append((args, data))
        command = args[-1]
        if command == "manifest " + CID:
            return json.dumps(self.manifest).encode()
        if command == "pcap " + CID:
            return self.packet + b"x" if self.bad_packet else self.packet
        if args[-2] == "reader@prod.example.test":
            self.queries.append(json.loads(data))
            if self.query_error:
                raise transfer.TransferError("fixture_query_error")
            return json.dumps({"status": "ok", "event_counts": self.counts}).encode()
        parts = shlex.split(command)
        if parts[0] == "/usr/libexec/homecluster-security-capture.py":
            self.actions.append(("ack-router", None))
            if self.on_ack:
                self.on_ack()
            already = self.acked
            self.acked = True
            if self.ack_transport_loss_once:
                self.ack_transport_loss_once = False
                raise transfer.TransferError("fixture_ack_transport_unknown")
            return json.dumps({"status": "acknowledged", "capture_id": CID,
                               "pcap_sha256": self.manifest["pcap_sha256"], "already_acknowledged": already}).encode()
        if "get" in parts:
            index = parts.index("get")
            kind, value = parts[index + 1:index + 3]
            if kind == "namespace":
                uid = (PROD if self.wrong_cluster else CLUSTER) if value == "kube-system" else NAMESPACE
                result = {"metadata": {"uid": uid, "labels": {"pod-security.kubernetes.io/enforce": "restricted"}}}
            elif kind == "node":
                result = {"metadata": {"name": "fixture-worker", "labels": {"security-lab.home.arpa/analysis": "true"}},
                          "spec": {}, "status": {"nodeInfo": {"architecture": "arm64"}, "conditions": [
                              {"type": k, "status": v} for k, v in (("Ready", "True"), ("MemoryPressure", "False"),
                                                                   ("DiskPressure", "False"), ("PIDPressure", "False"))]}}
            elif kind == "pods":
                result = {"items": [self.pod]}
            elif kind == "pod":
                result = copy.deepcopy(self.pod)
                if self.replaced:
                    result["metadata"]["uid"] = "pod-replacement"
            else:
                raise AssertionError(parts)
            return json.dumps(result).encode()
        if "exec" in parts:
            index = parts.index("--")
            action = parts[index + 3]
            value = parts[index + 4] if len(parts) > index + 4 else None
            self.actions.append((action, value))
            if action == "intake":
                assert data == self.packet
                self.nas_exists = True
                result = self.durable()
                if self.bad_receipt:
                    result["sha256"] = "f" * 64
            elif action == "verify-input":
                if not self.nas_exists:
                    raise transfer.TransferError("fixture_nas_missing")
                result = self.durable()
            elif action == "retention-source":
                evidence = json.loads(value)
                assert evidence == {"analysis_id": AID, "capture_id": CID, "pcap_sha256": self.manifest["pcap_sha256"],
                                    "kind": "router", "confirmation": "acknowledged"}
                assert self.acked
                if self.source_transport_loss_once:
                    self.source_transport_loss_once = False
                    raise transfer.TransferError("fixture_source_transport_unknown")
                self.source = evidence
                result = {"status": "source_confirmed", "analysis_id": AID}
            elif action == "outbox-status":
                result = {"status": "ready", "analysis_id": AID, "capture_id": CID,
                          "pcap_sha256": self.manifest["pcap_sha256"], "rows": self.rows, "result_status": "succeeded",
                          "delivery": self.delivery, "delivered": self.delivery is not None}
            elif action == "replay-missing":
                replay = json.loads(value)
                for event_id in replay["event_ids"]:
                    self.counts[event_id] = 1
                result = {"status": "replay_queued", "events": len(replay["event_ids"]), "analysis_id": AID}
            elif action == "delivery-ack":
                evidence = json.loads(value)
                assert evidence["analysis_id"] == AID and evidence["loki_cluster_uid"] == PROD
                assert set(evidence["event_ids"]) == {x["event_id"] for x in self.rows}
                self.delivery = {"schema": 2, "at": time.time(), "events": [x["event_id"] for x in self.rows],
                                 "loki_cluster_uid": PROD}
                result = {"status": "delivered", "analysis_id": AID, "events": len(self.rows)}
            else:
                raise AssertionError(parts)
            return json.dumps(result).encode()
        raise AssertionError(parts)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = self.root / "reader"
        self.key.write_text("synthetic identity fixture")
        self.key.chmod(0o600)
        self.receipt = self.root / "receipt.json"
        self.runner = FakeRunner()
        self.client = transfer.Client("operator@staging.example.test", CLUSTER, NAMESPACE, "fixture-worker",
                                      runner=self.runner)

    def tearDown(self):
        self.temp.cleanup()

    def send(self, ack=False):
        return self.client.transfer(CID, "root@router.example.test", self.key, self.receipt, acknowledge=ack)

    def test_durable_receipt_is_fsynced_before_router_ack(self):
        def inspect():
            saved = json.loads(self.receipt.read_text())
            self.assertEqual(saved["status"], "input_durable")
            self.assertEqual(saved["router_ack"], "started")
            self.assertEqual(saved["analysis_id"], AID)
        self.runner.on_ack = inspect
        result = self.send(ack=True)
        self.assertEqual(result["router_ack"], "acknowledged")
        actions = [x[0] for x in self.runner.actions]
        self.assertLess(actions.index("intake"), actions.index("ack-router"))
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual({x.name for x in self.root.iterdir()}, {"reader", "receipt.json"})

    def test_wrong_cluster_never_reads_private_router_packet_or_writes_nas(self):
        self.runner.wrong_cluster = True
        with self.assertRaisesRegex(transfer.TransferError, "cluster_uid_mismatch"):
            self.send(ack=True)
        self.assertFalse(self.runner.actions)
        self.assertFalse(any(args[-1].startswith("pcap ") for args, _ in self.runner.calls))
        self.assertFalse(self.receipt.exists())

    def test_bad_router_hash_never_intakes_or_acknowledges(self):
        self.runner.bad_packet = True
        with self.assertRaisesRegex(transfer.TransferError, "router_transfer_hash_mismatch"):
            self.send(ack=True)
        self.assertFalse(self.runner.actions)
        self.assertEqual(json.loads(self.receipt.read_text())["status"], "transfer_started")

    def test_untrusted_nas_receipt_never_acknowledges_router(self):
        self.runner.bad_receipt = True
        with self.assertRaisesRegex(transfer.TransferError, "nas_receipt_mismatch"):
            self.send(ack=True)
        self.assertFalse(self.runner.acked)

    def test_coordinator_replacement_never_receives_packet(self):
        self.runner.replaced = True
        with self.assertRaisesRegex(transfer.TransferError, "coordinator_replaced"):
            self.send(ack=True)
        self.assertFalse(self.runner.nas_exists)
        self.assertFalse(self.runner.acked)

    def test_reader_key_permissions_and_symlink_rejected(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(transfer.TransferError, "private_file_contract"):
            self.send()
        self.key.chmod(0o600)
        link = self.root / "link"
        link.symlink_to(self.key)
        with self.assertRaisesRegex(transfer.TransferError, "private_file_contract"):
            self.client.transfer(CID, "root@router.example.test", link, self.receipt)
        self.assertFalse(self.runner.calls)

    def test_ack_transport_loss_recovers_from_verified_nas_without_new_intake(self):
        self.runner.ack_transport_loss_once = True
        with self.assertRaisesRegex(transfer.TransferError, "fixture_ack_transport_unknown"):
            self.send(ack=True)
        result = self.send(ack=True)
        self.assertEqual(result["router_ack"], "acknowledged")
        self.assertEqual(sum(action == "intake" for action, _ in self.runner.actions), 1)
        self.assertEqual(sum(args[-1] == "pcap " + CID for args, _ in self.runner.calls), 1)

    def test_source_confirmation_never_precedes_actual_router_ack(self):
        self.send(ack=True)
        actions = [x[0] for x in self.runner.actions]
        self.assertLess(actions.index("ack-router"), actions.index("retention-source"))
        self.assertEqual(json.loads(self.receipt.read_text())["retention_source"], "confirmed")

    def test_source_confirmation_transport_loss_preserves_router_ack_and_recovers(self):
        self.runner.source_transport_loss_once = True
        with self.assertRaisesRegex(transfer.TransferError, "fixture_source_transport_unknown"):
            self.send(ack=True)
        self.assertEqual(json.loads(self.receipt.read_text())["router_ack"], "acknowledged")
        self.assertNotIn("retention_source", json.loads(self.receipt.read_text()))
        self.send(ack=True)
        self.assertEqual(sum(x[0] == "ack-router" for x in self.runner.actions), 1)
        self.assertEqual(sum(x[0] == "intake" for x in self.runner.actions), 1)
        self.assertEqual(json.loads(self.receipt.read_text())["retention_source"], "confirmed")

    def test_pending_source_cannot_enable_retirement(self):
        self.send()
        record = json.loads(self.receipt.read_text())
        with self.assertRaisesRegex(transfer.TransferError, "source_not_confirmed"):
            self.client.register_source(self.client.context(), record, self.receipt)
        self.assertIsNone(self.runner.source)

    def test_all_event_ids_must_reach_loki_before_delivery_ack(self):
        self.send()
        result = self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD, wait_seconds=0)
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(json.loads(self.receipt.read_text())["delivery"]["event_ids"], ["a" * 64, "b" * 64])
        self.assertEqual(sum(action == "delivery-ack" for action, _ in self.runner.actions), 1)

    def test_query_error_does_not_acknowledge_delivery(self):
        self.send()
        self.runner.query_error = True
        with self.assertRaisesRegex(transfer.TransferError, "fixture_query_error"):
            self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD, wait_seconds=0)
        self.assertNotIn("delivery", json.loads(self.receipt.read_text()))
        self.assertFalse(any(action == "delivery-ack" for action, _ in self.runner.actions))

    def test_old_undelivered_capture_can_replay_with_a_recent_query_window(self):
        self.send()
        saved = json.loads(self.receipt.read_text())
        saved["started_at"] = time.time() - 96 * 3600
        transfer.save_receipt(self.receipt, saved)
        self.runner.counts = {"a" * 64: 1}
        with patch.object(transfer.time, "sleep", return_value=None):
            result = self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD,
                                                wait_seconds=0, replay_missing=True)
        self.assertEqual(result["status"], "delivered")
        self.assertTrue(result["replayed"])
        self.assertTrue(all(q["start_ns"] > (time.time() - 72 * 3600) * 10 ** 9 for q in self.runner.queries))
        self.assertEqual(sum(action == "intake" for action, _ in self.runner.actions), 1)

    def test_historical_receipt_is_not_a_claim_of_current_loki_availability(self):
        self.send()
        self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD, wait_seconds=0)
        proof_at = self.runner.delivery["at"] = time.time() - 96 * 3600
        self.runner.query_error = True
        count = len(self.runner.queries)
        result = self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD, wait_seconds=0)
        self.assertEqual(result["confirmation"], "historical_receipt")
        self.assertEqual(result["loki_availability"], "not_checked")
        self.assertIsNone(result["duplicate_records"])
        self.assertEqual(len(self.runner.queries), count)
        self.assertEqual(json.loads(self.receipt.read_text())["delivery"]["verified_at"], proof_at)
        self.runner.delivery["loki_cluster_uid"] = CLUSTER
        with self.assertRaisesRegex(transfer.TransferError, "delivery_target_changed"):
            self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD, wait_seconds=0)

    def test_malformed_historical_event_receipt_never_becomes_delivered(self):
        self.send()
        self.runner.delivery = {"at": time.time(), "events": ["a" * 64], "loki_cluster_uid": PROD}
        with self.assertRaisesRegex(transfer.TransferError, "historical_delivery_invalid"):
            self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD, wait_seconds=0)
        self.assertFalse(any(action == "delivery-ack" for action, _ in self.runner.actions))

    def test_expired_legacy_marker_needs_matching_local_destination_proof(self):
        self.send()
        at = time.time() - 96 * 3600
        self.runner.delivery = {"at": at, "events": ["a" * 64, "b" * 64]}
        self.runner.query_error = True
        result = self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD,
                                            wait_seconds=0, replay_missing=True)
        self.assertEqual(result["reason"], "legacy_delivery_target_receipt_required")
        self.assertFalse(self.runner.queries)
        self.assertFalse(any(action == "replay-missing" for action, _ in self.runner.actions))
        record = json.loads(self.receipt.read_text())
        record["delivery"] = {"status": "delivered", "event_ids": ["a" * 64, "b" * 64],
                              "verified_at": at, "loki_cluster_uid": PROD}
        transfer.save_receipt(self.receipt, record)
        recovered = self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD, wait_seconds=0)
        self.assertEqual(recovered["confirmation"], "historical_receipt")
        self.assertEqual(json.loads(self.receipt.read_text())["delivery"]["verified_at"], at)
        self.assertFalse(self.runner.queries)

    def test_only_missing_events_are_queued_for_main_process_replay(self):
        self.send()
        self.runner.counts = {"a" * 64: 1}
        with patch.object(transfer.time, "sleep", return_value=None):
            result = self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD,
                                                wait_seconds=0, replay_missing=True)
        self.assertEqual(result["status"], "delivered")
        replay = next(json.loads(value) for action, value in self.runner.actions if action == "replay-missing")
        self.assertEqual(replay["event_ids"], ["b" * 64])
        self.assertEqual(sum(action == "intake" for action, _ in self.runner.actions), 1)

    def test_missing_events_stay_pending_without_implicit_replay_or_ack(self):
        self.send()
        self.runner.counts = {}
        result = self.client.check_delivery(self.receipt, "reader@prod.example.test", PROD, wait_seconds=0)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["missing_events"], 2)
        self.assertFalse(any(action in ("delivery-ack", "replay-missing") for action, _ in self.runner.actions))

    def test_command_injection_and_unsafe_receipt_parent_rejected(self):
        for value in ("operator@host;id", "-oProxyCommand=id", "operator@host\nid", "operator@$(id)"):
            with self.assertRaises(transfer.TransferError):
                transfer.Client(value, CLUSTER, NAMESPACE, "fixture-worker")
        self.root.chmod(0o755)
        with self.assertRaisesRegex(transfer.TransferError, "receipt_parent_contract"):
            self.send()


class TransportTests(unittest.TestCase):
    def test_bidirectional_binary_stdin_is_bounded_and_complete(self):
        data = b"synthetic input\n" * 100000
        result = transfer.Runner().run([sys.executable, "-c",
                                      "import sys;d=sys.stdin.buffer.read();sys.stdout.buffer.write(d[::-1])"],
                                     data=data, limit=len(data), timeout=5)
        self.assertEqual(result, data[::-1])

    def test_excess_stdout_and_silent_deadline_kill_only_the_owned_child(self):
        with self.assertRaisesRegex(transfer.TransferError, "transport_output_limit"):
            transfer.Runner().run([sys.executable, "-c", "print('x'*100000)"], limit=1024, timeout=5)
        with self.assertRaisesRegex(transfer.TransferError, "transport_timeout"):
            transfer.Runner().run([sys.executable, "-c", "import time;time.sleep(30)"], timeout=.1)


if __name__ == "__main__":
    unittest.main()
