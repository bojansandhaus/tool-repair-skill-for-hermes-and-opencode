# tool-repair-skill-for-hermes-and-opencode v1.0.2

Repository-quality release. The correctness fixes shipped in v1.0.1; this adds the
project files that release was missing.

## What v1.0.1 already contained

Recorded here so the two releases are not confused. v1.0.1 carried the three
behavioural fixes, each with regression coverage in `tests/test_tool_repair.py`
(24 tests):

- `_strip_null_fields` ignored the schema, so a required or nullable field was
  deleted along with the optional ones. Validation then reported a different
  error than the one the caller actually made. It now only removes genuinely
  optional nulls and leaves required and nullable fields for validation to
  report.
- The Claude Code hook detected null values with `paths(scalars)`, which never
  emits null paths. All three of the hook's repair patterns were dead and it
  silently allowed every null-valued call. Stringified-array and Markdown
  autolink detection were corrected the same way, to inspect string paths.
- The hook built its JSON response by string interpolation, which produced
  malformed output for any tool name or path containing a quote or backslash. It
  now uses `jq -nc --arg`.

## Added in this release

- `LICENSE`: MIT. The README and the skill manifest both claimed MIT and the file
  did not exist.
- `.github/workflows/ci.yml`: runs the test suite, the module self-test, and
  `bash -n` over both Claude Code adapters.
- `SECURITY.md`: what the skill does with tool arguments, and the explicit
  statement that it is not a security boundary.
- `CONTRIBUTING.md`: including the requirement that the Python and TypeScript
  implementations stay behaviourally identical, since the skill ships both.

## Corrected in this release

- The README claimed "Valid inputs are never touched". The implementation makes
  five narrow, tested repairs, and a valid input is not necessarily left alone.
  The claim now describes what actually happens.
- The patterns section was headed "Four Patterns" while listing five rules.
- The roadmap marked schema-aware null handling as pending; it was implemented in
  v1.0.1.
- The implementation description said `deduplicate_repair_notes` dropped the tool
  name. It retains it.

## Verification

```
24 tests pass
module self-test passes
bash -n adapters/claude-code/pre_tool_use.sh adapters/claude-code/post_tool_use.sh
```
