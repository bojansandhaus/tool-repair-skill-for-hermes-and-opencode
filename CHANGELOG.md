# Changelog

## 1.0.5 - 2026-10-09

Five high-severity defects fixed, plus one medium. Each is pinned by a test that
fails against v1.0.4; the suites grew from 48 to 98 (Python) and from 21 to 27
(TypeScript parity).

### Fixed

- **The Claude Code hook blocked legitimate tool calls.** Its stringified-array
  check was `test("^\\s*\\[")`, satisfied by any value that merely starts with a
  bracket, so a `writeFile` content field holding `"[1, 2] and [3, 4]"` was
  blocked. Its auto-link check was `test("\\[[^]]+\\]\\(https?://")` with no
  `\1` backreference, so *any* markdown link was flagged and a call carrying
  `see [click](https://example.com)` was blocked too. A documentation-writing
  agent was blocked by both, and its only escape was to corrupt its own output.
  Both selects now carry the corrected forms the DeepSeek Harness adapter
  already shipped in fcdee58: `try (getpath($p) | fromjson | type) == "array"`
  and the `\1` backreference. That commit's own message said the looser Claude
  Code check still had the false positive and was deliberately left alone; this
  closes it. Genuine stringified arrays and degenerate auto-links still block.
- **The Python core raised on valid JSON Schema shapes the TypeScript port
  survives.** `_expected_array_fields` read `field_schema.get("type", "")` with
  no type guard and `variant.get("type")` with no dict guard, and neither helper
  survived `"properties": null`. A boolean field schema (`true`), a field schema
  that is a string, or an `anyOf` that is an object all raised `AttributeError`
  or `TypeError`, taking the whole tool call down. Its own sibling
  `_null_is_removable` guarded some of this, which made the omission an internal
  inconsistency rather than a policy. All of these shapes are now walked with
  the same guards the TypeScript port uses; a union with a null entry still
  declares an array, so the guards swallow no real declaration.
- **`_null_is_removable` had the same unguarded paths and is fixed with it.** A
  boolean or string field schema raised there too, and a `required` given as a
  bare string became `set("files")` = {'f','i','l','e','s'}, preserving a null
  on a field named "f". The TypeScript port already ignored a non-array
  `required`; Python now does too.
- **Python and TypeScript disagreed on the two schema-gated repairs.** The two
  schema-aware repairs are now gated on `tool_schema is not None`, mirroring the
  TypeScript port's `if (toolSchema)`, so the gate reads as a decision instead of
  being implied through an empty field set. More importantly, Python compared
  `type == "array"` only, so a `{"type": ["string","array"]}` union was invisible
  to it and the bare-string and empty-object repairs silently did not fire there
  while the TypeScript port applied them. A type union that names `"array"` is
  now honoured, which is what the null walker already did for a union naming
  `"null"`. A 39-case differential run of both implementations on identical
  inputs went from 8 disagreements to 0.
- **The Python core mutated the caller's dict, so the documented write-back
  pattern silently discarded every repair.** `original = dict(function_args)`
  was computed and never read, and the repairs wrote into the caller's dict in
  place, so in the wiring SKILL.md documents, `fixed` and `parsed` were the same
  object and `if fixed != parsed` was always False: the repaired JSON was never
  written back and the model was never told. `repair_function_args` now deep
  copies its input, matching the `__main__` self-test which already deep-copied
  for exactly this reason, and the dead `original` is gone. The caller's dict is
  never mutated, nested values included. The TypeScript port keeps its
  documented in-place mutation, and its only caller writes the returned object
  back itself.
- **A non-JSON bracketed string was wrapped as a real array element.** The test
  was `v.strip().startswith("[")` but the parse was of the untrimmed `v`, and the
  bare-string wrap then turned `" [not json] "` into `["[not json]"]` — a valid
  array holding one garbage element, which the module's own notes call worse
  than leaving it alone, and which hides the real type error from the validator.
  The parse now runs on the trimmed value and requires success, and a
  bracket-shaped string that failed to parse is never wrapped. Both
  implementations did this, so both were fixed together.

### Changed

- The Claude Code pre-tool hook now answers a decision on unparseable input.
  `printf 'not json' | bash adapters/claude-code/pre_tool_use.sh` exited 5 with
  empty stdout under `set -e` (jq's parse error killed the script at the first
  extraction), while `printf ''` answered `proceed`: the same "nothing to
  inspect" case behaved two different ways, and one of them was no answer at
  all. The hook now proceeds, matching the existing no-tool-name path. The same
  guard was added to the DeepSeek Harness hook, which had the identical hole.
