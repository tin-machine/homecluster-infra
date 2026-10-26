#!/usr/bin/env python3
"""Exercise the rendered Terraform pre-apply wait with a fake k3s API."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "ansible/arm64/roles/k3s_observability_apply/templates/k3s-observability-apply-wait.sh.j2"


def write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


class PodSettleWaitTests(unittest.TestCase):
    def run_wait(self, responses: list[str], *, timeout: int = 12, include: str = "", exclude: str = ""):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            (root / "responses").write_text("\n".join(responses) + "\n")
            write_executable(bin_dir / "k3s", """#!/bin/sh
case " $* " in
  *' get --raw=/readyz '*) exit 0 ;;
  *' get nodes '*) printf 'test-node Ready control-plane 1d v1\\n'; exit 0 ;;
  *' wait --for=condition=Ready '*) exit 0 ;;
  *' get pods '*)
    count=$(cat "$FIXTURE_ROOT/pod-count" 2>/dev/null || printf 0)
    count=$((count + 1))
    printf '%s' "$count" > "$FIXTURE_ROOT/pod-count"
    response=$(sed -n "${count}p" "$FIXTURE_ROOT/responses")
    case "$response" in
      empty) exit 0 ;;
      pending) printf 'allowed pod-a 0/1 Pending 0 10s\\n'; exit 0 ;;
      excluded) printf 'excluded pod-a 0/1 Pending 0 10s\\n'; exit 0 ;;
      malformed) printf 'garbled-response\\n'; exit 0 ;;
      error) printf 'SECRET_API_ERROR' >&2; exit 1 ;;
      *) printf 'fixture response exhausted' >&2; exit 2 ;;
    esac ;;
esac
exit 2
""")
            write_executable(bin_dir / "systemctl", "#!/bin/sh\nexit 0\n")
            write_executable(bin_dir / "hostnamectl", "#!/bin/sh\nprintf 'test-node\\n'\n")
            write_executable(bin_dir / "sleep", "#!/bin/sh\nexit 0\n")
            write_executable(bin_dir / "date", """#!/bin/sh
count=$(cat "$FIXTURE_ROOT/date-count" 2>/dev/null || printf 0)
count=$((count + 1))
printf '%s' "$count" > "$FIXTURE_ROOT/date-count"
printf '%s\\n' "$count"
""")
            values = {
                "k3s_observability_apply_kubeconfig_path": str(root / "dummy-kubeconfig"),
                "k3s_observability_apply_run_root": str(root),
                "k3s_observability_apply_stage_name": "fixture",
                "k3s_observability_apply_wait_timeout_secs": str(timeout),
                "k3s_observability_apply_retry_secs": "0",
                "k3s_observability_apply_post_ready_settle_secs": "0",
                "k3s_observability_apply_expected_ready_nodes": "1",
                "k3s_observability_apply_pod_settle_enabled | bool | ternary('true', 'false')": "true",
                "k3s_observability_apply_pod_settle_pass_count": "3",
                "k3s_observability_apply_pod_settle_interval_secs": "0",
                "k3s_observability_apply_pod_settle_namespace_include_regex": include,
                "k3s_observability_apply_pod_settle_namespace_exclude_regex": exclude,
            }

            def replace(match: re.Match[str]) -> str:
                return values[match.group(1).strip()]

            rendered = re.sub(r"{{\s*(.*?)\s*}}", replace, TEMPLATE.read_text())
            rendered = rendered.replace("/usr/local/bin/k3s", str(bin_dir / "k3s"))
            script = root / "wait.sh"
            write_executable(script, rendered)
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["FIXTURE_ROOT"] = str(root)
            result = subprocess.run(["/bin/sh", str(script)], env=env, text=True, capture_output=True, timeout=5)
            count = int((root / "pod-count").read_text())
            return result, count

    def test_empty_response_needs_three_successes(self):
        result, count = self.run_wait(["empty"] * 3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 3)

    def test_pending_resets_successes(self):
        result, count = self.run_wait(["empty", "pending", "empty", "empty", "empty"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 5)

    def test_excluded_namespace_does_not_block(self):
        result, count = self.run_wait(["excluded"] * 3, exclude="^excluded$")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 3)

    def test_api_failure_resets_and_then_recovers(self):
        result, count = self.run_wait(["empty", "error", "empty", "empty", "empty"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 5)
        self.assertIn("API query failed", result.stderr)
        self.assertNotIn("SECRET_API_ERROR", result.stderr)

    def test_continuous_api_failure_times_out(self):
        result, count = self.run_wait(["error"] * 10, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertGreaterEqual(count, 3)
        self.assertIn("timed out waiting for pod settle", result.stderr)
        self.assertNotIn("SECRET_API_ERROR", result.stderr)

    def test_malformed_response_never_counts_as_empty(self):
        result, count = self.run_wait(["malformed"] * 10, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertGreaterEqual(count, 3)
        self.assertIn("invalid data", result.stderr)

    def test_include_filter_counts_only_selected_namespace(self):
        result, count = self.run_wait(["excluded"] * 3, include="^allowed$")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
