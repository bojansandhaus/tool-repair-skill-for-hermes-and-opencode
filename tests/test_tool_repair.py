"""Regression tests for tool_repair.py and the Claude Code hook.

Run with: python -m pytest tests -q   (or python tests/test_tool_repair.py)

Every test here corresponds to a defect that shipped in the original code:

- null deletion removed REQUIRED and NULLABLE fields, not just optional ones
- deduplicate_repair_notes dropped the tool name, printing "[Hermes repaired: ]"
- the shell hook's jq used paths(scalars), which never emits null paths, so the
  hook approved every call it was installed to catch
- the hook built its JSON by interpolation, so a quote in a field name broke it
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


@pytest.mark.parametrize("bad", [
    {"limit": None},
    {"paths": '["a","b"]'},
    {"path": "/x/[notes.md](http://notes.md)"},
])
def test_hook_blocks_the_patterns_it_exists_to_catch(bad):
    """Regression: paths(scalars) omitted null paths so this always proceeded."""
    assert run_hook({"tool": "readFile", "input": bad})["decision"] == "block"


def test_hook_allows_a_clean_call():
    assert run_hook({"tool": "readFile", "input": {"path": "/tmp/x"}})["decision"] == "proceed"


def test_hook_output_is_valid_json_for_a_hostile_field_name():
    out = run_hook({"tool": "readFile", "input": {'we"ird\\key': None}})
    assert out["decision"] == "block"
    assert isinstance(out["message"], str)


def test_hook_proceeds_without_a_tool_name():
    assert run_hook({"input": {"limit": None}})["decision"] == "proceed"