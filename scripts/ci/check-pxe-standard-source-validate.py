#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/pxe_standard_source_validate.py"
spec = importlib.util.spec_from_file_location(
    "pxe_standard_source_validate_tested",
    MODULE_PATH,
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load standard PXE source validator")
validator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validator
spec.loader.exec_module(validator)


def completed(
    command,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
):
    return subprocess.CompletedProcess(
        list(command),
        returncode,
        stdout,
        stderr,
    )


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, command, **_kwargs):
        self.calls.append(list(command))
        if not self.responses:
            raise AssertionError("unexpected command")
        return self.responses.pop(0)


class StandardPxeSourceValidatorTests(unittest.TestCase):
    def pass_responses(self):
        return [
            completed([], stdout="a" * 40 + "\n"),
            completed([], stdout=""),
            *[completed([]) for _ in validator.CHECKS],
        ]

    def test_pass_binds_exact_revision_and_fixed_check_count(self):
        runner = FakeRunner(self.pass_responses())

        result = validator.validate_source(repo_root=ROOT, runner=runner)

        self.assertEqual(
            result,
            {
                "schema": 1,
                "validator": validator.VALIDATOR,
                "status": "pass",
                "reason": "infra_standard_pxe_source_validated",
                "source_revision": "a" * 40,
                "checks": len(validator.CHECKS),
            },
        )
        self.assertEqual(len(runner.calls), 2 + len(validator.CHECKS))

    def test_dirty_repository_blocks_before_source_checks(self):
        runner = FakeRunner(
            [
                completed([], stdout="a" * 40 + "\n"),
                completed([], stdout=" M tracked.yml\n"),
            ]
        )

        result = validator.validate_source(repo_root=ROOT, runner=runner)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "infra_standard_pxe_repository_dirty")
        self.assertEqual(len(runner.calls), 2)

    def test_failed_fixed_check_blocks(self):
        responses = [
            completed([], stdout="a" * 40 + "\n"),
            completed([], stdout=""),
            completed([], returncode=1),
        ]
        runner = FakeRunner(responses)

        result = validator.validate_source(repo_root=ROOT, runner=runner)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "infra_standard_pxe_source_check_failed")
        self.assertEqual(result["checks"], 0)

    def test_invalid_revision_is_unknown(self):
        runner = FakeRunner([completed([], stdout="not-a-sha\n")])

        result = validator.validate_source(repo_root=ROOT, runner=runner)

        self.assertEqual(result["status"], "unknown")
        self.assertEqual(
            result["reason"],
            "infra_standard_pxe_source_revision_invalid",
        )


if __name__ == "__main__":
    unittest.main()
