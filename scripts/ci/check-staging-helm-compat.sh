#!/usr/bin/env bash
# Source-only release config validation; no cluster or Terraform backend access.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tools_dir="$(mktemp -d)"
trap 'rm -rf "${tools_dir}"' EXIT
cd "${tools_dir}"
curl -fLsS https://get.helm.sh/helm-v3.17.3-linux-amd64.tar.gz -o helm.tar.gz
curl -fLsS https://releases.hashicorp.com/terraform/1.16.4/terraform_1.16.4_linux_amd64.zip -o terraform.zip
curl -fLsS https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v0.102.1/otelcol-contrib_0.102.1_linux_amd64.tar.gz -o otel.tar.gz
curl -fLsS https://github.com/grafana/loki/releases/download/v2.9.6/loki-linux-amd64.zip -o loki.zip
# SHA-256 values from the official release checksum files.
sha256sum --check --strict <<'CHECKSUMS'
ee88b3c851ae6466a3de507f7be73fe94d54cbf2987cbaa3d1a3832ea331f2cd  helm.tar.gz
dc94af0eef1147718ad7c8daea792ed199e3e0492eec180d0adafa2a65a879df  terraform.zip
326772622016f7ff7e966a7ae8a0f439dc49a3d80b6d79a82b62608af447e851  otel.tar.gz
4d083365dc2c843de5d836cc836cf49e391eb1174f77a40bfd4a26a74ba3173e  loki.zip
CHECKSUMS
tar -xzf helm.tar.gz
install linux-amd64/helm ./helm
unzip -q terraform.zip
tar -xzf otel.tar.gz otelcol-contrib
unzip -q loki.zip
chmod +x helm terraform otelcol-contrib loki-linux-amd64
export PATH="${tools_dir}:${PATH}"
python3 -m venv "${tools_dir}/venv"
"${tools_dir}/venv/bin/pip" install --disable-pip-version-check -q PyYAML==6.0.3
export PATH="${tools_dir}/venv/bin:${PATH}"
cd "${repo_root}"
export STAGING_HELM_COMPAT_TOOLS_DIR="${tools_dir}"
python3 - <<'PY'
import os
from pathlib import Path
import re
import subprocess
destination = Path(os.environ['STAGING_HELM_COMPAT_TOOLS_DIR'])
sources = [
    ('terraform/env/common-crds/main.tf', 'metallb', 'metallb', 'https://metallb.github.io/metallb'),
    ('terraform/modules/observability_stack/main.tf', 'loki', 'loki', 'https://grafana.github.io/helm-charts'),
    ('terraform/modules/observability_stack/main.tf', 'otel_collector', 'opentelemetry-collector', 'https://open-telemetry.github.io/opentelemetry-helm-charts'),
]
for source, release, chart, repository in sources:
    block = Path(source).read_text().split(f'resource "helm_release" "{release}" {{', 1)[1]
    version = re.search(r'version\s*=\s*"([^"]+)"', block)[1]
    subprocess.run(['helm', 'pull', chart, '--version', version, '--repo', repository,
                    '--destination', str(destination)], check=True, timeout=120)
PY
python3 scripts/ci/check-staging-helm-compat.py \
  --charts-dir "${tools_dir}" \
  --collector-binary "${tools_dir}/otelcol-contrib" \
  --loki-binary "${tools_dir}/loki-linux-amd64"
