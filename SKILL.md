---
name: tool-call-repair-patterns
description: Validate-then-repair patterns for tool call resilience — four deterministic repairs, repair notes, and the structural insight that tool confusion is a harness problem, not a model problem. Based on CommandCode's approach that made DeepSeek V4 Pro outperform Opus 4.7 on tool calling.
version: 1.0.0
author: Hermione
---

# Tool Call Repair Patterns

## The Core Insight

When I hear "open model can't do tool calls," it's almost always a harness problem, not a model problem. Open models fail on tool calls because their training distributions leak through the tool boundary — auto-linking paths, sending null for optional fields, stringifying arrays. Commercial models eat these costs invisibly because they've seen enough contract variants during pretraining.

The fix isn't fine-tuning the model. It's making the contract more forgiving in exactly the places it needs to be.

## Validate-Then-Repair (The Structural Pattern)

```
model output → JSON parse → schema validate → [on success] dispatch
                                              → [on failure] walk validator issue list
                                                           → apply repairs at flagged paths
                                                           → re-validate
                                                           → [success] dispatch + repair note
                                                           → [failure] return model-readable error
```

**Critical rule:** Parse the input as-is first. If it succeeds, ship it. Valid inputs are never touched. Only spend repair budget at paths the validator actually disagreed at. This prevents silent corruption of valid data (e.g. writeFile content that happens to be JSON-shaped).

## The Four Universal Repair Patterns

Across DeepSeek-Flash, DeepSeek V4 Pro, GLM, Qwen — the same four mistakes repeat. ~90% of all tool call failures are one of these.

### 1. Null-Omit: `null` for optional fields

Symptom: The model sends `{"command": "ls", "timeout": null}` where timeout is optional. The schema rejects because it expects either a number or undefined, not null.

Fix: Walk the parsed JSON. For any field whose value is `null`, delete the key entirely (omit it). A field that isn't present satisfies any optional schema. This works because the model's intent is clear — it wanted to leave it unset, but its training distribution leaked `null` instead of omission.

### 2. Json-Array-Parse: `"[\"a\",\"b\"]"` as a string

Symptom: The model emits `{"files": "[\"src/main.ts\",\"src/utils.ts\"]"}` where the schema expects an actual array.

Fix: If a field's value is a string that looks like a JSON array (starts with `[`, ends with `]`), try to parse it. Replace the string with the parsed array.

**Ordering constraint:** This must run BEFORE Bare-String-Wrap. Otherwise `"[\"a\",\"b\"]"` becomes `["[\"a\",\"b\"]"]` — a single-element array containing the stringified version.

### 3. Empty-Object-To-Array: `{}` as array placeholder

Symptom: The schema expects an array but the model sends `{}`. Common for optional array fields the model wants to leave empty.

Fix: If the schema expects an array at this path and the value is a plain empty object `{}`, replace with `[]`.

### 4. Bare-String-Wrap: `"foo"` instead of `["foo"]`

Symptom: The schema expects a string array but the model sends a bare string for a single-item case.

Fix: If the schema expects an array at this path and the value is a string, wrap it: `["foo"]`.

## Repair Notes (The Game-Changer)

After every successful repair, append a compact note to the tool result:

```
[Repair note: the "offset" field was null — omitted it. The schema accepts undefined for optional fields but not null.]
```

The model reads this alongside the successful result and self-corrects on the next turn. Without the repair note, open models tend to repeat the same mistake (they have "alpha male energy" — convinced their output is correct and the validator is wrong). The note breaks this loop.

**The driving analogy:** You can let the learner hit the truck and then explain (waste tokens, break flow, degrade output quality). Or you can stop them before impact, fix the steering, and explain while they're still moving. The model stays in flow and the output quality never drops.

Format for repair notes:
- Prefix: `[Repair note:` or `[Hermes repaired:`
- Human-readable: what was wrong, what was changed, what the correct format is
- Concise: 1-2 sentences max
- Tone: informative, not apologetic

## Relational Invariants (Different Fix Than Shape)

Shape problems (wrong type, missing key, wrong container) → the four repairs above handle these.

Relational problems (paired fields that must co-occur) → need a different approach. Don't fix with input repair because each field is independently valid. Instead:

