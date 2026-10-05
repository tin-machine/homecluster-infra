#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/pxe_release_identity_status.py"

spec = importlib.util.spec_from_file_location(
    "pxe_release_identity_status_tested",
    MODULE_PATH,
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load PXE release identity status")
status = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = status
spec.loader.exec_module(status)


def completed(
    command: list[str] | tuple[str, ...],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        list(command),
        returncode,
        stdout,
        stderr,
    )


def inventory_list(hosts: list[str]) -> str:
    return json.dumps(
        {
            "openwrt": {"hosts": hosts},
            "_meta": {"hostvars": {}},
        }
    )


def host_vars(current: object) -> str:
    return json.dumps(
        {
            "openwrt_gentoo_release_bundle_stage_dates": {
                "stg": current,
            }
        }
    )


class FakeRunner:
    def __init__(self, responses: list[subprocess.CompletedProcess[str]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[str], int]] = []

    def __call__(
        self,
        command: list[str] | tuple[str, ...],
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(command), timeout))
        if not self.responses:
            raise AssertionError("unexpected command")
        return self.responses.pop(0)


class IdentityStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = Path("/tmp/public-fixture-inventory.yml")

    def test_invalid_candidate_is_blocked_before_commands(self):
        runner = FakeRunner([])

        result = status.probe_candidate(
            "2026-09-30",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(
            result,
            {
                "status": "blocked",
                "reason": "probe_blocked",
                "candidate": "2026-09-30",
            },
        )
        self.assertEqual(runner.calls, [])

    def test_invalid_calendar_candidate_is_blocked_before_commands(self):
        runner = FakeRunner([])

        result = status.probe_candidate(
            "20260230",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "probe_blocked")
        self.assertEqual(runner.calls, [])

    def test_current_inventory_identity_is_in_use_without_remote_probe(self):
        runner = FakeRunner(
            [
                completed([], stdout=inventory_list(["router.example.invalid"])),
                completed([], stdout=host_vars("20260930")),
            ]
        )

        result = status.probe_candidate(
            "20260930",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(
            result,
            {
                "status": "blocked",
                "reason": "identity_in_use",
                "candidate": "20260930",
            },
        )
        self.assertEqual(len(runner.calls), 2)

    def test_older_identity_is_rejected_without_remote_probe(self):
        runner = FakeRunner(
            [
                completed([], stdout=inventory_list(["router.example.invalid"])),
                completed([], stdout=host_vars("20261006")),
            ]
        )

        result = status.probe_candidate(
            "20261005",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "identity_in_use")
        self.assertEqual(len(runner.calls), 2)

    def test_numeric_inventory_identity_is_normalized(self):
        runner = FakeRunner(
            [
                completed([], stdout=inventory_list(["router.example.invalid"])),
                completed([], stdout=host_vars(20260930)),
            ]
        )

        result = status.probe_candidate(
            "20260930",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "identity_in_use")
        self.assertEqual(len(runner.calls), 2)

    def test_remote_materialization_is_in_use(self):
        runner = FakeRunner(
            [
                completed([], stdout=inventory_list(["router.example.invalid"])),
                completed([], stdout=host_vars("20260929")),
                completed([], stdout="router.example.invalid | CHANGED | rc=0 >> PXE_IDENTITY_COLLISION=1"),
            ]
        )

        result = status.probe_candidate(
            "20260930",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(
            result,
            {
                "status": "blocked",
                "reason": "identity_in_use",
                "candidate": "20260930",
            },
        )
        remote_command = runner.calls[2][0]
        raw = remote_command[remote_command.index("-a") + 1]
        for expected in (
            "/srv/gentoo/releases/20260930.json",
            "/srv/gentoo/20260930-rpi4",
            "/srv/gentoo/20260930-rpi5",
            "/srv/gentoo/tftp-root/dates/20260930-rpi4",
            "/srv/gentoo/tftp-root/dates/20260930-rpi5",
        ):
            self.assertIn(expected, raw)

    def test_unused_identity_is_available(self):
        runner = FakeRunner(
            [
                completed([], stdout=inventory_list(["router.example.invalid"])),
                completed([], stdout=host_vars("20260929")),
                completed([], stdout="router.example.invalid | CHANGED | rc=0 >> PXE_IDENTITY_COLLISION=0"),
            ]
        )

        result = status.probe_candidate(
            "20260930",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(
            result,
            {
                "status": "pass",
                "reason": "identity_available",
                "candidate": "20260930",
            },
        )

    def test_inventory_command_failure_is_unknown(self):
        runner = FakeRunner([completed([], returncode=2, stderr="fixture failure")])

        result = status.probe_candidate(
            "20260930",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(
            result,
            {
                "status": "unknown",
                "reason": "source_unavailable",
                "candidate": "20260930",
            },
        )

    def test_inventory_requires_exactly_one_openwrt_host(self):
        for hosts in ([], ["one.example.invalid", "two.example.invalid"]):
            with self.subTest(hosts=hosts):
                runner = FakeRunner([completed([], stdout=inventory_list(hosts))])

                result = status.probe_candidate(
                    "20260930",
                    inventory_path=self.inventory,
                    runner=runner,
                )

                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["reason"], "source_unavailable")
                self.assertEqual(len(runner.calls), 1)

    def test_invalid_stage_identity_is_unknown(self):
        runner = FakeRunner(
            [
                completed([], stdout=inventory_list(["router.example.invalid"])),
                completed([], stdout=host_vars("2026-09-29")),
            ]
        )

        result = status.probe_candidate(
            "20260930",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "source_unavailable")
        self.assertEqual(len(runner.calls), 2)

    def test_remote_failure_is_unknown(self):
        runner = FakeRunner(
            [
                completed([], stdout=inventory_list(["router.example.invalid"])),
                completed([], stdout=host_vars("20260929")),
                completed([], returncode=4, stderr="unreachable"),
            ]
        )

        result = status.probe_candidate(
            "20260930",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "source_unavailable")

    def test_ambiguous_remote_marker_is_unknown(self):
        runner = FakeRunner(
            [
                completed([], stdout=inventory_list(["router.example.invalid"])),
                completed([], stdout=host_vars("20260929")),
                completed(
                    [],
                    stdout=(
                        "PXE_IDENTITY_COLLISION=0 "
                        "PXE_IDENTITY_COLLISION=1"
                    ),
                ),
            ]
        )

        result = status.probe_candidate(
            "20260930",
            inventory_path=self.inventory,
            runner=runner,
        )

        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "source_unavailable")


if __name__ == "__main__":
    unittest.main()
