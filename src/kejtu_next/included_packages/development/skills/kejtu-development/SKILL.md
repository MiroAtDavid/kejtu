---
name: kejtu-development
description: "Implement, inspect and test code in an authorized working folder using direct command output. Select for coding tasks; it grants no file or network access."
---
# Development

Work on the user's concrete task in the assigned folder. Read its existing
instructions and relevant code before editing. Keep the scope small and preserve
unrelated changes. Existing project context and memory remain available through
their normal tools; use the Projects procedure when project discovery is needed.
Do not create another project structure, memory store or agent loop.

## Terminal

When this procedure is selected, Kejtu supplies the current permitted definitions
for terminal-use's terminal_run, terminal_output and terminal_cancel. They are
already discovered: call them through mcp_call with server="terminal-use".
Do not call mcp_tools for these supplied tools. Never guess fields or substitute
another connection when this one is unavailable. Report the actual missing access.

Use terminal_run for ordinary shell commands, with the assigned cwd. Each command
uses a fresh shell; keep durable progress in project files. Batch related reads
and coherent checks, without hiding exit codes. Read focused ranges and diffs.
Keep full test/build output in logs and return concise results. Output pages carry
byte offsets: follow next_offset with terminal_output instead of rereading old
output. Its complete log remains available when a preview is incomplete.

A running command returns an ID. Use terminal_output to check that same command;
use terminal_cancel to stop it. Keep the ID and actual status in your reply if
work is still running. A quiet stream is not completion: verify its exit code.
Respect the supplied timeout/output bounds. Never automatically rerun a command
with an uncertain outcome; inspect its status or saved files first.

The terminal can remain available between replies in this conversation, within
the original turn's execution deadline. Reply failure alone does not restart or
repeat its work. Stop, disabling/revoking the connection or closing Kejtu stops
its processes. Another conversation has its own terminal. If an ID expires,
report it and inspect retained files rather than assuming the command succeeded.

Use interactive terminal tools only for an actual interactive program. Those
tools keep normal on-demand discovery; ordinary coding needs the supplied command
tools. A task or mode never grants wider folders, network access or other agents.

## Changes and checks

Make the smallest change that solves the task. Run relevant existing tests and
keep acceptance tests intact. Add a test when it catches a meaningful regression.
Do not rerun successful checks without changed inputs or a concrete concern.
For longer work, keep a short checkpoint beside the requested handoff: completed
steps, changed files, check results and exact next step. Resume from it rather than
reconstructing or repeating work. Finish with the actual diff, checks and unresolved
issues. Merge, publication and daily-app activation follow the user's authorization.
