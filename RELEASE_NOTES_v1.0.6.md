# tool-repair-skill-for-hermes-and-opencode v1.0.6

Two medium findings and one small one, all in the shell adapters, all found by
reading the adapters against the bridges the install instructions tell you to
use. Every one was reproduced with a real command before it was fixed, and every
fix is pinned by a test that fails against v1.0.5.

## What is new

**The DeepSeek Harness adapter was a silent no-op for most tools under the
Codex bridge.** The README told you to register it with either bridge, and the
Codex bridge does not forward the arguments. Verified against both shipped
bridges: `@deepseek-ai/dsh-hooks-claude-code` sends
`tool_input: exec.arguments`, the full parsed object, and
`@deepseek-ai/dsh-hooks-codex` sends
`tool_input: { command: commandOf(exec.arguments) }` — one string and nothing
else. Under Codex, `files`, `limit`, `timeout` and `filePath` do not reach the
hook, so every null-field, stringified-array and auto-link pattern for a
non-command tool answered `proceed`:

```
$ printf '%s' '{"tool_name":"readFile","tool_input":{"command":"cat /x"}}' \
  | bash adapters/deepseek-harness/pre_tool_use.sh   # v1.0.5
{"decision": "proceed"}
# and nothing said why
```

The hook now reads `.tool_input.command` as an explicit fallback and scans that
string, so a command-shaped defect still blocks and names `command`; and when
the payload is the projection it says so on stderr, naming the fields the bridge
dropped:

```
$ printf '%s' '{"tool_name":"exec","tool_input":{"command":"[\"a.txt\"]"}}' \
  | bash adapters/deepseek-harness/pre_tool_use.sh
{"decision":"block","message":"[tool-repair] Detected likely tool call issue in exec: stringified arrays in: command. ..."}
[stderr] [tool-repair] codex bridge projection: only .tool_input.command is
         visible; files, limit, timeout, filePath and every other field were not
         inspected
```

The README states the projection in its own section and in its limitations
table, so a user can tell "this call had no defect" from "this call could not be
inspected". A full `exec` payload that carries `command` plus other fields is
not the projection — `keys` is compared against exactly `["command"]` — so a
real `{"command":"ls","timeout":null}` is still inspected field by field.

**Three near-identical jq detector blobs had drifted, and a fourth framework
would have meant a fourth copy.** The selects in
`adapters/claude-code/pre_tool_use.sh`, `adapters/claude-code/post_tool_use.sh`
and `adapters/deepseek-harness/pre_tool_use.sh` were private copies of each
other with no shared script, no single source of truth and no contract test
that they agree — the only parity test covered the Python↔TypeScript core.
v1.0.5 fixed the null detector twice in different copies and missed the post
hook's, which its own comments record. All three now source
`adapters/shared/detect.sh`, and `tests/test_adapter_parity.py` runs one
framework-neutral corpus through all three hooks and asserts they reach the
same verdict, name the same fields in the same order, and keep the
one-decision-on-stdout contract on the eight hostile shapes.

The one deliberate asymmetry, documented in the shared file and pinned by the
corpus: the telemetry hook reports `empty_obj`, the blocking hooks do not.
Without a schema an empty object is a legal value, so blocking on
`{"options":{}}` is the false positive v1.0.5 spent a release removing.

Two smaller things came with the extraction. The telemetry hook used to exit 5
with a jq traceback on unparseable stdin; it now exits 0 quietly, like the
pre hooks. And an install that copies one hook file without `adapters/shared/`
used to be undetectable — the hook now says so on stderr and still answers
`proceed`, rather than inspecting nothing in silence. The install instructions
in all three READMEs now copy the directories, and `TOOL_REPAIR_SHARED_DIR`
overrides where the detectors are looked up.

**The copied stringified-array select tested `startswith("[")`, so a stringified
array with leading whitespace was missed by both pre-tool hooks.** The library
trims before it tests the brackets, so it repairs ` ["a.txt"] `, and
`post_tool_use.sh` always detected it; only the two pre hooks answered
`proceed`. The shared select is now `test("^\\s*\\[")` plus the existing
"must actually parse as a JSON array" bar, so all three hooks agree. This is a
missed detection, not a false positive: the call goes to the executor, the
executor rejects the string, the model retries. It costs a turn.

## Verification

```
179 passed         (Python, 98 existing + 81 new)
27 pass, 0 fail    (TypeScript parity, unchanged — the core engine is untouched)
module self-test   passes
bash -n            clean on all four shell files: adapters/shared/detect.sh and
                   the three hooks
hostile battery    8 shapes (empty, non-JSON, truncated object, bare array,
                   null, false, "a string", 42) × 3 hooks: one decision on
                   stdout, exit 0
```

The new tests were reverted against v1.0.5's adapters to confirm they fail
without the fixes: 16 of the 100 in the two adapter suites fail, covering every
item above — the projection report, the leading-whitespace select, the
"no private copy of the selects" contract, and the telemetry hook's crash.

## Known limitations

| Limitation | Impact |
|---|---|
| Under the Codex bridge only `command` is visible | Only `command`-shaped defects are detected there. Use the Claude Code bridge for field-level coverage. See above. |
| A projected `command: null` still reports as a null field | The projection is not evidence the model sent a null, but it is not evidence it did not. The report is kept; suppressing it on that guess would be the silent-approval bug this adapter exists to fix. |
| The OpenCode adapter still has its own TypeScript copy | It is a TypeScript plugin and cannot source a bash file. Its detectors are covered by the Python↔TypeScript parity suite instead. |
| `empty_obj` is telemetry-only | Blocking hooks do not report it; see above. |
| TypeScript still mutates the caller's dict | Its documented contract, unchanged in v1.0.5. |
