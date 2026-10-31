#!/usr/bin/env python3
"""Offline PCAP intake, bounded engines and a single durable coordinator.

No shell commands, capture credentials or external network clients are used.
Only the coordinator has a projected, namespace-limited Kubernetes token.
"""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import ssl
import stat
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

GIB = 1024 ** 3
MAX_PCAP = 65_551_024
CAPTURE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16}$")
HEX = re.compile(r"^[a-f0-9]{64}$")
INPUT = Path("/input")
OUTPUTS = {"suricata": Path("/suricata"), "zeek": Path("/zeek")}
CONFIG = Path("/config")
TOKEN = Path("/var/run/secrets/security-lab/token")
CA = "/var/run/secrets/security-lab/ca.crt"


def require(test, reason):
    if not test:
        raise ValueError(reason)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe_file")
    require(not info.st_mode & 0o077, "nonprivate_file")
    return info


def private_dir(path, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o077, "unsafe_directory")
    require(info.st_uid == os.getuid(), "directory_owner_mismatch")
    return path


def atomic(path, value):
    """Publish manifest last, with fsync on file and parent (including NFS)."""
    temporary = path.with_name(path.name + ".part")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        # A partial file is evidence; do not silently overwrite/retry it.
        raise


def read_json(path, max_bytes=65536):
    info = regular(path)
    require(info.st_size <= max_bytes, "json_too_large")
    return json.loads(path.read_text())


def pcap(path):
    size = regular(path).st_size
    require(24 <= size <= MAX_PCAP, "pcap_size_invalid")
    with path.open("rb") as stream:
        header = stream.read(24)
        formats = {b"\xd4\xc3\xb2\xa1": ("<", 1_000_000), b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
                   b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000), b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000)}
        require(header[:4] in formats, "unsupported_pcap")
        endian, resolution = formats[header[:4]]
        major, minor, _, _, snap, link = struct.unpack(endian + "HHIIII", header[4:])
        require((major, minor) == (2, 4) and snap == 65535 and link in (1, 101, 113, 276),
                "unsupported_pcap_header")
        count = truncated = 0
        while True:
            record = stream.read(16)
            if not record:
                break
            require(len(record) == 16, "partial_pcap_record")
            _, fraction, included, original = struct.unpack(endian + "IIII", record)
            require(fraction < resolution and 0 < included <= snap and included <= original,
                    "invalid_pcap_record")
            require(len(stream.read(included)) == included, "partial_pcap_packet")
            count += 1
            truncated += included < original
            require(count <= 1000, "packet_budget_exceeded")
        require(count > 0, "empty_pcap")
    return {"bytes": size, "packets": count, "truncated_packets": truncated, "linktype": link, "snaplen": snap}


def tree_bytes(root):
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            require(not (Path(current) / name).is_symlink(), "directory_symlink")
        for name in files:
            info = (Path(current) / name).lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe_tree_file")
            total += info.st_size
            require(total <= GIB, "storage_budget_exceeded")
    return total


def space(root, reserve=GIB):
    info = os.statvfs(root)
    require(info.f_bavail * info.f_frsize >= reserve, "filesystem_reserve_low")
    return tree_bytes(root)


def nfs_mount(root, mountinfo=Path("/proc/self/mountinfo")):
    matches = []
    for row in mountinfo.read_text().splitlines():
        left, right = row.split(" - ", 1)
        mount = left.split()[4].replace(r"\040", " ").replace(r"\134", "\\")
        if str(root) == mount or str(root).startswith(mount.rstrip("/") + "/"):
            fields = right.split()
            matches.append((len(mount), fields[0], fields[2].split(",")))
    require(matches and max(matches)[1] in ("nfs", "nfs4"), "nfs_mount_missing")
    _, kind, options = max(matches)
    wanted = settings().get("nfs_version", 3)
    require(type(wanted) is int and wanted in (3, 4), "nfs_version_policy_invalid")
    versions = [o.split("=", 1)[1] for o in options if o.startswith("vers=")]
    require(len(versions) == 1 and versions[0].split(".")[0] == str(wanted), "nfs_version_mismatch")
    require("nolock" not in options and "local_lock=all" not in options, "nfs_remote_locking_required")
    return kind


def mem_available(path=Path("/proc/meminfo")):
    rows = [line.split() for line in path.read_text().splitlines() if line.startswith("MemAvailable:")]
    require(len(rows) == 1 and rows[0][2] == "kB", "mem_available_unknown")
    return int(rows[0][1]) * 1024


def capture_id(value):
    require(isinstance(value, str) and CAPTURE.fullmatch(value), "invalid_capture_id")
    return value


