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

# A decision is the contract: every invocation prints exactly one JSON object
# and exits 0. Unparseable stdin (a truncated payload, a non-JSON body, a bare
# array) makes jq fail, and under `set -e` that killed the script at the first
# extraction with exit 5 and EMPTY stdout, which is not a decision at all. It
# also meant `printf ''` answered `proceed` while `printf 'not json'` answered
# nothing, so the same "nothing to inspect" case behaved two different ways.
# Proceed matches the no-tool-name path below: there is nothing to inspect, and
# this hook's only available failure mode is a spurious block.
if ! printf '%s' "$INPUT" | jq -e 'type == "object"' >/dev/null 2>&1; then
  echo '{"decision": "proceed"}'
  exit 0
fi

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
# A leading "[" is not enough. The value must actually parse as a JSON array,
# which is the bar the library itself uses, and which the DeepSeek Harness
# adapter already carries. Testing only for a leading "[" blocked legitimate
# bracketed prose such as "[1, 2] and [3, 4]" inside a writeFile content field,
# leaving a documentation-writing agent no way to ship its own output except by
# corrupting it.
STRINGIFIED=$(printf '%s' "$ARGS_JSON" | jq -r '
  [paths(type == "string") as $p
  | select((getpath($p) | type) == "string")
  | select(getpath($p) | startswith("["))
  | select((try (getpath($p) | fromjson | type) catch null) == "array")
  | ($p | join("."))]
  | join(", ")
')
if [ -n "$STRINGIFIED" ]; then
  ISSUES="$ISSUES stringified arrays in: $STRINGIFIED"
fi

# Pattern 5: markdown auto-links in string values
# The link text must equal the URL's own path component, which is what makes
# this a leak from the chat distribution rather than a real link. The URL may
# be a bare host or host-plus-path, matching the library's own regex, so
# `[notes.md](http://notes.md)` and `[notes.md](http://host/notes.md)` both
# match while `[click](https://example.com)` does not. The value need not be
# only a path: a leading directory is fine, hence the unanchored prefix.
# The `\1` backreference is the whole point: without it the pattern matched ANY
# markdown link, and a documentation agent writing `see [click](https://...)`
# was blocked on every call.
AUTOLINKS=$(printf '%s' "$ARGS_JSON" | jq -r '
  [paths(type == "string") as $p
  | select((getpath($p) | type) == "string")
  | select(getpath($p) | test("\\[([^\\]]+)\\]\\(https?://(?:[^/]+/)?\\1\\)"))
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
