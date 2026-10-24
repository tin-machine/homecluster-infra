from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from staging_upgrade_support import run_captured, stop_process


class StagingUpgradeSupportTests(unittest.TestCase):
    def test_command_preserves_timeout_cwd_and_captured_streams(self) -> None:
        completed = subprocess.CompletedProcess(["fixture"], 0, stdout="ok", stderr="")
        with patch("staging_upgrade_support.subprocess.run", return_value=completed) as runner:
            self.assertIs(
                run_captured(["fixture"], data="input", timeout=7, cwd=Path("/fixture")),
                completed,
            )
        runner.assert_called_once_with(
            ["fixture"], input="input", text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=7, cwd=Path("/fixture"),
        )

    def test_stuck_forward_is_killed_and_reaped(self) -> None:
        class Process:
            events: list[str]

            def __init__(self) -> None:
                self.events = []

            def terminate(self) -> None:
                self.events.append("terminate")

            def wait(self, timeout: int | None = None) -> None:
                self.events.append(f"wait:{timeout}")
                if timeout is not None:
                    raise subprocess.TimeoutExpired("forward", timeout)

            def kill(self) -> None:
                self.events.append("kill")

        process = Process()
        stop_process(process, timeout=10)  # type: ignore[arg-type]
        self.assertEqual(process.events, ["terminate", "wait:10", "kill", "wait:None"])


if __name__ == "__main__":
    unittest.main()