def settings():
    value = json.loads((CONFIG / "settings.json").read_text())
    require(value["schema"] == 1 and value["uid"] >= 1000, "invalid_settings")
    require(value["node"] and value["cluster_uid"] and value["config_map"], "missing_identity")
    for engine in ("suricata", "zeek", "helper"):
        require(re.fullmatch(r"[a-z0-9./:_-]+@sha256:[a-f0-9]{64}", value["images"][engine]),
                "image_not_pinned")
    return value


def analysis_id(manifest, config):
    material = {"pcap_sha256": manifest["pcap_sha256"], "capture_id": manifest["capture_id"],
                "config": {k: v for k, v in config.items() if k not in ("cluster_uid", "config_map")},
                "assets": {p.name: digest(p) for p in sorted(CONFIG.iterdir())
                                            if p.is_file() and p.name != "settings.json"}}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def intake(manifest, source):
    """The caller retains the router artifact until this returns a durable receipt."""
    cid = capture_id(manifest["capture_id"])
    require(HEX.fullmatch(manifest["pcap_sha256"]), "invalid_hash")
    require(type(manifest.get("schema")) is int and manifest["schema"] == 1, "capture_schema_invalid")
    require(type(manifest["size_bytes"]) is int and 24 <= manifest["size_bytes"] <= MAX_PCAP, "invalid_size")
    nfs_mount(INPUT)
    root = private_dir(INPUT / "captures", create=True)
    lock = os.open(root / ".intake.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        space(INPUT, reserve=2 * GIB)
        target = root / cid
        if target.exists():
            private_dir(target)
            saved = read_json(target / "manifest.json")
            require(saved == manifest and digest(target / "capture.pcap") == manifest["pcap_sha256"],
                    "capture_collision_or_partial_intake")
            pcap(target / "capture.pcap")
            require((target / "request.json").exists(), "partial_queue_requires_reconciliation")
            request = read_json(target / "request.json")
            return {"status": "durable", "capture_id": cid, "sha256": manifest["pcap_sha256"],
                    "analysis_id": request["analysis_id"], "duplicate": True}
        require(tree_bytes(INPUT) + manifest["size_bytes"] <= GIB, "input_budget_exceeded")
        private_dir(target, create=True)
        part = target / "capture.pcap.part"
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        h, remaining = hashlib.sha256(), manifest["size_bytes"]
        with os.fdopen(fd, "wb") as destination:
            while remaining:
                block = source.read(min(remaining, 1024 * 1024))
                require(block, "input_stream_truncated")
                destination.write(block)
                h.update(block)
                remaining -= len(block)
            require(not source.read(1), "input_stream_too_long")
            destination.flush()
            os.fsync(destination.fileno())
        require(h.hexdigest() == manifest["pcap_sha256"], "input_hash_mismatch")
        info = pcap(part)
        expected = {"packet_count": info["packets"], "truncated_packet_count": info["truncated_packets"],
                    "linktype": info["linktype"], "snaplen": info["snaplen"]}
        require(all(type(manifest.get(k)) is int and manifest[k] == v for k, v in expected.items()),
                "pcap_manifest_summary_mismatch")
        os.replace(part, target / "capture.pcap")
        atomic(target / "manifest.json", manifest)
        # Reopen from the published NAS path, rather than trusting transfer buffers.
        require(digest(target / "capture.pcap") == manifest["pcap_sha256"], "durable_hash_mismatch")
        config = settings()
        aid = analysis_id(manifest, config)
        atomic(target / "request.json", {"schema": 1, "analysis_id": aid,
                                        "config_sha256": config["asset_sha256"], "queued_at": time.time()})
        return dict(info, status="durable", capture_id=cid, sha256=manifest["pcap_sha256"], analysis_id=aid, duplicate=False)
    finally:
        os.close(lock)


def verify_input(cid):
    """Read-only durable receipt; bytes never leave this process."""
    capture_id(cid)
    nfs_mount(INPUT)
    captures = private_dir(INPUT / "captures")
    regular(captures / ".intake.lock")
    lock = os.open(captures / ".intake.lock", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        target = private_dir(captures / cid)
        manifest = read_json(target / "manifest.json")
        require(manifest["capture_id"] == cid and manifest["schema"] == 1, "capture_identity_mismatch")
        packet = target / "capture.pcap"
        info = pcap(packet)
        require(info["bytes"] == manifest["size_bytes"] and digest(packet) == manifest["pcap_sha256"],
                "durable_hash_mismatch")
        require(info["packets"] == manifest["packet_count"] and info["snaplen"] == manifest["snaplen"] and
                info["linktype"] == manifest["linktype"] and info["truncated_packets"] == manifest["truncated_packet_count"],
                "pcap_manifest_summary_mismatch")
        request = read_json(target / "request.json")
        require(HEX.fullmatch(request["analysis_id"]), "invalid_queued_analysis_id")
        return dict(info, status="durable", capture_id=cid, sha256=manifest["pcap_sha256"],
                    analysis_id=request["analysis_id"])
    finally:
        os.close(lock)


def stored_outbox(aid):
    require(isinstance(aid, str) and HEX.fullmatch(aid), "invalid_analysis_id")
    run = private_dir(INPUT / "control" / aid)
    result = read_json(run / "result.json")
    require(result["analysis_id"] == aid and result["status"] in ("succeeded", "failed"), "result_not_durable")
    rows = read_json(run / "outbox.json", max_bytes=1024 * 1024)
    require(isinstance(rows, list) and 1 <= len(rows) <= 64, "outbox_size")
    require(all(row["analysis_id"] == aid and row["capture_id"] == result["capture_id"] and
                isinstance(row["event_id"], str) and HEX.fullmatch(row["event_id"]) for row in rows), "outbox_identity")
    require(len({row["event_id"] for row in rows}) == len(rows), "outbox_duplicate_identity")
    return rows


def outbox_info(aid):
    require(isinstance(aid, str) and HEX.fullmatch(aid), "invalid_analysis_id")
    control = private_dir(INPUT / "control")
    run = control / aid
    if not run.exists() or not (private_dir(run) / "result.json").exists():
        return {"status": "pending", "analysis_id": aid}
    rows = stored_outbox(aid)
    result = read_json(run / "result.json")
    return {"status": "ready", "analysis_id": aid, "capture_id": result["capture_id"],
            "pcap_sha256": result["pcap_sha256"], "result_status": result["status"],
            "rows": rows, "delivered": (run / "delivered.json").exists()}


def security_context():
    return {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]}}


