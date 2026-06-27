#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  opencode_implementation_run.sh --task TEXT [options]

Options:
  --task TEXT             Task prompt passed to opencode run.
  --model MODEL           OpenCode model, e.g. local-gemma4/gemma-4-12b-it-qat-q4_0.gguf.
  --config PATH           OPENCODE_CONFIG path.
  --agent NAME            OpenCode agent name. Defaults to OPENCODE_AGENT or homecluster-ansible-patch.
  --timeout SECONDS       Timeout for opencode run. Defaults to OPENCODE_TIMEOUT or 1800.
  --edit-only             Skip validation inside the OpenCode run.
  --repair-json PATH      Append a compact validation failure JSON object for a repair run.
  --no-expect-diff        Do not fail when OpenCode leaves no git diff.
  --skip-validation       Do not run opencode_validation_gate.sh after OpenCode exits.
  -h, --help              Show this help.

The script prints one compact JSON object and writes raw logs under /tmp. It treats finish=length,
zero-diff implementation runs, and failed/missing validation when validation is enabled as hard
failures. Tool permission enforcement is delegated to project opencode.json.
USAGE
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  echo '{"ok":false,"failed_step":"repo-root","exit_code":128,"summary":"not inside a git repository","session_id":"","log_dir":"","commands_run":[],"commands_not_run":[]}'
  exit 128
fi
cd "$repo_root"

task=""
model="${OPENCODE_MODEL:-}"
config_path="${OPENCODE_CONFIG:-}"
agent="${OPENCODE_AGENT:-homecluster-ansible-patch}"
timeout_seconds="${OPENCODE_TIMEOUT:-1800}"
expect_diff=1
run_validation=1
edit_only=0
repair_json_path=""

while (($# > 0)); do
  case "$1" in
    --task)
      task="${2:-}"
      shift 2
      ;;
    --model)
      model="${2:-}"
      shift 2
      ;;
    --config)
      config_path="${2:-}"
      shift 2
      ;;
    --agent)
      agent="${2:-}"
      shift 2
      ;;
    --timeout)
      timeout_seconds="${2:-}"
      shift 2
      ;;
    --edit-only)
      edit_only=1
      run_validation=0
      shift
      ;;
    --repair-json)
      repair_json_path="${2:-}"
      shift 2
      ;;
    --no-expect-diff)
      expect_diff=0
      shift
      ;;
    --skip-validation)
      run_validation=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 64
      ;;
  esac
done

if [[ -z "$task" ]]; then
  echo "missing required --task" >&2
  usage >&2
  exit 64
fi
if [[ -z "${OPENCODE_IMPLEMENTATION_STUB_OUTPUT:-}" && -z "$model" ]]; then
  echo "missing --model or OPENCODE_MODEL" >&2
  usage >&2
  exit 64
fi
if ! [[ "$timeout_seconds" =~ ^[0-9]+$ ]] || [[ "$timeout_seconds" -le 0 ]]; then
  echo "invalid --timeout: $timeout_seconds" >&2
  exit 64
fi
if [[ -n "$repair_json_path" && ! -f "$repair_json_path" ]]; then
  echo "repair JSON file not found: $repair_json_path" >&2
  exit 64
fi

if [[ "$edit_only" == "1" ]]; then
  task="${task}

Edit-only mode:
Codex will run validation after this OpenCode run."
fi

if [[ -n "$repair_json_path" ]]; then
  repair_json="$(python3 - "$repair_json_path" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8", errors="replace") as handle:
    data = json.load(handle)

compact = {
    "ok": data.get("ok"),
    "failed_step": data.get("failed_step"),
    "summary": data.get("summary"),
    "commands_not_run": data.get("commands_not_run", []),
}
print(json.dumps(compact, ensure_ascii=False, sort_keys=True))
PY
)"
  task="${task}

Repair input:
${repair_json}

Before repairing, re-read the current target file from disk. Do not edit from an old snapshot or an
oldString captured before the failed validation. Make the smallest source edit that fixes the compact
failure JSON above."
fi

log_dir="$(mktemp -d "${TMPDIR:-/tmp}/opencode-implementation-run.XXXXXX")"
events_log="${log_dir}/opencode-events.jsonl"
session_export_log="${log_dir}/session-export.json"
validation_log="${log_dir}/validation.json"
commands_run_file="${log_dir}/commands_run.txt"
commands_not_run_file="${log_dir}/commands_not_run.txt"

commands_run=()
commands_not_run=()
opencode_exit=0

if [[ -n "${OPENCODE_IMPLEMENTATION_STUB_OUTPUT:-}" ]]; then
  printf '%s\n' "$OPENCODE_IMPLEMENTATION_STUB_OUTPUT" >"$events_log"
  commands_run+=("stub: OPENCODE_IMPLEMENTATION_STUB_OUTPUT")
else
  opencode_cmd=(opencode run --format json --model "$model")
  if [[ -n "$agent" ]]; then
    opencode_cmd+=(--agent "$agent")
  fi
  opencode_cmd+=("$task")
  commands_run+=("opencode run --format json --model ${model} --agent ${agent}")

  set +e
  if [[ -n "$config_path" ]]; then
    OPENCODE_CONFIG="$config_path" timeout "${timeout_seconds}s" "${opencode_cmd[@]}" >"$events_log" 2>&1
  else
    timeout "${timeout_seconds}s" "${opencode_cmd[@]}" >"$events_log" 2>&1
  fi
  opencode_exit=$?
  set -e
