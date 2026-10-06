# tool-repair-skill-for-hermes-and-opencode v1.0.4

DeepSeek Harness adapter release. Adds a fourth framework to a project that
previously shipped three, plus the test suite and documentation to keep it
honest.

## What is new

A `PreToolUse` hook for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
(`@deepseek-ai/dsh`), written against the shipped `0.2.0-rc.2`, in
`adapters/deepseek-harness/`.

The harness already runs ordinary command hooks at its tool interception seams
through two bridge plugins it ships itself, `@deepseek-ai/dsh-hooks-claude-code`
and `@deepseek-ai/dsh-hooks-codex`. So this is a hook script plus a registration
snippet: no plugin build step, no package to publish, no harness fork.

## The seam can deny, but it cannot rewrite

The same limit as Claude Code, and it is the harness' design rather than a gap
in this adapter:

- `ToolExecution` in `@deepseek-ai/dsh-tools` declares `arguments` as
  `readonly`.
- `PreToolDecision` has four variants, `allow`, `deny`, `cancel`, `ask`, and
  none of them carries rewritten arguments. The doc comment says it outright:
  "Input rewriting is excluded because arguments are already logged and
  presented."
- The harness' own Claude Code bridge documents the same gap, listing
  `updatedInput` as "logged + warned but not honored", and its source logs
  `hook requested updatedInput, which is not yet honored (ignored)`.

So the hook detects a repairable argument, denies the call, and names the exact
fields in the reason. The model reads the message and retries with a corrected
format. That costs one turn and keeps the bad call away from the executor,
which is the strongest option this seam allows.

## The part that would have silently broken everything

The harness bridges emit their own field names. The payload is:

```json
{ "hook_event_name": "PreToolUse", "tool_name": "readFile",
  "tool_input": { "limit": null }, "tool_use_id": "call_1" }
```

The Claude Code adapter in this repo reads `.tool` and `.input`. The harness
never sends those keys. Same logical call, two payload shapes:

```
$ printf '%s' '{"hook_event_name":"PreToolUse","tool_name":"readFile","tool_input":{"limit":null}}' \
  | bash adapters/claude-code/pre_tool_use.sh
{"decision": "proceed"}

$ printf '%s' '{"hook_event_name":"PreToolUse","tool":"readFile","input":{"limit":null}}' \
  | bash adapters/claude-code/pre_tool_use.sh
{"decision":"block","message":"[tool-repair] Detected likely tool call issue in readFile: null values in: limit. ..."}
```

Reusing the existing script would have installed a hook that approved every
call and reported nothing. That is why this is a separate script, and why
`test_the_claude_code_keys_would_silently_approve` pins the mismatch rather
than leaving it as a comment.

## One defect fixed in the new hook before release

The first draft flagged any string starting with `[`, which blocks legitimate
bracketed prose such as `[1, 2] and [3, 4]`. The repair layer is required to
leave that content alone, and the library itself does: it only converts a
string that actually parses as a JSON array. The hook now requires the same
bar, via `fromjson | type == "array"`.

The Claude Code hook still uses the looser check, so it still has that false
positive. It is a real defect in a shipped file, deliberately not fixed here,
because changing a published adapter's detection behaviour belongs in its own
release with its own test. Recorded below rather than folded in silently.

## Verification

```
48 passed          (Python, 34 existing + 14 new)
21 pass, 0 fail    (TypeScript parity)
module self-test   passes
bash -n            clean on all three hooks
```

The new tests cover the harness payload keys, the Claude Code key mismatch, all
three repair patterns, the bare-host auto-link form, nested null field paths,
hostile field names, single-line output, and both valid-content cases.

## Known limitations

| Limitation | Impact |
|---|---|
| Seam cannot rewrite arguments | Block plus retry costs one turn |
| `tool_input` is already-parsed JSON | A call that failed to parse never reaches the hook |
| Requires `jq` on the harness process `PATH` | Common, but not universal |
| Version-coupled to `0.2.0-rc.2` | Developer preview with breaking changes; re-check `PreToolDecision` after upgrading |
| No telemetry added | The bridges already log every hook invocation and decision to the session log |