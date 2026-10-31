#!/usr/bin/env python3
"""Operator-side bounded transfer and delivery receipts. No PCAP/key is saved here."""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import stat
import subprocess
import sys
import time
import uuid

MAX_PCAP = 65_551_024
HEX = re.compile(r"^[a-f0-9]{64}$")
CAPTURE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{16}$")
TARGET = re.compile(r"^[a-z_][a-z0-9_-]*@[a-zA-Z0-9][a-zA-Z0-9_.:-]*$")
NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,252}$")
HELPER = "python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8"
KUBE = ["sudo", "kubectl", "--kubeconfig=/etc/rancher/k3s/k3s.yaml"]
SSH = ["ssh", "-T", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes", "-oForwardAgent=no",
       "-oClearAllForwardings=yes", "-oControlMaster=no", "-oControlPath=none"]

LOKI_QUERY = r"""
import ipaddress,json,re,subprocess,sys,time,urllib.parse,urllib.request
p=json.load(sys.stdin)
assert re.fullmatch('[a-f0-9]{64}',p['analysis_id'])
k=['kubectl','--kubeconfig=/etc/rancher/k3s/k3s.yaml']
def get(*a):return json.loads(subprocess.check_output(k+['get',*a,'-o','json'],text=True))
assert get('namespace','kube-system')['metadata']['uid']==p['cluster_uid']
service=get('service','prod-loki','-n','observability-prod')
host=str(ipaddress.ip_address(service['spec']['clusterIP']))
if ':' in host:host='['+host+']'
query='{service_name="pcap-analysis"} | json | analysis_id='+json.dumps(p['analysis_id'])
args=urllib.parse.urlencode({'query':query,'start':str(p['start_ns']),'end':str(time.time_ns()+60*10**9),'limit':5000})
with urllib.request.urlopen('http://'+host+':3100/loki/api/v1/query_range?'+args,timeout=15) as r:d=json.load(r)
assert d['status']=='success' and d['data']['resultType']=='streams'
counts={}
for stream in d['data']['result']:
 for entry in stream.get('values',[]):
  try:row=json.loads(entry[1])
  except (ValueError,TypeError):continue
  if row.get('analysis_id')==p['analysis_id'] and row.get('capture_id')==p['capture_id'] and isinstance(row.get('event_id'),str) and re.fullmatch('[a-f0-9]{64}',row['event_id']):
   counts[row['event_id']]=counts.get(row['event_id'],0)+1
print(json.dumps({'status':'ok','event_counts':counts}))
"""


class TransferError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise TransferError(reason)


def private_file(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid() and
            stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 1024 * 1024, "private_file_contract")


def private_parent(path):
    parent = path.parent
    info = parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
            stat.S_IMODE(info.st_mode) == 0o700, "receipt_parent_contract")


