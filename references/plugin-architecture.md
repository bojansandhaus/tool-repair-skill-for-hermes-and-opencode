# hermes-tool-repair Plugin Architecture (Draft Proposal)

## What It Is

A Hermes Agent plugin that sits between the model's `tool_calls` output and the tool dispatch executor, intercepting schema validation failures and applying deterministic repairs before the tool ever runs. This saves token waste, preserves model flow state, and generates per-(tool, model) telemetry.

## Integration Point

Hermes dispatches tool calls in `run_agent.py` → `_execute_tool_calls()`. Before each tool is invoked, its arguments are validated against the tool schema (pydantic/Zod). The plugin intercepts at this exact point:

```
model tool_calls → [current] JSON parse → validate → [fail] return error to model
                                            ↓
                  [with plugin]           → validate → [fail] REPAIR HARNESS
                                                                ↓
                                                    re-validate → [fail] return error + model-readable msg
                                                                  [pass] dispatch + append repair note
                                                                         ↓
                                                                  log telemetry (tool, model, repair_type)
```

## Plugin Registration

The plugin registers a `post_tool_call` or `transform_llm_output` hook, or a custom tool-call pre-processing step via `run_agent.py`'s plugin hooks.

In `plugin.yaml`:
```yaml
name: hermes-tool-repair
version: 1.0.0
description: Deterministic tool call repair harness
provides_hooks:
  - pre_tool_dispatch    # intercept tool call before execution
```

The `register()` function:
```python
def register(ctx):
    ctx.register_hook("pre_tool_dispatch", repair_tool_call_arguments)
```

## Core Loop

```python
def repair_tool_call_arguments(tool_name: str, arguments: dict, messages: list) -> dict:
    """
    Validate-then-repair loop for a single tool call.
    Returns the (possibly repaired) arguments dict.
    Side-effect: appends a repair note message if repairs were applied.
    """
    schema = get_tool_schema(tool_name)
    validation = validate(schema, arguments)
    
    if validation.is_valid:
        return arguments  # fast path — valid inputs are never touched
    
    # Collect repair candidates from the validator's issue list
    issues = validation.issues  # each issue has a `path` and `expected`/`received`
    repairs_applied = []
    
    for issue in issues:
        path = issue.path
        for repair_fn in REPAIR_MIGRATIONS:  # ordered list
            if repair_fn.can_apply(path, arguments, issue):
                arguments = repair_fn.apply(path, arguments)
                repairs_applied.append(repair_fn.name)
                break  # first matching repair wins
    
    if repairs_applied:
        # Re-validate
        validation = validate(schema, arguments)
        if validation.is_valid:
            append_repair_note(messages, tool_name, repairs_applied)
            log_telemetry(tool_name, repairs_applied, success=True)
            return arguments
    
    log_telemetry(tool_name, [], success=False)
    return arguments  # will fail — let the standard error handler deal with it
```

## Repair Migrations

Ordered array of `RepairMigration` objects (each is 30-100 lines):

```python
REPAIR_MIGRATIONS = [
    StripNullFields(),       # 1. Delete null values for optional fields
    JsonArrayParse(),        # 2. Parse stringified JSON arrays `"[\"a\"]"` → `["a"]`
    EmptyObjectToArray(),    # 3. `{}` → `[]` where array expected
    BareStringWrap(),        # 4. `"foo"` → `["foo"]` where array expected
]
```

Order matters — JsonArrayParse must run before BareStringWrap to prevent double-wrapping.

Each migration implements:
```python
class RepairMigration:
    name: str
    def can_apply(self, path: list, args: dict, issue: ValidationIssue) -> bool: ...
    def apply(self, path: list, args: dict) -> dict: ...
```

## Repair Notes

After a successful repair, inject a tool-result-level note into the message list:

```python
def append_repair_note(messages, tool_name, repairs_applied):
    repair_bullets = " | ".join(repairs_applied)
    note = (
        f"[Hermes repaired: {tool_name} — applied "
        f"{' '.join(repairs_applied)}. "
        f"Correct format was injected automatically.]"
    )
    # Append as a system-origin message or tool-result appendix
    # (depends on how Hermes prefers to surface it)
```

