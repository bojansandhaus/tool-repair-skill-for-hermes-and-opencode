#!/usr/bin/env bash
# pre_tool_use.sh: DeepSeek Harness (dsh) PreToolUse hook for tool-call repair.
#
# The harness ships two bridges that run ordinary command hooks at its
# interception seams: `@deepseek-ai/dsh-hooks-claude-code` and
# `@deepseek-ai/dsh-hooks-codex`. Register this script with either one and it
# runs before every tool call.
#
# The seam can block a call but cannot rewrite its arguments: `ToolExecution`
# exposes `arguments` as readonly and `PreToolDecision` has no rewrite variant.
# The harness' own Claude Code bridge says so in its docs, under "PreToolUse
# is partial", and logs a warning if a hook asks for `updatedInput`. So this
# hook does what the seam allows: it detects a repairable argument and denies
# the call with a message naming the exact fields, and the model retries with
# a corrected format on the next turn.
#
# Input: the bridge's stdin payload. Field names are the harness' own, not
# Claude Code's: `tool_name` and `tool_input`, where `tool_input` is the
# already-parsed arguments object. The Claude Code adapter in this repo reads
# `.tool` and `.input`, which the harness never sends, so it cannot be reused
# here.
#
# Install: see this directory's README.md, or ask your agent:
#   "Copy adapters/deepseek-harness/ from
#    github.com/bojansandhaus/tool-repair-skill-for-hermes-and-opencode
#    and register pre_tool_use.sh as a PreToolUse command hook with
#    @deepseek-ai/dsh-hooks-claude-code."

set -euo pipefail

INPUT=$(cat)

# A decision is the contract: every invocation prints exactly one JSON object
# and exits 0. Unparseable stdin (a truncated payload, a non-JSON body, a bare
# array) makes jq fail, and under `set -e` that kills the script at the first
# extraction with exit 5 and EMPTY stdout, which is not a decision. The same
# guard now sits in the Claude Code adapter's hook, for the same reason.
# Proceed matches the no-tool-name path below: nothing to inspect, and this
# hook's only failure mode is a spurious denial.
if ! printf '%s' "$INPUT" | jq -e 'type == "object"' >/dev/null 2>&1; then
  printf '%s\n' '{"decision": "proceed"}'
  exit 0
fi

TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty')
ARGS_JSON=$(printf '%s' "$INPUT" | jq -c '.tool_input // {}')

# No tool name or no arguments: nothing to inspect, let it through.
if [ -z "$TOOL_NAME" ] || [ "$ARGS_JSON" = "null" ]; then
  printf '%s\n' '{"decision": "proceed"}'
  exit 0
fi

ISSUES=""

# Pattern 1: null values for optional fields.
# NOTE: this must be `paths(. == null)`, not `paths(scalars) as $p |
# select(getpath($p) == null)`. paths(scalars) does not emit a path for a null
# value, so the select could never fire and the hook would approve every call
# it was installed to catch.
NULL_FIELDS=$(printf '%s' "$ARGS_JSON" | jq -r '
  [paths(. == null) as $p
  | ($p | join("."))]
  | join(", ")
')
if [ -n "$NULL_FIELDS" ]; then
  ISSUES="$ISSUES null values in: $NULL_FIELDS"
fi

# Pattern 2: stringified JSON arrays.
# A leading "[" is not enough: the value must actually parse as a JSON array,
# which is the bar the library itself uses. Without that second check this
# pattern blocks legitimate bracketed prose such as "[1, 2] and [3, 4]", which
# is content the repair layer is required to leave alone.
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

# Pattern 3: markdown auto-links leaking into a path-shaped value.
# The link text must equal the URL's own path component, which is what makes
# this a leak from the chat distribution rather than a real link. The URL may
# be a bare host or host-plus-path, matching the library's own regex, so
# `[notes.md](http://notes.md)` and `[notes.md](http://host/notes.md)` both
# match while `[click](https://example.com)` does not. The value need not be
# only a path: a leading directory is fine, hence the unanchored prefix.
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
  # Built with jq, not string interpolation: a field name or path containing a
  # quote or backslash would otherwise produce malformed JSON.
  MESSAGE="[tool-repair] Detected likely tool call issue in $TOOL_NAME:$ISSUES. Fix the format and retry. Send proper types: null should be omitted, arrays should be real arrays, not strings."

  jq -nc --arg m "$MESSAGE" '{"decision": "block", "message": $m}'
  exit 0
fi

printf '%s\n' '{"decision": "proceed"}'