def job(config, cid, aid):
    capture_id(cid)
    require(HEX.fullmatch(aid), "invalid_analysis_id")
    uid = config["uid"]
    volumes = [{"name": "config", "configMap": {"name": config["config_map"], "defaultMode": 0o444}},
               {"name": "input", "persistentVolumeClaim": {"claimName": "security-lab-pcap-input", "readOnly": True}},
               {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}]
    for engine in OUTPUTS:
        volumes.append({"name": engine, "persistentVolumeClaim": {"claimName": "security-lab-" + engine + "-output"}})
    containers = []
    for engine in OUTPUTS:
        containers.append({
            "name": engine, "image": config["images"][engine], "imagePullPolicy": "IfNotPresent",
            "command": ["/usr/bin/python3", "/config/runtime.py", "engine", engine],
            "securityContext": security_context(),
            "resources": {"requests": {"cpu": "500m", "memory": "2Gi", "ephemeral-storage": "256Mi"},
                          "limits": {"cpu": "2", "memory": "2Gi", "ephemeral-storage": "1Gi"}},
            "volumeMounts": [{"name": "config", "mountPath": "/config", "readOnly": True},
                             {"name": "input", "mountPath": "/input", "subPath": "captures/" + cid, "readOnly": True},
                             {"name": engine, "mountPath": "/result", "subPath": aid},
                             {"name": "tmp", "mountPath": "/tmp"}]})
    final = dict(containers[-1], name="terminal", image=config["images"]["helper"],
                 command=["python3", "-c", "pass"],
                 resources={"requests": {"cpu": "50m", "memory": "128Mi", "ephemeral-storage": "128Mi"},
                            "limits": {"cpu": "100m", "memory": "128Mi", "ephemeral-storage": "128Mi"}},
                 volumeMounts=[{"name": "tmp", "mountPath": "/tmp"}])
    return {"apiVersion": "batch/v1", "kind": "Job",
            "metadata": {"name": "pcap-" + aid[:40], "namespace": "security-lab",
                         "labels": {"app.kubernetes.io/name": "pcap-analysis", "analysis-id": aid[:63]},
                         "annotations": {"security-lab.home.arpa/analysis-id": aid}},
            "spec": {"parallelism": 1, "completions": 1, "backoffLimit": 0, "activeDeadlineSeconds": 600,
                     "ttlSecondsAfterFinished": 3600,
                     "template": {"metadata": {"labels": {"app.kubernetes.io/name": "pcap-analysis",
                                                         "app.kubernetes.io/component": "offline-analyzer"}},
                                  "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
                                           "hostNetwork": False, "hostPID": False, "hostIPC": False,
                                           "preemptionPolicy": "Never",
                                           "priorityClassName": "security-lab-background",
                                           "nodeSelector": {"kubernetes.io/hostname": config["node"],
                                                            "security-lab.home.arpa/analysis": "true",
                                                            "kubernetes.io/arch": "arm64"},
                                           "securityContext": {"runAsNonRoot": True, "runAsUser": uid, "runAsGroup": uid,
                                                               "seccompProfile": {"type": "RuntimeDefault"}},
                                           "initContainers": containers, "containers": [final], "volumes": volumes}}}}


