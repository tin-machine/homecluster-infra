#!/usr/bin/env bash
# Hosted-CI source-only Loki image/config and loopback smoke; no cluster access.
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v docker >/dev/null
command -v python3 >/dev/null
scratch="$(mktemp -d)"
cid=""
cleanup() {
  if [ -n "$cid" ]; then docker rm -f "$cid" >/dev/null 2>&1 || true; fi
  rm -f "$scratch/loki.json"
  rmdir "$scratch"
}
trap cleanup EXIT

python3 - "$scratch/loki.json" <<'PY'
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, "scripts")
import prod_o4_loki_render as o4
doc = o4.build_manifest()
configmap = next(i for i in doc["items"] if i["kind"] == "ConfigMap")
output = Path(sys.argv[1])
with output.open("x", encoding="utf-8") as stream:
    stream.write(configmap["data"]["loki.yaml"])
os.chmod(output, 0o644)
assert json.loads(output.read_text())["common"]["instance_addr"] == "127.0.0.1"
PY

image="$(python3 - <<'PY'
import sys
sys.path.insert(0, "scripts")
import prod_o4_loki_render as o4
print(o4.IMAGE)
PY
)"
docker manifest inspect "$image" | python3 -c '
import json, sys
doc=json.load(sys.stdin)
platforms=[m.get("platform", {}) for m in doc.get("manifests", [])]
assert any(p.get("os")=="linux" and p.get("architecture")=="arm64" for p in platforms), "missing linux/arm64 image"
'
docker pull "$image" >/dev/null
docker pull curlimages/curl:8.10.1 >/dev/null

common_args=(
  --network none --read-only --cap-drop ALL
  --security-opt no-new-privileges --user 10001:10001
  --mount "type=bind,src=$scratch/loki.json,dst=/etc/loki/loki.yaml,readonly"
  --tmpfs /tmp:rw,uid=10001,gid=10001,mode=0700
  --tmpfs /var/loki:rw,uid=10001,gid=10001,mode=0700
)

docker run --rm "${common_args[@]}" "$image" \
  -config.file=/etc/loki/loki.yaml -verify-config=true
cid="$(docker run -d "${common_args[@]}" "$image" -config.file=/etc/loki/loki.yaml)"
for _ in $(seq 1 60); do
  if [ "$(docker inspect --format '{{.State.Running}}' "$cid")" != "true" ]; then
    echo 'Loki exited before readiness' >&2
    docker logs --tail=80 "$cid" >&2 || true
    exit 1
  fi
  if docker run --rm --network "container:$cid" --read-only \
      --cap-drop ALL --security-opt no-new-privileges \
      curlimages/curl:8.10.1 -fsS --connect-timeout 1 --max-time 3 \
      http://127.0.0.1:3100/ready >/dev/null 2>&1; then
    echo 'Loki isolated config, ARM64 image and loopback readiness PASS'
    exit 0
  fi
  sleep 2
done
echo 'Loki isolated readiness timed out' >&2
docker logs --tail=80 "$cid" >&2 || true
exit 1
