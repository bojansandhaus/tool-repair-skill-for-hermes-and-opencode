# Hermes Tool Repair

Deterministic tool call repair for LLM agents — catches the four common JSON formatting mistakes open models make and fixes them before they reach the tool executor, with repair notes that teach the model to self-correct.

Based on the approach that made DeepSeek V4 Pro outperform Opus 4.7 on tool calling (see [CommandCode's post](https://x.com/CommandCodeAI/status/1927626163496718571) and [YouTube deep dive](https://www.youtube.com/watch?v=f61DCDwvFis)).

## The Problem

Open models (DeepSeek, GLM, Qwen, Kimi) make the same tiny JSON mistakes in tool calls over and over. Each mistake triggers a validation error, the model retries with the same bad format, and the session degrades through 56+ wasted retry cycles. The model doesn't learn because the error messages are opaque.

These mistakes aren't random — they're a small finite set of four patterns caused by the model's training distribution leaking through the tool boundary.

## The Four Patterns This Fixes

| Pattern | What the model sends | What it should be |
|---------|---------------------|-------------------|
| Null omission | `{"cmd": "ls", "timeout": null}` | `{"cmd": "ls"}` |
| Stringified array | `{"files": "[\"a\",\"b\"]"}` | `{"files": ["a", "b"]}` |
| Empty object | `{"files": {}}` | `{"files": []}` |
| Bare string | `{"files": "main.ts"}` | `{"files": ["main.ts"]}` |
| Markdown autolink | `{"filePath": "/x/[f.md](http://f.md)"}` | `{"filePath": "/x/f.md"}` |

## How It Works

```
model tool_calls → JSON parse → validate → [pass] dispatch
                                           [fail] walk validator issue list
                                                 → apply repairs at flagged paths
                                                 → re-validate
                                                 → [pass] execute tool + append repair note
                                                 → [fail] return readable error
```

**Key design rule:** Valid inputs are never touched. The repair layer parses the input as-is first. If it succeeds against the schema, it ships. Repairs only fire at paths the validator actually flagged. This prevents silent corruption of legitimate data (e.g., writeFile content that happens to be JSON-shaped).

## Components

### `tool_repair.py` — the core library

Standalone Python module (no dependencies beyond stdlib). Main entry point:

```python
from agent.tool_repair import repair_function_args

repaired_args, repair_notes = repair_function_args(
    function_name="readFile",
    function_args={"path": "/tmp/test.txt", "limit": None},
    tool_schema=None,  # optional JSON schema for type-aware repairs
)
# repaired_args = {"path": "/tmp/test.txt"}
# repair_notes = ["[repair: null values removed for optional fields]"]
```

Can be imported and used by any agent framework — not Hermes-specific.

### Hermes Agent integration (included)

Two small modifications to the Hermes core:

1. **`agent/agent_runtime_helpers.py`** — in `sanitize_tool_call_arguments()`, after `json.loads()` succeeds, calls `repair_function_args()` on the parsed dict. If repairs are applied, updates the arguments JSON and stores a repair note in a side-channel dict.

2. **`agent/tool_dispatch_helpers.py`** — in `make_tool_result_message()`, checks the side channel for pending repair notes and appends them to the tool result content before it goes back to the model.

The model reads the repair note alongside the successful result and self-corrects on the next turn. No more 50-retry loops.

### Hermes Plugin (draft)

`references/plugin.yaml` + `plugin-architecture.md` — a blueprint for packaging the repair logic as a proper Hermes plugin with telemetry, dashboard, and config. Requires a `pre_tool_call` hook that supports argument modification (not currently available in Hermes' hook system).

## Safety Guarantees

- **Valid inputs are never touched.** The first step is always "try the input as-is." Only paths that fail validation get repaired.
- **Non-JSON tool data is unaffected.** The repair layer only examines tool call *arguments* (the JSON dict describing what the tool should do), not tool results, binary content, images, or multimodal data.
- **Schema-aware array repairs.** Array-specific repairs (empty-object-to-array, bare-string-wrap) only fire when the tool's JSON schema confirms the field expects an array type. Without a schema, only safe universal repairs run (null-strip, stringified-array-parse, autolink-unwrap).
- **Repair notes deduplicate.** If a repair note was already appended on a previous turn, it won't be stacked again.

## Usage

### From any Python project

```python
import json
from tool_repair import repair_function_args

def dispatch_tool(name, args_json):
    args = json.loads(args_json)
    if isinstance(args, dict):
        fixed_args, notes = repair_function_args(name, args)
        if notes:
            print(f"Repaired {name}: {notes}")
            args_json = json.dumps(fixed_args)
    # proceed to execute tool with args_json
```

### In Hermes Agent

Already wired in — no additional setup needed. The integration is in `sanitize_tool_call_arguments` and `make_tool_result_message`. Enable/disable via config:

```yaml
# ~/.hermes/config.yaml
agent:
  tool_repair: true  # default: true
```

## Roadmap

- [x] Core repair library (5 pattern fixes)
- [x] Hermes integration (sanitize + tool result pipeline)
- [x] Repair note side channel (model self-correction)
- [ ] Schema-aware repairs (type inference from JSON schema)
- [ ] Per-model repair telemetry (dashboard tab)
- [ ] Model-specific repair profiles (DeepSeek, GLM, Kimi quirks)

## License

MIT — free to use, modify, and distribute. This is a direct implementation of patterns discovered by the CommandCode team. All credit for the original insight goes to them.
