# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.0.1 | yes |
| < 1.0.1 | no |

## What this component does with tool arguments

This component reads and rewrites tool-call arguments before dispatch, and
appends human-readable repair notes to tool results. It never writes credentials,
never logs argument values, and never sends anything over the network.

`references/tool_repair.py` has a telemetry helper, `log_repair_event`, which
appends tool name, model name, repair type names, a boolean and a session id to
`$HERMES_HOME/data/tool-repair-telemetry.jsonl`. It records which repair fired,
never the arguments themselves. It is opt-in: nothing calls it unless you wire it
up, and failures to write are swallowed so telemetry can never break a tool call.

## Reporting a vulnerability

Open a private security advisory on the repository, or email the maintainer.
Please do not open a public issue for an unfixed vulnerability.

## A note on the Claude Code hook

The hook emits a `block` decision with a message naming the argument paths that
look malformed. Those paths come from the model's own tool call and are passed to
`jq` for JSON construction rather than interpolated, so a field name containing
quotes or backslashes cannot break the response. It does not echo argument
values into the message.
