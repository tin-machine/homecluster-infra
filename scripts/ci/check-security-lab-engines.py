#!/usr/bin/env python3
"""Run the pinned native images without network, root, capabilities or writable root."""
import base64
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "terraform/env/staging/security_lab"
spec = importlib.util.spec_from_file_location("pcap_fixtures", ASSETS / "fixtures.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
config = (ROOT / "terraform/env/staging/security_lab_pipeline.tf").read_text()
results = []
with tempfile.TemporaryDirectory(prefix="security-engine-fixture-") as directory:
    mount = Path(directory)
    mount.chmod(0o755)
    for name in ("runtime.py", "retention.py", "suricata.yaml", "local.rules", "classification.config", "reference.config", "threshold.config"):
        target = mount / name
        target.write_text((ASSETS / name).read_text().replace("@HOME_NET@", "[192.0.2.0/24]"))
        target.chmod(0o644)
    for engine in ("suricata", "zeek"):
        image = re.search(r"\b" + engine + r'\s*=\s*"([^"]+@sha256:[a-f0-9]{64})"', config).group(1)
        for sample, payload in fixtures.samples().items():
            code = """
import os,sys,pathlib,base64,json,hashlib
os.umask(0o077)
sys.path.insert(0,"/config")
import runtime
engine,sample,encoded=sys.argv[1:]
pathlib.Path("/input/capture.pcap").write_bytes(base64.b64decode(encoded))
rc=runtime.engine_run(engine)
assert rc==0, "engine wrapper failed"
result=json.loads(pathlib.Path("/result/engine.json").read_text())
assert result["actual_version"]=={"suricata":"8.0.8","zeek":"9.0.1"}[engine]
assert result["output_files"]
for item in result["output_files"]:
 assert hashlib.sha256((pathlib.Path("/result")/item["path"]).read_bytes()).hexdigest()==item["sha256"]
if engine=="suricata":
 assert result["enabled_sids"]==[1000001,1000002]
 assert result["rules_sha256"]==hashlib.sha256(pathlib.Path("/config/local.rules").read_bytes()).hexdigest()
 rows=[json.loads(x) for x in pathlib.Path("/result/eve.json").read_text().splitlines()]
 sids=[x["alert"]["signature_id"] for x in rows if x.get("event_type")=="alert"]
 assert ({1000001,1000002}<=set(sids)) if sample=="positive" else not sids, sids
 assert result["decoded_packets"]>0
else:
 rows=[json.loads(x) for x in pathlib.Path("/result/conn.log").read_text().splitlines()]
 assert rows
 if sample=="negative":
  dns=[json.loads(x) for x in pathlib.Path("/result/dns.log").read_text().splitlines()]
  http=[json.loads(x) for x in pathlib.Path("/result/http.log").read_text().splitlines()]
  tls=[json.loads(x) for x in pathlib.Path("/result/ssl.log").read_text().splitlines()]
  assert any(x.get("query")=="fixture.example.test" for x in dns)
  assert any(x.get("host")=="fixture.example.test" for x in http)
  assert any(x.get("server_name")=="fixture.example.test" for x in tls)
  assert any(":" in x.get("id.orig_h","") for x in rows)
print(json.dumps({"status":"pass","engine":engine,"sample":sample,"peak_child_rss_bytes":result["peak_child_rss_bytes"]}),flush=True)
"""
            command = ["docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
                       "--security-opt", "no-new-privileges", "--read-only", "--user", "10001:10001",
                       "--memory", "2g", "--cpus", "1", "--pids-limit", "128",
                       "--env", "PYTHONDONTWRITEBYTECODE=1",
                       "--tmpfs", "/input:uid=10001,gid=10001,mode=0700,size=8m",
                       "--tmpfs", "/result:uid=10001,gid=10001,mode=0700,size=2g",
                       "--tmpfs", "/tmp:uid=10001,gid=10001,mode=0700,size=32m",
                       "--mount", "type=bind,src=" + str(mount) + ",dst=/config,readonly",
                       "--entrypoint", "/usr/bin/python3", image, "-c", code,
                       engine, sample, base64.b64encode(payload).decode()]
            process = subprocess.run(command, capture_output=True, text=True, timeout=150)
            if process.returncode:
                raise RuntimeError(engine + "/" + sample + ": " + (process.stdout + process.stderr)[-5000:])
            result = json.loads(process.stdout.strip().splitlines()[-1])
            results.append(result)
            print(json.dumps(result), flush=True)
print(json.dumps({"status": "pass", "cases": len(results)}))
