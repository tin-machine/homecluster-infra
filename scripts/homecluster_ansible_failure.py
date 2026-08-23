from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

TASK_RE = re.compile(r"^TASK \[(?P<task>.+?)\](?:\s+\*+)?$")
FAILED_HOST_RE = re.compile(r"^(?:fatal|failed): \[(?P<host>[^\]]+)\]")
ORIGIN_RE = re.compile(r"^Origin:\s+(?P<origin>.+)$")
ERROR_RE = re.compile(r"^\[ERROR\]:\s*(?P<message>.+)$")
RC_RE = re.compile(r'"rc"\s*:\s*(-?\d+)')

MAX_FIELD = 500
MAX_DIAGNOSTICS = 20


@dataclass(frozen=True)
class AnsibleFailure:
    task: str = ""
    host: str = ""
    origin: str = ""
    message: str = ""
    rc: str = ""


def _bounded(value: object, limit: int = MAX_FIELD) -> str:
    text = " ".join(str(value).replace("\x00", "").split())
    return text[:limit]


def _json_payload(line: str) -> dict[str, object]:
    if "=>" not in line:
        return {}
    raw = line.split("=>", 1)[1].strip()
    if not raw.startswith("{"):
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_ansible_failure(stdout: str, stderr: str = "") -> AnsibleFailure:
    task = ""
    host = ""
    origin = ""
    message = ""
    rc = ""

    for raw in (stdout + "\n" + stderr).splitlines():
        line = raw.strip()
        if not line:
            continue
        task_match = TASK_RE.match(line)
        if task_match:
            task = _bounded(task_match.group("task"), 240)
            continue
        origin_match = ORIGIN_RE.match(line)
        if origin_match:
            origin = _bounded(origin_match.group("origin"), 300)
            continue
        error_match = ERROR_RE.match(line)
        if error_match:
            message = _bounded(error_match.group("message"))
            continue
        host_match = FAILED_HOST_RE.match(line)
        if host_match:
            host = _bounded(host_match.group("host"), 120)
            payload = _json_payload(line)
            if payload:
                payload_message = payload.get("msg")
                if isinstance(payload_message, str) and payload_message.strip():
                    message = _bounded(payload_message)
                payload_rc = payload.get("rc")
                if isinstance(payload_rc, int):
                    rc = str(payload_rc)
                elif isinstance(payload_rc, str) and re.fullmatch(r"-?\d+", payload_rc.strip()):
                    rc = payload_rc.strip()
            if not rc:
                rc_match = RC_RE.search(line)
                if rc_match:
                    rc = rc_match.group(1)

    return AnsibleFailure(task=task, host=host, origin=origin, message=message, rc=rc)


def failure_diagnostics(
    stdout: str,
    stderr: str = "",
    *,
    stage: str,
    mutation_committed: bool,
    power_cycle_started: bool,
    next_check_id: str = "",
) -> list[str]:
    failure = parse_ansible_failure(stdout, stderr)
    values: Iterable[str] = (
        f"ansible_failure_stage={_bounded(stage, 120)}",
        f"ansible_failed_task={failure.task}" if failure.task else "",
        f"ansible_failed_host={failure.host}" if failure.host else "",
        f"ansible_failed_origin={failure.origin}" if failure.origin else "",
        f"ansible_error={failure.message}" if failure.message else "",
        f"ansible_failed_rc={failure.rc}" if failure.rc else "",
        f"runtime_mutation_committed={'true' if mutation_committed else 'false'}",
        f"power_cycle_started={'true' if power_cycle_started else 'false'}",
        f"ansible_next_check_id={_bounded(next_check_id, 120)}" if next_check_id else "",
    )
    return [item for item in values if item][:MAX_DIAGNOSTICS]
