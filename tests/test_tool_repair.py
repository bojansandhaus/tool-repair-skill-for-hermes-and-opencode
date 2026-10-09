"""Regression tests for tool_repair.py and the Claude Code hook.

Run with: python -m pytest tests -q   (or python tests/test_tool_repair.py)

Every test here corresponds to a defect that shipped in the original code:

- null deletion removed REQUIRED and NULLABLE fields, not just optional ones
- deduplicate_repair_notes dropped the tool name, printing "[Hermes repaired: ]"
- the shell hook's jq used paths(scalars), which never emits null paths, so the
  hook approved every call it was installed to catch
- the hook built its JSON by interpolation, so a quote in a field name broke it
- the OpenCode adapter read the tool name from output.tool, which does not exist
  in the published hook types, so toolName was undefined on every call
- the OpenCode adapter keyed repair notes on input.id, which is not in the
  published types either, so two concurrent calls to one tool stole each other's
  notes
- post_tool_use.sh still used paths(scalars) for its null check. v1.0.1 fixed
  pre_tool_use.sh and missed this one, so the telemetry it exists to collect
  reported no null fields, ever
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "references"))

from tool_repair import (  # noqa: E402
    deduplicate_repair_notes,
    make_repair_note_block,
    repair_function_args,
)

HOOK = REPO / "adapters" / "claude-code" / "pre_tool_use.sh"
POST_HOOK = REPO / "adapters" / "claude-code" / "post_tool_use.sh"


def repair(args, schema=None):
    return repair_function_args("t", dict(args), schema)


# --------------------------------------------------------------------------
# null deletion must respect the schema
# --------------------------------------------------------------------------

def test_null_on_optional_field_is_removed():
    out, notes = repair({"command": "ls", "timeout": None},
                        {"properties": {"command": {"type": "string"},
                                        "timeout": {"type": "integer"}}})
    assert out == {"command": "ls"}
    assert len(notes) == 1


def test_null_on_required_field_is_preserved():
    """Deleting a required key turns a bad call into a differently invalid one."""
    out, notes = repair({"name": None},
                        {"properties": {"name": {"type": "string"}}, "required": ["name"]})
    assert out == {"name": None}
    assert notes == [], "a required field must not be silently deleted"


def test_null_on_nullable_field_is_preserved():
    """The schema admits null here, so null is a valid value, not a mistake."""
    out, notes = repair({"tags": None},
                        {"properties": {"tags": {"type": ["array", "null"]}}})
    assert out == {"tags": None}
    assert notes == []


def test_nullable_via_anyof_is_preserved():
    out, notes = repair({"tags": None},
                        {"properties": {"tags": {"anyOf": [{"type": "array"},
                                                          {"type": "null"}]}}})
    assert out == {"tags": None}
    assert notes == []


def test_explicit_null_type_is_preserved():
    out, notes = repair({"x": None}, {"properties": {"x": {"type": "null"}}})
    assert out == {"x": None}
    assert notes == []


def test_no_schema_keeps_original_behaviour():
    """Without a schema every field is optional, so nulls still go."""
    out, notes = repair({"timeout": None})
    assert out == {}
    assert len(notes) == 1


# --------------------------------------------------------------------------
# the other three repairs still behave
# --------------------------------------------------------------------------

ARRAY_SCHEMA = {"properties": {"files": {"type": "array"}}}


def test_stringified_array_with_surrounding_whitespace_is_parsed():
    """Regression: the test was startswith/endswith without stripping, so a
    trailing space made the whole junk string one array element, which is worse
    than leaving the call alone. The TypeScript port already trimmed."""
    for value in ('["a.txt"] ', ' ["a.txt"]', ' ["a.txt"] '):
        fixed, notes = repair_function_args(
            "read", {"files": value},
            {"type": "object", "properties": {"files": {"type": "array"}}},
        )
        assert fixed["files"] == ["a.txt"], value
        assert notes, value


def test_a_string_with_brackets_but_no_json_is_not_parsed_as_an_array():
    """Guards the other direction: trimming must not make the check sloppier.

    Nothing may claim to have repaired this value, and the value itself must
    survive verbatim. The assertion used to be `assert notes`, which passed only
    because the bare-string wrap then turned the junk into `["[not json]"]` — a
    real array holding one garbage element, which is precisely the outcome this
    case exists to rule out. It now asserts the outcome instead of a side effect
    that happened to be non-empty.
    """
    fixed, notes = repair_function_args(
        "read", {"files": " [not json] "},
        {"type": "object", "properties": {"files": {"type": "array"}}},
    )
    assert fixed == {"files": " [not json] "}, "junk must survive untouched"
    assert notes == [], "a non-JSON bracket string is not a repair"


def test_stringified_array_is_parsed():
    out, notes = repair({"files": '["a.txt","b.txt"]'}, ARRAY_SCHEMA)
    assert out["files"] == ["a.txt", "b.txt"]
    assert len(notes) == 1


def test_bare_string_is_wrapped():
    out, notes = repair({"files": "foo.txt"}, ARRAY_SCHEMA)
    assert out["files"] == ["foo.txt"]


def test_empty_object_becomes_empty_array():
    out, _ = repair({"files": {}}, ARRAY_SCHEMA)
    assert out["files"] == []


def test_markdown_autolink_is_unwrapped():
    out, notes = repair({"filePath": "/Users/x/[notes.md](http://notes.md)"})
    assert out["filePath"] == "/Users/x/notes.md"
    assert len(notes) == 1


def test_real_markdown_link_is_untouched():
    text = "see [click](https://example.com)"
    out, notes = repair({"content": text})
    assert out["content"] == text
    assert notes == []


def test_plain_bracketed_prose_is_not_parsed_as_json():
    out, notes = repair({"content": "[1, 2] and [3, 4]"})
    assert out["content"] == "[1, 2] and [3, 4]"
    assert notes == []


def test_valid_input_produces_no_notes():
    out, notes = repair({"command": "ls", "timeout": 30})
    assert notes == []
    assert out == {"command": "ls", "timeout": 30}


def test_repair_does_not_mutate_the_callers_dict_when_notes_are_empty():
    original = {"command": "ls"}
    repair(original)
    assert original == {"command": "ls"}


# --------------------------------------------------------------------------
# a malformed or boolean schema must not raise
# --------------------------------------------------------------------------

# Every shape here is legal JSON Schema input: a boolean schema, a null field
# schema, and malformed unions. The TypeScript port survives all of them
# (tests/parity.test.ts, "a malformed schema does not throw"); the Python one
# raised AttributeError or TypeError on each, because the walker assumed every
# field schema was a dict with a string `type` and every anyOf/oneOf entry a
# dict, while its own sibling `_null_is_removable` guarded both.
MALFORMED_SCHEMAS = [
    ("properties-null", {"type": "object", "properties": None}),
    ("field-null", {"type": "object", "properties": {"files": None}}),
    ("field-true", {"type": "object", "properties": {"files": True}}),
    ("field-false", {"type": "object", "properties": {"files": False}}),
    ("field-string", {"type": "object", "properties": {"files": "array"}}),
    ("field-int", {"type": "object", "properties": {"files": 3}}),
    ("anyof-null", {"type": "object", "properties": {"files": {"anyOf": None}}}),
    ("anyof-dict", {"type": "object", "properties": {"files": {"anyOf": {"type": "array"}}}}),
    ("oneof-null-entry", {"type": "object", "properties": {"files": {"oneOf": [None]}}}),
]


@pytest.mark.parametrize("name,schema", MALFORMED_SCHEMAS, ids=[n for n, _ in MALFORMED_SCHEMAS])
def test_a_malformed_or_boolean_schema_does_not_raise(name, schema):
    """The guards must not turn into a silent no-op either: these still repair."""
    fixed, notes = repair_function_args("t", {"files": "a.txt"}, schema)
    assert fixed == {"files": "a.txt"}, name
    assert notes == [], name


def test_the_same_guards_do_not_swallow_a_real_array_declaration():
    """A union with a null entry still declares an array, so the repair fires."""
    fixed, notes = repair_function_args(
        "t", {"files": "a.txt"},
        {"type": "object", "properties": {"files": {"anyOf": [None, {"type": "array"}]}}},
    )
    assert fixed == {"files": ["a.txt"]}
    assert len(notes) == 1


def test_a_type_union_that_names_array_is_recognised():
    """`{"type": ["string","array"]}` is the same declaration the null walker
    already honours for a union naming "null". The TypeScript port checks the
    union, so Python leaving it alone was a divergence, not a platform choice."""
    for schema in (
        {"properties": {"files": {"type": ["string", "array"]}}},
        {"properties": {"files": {"type": ["array", "string"]}}},
    ):
        fixed, notes = repair_function_args("t", {"files": "a.txt"}, schema)
        assert fixed == {"files": ["a.txt"]}, schema
        fixed_obj, _ = repair_function_args("t", {"files": {}}, schema)
        assert fixed_obj == {"files": []}, schema


# --------------------------------------------------------------------------
# the two schema-gated repairs stay dormant without a schema
# --------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    {"files": {}},
    {"files": "foo.txt"},
    {"files": " [not json] "},
    {"files": {"a": 1}},
])
def test_no_schema_leaves_the_schema_gated_repairs_dormant(args):
    """SKILL.md:50 and README.md:135 both promise that without a schema only the
    three universal repairs run. The TypeScript port gates these two on
    `if (toolSchema)`; Python now states the same gate explicitly."""
    for schema in (None, {}, {"type": "object"}):
        fixed, notes = repair_function_args("t", dict(args), schema)
        assert fixed == args, (args, schema)
        assert notes == [], (args, schema)


def test_a_type_only_schema_still_gates_the_array_repairs():
    """A schema with no `properties` declares no array fields, so nothing fires."""
    fixed, notes = repair_function_args("t", {"files": "a.txt"}, {"type": "object"})
    assert fixed == {"files": "a.txt"}
    assert notes == []


# --------------------------------------------------------------------------
# the caller's dict is not the caller's problem
# --------------------------------------------------------------------------

def test_repair_does_not_mutate_the_callers_dict_even_when_it_repairs():
    """Every repair used to write straight into the dict the caller passed in."""
    caller = {"path": "/tmp/x", "limit": None, "files": '["a.txt"]'}
    snapshot = json.dumps(caller, sort_keys=True)
    fixed, notes = repair_function_args(
        "t", caller,
        {"properties": {"files": {"type": "array"}}},
    )
    assert notes, "the repairs must have fired for this case to mean anything"
    assert json.dumps(caller, sort_keys=True) == snapshot, (
        "the caller's dict was mutated: %r" % (caller,)
    )
    assert fixed == {"path": "/tmp/x", "files": ["a.txt"]}


def test_fixed_is_a_distinct_object_from_the_parsed_arguments():
    """`fixed is parsed` made the documented write-back compare an object with
    itself, so it could never fire."""
    parsed = {"command": "ls", "timeout": None}
    fixed, _ = repair_function_args("t", parsed, None)
    assert fixed is not parsed


def test_the_documented_write_back_pattern_writes_the_repaired_json():
    """The wiring SKILL.md:260-264 tells a harness to write. With the repairs
    mutating the caller's dict, `fixed != parsed` was always False and the
    repaired JSON was silently discarded on every call."""
    function = {"name": "readFile", "arguments": '{"path": "/tmp/x", "limit": null}'}
    tool_schema = {"properties": {"path": {"type": "string"},
                                  "limit": {"type": "integer"}}}
    before = function["arguments"]
    parsed = json.loads(function["arguments"])
    fixed, notes = repair_function_args(function["name"], parsed, tool_schema)
    assert fixed != parsed, "a repair happened, so the comparison must see it"
    if fixed != parsed:
        function["arguments"] = json.dumps(fixed)
    assert function["arguments"] != before
    assert json.loads(function["arguments"]) == {"path": "/tmp/x"}
    assert notes


def test_a_clean_call_still_writes_nothing_back():
    """The other half of the contract: no repair means no write-back, so the
    deep copy must compare equal rather than merely being a different object."""
    function = {"name": "readFile", "arguments": '{"path": "/tmp/x"}'}
    before = function["arguments"]
    parsed = json.loads(function["arguments"])
    fixed, notes = repair_function_args(function["name"], parsed, None)
    assert notes == []
    assert fixed == parsed
    if fixed != parsed:
        function["arguments"] = json.dumps(fixed)
    assert function["arguments"] == before


def test_nested_values_are_copied_not_shared():
    """A shallow copy would still leak the repair into the caller's nesting."""
    caller = {"opts": {"nested": ["keep"]}}
    fixed, _ = repair_function_args("t", caller, None)
    assert fixed["opts"] is not caller["opts"]