- Version bookkeeping. `SKILL.md`'s frontmatter said `version: 1.3.0`, a value
  set before this repository's 1.0.x release line existed (a09e196, 2026-06-27)
  and never updated across v1.0.1, v1.0.2 or v1.0.3, so it described no released
  version. It is re-synced to 1.0.5, as is `references/plugin.yaml`, which still
  said 1.0.1. The CHANGELOG's "Unreleased" heading held exactly the content of
  `RELEASE_NOTES_v1.0.4.md` and is now dated 1.0.4; the v1.0.4 tag and GitHub
  release were never cut and are not cut here.

### Added

- `tests/test_tool_repair.py`: the malformed-schema matrix, the no-schema gate
  cases, the type-union cases, the caller-dict and write-back tests, the
  bracketed-junk cases, the hook's two false-positive cases with their
  true-positive controls, and the unparseable-stdin decision contract.
- `tests/parity.test.ts`: the malformed-schema matrix asserted value-for-value
  (not just `doesNotThrow`), the type-union and no-schema cases, and the
  bracket-shaped-string case. One of these fails against v1.0.4's
  `tool_repair.ts`; the rest pin the side that was already right.
- `tests/test_deepseek_harness_adapter.py`: the unparseable-stdin contract.
- `README.md`: a safety-guarantee line for the new bracket-shaped-junk boundary.

## 1.0.4 - 2026-10-06

- **Added a DeepSeek Harness adapter** (`adapters/deepseek-harness/`): a
  `PreToolUse` command hook plus registration instructions for the bridge
  plugins the harness ships (`@deepseek-ai/dsh-hooks-claude-code`,
  `@deepseek-ai/dsh-hooks-codex`). Written against the shipped `0.2.0-rc.2`.
  The harness seam can deny a call but cannot rewrite its arguments, the same
  limit the Claude Code adapter has, so this adapter blocks with a message
  naming the offending fields and the model retries on the next turn.
- **The DeepSeek Harness adapter reads different payload keys from the Claude
  Code one, and that is load-bearing.** The harness bridges emit `tool_name` and
  `tool_input`; the Claude Code adapter reads `.tool` and `.input`, which the
  harness never sends. Feeding a harness-shaped payload with a null field to
  the Claude Code hook returns `proceed`, so reusing it would have silently
  disabled every repair. Pinned by a test.
- The harness adapter's stringified-array check requires the value to actually
  parse as a JSON array, matching the library's own bar. A leading `[` alone
  is not enough, otherwise the hook would block legitimate bracketed prose like
  `[1, 2] and [3, 4]`, which the repair layer must leave alone. The Claude Code
  hook still uses the looser check; see below.
- README and SKILL.md: adapter tables, install section, dependency table and
  roadmap updated for the fourth framework. CI now syntax-checks the new hook.
- `tests/test_deepseek_harness_adapter.py`: 14 tests covering the payload keys,
  all three patterns, the valid-content cases, and the output contract.

Documentation and CI corrections. This repository is a public, standalone
project: what it documents is how to wire the repair layer into a Hermes
harness, and every claim below is about this repository's own code, tests and
CI.
- The Hermes integration section in `SKILL.md` and `README.md` was rewritten. It
  previously described the wiring in the language of a private audit (checking
  one particular Hermes checkout and reporting what was or was not found there),
  which is not a useful thing to tell a public reader. It now documents the
  integration as what it is: two calls into the harness, the exact code for
  both, and why the second one is written by hand today.
- Removed every mention of a secondary code host and of its publishing guide.
  `SKILL.md` now names the GitHub repository as the single authoritative
  remote.
- Added a `typescript` job to CI. `.github/workflows/ci.yml` had no Node, npx or
  parity step, so `tests/parity.test.ts` (21 tests) had never run anywhere
  automatically. The job uses `actions/setup-node@v4` with Node 22 and runs
  `npx --yes -p typescript@5 -p tsx@4 tsx --test tests/parity.test.ts`, the same
  command documented in the parity test's header comment.
- Removed the `agent.tool_repair: true` instruction from `README.md`. There is no
  such configuration key, so it was a dead instruction.
- Corrected the repair count from four to five in `SKILL.md` (frontmatter
  description included, since that is what a skill loader matches on) and in
  `adapters/opencode/tool_repair.ts`. Two of the five are schema-gated, which
  the old text called universal.
- Corrected the library size claim: `references/tool_repair.py` is 337 lines,
  not 297.