The note becomes part of the context the model sees on its next reasoning step. This is the key behavioral feedback — without it, open models repeat the same mistake.

## Schema Hints (Path-Aware Types)

For commonly mis-formatted fields, replace generic `z.string()` with semantic wrappers:

```
z.string()      → pathString()     # file path — auto-link unwrap, tilde expansion
z.string()      → urlOrPath()      # differentiate URLs from filesystem paths
z.array()       → stringArray()    # array of strings — bare-string-wrap hint
z.number().int() → portNumber()    # port — range hint baked in
```

The hint doesn't change validation rules. It changes what repairs the harness applies when validation fails. A `pathString()` field that fails on a markdown auto-link value triggers the auto-link unwrapper. A plain `z.string()` field with the same value would not.

This is the "tell the model this path is going to fopen, not into a chat bubble" insight — encoded at the schema level.

## Telemetry

Simple JSON append-log at `~/.hermes/data/tool-repair-telemetry.jsonl`:

```jsonl
{"ts": "2026-06-26T10:00:00Z", "tool": "readFile", "model": "deepseek-v4-pro", "repairs": ["bare-string-wrap"], "success": true}
{"ts": "2026-06-26T10:00:01Z", "tool": "writeFile", "model": "deepseek-v4-flash", "repairs": ["json-array-parse"], "success": true}
{"ts": "2026-06-26T10:00:02Z", "tool": "readFile", "model": "deepseek-v4-flash", "repairs": [], "success": false}
```

A dashboard tab or CLI command can aggregate these:
```bash
# Repair rate per tool
cat ~/.hermes/data/tool-repair-telemetry.jsonl | python3 -c "
import json, sys, collections
counts = collections.Counter()
for line in sys.stdin:
    r = json.loads(line)
    counts[f\"{r['tool']}:{r['model']}\"] += 1
for key, count in counts.most_common():
    print(f'{key}: {count}')
"
```

## Config

```yaml
plugins:
  enabled:
    - hermes-tool-repair
  entries:
    hermes-tool-repair:
      enabled: true
      log_repairs: true
      log_path: ~/.hermes/data/tool-repair-telemetry.jsonl
      schema_hints:
        # Enable/disable semantic type wrappers per tool
        paths: [writeFile, readFile, terminal]
        arrays: [writeFile, patch, readFile]
```

## Relational Defaults (Separate from Repairs)

Repairs handle shape problems. Relational invariants need semantic defaults:

```yaml
relational_defaults:
  readFile:
    # offset without limit → limit = 2000
    # limit without offset → offset = 0
    - fields: [offset, limit]
      resolver: fill_partial({offset: 0, limit: 2000})
      note: true
  terminal:
    # timeout without workdir → workdir = cwd
    - fields: [timeout, workdir]
      resolver: fill_partial({})
      note: false
  patch:
    # old_string without new_string → not allowed, let it fail
    - fields: [old_string, new_string]
      resolver: raise_on_partial
```

## Implementation Order

1. **Skill + system prompt** (done — the `tool-call-repair-patterns` skill)
2. **Standalone Python script** that wraps a single tool call's repair logic (testable in isolation)
3. **Hermes plugin** with the `pre_tool_dispatch` hook registration
4. **Schema hints** — start with `pathString()` for writeFile/readFile
5. **Relational defaults** — start with readFile offset/limit
6. **Dashboard tab** for repair telemetry

## Known Limitations

- **Non-deterministic guesses** — when the model omits a required field entirely, there's nothing to repair. The Harness can guess (e.g. default timeout for terminal) but should always surface the guess via a repair note so the model can self-correct.
- **Tool-specific patterns** — some models have per-tool quirks that aren't captured by the four universal patterns (e.g. Gemini double-wrapping arrays, Claude omitting `filePath` for writeFile). These need tool-specific repair entries.
- **Model regression detection** — if a model's repair rate suddenly spikes, the harness should fall back to passing the raw error instead of silently repairing, so the user knows the model is having issues.
