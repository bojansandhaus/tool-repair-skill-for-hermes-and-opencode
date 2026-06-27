---
name: tool-call-repair-patterns
description: Validate-then-repair patterns for tool call resilience. Four deterministic repairs, repair notes, and the structural insight that tool confusion is a harness problem, not a model problem. Based on CommandCode's approach that made DeepSeek V4 Pro outperform Opus 4.7 on tool calling.
version: 1.3.0
author: Hermione
---

# Tool Call Repair Patterns

## The Core Insight

When I hear "open model can't do tool calls," it is almost always a harness problem, not a model problem.

The harness is the layer between the model's output and the tool executor. It decides what to do with the raw JSON the model emits. A harness that rejects bad JSON and bounces an error back to the model wastes tokens and degrades the session. A harness that fixes the bad JSON deterministically turns a "bad at tool calling" model into a functional one in 200 lines of code. The model did not change. The harness got more forgiving.

Open models fail on tool calls because their training distributions leak through the tool boundary: auto-linking paths, sending null for optional fields, stringifying arrays. Commercial models eat these costs invisibly because they have seen enough contract variants during pretraining.

The fix is not fine-tuning the model. It is making the harness contract more forgiving in exactly the places it needs to be. Most tool-calling benchmarks measure the model. They should measure the harness.

## Validate-Then-Repair (The Structural Pattern)

```
                    HARNESS BOUNDARY
  model output --> JSON parse --> schema validate
                                     |
                                    / \
                                 pass  fail
                                  |     |
                             dispatch  walk issue list
                                  |     |
                                  |  apply repairs
                                  |     |
                                  |  re-validate
                                  |   /    \
                                  |pass    fail
                                  |  |       |
                                  |  |  return error
                                  |  |
                                  +--+---> tool result + repair note --> back to model
```

Everything in the box is the harness. The model only provides raw JSON and receives the result. All repair logic, validation, and correction notes are handled at the harness layer.

**Critical rule:** Parse the input as-is first. If it succeeds, ship it. Valid inputs are never touched. Only spend repair budget at paths the validator actually disagreed at. This prevents silent corruption of valid data (e.g. writeFile content that happens to be JSON-shaped).

## The Four Universal Repair Patterns

Across DeepSeek-Flash, DeepSeek V4 Pro, GLM, Qwen: the same four mistakes repeat. ~90% of all tool call failures are one of these.

### 1. Null-Omit: `null` for optional fields

Symptom: The model sends `{"command": "ls", "timeout": null}` where timeout is optional. The schema rejects because it expects either a number or undefined, not null.

Fix: Walk the parsed JSON. For any field whose value is `null`, delete the key entirely (omit it). A field that isn't present satisfies any optional schema. This works because the model's intent is clear: it wanted to leave it unset, but its training distribution leaked `null` instead of omission.

### 2. Json-Array-Parse: `"[\"a\",\"b\"]"` as a string

Symptom: The model emits `{"files": "[\"src/main.ts\",\"src/utils.ts\"]"}` where the schema expects an actual array.

Fix: If a field's value is a string that looks like a JSON array (starts with `[`, ends with `]`), try to parse it. Replace the string with the parsed array.

**Ordering constraint:** This must run BEFORE Bare-String-Wrap. Otherwise `"[\"a\",\"b\"]"` becomes `["[\"a\",\"b\"]"]`: a single-element array containing the stringified version.

### 3. Empty-Object-To-Array: `{}` as array placeholder

Symptom: The schema expects an array but the model sends `{}`. Common for optional array fields the model wants to leave empty.

Fix: If the schema expects an array at this path and the value is a plain empty object `{}`, replace with `[]`.

### 4. Bare-String-Wrap: `"foo"` instead of `["foo"]`

Symptom: The schema expects a string array but the model sends a bare string for a single-item case.

Fix: If the schema expects an array at this path and the value is a string, wrap it: `["foo"]`.

## Repair Notes (The Game-Changer)

After every successful repair, append a compact note to the tool result:

```
[Repair note: the "offset" field was null: omitted it. The schema accepts undefined for optional fields but not null.]
```

The model reads this alongside the successful result and self-corrects on the next turn. Without the repair note, open models tend to repeat the same mistake (they have "alpha male energy": convinced their output is correct and the validator is wrong). The note breaks this loop.

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
3. No `Error:` prefix. Don't paint it red in the UI. The model sees what we picked and self-corrects if wrong.

