#!/usr/bin/env bash
# Shared defect detectors for the shell adapters. Sourced, never executed.
#
# Before this file existed the same jq selects were maintained by hand in
# adapters/claude-code/pre_tool_use.sh, adapters/claude-code/post_tool_use.sh
# and adapters/deepseek-harness/pre_tool_use.sh, and they had already drifted
# twice: v1.0.1 fixed the null detector in two of the three copies and missed
# the third (post_tool_use.sh's own comments record it), and v1.0.5 found the
# stringified-array and auto-link selects had diverged again. Nothing about a
# fourth copy makes the first three agree, so the jq lives here once and the
# hooks are left with their seam-specific parts.
#
# Contract
# --------
#   SHARED_DIR=$SCRIPT_DIR/../shared
#   . "$SHARED_DIR/detect.sh"
#   tr_detect "$PAYLOAD" "$TOOL_KEY" "$ARGS_KEY"
#
# TOOL_KEY and ARGS_KEY are plain key names, not jq paths, so each framework's
# envelope can name its own fields: Claude Code sends `tool` / `input`, the
# DeepSeek Harness bridges send `tool_name` / `tool_input`.
#
# tr_detect always succeeds. That is deliberate and it is the whole reason for
# the odd `|| out=""`: an unparseable payload once made jq fail under `set -e`,
# which killed the hook with exit 5 and EMPTY stdout. A command hook's contract
# is one decision on stdout with exit 0, and no output at all is not a decision.
#
# What it sets
# ------------
#   TR_TOOL_NAME   the tool name, or empty when the payload carries none
#   TR_ARGS_JSON   the arguments object, always a JSON object
#   TR_ROWS        newline-separated `pattern=field list` rows for every
#                  pattern that fired, in this order: null, stringified,
#                  autolink, empty_object. Field lists are joined with ", ".
#   TR_ISSUES      the fragment the blocking hooks interpolate into their
#                  message, built from null, stringified and autolink only
#   TR_PROJECTED   1 when the payload carried nothing but a projected command
#                  string, which is what @deepseek-ai/dsh-hooks-codex sends
#
# Which patterns a hook reports is the hook's business, and the one deliberate
# asymmetry in this repo is here: the blocking hooks do not report
# empty_object. Without a schema an empty object is a perfectly legal value
# (`{"options":{}}`), so blocking on it is the false positive v1.0.5 spent a
# release removing; the schema that makes it a defect lives with the executor.
# The telemetry hook reports it, because a log line costs nothing and the
# signal is worth having.

# tr_jq <json> [jq args...] -- jq's stdout, or the empty string on any failure.
# Never propagates a non-zero status, so it is safe under `set -e`/`pipefail`.
tr_jq() {
  local out
  out=$(printf '%s' "$1" | jq -r "${@:2}" 2>/dev/null) || out=""
  printf '%s' "$out"
}

TR_TOOL_NAME=""
TR_ARGS_JSON="{}"
TR_ROWS=""
TR_ISSUES=""
TR_PROJECTED=0

