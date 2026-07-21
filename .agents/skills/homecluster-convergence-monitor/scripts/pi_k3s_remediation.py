#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--mode", choices=("json", "text"), default="text")
    return parser.parse_args()


def read_object(stream: Any) -> dict[str, Any]:
    value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("status result must be a JSON object")
    return value


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def candidate_keys(result: dict[str, Any]) -> list[str]:
    ai = result.get("ai_analysis")
    if not isinstance(ai, dict):
        ai = {}

    keys: list[str] = []
    case_id = ai.get("case_id")
    if isinstance(case_id, str) and case_id and case_id != "NEW_CASE":
        keys.append(f"case:{case_id}")

    for item in string_list(ai.get("secondary_case_ids")):
        keys.append(f"case:{item}")
    for item in string_list(ai.get("rule_candidates")):
        keys.append(f"case:{item}")
    for item in string_list(result.get("issues")):
        keys.append(f"issue:{item}")
    for item in string_list(result.get("diagnostic_triggers")):
        keys.append(f"trigger:{item}")

    deduplicated: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key not in seen:
            seen.add(key)
            deduplicated.append(key)
    return deduplicated


def resolve_remediation(
    result: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, Any]:
    version = catalog.get("version")
    if not isinstance(version, str):
        version = "unknown"

    mappings = catalog.get("mappings")
    if not isinstance(mappings, dict):
        mappings = {}

    base_url = os.environ.get("HOMECLUSTER_K3S_DOC_BASE_URL", "").strip()
    if not base_url:
        catalog_base = catalog.get("document_base_url")
        base_url = catalog_base if isinstance(catalog_base, str) else ""
    base_url = base_url.rstrip("/")

    for key in candidate_keys(result):
        entry = mappings.get(key)
        if not isinstance(entry, dict):
            continue
        remediation_id = entry.get("id")
        title = entry.get("title")
        path = entry.get("path")
        if not all(isinstance(value, str) and value for value in (remediation_id, title, path)):
            continue
        url = f"{base_url}/{path.lstrip('/')}" if base_url else path
        return {
            "status": "matched",
            "match_key": key,
            "id": remediation_id,
            "title": title,
            "url": url,
            "catalog_version": version,
        }

    return {
        "status": "none",
        "match_key": "",
        "id": "",
        "title": "",
        "url": "",
        "catalog_version": version,
    }


def scalar(value: Any, default: str = "none") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def joined(value: Any, separator: str = ",") -> str:
    items = string_list(value)
    return separator.join(items) if items else "none"


def format_text(result: dict[str, Any]) -> str:
    ai = result.get("ai_analysis")
    if not isinstance(ai, dict):
        ai = {}
    remediation = result.get("remediation")
    if not isinstance(remediation, dict):
        remediation = {}

    lines = [
        f"status={scalar(result.get('status'), 'unknown')}",
        f"terminal={scalar(result.get('terminal'), 'true')}",
        f"reason={scalar(result.get('reason'), 'unknown')}",
        f"generated_at={scalar(result.get('generated_at'), 'unknown')}",
        f"target_resolution={scalar(result.get('target_resolution'), 'unresolved')}",
        f"target_resolution_reason={scalar(result.get('target_resolution_reason'), 'unknown')}",
        f"target_inventory_path={scalar(result.get('target_inventory_path'))}",
        f"target_control_group={scalar(result.get('target_control_group'))}",
        f"target_agent_group={scalar(result.get('target_agent_group'))}",
        f"target_control_host={scalar(result.get('target_control_host'))}",
        f"target_node_hosts={joined(result.get('target_node_hosts'))}",
        f"nodes_ready={scalar(result.get('nodes_ready'), '0')}/{scalar(result.get('nodes_total'), '0')}",
        f"node_pressure={scalar(result.get('node_pressure'), '0')}",
        f"non_running_pods={scalar(result.get('non_running_pods'), '0')}",
        f"running_not_ready_pods={scalar(result.get('running_not_ready_pods'), '0')}",
        f"node_exporter_ready={scalar(result.get('node_exporter_ready'), '0')}/{scalar(result.get('node_exporter_desired'), '0')}",
        f"issues={joined(result.get('issues'))}",
        f"signals={scalar(result.get('signals'), '0')}",
        f"diagnostics_status={scalar(result.get('diagnostics_status'), 'unknown')}",
        f"diagnostic_triggers={joined(result.get('diagnostic_triggers'))}",
        f"not_ready_nodes={joined(result.get('not_ready_nodes'))}",
        f"diagnostic_failures={scalar(result.get('diagnostic_failures'), '0')}",
    ]

    for index, finding in enumerate(string_list(result.get("diagnostic_findings")), start=1):
        lines.append(f"diagnostic_{index}={finding}")

    lines.extend(
        [
            f"ai_analysis_status={scalar(ai.get('status'), 'unknown')}",
            f"ai_analysis_reason={scalar(ai.get('reason'), 'unknown')}",
            f"ai_case_id={scalar(ai.get('case_id'))}",
            f"ai_failure_class={scalar(ai.get('failure_class'))}",
            f"ai_confidence={scalar(ai.get('confidence'), 'low')}",
            f"ai_summary={scalar(ai.get('summary'))}",
            f"ai_rule_candidates={joined(ai.get('rule_candidates'))}",
            f"ai_secondary_cases={joined(ai.get('secondary_case_ids'))}",
            f"ai_evidence_refs={joined(ai.get('evidence_refs'))}",
            f"ai_recommended_checks={joined(ai.get('recommended_check_ids'))}",
            f"ai_new_case_signals={joined(ai.get('new_case_signals'), ' | ')}",
            f"ai_model={scalar(ai.get('model'))}",
            f"ai_case_library_version={scalar(ai.get('case_library_version'), 'unknown')}",
            f"ai_operator_gate_required={scalar(ai.get('operator_gate_required'), 'true')}",
            f"ai_automatic_repair_allowed={scalar(ai.get('automatic_repair_allowed'), 'false')}",
            f"remediation_status={scalar(remediation.get('status'), 'none')}",
            f"remediation_match_key={scalar(remediation.get('match_key'))}",
            f"remediation_id={scalar(remediation.get('id'))}",
            f"remediation_title={scalar(remediation.get('title'))}",
            f"remediation_url={scalar(remediation.get('url'))}",
            f"remediation_catalog_version={scalar(remediation.get('catalog_version'), 'unknown')}",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    result = read_object(sys.stdin)
    catalog = read_object(Path(args.catalog).open(encoding="utf-8"))
    result["remediation"] = resolve_remediation(result, catalog)

    if args.mode == "json":
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(format_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
