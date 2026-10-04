#!/usr/bin/env bash
# post_tool_use.sh — Claude Code PostToolUse hook.
#
# After a tool call succeeds, inspect the arguments for patterns that the
# repair layer would have fixed. Log findings for visibility and build a
# session-level profile of the model's common mistakes.
#
# This is informational — the call already succeeded. The data feeds
# telemetry and helps tune the pre_tool_use hook's block thresholds.
#
# Install:
#   Place in ~/.claude/hooks/ or .claude/hooks/ in your project.
#   Add to claude.json:
#     {
#       "hooks": {
#         "post_tool_use": {
#           "matcher": "*",
#           "command": "bash .claude/hooks/post_tool_use.sh"
#         }
#       }
#     }
#
# Reference: https://code.claude.com/docs/en/hooks

set -euo pipefail

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool // empty')
ARGS_JSON=$(echo "$INPUT" | jq -c '.input // {}')
RESULT_STATUS=$(echo "$INPUT" | jq -r '.result.isError // false')

if [ -z "$TOOL_NAME" ] || [ "$ARGS_JSON" = "null" ]; then
  exit 0
fi

# Log file — scoped to the project directory
LOG_DIR="${CLAUDE_CODE_DIR:-$HOME/.claude}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/tool-repair-telemetry.log"

# Check each pattern
# NOTE: this must be `paths(. == null)`, not `paths(scalars) as $p | select(getpath($p) == null)`.
# paths(scalars) emits a path only for non-null scalars, so the select could
# never fire and this check silently reported no null fields at all. Verified on
# jq 1.8.1: `{"limit":null,"s":"x"} | [paths(scalars)]` is [["s"]], and the
# select returns []. The pre_tool_use.sh hook had the same defect and was fixed
# in v1.0.1; this hook was missed.
NULL_FIELDS=$(echo "$ARGS_JSON" | jq -r '
  [paths(. == null) as $p
  | ($p | join("."))]
  | join(",")
')

STRINGIFIED=$(echo "$ARGS_JSON" | jq -r '
  [paths(type == "string") as $p
  | select(getpath($p) | test("^\\s*\\["))
  | ($p | join("."))]
  | join(",")
')

EMPTY_OBJ=$(echo "$ARGS_JSON" | jq -r '
  [paths as $p
  | select((getpath($p) | type) == "object" and (getpath($p) | length) == 0)
  | ($p | join("."))]
  | join(",")
')

AUTOLINKS=$(echo "$ARGS_JSON" | jq -r '
  [paths(type == "string") as $p
  | select(getpath($p) | test("\\[[^]]+\\]\\(https?://"))
  | ($p | join("."))]
  | join(",")
')

# If any patterns found, log them
PATTERNS=""
[ -n "$NULL_FIELDS" ]   && PATTERNS="$PATTERNS null=($NULL_FIELDS)"
[ -n "$STRINGIFIED" ]   && PATTERNS="$PATTERNS stringified=($STRINGIFIED)"
[ -n "$EMPTY_OBJ" ]     && PATTERNS="$PATTERNS empty_obj=($EMPTY_OBJ)"
[ -n "$AUTOLINKS" ]     && PATTERNS="$PATTERNS autolink=($AUTOLINKS)"

if [ -n "$PATTERNS" ]; then
  TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  echo "[$TIMESTAMP] $TOOL_NAME |$PATTERNS| error=$RESULT_STATUS" >> "$LOG_FILE"
  echo "[tool-repair] $TOOL_NAME had repairable patterns:$PATTERNS" >&2
fi
