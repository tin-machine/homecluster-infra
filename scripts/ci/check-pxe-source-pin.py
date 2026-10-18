#!/usr/bin/env python3
"""Synthetic contract checks for immutable boot source declarations; no live IO."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[2] / (
    "ansible/openwrt/roles/openwrt_gentoo_rootfs/files/pxe-source-pin.py"
)
SPEC = importlib.util.spec_from_file_location("pxe_source_pin_fixture", SOURCE)
assert SPEC is not None and SPEC.loader is not None
pin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pin)


def declaration(**changes):
    value = {
        "schema": "homecluster-pxe-source-declaration-v1",
        "environment": "staging",
        "build_id": "20261018T000000Z-fixture-build-1",
        "infra_revision": "a" * 40,
    }
    value.update(changes)
    return json.dumps(value, sort_keys=True).encode("ascii") + b"\n"


class SourcePinTests(unittest.TestCase):
    def test_staging_declaration_returns_exact_revision_and_raw_bytes_digest(self):
        for stage in ("stg", "staging"):
            raw = declaration()
            self.assertEqual(
                pin.decode_declaration(raw, stage=stage),
                {
                    "mode": "pinned",
                    "infra_revision": "a" * 40,
                    "build_id": "20261018T000000Z-fixture-build-1",
                    "declaration_sha256": hashlib.sha256(raw).hexdigest(),
                },
            )

    def test_digest_includes_file_terminator_without_rewriting_source(self):
        raw = declaration()
        a = pin.decode_declaration(raw, stage="stg")
        b = pin.decode_declaration(raw.rstrip(b"\n"), stage="stg")
        self.assertNotEqual(a["declaration_sha256"], b["declaration_sha256"])
        self.assertEqual(a["infra_revision"], b["infra_revision"])

    def test_pinned_declaration_rejects_prod_unknown_and_other_environments(self):
        for stage in ("prod", "prd", "production", "", "unknown", None):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                pin.decode_declaration(declaration(), stage=stage)
        for environment in ("production", "stg", "unknown", None, 1):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                pin.decode_declaration(
                    declaration(environment=environment), stage="stg"
                )

    def test_missing_unknown_and_self_referencing_fields_rejected(self):
        base = json.loads(declaration())
        for key in base:
            value = {k: v for k, v in base.items() if k != key}
            with self.subTest(missing=key), self.assertRaises(ValueError):
                pin.decode_declaration(json.dumps(value).encode(), stage="stg")
        for key in ("artifact_sha256", "deployment_attempt_id", "secret", "path"):
            with self.subTest(extra=key), self.assertRaises(ValueError):
                pin.decode_declaration(
                    declaration(**{key: "fixture-only"}), stage="stg"
                )

    def test_invalid_revision_and_build_identity_rejected(self):
        cases = {
            "infra_revision": [
                "main",
                "a" * 39,
                "A" * 40,
                "g" * 40,
                "a" * 40 + "\n",
                None,
                1,
                [],
            ],
            "build_id": [
                "",
                "../escape",
                "x y",
                "$(id)",
                "x\ny",
                "x" * 129,
                None,
                [],
                "é",
            ],
            "schema": [1, None, "homecluster-pxe-source-declaration-v2"],
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    pin.decode_declaration(declaration(**{key: value}), stage="stg")

    def test_duplicate_json_keys_and_malformed_or_oversized_bytes_rejected(self):
        raw = declaration().rstrip()
        duplicate = raw[:-1] + b',"infra_revision":"' + b"b" * 40 + b'"}'
        for value in (
            duplicate,
            b"{}",
            b"[]",
            b"null",
            b"{",
            b"\xff",
            b"x" * 4097,
            raw.decode(),
            None,
        ):
            with self.subTest(shape=type(value).__name__), self.assertRaises(
                ValueError
            ):
                pin.decode_declaration(value, stage="stg")

    def test_build_identifier_boundary_is_bounded(self):
        raw = declaration(build_id="x" * 128)
        self.assertEqual(
            pin.decode_declaration(raw, stage="stg")["build_id"], "x" * 128
        )

    def test_fixed_file_resolution_preserves_the_declaration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source-declaration.json"
            raw = declaration()
            path.write_bytes(raw)
            before = path.stat()
            with mock.patch.object(pin, "SOURCE_PATH", path):
                self.assertEqual(
                    pin.resolve(mode="pinned", stage="stg"),
                    pin.decode_declaration(raw, stage="stg"),
                )
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_missing_declaration_never_falls_back_in_pinned_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(pin, "SOURCE_PATH", Path(temp) / "missing"):
                with self.assertRaises((ValueError, OSError)):
                    pin.resolve(mode="pinned", stage="stg")

    def test_legacy_mode_requires_absent_declaration_and_known_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source-declaration.json"
            with mock.patch.object(pin, "SOURCE_PATH", path):
                for stage in ("stg", "staging", "prod", "prd", "production"):
                    self.assertEqual(
                        pin.resolve(mode="legacy", stage=stage), {"mode": "legacy"}
                    )
                for stage in ("", "unknown"):
                    with self.assertRaises(ValueError):
                        pin.resolve(mode="legacy", stage=stage)
                path.write_bytes(declaration())
                with self.assertRaises(ValueError):
                    pin.resolve(mode="legacy", stage="stg")

    def test_unknown_mode_rejected(self):
        for mode in ("", "auto", "PINNED", None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                pin.resolve(mode=mode, stage="stg")

    def test_symlink_directory_and_oversized_file_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.write_bytes(declaration())
            link = root / "link"
            link.symlink_to(target)
            broken = root / "broken"
            broken.symlink_to(root / "absent")
            large = root / "large"
            large.write_bytes(declaration() + b" " * 4097)
            for path in (link, broken, root, large):
                with self.subTest(path=path.name), mock.patch.object(
                    pin, "SOURCE_PATH", path
                ):
                    for mode in ("pinned", "legacy"):
                        with self.assertRaises((ValueError, OSError)):
                            pin.resolve(mode=mode, stage="stg")

    def test_cli_returns_only_validated_shell_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source-declaration.json"
            raw = declaration()
            path.write_bytes(raw)
            output, error = io.StringIO(), io.StringIO()
            with mock.patch.object(
                pin, "SOURCE_PATH", path
            ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                self.assertEqual(pin.main(["--mode", "pinned", "--stage", "stg"]), 0)
            self.assertEqual(error.getvalue(), "")
            self.assertEqual(
                output.getvalue().split(),
                [
                    "pinned",
                    "a" * 40,
                    "20261018T000000Z-fixture-build-1",
                    hashlib.sha256(raw).hexdigest(),
                ],
            )

    def test_cli_failure_prints_no_raw_values_or_partial_pin(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source-declaration.json"
            path.write_bytes(declaration(secret="fixture-sensitive-value"))
            output, error = io.StringIO(), io.StringIO()
            with mock.patch.object(
                pin, "SOURCE_PATH", path
            ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                self.assertNotEqual(pin.main(["--mode", "pinned", "--stage", "stg"]), 0)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(error.getvalue().strip(), "source_pin_invalid")

    def test_cli_legacy_fields_have_no_fabricated_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            with mock.patch.object(
                pin, "SOURCE_PATH", Path(temp) / "absent"
            ), contextlib.redirect_stdout(output):
                self.assertEqual(pin.main(["--mode", "legacy", "--stage", "prod"]), 0)
            self.assertEqual(output.getvalue().split(), ["legacy", "-", "-", "-"])

    def test_cli_has_no_caller_selected_path(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(
            SystemExit
        ) as failure:
            pin.main(["--mode", "pinned", "--stage", "stg", "--path", "/tmp/other"])
        self.assertNotEqual(failure.exception.code, 0)

    def test_import_has_no_runtime_side_effects(self):
        self.assertEqual(
            Path(pin.SOURCE_PATH), Path("/etc/ansible-pull/source-declaration.json")
        )
        self.assertFalse(hasattr(pin, "subprocess"))


if __name__ == "__main__":
    unittest.main()