- Replaced the dead `x.com/CommandCodeAI/status/1927626163496718571` link
  (HTTP 404) in `README.md` with two live CommandCode URLs. Attribution is kept,
  since the approach is theirs.
- Rewrote the production-scale claims. "Over 56,000 repair invariants" and "a
  trillion tokens per month" had no supporting artefact here and did not match
  the source material, which reports roughly 1M repaired tool calls per 1T
  tokens. Those figures are now attributed to the author with a resolvable
  citation, and a third-party decompilation count is given in their place.
- Added `.pytest_cache/` to `.gitignore`.
- Updated `CONTRIBUTING.md`: the Python suite is 34 tests, not 24, and the
  TypeScript parity command is documented alongside it.

## 1.0.1 - 2026-10-04

Four defects fixed. All four were found by reviewing the code and are now pinned
by `tests/test_tool_repair.py`, which fails against v1.0.0.

### Fixed

- **Null deletion ignored the schema.** `_strip_null_fields` documented itself as
  removing nulls "for optional fields" but deleted every null, including one on a
  field the schema marks `required` or whose type union contains `"null"`. That
  turned a rejected tool call into a differently invalid one and hid the error
  from the validator. Nulls are now removed only where deletion is the actual
  repair. The same defect existed in the TypeScript adapter's `applyNullOmit`,
  which additionally ran before the schema was ever consulted; both are fixed and
  their behaviour now matches.
- **The Claude Code hook never blocked anything.** Its `jq` used
  `paths(scalars) | select(getpath($p) == null)`. This build of `jq` does not emit
  a path for a null value, so the pattern could not match and the hook approved
  every call it was installed to catch, silently. Now `paths(. == null)`, verified
  to fire on a real null-valued argument.
- **Two more hook patterns were dead for the same reason** (stringified arrays and
  markdown auto-links): `paths(scalars)` skipped the null leaf and the remaining
  string paths, so all three patterns were unreachable. Rewritten as
  `paths(type == "string")`.
- **Hook emitted malformed JSON on hostile field names.** The block response was
  built by string interpolation, so a quote or backslash in a field name produced
  invalid JSON. Now built with `jq -nc --arg`.
- **`deduplicate_repair_notes` dropped the tool name**, printing
  `[Hermes repaired: ]`. It now takes and forwards the name.

### Changed

- The repair pipeline's dataflow is explicit. Every migration previously reassigned
  a local `args` that was then never read, which read as if each step threaded a
  value forward. The steps mutate one dict in place, in order, and the ordering
  comment now explains that ordering is load-bearing.
- `echo "$JSON" | jq` replaced with `printf '%s'` throughout the hook so arguments
  beginning with `-` or `\` cannot be mangled.
- `SKILL.md` and `README.md` documented null deletion as unconditional. Both now
  state the boundary.

### Added

- `tests/test_tool_repair.py`, 24 tests covering both languages' logic and the
  hook end to end. Four of them fail against v1.0.0.

### Not changed

- No behaviour outside the four defects above. `local_model`-style settings, note
  text, and the array repairs are untouched.

## 1.0.2 - 2026-10-05

- Added MIT `LICENSE` (the README and manifest claimed MIT; the file did not exist).
- Added CI, `SECURITY.md` and `CONTRIBUTING.md`.
- Corrected the README: the "valid inputs are never touched" claim, the "Four Patterns" heading that listed five rules, the roadmap entry for null handling that shipped in v1.0.1, and the `deduplicate_repair_notes` description.

## 1.0.3 - 2026-10-05

- Fixed the OpenCode adapter: it read the tool name from `output.tool` and keyed notes on `input.id`, neither of which exists in the published `@opencode-ai/plugin` hook types, so `toolName` was undefined on every call and concurrent calls to one tool stole each other's notes.
- Closed four Python/TypeScript divergences: `anyOf`/`oneOf` array variants, the bare-host autolink form, and Python's missing whitespace trim before the stringified-array test.
- Fixed `post_tool_use.sh`, which still used the dead `paths(scalars)` null check that v1.0.1 fixed in `pre_tool_use.sh` only.
- Added a TypeScript CI job. Nothing ran the TypeScript before, so every divergence above shipped green.
  Corrected in Unreleased: that job was claimed here but was not present in
  `.github/workflows/ci.yml` until the current change. The TypeScript parity
  suite had therefore never run in CI up to this point.
- Documented that the OpenCode adapter passes no schema, so its two schema-aware repairs cannot fire. Not fixed: it needs the tool registry to supply one.
