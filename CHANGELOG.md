# Changelog

## 1.0.1 — 2026-10-04

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
- Documented that the OpenCode adapter passes no schema, so its two schema-aware repairs cannot fire. Not fixed: it needs the tool registry to supply one.