def engine_run(engine):
    require(engine in OUTPUTS, "invalid_engine")
    os.umask(0o077)
    out = Path("/result")
    private_dir(out)
    require(not any(out.iterdir()), "existing_engine_output_requires_reconciliation")
    pcap(INPUT / "capture.pcap")
    require(mem_available() >= GIB, "host_memory_reserve_low")
    config = CONFIG / "suricata.yaml"
    if engine == "suricata":
        rules = [line for line in (CONFIG / "local.rules").read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        require(rules, "empty_rules")
        commands = [["/usr/bin/suricata", "-V"],
                    ["/usr/bin/suricata", "-T", "-c", str(config), "-S", "/config/local.rules", "-l", str(out)],
                    ["/usr/bin/suricata", "--runmode", "single", "-c", str(config), "-S", "/config/local.rules",
                     "-r", "/input/capture.pcap", "-l", str(out)]]
    else:
        commands = [["/usr/local/zeek/bin/zeek", "--version"],
                    ["/usr/local/zeek/bin/zeek", "-r", "/input/capture.pcap", "LogAscii::use_json=T"]]
    started = time.time()
    result = {"schema": 1, "engine": engine, "status": "running", "started_at": started,
              "checksum_override": False, "exit_code": None}
    child = None
    parent_pid = os.getpid()

    def child_limits():
        os.setsid()
        libc = ctypes.CDLL(None, use_errno=True)
        require(libc.prctl(1, signal.SIGKILL) == 0, "parent_death_guard_failed")
        require(os.getppid() == parent_pid, "parent_already_exited")
        os.nice(10)
        resource.setrlimit(resource.RLIMIT_FSIZE, (GIB, GIB))

    try:
        for index, command in enumerate(commands):
            with (out / ("engine-" + str(index) + ".log")).open("xb") as log:
                child = subprocess.Popen(command, cwd=out, stdout=log, stderr=subprocess.STDOUT,
                                         preexec_fn=child_limits)
                while child.poll() is None:
                    require(time.time() - started < 480, "engine_timeout")
                    require(mem_available() >= GIB, "host_memory_reserve_low")
                    space(out)
                    time.sleep(1)
                require(child.returncode == 0, "engine_exit_" + str(child.returncode))
        version_match = re.search(r"version[^0-9]*([0-9]+\.[0-9]+\.[0-9]+)", (out / "engine-0.log").read_text(), re.I)
        require(version_match and version_match[1] == {"suricata": "8.0.8", "zeek": "9.0.1"}[engine],
                "engine_version_mismatch")
        result["actual_version"] = version_match[1]
        if engine == "suricata":
            count = alerts = decoded = 0
            with (out / "eve.json").open() as rows:
                for row in rows:
                    require(len(row) <= 1024 * 1024, "engine_record_too_large")
                    value = json.loads(row)
                    count += 1
                    alerts += value.get("event_type") == "alert"
                    if value.get("event_type") == "stats":
                        decoded = max(decoded, value.get("stats", {}).get("decoder", {}).get("pkts", 0))
            require(count > 0 and decoded > 0, "no_decoded_packets")
            result.update(events=count, alerts=alerts, decoded_packets=decoded)
        else:
            conn = out / "conn.log"
            require(conn.exists(), "no_connection_log")
            rows = conn.read_text().splitlines()
            require(rows, "empty_connection_log")
            for row in rows:
                json.loads(row)
            result.update(connections=len(rows))
        tree_bytes(out)
        files = sorted(p for p in out.rglob("*") if p.is_file())
        require(len(files) <= 128, "output_file_count_exceeded")
        outputs = []
        for path in files:
            info = regular(path)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
            outputs.append({"path": str(path.relative_to(out)), "bytes": info.st_size, "sha256": digest(path)})
        result.update(status="succeeded", exit_code=0, output_files=outputs,
                      config_sha256=digest(CONFIG / "suricata.yaml") if engine == "suricata" else None,
                      rules_sha256=digest(CONFIG / "local.rules") if engine == "suricata" else None,
                      enabled_sids=sorted(int(x) for x in re.findall(r"sid:([0-9]+);", (CONFIG / "local.rules").read_text())) if engine == "suricata" else [])
    except Exception as error:
        result.update(status="failed", reason=str(error)[:160],
                      exit_code=child.returncode if child and child.poll() is not None else None)
    finally:
        if child and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
        result.update(finished_at=time.time(), peak_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024)
        atomic(out / "engine.json", result)
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 0 if result["status"] == "succeeded" else 1


class API:
    def __init__(self):
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        host = "[" + host + "]" if ":" in host else host
        self.base = "https://" + host + ":" + os.environ["KUBERNETES_SERVICE_PORT_HTTPS"]
        self.context = ssl.create_default_context(cafile=CA)

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Authorization": "Bearer " + TOKEN.read_text().strip(),
                                                  "Content-Type": "application/json"})
        with urllib.request.urlopen(request, context=self.context, timeout=15) as response:
            return json.load(response)