fi

session_id="$(
  python3 - "$events_log" <<'PY'
import json
import sys

def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"sessionID", "sessionId", "session_id"} and isinstance(item, str):
                yield item
            if key == "session" and isinstance(item, dict):
                candidate = item.get("id") or item.get("sessionID") or item.get("sessionId")
                if isinstance(candidate, str):
                    yield candidate
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)

seen = []
with open(sys.argv[1], encoding="utf-8", errors="replace") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for candidate in walk(event):
            if candidate not in seen:
                seen.append(candidate)

print(seen[-1] if seen else "")
PY
)"

if [[ -n "$session_id" ]]; then
  if [[ -n "${OPENCODE_IMPLEMENTATION_STUB_SESSION_EXPORT:-}" ]]; then
    printf '%s\n' "$OPENCODE_IMPLEMENTATION_STUB_SESSION_EXPORT" >"$session_export_log"
    commands_run+=("stub: OPENCODE_IMPLEMENTATION_STUB_SESSION_EXPORT")
  else
    set +e
    opencode export "$session_id" >"$session_export_log" 2>&1
    export_exit=$?
    set -e
    commands_run+=("opencode export ${session_id}")
    if [[ "$export_exit" -ne 0 ]]; then
      printf 'opencode export failed with exit %s\n' "$export_exit" >>"$session_export_log"
    fi
  fi
else
  : >"$session_export_log"
  commands_not_run+=("opencode export: no session id found")
fi

emit_result() {
  local ok="$1"
  local failed_step="$2"
  local exit_code="$3"
  local summary="$4"
  local validation_ok="${5:-}"

  if [[ "${#commands_run[@]}" -gt 0 ]]; then
    printf '%s\n' "${commands_run[@]}" >"$commands_run_file"
  else
    : >"$commands_run_file"
  fi
  if [[ "${#commands_not_run[@]}" -gt 0 ]]; then
    printf '%s\n' "${commands_not_run[@]}" >"$commands_not_run_file"
  else
    : >"$commands_not_run_file"
  fi

  OK="$ok" \
  FAILED_STEP="$failed_step" \
  EXIT_CODE="$exit_code" \
  SUMMARY="$summary" \
  SESSION_ID="$session_id" \
  LOG_DIR="$log_dir" \
  VALIDATION_OK="$validation_ok" \
  python3 - "$commands_run_file" "$commands_not_run_file" <<'PY'
import json
import os
import sys

def read_lines(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()

ok = os.environ["OK"] == "true"
validation_ok_raw = os.environ.get("VALIDATION_OK", "")
validation_ok = None if validation_ok_raw == "" else validation_ok_raw == "true"
result = {
    "ok": ok,
    "failed_step": None if ok else os.environ["FAILED_STEP"],
    "exit_code": int(os.environ["EXIT_CODE"]),
    "summary": os.environ["SUMMARY"],
    "session_id": os.environ.get("SESSION_ID", ""),
    "log_dir": os.environ["LOG_DIR"],
    "validation_ok": validation_ok,
    "commands_run": read_lines(sys.argv[1]),
    "commands_not_run": read_lines(sys.argv[2]),
}
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
PY
}

if [[ "$opencode_exit" -ne 0 ]]; then
  emit_result false opencode-run "$opencode_exit" "opencode run exited nonzero"
  exit "$opencode_exit"
fi

if grep -Eiq '"finish"[[:space:]]*:[[:space:]]*"length"|finish[=:][[:space:]]*length' "$events_log" "$session_export_log"; then
  emit_result false finish-length 1 "OpenCode stopped because the response hit the output limit"
  exit 1
fi

diff_shortstat="$(git diff --shortstat --)"
if [[ "$expect_diff" == "1" && -z "$diff_shortstat" ]]; then
  emit_result false diff-gate 1 "OpenCode completed without producing a git diff"
  exit 1
fi

validation_ok=""
if [[ "$run_validation" == "1" ]]; then
  set +e
  ./.agents/skills/homecluster-ansible-implementer/scripts/opencode_validation_gate.sh >"$validation_log" 2>&1
  validation_exit=$?
  set -e
  commands_run+=("./.agents/skills/homecluster-ansible-implementer/scripts/opencode_validation_gate.sh")
  validation_ok="$(
    python3 - "$validation_log" <<'PY'
import json
import sys

ok = False
with open(sys.argv[1], encoding="utf-8", errors="replace") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and "ok" in event:
            ok = event["ok"] is True

print("true" if ok else "false")
PY
  )"
  if [[ "$validation_exit" -ne 0 || "$validation_ok" != "true" ]]; then
    emit_result false validation-gate "${validation_exit:-1}" "validation gate did not return ok=true" "$validation_ok"
    exit 1
  fi
else
  if [[ "$edit_only" == "1" ]]; then
    commands_not_run+=("./.agents/skills/homecluster-ansible-implementer/scripts/opencode_validation_gate.sh: skipped by --edit-only")
  else
    commands_not_run+=("./.agents/skills/homecluster-ansible-implementer/scripts/opencode_validation_gate.sh: skipped by --skip-validation")
  fi
fi

emit_result true "" 0 "OpenCode implementation run passed wrapper gates" "$validation_ok"