**Repair where you can. Extend semantics where you can't. Surface the choice either way.**

## The Markdown Auto-Link Leak (Bonus)

DeepSeek models sometimes emit file paths as markdown auto-links:

```json
{"filePath": "/Users/x/proj/[notes.md](http://notes.md)"}
```

This isn't a hallucination: it's the post-training chat distribution leaking through the tool boundary. The model has been rewarded for auto-linking URLs in conversational output and applies the same prior inside a tool call where it makes no sense.

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

## Lessons from Production Scale

The four universal patterns are the core, but real-world deployment grows
beyond them. A harness that started with four repairs now has over 56,000
repair invariants. Here is what that scale teaches us.

### The 4 to 56,000 Evolution

It started with four deterministic repairs across ~200 lines. Each was
model-agnostic and covered the most frequent failures. Over time, as more
models were added (DeepSeek Flash, GLM, Qwen, Kimi K2.6, MiniMax), new
patterns emerged that were model-specific or language-specific. The repair
count grew to 12-16, then to 36,000, and eventually to over 56,000 small
migration rules.

The invariant count goes up and down. Models improve with new releases, so
some repairs become unnecessary and are retired. Other repairs are added as
new quirks are discovered. The four universal patterns stay constant.
Everything else is a growing catalog of edge cases per-model, per-language,
per-scenario.

**If you start implementing this:** build the four core repairs first. Add
model-specific and language-specific migrations on top, measured against
telemetry. Do not start with 56,000 rules. Let the data tell you where to
grow.

### Alpha Male Energy

Open models have a specific failure mode that closed models rarely exhibit:
when they receive a validation error, they repeat the same mistake rather
than adapting. It is not confusion or bad memory. The model is convinced its
output is correct and the validator is wrong.

This manifests as 56 consecutive identical bad tool calls before the model
finally changes its format. Every one of those 56 round-trips wastes tokens,
breaks session flow, and degrades overall output quality. A model receiving
a wall of Zod error blobs cannot recover because the error messages are not
in a form the model can read.

The repair note pattern is the specific antidote. When you give the model a
successful tool result with a one-line note saying "by the way, I fixed your
null value on the timeout field" the model learns instantly and stops making
that mistake for the rest of the session. It is not about the error. It is
about preserving flow so the model never enters a defensive loop.

### Inference Capacity Correlation

Tool call error rates are not static. They spike when inference capacity is
under strain. The same model that makes zero mistakes at 2 AM will produce
dozens of bad tool calls at peak usage hours every one of the four universal
patterns. This pattern is reproducible across providers and model families.

When the model is overloaded (high request volume, shared compute, degraded
inference hardware), output quality drops in characteristic ways: wrong types
become more frequent, nulls appear where numbers should be, stringified
arrays proliferate, and markdown auto-links leak through path fields. The
harness saw this hundreds of thousands of times across a trillion tokens per
month of production traffic.

**Practical takeaway:** if your harness seems to work fine in testing but
fails under real usage, you are not seeing a regression in your code. You
are seeing inference capacity strain. The solution is not to tighten your
schema. The solution is to make your repairs more aggressive or to route to
a less loaded inference endpoint. The repair harness is your buffer against
shared-infrastructure variance.

## My Own Agent Behavior

When I receive a validation error from a tool, I should:

1. **Stay calm**: it's almost certainly one of the four patterns above. Don't waste context re-reading the entire schema.
2. **Be surgical**: the error message tells me exactly which field and what type was expected. Fix only that field.
3. **Note the fix** in my reasoning so I don't repeat it this session.
4. **Use the first successful call's repair pattern** as a template for the rest of the session (the model tends to make the same mistake consistently within a session).

## Hermes Integration (Complete: Built and Tested)

This skill ships with a working Python repair library at `references/tool_repair.py` that is **now integrated into the Hermes agent core**. The integration uses a side-channel pattern because Hermes' plugin hooks do not support argument mutation.

### Files changed

| File | Change | Lines added |
|------|--------|-------------|
| `agent/agent_runtime_helpers.py` | Added `_SEMANTIC_REPAIR_NOTES` dict + `pop_semantic_repair_notes()` helper + semantic repair call in `sanitize_tool_call_arguments()` | ~30 |
| `agent/tool_dispatch_helpers.py` | Added `_pop_repair_notes()` helper + repair note appending logic inside `make_tool_result_message()` | ~25 |
| `agent/tool_repair.py` | New file: the repair library | 297 |