JOBS = "/apis/batch/v1/namespaces/security-lab/jobs"
PODS = "/api/v1/namespaces/security-lab/pods"


def node_ready(node):
    require(node["status"]["nodeInfo"]["architecture"] == "arm64", "wrong_architecture")
    require(not node.get("spec", {}).get("unschedulable", False), "scheduling_suspended")
    require(not any(key in node["metadata"].get("labels", {}) for key in
                    ("node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master")), "control_plane")
    require(node["metadata"]["labels"].get("security-lab.home.arpa/analysis") == "true", "worker_not_allowed")
    require(not any(t["effect"] in ("NoSchedule", "NoExecute") for t in node.get("spec", {}).get("taints", [])), "worker_tainted")
    for kind, expected in (("Ready", "True"), ("MemoryPressure", "False"), ("DiskPressure", "False"), ("PIDPressure", "False")):
        values = [c["status"] for c in node["status"]["conditions"] if c["type"] == kind]
        require(values == [expected], "node_condition_" + kind)


def terminal(value):
    return next((c["type"] for c in value.get("status", {}).get("conditions", [])
                 if c["type"] in ("Complete", "Failed") and c["status"] == "True"), None)


def envelope(aid, cid, kind, data):
    row = {"schema": 1, "security_source": "pcap-analysis", "analysis_id": aid, "capture_id": cid,
           "event_type": kind, "environment": "staging", "analysis_time": time.time(), **data}
    row["event_id"] = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(len(json.dumps(row).encode()) <= 16384, "envelope_too_large")
    return row


def publish(run, event_ids=None):
    """Once per durable submission marker. Explicit replay preserves event IDs."""
    rows = read_json(run / "outbox.json", max_bytes=1024 * 1024)
    if event_ids is not None:
        wanted = set(event_ids)
        rows = [row for row in rows if row["event_id"] in wanted]
        require(len(rows) == len(wanted), "replay_event_not_found")
    for row in rows:
        print(json.dumps(row, separators=(",", ":")), flush=True)
    if event_ids is None and not (run / "submitted.json").exists():
        atomic(run / "submitted.json", {"at": time.time(), "events": [r["event_id"] for r in rows]})


def request_replay(aid, event_ids=None):
    rows = stored_outbox(aid)
    all_ids = [r["event_id"] for r in rows]
    if event_ids is None:
        ids = all_ids
    else:
        require(isinstance(event_ids, list) and 1 <= len(event_ids) <= len(all_ids) and
                all(isinstance(x, str) and HEX.fullmatch(x) for x in event_ids) and
                len(set(event_ids)) == len(event_ids) and set(event_ids) <= set(all_ids), "invalid_replay_events")
        ids = [x for x in all_ids if x in set(event_ids)]
    run = private_dir(INPUT / "control" / aid)
    require(not (run / "delivered.json").exists(), "already_delivered")
    request = run / "replay-request.json"
    if request.exists():
        saved = read_json(request)
        require(saved["event_ids"] == ids, "replay_request_changed")
    else:
        saved = {"schema": 1, "token": uuid.uuid4().hex, "event_ids": ids,
                 "requested_at": time.time()}
        atomic(request, saved)
    return {"status": "replay_queued", "analysis_id": aid, "token": saved["token"], "events": len(ids)}


REPLAY_WARNED = set()


def process_replays():
    """Only the coordinator's main stdout is a CRI log stream.

    kubectl exec stdout goes to the caller. The exec action writes a durable
    request, while this main-process publisher emits the saved envelopes.
    """
    control = private_dir(INPUT / "control")
    for run in sorted(control.iterdir()):
        if not HEX.fullmatch(run.name) or not run.is_dir():
            continue
        request = run / "replay-request.json"
        if not request.exists():
            continue
        try:
            private_dir(run)
            rows = stored_outbox(run.name)
            saved = read_json(request)
            require(re.fullmatch(r"[a-f0-9]{32}", saved["token"]) and
                    isinstance(saved["event_ids"], list) and 1 <= len(saved["event_ids"]) <= len(rows) and
                    all(isinstance(x, str) and HEX.fullmatch(x) for x in saved["event_ids"]) and
                    len(set(saved["event_ids"])) == len(saved["event_ids"]) and
                    set(saved["event_ids"]) <= {r["event_id"] for r in rows}, "replay_request_invalid")
            result_path = run / "replay-result.json"
            previous = read_json(result_path) if result_path.exists() else {}
            if previous.get("token") != saved["token"]:
                if not (run / "delivered.json").exists():
                    publish(run, saved["event_ids"])
                atomic(result_path, {"token": saved["token"], "at": time.time(),
                                     "status": "submitted", "events": saved["event_ids"]})
            request.unlink()
        except (ValueError, OSError, KeyError, TypeError) as error:
            if run.name not in REPLAY_WARNED:
                print(json.dumps({"schema": 1, "security_source": "pcap-analysis", "event_type": "coordinator_blocked",
                                  "reason": "replay_unavailable:" + type(error).__name__, "at": time.time()}), flush=True)
                REPLAY_WARNED.add(run.name)


