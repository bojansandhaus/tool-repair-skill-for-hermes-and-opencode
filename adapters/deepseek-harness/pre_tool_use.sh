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
# Claude Code's: `tool_name` and `tool_input`. The detectors themselves are the
# ones the Claude Code adapter uses — adapters/shared/detect.sh — so the two
# adapters cannot drift apart again; only the envelope key names and the
# decision wording are per-framework.
#
# Install: see this directory's README.md, or ask your agent:
#   "Copy adapters/deepseek-harness/ and adapters/shared/ from
#    github.com/bojansandhaus/tool-repair-skill-for-hermes-and-opencode into
#    ~/.dsh/tool-repair/ keeping the directory layout, make
#    ~/.dsh/tool-repair/deepseek-harness/pre_tool_use.sh executable, and
#    register it as a PreToolUse command hook with
#    @deepseek-ai/dsh-hooks-claude-code."

set -euo pipefail

INPUT=$(cat)

# Detectors shared with the Claude Code adapter. Resolved from this file's own
# location so the hook works from any working directory; TOOL_REPAIR_SHARED_DIR
# overrides it for an install that flattens the layout.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SHARED_DIR="${TOOL_REPAIR_SHARED_DIR:-$SCRIPT_DIR/../shared}"
if [ ! -f "$SHARED_DIR/detect.sh" ]; then
  printf '%s\n' '{"decision": "proceed"}'
  printf '%s\n' "[tool-repair] detectors not found at $SHARED_DIR/detect.sh; nothing was inspected" >&2
  exit 0
fi
# shellcheck source=../../shared/detect.sh
. "$SHARED_DIR/detect.sh"

# A decision is the contract: every invocation prints exactly one JSON object
# and exits 0. Unparseable stdin (a truncated payload, a non-JSON body, a bare
# array) makes jq fail, and under `set -e` that kills the script at the first
# extraction with exit 5 and EMPTY stdout, which is not a decision. The same
# guard now sits in the Claude Code adapter's hook, for the same reason.
# Proceed matches the no-tool-name path below: nothing to inspect, and this
# hook's only failure mode is a spurious denial.
if ! tr_payload_is_object "$INPUT"; then
  printf '%s\n' '{"decision": "proceed"}'
  exit 0
fi

# The harness' own envelope: `tool_name` and `tool_input`.
tr_detect "$INPUT" tool_name tool_input

# No tool name: nothing to inspect, let it through.
if [ -z "$TR_TOOL_NAME" ]; then
  printf '%s\n' '{"decision": "proceed"}'
  exit 0
fi

# The Codex bridge does not forward the arguments. It projects them down to a
# single command string, `tool_input: { command: commandOf(exec.arguments) }`,
# so every field other than `command` — `files`, `limit`, `timeout`,
# `filePath` — is gone before this script runs. The command itself is scanned
# (tr_detect reads it explicitly), and the loss of everything else is reported
# here rather than being waved through as though it had been inspected. Without
# this line the hook answers `proceed` on exactly the calls it was installed to
# catch and nobody can tell why.
if [ "$TR_PROJECTED" = "1" ]; then
  printf '%s\n' "[tool-repair] codex bridge projection: only .tool_input.command is visible; files, limit, timeout, filePath and every other field were not inspected" >&2
fi

if [ -n "$TR_ISSUES" ]; then
  # Built with jq, not string interpolation: a field name or path containing a
  # quote or backslash would otherwise produce malformed JSON.
  MESSAGE="[tool-repair] Detected likely tool call issue in $TR_TOOL_NAME:$TR_ISSUES. Fix the format and retry. Send proper types: null should be omitted, arrays should be real arrays, not strings."

  jq -nc --arg m "$MESSAGE" '{"decision": "block", "message": $m}'
  exit 0
fi

printf '%s\n' '{"decision": "proceed"}'