def save_receipt(path, value):
    private_parent(path)
    if path.exists():
        private_file(path)
    part = path.with_name(path.name + ".part")
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(part, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Runner:
    """Bound stdout/stderr while sending binary stdin, including blocked peers."""
    def run(self, args, data=None, limit=1024 * 1024, timeout=150):
        require(0 < limit <= MAX_PCAP + 65536 and 0 < timeout <= 300, "transport_budget")
        process = subprocess.Popen(args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        selector = selectors.DefaultSelector()
        output, errors = bytearray(), bytearray()
        position = 0
        payload = memoryview(data or b"")
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        if data is not None:
            if payload:
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                require(time.monotonic() < deadline, "transport_timeout")
                for key, _ in selector.select(.25):
                    if key.data == "stdin":
                        try:
                            position += os.write(key.fd, payload[position:position + 65536])
                        except BrokenPipeError:
                            position = len(payload)
                        if position == len(payload):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    else:
                        target = output if key.data == "stdout" else errors
                        target.extend(block)
                        require(len(target) <= (limit if key.data == "stdout" else 65536), "transport_output_limit")
            try:
                code = process.wait(timeout=max(.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                raise TransferError("transport_timeout") from error
            require(code == 0, "ssh_command_failed:rc=" + str(code))
            return bytes(output)
        finally:
            selector.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
            if process.poll() is None:
                process.kill()  # Only this invocation's transport process.
                process.wait(timeout=5)


def json_response(data):
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise TransferError("transport_json_invalid") from error


class Client:
    def __init__(self, server, cluster_uid, namespace_uid, worker, uid=10001, runner=None):
        require(TARGET.fullmatch(server), "invalid_server")
        for value in (cluster_uid, namespace_uid):
            try:
                require(str(uuid.UUID(value)) == value, "invalid_namespace_uid")
            except (ValueError, AttributeError) as error:
                raise TransferError("invalid_namespace_uid") from error
        require(NAME.fullmatch(worker) and 1000 <= uid <= 65534, "invalid_worker")
        self.server, self.cluster_uid, self.namespace_uid, self.worker, self.uid = server, cluster_uid, namespace_uid, worker, uid
        self.runner = runner or Runner()
        self.deadline = time.monotonic() + 300

    def transport(self, args, data=None, limit=1024 * 1024):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "action_deadline")
        return self.runner.run(args, data=data, limit=limit, timeout=min(150, remaining))

    def ssh(self, args, data=None, limit=1024 * 1024):
        return self.transport(SSH + [self.server, shlex.join(args)], data=data, limit=limit)

    def kube(self, args):
        return json_response(self.ssh(KUBE + args))

    def context(self):
        cluster = self.kube(["get", "namespace", "kube-system", "-o", "json"])
        require(cluster["metadata"]["uid"] == self.cluster_uid, "cluster_uid_mismatch")
        namespace = self.kube(["get", "namespace", "security-lab", "-o", "json"])
        require(namespace["metadata"]["uid"] == self.namespace_uid and
                namespace["metadata"]["labels"].get("pod-security.kubernetes.io/enforce") == "restricted",
                "namespace_contract")
        node = self.kube(["get", "node", self.worker, "-o", "json"])
        require(node["metadata"]["name"] == self.worker and
                node["metadata"]["labels"].get("security-lab.home.arpa/analysis") == "true" and
                node["status"]["nodeInfo"]["architecture"] == "arm64" and
                not node.get("spec", {}).get("unschedulable", False), "worker_contract")
        require(not any(k in node["metadata"]["labels"] for k in
                        ("node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master")), "control_plane")
        require(not any(t["effect"] in ("NoSchedule", "NoExecute") for t in node.get("spec", {}).get("taints", [])),
                "worker_tainted")
        for kind, expected in (("Ready", "True"), ("MemoryPressure", "False"), ("DiskPressure", "False"), ("PIDPressure", "False")):
            require([x["status"] for x in node["status"]["conditions"] if x["type"] == kind] == [expected],
                    "worker_condition_" + kind)
        pods = self.kube(["get", "pods", "-n", "security-lab", "-l",
                          "app.kubernetes.io/component=security-coordinator", "-o", "json"])["items"]
        require(len(pods) == 1, "coordinator_count")
        pod = pods[0]
        require(not pod["metadata"].get("deletionTimestamp") and pod["status"]["phase"] == "Running" and
                [c["status"] for c in pod["status"]["conditions"] if c["type"] == "Ready"] == ["True"],
                "coordinator_not_ready")
        require(pod["spec"]["nodeName"] == self.worker and
                pod["spec"]["securityContext"]["runAsUser"] == self.uid and
                pod["spec"]["serviceAccountName"] == "security-lab-launcher", "coordinator_identity")
        containers = pod["spec"]["containers"]
        require(len(containers) == 1 and containers[0]["name"] == "coordinator" and containers[0]["image"] == HELPER and
                containers[0]["command"] == ["python3", "/config/runtime.py", "coordinate"], "coordinator_command")
        return {"pod": pod["metadata"]["name"], "pod_uid": pod["metadata"]["uid"]}

    def runtime(self, context, action, value=None, data=None):
        current = self.kube(["get", "pod", context["pod"], "-n", "security-lab", "-o", "json"])
        require(current["metadata"]["uid"] == context["pod_uid"], "coordinator_replaced")
        command = KUBE + ["exec", "-n", "security-lab", context["pod"], "-c", "coordinator"]
        if data is not None:
            command.append("-i")
        command += ["--", "python3", "/config/runtime.py", action]
        if value is not None:
            command.append(value)
        return json_response(self.ssh(command, data=data))

    def bind_receipt(self, receipt):
        require(type(receipt["schema"]) is int and receipt["schema"] == 1 and receipt["server"] == self.server and
                receipt["cluster_uid"] == self.cluster_uid and receipt["namespace_uid"] == self.namespace_uid and
                receipt["worker"] == self.worker and CAPTURE.fullmatch(receipt["capture_id"]) and
                HEX.fullmatch(receipt["pcap_sha256"]), "receipt_identity")

    def verify_receipt_input(self, context, receipt):
        self.bind_receipt(receipt)
        result = self.runtime(context, "verify-input", receipt["capture_id"])
        require(result["status"] == "durable" and result["capture_id"] == receipt["capture_id"] and
                result["sha256"] == receipt["pcap_sha256"] and result["bytes"] == receipt["size_bytes"] and
                HEX.fullmatch(result["analysis_id"]), "nas_receipt_mismatch")
        return result

    def acknowledge_router(self, context, receipt, path):
        verified = self.verify_receipt_input(context, receipt)
        require(verified["analysis_id"] == receipt["analysis_id"], "analysis_identity_changed")
        require(TARGET.fullmatch(receipt["router"]) and receipt["router"].startswith("root@"), "invalid_router")
        receipt["router_ack"] = "started"
        save_receipt(path, receipt)
        raw = self.transport(SSH + [receipt["router"], shlex.join([
            "/usr/libexec/homecluster-security-capture.py", "ack", receipt["capture_id"], receipt["pcap_sha256"]])],
            limit=65536)
        ack = json_response(raw)
        require(ack["status"] == "acknowledged" and ack["capture_id"] == receipt["capture_id"] and
                ack["pcap_sha256"] == receipt["pcap_sha256"], "router_ack_mismatch")
        receipt["router_ack"] = "acknowledged"
        save_receipt(path, receipt)
        return ack

    def transfer(self, cid, router, key, path, acknowledge=False):
        require(CAPTURE.fullmatch(cid) and TARGET.fullmatch(router) and router.startswith("root@"),
                "invalid_capture_or_router")
        private_file(key)
        private_parent(path)
        context = self.context()
        if path.exists():
            private_file(path)
            receipt = json.loads(path.read_text())
            self.bind_receipt(receipt)
            require(receipt["capture_id"] == cid and receipt["router"] == router, "receipt_collision")
            if "analysis_id" not in receipt:
                verified = self.verify_receipt_input(context, receipt)
                receipt.update(analysis_id=verified["analysis_id"], status="input_durable")
                save_receipt(path, receipt)
            else:
                self.verify_receipt_input(context, receipt)
        else:
            reader = SSH + ["-F", "/dev/null", "-oIdentitiesOnly=yes", "-oIdentityAgent=none", "-i", str(key), router]
            manifest = json_response(self.transport(reader + ["manifest " + cid], limit=32768))
            require(type(manifest["schema"]) is int and manifest["schema"] == 1 and manifest["capture_id"] == cid and
                    HEX.fullmatch(manifest["pcap_sha256"]) and type(manifest["size_bytes"]) is int and
                    24 <= manifest["size_bytes"] <= MAX_PCAP, "capture_manifest_invalid")
            receipt = {"schema": 1, "status": "transfer_started", "server": self.server,
                       "cluster_uid": self.cluster_uid, "namespace_uid": self.namespace_uid, "worker": self.worker,
                       "router": router, "capture_id": cid, "pcap_sha256": manifest["pcap_sha256"],
                       "size_bytes": manifest["size_bytes"], "started_at": time.time(), "router_ack": "pending"}
            save_receipt(path, receipt)
            packet = self.transport(reader + ["pcap " + cid], limit=manifest["size_bytes"])
            require(len(packet) == manifest["size_bytes"] and hashlib.sha256(packet).hexdigest() == manifest["pcap_sha256"],
                    "router_transfer_hash_mismatch")
            durable = self.runtime(context, "intake", json.dumps(manifest, separators=(",", ":")), data=packet)
            require(durable["status"] == "durable" and durable["capture_id"] == cid and
                    durable["sha256"] == receipt["pcap_sha256"] and durable["bytes"] == receipt["size_bytes"] and
                    HEX.fullmatch(durable["analysis_id"]), "nas_receipt_mismatch")
            receipt.update(status="input_durable", analysis_id=durable["analysis_id"], durable_at=time.time())
            save_receipt(path, receipt)
        if acknowledge and receipt.get("router_ack") != "acknowledged":
            self.acknowledge_router(context, receipt, path)
        return {"status": receipt["status"], "capture_id": cid, "analysis_id": receipt["analysis_id"],
                "router_ack": receipt["router_ack"], "bytes": receipt["size_bytes"]}

    def loki_events(self, reader, cluster_uid, receipt):
        require(TARGET.fullmatch(reader), "invalid_loki_reader")
        try:
            require(str(uuid.UUID(cluster_uid)) == cluster_uid, "invalid_loki_cluster_uid")
        except (ValueError, AttributeError) as error:
            raise TransferError("invalid_loki_cluster_uid") from error
        now = time.time()
        require(type(receipt["started_at"]) in (int, float) and math.isfinite(receipt["started_at"]) and
                0 < receipt["started_at"] <= now + 300, "delivery_time_invalid")
        # Replayed saved records receive a new CRI delivery timestamp. An old
        # undelivered capture must remain recoverable after Loki retention.
        start = max(0, receipt["started_at"] - 300, now - (72 * 3600 - 300))
        query = {"cluster_uid": cluster_uid, "analysis_id": receipt["analysis_id"], "capture_id": receipt["capture_id"],
                 "start_ns": int(start * 10 ** 9)}
        response = json_response(self.transport(SSH + [reader, shlex.join(["sudo", "python3", "-c", LOKI_QUERY])],
                                                data=json.dumps(query).encode()))
        require(response["status"] == "ok" and isinstance(response["event_counts"], dict), "loki_query_invalid")
        require(all(isinstance(k, str) and HEX.fullmatch(k) and type(v) is int and 0 < v <= 5000
                    for k, v in response["event_counts"].items()), "loki_query_counts_invalid")
        return response["event_counts"]

    def check_delivery(self, path, reader, cluster_uid, wait_seconds=90, replay_missing=False):
        require(0 <= wait_seconds <= 120, "delivery_wait_budget")
        require(TARGET.fullmatch(reader), "invalid_loki_reader")
        try:
            require(str(uuid.UUID(cluster_uid)) == cluster_uid, "invalid_loki_cluster_uid")
        except (ValueError, AttributeError) as error:
            raise TransferError("invalid_loki_cluster_uid") from error
        private_file(path)
        receipt = json.loads(path.read_text())
        self.bind_receipt(receipt)
        context = self.context()
        deadline = time.monotonic() + wait_seconds
        replayed = False
        expected = None
        counts = {}
        while True:
            outbox = self.runtime(context, "outbox-status", receipt["analysis_id"])
            require(outbox["analysis_id"] == receipt["analysis_id"], "outbox_identity_changed")
            if outbox["status"] == "ready":
                require(outbox["capture_id"] == receipt["capture_id"] and
                        outbox["pcap_sha256"] == receipt["pcap_sha256"] and outbox["rows"], "outbox_identity_changed")
                ids = [row["event_id"] for row in outbox["rows"]]
                require(all(HEX.fullmatch(x) for x in ids) and len(set(ids)) == len(ids), "outbox_ids_invalid")
                require(all(row["analysis_id"] == receipt["analysis_id"] and
                            row["capture_id"] == receipt["capture_id"] for row in outbox["rows"]), "outbox_rows_invalid")
                expected = set(ids)
                proof = outbox.get("delivery")
                if proof is not None:
                    require(isinstance(proof, dict) and proof.get("events") == ids and
                            type(proof.get("at")) in (int, float) and math.isfinite(proof["at"]) and
                            0 < proof["at"] <= time.time() + 300, "historical_delivery_invalid")
                    target = proof.get("loki_cluster_uid")
                    verified = proof.get("confirmed_at", proof["at"])
                    if target is None and time.time() - proof["at"] >= 72 * 3600:
                        # A legacy NAS marker did not bind its destination.
                        # Recover only with the matching operator-side proof.
                        local = receipt.get("delivery", {})
                        if (not isinstance(local, dict) or local.get("status") != "delivered" or
                                local.get("event_ids") != sorted(expected) or not local.get("loki_cluster_uid")):
                            return {"status": "pending", "analysis_id": receipt["analysis_id"],
                                    "reason": "legacy_delivery_target_receipt_required", "replayed": False}
                        target, verified = local["loki_cluster_uid"], local.get("verified_at")
                    if target is not None:
                        require(target == cluster_uid, "delivery_target_changed")
                        require(type(verified) in (int, float) and math.isfinite(verified) and
                                0 < verified <= time.time() + 300, "historical_delivery_invalid")
                        receipt["delivery"] = {"status": "delivered", "event_ids": sorted(expected),
                                               "verified_at": verified, "loki_cluster_uid": cluster_uid}
                        save_receipt(path, receipt)
                        return {"status": "delivered", "analysis_id": receipt["analysis_id"],
                                "events": len(expected), "duplicate_records": None,
                                "analysis_status": outbox["result_status"], "replayed": False,
                                "confirmation": "historical_receipt", "loki_availability": "not_checked"}
                counts = self.loki_events(reader, cluster_uid, receipt)
                missing = expected - counts.keys()
                if not missing:
                    evidence = {"analysis_id": receipt["analysis_id"], "event_ids": sorted(expected),
                                "loki_cluster_uid": cluster_uid}
                    ack = self.runtime(context, "delivery-ack", json.dumps(evidence))
                    require(ack["status"] == "delivered" and ack["analysis_id"] == receipt["analysis_id"] and
                            ack["events"] == len(expected), "delivery_ack_mismatch")
                    receipt["delivery"] = {"status": "delivered", "event_ids": sorted(expected),
                                           "verified_at": time.time(), "loki_cluster_uid": cluster_uid}
                    save_receipt(path, receipt)
                    return {"status": "delivered", "analysis_id": receipt["analysis_id"],
                            "events": len(expected), "duplicate_records": sum(max(0, counts[x] - 1) for x in expected),
                            "analysis_status": outbox["result_status"], "replayed": replayed}
            else:
                require(outbox["status"] == "pending", "outbox_status_unknown")
            if time.monotonic() >= deadline:
                if replay_missing and expected and not replayed:
                    queued = self.runtime(context, "replay-missing", json.dumps({
                        "analysis_id": receipt["analysis_id"], "event_ids": sorted(expected - counts.keys())}))
                    require(queued["status"] == "replay_queued", "replay_not_queued")
                    replayed = True
                    deadline = time.monotonic() + min(90, max(1, wait_seconds))
                else:
                    return {"status": "pending", "analysis_id": receipt["analysis_id"], "replayed": replayed,
                            "missing_events": len(expected - counts.keys()) if expected else None}
            time.sleep(2)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("transfer", "ack-router", "replay", "check-delivery"))
    parser.add_argument("--server", required=True)
    parser.add_argument("--cluster-uid", required=True)
    parser.add_argument("--namespace-uid", required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--uid", type=int, default=10001)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--capture-id")
    parser.add_argument("--router")
    parser.add_argument("--reader-key", type=Path)
    parser.add_argument("--ack-router", action="store_true")
    parser.add_argument("--loki-reader")
    parser.add_argument("--loki-cluster-uid")
    parser.add_argument("--wait-seconds", type=int, default=90)
    parser.add_argument("--replay-missing", action="store_true")
    args = parser.parse_args(argv)
    private_parent(args.receipt)
    lock_path = args.receipt.with_name(args.receipt.name + ".lock")
    lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        private_file(lock_path)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = Client(args.server, args.cluster_uid, args.namespace_uid, args.worker, args.uid)
        if args.action == "transfer":
            require(args.capture_id and args.router and args.reader_key, "missing_transfer_arguments")
            result = client.transfer(args.capture_id, args.router, args.reader_key, args.receipt, args.ack_router)
        else:
            private_file(args.receipt)
            receipt = json.loads(args.receipt.read_text())
            client.bind_receipt(receipt)
            context = client.context()
            if args.action == "check-delivery":
                require(args.loki_reader and args.loki_cluster_uid, "missing_loki_binding")
                result = client.check_delivery(args.receipt, args.loki_reader, args.loki_cluster_uid,
                                               args.wait_seconds, args.replay_missing)
            elif args.action == "ack-router":
                client.acknowledge_router(context, receipt, args.receipt)
                result = {"status": "acknowledged", "capture_id": receipt["capture_id"]}
            else:
                result = client.runtime(context, "replay", receipt["analysis_id"])
                require(result["status"] == "replay_queued", "replay_not_queued")
        print(json.dumps(result, sort_keys=True))
    finally:
        os.close(lock)


if __name__ == "__main__":
    try:
        main()
    except (TransferError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        # Fixed reason only; no PCAP, private key, token, or remote stderr output.
        print(json.dumps({"status": "failed", "reason": str(error) if isinstance(error, TransferError) else type(error).__name__}))
        sys.exit(1)