# --------------------------------------------------------------------------
# bracketed junk is never squeezed into an array element
# --------------------------------------------------------------------------

@pytest.mark.parametrize("junk", [
    " [not json] ",
    "[not json]",
    "[1, 2] and [3, 4]",
    "[a.md] and [b.md]",
])
def test_bracketed_junk_is_left_alone_under_an_array_schema(junk):
    """Wrapping any of these yields `["[not json]"]`: a valid array holding one
    garbage element, which hides the real type error from the validator. The
    TypeScript port wrapped them too, so both were fixed together."""
    fixed, notes = repair_function_args(
        "t", {"files": junk},
        {"type": "object", "properties": {"files": {"type": "array"}}},
    )
    assert fixed == {"files": junk}, junk
    assert notes == [], junk


def test_a_string_that_does_parse_as_an_array_is_still_parsed():
    """The guard must not swallow the repair it sits next to."""
    for value in ('["a.txt"]', ' ["a.txt"]', '["a.txt"] ', ' ["a.txt"] '):
        fixed, notes = repair_function_args(
            "t", {"files": value},
            {"type": "object", "properties": {"files": {"type": "array"}}},
        )
        assert fixed == {"files": ["a.txt"]}, value
        assert len(notes) == 1, value


def test_a_bare_string_is_still_wrapped():
    """The ordinary case: not bracket-shaped, so the wrap still applies."""
    fixed, notes = repair_function_args(
        "t", {"files": " foo.txt "},
        {"type": "object", "properties": {"files": {"type": "array"}}},
    )
    assert fixed == {"files": [" foo.txt "]}
    assert len(notes) == 1


