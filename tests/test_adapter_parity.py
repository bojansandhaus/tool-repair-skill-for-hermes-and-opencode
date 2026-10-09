"""Cross-adapter parity: one corpus of payloads, every shell hook, one verdict.

The same jq selects used to be maintained by hand in three places —
``adapters/claude-code/pre_tool_use.sh``,
``adapters/claude-code/post_tool_use.sh`` and
``adapters/deepseek-harness/pre_tool_use.sh`` — and they had already drifted
twice: v1.0.1 fixed the null detector in two of the three copies and missed the
third, which post_tool_use.sh's own comments record, and v1.0.5 found the
stringified-array and auto-link selects had diverged again. Three private copies
are three chances to be wrong, and a fourth framework means a fourth copy.

They now live once, in ``adapters/shared/detect.sh``. This suite is the
contract that keeps them there. Each payload below is rendered into every
framework's envelope and fed to every hook, and the hooks must reach the same
verdict on it — which is what "the same decision" means for a hook whose job is
to name offending fields.

Run with: python -m pytest tests -q
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ADAPTERS = REPO / "adapters"
CC_PRE = ADAPTERS / "claude-code" / "pre_tool_use.sh"
CC_POST = ADAPTERS / "claude-code" / "post_tool_use.sh"
DSH_PRE = ADAPTERS / "deepseek-harness" / "pre_tool_use.sh"
SHARED = ADAPTERS / "shared" / "detect.sh"

# The corpus is written once, in framework-neutral terms, and rendered into
# each envelope below. Adding a payload here exercises all hooks at once.
#
# "expect" maps a detector name to the field paths it must report. An empty
# mapping means "no detector fires": both pre hooks must answer `proceed` and
# the telemetry hook must log nothing.
CORPUS = [
    {
        "name": "null on an optional field",
        "tool": "readFile",
        "args": {"path": "/tmp/x", "limit": None},
        "expect": {"null": ["limit"]},
    },
    {
        "name": "null on a nested optional field",
        "tool": "writeFile",
        "args": {"options": {"timeout": None}},
        "expect": {"null": ["options.timeout"]},
    },
    {
        "name": "a stringified array",
        "tool": "writeFile",
        "args": {"files": '["a.txt", "b.txt"]'},
        "expect": {"stringified": ["files"]},
    },
    {
        "name": "a stringified array with leading whitespace",
        # The library trims before it tests the brackets, so it repairs this.
        # post_tool_use.sh has always detected it; the pre hooks used
        # startswith("[") and missed it until the selects were unified.
        "tool": "writeFile",
        "args": {"files": ' ["a.txt"] '},
        "expect": {"stringified": ["files"]},
    },
    {
        "name": "a degenerate auto-link",
        "tool": "readFile",
        "args": {"path": "/x/[notes.md](http://notes.md)"},
        "expect": {"autolink": ["path"]},
    },
    {
        "name": "a bare-host auto-link",
        "tool": "readFile",
        "args": {"filePath": "[notes.md](http://host/notes.md)"},
        "expect": {"autolink": ["filePath"]},
    },
    {
        "name": "two defects at once",
        "tool": "writeFile",
        "args": {"files": '["a"]', "options": {"retries": None}},
        "expect": {"null": ["options.retries"], "stringified": ["files"]},
    },
    {
        "name": "a hostile field name",
        "tool": "readFile",
        "args": {'we"ird\\key': None},
        "expect": {"null": ['we"ird\\key']},
    },
    # --- nothing fires -----------------------------------------------------
    {
        "name": "bracketed prose is not a stringified array",
        "tool": "writeFile",
        "args": {"content": "[1, 2] and [3, 4]"},
        "expect": {},
    },
    {
        "name": "a real markdown link is not a leak",
        "tool": "writeFile",
        "args": {"content": "see [click](https://example.com)"},
        "expect": {},
    },
    {
        "name": "an auto-link whose text is not the url path",
        "tool": "readFile",
        "args": {"path": "[notes.md](https://example.com/docs/notes.md)"},
        "expect": {},
    },
    {
        "name": "an empty object is legal without a schema",
        # empty_obj is reported by the telemetry hook only: the schema that
        # makes `{}` a defect lives with the executor, and blocking on it
        # without one is the false positive v1.0.5 spent a release removing.
        "tool": "edit",
        "args": {"opts": {}},
        "expect": {},
        "telemetry_only": {"empty_obj": ["opts"]},
    },
    {
        "name": "a clean call",
        "tool": "readFile",
        "args": {"path": "/tmp/x", "limit": 5},
        "expect": {},
    },
    {
        "name": "no arguments at all",
        "tool": "readFile",
        "args": {},
        "expect": {},
    },
]

# Wording each adapter uses for a detector, so the corpus can stay neutral.
LABELS = {
    "null": "null values in",
    "stringified": "stringified arrays in",
    "autolink": "markdown auto-links in",
}
# Order the shared detector emits its rows in, so a message and a telemetry
# line are compared as a whole, not just as a set of substrings.
ROW_ORDER = ["null", "stringified", "autolink", "empty_obj"]


def run(payload, hook):
    return subprocess.run(
        ["bash", str(hook)], input=json.dumps(payload),
        capture_output=True, text=True, check=True,
    )


def claude_payload(case):
    return {"tool": case["tool"], "input": case["args"],
            "result": {"isError": False}}


def dsh_payload(case):
    return {"hook_event_name": "PreToolUse", "tool_name": case["tool"],
            "tool_input": case["args"], "tool_use_id": "call_1"}


def expected_message(case):
    """The `null values in: limit` style fragments, in row order."""
    return [f"{LABELS[name]}: {', '.join(fields)}"
            for name in ROW_ORDER
            for fields in [case["expect"].get(name)]
            if fields]


def expected_telemetry(case):
    """The `null=(limit)` style rows the telemetry hook logs, in row order."""
    expected = dict(case["expect"])
    expected.update(case.get("telemetry_only", {}))
    return [f"{name}=({', '.join(expected[name])})"
            for name in ROW_ORDER if name in expected]


CASES = [pytest.param(c, id=c["name"]) for c in CORPUS]


# --------------------------------------------------------------------------
# the two blocking hooks
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_claude_code_pre_hook_decides_the_same(case):
    out = run(claude_payload(case), CC_PRE)
    decision = json.loads(out.stdout)
    if case["expect"]:
        assert decision["decision"] == "block", case["name"]
        for fragment in expected_message(case):
            assert fragment in decision["message"], (case["name"], fragment)
        assert case["tool"] in decision["message"], case["name"]
    else:
        assert decision["decision"] == "proceed", case["name"]
        assert "message" not in decision, case["name"]


@pytest.mark.parametrize("case", CASES)
def test_deepseek_harness_pre_hook_decides_the_same(case):
    out = run(dsh_payload(case), DSH_PRE)
    decision = json.loads(out.stdout)
    if case["expect"]:
        assert decision["decision"] == "block", case["name"]
        for fragment in expected_message(case):
            assert fragment in decision["message"], (case["name"], fragment)
        assert case["tool"] in decision["message"], case["name"]
    else:
        assert decision["decision"] == "proceed", case["name"]
        assert "message" not in decision, case["name"]


@pytest.mark.parametrize("case", CASES)
def test_the_two_blocking_hooks_agree_on_every_payload(case):
    """The decision, and the fields named to fix it. This is the assertion that
    the shared detector is doing its job: two frameworks, two envelope shapes,
    one verdict, one list of offending fields in one order.

    The closing sentence is each adapter's own — the Claude Code hook says
    "Send proper types — null should be omitted", the Harness hook uses a
    colon — so what is compared is everything up to "Fix the format and
    retry", which is where the shared detector's output ends and the
    adapter's own wording begins.
    """
    claude = json.loads(run(claude_payload(case), CC_PRE).stdout)
    harness = json.loads(run(dsh_payload(case), DSH_PRE).stdout)
    assert claude["decision"] == harness["decision"], case["name"]

    def verdict(decision):
        return decision.get("message", "").split("Fix the format and retry")[0]

    assert verdict(claude) == verdict(harness), case["name"]


# --------------------------------------------------------------------------
# the telemetry hook
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_the_telemetry_hook_reports_the_same_fields(case, tmp_path):
    proc = subprocess.run(
        ["bash", str(CC_POST)], input=json.dumps(claude_payload(case)),
        capture_output=True, text=True, check=True,
        env={"CLAUDE_CODE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    rows = expected_telemetry(case)
    if rows:
        # The log line carries the same rows in the same order, so a reader of
        # the log sees what the blocking hook would have said.
        for row in rows:
            assert f" {row})" in proc.stderr or f" {row}" in proc.stderr, (
                case["name"], row, proc.stderr,
            )
    else:
        assert "repairable patterns" not in proc.stderr, case["name"]


# --------------------------------------------------------------------------
# the hooks have no private copy of the detectors
# --------------------------------------------------------------------------


def test_no_hook_carries_its_own_copy_of_the_selects():
    """The point of adapters/shared/detect.sh. A hook that reaches for jq
    again is a copy waiting to drift, so the jq belongs in one file and these
    three must not invoke it at all."""
    for hook in (CC_PRE, CC_POST, DSH_PRE):
        body = hook.read_text()
        code = "\n".join(
            line for line in body.splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "| jq " not in code, f"{hook.name} pipes into jq again"
        assert "paths(" not in code, f"{hook.name} carries a detector again"


def test_every_hook_sources_the_shared_detectors():
    for hook in (CC_PRE, CC_POST, DSH_PRE):
        assert 'detect.sh' in hook.read_text(), hook.name
        assert SHARED.is_file(), hook.name


# --------------------------------------------------------------------------
# the decision contract, on every hook, including the one that used to crash
# --------------------------------------------------------------------------


HOSTILE_STDIN = [
    "",
    "not json",
    '{"tool": "readFile", "input": {',
    "[1,2,3]",
    "null",
    "false",
    '"a string"',
    "42",
]


@pytest.mark.parametrize("stdin_text", HOSTILE_STDIN)
def test_every_blocking_hook_answers_one_decision(stdin_text):
    """One JSON object on stdout, exit 0, for all 8 hostile shapes. v1.0.5
    put this contract in the two pre hooks; this suite keeps it for both and
    extends it to the telemetry hook, which used to die under `set -e` on the
    same shapes."""
    for hook in (CC_PRE, DSH_PRE):
        proc = subprocess.run(["bash", str(hook)], input=stdin_text,
                              capture_output=True, text=True)
        assert proc.returncode == 0, (hook.name, stdin_text, proc.stderr)
        assert proc.stdout.strip(), (hook.name, stdin_text)
        assert json.loads(proc.stdout)["decision"] == "proceed", hook.name
        assert len(proc.stdout.strip().splitlines()) == 1, hook.name


@pytest.mark.parametrize("stdin_text", HOSTILE_STDIN)
def test_the_telemetry_hook_survives_the_same_shapes(stdin_text, tmp_path):
    """Unparseable stdin is not a tool call, so there is nothing to log. Before
    the shared guard it exited 5 with a jq traceback in the session log."""
    proc = subprocess.run(
        ["bash", str(CC_POST)], input=stdin_text,
        capture_output=True, text=True,
        env={"CLAUDE_CODE_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, (stdin_text, proc.stderr)
    assert proc.stdout.strip() == "", stdin_text
    assert not (tmp_path / "tool-repair-telemetry.log").exists(), stdin_text


def test_a_missing_shared_detector_dir_is_reported_not_silent(tmp_path):
    """An install that copies one hook file and not the shared directory used
    to be undetectable. The hook now says so on stderr and still answers a
    decision."""
    payload = json.dumps(claude_payload(CORPUS[0]))
    for hook in (CC_PRE, DSH_PRE):
        proc = subprocess.run(
            ["bash", str(hook)], input=payload, capture_output=True, text=True,
            env={"TOOL_REPAIR_SHARED_DIR": str(tmp_path / "nope"),
                 "PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0, hook.name
        assert json.loads(proc.stdout)["decision"] == "proceed", hook.name
        assert "nothing was inspected" in proc.stderr, hook.name
