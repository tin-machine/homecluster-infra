"""Retire only delivered, source-confirmed payloads; retain identity tombstones.

The coordinator calls sweep while holding its global lock. All mutations also
take the intake lock and the analysis mutation lock, in that order. No clock
age, capacity pressure, or TTL alone authorizes removal of unknown artifacts.
"""
import fcntl
import os
from pathlib import Path
import re
import time

TTL = 72 * 3600
BATCH = 32
MAX_ENTRIES = 4096
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class Store:
    def __init__(self, runtime):
        # Use the same private-file, fsync, identity and receipt contracts as intake.
        self.r = runtime

    def run(self, aid):
        self.r.require(isinstance(aid, str) and self.r.HEX.fullmatch(aid), "invalid_analysis_id")
        return self.r.private_dir(self.r.INPUT / "control" / aid)

    def mutable(self, run):
        self.r.require(not (run / "cleanup-plan.json").exists() and not (run / "retired.json").exists(),
                       "retirement_already_started")

    def register_source(self, value):
        r = self.r
        r.require(isinstance(value, dict) and set(value) == {
            "analysis_id", "capture_id", "pcap_sha256", "kind", "confirmation"}, "invalid_source_evidence")
        r.require((value["kind"], value["confirmation"]) in {
            ("router", "acknowledged"), ("synthetic", "not_applicable")}, "source_not_confirmed")
        verified = r.verify_input(value["capture_id"])
        r.require(verified["analysis_id"] == value["analysis_id"] and verified["sha256"] == value["pcap_sha256"],
                  "source_identity_changed")
        capture = r.private_dir(r.INPUT / "captures" / value["capture_id"])
        # Intake may be ACKed before the coordinator creates its analysis run.
        # Source evidence belongs to the capture and never creates a fake run.
        fd = os.open(capture.parent / ".intake.lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            r.regular(capture.parent / ".intake.lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r.require(not (capture / "retired.json").exists(), "retirement_already_started")
            run = r.INPUT / "control" / value["analysis_id"]
            if run.exists():
                self.mutable(r.private_dir(run))
            path = capture / "source.json"
            config = r.settings()
            record = dict(value, schema=1, cluster_uid=config["cluster_uid"])
            if path.exists():
                previous = r.read_json(path)
                r.require({k: v for k, v in previous.items() if k != "at"} == record and r.valid_epoch(previous["at"]),
                          "source_evidence_changed")
            else:
                r.atomic(path, dict(record, at=time.time()))
        finally:
            os.close(fd)
        return {"status": "source_confirmed", "analysis_id": value["analysis_id"]}

    def hold(self, value):
        r = self.r
        r.require(isinstance(value, dict) and set(value) == {"analysis_id", "held"} and
                  type(value["held"]) is bool, "invalid_retention_hold")
        run = self.run(value["analysis_id"])
        with r.run_lock(run):
            self.mutable(run)
            path = run / "hold.json"
            if path.exists():
                previous = self.read_hold(run)
                if previous["held"] == value["held"]:
                    return dict(status="held" if value["held"] else "released", analysis_id=value["analysis_id"])
            r.atomic(path, dict(schema=1, held=value["held"], at=time.time()))
        return dict(status="held" if value["held"] else "released", analysis_id=value["analysis_id"])

    def read_hold(self, run):
        r = self.r
        if not (run / "hold.json").exists():
            return {"held": False}
        value = r.read_json(run / "hold.json")
        r.require(set(value) == {"schema", "held", "at"} and type(value["schema"]) is int and
                  value["schema"] == 1 and type(value["held"]) is bool and r.valid_epoch(value["at"]),
                  "invalid_retention_hold")
        return value

    def file(self, path):
        info = self.r.regular(path)
        self.r.require(info.st_uid == os.getuid(), "file_owner_mismatch")
        return {"bytes": info.st_size, "sha256": self.r.digest(path)}

    def identities(self, aid, config):
        r = self.r
        run = self.run(aid)
        result = r.read_json(run / "result.json")
        r.require(type(result["schema"]) is int and result["schema"] == 1 and
                  result["analysis_id"] == aid and result["status"] in ("succeeded", "failed") and
                  result["cluster_uid"] == config["cluster_uid"] and r.canonical_uid(result["job_uid"]) and
                  isinstance(result["pcap_sha256"], str) and r.HEX.fullmatch(result["pcap_sha256"]),
                  "retention_result_identity")
        cid = r.capture_id(result["capture_id"])
        capture = r.private_dir(r.INPUT / "captures" / cid)
        manifest, request = r.read_json(capture / "manifest.json"), r.read_json(capture / "request.json")
        r.require(type(manifest["schema"]) is int and manifest["schema"] == 1 and
                  type(request["schema"]) is int and request["schema"] == 1 and
                  manifest["capture_id"] == cid and manifest["pcap_sha256"] == result["pcap_sha256"] and
                  request["analysis_id"] == aid and request["config_sha256"] == result["config_sha256"],
                  "retention_capture_identity")
        return run, capture, result, request

    def payload_names(self, result):
        """Paths are derived from validated IDs and a flat, bounded engine manifest."""
        r = self.r
        aid, cid = result["analysis_id"], result["capture_id"]
        names = {"input": ["captures/" + cid + "/capture.pcap"]}
        for engine in ("suricata", "zeek"):
            meta = result["engines"][engine]
            r.require(meta["status"] == "succeeded" and isinstance(meta["output_files"], list) and
                      1 <= len(meta["output_files"]) <= 128, "incomplete_engine_payload_inventory")
            files = meta["output_files"]
            r.require(all(isinstance(x["path"], str) and NAME.fullmatch(x["path"]) and x["path"] != "engine.json"
                          for x in files), "unsafe_retention_path")
            r.require(len({x["path"] for x in files}) == len(files), "duplicate_retention_path")
            names[engine] = [aid + "/engine.json"] + [aid + "/" + x["path"] for x in files]
        return names

    def roots(self):
        return dict(input=self.r.INPUT, **self.r.OUTPUTS)

    def validate_directories(self, names, resumed=False):
        r = self.r
        for engine in ("suricata", "zeek"):
            parent = self.roots()[engine] / names[engine][0].split("/")[0]
            r.require(not parent.is_symlink(), "unsafe_engine_directory")
            if resumed and not parent.exists():
                continue
            r.private_dir(parent)
            allowed = {Path(x).name for x in names[engine]}
            actual = {x.name for x in parent.iterdir()}
            r.require(actual <= allowed if resumed else actual == allowed, "unknown_engine_payload")

    def eligible(self, aid, config):
        r = self.r
        run, capture, result, request = self.identities(aid, config)
        r.require(not self.read_hold(run)["held"], "operator_hold")
        r.require(not (run / "replay-request.json").exists(), "replay_pending")
        rows = r.stored_outbox(aid)
        delivery = r.delivery_info(run, [x["event_id"] for x in rows])
        r.require(delivery is not None and delivery.get("schema") == 2 and
                  r.canonical_uid(delivery.get("loki_cluster_uid")), "delivery_not_bound")
        source = r.read_json(capture / "source.json")
        r.require(type(source["schema"]) is int and source["schema"] == 1 and source["analysis_id"] == aid and
                  source["capture_id"] == result["capture_id"] and source["pcap_sha256"] == result["pcap_sha256"] and
                  source["cluster_uid"] == config["cluster_uid"] and (source["kind"], source["confirmation"]) in {
                      ("router", "acknowledged"), ("synthetic", "not_applicable")}, "source_not_confirmed")
        times = [request["queued_at"], result["started_at"], result["finished_at"],
                 delivery.get("confirmed_at", delivery["at"]), source["at"]]
        r.require(all(r.valid_epoch(x) for x in times) and result["started_at"] <= result["finished_at"],
                  "retention_time_invalid")
        r.require(time.time() - max(times) >= TTL, "retention_not_expired")
        return run, capture, result, rows

    def plan(self, aid, config):
        r = self.r
        run, capture, result, rows = self.eligible(aid, config)
        self.validate_capture(capture)
        names = self.payload_names(result)
        self.validate_directories(names)
        files = []
        for volume, paths in names.items():
            for relative in paths:
                path = self.roots()[volume] / relative
                fingerprint = self.file(path)
                if volume == "input":
                    manifest = r.read_json(capture / "manifest.json")
                    r.require(fingerprint == {"bytes": manifest["size_bytes"], "sha256": manifest["pcap_sha256"]},
                              "retention_input_changed")
                elif path.name == "engine.json":
                    r.require(r.read_json(path) == result["engines"][volume], "engine_inventory_changed")
                else:
                    expected = next(x for x in result["engines"][volume]["output_files"] if x["path"] == path.name)
                    r.require(fingerprint == {k: expected[k] for k in ("bytes", "sha256")}, "engine_payload_changed")
                files.append(dict(volume=volume, path=relative, **fingerprint))
        proofs = {str(path.relative_to(r.INPUT)): self.file(path)["sha256"] for path in [
            run / "result.json", run / "delivered.json", capture / "source.json", capture / "manifest.json", capture / "request.json"]}
        compact = [{k: row[k] for k in ("schema", "security_source", "event_type", "analysis_id", "capture_id", "event_id")}
                   for row in rows]
        return dict(schema=1, analysis_id=aid, capture_id=result["capture_id"], cluster_uid=config["cluster_uid"],
                    pcap_sha256=result["pcap_sha256"], at=time.time(), files=files, proofs=proofs,
                    outbox_sha256=self.file(run / "outbox.json")["sha256"], compact_outbox=compact)

    def retire(self, aid, config):
        """Called only with global, intake, and run locks; every file verified before unlink."""
        r = self.r
        run = self.run(aid)
        if (run / "retired.json").exists():
            _, capture, result, _ = self.identities(aid, config)
            marker = r.read_json(run / "retired.json")
            r.require(type(marker["schema"]) is int and marker["schema"] == 1 and
                      marker == r.read_json(capture / "retired.json") and marker["status"] == "retired" and
                      marker["analysis_id"] == aid and marker["capture_id"] == result["capture_id"] and
                      marker["pcap_sha256"] == result["pcap_sha256"] and r.valid_epoch(marker["completed_at"]) and
                      marker["plan_sha256"] == r.digest(run / "cleanup-plan.json"), "invalid_retirement_receipt")
            return {"status": "retired", "analysis_id": aid, "already_retired": True}
        plan_path = run / "cleanup-plan.json"
        if not plan_path.exists():
            r.atomic(plan_path, self.plan(aid, config))
        plan = r.read_json(plan_path, max_bytes=128 * 1024)
        run, capture, result, _ = self.identities(aid, config)
        r.require(not self.read_hold(run)["held"] and not (run / "replay-request.json").exists(), "retirement_hold")
        names = self.payload_names(result)
        r.require(type(plan["schema"]) is int and plan["schema"] == 1 and plan["analysis_id"] == aid and
                  plan["capture_id"] == result["capture_id"] and plan["cluster_uid"] == config["cluster_uid"] and
                  plan["pcap_sha256"] == result["pcap_sha256"] and r.valid_epoch(plan["at"]), "retirement_plan_identity")
        expected = {(v, p) for v, paths in names.items() for p in paths}
        r.require(isinstance(plan["files"], list) and len(plan["files"]) == len(expected) and
                  {(x["volume"], x["path"]) for x in plan["files"]} == expected, "retirement_plan_paths")
        proof_paths = [run / "result.json", run / "delivered.json", capture / "source.json", capture / "manifest.json", capture / "request.json"]
        r.require(plan["proofs"] == {str(x.relative_to(r.INPUT)): self.file(x)["sha256"] for x in proof_paths},
                  "retirement_proof_changed")
        # Revalidate eligibility even during crash recovery; no age-only resumption.
        self.eligible(aid, config)
        self.validate_capture(capture, resumed=True)
        self.validate_directories(names, resumed=True)
        current_rows = r.stored_outbox(aid)
        compact = [{k: row[k] for k in ("schema", "security_source", "event_type", "analysis_id", "capture_id", "event_id")}
                   for row in current_rows]
        r.require(compact == plan["compact_outbox"] and
                  (self.file(run / "outbox.json")["sha256"] == plan["outbox_sha256"] or current_rows == compact),
                  "retirement_outbox_changed")
        for row in plan["files"]:
            path = self.roots()[row["volume"]] / row["path"]
            if path.exists() or path.is_symlink():
                r.private_dir(path.parent)
                r.require(self.file(path) == {k: row[k] for k in ("bytes", "sha256")}, "retirement_payload_changed")
        marker = dict(schema=1, analysis_id=aid, capture_id=result["capture_id"],
                      pcap_sha256=result["pcap_sha256"], plan_sha256=r.digest(plan_path), at=plan["at"])
        if not (capture / "retired.json").exists():
            r.atomic(capture / "retired.json", dict(marker, status="retiring"))
        else:
            previous = r.read_json(capture / "retired.json")
            r.require(all(previous[k] == v for k, v in marker.items()), "retirement_marker_changed")
        for row in plan["files"]:
            path = self.roots()[row["volume"]] / row["path"]
            if path.exists():
                r.require(self.file(path) == {k: row[k] for k in ("bytes", "sha256")}, "retirement_payload_changed")
                path.unlink()
                self.fsync_dir(path.parent)
        for engine in ("suricata", "zeek"):
            parent = self.roots()[engine] / aid
            if parent.exists():
                r.private_dir(parent).rmdir()
                self.fsync_dir(parent.parent)
        if current_rows != compact:
            r.atomic(run / "outbox.json", compact)
        completed = dict(marker, status="retired", completed_at=time.time())
        r.atomic(capture / "retired.json", completed)
        r.atomic(run / "retired.json", completed)
        return dict(status="retired", analysis_id=aid, already_retired=False)

    @staticmethod
    def fsync_dir(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def validate_capture(self, capture, resumed=False):
        allowed = {"manifest.json", "request.json", "source.json", "capture.pcap"}
        actual = {x.name for x in self.r.private_dir(capture).iterdir()}
        self.r.require(actual <= allowed | {"retired.json"} if resumed else actual == allowed,
                       "unknown_capture_payload")
        for child in capture.iterdir():
            self.r.regular(child)

    def sweep(self, api, config):
        r = self.r
        r.require(type(config.get("retention_enabled", False)) is bool, "invalid_retention_setting")
        if not config.get("retention_enabled", False):
            return {"status": "disabled"}
        control = r.private_dir(r.INPUT / "control")
        if (control / "owner.json").exists():
            return {"status": "held", "reason": "analysis_owner_present"}
        jobs, pods = api.call("GET", r.JOBS)["items"], api.call("GET", r.PODS)["items"]
        if any(not r.terminal(x) for x in jobs) or any(
                x["status"]["phase"] not in ("Succeeded", "Failed") and
                x["metadata"].get("labels", {}).get("app.kubernetes.io/component") != "security-coordinator" for x in pods):
            return {"status": "held", "reason": "analysis_not_quiescent"}
        for root in self.roots().values():
            r.nfs_mount(root)
        captures = r.private_dir(r.INPUT / "captures", create=True)
        fd = os.open(captures / ".intake.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            r.regular(captures / ".intake.lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            entries = []
            with os.scandir(control) as scan:
                for entry in scan:
                    r.require(len(entries) < MAX_ENTRIES, "retention_scan_bound")
                    entries.append(entry.name)
            ids = sorted(x for x in entries if r.HEX.fullmatch(x))
            status_path = control / "retention-status.json"
            prior = r.read_json(status_path) if status_path.exists() else {}
            cursor = prior.get("cursor", "")
            ids = ([x for x in ids if x > cursor] + [x for x in ids if x <= cursor])[:BATCH]
            results = []
            for aid in ids:
                try:
                    run = self.run(aid)
                    with r.run_lock(run, blocking=False):
                        results.append(self.retire(aid, config))
                except (ValueError, OSError, KeyError, TypeError) as error:
                    results.append(dict(status="held", analysis_id=aid, reason=type(error).__name__ + ":" + str(error)[:120]))
            value = dict(schema=1, status="checked", at=time.time(), cursor=ids[-1] if ids else "", results=results)
            r.atomic(status_path, value)
            return value
        finally:
            os.close(fd)

    def status(self):
        r = self.r
        path = r.INPUT / "control" / "retention-status.json"
        usage = {}
        for name, root in self.roots().items():
            try:
                r.nfs_mount(root)
                usage[name] = dict(status="within_budget", bytes=r.tree_bytes(root), limit_bytes=r.GIB)
            except (ValueError, OSError):
                usage[name] = dict(status="unknown_or_over_budget", bytes=None, limit_bytes=r.GIB)
        return dict(enabled=r.settings().get("retention_enabled", False), usage=usage,
                    last_sweep=r.read_json(path) if path.exists() else None)
