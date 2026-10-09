"""Tests for the DeepSeek Harness adapter hook.

The harness payload shape is the point of these tests: the bridges emit
`tool_name` and `tool_input`, not Claude Code's `tool` and `input`. An adapter
that reads the Claude Code keys approves every call under the harness, which is
exactly what ``test_the_claude_code_keys_would_silently_approve`` pins.

Written against @deepseek-ai/dsh 0.2.0-rc.2.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "adapters" / "deepseek-harness" / "pre_tool_use.sh"
CC_HOOK = REPO / "adapters" / "claude-code" / "pre_tool_use.sh"


def run_hook(payload: dict) -> dict:
    """Feed a harness-shaped payload to the hook and parse its decision."""
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def dsh_payload(tool: str = "readFile", args: dict | None = None, **extra) -> dict:
    """A payload shaped like the one the harness' own bridges emit."""
    return {
        "session_id": "s1",
        "transcript_path": "",
        "cwd": "/tmp",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {} if args is None else args,
        "tool_use_id": "call_1",
        **extra,
    }


# --------------------------------------------------------------------------
# the payload keys: this is the regression that matters
# --------------------------------------------------------------------------


def test_blocks_a_null_field_under_the_harness_payload_keys():
    """The harness' own key names, straight from the bridge source."""
    out = run_hook(dsh_payload("readFile", {"path": "/tmp/x", "limit": None}))
    assert out["decision"] == "block"
    assert "limit" in out["message"]
    assert "readFile" in out["message"]


def test_the_claude_code_keys_would_silently_approve():
    """Pin why this adapter cannot be the Claude Code one.

    The harness never sends ``tool`` / ``input``. Proving the shared script
    approves the same logical call under harness keys documents the mismatch
    that would otherwise cost every repair in silence.
    """
    proc = subprocess.run(
        ["bash", str(CC_HOOK)],
        input=json.dumps(dsh_payload("readFile", {"limit": None})),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout)["decision"] == "proceed"


def test_allows_a_clean_call():
    assert run_hook(dsh_payload("readFile", {"path": "/tmp/x"}))["decision"] == "proceed"


def test_proceeds_without_a_tool_name():
    payload = dsh_payload(args={"limit": None})
    del payload["tool_name"]
    assert run_hook(payload)["decision"] == "proceed"


def test_proceeds_when_tool_input_is_null():
    payload = dsh_payload()
    payload["tool_input"] = None
    assert run_hook(payload)["decision"] == "proceed"


# --------------------------------------------------------------------------
# the patterns
# --------------------------------------------------------------------------


def test_blocks_a_stringified_array():
    out = run_hook(dsh_payload("writeFile", {"files": '["a.txt", "b.txt"]'}))
    assert out["decision"] == "block"
    assert "stringified arrays" in out["message"]


def test_blocks_a_degenerate_autolink():
    out = run_hook(dsh_payload("readFile", {"path": "/x/[notes.md](http://notes.md)"}))
    assert out["decision"] == "block"
    assert "markdown auto-links" in out["message"]


def test_leaves_a_real_markdown_link_alone():
    """Valid content must survive. A real link is not a leak."""
    out = run_hook(dsh_payload("writeFile", {"content": "see [click](https://example.com)"}))
    assert out["decision"] == "proceed"


def test_a_valid_json_array_string_is_repaired_not_spared():
    """A string that IS a JSON array is a genuine mistake, so it is reported.

    This matches the library: it parses ``["a", "b"]`` into a real array even
    with no schema. Bracketed prose like ``[1, 2] and [3, 4]`` is left alone by
    both, and is pinned here too.
    """
    out = run_hook(dsh_payload("writeFile", {"files": '["a", "b"]'}))
    assert out["decision"] == "block"
    assert "stringified arrays" in out["message"]


def test_leaves_bracketed_prose_alone():
    """The valid-content case: prose that merely looks array-shaped."""
    out = run_hook(dsh_payload("writeFile", {"content": "[1, 2] and [3, 4]"}))
    assert out["decision"] == "proceed"


def test_blocks_a_bare_host_autolink_form():
    """The library accepts a bare host, so the hook must flag it too."""
    out = run_hook(dsh_payload("readFile", {"filePath": "[notes.md](http://notes.md)"}))
    assert out["decision"] == "block"
    assert "markdown auto-links" in out["message"]


# --------------------------------------------------------------------------
# output contract
# --------------------------------------------------------------------------


def test_output_is_valid_json_for_a_hostile_field_name():
    """A quote or backslash in a field name must not produce broken JSON."""
    out = run_hook(dsh_payload("readFile", {'we"ird\\key': None}))
    assert out["decision"] == "block"
    assert isinstance(out["message"], str)


def test_every_output_line_is_valid_json():
    """The hook prints exactly one line; a second line would be stray output."""
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(dsh_payload("readFile", {"limit": None})),
        capture_output=True,
        text=True,
        check=True,
    )
    assert len(proc.stdout.strip().splitlines()) == 1


def test_reports_the_offending_field_path_for_a_nested_null():
    out = run_hook(dsh_payload("writeFile", {"options": {"timeout": None}}))
    assert out["decision"] == "block"
    assert "options.timeout" in out["message"]


@pytest.mark.parametrize("stdin_text", [
    "",
    "not json",
    '{"tool_name": "readFile",',
    "[1,2,3]",
    "null",
])
def test_answers_a_decision_on_unparseable_stdin(stdin_text):
    """A decision is the contract for a command hook. jq failing under `set -e`
    used to kill the script with exit 5 and EMPTY stdout, which the harness
    reads as no decision at all. The Claude Code hook had the same hole and now
    carries the same guard."""
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=stdin_text,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (stdin_text, proc.returncode, proc.stderr)
    assert proc.stdout.strip(), (stdin_text, "empty stdout is not a decision")
    out = json.loads(proc.stdout)
    assert out["decision"] == "proceed", stdin_text
    assert len(proc.stdout.strip().splitlines()) == 1, stdin_text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))