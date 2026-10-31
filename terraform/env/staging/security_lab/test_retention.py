"""Retention failure/crash/race fixtures; never read actual PCAPs or a cluster."""
import copy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from test_runtime import fixture, runtime
from retention import Store, TTL

CLUSTER = "00000000-0000-0000-0000-000000000001"
PROD = "00000000-0000-0000-0000-000000000002"
JOB = "00000000-0000-0000-0000-000000000003"


class API:
    def __init__(self, jobs=None, pods=None):
        self.jobs, self.pods = jobs or [], pods or []

    def call(self, method, path, body=None):
        assert method == "GET" and body is None  # No stop, delete, or engine POST.
        assert path in (runtime.JOBS, runtime.PODS)
        return {"items": self.jobs if path == runtime.JOBS else self.pods}


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.input = self.root / "input"
        self.outputs = {x: self.root / x for x in ("suricata", "zeek")}
        for p in (self.input, *self.outputs.values()):
            p.mkdir(mode=0o700)
        self.config = {"schema": 1, "node": "fixture-worker", "uid": 10001, "cluster_uid": CLUSTER,
                       "config_map": "fixture-config", "asset_sha256": "0" * 64, "retention_enabled": True,
                       "images": {e: "example.invalid/" + e + "@sha256:" + "a" * 64
                                  for e in ("suricata", "zeek", "helper")}}
        self.patches = [patch.object(runtime, "INPUT", self.input), patch.object(runtime, "OUTPUTS", self.outputs),
                        patch.object(runtime, "CONFIG", self.root), patch.object(runtime, "nfs_mount", return_value="nfs"),
                        patch.object(runtime, "settings", return_value=self.config)]
        for p in self.patches:
            p.start()
        self.store = Store(runtime)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def seed(self, suffix="0123456789abcdef", age=96 * 3600):
        """Create only new, local synthetic artifacts with an explicit fixture clock."""
        with patch("time.time", return_value=time.time() - age):
            cid = "20261031T000000Z-" + suffix
            data = fixture()
            manifest = dict(schema=1, capture_id=cid, pcap_sha256=hashlib.sha256(data).hexdigest(),
                            size_bytes=len(data), packet_count=1, truncated_packet_count=0, snaplen=65535, linktype=1)
            receipt = runtime.intake(manifest, io.BytesIO(data))
            aid = receipt["analysis_id"]
            capture = self.input / "captures" / cid
            # This can happen before the coordinator has created INPUT/control.
            source = dict(analysis_id=aid, capture_id=cid, pcap_sha256=manifest["pcap_sha256"],
                          kind="synthetic", confirmation="not_applicable")
            self.store.register_source(source)
            self.assertFalse((self.input / "control" / aid).exists())
            control = self.input / "control"
            control.mkdir(mode=0o700, exist_ok=True)
            run = control / aid
            run.mkdir(mode=0o700)
            engines = {}
            for engine, root in self.outputs.items():
                target = root / aid
                target.mkdir(mode=0o700)
                runtime.atomic(target / "payload.log", {"fixture": "private synthetic event"})
                path = target / "payload.log"
                meta = dict(status="succeeded", output_files=[dict(path=path.name, bytes=path.stat().st_size,
                                                                 sha256=runtime.digest(path))])
                runtime.atomic(target / "engine.json", meta)
                engines[engine] = meta
            result = dict(schema=1, analysis_id=aid, capture_id=cid, pcap_sha256=manifest["pcap_sha256"],
                          status="succeeded", cluster_uid=CLUSTER, config_sha256=self.config["asset_sha256"],
                          job_uid=JOB, started_at=time.time(), finished_at=time.time(), engines=engines)
            runtime.atomic(run / "result.json", result)
            row = runtime.envelope(aid, cid, "analysis_summary", {"private": "should expire"})
            runtime.atomic(run / "outbox.json", [row])
            runtime.acknowledge_delivery(aid, dict(analysis_id=aid, event_ids=[row["event_id"]], loki_cluster_uid=PROD))
        return aid, capture, run

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_retire_payloads_keep_compact_delivery_and_no_reintake_or_replay(self):
        aid, capture, run = self.seed()
        manifest = runtime.read_json(capture / "manifest.json")
        ids = [x["event_id"] for x in runtime.stored_outbox(aid)]
        out = self.store.sweep(API(), self.config)
        self.assertEqual(out["results"], [dict(status="retired", analysis_id=aid, already_retired=False)])
        self.assertFalse((capture / "capture.pcap").exists())
        self.assertTrue(all(not (root / aid).exists() for root in self.outputs.values()))
        self.assertNotIn("data", runtime.stored_outbox(aid)[0])
        self.assertNotIn(b"should expire", (run / "outbox.json").read_bytes())
        box = runtime.outbox_info(aid)
        self.assertEqual(box["delivery"]["events"], ids)
        self.assertEqual(box["delivery"]["loki_cluster_uid"], PROD)
        self.assertTrue(box["delivered"])
        for action in [lambda: runtime.intake(manifest, io.BytesIO(fixture())), lambda: runtime.verify_input(manifest["capture_id"]),
                       lambda: runtime.request_replay(aid), lambda: runtime.publish(run)]:
            with self.assertRaisesRegex(ValueError, "retired"):
                action()
        self.assertTrue(self.store.sweep(API(), self.config)["results"][0]["already_retired"])

    def test_fresh_payloads_and_late_source_confirmation_are_held(self):
        aid, capture, run = self.seed(age=TTL - 1)
        self.assertIn("retention_not_expired", self.store.sweep(API(), self.config)["results"][0]["reason"])
        self.assertTrue((capture / "capture.pcap").exists())
        source = runtime.read_json(capture / "source.json")
        runtime.atomic(capture / "source.json", dict(source, at=time.time()))
        self.assertIn("retention_not_expired", self.store.sweep(API(), self.config)["results"][0]["reason"])
        self.assertFalse((run / "cleanup-plan.json").exists())

    def test_unknown_source_and_legacy_delivery_are_never_deleted(self):
        aid, capture, run = self.seed()
        (capture / "source.json").unlink()
        self.assertEqual(self.store.sweep(API(), self.config)["results"][0]["status"], "held")
        self.assertTrue((capture / "capture.pcap").exists())
        runtime.atomic(capture / "source.json", dict(schema=1, analysis_id=aid, capture_id=capture.name,
                       pcap_sha256=runtime.read_json(capture / "manifest.json")["pcap_sha256"], cluster_uid=CLUSTER,
                       kind="router", confirmation="pending", at=time.time() - 96 * 3600))
        self.assertIn("source_not_confirmed", self.store.sweep(API(), self.config)["results"][0]["reason"])
        old = runtime.read_json(run / "delivered.json")
        runtime.atomic(run / "delivered.json", {"at": old["at"], "events": old["events"]})
        self.assertIn("delivery_not_bound", self.store.sweep(API(), self.config)["results"][0]["reason"])

    def test_undelivered_is_held_regardless_of_age_or_capacity(self):
        aid, capture, run = self.seed(age=1000 * 3600)
        (run / "delivered.json").unlink()
        with patch.object(runtime, "space", side_effect=ValueError("storage_budget_exceeded")):
            out = self.store.sweep(API(), self.config)
        self.assertIn("delivery_not_bound", out["results"][0]["reason"])
        self.assertTrue((capture / "capture.pcap").exists())
        with patch.object(runtime, "tree_bytes", side_effect=ValueError("storage_budget_exceeded")):
            self.assertEqual(self.store.status()["usage"]["input"]["status"], "unknown_or_over_budget")

    def test_operator_hold_release_and_hold_after_retirement_started(self):
        aid, capture, run = self.seed()
        self.assertEqual(self.store.hold(dict(analysis_id=aid, held=True))["status"], "held")
        self.assertIn("operator_hold", self.store.sweep(API(), self.config)["results"][0]["reason"])
        self.assertFalse((run / "cleanup-plan.json").exists())
        self.store.hold(dict(analysis_id=aid, held=False))
        self.store.sweep(API(), self.config)
        with self.assertRaisesRegex(ValueError, "retirement_already_started"):
            self.store.hold(dict(analysis_id=aid, held=True))
        self.assertFalse((capture / "capture.pcap").exists())

    def test_unknown_files_symlinks_hardlinks_and_tampering_hold_all_payloads(self):
        aid, capture, run = self.seed()
        root = self.outputs["suricata"] / aid
        for kind in ("unknown", "symlink", "hardlink", "changed", "permissions", "capture_extra"):
            with self.subTest(kind=kind):
                path = root / "payload.log"
                original = path.read_bytes()
                extra = root / "unknown.log"
                if kind == "unknown":
                    runtime.atomic(extra, {})
                elif kind == "symlink":
                    path.unlink(); path.symlink_to(capture / "capture.pcap")
                elif kind == "hardlink":
                    os.link(path, extra)
                elif kind == "changed":
                    path.write_bytes(b"changed")
                elif kind == "permissions":
                    path.chmod(0o644)
                else:
                    runtime.atomic(capture / "unexpected.json", {})
                self.assertEqual(self.store.sweep(API(), self.config)["results"][0]["status"], "held")
                self.assertTrue((capture / "capture.pcap").exists())
                self.assertFalse((run / "cleanup-plan.json").exists())
                if extra.exists(): extra.unlink()
                if kind == "symlink": path.unlink(); path.write_bytes(original)
                elif kind == "changed": path.write_bytes(original)
                path.chmod(0o600)
                if kind == "capture_extra": (capture / "unexpected.json").unlink()

    def test_crash_after_first_unlink_resumes_only_saved_files(self):
        aid, capture, run = self.seed()
        original = Path.unlink
        count = [0]
        def interrupted(path, *args, **kwargs):
            count[0] += 1
            if count[0] == 2:
                raise OSError("fixture interrupted cleanup")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", interrupted):
            self.assertEqual(self.store.sweep(API(), self.config)["results"][0]["status"], "held")
        self.assertTrue((run / "cleanup-plan.json").exists())
        self.assertEqual(runtime.read_json(capture / "retired.json")["status"], "retiring")
        self.assertEqual(self.store.sweep(API(), self.config)["results"][0]["status"], "retired")

    def test_crash_after_compaction_preserves_identity_and_resumes(self):
        aid, capture, run = self.seed()
        original = runtime.atomic
        def interrupted(path, value):
            if path == capture / "retired.json" and value.get("status") == "retired":
                raise OSError("fixture crash after compact outbox")
            return original(path, value)
        with patch.object(runtime, "atomic", side_effect=interrupted):
            self.assertEqual(self.store.sweep(API(), self.config)["results"][0]["status"], "held")
        self.assertNotIn("data", runtime.stored_outbox(aid)[0])
        self.assertEqual(self.store.sweep(API(), self.config)["results"][0]["status"], "retired")

    def test_retirement_plan_proof_or_new_unknown_file_cannot_expand_cleanup(self):
        aid, capture, run = self.seed()
        plan = self.store.plan(aid, self.config)
        runtime.atomic(run / "cleanup-plan.json", plan)
        other = self.root / "unrelated"
        other.write_text("must survive")
        other.chmod(0o600)
        changed = copy.deepcopy(plan)
        changed["files"][0]["path"] = "../unrelated"
        runtime.atomic(run / "cleanup-plan.json", changed)
        self.assertIn("retirement_plan_paths", self.store.sweep(API(), self.config)["results"][0]["reason"])
        self.assertEqual(other.read_text(), "must survive")
        runtime.atomic(run / "cleanup-plan.json", plan)
        runtime.atomic(capture / "unexpected.json", {})
        self.assertIn("unknown_capture_payload", self.store.sweep(API(), self.config)["results"][0]["reason"])
        self.assertTrue((capture / "capture.pcap").exists())

    def test_owner_active_jobs_active_pods_and_other_cluster_block_cleanup(self):
        aid, capture, run = self.seed()
        for api in (API(jobs=[{"status": {}}]), API(pods=[{"metadata": {}, "status": {"phase": "Running"}}])):
            self.assertEqual(self.store.sweep(api, self.config)["reason"], "analysis_not_quiescent")
        owner = self.input / "control/owner.json"
        runtime.atomic(owner, {})
        self.assertEqual(self.store.sweep(API(), self.config)["reason"], "analysis_owner_present")
        owner.unlink()
        out = self.store.sweep(API(), dict(self.config, cluster_uid=PROD))
        self.assertIn("retention_result_identity", out["results"][0]["reason"])
        self.assertTrue((capture / "capture.pcap").exists())

    def test_intake_lock_and_run_lock_prevent_racing_deletion(self):
        aid, capture, run = self.seed()
        with (capture.parent / ".intake.lock").open("r+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.store.sweep(API(), self.config)
        with runtime.run_lock(run):
            self.assertIn("BlockingIOError", self.store.sweep(API(), self.config)["results"][0]["reason"])
        self.assertTrue((capture / "capture.pcap").exists())

    def test_clock_and_disabled_or_invalid_setting_fail_closed(self):
        aid, capture, run = self.seed()
        before = self.snapshot()
        self.assertEqual(self.store.sweep(API(), dict(self.config, retention_enabled=False))["status"], "disabled")
        self.assertEqual(before, self.snapshot())
        for value in ("true", 1, None):
            with self.assertRaisesRegex(ValueError, "invalid_retention_setting"):
                self.store.sweep(API(), dict(self.config, retention_enabled=value))
        source = runtime.read_json(capture / "source.json")
        for value in (True, float("nan"), -1, time.time() + 3600):
            runtime.atomic(capture / "source.json", dict(source, at=value))
            self.assertIn("retention_time_invalid", self.store.sweep(API(), self.config)["results"][0]["reason"])
        self.assertTrue((capture / "capture.pcap").exists())

    def test_source_registration_is_idempotent_and_does_not_create_analysis_run(self):
        aid, capture, run = self.seed()
        value = runtime.read_json(capture / "source.json")
        evidence = {k: value[k] for k in ("analysis_id", "capture_id", "pcap_sha256", "kind", "confirmation")}
        before = (capture / "source.json").read_bytes()
        self.store.register_source(evidence)
        self.assertEqual((capture / "source.json").read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "source_identity_changed"):
            self.store.register_source(dict(evidence, pcap_sha256="a" * 64))
        with self.assertRaisesRegex(ValueError, "source_not_confirmed"):
            self.store.register_source(dict(evidence, confirmation="pending"))

    def test_pending_replay_incomplete_result_and_changed_delivery_remain_held(self):
        aid, capture, run = self.seed()
        runtime.atomic(run / "replay-request.json", {})
        self.assertIn("replay_pending", self.store.sweep(API(), self.config)["results"][0]["reason"])
        (run / "replay-request.json").unlink()
        original = runtime.read_json(run / "result.json")
        runtime.atomic(run / "result.json", dict(original, status="running"))
        self.assertIn("retention_result_identity", self.store.sweep(API(), self.config)["results"][0]["reason"])
        runtime.atomic(run / "result.json", original)
        delivery = runtime.read_json(run / "delivered.json")
        runtime.atomic(run / "delivered.json", dict(delivery, events=["f" * 64]))
        self.assertIn("invalid_delivery_receipt", self.store.sweep(API(), self.config)["results"][0]["reason"])
        self.assertTrue((capture / "capture.pcap").exists())

    def test_sweep_is_bounded_and_cursor_reaches_later_captures(self):
        captures = [self.seed(suffix=f"{n:016x}")[1] for n in range(35)]
        self.assertEqual(len(self.store.sweep(API(), self.config)["results"]), 32)
        self.assertEqual(sum((p / "capture.pcap").exists() for p in captures), 3)
        self.assertEqual(len(self.store.sweep(API(), self.config)["results"]), 32)
        self.assertFalse(any((p / "capture.pcap").exists() for p in captures))

    def test_held_capture_does_not_block_another_eligible_capture(self):
        first, capture, run = self.seed()
        second, other, _ = self.seed(suffix="1123456789abcdef")
        self.store.hold(dict(analysis_id=first, held=True))
        statuses = {x["analysis_id"]: x["status"] for x in self.store.sweep(API(), self.config)["results"]}
        self.assertEqual(statuses, {first: "held", second: "retired"})
        self.assertTrue((capture / "capture.pcap").exists())
        self.assertFalse((other / "capture.pcap").exists())

    def test_corrupt_retirement_receipt_is_not_reported_as_already_retired(self):
        aid, capture, run = self.seed()
        runtime.atomic(run / "retired.json", {})
        self.assertEqual(self.store.sweep(API(), self.config)["results"][0]["status"], "held")
        self.assertTrue((capture / "capture.pcap").exists())

    def test_empty_new_coordinator_can_sweep_before_first_intake(self):
        (self.input / "control").mkdir(mode=0o700)
        out = self.store.sweep(API(), self.config)
        self.assertEqual(out["results"], [])
        self.assertEqual((self.input / "captures/.intake.lock").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.status()["last_sweep"]["status"], "checked")


if __name__ == "__main__":
    unittest.main()
