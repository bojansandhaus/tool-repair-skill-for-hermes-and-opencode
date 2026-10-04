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
# Reference: https://code.claude.com/docs/en/hooks

set -euo pipefail

# Read the full JSON input from stdin
INPUT=$(cat)

# Extract tool name and arguments
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool // empty')
ARGS_JSON=$(printf '%s' "$INPUT" | jq -c '.input // {}')

# If no tool name or empty args, let it through
if [ -z "$TOOL_NAME" ] || [ "$ARGS_JSON" = "null" ]; then
  echo '{"decision": "proceed"}'
  exit 0
fi

# Check each argument for common patterns
ISSUES=""

# Pattern 1: null values for optional fields
# NOTE: this must be `paths(. == null)`, not `paths(scalars) as $p | select(getpath($p) == null)`.
# paths(scalars) does not emit a path for a null value, so the select could never
# fire and this hook silently approved every call it was installed to catch.
NULL_FIELDS=$(printf '%s' "$ARGS_JSON" | jq -r '
  [paths(. == null) as $p
  | ($p | join("."))]
  | join(", ")
')
if [ -n "$NULL_FIELDS" ]; then
  ISSUES="$ISSUES null values in: $NULL_FIELDS"
fi

# Pattern 2: stringified JSON arrays
STRINGIFIED=$(printf '%s' "$ARGS_JSON" | jq -r '
  [paths(type == "string") as $p
  | select((getpath($p) | type) == "string")
  | select(getpath($p) | test("^\\s*\\["))
  | ($p | join("."))]
  | join(", ")
')
if [ -n "$STRINGIFIED" ]; then
  ISSUES="$ISSUES stringified arrays in: $STRINGIFIED"
fi

# Pattern 5: markdown auto-links in string values
AUTOLINKS=$(printf '%s' "$ARGS_JSON" | jq -r '
  [paths(type == "string") as $p
  | select((getpath($p) | type) == "string")
  | select(getpath($p) | test("\\[[^]]+\\]\\(https?://"))
  | ($p | join("."))]
  | join(", ")
')
if [ -n "$AUTOLINKS" ]; then
  ISSUES="$ISSUES markdown auto-links in: $AUTOLINKS"
fi

if [ -n "$ISSUES" ]; then
  # Build the response with jq, not string interpolation: a field name or path
  # containing a quote or backslash would otherwise produce malformed JSON.
  MESSAGE="[tool-repair] Detected likely tool call issue in $TOOL_NAME:$ISSUES. Fix the format and retry. Send proper types — null should be omitted, arrays should be real arrays, not strings."

  jq -nc --arg m "$MESSAGE" '{"decision": "block", "message": $m}'
  exit 0
fi

echo '{"decision": "proceed"}'
