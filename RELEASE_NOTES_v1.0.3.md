# tool-repair-skill-for-hermes-and-opencode v1.0.3

Parity and adapter-correctness release, from an independent review of the Python
and TypeScript implementations against each other. Every divergence below was
reproduced by running both implementations on the same input, and each is now
pinned by a test that fails against v1.0.2.

## The OpenCode adapter could not repair arguments at all

`plugin.ts` read the tool name from `output.tool` and keyed repair notes on
`input.id`. Neither exists. The published `@opencode-ai/plugin` types are:

```
"tool.execute.before"?: (input: { tool, sessionID, callID },
                         output: { args: any }) => Promise<void>
```

So `toolName` was `undefined` on every call, and two concurrent calls to the same
tool overwrote each other's notes, meaning one call could be told about a defect
it did not have while its real repair went unmentioned. The adapter now reads
`input.tool`, keys on `input.callID`, and delivers notes by prepending them to the
tool output rather than assigning to a property the output object does not have.

The adapter also passed no schema to `repairFunctionArgs`, so both schema-aware
repairs could never fire there. That one is **not** fixed: it needs the tool
registry or SDK to supply the schema, which is a real integration question rather
than a bug. It is recorded in the README and pinned by a test that documents the
current behaviour.

## Four Python/TypeScript divergences

| input | v1.0.2 Python | v1.0.2 TypeScript |
| --- | --- | --- |
| `{"anyOf":[{"type":"array"}]}` schema, `{"files":"a.txt"}` | `["a.txt"]` | unchanged |
| `{"oneOf":[{"type":"array"}]}` schema, same | `["a.txt"]` | unchanged |
| `{"files":'["a.txt"] '}` with array schema | `['["a.txt"] ']` | `["a.txt"]` |
| `"/x/[notes.md](http://notes.md)"` | `/x/notes.md` | unchanged |

- The TypeScript schema walker now walks `anyOf`/`oneOf` like Python does, with
  `Array.isArray` guards so a malformed schema cannot throw.
- The autolink regex is now `(?:[^/]+/)?`, a superset of both previous forms. The
  Python side matched a bare host only and the TypeScript side required a path
  segment, so neither matched what the other documented; the bare-host form is
  the one asserted by the Python module's own self-test.
- Python now strips before testing for a stringified array. With a trailing
  space it used to wrap the junk string as a single array element, which is worse
  than leaving the call alone.

## post_tool_use.sh was still silently broken

v1.0.1 fixed the null detection in `pre_tool_use.sh` and missed this hook. All
three of its patterns used `paths(scalars)`, which emits a path only for non-null
scalars, so the null check could never fire. Verified on jq 1.8.1:
`{"limit":null,"s":"x"} | [paths(scalars)]` is `[["s"]]` and the select returns
`[]`. The telemetry this hook exists to collect reported no null fields, ever.
Fixed and asserted against the real hook, fed a real envelope on stdin.

## CI never ran the TypeScript

The Python suite ran; the TypeScript did not, so any divergence above shipped
green. A second CI job now runs the parity suite.

Correction, added after publication: this release note originally claimed that
job type-checked both adapters under `--strict` and shipped in v1.0.3. It did
not. `.github/workflows/ci.yml` at v1.0.3 contained no Node step at all, so the
TypeScript parity suite had still never run in CI when this release was
published. The job arrived later, on `actions/setup-node@v4` with Node 22,
running `npx --yes -p typescript@5 -p tsx@4 tsx --test tests/parity.test.ts`. No
lockfile is committed. The `tsc --strict` claim below is likewise a local check,
not a CI gate.

## Verification

```
34 passed          (Python, 24 in v1.0.2 + 10 new)
21 passed          (TypeScript parity, new)
tsc --strict       clean on tool_repair.ts and plugin.ts
bash -n            clean on both adapters
module self-test   passes
```

Each fix was reverted individually to confirm the corresponding tests fail:
removing the TypeScript autolink group fails 7 parity tests, reverting the Python
trim fails 1, reverting the post-hook null pattern fails 3.

## Also checked, found sound

Nested argument objects are top-level-only in both, idempotent across repeated
scans, no mutation on a clean call, unicode-safe, large payloads under 10 ms, and
real markdown links left alone. Those are now pinned as behaviour rather than
left implicit.