### How the side channel works

```
sanitize_tool_call_arguments()                make_tool_result_message()
  |                                              |
  |- json.loads(args)  ✓                        |
  |- repair_function_args() → notes             |
  |- _SEMANTIC_REPAIR_NOTES[tc_id] = notes  ----|
  |- update function["arguments"]               |
  |                                              |- _pop_repair_notes(tc_id) → notes
  |                                              |- append notes to result content
  |                                              |- return result dict with notes
```

1. **`sanitize_tool_call_arguments`** (runs before dispatch): after `json.loads()` succeeds, calls `repair_function_args()` on the parsed dict. If repairs were applied, updates `function["arguments"]` with the fixed JSON and stores the repair notes in `_SEMANTIC_REPAIR_NOTES` keyed by `("", tool_call_id)`.

2. **`make_tool_result_message`** (runs when the tool completes): queries `_SEMANTIC_REPAIR_NOTES` for any pending notes matching the tool_call_id. If found, appends them to the tool result content (handles string, list, and dict/multimodal shapes). The key is consumed once: no memory leak.

3. The model receives the successful tool result with the repair note appended. It self-corrects on the next turn.

### Key design decisions

- **Validate-then-repair**: `json.loads()` runs first. If it succeeds and the args are semantically valid, they pass through untouched.
- **Side channel, not global state**: The dict is module-level but keyed by unique tool_call_id. Each entry consumed exactly once.
- **Content-shape agnostic**: Repair note appending handles string, list, and dict result shapes.
- **Plugin-optional**: Works without a plugin by hooking into existing Hermes functions.

### Plugin limitation

The `pre_tool_call` hook only supports blocking a tool. It cannot modify arguments. Until hooks support argument mutation, the agent-core modification is the only path. See `references/plugin-architecture.md`.

### Available reference files

| File | Purpose |
|------|---------|
| `references/tool_repair.py` | Working Python library. Import and call `repair_function_args()` |
| `references/plugin-architecture.md` | Full plugin architecture proposal with config, hooks, telemetry, schema hints |
| `references/plugin.yaml` | Example plugin metadata |
| `references/gitflic-publishing.md` | GitFlic API reference for creating repos and pushing to secondary remote |

## Repositories

Source code and README at [github.com/bojansandhaus/tool-repair-skill-for-hermes-and-opencode](https://github.com/bojansandhaus/tool-repair-skill-for-hermes-and-opencode) (public) and
[gitflic.ru/bojansandhaus/tool-repair-skill-for-hermes-and-opencode](https://gitflic.ru/project/bojansandhaus/tool-repair-skill-for-hermes-and-opencode) (private mirror).
Both remotes are kept identical.

## Pitfalls

- **Array repair ordering**: `json-array-parse` MUST run before `bare-string-wrap`. Reversed, a stringified array like `'["a","b"]'` becomes `['["a","b"]']`.
- **Valid-content protection**: Schema-aware array repairs only fire when the schema confirms array type. Without a schema, only safe universal repairs run (null-strip, stringified-array-parse, autolink-unwrap).
- **No infinite stacking**: `deduplicate_repair_notes()` prevents the same note from being appended across multiple turns.
- **No em dashes in skill content**: The SOUL.md format rules prohibit em dashes. Use colons, semicolons, or periods instead. This applies to every file in the skill: SKILL.md, reference docs, templates, scripts. Check before writing.
- **Side channel lifecycle**: Entries are consumed when `make_tool_result_message` runs. If a tool call id is stored but never dispatched (e.g. an earlier turn's abandoned call), the dict entry persists until overwritten: negligible in practice since tool_call_id generation is strongly unique.

## This Is Not Model-Specific

These patterns apply to ALL models, not just open ones. Commercial models just fail less often because they've memorized more contract variants. When they DO fail, it's the same four patterns. The harness should protect against them regardless of model tier.

The largest commercial models eat the cost invisibly because they've seen enough contract variants during training. Open models pay it loudly and get dismissed for it. The harness is where you mediate between distributions.

"Skill issue" applies to the harness more often than the model.
