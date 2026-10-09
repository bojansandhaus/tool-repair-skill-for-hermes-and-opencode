# Tool Repair: DeepSeek Harness Adapter

Adapter for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
(`@deepseek-ai/dsh`). Written against the shipped `0.2.0-rc.2`.

The harness already runs ordinary command hooks at its tool interception
seams, through two bridge plugins it ships itself:

| Bridge package | Runs |
|---|---|
| `@deepseek-ai/dsh-hooks-claude-code` | a Claude Code `hooks.json` / settings `hooks` config |
| `@deepseek-ai/dsh-hooks-codex` | Codex hook configuration |

So this adapter is a hook script plus a registration snippet. No plugin build
step, no publishing, no harness fork.

## What it can and cannot do

The harness seam can **block** a tool call. It cannot **rewrite** its arguments,
and this is not a limitation of this adapter:

- `ToolExecution` in `@deepseek-ai/dsh-tools` declares `arguments` as
  `readonly`, with the doc comment "Losslessly JSON-serializable parsed
  arguments".
- `PreToolDecision` has exactly four variants: `allow`, `deny`, `cancel`,
  `ask`. None carries rewritten arguments. Its doc comment states plainly:
  "Input rewriting is excluded because arguments are already logged and
  presented."
- The harness' own Claude Code bridge documents the same gap under
  "PreToolUse is partial": `updatedInput` is "logged + warned but not honored",
  and its source logs `hook requested updatedInput, which is not yet honored`.

So this hook detects a repairable argument and denies the call with a message
naming the exact fields. The model reads the message and retries with a
corrected format on the next turn. That costs one turn and keeps the bad call
away from the executor, which is the strongest option the seam allows.

For deterministic repair with no wasted turn, use the OpenCode adapter, whose
`tool.execute.before` hook mutates arguments in place, or the Hermes
integration, which is two calls into the harness.

## Payload shape: this is the part that bites

The bridges emit the harness' own field names, **not** Claude Code's:

```json
{
  "hook_event_name": "PreToolUse",
  "tool_name": "readFile",
  "tool_input": { "limit": null },
  "tool_use_id": "call_1"
}
```

The Claude Code adapter in this repo reads `.tool` and `.input`. The harness
never sends those keys, so that adapter silently approves every call under the
harness. This adapter reads `tool_name` and `tool_input`, which is why it is a
separate script rather than a shared one.

Demonstrated, same input both ways:

```
$ printf '%s' '{"hook_event_name":"PreToolUse","tool_name":"readFile","tool_input":{"limit":null}}' \
  | bash adapters/claude-code/pre_tool_use.sh
{"decision": "proceed"}

$ printf '%s' '{"hook_event_name":"PreToolUse","tool":"readFile","input":{"limit":null}}' \
  | bash adapters/claude-code/pre_tool_use.sh
{"decision":"block","message":"[tool-repair] Detected likely tool call issue in readFile: null values in: limit. ..."}
```

## Installation

Copy the hook:

```bash
mkdir -p ~/.dsh/tool-repair
cp adapters/deepseek-harness/pre_tool_use.sh ~/.dsh/tool-repair/pre_tool_use.sh
chmod +x ~/.dsh/tool-repair/pre_tool_use.sh
```

Then register it with one of the bridges. Both live in
`$DSH_HOME/cordis.patch.yml` (`~/.dsh/cordis.patch.yml` by default), which
appends plugin entries after the bundle and profile layers.

### With the Claude Code bridge

```yaml
- id: hooks-claude-code
  name: '@deepseek-ai/dsh-hooks-claude-code'
  config:
    configPath: ~/.dsh/tool-repair/hooks.json
```

with `~/.dsh/tool-repair/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.dsh/tool-repair/pre_tool_use.sh"
          }
        ]
      }
    ]
  }
}
```

Or prompt your agent:

> Clone `https://github.com/bojansandhaus/tool-repair-skill-for-hermes-and-opencode`,
> copy `adapters/deepseek-harness/` **and** `adapters/shared/` into
> `~/.dsh/tool-repair/` keeping the directory layout, make
> `~/.dsh/tool-repair/deepseek-harness/pre_tool_use.sh` executable, write a
> `hooks.json` that registers it as a `PreToolUse` command hook, and add a
> `@deepseek-ai/dsh-hooks-claude-code` plugin entry pointing at it in
> `~/.dsh/cordis.patch.yml`.

