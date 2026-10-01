#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/rpi5_common_kernel_identity_status.py"
spec = importlib.util.spec_from_file_location(
    "rpi5_common_kernel_identity_status_tested",
    MODULE_PATH,
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load common-kernel identity probe")
probe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = probe
spec.loader.exec_module(probe)


KERNEL_DATE = "20261002"
PXE_DATE = "20261003"
OLD_KERNEL_DATE = "20260930"
OLD_PXE_DATE = "20261001"


def completed(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
):
    return subprocess.CompletedProcess(
        [],
        returncode,
        stdout,
        stderr,
    )


def kernel_inventory(current: str = OLD_KERNEL_DATE) -> str:
    return json.dumps(
        {
            probe.BUILDER_GROUP: {"hosts": ["rpi5-03"]},
            "_meta": {
                "hostvars": {
                    "rpi5-03": {
                        probe.BUILD_DATE_KEY: current,
                    }
                }
            },
        }
    )


def pxe_inventory() -> str:
    return json.dumps(
        {
            "openwrt": {"hosts": ["home-router"]},
        }
    )


def pxe_host(current: str = OLD_PXE_DATE) -> str:
    return json.dumps(
        {
            "openwrt_gentoo_release_bundle_stage_dates": {
                "stg": current,
            }
        }
    )


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, command, _timeout):
        self.calls.append(tuple(command))
        if not self.responses:
            raise AssertionError(f"unexpected command: {command}")
        return self.responses.pop(0)


class CommonKernelIdentityStatusTests(unittest.TestCase):
    def test_independent_unused_identities_pass(self):
        runner = FakeRunner(
            [
                completed(stdout=kernel_inventory()),
                completed(stdout=f"{probe.MARKER}=0\n"),
                completed(stdout=pxe_inventory()),
                completed(stdout=pxe_host()),
                completed(stdout="PXE_IDENTITY_COLLISION=0\n"),
            ]
        )

        result = probe.probe_candidates(
            KERNEL_DATE,
            PXE_DATE,
            inventory_path=Path("/fixture/inventory.yml"),
            runner=runner,
        )

        self.assertEqual(
            result,
            {
                "status": "pass",
                "reason": "identities_available",
                "kernel_build_date": KERNEL_DATE,
                "pxe_release_date": PXE_DATE,
                "kernel_identity": "available",
                "pxe_identity": "available",
            },
        )
        self.assertEqual(len(runner.calls), 5)
        builder_command = runner.calls[1][-3]
        self.assertIn(f"{KERNEL_DATE}-rpi5-homecluster", builder_command)
        self.assertIn(f"rpi5-egpu-{KERNEL_DATE}-rpi5.tar.gz", builder_command)
        pxe_command = runner.calls[4][-3]
        self.assertIn(f"/srv/gentoo/releases/{PXE_DATE}.json", pxe_command)
        self.assertNotIn(KERNEL_DATE, pxe_command)

    def test_current_kernel_inventory_identity_blocks_before_remote_probe(self):
        runner = FakeRunner(
            [
                completed(stdout=kernel_inventory(KERNEL_DATE)),
            ]
        )

        result = probe.probe_candidates(
            KERNEL_DATE,
            PXE_DATE,
            inventory_path=Path("/fixture/inventory.yml"),
            runner=runner,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "kernel_identity_in_use")
        self.assertEqual(result["kernel_identity"], "in_use")
        self.assertEqual(result["pxe_identity"], "not_checked")
        self.assertEqual(len(runner.calls), 1)

    def test_materialized_builder_identity_blocks_before_pxe_probe(self):
        runner = FakeRunner(
            [
                completed(stdout=kernel_inventory()),
                completed(stdout=f"{probe.MARKER}=1\n"),
            ]
        )

        result = probe.probe_candidates(
            KERNEL_DATE,
            PXE_DATE,
            inventory_path=Path("/fixture/inventory.yml"),
            runner=runner,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "kernel_identity_in_use")
        self.assertEqual(len(runner.calls), 2)

    def test_existing_pxe_identity_blocks_after_kernel_pass(self):
        runner = FakeRunner(
            [
                completed(stdout=kernel_inventory()),
                completed(stdout=f"{probe.MARKER}=0\n"),
                completed(stdout=pxe_inventory()),
                completed(stdout=pxe_host(PXE_DATE)),
            ]
        )

        result = probe.probe_candidates(
            KERNEL_DATE,
            PXE_DATE,
            inventory_path=Path("/fixture/inventory.yml"),
            runner=runner,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "pxe_identity_in_use")
        self.assertEqual(result["kernel_identity"], "available")
        self.assertEqual(result["pxe_identity"], "in_use")
        self.assertEqual(len(runner.calls), 4)

    def test_invalid_calendar_date_blocks_without_commands(self):
        runner = FakeRunner([])

        result = probe.probe_candidates(
            "20260230",
            PXE_DATE,
            inventory_path=Path("/fixture/inventory.yml"),
            runner=runner,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "probe_blocked")
        self.assertEqual(runner.calls, [])

    def test_broken_builder_probe_is_unknown(self):
        runner = FakeRunner(
            [
                completed(stdout=kernel_inventory()),
                completed(returncode=1),
            ]
        )

        result = probe.probe_candidates(
            KERNEL_DATE,
            PXE_DATE,
            inventory_path=Path("/fixture/inventory.yml"),
            runner=runner,
        )

        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "source_unavailable")
        self.assertEqual(result["kernel_identity"], "unknown")
        self.assertEqual(result["pxe_identity"], "not_checked")


if __name__ == "__main__":
    unittest.main()
