# tool-repair-skill-for-hermes-and-opencode v1.0.5

Five high-severity defects and one medium, all found by reading the code against
its own documentation and against the TypeScript port. Every one of them was
reproduced with a real command before it was fixed, and every fix is pinned by a
test that fails against v1.0.4.

## What is fixed

**The Claude Code hook blocked legitimate tool calls.** Two of its three
detection patterns were wrong in the direction that matters: they fired on valid
input.

```
$ printf '%s' '{"tool":"writeFile","input":{"content":"[1, 2] and [3, 4]"}}' \
  | bash adapters/claude-code/pre_tool_use.sh
{"decision":"block", ... "stringified arrays in: content"}

$ printf '%s' '{"tool":"writeFile","input":{"content":"see [click](https://example.com)"}}' \
  | bash adapters/claude-code/pre_tool_use.sh
{"decision":"block", ... "markdown auto-links in: content"}
```

The stringified-array test was `test("^\\s*\\[")`, which only asks whether the
value *starts* with a bracket, and the auto-link test was
`test("\\[[^]]+\\]\\(https?://")`, which has no `\1` backreference and therefore
matches any markdown link at all. A documentation-writing agent was blocked by
both, and its only escape was to corrupt its own output, which is the one thing
the repair layer exists to prevent. Both selects now carry the forms the
DeepSeek Harness adapter already shipped in fcdee58: the value must parse as a
JSON array, and the link text must equal the URL's own path component. That
commit's message said in as many words that the Claude Code hook still used the
looser check and still had the false positive, and that it was deliberately not
fixed there. This release closes it.

**The Python core raised on valid JSON Schema shapes.** The TypeScript port had
been hardened against a malformed schema and the Python port had not, so the same
call either raised or worked depending on which implementation was loaded.

```
{"properties":{"files":None}}                 -> AttributeError
{"properties":{"files":{"anyOf":None}}}      -> TypeError
{"properties":{"files":{"anyOf":[None,...]}}}-> AttributeError
{"properties":None}                          -> AttributeError
{"properties":{"files":"array"}}              -> AttributeError
{"properties":{"files":true}}                -> AttributeError
```

`true` and `false` are legal JSON Schema field schemas, and a `null` or a string
where a schema object belongs is malformed input that a repair layer should walk
past, not crash on. The sibling helper `_null_is_removable` guarded part of this,
which made the omission an internal inconsistency rather than a policy decision.
It turns out that helper had the same holes: a boolean or string field schema
raised there too, and a `required` given as a bare string became
`set("files")` = `{'f','i','l','e','s'}`, preserving a null on a field named
"f". Both helpers now carry the same guards.

**Python and TypeScript disagreed on the two schema-gated repairs.** The
schema-aware repairs are gated on `tool_schema is not None`, mirroring the
TypeScript port's `if (toolSchema)`. More substantively, Python compared
`type == "array"` only, so a `{"type": ["string","array"]}` union was invisible to
it: the bare-string and empty-object repairs silently did not fire on a field the
TypeScript port repaired, which is a divergence a union-schema caller would have
to notice by hand. A type union that names `"array"` is now honoured, exactly as
the null walker already treated a union naming `"null"`. A 39-case differential
run of both implementations on identical inputs went from 8 disagreements to 0.

**The Python core mutated the caller's dict, so the documented write-back pattern
silently discarded every repair.** The wiring SKILL.md tells a harness to write is:

```python
fixed, notes = repair_function_args(function["name"], parsed, tool_schema)
if fixed != parsed:
    function["arguments"] = json.dumps(fixed)
```

`fixed` and `parsed` were the same object, because every repair mutated the
caller's dict in place and `original = dict(function_args)` was computed and never
read. The comparison was therefore always False and the repaired JSON was never
written back, on every call, while the notes claimed a repair had happened.
`repair_function_args` now deep-copies its input, matching the `__main__`
self-test which already deep-copied for exactly this reason. The caller's dict is
left untouched, nested values included.

**A non-JSON bracketed string was wrapped as a real array element.** The test was
`v.strip().startswith("[")` but the parse was of the untrimmed `v`, and the
bare-string wrap then produced `["[not json]"]`: a valid array holding one garbage
element, which the module's own notes call worse than leaving it alone, and which
hides the real type error from the validator behind a successful-looking call.
The parse now runs on the trimmed value and requires success, and a bracket-shaped
string that failed to parse is never wrapped. The TypeScript port did this too, so
both were fixed together; only one of the six new parity tests fails against
v1.0.4, and it is this one.

**The hook had no defined decision on unparseable input.**
`printf 'not json' | bash adapters/claude-code/pre_tool_use.sh` exited 5 with
empty stdout under `set -e`, because jq's parse error killed the script at the
first extraction. `printf ''` answered `proceed`. The same "nothing to inspect"
case behaved two different ways, and one of them was no answer at all. It now
proceeds, matching the existing no-tool-name path: the hook's only available
failure mode is a spurious block, and there is nothing to inspect. The DeepSeek
Harness hook had the identical hole and carries the same guard.

## The one thing deliberately not changed

The copied stringified-array select tests `startswith("[")`, so a stringified
array with *leading* whitespace (` ["a.txt"] `) is still missed by both pre-tool
hooks, while the library repairs it and `post_tool_use.sh` detects it. That is a
missed detection rather than a false positive: the call goes to the executor, the
executor rejects the string, and the model retries, so it costs a turn instead of
a corrupted call. It is left alone because the brief for this release was to copy
the DeepSeek Harness selects verbatim, and changing a published adapter's
detection behaviour belongs in its own release with its own test. The fix is a
one-token change, `startswith("[")` to `test("^\\s*\\[")`, in both hooks and in
`post_tool_use.sh`.

## Verification

```
98 passed          (Python, 48 existing + 50 new)
27 pass, 0 fail    (TypeScript parity, 21 existing + 6 new)
module self-test   passes
bash -n            clean on all three hooks
compileall         clean
differential run   0 of 39 cases divergent (8 of 30 before)
```

Each fix was reverted individually against the new tests to confirm the
corresponding tests fail on v1.0.4's code: 34 Python tests fail, and 1 TypeScript
parity test fails. The TypeScript side fails only on the bracket-shaped-string
case, because the TypeScript port was already right about the schema shapes and
the schema gate; those tests pin the Python side coming to meet it.

## Known limitations

| Limitation | Impact |
|---|---|
| Leading-whitespace stringified arrays are missed by both pre-tool hooks | Costs one retry, never corrupts a call. See above. |
| TypeScript still mutates the caller's dict | Its documented contract, and its only caller writes the returned object back itself. The Python side now copies; the repair outcomes are identical. |
| Repair walks top-level arguments only | Nested objects are left alone by both, pinned as behaviour. |
| The schema gate is `is not None`, not truthiness | A `{}` schema is therefore walked and yields no array fields, which is the same outcome as skipping it. |

