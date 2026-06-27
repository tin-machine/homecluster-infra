#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  opencode_preflight_gate.sh --plan PATH --task TEXT [--model PROVIDER/MODEL] [--config PATH] [--timeout SECONDS]

Runs a read-only OpenCode preflight and fails closed unless the model returns:
  may_proceed=true
  requires_user_confirmation=false
  skill_task_conflicts=[]
  plan_task_conflicts=[]
  plan_skill_conflicts=[]

Environment:
  OPENCODE_MODEL                  Default model when --model is omitted.
  OPENCODE_CONFIG                 Provider config path when --config is omitted.
  OPENCODE_PREFLIGHT_STUB_OUTPUT  Test hook: parse this output instead of running OpenCode.
USAGE
}

plan_path=""
task_text=""
model="${OPENCODE_MODEL:-}"
config_path="${OPENCODE_CONFIG:-}"
timeout_seconds=900

while (($# > 0)); do
  case "$1" in
    --plan)
      plan_path="${2:-}"
      shift 2
      ;;
    --task)
      task_text="${2:-}"
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
    --timeout)
      timeout_seconds="${2:-}"
      shift 2
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

if [[ -z "$plan_path" || -z "$task_text" ]]; then
  echo "--plan and --task are required" >&2
  usage >&2
  exit 64
fi

if [[ ! -f "$plan_path" ]]; then
  echo "plan file not found: $plan_path" >&2
  exit 66
fi

prompt=$(cat <<PROMPT
Use \$homecluster-ansible-implementer.

Preflight only. Do not start implementation.

Task:
$task_text

Plan file:
$plan_path

Read the skill and the plan. Then return exactly one compact JSON object and no markdown.

Required JSON schema:
{
  "skill_task_conflicts": ["string"],
  "plan_task_conflicts": ["string"],
  "plan_skill_conflicts": ["string"],
  "requires_user_confirmation": true,
  "may_proceed": false,
  "blocking_reason": "string",
  "resolution_notes": ["string"]
}

Rules:
- Set may_proceed=false if the task prompt forbids something that the plan asks you to do.
- Set may_proceed=false if the plan asks for live apply, real inventory, runbook edits, private paths
  in public files, or any action this skill forbids, unless the task explicitly resolves it.
- Set requires_user_confirmation=true when operator approval or a policy decision is needed before
  implementation.
- If the task explicitly resolves a plan item by converting it to "do not execute", "report proposed
  wording only", "use placeholders", "use example inventory only", or another source-only
  instruction, do not treat that resolved item as a conflict. Put the resolution in
  resolution_notes instead.
- If every unsafe or conflicting plan item has an explicit source-only resolution in the task, set
  may_proceed=true and requires_user_confirmation=false.
- If may_proceed is true, all three conflict arrays must be empty. Do not include resolved items in
  conflict arrays.
- If any conflict array is non-empty, may_proceed must be false.
- Put every detected conflict in the most specific conflict array. Do not leave all conflict arrays
  empty when may_proceed=false because of conflicting instructions.
- Keep every string short and concrete.
PROMPT
)

if [[ -n "${OPENCODE_PREFLIGHT_STUB_OUTPUT:-}" ]]; then
  raw_output="$OPENCODE_PREFLIGHT_STUB_OUTPUT"
else
  if [[ -z "$model" ]]; then
    echo "--model or OPENCODE_MODEL is required unless OPENCODE_PREFLIGHT_STUB_OUTPUT is set" >&2
    exit 64
  fi
  if ! command -v opencode >/dev/null 2>&1; then
    echo "opencode not found in PATH" >&2
    exit 69
  fi

  opencode_cmd=(opencode run --model "$model" --format json --agent homecluster-read-only "$prompt")
  if [[ -n "$config_path" ]]; then
    raw_output="$(OPENCODE_CONFIG="$config_path" timeout "${timeout_seconds}s" "${opencode_cmd[@]}")"
  else
    raw_output="$(timeout "${timeout_seconds}s" "${opencode_cmd[@]}")"
  fi
fi

printf '%s\n' "$raw_output" | python3 -c '
import json
import re
import sys

raw = sys.stdin.read()
texts = []
for line in raw.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    part = event.get("part")
    if isinstance(part, dict):
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)

candidate = "\n".join(texts).strip() or raw.strip()
match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.S)
if match:
    candidate = match.group(1)
else:
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        candidate = candidate[start:end + 1]

required = {
    "skill_task_conflicts": list,
    "plan_task_conflicts": list,
    "plan_skill_conflicts": list,
    "requires_user_confirmation": bool,
    "may_proceed": bool,
    "blocking_reason": str,
    "resolution_notes": list,
}

def object_has_schema(obj):
    return isinstance(obj, dict) and all(key in obj for key in required)

def decode_schema_object(text):
    decoder = json.JSONDecoder()
    try:
        obj = json.loads(text)
        if object_has_schema(obj):
            return obj
    except json.JSONDecodeError:
        pass

    for match in re.finditer(r"{", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if object_has_schema(obj):
            return obj
    return None

data = decode_schema_object(candidate)
if data is None:
    data = decode_schema_object(raw)
if data is None:
    print("failed to parse preflight JSON with required schema", file=sys.stderr)
    print(candidate[:2000], file=sys.stderr)
    sys.exit(65)

for key, typ in required.items():
    if key not in data:
        print("missing preflight key: %s" % key, file=sys.stderr)
        sys.exit(65)
    if not isinstance(data[key], typ):
        print("preflight key has wrong type: %s" % key, file=sys.stderr)
        sys.exit(65)

has_conflicts = bool(data["skill_task_conflicts"] or data["plan_task_conflicts"] or data["plan_skill_conflicts"])
if data["may_proceed"] and has_conflicts:
    print("inconsistent preflight JSON: may_proceed=true with non-empty conflict arrays", file=sys.stderr)
    print(json.dumps(data, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    sys.exit(65)

print(json.dumps(data, ensure_ascii=False, sort_keys=True))

blocked = (
    not data["may_proceed"]
    or data["requires_user_confirmation"]
    or has_conflicts
)
sys.exit(2 if blocked else 0)
'