def engine_summary(result):
    keys = ("status", "reason", "exit_code", "actual_version", "events", "alerts", "decoded_packets",
            "connections", "peak_child_rss_bytes", "config_sha256", "rules_sha256", "enabled_sids")
    value = {k: result[k] for k in keys if k in result}
    outputs = result.get("output_files", [])
    value["output_file_count"] = len(outputs)
    value["output_bytes"] = sum(row["bytes"] for row in outputs)
    terminated = result.get("kubernetes_state", {}).get("terminated", {}) if result.get("kubernetes_state") else {}
    if terminated:
        value["kubernetes_termination"] = {k: terminated[k] for k in ("reason", "exitCode", "signal") if k in terminated}
    return value


def collect_result(api, config, owner, current):
    aid, cid = owner["analysis_id"], owner["capture_id"]
    run = private_dir(INPUT / "control" / aid)
    pod_values = api.call("GET", PODS + "?labelSelector=batch.kubernetes.io%2Fjob-name%3D" + owner["job_name"])["items"]
    require(all(any(x.get("uid") == owner["job_uid"] for x in p["metadata"].get("ownerReferences", []))
                for p in pod_values), "job_pod_owner_mismatch")
    container_statuses = [s for p in pod_values for s in
                          p.get("status", {}).get("initContainerStatuses", []) + p.get("status", {}).get("containerStatuses", [])]
    states = {s["name"]: s.get("state", {}) for s in container_statuses}
    results = {}
    for engine, root in OUTPUTS.items():
        result_path = root / aid / "engine.json"
        results[engine] = read_json(result_path) if result_path.exists() else {"status": "missing", "kubernetes_state": states.get(engine)}
    success = terminal(current) == "Complete" and all(r.get("status") == "succeeded" for r in results.values())
    result = dict(owner, status="succeeded" if success else "failed", finished_at=time.time(),
                  engines=results, job_status=current.get("status", {}), delivery_status="pending")
    rows = []
    eve = OUTPUTS["suricata"] / aid / "eve.json"
    if eve.exists() and results["suricata"].get("status") == "succeeded":
        with eve.open() as source:
            for line in source:
                require(len(line) <= 1024 * 1024, "engine_record_too_large")
                value = json.loads(line)
                if value.get("event_type") != "alert":
                    continue
                alert = value["alert"]
                rows.append(envelope(aid, cid, "detection_candidate", {
                    "engine": "suricata", "event_time": value.get("timestamp"),
                    "sid": alert.get("signature_id"), "severity": alert.get("severity"),
                    "signature": str(alert.get("signature", ""))[:256],
                    "validation_fixture": alert.get("signature_id") == 1000001,
                    "src_ip": value.get("src_ip"), "dest_ip": value.get("dest_ip"),
                    "src_port": value.get("src_port"), "dest_port": value.get("dest_port")}))
                if len(rows) >= 32:
                    break
    rows.append(envelope(aid, cid, "analysis_summary", {"status": result["status"],
                                                     "engines": {e: engine_summary(r) for e, r in results.items()},
                                                     "images": config["images"], "config_sha256": config["asset_sha256"],
                                                     "candidate_records_published": len(rows)}))
    if not (run / "result.json").exists():
        atomic(run / "result.json", result)
    if not (run / "outbox.json").exists():
        atomic(run / "outbox.json", rows)
    if not (run / "submitted.json").exists():
        publish(run)
    return result


