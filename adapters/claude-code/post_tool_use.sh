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
#   The detectors are shared with the pre_tool_use hook and the DeepSeek
#   Harness adapter, so copy the directory rather than this file alone:
#     mkdir -p .claude/hooks
#     cp -r adapters/claude-code adapters/shared .claude/hooks/
#     bash .claude/hooks/claude-code/post_tool_use.sh
#   Set TOOL_REPAIR_SHARED_DIR to adapters/shared if you keep the two apart.
#
# Reference: https://code.claude.com/docs/en/hooks

set -euo pipefail

INPUT=$(cat)

# The detectors live in one copy at adapters/shared/detect.sh. Three copies of
# these selects used to be maintained by hand and had already drifted twice:
# v1.0.1 fixed the null detector in two of them and missed this one, so the
# telemetry this hook exists to collect reported no null fields at all;
# v1.0.5 found the stringified-array and auto-link selects had diverged the
# same way. See adapters/shared/detect.sh for the history and the reason the
# null select is `paths(. == null)`.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SHARED_DIR="${TOOL_REPAIR_SHARED_DIR:-$SCRIPT_DIR/../shared}"
if [ ! -f "$SHARED_DIR/detect.sh" ]; then
  printf '%s\n' "[tool-repair] detectors not found at $SHARED_DIR/detect.sh; nothing was inspected" >&2
  exit 0
fi
# shellcheck source=../shared/detect.sh
. "$SHARED_DIR/detect.sh"

# An unparseable body is not a tool call, so there is nothing to log. v1.0.5
# put this guard's twin in both pre-tool hooks, where a missing decision was a
# broken contract; here it was a crash under `set -e` with exit 5 and stderr
# tracebacks in the session log for empty or truncated payloads.
if ! tr_payload_is_object "$INPUT"; then
  exit 0
fi

tr_detect "$INPUT" tool input
TOOL_NAME="$TR_TOOL_NAME"
RESULT_STATUS=$(tr_jq "$INPUT" '.result.isError // false')

if [ -z "$TOOL_NAME" ]; then
  exit 0
fi

# Log file — scoped to the project directory
LOG_DIR="${CLAUDE_CODE_DIR:-$HOME/.claude}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/tool-repair-telemetry.log"

# TR_ROWS carries one `pattern=fields` row per detector that fired. This hook
# reports all four patterns, including empty_object: a log line costs nothing
# and the schema that decides whether an empty object is a defect belongs to
# the executor. The blocking hooks deliberately leave that one out, because
# blocking on `{"options":{}}` without a schema is a false positive.
PATTERNS=""
while IFS='=' read -r name fields; do
  [ -n "$name" ] || continue
  PATTERNS="$PATTERNS $name=($fields)"
done <<< "$TR_ROWS"

if [ -n "$PATTERNS" ]; then
  TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  echo "[$TIMESTAMP] $TOOL_NAME |$PATTERNS| error=$RESULT_STATUS" >> "$LOG_FILE"
  echo "[tool-repair] $TOOL_NAME had repairable patterns:$PATTERNS" >&2
fi