1. Default intelligently: `limit` without `offset` → offset = 0. `offset` without `limit` → limit = tool's sensible max (e.g. 2000 lines for read_file).
2. Surface the choice: "Note: limit was not provided; defaulted to 2000 lines. To read more or fewer lines, retry with both offset and limit."
3. No `Error:` prefix — don't paint it red in the UI. The model sees what we picked and self-corrects if wrong.

**Repair where you can. Extend semantics where you can't. Surface the choice either way.**

## The Markdown Auto-Link Leak (Bonus)

DeepSeek models sometimes emit file paths as markdown auto-links:

```json
{"filePath": "/Users/x/proj/[notes.md](http://notes.md)"}
```

This isn't a hallucination — it's the post-training chat distribution leaking through the tool boundary. The model has been rewarded for auto-linking URLs in conversational output and applies the same prior inside a tool call where it makes no sense.

Fix: Two regex lines that detect the degenerate case where link text equals the URL path (without protocol). Real markdown like `[click](https://x.com)` passes through untouched.

```python
# Unwrap degenerate auto-links like `/path/[name.md](http://name.md)`
import re
def unwrap_auto_link(path: str) -> str:
    """Detect and unwrap markdown auto-links where text == url-stem."""
    # Match patterns like: [filename.md](http://filename.md)
    # Only when link text matches the URL's path component (sans protocol)
    return re.sub(
        r'\[([^\]]+)\]\(https?://[^/]+/\1\)',
        r'\1',
        path
    )
```

Generalize: `pathString()` custom type instead of `z.string()` for path fields. When the schema tells the harness "this string will go to fopen", the harness knows to apply path-specific repairs (auto-link unwrap, tilde expansion, trailing slash normalization).

## Telemetry (Free Side-Effect)

The validate-then-repair architecture gives you per-(tool, model) repair counters for free:

```
tool_input_repaired:readFile (model=deepseek-v4-pro, repair=bare-string-wrap)
tool_input_repaired:writeFile (model=deepseek-v4-flash, repair=json-array-parse)
tool_input_repaired:terminal (model=deepseek-v4-flash, repair=null-omit)
tool_input_invalid:terminal (model=deepseek-v4-flash)
```

Track in a simple JSON file or statsd metric. Watch for:
- Spikes in repair rate → model regression
- Per-tool patterns → tighten schema hints for that tool
- Invalid rate vs repair success rate → are you missing a repair pattern?

## My Own Agent Behavior

When I receive a validation error from a tool, I should:

1. **Stay calm** — it's almost certainly one of the four patterns above. Don't waste context re-reading the entire schema.
2. **Be surgical** — the error message tells me exactly which field and what type was expected. Fix only that field.
3. **Note the fix** in my reasoning so I don't repeat it this session.
4. **Use the first successful call's repair pattern** as a template for the rest of the session (the model tends to make the same mistake consistently within a session).

## Hermes Integration Guide

This skill ships with a working Python repair library at `references/tool_repair.py`. To integrate it into Hermes, the change is in one file.

### Integration point

The function `sanitize_tool_call_arguments` in `agent/agent_runtime_helpers.py` (line 237) already walks tool_calls and repairs JSON — but only catches `json.JSONDecodeError` and replaces the entire thing with `{}`. The upgrade:

1. Drop `tool_repair.py` into `agent/tool_repair.py`
2. After `json.loads(arguments)` succeeds, add:
   - Call `repair_function_args()` on the decoded dict
   - If repairs happened, replace `function["arguments"]` with the fixed version
   - Append repair notes to the tool result message
3. Call `log_repair_event()` for telemetry

### Plugin limitation

The `pre_tool_call` hook only supports **blocking** a tool with a return message — it cannot modify arguments. A proper repair plugin needs a hook that can mutate args before execution. Until that exists, the agent-core modification above is the only path.

### Available reference files

| File | Purpose |
|------|---------|
| `references/tool_repair.py` | Working Python library — import and call `repair_function_args()` |
| `references/plugin-architecture.md` | Full plugin architecture proposal with config, hooks, telemetry, schema hints |
| `references/plugin.yaml` | Example plugin.yaml for a Hermes tool-repair plugin |

## This Is Not Model-Specific

These patterns apply to ALL models, not just open ones. Commercial models just fail less often because they've memorized more contract variants. When they DO fail, it's the same four patterns. The harness should protect against them regardless of model tier.

The largest commercial models eat the cost invisibly because they've seen enough contract variants during training. Open models pay it loudly and get dismissed for it. The harness is where you mediate between distributions.

"Skill issue" applies to the harness more often than the model.
