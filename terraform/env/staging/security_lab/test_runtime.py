"""Synthetic PCAP, filesystem and Kubernetes boundary regressions (no cluster)."""
import copy
import hashlib
import importlib.util
import io
import json
import os
import fcntl
from pathlib import Path
import struct
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("security_runtime", Path(__file__).with_name("runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def fixture():
    # One valid Ethernet/IPv4/ICMP echo, reserved documentation addresses.
    def checksum(data):
        if len(data) % 2:
            data += b"\0"
        value = sum(struct.unpack("!" + str(len(data) // 2) + "H", data))
        while value >> 16:
            value = (value & 0xffff) + (value >> 16)
        return (~value) & 0xffff
    body = struct.pack("!BBHHH", 8, 0, 0, 1, 1) + b"fixture"
    body = body[:2] + struct.pack("!H", checksum(body)) + body[4:]
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(body), 1, 0, 64, 1, 0,
                     bytes([198, 51, 100, 5]), bytes([192, 0, 2, 10]))
    ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
    packet = bytes.fromhex("0200000000020200000000010800") + ip + body
    return (struct.pack("<IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1) +
            struct.pack("<IIII", 1_700_000_000, 0, len(packet), len(packet)) + packet)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.input = self.root / "input"
        self.input.mkdir(mode=0o700)
        self.patches = [patch.object(runtime, "INPUT", self.input),
                        patch.object(runtime, "nfs_mount", return_value="nfs"),
                        patch.object(runtime, "CONFIG", self.root)]
        for p in self.patches:
            p.start()
        self.data = fixture()
        self.cid = "20261031T000000Z-0123456789abcdef"
        self.manifest = {"schema": 1, "capture_id": self.cid, "packet_count": 1, "truncated_packet_count": 0, "linktype": 1, "snaplen": 65535,
                         "pcap_sha256": hashlib.sha256(self.data).hexdigest(), "size_bytes": len(self.data)}
        self.config = {"schema": 1, "node": "fixture-worker", "uid": 10001, "cluster_uid": "cluster-a",
                       "config_map": "fixture-runtime", "asset_sha256": "0" * 64,
                       "images": {e: "example.invalid/" + e + "@sha256:" + "a" * 64
                                  for e in ("suricata", "zeek", "helper")}}
        config_patch = patch.object(runtime, "settings", return_value=self.config)
        config_patch.start()
        self.patches.append(config_patch)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def file(self, name, content):
        path = self.root / name
        path.write_bytes(content)
        path.chmod(0o600)
        return path

    def test_closed_pcap_validation(self):
        path = self.file("fixture.pcap", self.data)
        self.assertEqual(runtime.pcap(path)["packets"], 1)
        for corrupt in (self.data[:23], self.data[:-1], self.data + b"x",
                        self.data[:24], b"BAD!" + self.data[4:]):
            path.write_bytes(corrupt)
            with self.assertRaises(ValueError):
                runtime.pcap(path)

    def test_actual_capture_producer_manifest_contract(self):
        path = Path(__file__).resolve().parents[4] / "ansible/openwrt/roles/openwrt_security_capture/files/security_capture.py"
        spec = importlib.util.spec_from_file_location("capture_producer", path)
        producer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(producer)
        packet = self.file("producer.pcap", self.data)
        manifest = dict(producer.pcap(packet), schema=1, capture_id=self.cid,
                        profile="synthetic-fixture", interface="fixture", stop_reason="duration_limit")
        self.assertNotIn("status", manifest)
        receipt = runtime.intake(manifest, io.BytesIO(self.data))
        self.assertEqual(receipt["sha256"], manifest["pcap_sha256"])
        self.assertEqual(receipt["packets"], manifest["packet_count"])

    def test_pcap_permissions_and_symlinks(self):
        path = self.file("fixture.pcap", self.data)
        link = self.root / "link"
        link.symlink_to(path)
        with self.assertRaisesRegex(ValueError, "unsafe_file"):
            runtime.pcap(link)
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "nonprivate"):
            runtime.pcap(path)

    def test_durable_intake_and_duplicate(self):
        receipt = runtime.intake(self.manifest, io.BytesIO(self.data))
        self.assertEqual(receipt["status"], "durable")
        target = self.input / "captures" / self.cid
        self.assertEqual(runtime.digest(target / "capture.pcap"), self.manifest["pcap_sha256"])
        self.assertEqual(runtime.read_json(target / "manifest.json"), self.manifest)
        self.assertTrue(runtime.intake(self.manifest, io.BytesIO(self.data))["duplicate"])
        self.assertEqual((target / "capture.pcap").stat().st_mode & 0o777, 0o600)

    def test_bad_intake_never_publishes_manifest(self):
        for value in (self.data[:-1], self.data + b"x", self.data[:-1] + b"x"):
            with self.subTest(length=len(value)):
                cid = "20261031T000000Z-" + hashlib.sha256(value).hexdigest()[:16]
                manifest = dict(self.manifest, capture_id=cid)
                with self.assertRaises(ValueError):
                    runtime.intake(manifest, io.BytesIO(value))
                self.assertFalse((self.input / "captures" / cid / "manifest.json").exists())

    def test_input_path_and_hash_are_not_commands(self):
        for cid in ("../capture", self.cid + "/x", "$(id)", self.cid + "\n", ""):
            with self.assertRaises(ValueError):
                runtime.intake(dict(self.manifest, capture_id=cid), io.BytesIO(self.data))
        with self.assertRaisesRegex(ValueError, "invalid_hash"):
            runtime.intake(dict(self.manifest, pcap_sha256="0" * 63 + "/"), io.BytesIO(self.data))

    def test_collision_and_partial_intake_are_not_overwritten(self):
        runtime.intake(self.manifest, io.BytesIO(self.data))
        with self.assertRaisesRegex(ValueError, "collision"):
            runtime.intake(dict(self.manifest, pcap_sha256="b" * 64), io.BytesIO(self.data))
        target = self.input / "captures" / self.cid
        (target / "manifest.json").unlink()
        with self.assertRaises(FileNotFoundError):
            runtime.intake(self.manifest, io.BytesIO(self.data))

    def test_budget_before_publish(self):
        with patch.object(runtime, "space", side_effect=ValueError("filesystem_reserve_low")):
            with self.assertRaisesRegex(ValueError, "reserve"):
                runtime.intake(self.manifest, io.BytesIO(self.data))
        self.assertFalse((self.input / "captures" / self.cid).exists())

    def test_manifest_is_published_after_closed_pcap(self):
        original = runtime.atomic
        def check(path, value):
            if path.name == "manifest.json":
                self.assertEqual(runtime.digest(path.parent / "capture.pcap"), value["pcap_sha256"])
                self.assertFalse((path.parent / "capture.pcap.part").exists())
            return original(path, value)
        with patch.object(runtime, "atomic", side_effect=check):
            runtime.intake(self.manifest, io.BytesIO(self.data))

    def test_no_silent_overwrite_of_partial_manifest(self):
        path = self.root / "state.json"
        path.with_name("state.json.part").write_text("partial")
        with self.assertRaises(FileExistsError):
            runtime.atomic(path, {"status": "succeeded"})
        self.assertFalse(path.exists())

    def test_job_engine_order_and_isolation(self):
        job = runtime.job(self.config, self.cid, "b" * 64)
        pod = job["spec"]["template"]["spec"]
        self.assertEqual([x["name"] for x in pod["initContainers"]], ["suricata", "zeek"])
        self.assertEqual(pod["nodeSelector"]["kubernetes.io/hostname"], "fixture-worker")
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["preemptionPolicy"], "Never")
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 600)
        self.assertFalse(any("hostPath" in x for x in pod["volumes"]))
        for container in pod["initContainers"]:
            self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
            self.assertEqual(container["resources"]["limits"]["memory"], "2Gi")
            self.assertTrue(next(m for m in container["volumeMounts"] if m["name"] == "input")["readOnly"])
            self.assertEqual(next(m for m in container["volumeMounts"] if m["mountPath"] == "/result")["subPath"], "b" * 64)

    def test_analysis_identity_survives_staging_rebuild(self):
        before = runtime.analysis_id(self.manifest, self.config)
        after = runtime.analysis_id(self.manifest, dict(self.config, cluster_uid="new-cluster"))
        self.assertEqual(before, after)
        self.assertNotEqual(before, runtime.analysis_id(dict(self.manifest, pcap_sha256="c" * 64), self.config))
        changed = copy.deepcopy(self.config)
        changed["images"]["zeek"] += "changed"
        self.assertNotEqual(before, runtime.analysis_id(self.manifest, changed))

    def test_duplicate_intake_does_not_requeue_with_new_rules(self):
        runtime.intake(self.manifest, io.BytesIO(self.data))
        path = self.input / "captures" / self.cid / "request.json"
        first = runtime.read_json(path)
        self.config["asset_sha256"] = "d" * 64
        runtime.intake(self.manifest, io.BytesIO(self.data))
        self.assertEqual(runtime.read_json(path), first)

    def test_read_only_input_verification_detects_later_tampering(self):
        runtime.intake(self.manifest, io.BytesIO(self.data))
        target = self.input / "captures" / self.cid
        before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in target.iterdir()}
        result = runtime.verify_input(self.cid)
        self.assertEqual(result["sha256"], self.manifest["pcap_sha256"])
        self.assertEqual(before, {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in target.iterdir()})
        (target / "capture.pcap").write_bytes(self.data[:-1] + b"x")
        with self.assertRaisesRegex(ValueError, "durable_hash_mismatch"):
            runtime.verify_input(self.cid)

    def test_replay_request_emits_only_from_main_publisher_and_is_consumed_once(self):
        owner, owner_path = self.owner()
        run = owner_path.parent / owner["analysis_id"]
        runtime.atomic(run / "result.json", dict(owner, status="succeeded"))
        row = runtime.envelope(owner["analysis_id"], self.cid, "analysis_summary", {"status": "succeeded"})
        runtime.atomic(run / "outbox.json", [row])
        queued_output = io.StringIO()
        with patch.object(runtime.sys, "stdout", queued_output):
            first = runtime.request_replay(owner["analysis_id"])
            second = runtime.request_replay(owner["analysis_id"])
        self.assertEqual(queued_output.getvalue(), "")  # exec is not a CRI log stream
        self.assertEqual(first, second)
        output = io.StringIO()
        with patch.object(runtime.sys, "stdout", output):
            runtime.process_replays()
        self.assertEqual(json.loads(output.getvalue())["event_id"], row["event_id"])
        self.assertFalse((run / "replay-request.json").exists())
        repeat = io.StringIO()
        with patch.object(runtime.sys, "stdout", repeat):
            runtime.process_replays()
        self.assertEqual(repeat.getvalue(), "")
        self.assertEqual(runtime.read_json(run / "replay-result.json")["token"], first["token"])

    def test_missing_only_replay_never_skips_initial_unsubmitted_events(self):
        owner, path = self.owner()
        run = path.parent / owner["analysis_id"]
        runtime.atomic(run / "result.json", dict(owner, status="succeeded"))
        rows = [runtime.envelope(owner["analysis_id"], self.cid, kind, {"status": "succeeded"})
                for kind in ("analysis_summary", "suricata_candidate")]
        runtime.atomic(run / "outbox.json", rows)
        runtime.request_replay(owner["analysis_id"], [rows[1]["event_id"]])
        stream = io.StringIO()
        with patch.object(runtime.sys, "stdout", stream):
            runtime.process_replays()
        self.assertEqual([json.loads(x)["event_id"] for x in stream.getvalue().splitlines()], [rows[1]["event_id"]])
        self.assertFalse((run / "submitted.json").exists())
        initial = io.StringIO()
        with patch.object(runtime.sys, "stdout", initial):
            runtime.publish(run)
        self.assertEqual([json.loads(x)["event_id"] for x in initial.getvalue().splitlines()], [x["event_id"] for x in rows])
        with self.assertRaisesRegex(ValueError, "invalid_replay_events"):
            runtime.request_replay(owner["analysis_id"], ["f" * 64])

    def test_invalid_replay_is_not_a_reason_to_stop_the_analysis_job(self):
        owner, path = self.owner()
        run = path.parent / owner["analysis_id"]
        runtime.atomic(run / "result.json", dict(owner, status="succeeded"))
        row = runtime.envelope(owner["analysis_id"], self.cid, "analysis_summary", {"status": "succeeded"})
        runtime.atomic(run / "outbox.json", [row])
        runtime.atomic(run / "replay-request.json", {"token": "invalid", "event_ids": []})
        with patch.object(runtime.sys, "stdout", io.StringIO()), patch.object(runtime.os, "killpg") as kill:
            runtime.process_replays()
            kill.assert_not_called()
        self.assertTrue((run / "replay-request.json").exists())
        self.assertTrue(path.exists())

    def test_previous_cluster_owner_refuses_api_calls(self):
        class RejectAPI:
            def call(self, *args):
                raise AssertionError("cross-cluster API must not be called")
        with self.assertRaisesRegex(ValueError, "previous_cluster_owner"):
            runtime.execute(RejectAPI(), self.config, {"cluster_uid": "old"}, self.root / "owner.json")

    def test_replaced_job_is_never_adopted(self):
        class ChangedJobAPI:
            def call(self, *args):
                return {"metadata": {"uid": "replacement", "annotations": {"security-lab.home.arpa/analysis-id": "a" * 64}}}
        owner = {"cluster_uid": "cluster-a", "config_sha256": "0" * 64,
                 "job_name": "fixture", "analysis_id": "a" * 64, "job_uid": "original"}
        with self.assertRaisesRegex(ValueError, "job_uid_changed"):
            runtime.execute(ChangedJobAPI(), self.config, owner, self.root / "owner.json")

    def owner(self):
        control = self.input / "control"
        control.mkdir(mode=0o700, exist_ok=True)
        aid = "e" * 64
        (control / aid).mkdir(mode=0o700)
        owner = {"cluster_uid": "cluster-a", "config_sha256": "0" * 64, "analysis_id": aid,
                 "capture_id": self.cid, "job_name": "fixture", "job_uid": "original",
                 "started_at": runtime.time.time()}
        path = control / "owner.json"
        runtime.atomic(path, owner)
        return owner, path

    def test_global_lock_contention_never_creates_job(self):
        control = self.input / "control"
        control.mkdir(mode=0o700)
        with (control / "global.lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(runtime, "API") as api:
                with self.assertRaises(BlockingIOError):
                    runtime.coordinate()
                api.return_value.call.assert_not_called()

    def test_oom_and_missing_engine_results_never_become_success(self):
        for condition in ("Failed", "Complete"):
            with self.subTest(job_terminal=condition):
                # Isolate result paths for the second terminal case.
                control = self.input / "control"
                if control.exists():
                    import shutil
                    shutil.rmtree(control)
                owner, path = self.owner()
                class TerminalAPI:
                    def call(self, method, url, body=None):
                        if url.startswith(runtime.PODS):
                            return {"items": [{"metadata": {"ownerReferences": [{"uid": "original"}]},
                                               "status": {"initContainerStatuses": [
                                                   {"name": "suricata", "state": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}]}}]}
                        return {"metadata": {"uid": "original", "annotations": {"security-lab.home.arpa/analysis-id": owner["analysis_id"]}},
                                "status": {"conditions": [{"type": condition, "status": "True"}]}}
                with patch.object(runtime, "OUTPUTS", {e: self.root / e for e in ("suricata", "zeek")}), patch.object(runtime.sys, "stdout", io.StringIO()):
                    runtime.execute(TerminalAPI(), self.config, owner, path)
                result = runtime.read_json(control / owner["analysis_id"] / "result.json")
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["engines"]["suricata"]["kubernetes_state"]["terminated"]["reason"], "OOMKilled")
                self.assertFalse(path.exists())

    def test_memory_stop_persists_before_uid_scoped_delete_and_waits_for_pods(self):
        owner, path = self.owner()
        class StopAPI:
            deleted = False
            has_pod = True
            def call(self, method, url, body=None):
                if method == "DELETE":
                    self.deleted = True
                    self.delete_body = body
                    return {}
                if url.startswith(runtime.PODS):
                    return {"items": ([{"metadata": {"ownerReferences": [{"uid": "original"}]}}] if self.has_pod else [])}
                if self.deleted:
                    raise urllib.error.HTTPError(url, 404, "gone", {}, io.BytesIO())
                return {"metadata": {"uid": "original", "annotations": {"security-lab.home.arpa/analysis-id": owner["analysis_id"]}}}
        api = StopAPI()
        with patch.object(runtime, "mem_available", return_value=0):
            with self.assertRaisesRegex(ValueError, "host_memory_reserve_low"):
                runtime.execute(api, self.config, owner, path)
        self.assertEqual(api.delete_body["preconditions"], {"uid": "original"})
        self.assertEqual(runtime.read_json(path)["stop_reason"], "host_memory_reserve_low")
        with self.assertRaisesRegex(ValueError, "stopping_owned_analysis"):
            runtime.execute(api, self.config, runtime.read_json(path), path)
        self.assertTrue(path.exists())
        api.has_pod = False
        with patch.object(runtime, "OUTPUTS", {e: self.root / e for e in ("suricata", "zeek")}), patch.object(runtime.sys, "stdout", io.StringIO()):
            runtime.execute(api, self.config, runtime.read_json(path), path)
        self.assertFalse(path.exists())
        result = runtime.read_json(self.input / "control" / owner["analysis_id"] / "result.json")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stop_reason"], "host_memory_reserve_low")

    def test_engine_guard_stops_only_its_child_and_saves_failure(self):
        output = self.root / "engine-output"
        output.mkdir(mode=0o700)
        packet = self.input / "capture.pcap"
        packet.write_bytes(self.data)
        packet.chmod(0o600)
        self.file("local.rules", b"alert icmp any any -> any any (sid:1000001;)\n")
        real_path = Path
        def mapped_path(value):
            return output if value == "/result" else real_path(value)
        class Child:
            pid = 987654
            returncode = None
            def poll(self):
                return self.returncode
            def wait(self, timeout):
                self.returncode = -9
                return self.returncode
        child = Child()
        with patch.object(runtime, "Path", side_effect=mapped_path), patch.object(runtime, "mem_available", side_effect=[2 * runtime.GIB, 0]), \
                patch.object(runtime.subprocess, "Popen", return_value=child), patch.object(runtime.os, "killpg") as kill, patch.object(runtime.sys, "stdout", io.StringIO()):
            self.assertEqual(runtime.engine_run("suricata"), 1)
        kill.assert_called_once_with(child.pid, runtime.signal.SIGKILL)
        result = runtime.read_json(output / "engine.json")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "host_memory_reserve_low")

    def test_existing_engine_evidence_is_not_overwritten(self):
        output = self.root / "existing-output"
        output.mkdir(mode=0o700)
        runtime.atomic(output / "engine.json", {"status": "succeeded", "previous": True})
        def mapped_path(value):
            return output if value == "/result" else Path(value)
        with patch.object(runtime, "Path", side_effect=mapped_path), patch.object(runtime.subprocess, "Popen") as child:
            with self.assertRaisesRegex(ValueError, "existing_engine_output_requires_reconciliation"):
                runtime.engine_run("suricata")
            child.assert_not_called()
        self.assertEqual(runtime.read_json(output / "engine.json"), {"status": "succeeded", "previous": True})

    def test_stdout_summary_is_bounded_without_losing_durable_output_inventory(self):
        result = {"status": "succeeded", "actual_version": "fixture",
                  "output_files": [{"path": "x" * 200, "bytes": 1000, "sha256": "a" * 64} for _ in range(128)]}
        summary = runtime.engine_summary(result)
        self.assertEqual(summary["output_file_count"], 128)
        self.assertEqual(summary["output_bytes"], 128000)
        self.assertNotIn("output_files", summary)
        row = runtime.envelope("a" * 64, self.cid, "analysis_summary", {"engines": {"suricata": summary, "zeek": summary}})
        self.assertLess(len(json.dumps(row)), 16384)
        self.assertEqual(len(result["output_files"]), 128)

    def test_missing_and_duplicate_node_conditions_fail_closed(self):
        node = {"metadata": {"labels": {"security-lab.home.arpa/analysis": "true"}},
                "spec": {}, "status": {"nodeInfo": {"architecture": "arm64"}, "conditions": [
                    {"type": k, "status": v} for k, v in (("Ready", "True"), ("MemoryPressure", "False"),
                                                         ("DiskPressure", "False"), ("PIDPressure", "False"))]}}
        runtime.node_ready(node)
        for change in ("duplicate", "missing", "pressure", "control-plane", "taint"):
            bad = copy.deepcopy(node)
            if change == "duplicate":
                bad["status"]["conditions"].append({"type": "Ready", "status": "True"})
            elif change == "missing":
                bad["status"]["conditions"].pop()
            elif change == "pressure":
                bad["status"]["conditions"][1]["status"] = "Unknown"
            elif change == "control-plane":
                bad["metadata"]["labels"]["node-role.kubernetes.io/control-plane"] = ""
            else:
                bad["spec"]["taints"] = [{"effect": "NoSchedule"}]
            with self.assertRaises(ValueError):
                runtime.node_ready(bad)

    def test_replay_preserves_stored_event_identity(self):
        run = self.root / "run"
        run.mkdir(mode=0o700)
        row = runtime.envelope("a" * 64, self.cid, "analysis_summary", {"status": "failed"})
        runtime.atomic(run / "outbox.json", [row])
        first, second = io.StringIO(), io.StringIO()
        with patch.object(runtime.sys, "stdout", first):
            runtime.publish(run)
        with patch.object(runtime.sys, "stdout", second):
            runtime.publish(run)
        self.assertEqual(first.getvalue(), second.getvalue())
        self.assertEqual(runtime.read_json(run / "submitted.json")["events"], [row["event_id"]])

    def test_output_symlinks_and_oversize_envelopes_rejected(self):
        (self.root / "link").symlink_to(self.input)
        with self.assertRaisesRegex(ValueError, "symlink"):
            runtime.tree_bytes(self.root)
        with self.assertRaisesRegex(ValueError, "envelope_too_large"):
            runtime.envelope("a" * 64, self.cid, "analysis_summary", {"body": "x" * 20000})

    def test_actual_nfs_version_must_match_policy_with_remote_locking(self):
        self.patches[1].stop()
        mountinfo = self.file("mountinfo", b"")
        for fs, options, accepted in (("nfs", "rw,vers=3,local_lock=none", True),
                                      ("nfs4", "rw,vers=4.1", False),
                                      ("nfs", "rw", False),
                                      ("nfs", "rw,vers=3,nolock", False),
                                      ("nfs", "rw,vers=3,local_lock=all", False)):
            with self.subTest(fs=fs, options=options):
                mountinfo.write_text("1 0 0:1 / " + str(self.input) + " rw - " + fs + " fixture:/export " + options + "\n")
                if accepted:
                    self.assertEqual(runtime.nfs_mount(self.input, mountinfo), "nfs")
                else:
                    with self.assertRaises(ValueError):
                        runtime.nfs_mount(self.input, mountinfo)
        self.config["nfs_version"] = 4
        mountinfo.write_text("1 0 0:1 / " + str(self.input) + " rw - nfs4 fixture:/export rw,vers=4.1\n")
        self.assertEqual(runtime.nfs_mount(self.input, mountinfo), "nfs4")

    def test_local_filesystem_is_not_nfs(self):
        mountinfo = self.file("mountinfo", b"1 0 0:1 / / rw - overlay overlay rw\n")
        # Bypass the fixture override to test the real detector directly.
        self.patches[1].stop()
        with self.assertRaisesRegex(ValueError, "nfs_mount_missing"):
            runtime.nfs_mount(self.input, mountinfo)


if __name__ == "__main__":
    unittest.main()
