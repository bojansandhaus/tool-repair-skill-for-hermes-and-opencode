"""
tool_repair.py — Validate-then-repair for tool call arguments.

Drop this into Hermes' agent/ directory, then add ONE import + call in
the existing sanitize_tool_call_arguments function to upgrade from
"replace everything with {}" to the four semantic repairs.

Design: parse first, ship valid inputs untouched. Only spend repair
budget at paths the validator actually flags. Prevents silent corruption
of data that happens to be JSON-shaped (e.g. writeFile content).

Usage from agent_runtime_helpers.py:

    from agent.tool_repair import repair_function_args

    # Inside sanitize_tool_call_arguments, after json.loads succeeds:
    if isinstance(arguments, dict):
        args_repaired, notes = repair_function_args(
            function_name=function_name,
            function_args=arguments,
            tool_schema=get_schema_for(function_name),  # whatever your schema lookup is
        )
        if notes:
            function["arguments"] = json.dumps(args_repaired)
            # Then append notes to the tool result...
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Repair migrations — ordered, each ~20-50 lines, composable
# ---------------------------------------------------------------------------

def _strip_null_fields(args: dict) -> Tuple[dict, bool]:
    """Strip null values for optional fields. Model sends null instead of omitting."""
    applied = False
    keys = list(args.keys())
    for k in keys:
        if args[k] is None:
            del args[k]
            applied = True
    return args, applied


def _parse_stringified_arrays(args: dict) -> Tuple[dict, bool, List[str]]:
    """Detect `"[\"a\",\"b\"]"` string values and parse them as real arrays."""
    applied = False
    repaired_keys = []
    for k, v in args.items():
        if isinstance(v, str) and v.startswith("[") and v.endswith("]"):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    args[k] = parsed
                    applied = True
                    repaired_keys.append(k)
            except (json.JSONDecodeError, TypeError):
                pass
    return args, applied, repaired_keys


def _unwrap_empty_object_arrays(args: dict, expected_array_fields: set) -> Tuple[dict, bool]:
    """Replace `{}` with `[]` where schema expects an array."""
    applied = False
    for k, v in args.items():
        if k in expected_array_fields and isinstance(v, dict) and len(v) == 0:
            args[k] = []
            applied = True
    return args, applied


def _wrap_bare_string_arrays(args: dict, expected_array_fields: set) -> Tuple[dict, bool]:
    """Wrap `"foo"` -> `["foo"]` where schema expects an array."""
    applied = False
    for k, v in args.items():
        if k in expected_array_fields and isinstance(v, str):
            args[k] = [v]
            applied = True
    return args, applied


def _unwrap_markdown_autolink(args: dict) -> Tuple[dict, bool]:
    """Unwrap degenerate markdown auto-links in string values.

    DeepSeek models sometimes emit file paths as markdown links:
        filePath: "/Users/x/[notes.md](http://notes.md)"
    Fix: unwrap only when link text equals the URL-path component.
    Real links like [click](https://example.com) pass through.
    """
    AUTO_LINK_RE = re.compile(r"\[([^\]]+)\]\(https?://\1\)")
    applied = False
    for k, v in args.items():
        if isinstance(v, str):
            fixed = AUTO_LINK_RE.sub(r"\1", v)
            if fixed != v:
                args[k] = fixed
                applied = True
    return args, applied


# ---------------------------------------------------------------------------
# Schema introspection helpers
# ---------------------------------------------------------------------------

def _expected_array_fields(tool_schema: Optional[dict]) -> set:
    """Given a tool's JSON schema, return the set of field names that expect arrays."""
    if not tool_schema:
        return set()
    array_fields = set()
    properties = tool_schema.get("properties", {})
    for field_name, field_schema in properties.items():
        field_type = field_schema.get("type", "")
        if field_type == "array":
            array_fields.add(field_name)
        # Also check anyOf / oneOf for array variants
        for poly_key in ("anyOf", "oneOf"):
            for variant in field_schema.get(poly_key, []):
                if variant.get("type") == "array":
                    array_fields.add(field_name)
    return array_fields


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def repair_function_args(
    function_name: str,
    function_args: Dict[str, Any],
    tool_schema: Optional[dict] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Validate-then-repair for a decoded tool call's arguments dict.

    Args:
        function_name: Name of the tool being called (for logging/telemetry).
        function_args: Decoded JSON dict of tool arguments (NOT a string).
        tool_schema: JSON Schema dict for the tool (for array-field detection).
                     If None, only universal repairs (null-strip, stringified
                     arrays, auto-link unwrap) are applied.

    Returns:
        (repaired_args, repair_notes)
        - repaired_args: the possibly-modified arguments dict.
        - repair_notes: list of human-readable notes explaining what was fixed.
                        Empty list means no repairs were needed.
    """
    original = dict(function_args)  # shallow copy for comparison
    notes: List[str] = []

    # Migration 0: markdown auto-link unwrap (path fields)
    args, applied = _unwrap_markdown_autolink(function_args)
    if applied:
        notes.append(f"[repair: unwrapped markdown autolinks in file paths]")

    # Migration 1: Strip null values for optional fields
    args, applied = _strip_null_fields(function_args)
    if applied:
        notes.append("[repair: null values removed for optional fields]")

    # Migration 2: Parse stringified JSON arrays (MUST run before bare-string-wrap)
    args, applied, repaired_keys = _parse_stringified_arrays(function_args)
    if applied:
        keys_str = ", ".join(repaired_keys)
        notes.append(f"[repair: string values parsed as arrays for: {keys_str}]")

    # Determine which fields the schema expects as arrays
    array_fields = _expected_array_fields(tool_schema) if tool_schema else set()

    # Migration 3: Empty object -> empty array
    args, applied = _unwrap_empty_object_arrays(function_args, array_fields)
    if applied:
        notes.append("[repair: empty objects replaced with empty arrays]")

    # Migration 4: Bare string -> single-element array
    args, applied = _wrap_bare_string_arrays(function_args, array_fields)
    if applied:
        notes.append("[repair: bare strings wrapped as single-element arrays]")

    return function_args, notes


def make_repair_note_block(notes: List[str], tool_name: str) -> Optional[str]:
    """Format repair notes as a compact block for appending to tool results.

    Returns None if notes is empty (no-op), otherwise a formatted string
    that should be appended to the tool result content.
    """
    if not notes:
        return None
    lines = "\n".join(f"  • {note}" for note in notes)
    return f"\n\n[Hermes repaired: {tool_name}]\n{lines}\nNext time use the correct format directly — the schema expects these types."


def deduplicate_repair_notes(content: str, new_notes: List[str]) -> str:
    """Prevent stacking the same repair notes across turns.

    If the tool result already has a repair note for the same issue,
    don't append it again. This prevents the model from seeing the same
    feedback 50 times in a long session.
    """
    if not new_notes:
        return content
    # Check each new note — if it's already in the content, skip it
    notes_to_add = []
    for note in new_notes:
        # Use the note's first ~40 chars as a fingerprint
        fingerprint = note.split("]")[0].strip("[").rstrip() if "]" in note else note[:40]
        if fingerprint not in content:
            notes_to_add.append(note)
    if not notes_to_add:
        return content  # all already present — no change
    block = make_repair_note_block(notes_to_add, "")
    return content + (block if block else "")


# ---------------------------------------------------------------------------
# Telemetry helpers
# ---------------------------------------------------------------------------

import time
import os
from pathlib import Path

_TELEMETRY_PATH = None


def _get_telemetry_path() -> Path:
    global _TELEMETRY_PATH
    if _TELEMETRY_PATH is None:
        hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
        _TELEMETRY_PATH = Path(hermes_home) / "data" / "tool-repair-telemetry.jsonl"
        _TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _TELEMETRY_PATH


def log_repair_event(
    tool_name: str,
    model_name: str,
    repair_types: List[str],
    success: bool,
    session_id: str = "",
):
    """Append a repair event to the telemetry log.

    Format: JSONL, one event per line, easy to aggregate with jq or Python.
    """
    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool_name,
        "model": model_name,
        "repairs": repair_types,
        "success": success,
        "session_id": session_id,
    }
    path = _get_telemetry_path()
    try:
        with open(path, "a") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass  # best effort — don't crash the tool call over telemetry


# ---------------------------------------------------------------------------
# CLI usage (standalone test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys, copy

    # Deep-copy test dicts so the first loop doesn't mutate them for the second
    test_cases = [
        ("null-optional", {"command": "ls", "timeout": None}, None, 1),
        ("stringified-array", {"files": '["a.txt","b.txt"]'}, {"properties": {"files": {"type": "array"}}}, 1),
        ("empty-object", {"files": {}}, {"properties": {"files": {"type": "array"}}}, 1),
        ("bare-string", {"files": "foo.txt"}, {"properties": {"files": {"type": "array"}}}, 1),
        ("autolink", {"filePath": "/Users/x/[notes.md](http://notes.md)"}, None, 1),
        ("valid-input", {"command": "ls", "timeout": 30}, None, 0),
    ]

    all_pass = True
    for name, args, schema, expected in test_cases:
        args_copy = copy.deepcopy(args)
        repaired, notes = repair_function_args(name, args_copy, schema)
        ok = len(notes) == expected
        if not ok:
            all_pass = False
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}: {len(notes)} notes (expected {expected})")
        if notes:
            for n in notes:
                print(f"         {n}")

    sys.exit(0 if all_pass else 1)
