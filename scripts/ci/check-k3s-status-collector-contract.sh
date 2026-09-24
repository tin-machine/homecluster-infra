#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

bash "$repo_root/.agents/skills/homecluster-convergence-monitor/scripts/test-pi-k3s-status.sh"
bash "$repo_root/.agents/skills/homecluster-convergence-monitor/scripts/test-collect-k3s-convergence.sh"
python3 -m unittest discover -s "$repo_root/scripts" -p 'test_pi_rpi5_common_kernel_rollout_health.py' -v
