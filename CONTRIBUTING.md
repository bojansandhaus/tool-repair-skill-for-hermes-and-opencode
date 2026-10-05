# Contributing

## Layout

| Path | What it is |
| --- | --- |
| `references/tool_repair.py` | the repair logic, importable and also runnable as a self-test |
| `adapters/opencode/tool_repair.ts` | TypeScript port with identical behaviour |
| `adapters/claude-code/pre_tool_use.sh` | Claude Code hook that blocks a malformed call so the model retries |
| `adapters/opencode/plugin.ts` | OpenCode plugin wrapper |
| `tests/test_tool_repair.py` | the suite; run it before every commit |

## The two implementations must agree

`tool_repair.py` and `tool_repair.ts` are ports of each other. When you change the
repair rules, change both and add a test to each. A behavioural difference between
them is a bug in one of them, not a platform difference.

## Ordering is load-bearing

Stringified-array parsing must run before bare-string wrapping. If that order ever
flips, `"[\"a\"]"` gets wrapped as a one-element array containing the literal text
instead of being parsed. The pipeline mutates one dict in place; do not refactor the
steps to return new values without re-checking the order.

## Rules the repairs must keep

- A null is deleted only where the schema does not require the field and does not
  admit null for it. Deleting a required or explicitly nullable null turns a
  rejected call into a differently invalid one.
- Array-shaped repairs fire only when the schema says the field is an array.
- Never invent a value. Wrapping `"foo"` as `["foo"]` is a repair; inventing a
  probability or a default is not.

## Running the checks

```sh
python -m pytest tests -q          # 34 tests
python references/tool_repair.py   # module self-test, exit 0 on success
bash -n adapters/claude-code/*.sh  # shell syntax
```

The self-test must exit 0. It is what runs without pytest installed.

The TypeScript side has its own suite, and it guards the agreement between the
two implementations:

```sh
npx --yes -p typescript@5 -p tsx@4 tsx --test tests/parity.test.ts   # 21 tests
```

There is no `package.json`, so that command resolves TypeScript and tsx through
`npx`. CI runs it in the `typescript` job.

## Style

Match the surrounding code. Four-space indent in Python, two in TypeScript, and
the shell hook keeps `set -euo pipefail` and uses `printf '%s'` rather than `echo`
for JSON.
