#!/usr/bin/env bash
# Source-only Loki validation with public fixtures; no cluster or state access.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tools_dir="$(mktemp -d)"
trap 'rm -rf "${tools_dir}"' EXIT
cd "${tools_dir}"
curl -fLsS https://get.helm.sh/helm-v3.17.3-linux-amd64.tar.gz -o helm.tar.gz
curl -fLsS https://github.com/grafana/loki/releases/download/v3.6.11/loki-linux-amd64.zip -o loki.zip
sha256sum --check --strict <<'CHECKSUMS'
ee88b3c851ae6466a3de507f7be73fe94d54cbf2987cbaa3d1a3832ea331f2cd  helm.tar.gz
e87959e3d7f32ae3e6a74704d71f5721d51c7558ff42614a7a0c85b5da2a9c9d  loki.zip
CHECKSUMS
tar -xzf helm.tar.gz
install linux-amd64/helm ./helm
unzip -q loki.zip
chmod +x loki-linux-amd64
python3 -m venv "${tools_dir}/venv"
"${tools_dir}/venv/bin/pip" install --disable-pip-version-check -q PyYAML==6.0.3
export PATH="${tools_dir}/venv/bin:${tools_dir}:${PATH}"
cd "${repo_root}"
python3 - "${tools_dir}" <<'PY'
from pathlib import Path
import re
import subprocess
import sys
module = Path('terraform/modules/observability_stack/main.tf').read_text()
block = module.split('resource "helm_release" "loki" {', 1)[1]
version = re.search(r'version\s*=\s*"([^"]+)"', block)[1]
subprocess.run(['helm', 'pull', 'loki', '--version', version,
                '--repo', 'https://grafana.github.io/helm-charts',
                '--destination', sys.argv[1]], check=True, timeout=120)
PY
python3 scripts/ci/check-staging-loki-compat.py \
  --charts-dir "${tools_dir}" --loki-binary "${tools_dir}/loki-linux-amd64"
