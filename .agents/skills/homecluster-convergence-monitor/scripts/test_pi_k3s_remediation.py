from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("pi_k3s_remediation.py")
SPEC = importlib.util.spec_from_file_location("pi_k3s_remediation", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
remediation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remediation)


CATALOG = {
    "version": "test.1",
    "document_base_url": "https://example.invalid/repo/blob/main",
    "mappings": {
        "case:k3s_agent_registration_auth_mismatch": {
            "id": "auth-mismatch",
            "title": "Authentication mismatch",
            "path": "docs/troubleshooting/auth.md",
        },
        "issue:nodes_not_ready": {
            "id": "node-not-ready",
            "title": "Node NotReady",
            "path": "docs/troubleshooting/node.md",
        },
    },
}


class RemediationTests(unittest.TestCase):
    def test_primary_case_has_highest_priority(self) -> None:
        result = {
            "issues": ["nodes_not_ready"],
            "ai_analysis": {
                "case_id": "k3s_agent_registration_auth_mismatch",
                "secondary_case_ids": [],
                "rule_candidates": [],
            },
        }
        resolved = remediation.resolve_remediation(result, CATALOG)
        self.assertEqual(resolved["status"], "matched")
        self.assertEqual(resolved["match_key"], "case:k3s_agent_registration_auth_mismatch")
        self.assertTrue(resolved["url"].endswith("docs/troubleshooting/auth.md"))

    def test_issue_is_used_without_classifier(self) -> None:
        result = {
            "issues": ["nodes_not_ready"],
            "diagnostic_triggers": [],
            "ai_analysis": {},
        }
        resolved = remediation.resolve_remediation(result, CATALOG)
        self.assertEqual(resolved["id"], "node-not-ready")
        self.assertEqual(resolved["match_key"], "issue:nodes_not_ready")

    def test_unknown_case_falls_through_to_issue(self) -> None:
        result = {
            "issues": ["nodes_not_ready"],
            "ai_analysis": {
                "case_id": "NEW_CASE",
                "secondary_case_ids": ["unknown"],
                "rule_candidates": [],
            },
        }
        resolved = remediation.resolve_remediation(result, CATALOG)
        self.assertEqual(resolved["id"], "node-not-ready")

    def test_no_mapping_returns_none(self) -> None:
        resolved = remediation.resolve_remediation(
            {"issues": [], "diagnostic_triggers": [], "ai_analysis": {}}, CATALOG
        )
        self.assertEqual(resolved["status"], "none")
        self.assertEqual(resolved["url"], "")

    def test_text_output_contains_remediation_url(self) -> None:
        result = {
            "status": "converging",
            "terminal": True,
            "reason": "cluster_converging",
            "issues": ["nodes_not_ready"],
            "ai_analysis": {"status": "unknown"},
            "remediation": {
                "status": "matched",
                "match_key": "issue:nodes_not_ready",
                "id": "node-not-ready",
                "title": "Node NotReady",
                "url": "https://example.invalid/docs/node.md",
                "catalog_version": "test.1",
            },
        }
        output = remediation.format_text(result)
        self.assertIn("remediation_status=matched", output)
        self.assertIn("remediation_url=https://example.invalid/docs/node.md", output)


if __name__ == "__main__":
    unittest.main()