def execute(api, config, owner, owner_path):
    require(owner["cluster_uid"] == config["cluster_uid"], "previous_cluster_owner_requires_reconciliation")
    require(owner["config_sha256"] == config["asset_sha256"], "owner_configuration_changed")
    try:
        current = api.call("GET", JOBS + "/" + owner["job_name"])
    except urllib.error.HTTPError as error:
        error.close()
        if error.code != 404 or not owner.get("stop_reason"):
            raise
        current = None
    if owner.get("stop_reason"):
        if current:
            require(current["metadata"]["uid"] == owner["job_uid"], "job_uid_changed")
            api.call("DELETE", JOBS + "/" + owner["job_name"],
                     {"apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Foreground",
                      "preconditions": {"uid": owner["job_uid"]}})
        remaining = api.call("GET", PODS)["items"]
        require(not any(any(x.get("uid") == owner["job_uid"] for x in p["metadata"].get("ownerReferences", []))
                        for p in remaining), "stopping_owned_analysis")
        collect_result(api, config, owner, {"status": {"conditions": [{"type": "Failed", "status": "True",
                                                                     "reason": owner["stop_reason"]}]}})
        owner_path.unlink()
        return
    require(current["metadata"]["annotations"]["security-lab.home.arpa/analysis-id"] == owner["analysis_id"],
            "job_identity_mismatch")
    if owner.get("job_uid"):
        require(owner["job_uid"] == current["metadata"]["uid"], "job_uid_changed")
    else:
        owner["job_uid"] = current["metadata"]["uid"]
        atomic(owner_path, owner)
    while not terminal(current):
        process_replays()
        try:
            require(mem_available() >= GIB, "host_memory_reserve_low")
            node_ready(api.call("GET", "/api/v1/nodes/" + config["node"]))
            for root in OUTPUTS.values():
                space(root)
            require(time.time() - owner["started_at"] <= 660, "analysis_deadline")
        except ValueError as error:
            owner["stop_reason"] = str(error)
            atomic(owner_path, owner)
            api.call("DELETE", JOBS + "/" + owner["job_name"],
                     {"apiVersion": "v1", "kind": "DeleteOptions", "propagationPolicy": "Foreground",
                      "preconditions": {"uid": owner["job_uid"]}})
            # Never clear ownership until the matching Pods really disappeared.
            raise
        time.sleep(3)
        current = api.call("GET", JOBS + "/" + owner["job_name"])
        require(current["metadata"]["uid"] == owner["job_uid"], "job_uid_changed")
    collect_result(api, config, owner, current)
    owner_path.unlink()
    fd = os.open(owner_path.parent, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(fd)
    os.close(fd)


def coordinate():
    os.umask(0o077)
    config, api = settings(), API()
    for root in (INPUT, *OUTPUTS.values()):
        nfs_mount(root)
    control = private_dir(INPUT / "control", create=True)
    lock = os.open(control / "global.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    owner_path = control / "owner.json"
    last_error = None
    while True:
        try:
            process_replays()
            if owner_path.exists():
                execute(api, config, read_json(owner_path), owner_path)
            captures = private_dir(INPUT / "captures", create=True)
            for directory in sorted(captures.iterdir()):
                if directory.name.startswith("."):
                    continue
                capture_id(directory.name)
                private_dir(directory)
                if not (directory / "manifest.json").exists():
                    continue  # incomplete intake is retained, never analyzed
                manifest = read_json(directory / "manifest.json")
                require(manifest["capture_id"] == directory.name, "capture_identity_mismatch")
                require((directory / "request.json").exists(), "capture_requires_explicit_queue_request")
                request = read_json(directory / "request.json")
                aid = request["analysis_id"]
                require(isinstance(aid, str) and HEX.fullmatch(aid), "invalid_queued_analysis_id")
                run = control / aid
                if run.exists():
                    require((run / "result.json").exists(), "incomplete_analysis_requires_reconciliation")
                    continue  # failed/successful attempts are never automatically rerun
                require(request["config_sha256"] == config["asset_sha256"] and aid == analysis_id(manifest, config),
                        "queued_configuration_changed_requires_reconciliation")
                require(mem_available() >= 3 * GIB, "host_memory_reserve_low")
                node_ready(api.call("GET", "/api/v1/nodes/" + config["node"]))
                require(len(api.call("GET", JOBS)["items"]) < 8 and len(api.call("GET", PODS)["items"]) < 8,
                        "namespace_job_or_pod_budget")
                pcap(directory / "capture.pcap")
                require(digest(directory / "capture.pcap") == manifest["pcap_sha256"], "input_hash_changed")
                for root in (INPUT, *OUTPUTS.values()):
                    space(root)
                for root in OUTPUTS.values():
                    target = root / aid
                    require(not target.exists() or not any(private_dir(target).iterdir()),
                            "existing_engine_output_requires_reconciliation")
                for root in OUTPUTS.values():
                    private_dir(root / aid, create=True)
                private_dir(run, create=True)
                spec = job(config, directory.name, aid)
                owner = {"schema": 1, "analysis_id": aid, "capture_id": directory.name,
                         "cluster_uid": config["cluster_uid"], "job_name": spec["metadata"]["name"],
                         "config_sha256": config["asset_sha256"],
                         "started_at": time.time(), "pcap_sha256": manifest["pcap_sha256"], "job_uid": None}
                atomic(run / "queued.json", owner)
                atomic(owner_path, owner)
                # Failure/ambiguous POST leaves the owner record. Recovery uses GET, never blind POST retry.
                api.call("POST", JOBS, spec)
                execute(api, config, owner, owner_path)
            last_error = None
        except Exception as error:
            reason = type(error).__name__ + ":" + str(error)[:200]
            if reason != last_error:
                print(json.dumps({"schema": 1, "security_source": "pcap-analysis", "event_type": "coordinator_blocked",
                                  "reason": reason, "at": time.time()}), flush=True)
                last_error = reason
        time.sleep(10)


def probe():
    """Read metadata and test only this tool's own per-PVC private directory."""
    result = {"schema": 1, "uid": os.getuid(), "gid": os.getgid(), "mem_available": mem_available(), "volumes": {}}
    for name, root in {"input": INPUT, **OUTPUTS}.items():
        before = root.stat()
        kind = nfs_mount(root)
        folder = root / ("permission-probe-" + os.urandom(8).hex())
        folder.mkdir(mode=0o700, exist_ok=False)
        atomic(folder / "probe.json", {"probe": True})
        require(read_json(folder / "probe.json") == {"probe": True}, "probe_readback_failed")
        handle = (folder / "lock").open("x+")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_code = ("import fcntl,sys; f=open(sys.argv[1],'r+');\n"
                     "try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
                     "except BlockingIOError: sys.exit(73)\n")
        blocked = subprocess.run([sys.executable, "-c", lock_code, str(folder / "lock")],
                                 capture_output=True, timeout=5)
        require(blocked.returncode == 73, "nfs_lock_exclusion_failed")
        handle.close()
        released = subprocess.run([sys.executable, "-c", lock_code, str(folder / "lock")],
                                  capture_output=True, timeout=5)
        require(released.returncode == 0, "nfs_lock_release_failed")
        result["volumes"][name] = {"root_uid": before.st_uid, "root_gid": before.st_gid,
                                  "root_mode": oct(stat.S_IMODE(before.st_mode)), "private_create_fsync_lock": True,
                                  "filesystem": kind, "created_uid": folder.stat().st_uid,
                                  "independent_process_lock_exclusion": True,
                                  "created_gid": folder.stat().st_gid,
                                  "owner_matches": folder.stat().st_uid == os.getuid(),
                                  "free_bytes": os.statvfs(root).f_bavail * os.statvfs(root).f_frsize}
        (folder / "probe.json").unlink()
        (folder / "lock").unlink()
        folder.rmdir()
    print(json.dumps(result), flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("engine", "coordinate", "intake", "verify-input", "read-outbox", "outbox-status",
                                          "probe", "replay", "replay-missing", "delivery-ack"))
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    if args.action == "engine":
        return engine_run(args.value)
    if args.action == "coordinate":
        coordinate()
    elif args.action == "intake":
        require(args.value and len(args.value) < 32768, "missing_manifest")
        print(json.dumps(intake(json.loads(args.value), sys.stdin.buffer)), flush=True)
    elif args.action == "probe":
        probe()
    elif args.action == "verify-input":
        print(json.dumps(verify_input(args.value)), flush=True)
    elif args.action == "read-outbox":
        print(json.dumps(stored_outbox(args.value)), flush=True)
    elif args.action == "outbox-status":
        print(json.dumps(outbox_info(args.value)), flush=True)
    elif args.action == "replay-missing":
        require(args.value and len(args.value) < 8192, "missing_replay_request")
        value = json.loads(args.value)
        require(set(value) == {"analysis_id", "event_ids"}, "invalid_replay_request")
        print(json.dumps(request_replay(value["analysis_id"], value["event_ids"])), flush=True)
    else:
        require(args.value and HEX.fullmatch(args.value), "invalid_analysis_id")
        run = private_dir(INPUT / "control" / args.value)
        require((run / "result.json").exists(), "result_not_durable")
        if args.action == "replay":
            print(json.dumps(request_replay(args.value)), flush=True)
        else:
            rows = stored_outbox(args.value)
            target = run / "delivered.json"
            ids = [r["event_id"] for r in rows]
            duplicate = target.exists()
            if duplicate:
                require(read_json(target)["events"] == ids, "delivery_receipt_changed")
            else:
                atomic(target, {"at": time.time(), "events": ids})
            print(json.dumps({"status": "delivered", "analysis_id": args.value,
                              "events": len(ids), "already_acknowledged": duplicate}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