# --------------------------------------------------------------------------
# repair notes
# --------------------------------------------------------------------------

def test_note_block_carries_the_tool_name():
    block = make_repair_note_block(["[repair: null values removed]"], "terminal")
    assert "terminal" in block


def test_dedup_preserves_the_tool_name():
    """Regression: this printed "[Hermes repaired: ]" with the name missing."""
    out = deduplicate_repair_notes("", ["[repair: null values removed]"], "terminal")
    assert "[Hermes repaired: terminal]" in out


def test_dedup_still_suppresses_a_repeated_note():
    note = ["[repair: null values removed]"]
    once = deduplicate_repair_notes("", note, "terminal")
    twice = deduplicate_repair_notes(once, note, "terminal")
    assert once == twice


def test_dedup_with_no_new_notes_is_a_noop():
    assert deduplicate_repair_notes("body", [], "terminal") == "body"


# --------------------------------------------------------------------------
# the Claude Code hook
# --------------------------------------------------------------------------

def run_hook(payload):
    proc = subprocess.run(
        ["bash", str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True,
    )
    return json.loads(proc.stdout)


def run_hook_raw(stdin_text):
    """Feed the hook raw bytes and report the whole result, so exit codes and
    empty stdout are visible rather than being swallowed by json.loads."""
    return subprocess.run(
        ["bash", str(HOOK)], input=stdin_text, capture_output=True, text=True,
    )


@pytest.mark.parametrize("bad", [
    {"limit": None},
    {"paths": '["a","b"]'},
    {"path": "/x/[notes.md](http://notes.md)"},
    {"filePath": "[notes.md](http://notes.md)"},
])
def test_hook_blocks_the_patterns_it_exists_to_catch(bad):
    """Regression: paths(scalars) omitted null paths so this always proceeded."""
    assert run_hook({"tool": "readFile", "input": bad})["decision"] == "block"


def test_hook_allows_a_clean_call():
    assert run_hook({"tool": "readFile", "input": {"path": "/tmp/x"}})["decision"] == "proceed"


# --- the two false positives -------------------------------------------------
# The stringified-array test was `test("^\\s*\\[")`, which is satisfied by any
# value that merely starts with a bracket, so a writeFile content field holding
# "[1, 2] and [3, 4]" was blocked. The auto-link test had no `\1` backreference,
# so ANY markdown link was flagged. A doc-writing agent was blocked on both and
# its only escape was to corrupt its own output. The DeepSeek Harness adapter
# already carried both corrected forms; these pin the same two in Claude Code.

@pytest.mark.parametrize("content", [
    "[1, 2] and [3, 4]",
    "a [b] c [d] e",
    "see [click](https://example.com)",
    "[notes.md](https://example.com/docs/notes.md)",
    "[notes.md](http://notes.md?raw=1)",
    "array literal [1,2] then prose",
    "[not json] and more",
])
def test_hook_leaves_valid_content_alone(content):
    out = run_hook({"tool": "writeFile", "input": {"content": content}})
    assert out["decision"] == "proceed", content
    assert "message" not in out, content


def test_hook_still_blocks_a_string_that_really_is_stringified_json():
    """The correction must not go quiet: `["a","b"]` and `[1, 2]` both parse as
    real arrays, so both are the genuine defect, not prose that looks like one."""
    for value in ('["a.txt","b.txt"]', "[1, 2]"):
        out = run_hook({"tool": "writeFile", "input": {"files": value}})
        assert out["decision"] == "block", value
        assert "stringified arrays" in out["message"], value
        assert "files" in out["message"], value


def test_hook_still_blocks_a_degenerate_autolink_in_both_url_forms():
    """The `\1` backreference is what separates a leak from a real link."""
    for value in ("/x/[notes.md](http://notes.md)",
                  "[notes.md](http://host/notes.md)",
                  "/x/[notes.md](https://notes.md)"):
        out = run_hook({"tool": "readFile", "input": {"path": value}})
        assert out["decision"] == "block", value
        assert "markdown auto-links" in out["message"], value


# --- every invocation must answer -------------------------------------------

@pytest.mark.parametrize("stdin_text", [
    "",
    "not json",
    '{"tool": "readFile", "input": {',
    "[1,2,3]",
    "null",
    "false",
    '"a string"',
    "42",
])
def test_hook_answers_a_decision_on_unparseable_stdin(stdin_text):
    """A decision is the contract. jq failing under `set -e` used to kill the
    script with exit 5 and EMPTY stdout, which is not a decision, while empty
    stdin answered `proceed`: the same "nothing to inspect" case, two answers."""
    proc = run_hook_raw(stdin_text)
    assert proc.returncode == 0, (stdin_text, proc.returncode, proc.stderr)
    assert proc.stdout.strip(), (stdin_text, "empty stdout is not a decision")
    out = json.loads(proc.stdout)
    assert out["decision"] == "proceed", stdin_text
    assert len(proc.stdout.strip().splitlines()) == 1, stdin_text


def test_hook_proceeds_without_a_tool_name():
    assert run_hook({"input": {"limit": None}})["decision"] == "proceed"


def test_hook_output_is_valid_json_for_a_hostile_field_name():
    out = run_hook({"tool": "readFile", "input": {'we"ird\\key': None}})
    assert out["decision"] == "block"
    assert isinstance(out["message"], str)


# --------------------------------------------------------------------------
# post_tool_use.sh: the null check was dead in v1.0.1
# --------------------------------------------------------------------------

def run_post_hook(payload, tmp_path):
    proc = subprocess.run(
        ["bash", str(POST_HOOK)], input=json.dumps(payload),
        capture_output=True, text=True,
        env={"CLAUDE_CODE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    log = tmp_path / "tool-repair-telemetry.log"
    return proc.stdout.strip(), (log.read_text() if log.exists() else "")


@pytest.mark.parametrize("bad,expected", [
    ({"limit": None}, "null=(limit)"),
    ({"files": '["a.txt"] '}, "stringified=(files)"),
    ({"opts": {}}, "empty_obj=(opts)"),
    ({"path": "/x/[notes.md](http://notes.md)"}, "autolink=(path)"),
])
def test_post_hook_detects_every_pattern_it_exists_to_collect(bad, expected, tmp_path):
    """All four were silent. `paths(scalars)` emits a path only for non-null
    scalars, so the null select could never fire; verified on jq 1.8.1 that
    `{"limit":null,"s":"x"} | [paths(scalars)]` is [["s"]]."""
    envelope = {"tool": "edit", "input": bad, "result": {"isError": False}}
    out, log = run_post_hook(envelope, tmp_path)
    assert expected in log, f"telemetry {log!r} missing {expected!r} (stdout {out!r})"


def test_post_hook_reports_a_clean_call_as_clean(tmp_path):
    envelope = {"tool": "edit", "input": {"limit": 5, "path": "notes.md"},
                "result": {"isError": False}}
    out, log = run_post_hook(envelope, tmp_path)
    assert "repairable patterns" not in out


def test_post_hook_survives_a_quote_in_a_field_name(tmp_path):
    envelope = {"tool": "edit", "input": {'we"ird': None}, "result": {"isError": False}}
    out, log = run_post_hook(envelope, tmp_path)
    assert "null=" in log


def test_both_hooks_are_free_of_the_dead_pattern():
    """Guard against paths(scalars) creeping back into either hook."""
    for hook in (HOOK, POST_HOOK):
        body = hook.read_text()
        code = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        assert "paths(scalars)" not in code, f"{hook.name} reintroduced paths(scalars)"


def test_opencode_plugin_reads_the_published_hook_types():
    """The adapter invented its own hook shape. Checked against the real
    @opencode-ai/plugin types, where `output` carries only `args` and the tool
    name and callID live on `input`."""
    plugin = (REPO / "adapters" / "opencode" / "plugin.ts").read_text()
    code = "\n".join(
        line for line in plugin.splitlines() if not line.lstrip().startswith("//")
    )
    assert "output.tool" not in code, "tool name must come from input.tool"
    assert "input.id" not in code, "notes must be keyed on input.callID"
    assert "input.callID" in code
    assert "input.tool" in code