tr_detect() {
  local payload="${1:-}" tool_key="${2:-}" args_key="${3:-}"

  TR_TOOL_NAME=""
  TR_ARGS_JSON="{}"
  TR_ROWS=""
  TR_ISSUES=""
  TR_PROJECTED=0

  TR_TOOL_NAME=$(tr_jq "$payload" --arg k "$tool_key" '.[$k] // empty')
  # A non-object arguments value is not something these selects can walk: a
  # bare string has no paths, and a `null` means the bridge sent nothing. Both
  # are inspected as nothing, which is what they are.
  TR_ARGS_JSON=$(tr_jq "$payload" --arg k "$args_key" \
    'if (.[$k] | type) == "object" then .[$k] else {} end')
  [ -n "$TR_ARGS_JSON" ] || TR_ARGS_JSON="{}"

  # The Codex bridge does not forward the arguments at all. It projects them
  # down to one string:
  #     tool_input: { command: commandOf(exec.arguments) }
  # So `files`, `limit`, `timeout` and `filePath` are gone before this script
  # runs, and no amount of jq brings them back: every pattern for a
  # non-command tool answers `proceed` under that bridge. What does arrive is
  # the command, so the command is read explicitly and scanned in its own
  # right, and the shape is recorded in TR_PROJECTED so the hook can say the
  # rest of the arguments were never inspected instead of answering as though
  # it had looked. Keys is sorted, so a single-key object is a one-element
  # list; a full `exec` payload also has a `command` and is not the projection,
  # because it still carries the rest of its fields.
  if [ "$(tr_jq "$TR_ARGS_JSON" 'keys == ["command"]')" = "true" ]; then
    TR_PROJECTED=1
    # Rebuild the scan target from the command alone. The command keeps the
    # type it arrived with: a null command is still reported, because the
    # bridge's projection is not evidence that the model sent a null, and
    # suppressing a report on that guess would be the silent-approval bug this
    # adapter was written to fix.
    TR_ARGS_JSON=$(tr_jq "$TR_ARGS_JSON" '{"command": .command}')
  fi

  # --- the four detectors, one copy ----------------------------------------
  #
  # null: this must be `paths(. == null)`, not
  # `paths(scalars) as $p | select(getpath($p) == null)`. paths(scalars) emits
  # a path only for non-null scalars, so the select could never fire and the
  # hook approved every call it was installed to catch. v1.0.1 fixed that in
  # two of the three copies and missed the third.
  local null_fields stringified autolinks empty_objects
  null_fields=$(tr_jq "$TR_ARGS_JSON" '
    [paths(. == null) as $p
    | ($p | join("."))]
    | join(", ")
  ')

  # stringified arrays: a leading bracket is not enough, and neither is a
  # leading bracket with whitespace before it. The value must actually parse as
  # a JSON array, which is the bar the library itself uses
  # (references/tool_repair.py trims, then requires both brackets, then
  # parses). Testing only the shape blocked legitimate bracketed prose such as
  # "[1, 2] and [3, 4]" inside a writeFile content field, leaving a
  # documentation-writing agent no way to ship its own output except by
  # corrupting it. The `^\\s*\\[` is the other half: the library repairs
  # ` ["a.txt"] `, so the hooks that followed it missed the leading-whitespace
  # form that post_tool_use.sh has always detected.
  stringified=$(tr_jq "$TR_ARGS_JSON" '
    [paths(type == "string") as $p
    | select((getpath($p) | type) == "string")
    | select(getpath($p) | test("^\\s*\\["))
    | select((try (getpath($p) | fromjson | type) catch null) == "array")
    | ($p | join("."))]
    | join(", ")
  ')

  # auto-links: the link text must equal the URL's own path component, which
  # is what makes it a leak from the chat distribution rather than a real link.
  # The URL may be a bare host or host-plus-path, matching the library's own
  # regex, so `[notes.md](http://notes.md)` and
  # `[notes.md](http://host/notes.md)` both match while
  # `[click](https://example.com)` does not. The value need not be only a path:
  # a leading directory is fine, hence the unanchored prefix. The `\1`
  # backreference is the point: without it the pattern matched ANY markdown
  # link, and a documentation agent writing `see [click](https://...)` was
  # blocked on every call.
  autolinks=$(tr_jq "$TR_ARGS_JSON" '
    [paths(type == "string") as $p
    | select((getpath($p) | type) == "string")
    | select(getpath($p) | test("\\[([^\\]]+)\\]\\(https?://(?:[^/]+/)?\\1\\)"))
    | ($p | join("."))]
    | join(", ")
  ')

  # empty objects: telemetry only, see the header.
  empty_objects=$(tr_jq "$TR_ARGS_JSON" '
    [paths as $p
    | select((getpath($p) | type) == "object" and (getpath($p) | length) == 0)
    | ($p | join("."))]
    | join(", ")
  ')

  TR_ROWS=""
  [ -n "$null_fields" ]     && TR_ROWS="${TR_ROWS}null=$null_fields"$'\n'
  [ -n "$stringified" ]     && TR_ROWS="${TR_ROWS}stringified=$stringified"$'\n'
  [ -n "$autolinks" ]       && TR_ROWS="${TR_ROWS}autolink=$autolinks"$'\n'
  [ -n "$empty_objects" ]   && TR_ROWS="${TR_ROWS}empty_obj=$empty_objects"$'\n'

  # The blocking hooks' message fragment. The three shared patterns, in the
  # order v1.0.5 shipped them, so a block message reads the same on every
  # adapter.
  TR_ISSUES=""
  [ -n "$null_fields" ]  && TR_ISSUES="$TR_ISSUES null values in: $null_fields"
  [ -n "$stringified" ]  && TR_ISSUES="$TR_ISSUES stringified arrays in: $stringified"
  [ -n "$autolinks" ]    && TR_ISSUES="$TR_ISSUES markdown auto-links in: $autolinks"

  return 0
}

# tr_payload_is_object — is the payload an object at all?
tr_payload_is_object() {
  printf '%s' "$1" | jq -e 'type == "object"' >/dev/null 2>&1
}
