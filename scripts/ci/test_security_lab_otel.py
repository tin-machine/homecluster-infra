#!/usr/bin/env python3
"""Actual Collector binary: short logs, parser, persistent offsets/queue, rotation.

Only this fixture's internal network, containers and named volume are created.
No site input, credential, host log directory or externally published port is used.
"""
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELPER = "python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8"


def docker(*args, check=True, input=None):
    return subprocess.run(["docker", *args], text=True, capture_output=True, input=input,
                          timeout=150, check=check)


def merge(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        value = copy.deepcopy(left)
        for key, item in right.items():
            value[key] = merge(value.get(key), item)
        return value
    return copy.deepcopy(right)


SINK = r"""
import gzip,json,http.server
state={"accept":True,"rows":[],"attempts":0,"rejected":0}
def attrs(values):return {x["key"]:next(iter(x["value"].values())) for x in values}
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def answer(self,code,value):
  data=json.dumps(value).encode();self.send_response(code);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
 def do_GET(self):self.answer(200,state)
 def do_POST(self):
  size=int(self.headers.get("Content-Length","0"));assert size<=8*1024*1024
  data=self.rfile.read(size)
  if self.path in ("/accept","/reject"):
   state["accept"]=self.path=="/accept";self.answer(200,{});return
  assert self.path=="/v1/logs",self.path
  state["attempts"]+=1
  if not state["accept"]:
   state["rejected"]+=1;self.answer(503,{});return
  if self.headers.get("Content-Encoding")=="gzip":data=gzip.decompress(data)
  payload=json.loads(data)
  for resource in payload.get("resourceLogs",[]):
   for scope in resource.get("scopeLogs",[]):
    for row in scope.get("logRecords",[]):
     body=row.get("body",{}).get("stringValue","")
     state["rows"].append({"body":body,"attributes":attrs(row.get("attributes",[])),"resource":attrs(resource.get("resource",{}).get("attributes",[])),"timeUnixNano":row.get("timeUnixNano")})
  self.answer(200,{})
http.server.HTTPServer(("0.0.0.0",4318),Handler).serve_forever()
"""


def record(case, **extra):
    return {"schema": 1, "security_source": "pcap-analysis", "event_type": "analysis_summary",
            "analysis_id": "a" * 64, "capture_id": "20261031T000000Z-0123456789abcdef",
            "event_id": hashlib.sha256(case.encode()).hexdigest(), "status": "succeeded",
            "analysis_time": time.time(), "event_time": "2020-01-01T00:00:00Z",
            "engines": {"suricata": {"status": "succeeded"}, "zeek": {"status": "succeeded"}},
            "fixture_case": case, **extra}


def append(path, values):
    with path.open("a") as stream:
        for value in values:
            body = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
            now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
            stream.write(now + " stdout F " + body + "\n")
        stream.flush()


def main():
    staging = ROOT / "clusters/homelab/apps/staging"
    base = yaml.safe_load((ROOT / "clusters/homelab/apps/base/values-otel-collector.yaml").read_text())
    env = yaml.safe_load((staging / "values-otel-collector.yaml").read_text())
    o4 = yaml.safe_load((staging / "values-otel-collector-o4-prod-logs.yaml").read_text())
    router = yaml.safe_load((staging / "values-otel-collector-openwrt-security.yaml").read_text())
    delivery = yaml.safe_load((staging / "values-otel-collector-security-lab.yaml").read_text())
    active = merge(merge(merge(env, o4), router), delivery)["config"]
    previous = merge(merge(env, o4), router)["config"]
    assert active["service"]["pipelines"]["logs/openwrt"] == previous["service"]["pipelines"]["logs/openwrt"]
    assert active["service"]["pipelines"]["logs"] == previous["service"]["pipelines"]["logs"]
    assert active["receivers"]["filelog/pods"]["start_at"] == "end"
    assert active["receivers"]["filelog/pods"]["exclude"] == ["/var/log/pods/security-lab_*/*/*.log"]
    assert delivery["extraVolumes"][:-1] == o4["extraVolumes"]
    assert delivery["extraVolumeMounts"][:-1] == o4["extraVolumeMounts"]
    image = base["image"]["repository"] + ":" + str(base["image"]["tag"])
    token = "security-log-fixture-" + uuid.uuid4().hex[:12]
    network, collector, sink, volume = (token + x for x in ("-net", "-collector", "-sink", "-state"))
    created = []
    try:
        for candidate in (image, HELPER):
            if docker("image", "inspect", candidate, check=False).returncode:
                docker("pull", candidate)
        docker("network", "create", "--internal", network)
        created.append("network")
        docker("volume", "create", volume)
        created.append("volume")
        # Exercise the actual directory initializer, including restrictive ownership.
        init = delivery["initContainers"][0]
        assert init["securityContext"]["capabilities"] == {"drop": ["ALL"], "add": ["CHOWN", "DAC_OVERRIDE"]}
        init_args = ["run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                     "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--security-opt", "no-new-privileges",
                     "--user", "0:0", "--memory", "64m", "--cpus", "0.25",
                     "--mount", "type=volume,src=" + volume + ",dst=/offsets", HELPER, *init["command"]]
        fs_probe = docker("run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                          "--mount", "type=volume,src=" + volume + ",dst=/offsets", HELPER, "python3", "-c",
                          "import pathlib;print(next(r.split(' - ',1)[1].split()[0] for r in pathlib.Path('/proc/self/mountinfo').read_text().splitlines() if r.split(' - ',1)[0].split()[4]=='/offsets'))")
        fixture_filesystem = fs_probe.stdout.strip()
        if fixture_filesystem not in ("ext4", "xfs"):
            # A test daemon may use another filesystem (including overlay or
            # btrfs). Keep the production ext4/xfs gate. Map mountinfo only in
            # this isolated permission/idempotency test after testing rejection.
            rejected = docker(*init_args, check=False)
            assert rejected.returncode and "state directory must use persistent block storage" in rejected.stderr
            source = init_args[-1]
            wrapper = (
                "import pathlib,unittest.mock\n"
                "original=pathlib.Path.read_text\n"
                "def mapped(self,*a,**k):\n"
                " if str(self)=='/proc/self/mountinfo':return '1 0 0:1 / /offsets rw - ext4 /dev/fixture rw\\n'\n"
                " return original(self,*a,**k)\n"
                "with unittest.mock.patch.object(pathlib.Path,'read_text',mapped):\n"
                " exec(" + repr(source) + ")\n")
            init_args[-1] = wrapper
        docker(*init_args)
        docker(*init_args)  # Existing marker must be idempotent, not recursively chowned.
        docker("run", "-d", "--name", sink, "--network", network, "--network-alias", "security-fixture-sink",
               "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "10001:10001",
               "--memory", "128m", "--cpus", "0.5", HELPER, "python3", "-c", SINK)

        def state(action=""):
            code = ("import urllib.request,json; u='http://127.0.0.1:4318/" + action +
                    "';r=urllib.request.urlopen(urllib.request.Request(u,data=b'' if " + repr(bool(action)) +
                    " else None),timeout=5);print(r.read().decode())")
            return json.loads(docker("exec", sink, "python3", "-c", code).stdout)

        with tempfile.TemporaryDirectory(prefix=token, dir=os.environ.get("HOMECLUSTER_TEST_SHARED_DIR")) as directory:
            root = Path(directory)
            root.chmod(0o755)
            logs = root / "logs"
            target = logs / "security-lab_fixture_00000000-0000-0000-0000-000000000001" / "app" / "0.log"
            ordinary = logs / "fixture-default_ordinary_00000000-0000-0000-0000-000000000002" / "app" / "0.log"
            for path in (target, ordinary):
                path.parent.mkdir(parents=True, mode=0o755)
            append(target, [record("short-preexisting")])
            append(ordinary, [{"fixture_case": "ordinary-preexisting"}])
            config = {"receivers": {name: active["receivers"][name] for name in ("filelog/pods", "filelog/security-lab")},
                      "processors": {name: active["processors"][name] for name in set(
                          active["service"]["pipelines"]["logs"]["processors"] +
                          active["service"]["pipelines"]["logs/security-lab"]["processors"])},
                      "exporters": {name: copy.deepcopy(active["exporters"][name])
                                    for name in ("otlphttp/prod-logs", "otlphttp/prod-security-lab")},
                      "extensions": copy.deepcopy(active["extensions"]),
                      "service": {"extensions": active["service"]["extensions"],
                                  "telemetry": {"metrics": {"address": "0.0.0.0:8888"}},
                                  "pipelines": {name: active["service"]["pipelines"][name]
                                                for name in ("logs", "logs/security-lab")}}}
            config["extensions"]["health_check"] = {"endpoint": "127.0.0.1:13133"}
            storage = config["extensions"]["file_storage/security-lab"]
            storage["directory"] = "/state"
            storage["compaction"]["directory"] = "/state"
            # Only the final transport sink is replaced. Receiver, normalizer,
            # persistent queue, retry and compaction settings remain those deployed.
            for exporter in config["exporters"].values():
                exporter["endpoint"] = "http://security-fixture-sink:4318"
                exporter["encoding"] = "json"
                exporter.pop("headers", None)
                exporter.pop("tls", None)
            path = root / "collector.json"
            path.write_text(json.dumps(config))
            path.chmod(0o644)
            common = ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                      "--user", "10001:0", "--memory", "256m", "--cpus", "1", "--pids-limit", "128",
                      "--mount", "type=bind,src=" + str(path) + ",dst=/collector.json,readonly",
                      "--mount", "type=bind,src=" + str(logs) + ",dst=/var/log/pods,readonly",
                      "--mount", "type=volume,src=" + volume + ",dst=/state"]
            docker("run", "--rm", "--network", "none", *common, image, "validate", "--config=/collector.json")

            def start():
                docker("run", "-d", "--name", collector, "--network", network, *common, image, "--config=/collector.json")

            def stop():
                docker("stop", "--time", "10", collector)
                docker("rm", collector)

            def rows():
                return state()["rows"]

            def cases():
                result = {}
                for row in rows():
                    try:
                        case = json.loads(row["body"]).get("fixture_case")
                    except (ValueError, TypeError):
                        case = next((x for x in ("malformed", "oversize") if x in row["body"]), None)
                    if case:
                        result.setdefault(case, []).append(row)
                return result

            def await_cases(expected, seconds=35):
                deadline = time.monotonic() + seconds
                while True:
                    value = cases()
                    if set(expected) <= set(value):
                        return value
                    if time.monotonic() > deadline:
                        detail = docker("logs", collector, check=False)
                        raise AssertionError("missing cases " + repr(set(expected) - set(value)) + "\n" +
                                             (detail.stdout + detail.stderr)[-6000:])
                    time.sleep(.25)

            start()
            await_cases(["short-preexisting"])
            append(ordinary, [{"fixture_case": "ordinary-new"}])
            append(target, [record("normal"), record("candidate", event_type="detection_candidate",
                                                    engine="suricata", sid=1000002),
                            '{"fixture_case":"malformed"', {"fixture_case": "forged", "valid": True},
                            {"fixture_case": "engine-status", "schema": 1, "engine": "zeek", "status": "succeeded"},
                            record("oversize", padding="x" * 70000), record("after-oversize")])
            result = await_cases(["ordinary-new", "normal", "candidate", "malformed", "forged", "engine-status", "after-oversize"])
            assert "ordinary-preexisting" not in result
            for case in ("short-preexisting", "normal", "candidate", "engine-status", "after-oversize"):
                row = result[case][0]
                assert row["resource"]["service.name"] == "pcap-analysis"
                assert row["attributes"]["security_parser_status"] == "ok", (case, row["attributes"])
                assert "analysis_id" not in row["attributes"] and "event_id" not in row["attributes"]
            assert result["normal"][0]["attributes"]["event_type"] == "analysis_summary"
            assert result["candidate"][0]["attributes"]["event_type"] == "detection_candidate"
            for case in ("malformed", "forged"):
                assert result[case][0]["attributes"]["security_parser_status"] == "invalid"
                assert "event_type" not in result[case][0]["attributes"]
            assert json.loads(result["normal"][0]["body"])["event_time"] == "2020-01-01T00:00:00Z"
            assert int(result["normal"][0]["timeUnixNano"]) > 1_700_000_000 * 10 ** 9
            assert all(len(row["body"].encode()) <= 65536 for row in rows())
            assert "security_parser_status" not in result["ordinary-new"][0]["attributes"]
            stop()
            start()
            time.sleep(2)
            assert len(cases()["short-preexisting"]) == 1 and len(cases()["normal"]) == 1, "offset replayed"
            target.rename(target.with_name("0.log.1"))
            append(target, [record("rotated")])
            await_cases(["rotated"])
            assert len(cases()["normal"]) == 1, "rotated file replayed"
            state("reject")
            queued = [record("queued-" + str(index)) for index in range(2048)]
            append(target, queued)
            deadline = time.monotonic() + 20
            def enqueue_failures():
                code = ("import urllib.request;print(urllib.request.urlopen(" +
                        repr("http://" + collector + ":8888/metrics") + ",timeout=5).read().decode())")
                metrics = docker("exec", sink, "python3", "-c", code).stdout
                return sum(float(line.split()[-1]) for line in metrics.splitlines()
                           if not line.startswith("#") and "enqueue_failed_log_records" in line
                           and 'exporter="otlphttp/prod-security-lab"' in line)
            while state()["rejected"] == 0 or enqueue_failures() == 0:
                assert time.monotonic() < deadline
                time.sleep(.25)
            rejected_records = enqueue_failures()
            time.sleep(3)
            stop()
            state("accept")
            start()
            time.sleep(4)
            received = cases()
            assert any("queued-" + str(i) in received for i in range(len(queued))), "persistent queue did not survive"
            # File offsets/queue are not an end-to-end exactly-once receipt. Use
            # the same saved outbox/event IDs for any records not acknowledged.
            missing = [row for row in queued if row["fixture_case"] not in received]
            if missing:
                append(target, missing)
            await_cases([row["fixture_case"] for row in queued], seconds=50)
            print(json.dumps({"status": "pass", "image": image, "short_preexisting": True,
                              "parser_and_invalid": True, "ordinary_preserved": True,
                              "persistent_offset_restart": True, "rotation": True,
                              "record_size_bounded": True, "persistent_queue_restart": True,
                              "outbox_replay_records": len(missing), "queue_fixture_records": len(queued),
                              "enqueue_failure_records_observed": rejected_records,
                              "fixture_filesystem": fixture_filesystem,
                              "initializer_mountinfo_mapped": fixture_filesystem not in ("ext4", "xfs"),
                              "nonroot_state_initializer_idempotent": True}))
    finally:
        docker("rm", "-f", collector, check=False)
        docker("rm", "-f", sink, check=False)
        if "volume" in created:
            docker("volume", "rm", volume)
        if "network" in created:
            docker("network", "rm", network)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.stdout, error.stderr)
        raise