`adapters/shared/detect.sh` is not optional. The three hooks in this repo share
one copy of the jq detectors, so the copy of `pre_tool_use.sh` you drop into
`~/.dsh/tool-repair/` needs `../shared/detect.sh` next to it. Copying the
directories keeps that true; copying the two `.sh` files into one flat directory
does not, unless you set `TOOL_REPAIR_SHARED_DIR` to wherever you put
`detect.sh`. A hook that cannot find its detectors says so on stderr and
answers `proceed` — it never blocks on the strength of half a detector.

### With the Codex bridge

Same shape, different package and config key. The Codex bridge reads its own
configuration format, so use its documented field names rather than the Claude
Code ones.

**The Codex bridge does not forward the arguments.** The Claude Code bridge
sends `tool_input: exec.arguments`, the full parsed object. The Codex bridge
sends:

```json
{
  "hook_event_name": "PreToolUse",
  "tool_name": "readFile",
  "tool_input": { "command": "commandOf(exec.arguments)" }
}
```

One string, and nothing else. Verified against the shipped bridges: under
`@deepseek-ai/dsh-hooks-codex` the fields `files`, `limit`, `timeout` and
`filePath` do not reach the hook at all, so for a tool whose arguments are not
command-shaped there is nothing for this adapter to inspect and it answers
`proceed`. That is not a payload-key mismatch and no change to the hook can
recover those fields — the bridge has already dropped them.

What the hook does do under the Codex bridge:

- it reads `.tool_input.command` explicitly and scans that string for
  command-shaped defects — a stringified array (`["a.txt", "b.txt"]`) or a
  degenerate auto-link (`[notes.md](http://notes.md)`), naming `command` in the
  block message;
- it says so, on stderr, on every invocation:
  `[tool-repair] codex bridge projection: only .tool_input.command is visible;
  files, limit, timeout, filePath and every other field were not inspected`.

So a user of this adapter under Codex can tell the difference between "this
call had no defect" and "this call could not be inspected". Under the Claude
Code bridge nothing is projected, every field is visible, and the message does
not appear.

```
$ printf '%s' '{"tool_name":"exec","tool_input":{"command":"[\"a.txt\"]"}}' \
  | bash adapters/deepseek-harness/pre_tool_use.sh
{"decision":"block","message":"[tool-repair] Detected likely tool call issue in
exec: stringified arrays in: command. ..."}
```

A full `exec` payload that carries `command` plus other fields is not the
projection: `keys` is compared against exactly `["command"]`, so a real
`{"command":"ls","timeout":null}` is inspected field by field and still reports
`timeout`.

## Verification

Confirm the hook is registered and firing:

```bash
dsh --profile headless --dump-config 2>/dev/null | grep -A6 hooks-claude-code
```

Then run a headless session and watch for the block message in the log:

```bash
dsh --profile headless "Use the readFile tool with {\"limit\": null}"
```

A block message naming `null values in: limit` means the seam is live. If the
call succeeds instead, the most likely cause is the payload key mismatch above:
check that the hook reads `tool_name` and `tool_input`.

## Limitations

| Limitation | Impact |
|---|---|
| Seam cannot rewrite arguments | Block plus retry costs one turn |
| `tool_input` is already-parsed JSON, so malformed-JSON errors never reach the hook | The hook cannot catch a call that failed to parse at all |
| The Codex bridge projects the arguments to `command` alone | Only `command`-shaped defects are detected under Codex; `files`, `limit`, `timeout` and `filePath` are invisible there. Use the Claude Code bridge for field-level coverage |
| A projected `command: null` still reports as a null field | The projection is not evidence the model sent a null, but it is not evidence it did not. The report is kept; suppressing it would be the silent-approval bug this adapter exists to fix |
| Requires `jq` | Common, but it must be on `PATH` for the harness process |
| Requires a bridge plugin | Both bridges ship with the harness; no extra install |
| Requires the shared detectors | Copy `adapters/shared/` alongside this adapter, or set `TOOL_REPAIR_SHARED_DIR` |
| Version-coupled to `0.2.0-rc.2` | The harness is a developer preview and ships breaking changes; re-check the `PreToolDecision` variants after upgrading |

## Telemetry

This adapter adds no telemetry. The bridges already record every hook
invocation and result to the session log, including the decision and the reason,
so the repair signal is captured without a second logging path. A block's
`reason` is the message this hook emits.