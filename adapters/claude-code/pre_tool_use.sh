#!/usr/bin/env bash
# pre_tool_use.sh — Claude Code PreToolUse hook for tool-call repair.
#
# Claude Code's PreToolUse hooks can block a tool call but cannot modify
# arguments. This hook:
#   1. Reads the upcoming tool call from stdin
#   2. Checks for the four common JSON formatting mistakes
#   3. If a repairable pattern is detected, blocks with a helpful message
#      so the model can self-correct
#   4. Otherwise lets it proceed
#
# Install:
#   Place in ~/.claude/hooks/ or .claude/hooks/ in your project.
#   Add to claude.json:
#     {
#       "hooks": {
#         "pre_tool_use": {
#           "matcher": "*",
#           "command": "bash .claude/hooks/pre_tool_use.sh"
#         }
#       }
#     }
#
#   The detectors are shared with the other adapters and live in
#   adapters/shared/detect.sh, so copy the directory rather than this file
#   alone:
#     mkdir -p .claude/hooks
#     cp -r adapters/claude-code adapters/shared .claude/hooks/
#     bash .claude/hooks/claude-code/pre_tool_use.sh
#   Set TOOL_REPAIR_SHARED_DIR to adapters/shared if you keep the two apart.
#
# Reference: https://code.claude.com/docs/en/hooks

set -euo pipefail

# Read the full JSON input from stdin
INPUT=$(cat)

# The detectors live in one copy at adapters/shared/detect.sh, shared with the
# Claude Code post hook and the DeepSeek Harness adapter. This file is resolved
# from its own location rather than the working directory, so the hook behaves
# the same however it is invoked; TOOL_REPAIR_SHARED_DIR overrides it for an
# install that flattens the layout.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SHARED_DIR="${TOOL_REPAIR_SHARED_DIR:-$SCRIPT_DIR/../shared}"
if [ ! -f "$SHARED_DIR/detect.sh" ]; then
  # Nothing to inspect without the detectors, and a hook whose only failure
  # mode is a spurious block must not invent one. Say so, on stderr, rather
  # than failing silently.
  echo '{"decision": "proceed"}'
  printf '%s\n' "[tool-repair] detectors not found at $SHARED_DIR/detect.sh; nothing was inspected" >&2
  exit 0
fi
# shellcheck source=../shared/detect.sh
. "$SHARED_DIR/detect.sh"

# A decision is the contract: every invocation prints exactly one JSON object
# and exits 0. Unparseable stdin (a truncated payload, a non-JSON body, a bare
# array) makes jq fail, and under `set -e` that killed the script at the first
# extraction with exit 5 and EMPTY stdout, which is not a decision at all. It
# also meant `printf ''` answered `proceed` while `printf 'not json'` answered
# nothing, so the same "nothing to inspect" case behaved two different ways.
# Proceed matches the no-tool-name path below: there is nothing to inspect, and
# this hook's only available failure mode is a spurious block.
if ! tr_payload_is_object "$INPUT"; then
  echo '{"decision": "proceed"}'
  exit 0
fi

# Claude Code's envelope: `tool` and `input`.
tr_detect "$INPUT" tool input

# If no tool name, let it through
if [ -z "$TR_TOOL_NAME" ]; then
  echo '{"decision": "proceed"}'
  exit 0
fi

if [ -n "$TR_ISSUES" ]; then
  # Build the response with jq, not string interpolation: a field name or path
  # containing a quote or backslash would otherwise produce malformed JSON.
  MESSAGE="[tool-repair] Detected likely tool call issue in $TR_TOOL_NAME:$TR_ISSUES. Fix the format and retry. Send proper types — null should be omitted, arrays should be real arrays, not strings."

  jq -nc --arg m "$MESSAGE" '{"decision": "block", "message": $m}'
  exit 0
fi

echo '{"decision": "proceed"}'
