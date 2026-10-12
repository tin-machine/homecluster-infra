#!/usr/bin/env bash
# Isolated source smoke for the O4 TLS/auth gateway; no Kubernetes access.
set -euo pipefail
cd "$(dirname "$0")/../.."
command -v docker >/dev/null
command -v openssl >/dev/null
command -v python3 >/dev/null
scratch="$(mktemp -d)"
cid=""
cleanup() {
  if [ -n "$cid" ]; then docker rm -f "$cid" >/dev/null 2>&1 || true; fi
  rm -f "$scratch/nginx.conf" "$scratch/secrets/tls.crt" \
        "$scratch/secrets/tls.key" "$scratch/secrets/htpasswd"
  rm -f "$scratch/docker/config.json"
  rmdir "$scratch/docker" "$scratch/secrets" "$scratch"
}
trap cleanup EXIT
mkdir "$scratch/secrets"
mkdir "$scratch/docker"
printf '{"auths":{}}\n' > "$scratch/docker/config.json"
chmod 0600 "$scratch/docker/config.json"
export DOCKER_CONFIG="$scratch/docker"

python3 - "$scratch/nginx.conf" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, "scripts")
import prod_o4_push_gateway_render as gateway
doc = gateway.build_manifest(("10.42.0.0/24",))
config = next(item for item in doc["items"] if item["kind"] == "ConfigMap")
Path(sys.argv[1]).write_text(config["data"]["nginx.conf"], encoding="utf-8")
PY
openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
  -subj /CN=gateway.fixture -addext subjectAltName=DNS:gateway.fixture \
  -keyout "$scratch/secrets/tls.key" -out "$scratch/secrets/tls.crt" \
  >/dev/null 2>&1
printf 'fixture:%s\n' "$(openssl passwd -apr1 fixture-password)" \
  > "$scratch/secrets/htpasswd"
chmod 0644 "$scratch/nginx.conf" "$scratch/secrets/tls.crt" \
  "$scratch/secrets/tls.key" "$scratch/secrets/htpasswd"

image="nginx:1.30.5-alpine3.24"
docker manifest inspect "$image" | python3 -c '
import json, sys
doc=json.load(sys.stdin)
platforms=[m.get("platform", {}) for m in doc.get("manifests", [])]
assert any(p.get("os")=="linux" and p.get("architecture")=="arm64" for p in platforms), "missing linux/arm64 image"
'
docker pull "$image" >/dev/null
docker pull curlimages/curl:8.10.1 >/dev/null
common_args=(
  --entrypoint nginx
  --network none --add-host prod-loki.observability-prod.svc.cluster.local:127.0.0.1
  --read-only --cap-drop ALL --security-opt no-new-privileges --user 101:101
  --mount "type=bind,src=$scratch/nginx.conf,dst=/etc/nginx/nginx.conf,readonly"
  --mount "type=bind,src=$scratch/secrets,dst=/run/secrets/o4,readonly"
  --tmpfs "/tmp:rw,uid=101,gid=101,mode=0700"
)
docker run --rm "${common_args[@]}" "$image" -t -c /etc/nginx/nginx.conf
cid="$(docker run -d "${common_args[@]}" "$image" \
  -g 'daemon off;' -c /etc/nginx/nginx.conf)"
for _ in $(seq 1 30); do
  if [ "$(docker inspect --format '{{.State.Running}}' "$cid")" != "true" ]; then
    echo 'gateway exited before readiness' >&2
    docker logs --tail=40 "$cid" >&2 || true
    exit 1
  fi
  if docker run --rm --network "container:$cid" --read-only \
      --cap-drop ALL --security-opt no-new-privileges \
      --mount "type=bind,src=$scratch/secrets/tls.crt,dst=/tmp/ca.crt,readonly" \
      curlimages/curl:8.10.1 -fsS --connect-timeout 1 --max-time 3 \
      --cacert /tmp/ca.crt --resolve gateway.fixture:8443:127.0.0.1 \
      https://gateway.fixture:8443/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
status="$(docker run --rm --network "container:$cid" --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --mount "type=bind,src=$scratch/secrets/tls.crt,dst=/tmp/ca.crt,readonly" \
  curlimages/curl:8.10.1 -sS -o /dev/null -w '%{http_code}' \
  --cacert /tmp/ca.crt --resolve gateway.fixture:8443:127.0.0.1 \
  -X POST https://gateway.fixture:8443/otlp/v1/logs)"
if [ "$status" != 401 ]; then
  echo 'unauthenticated OTLP POST was not denied' >&2
  exit 1
fi
echo 'gateway image, TLS readiness and unauthenticated POST denial PASS'
