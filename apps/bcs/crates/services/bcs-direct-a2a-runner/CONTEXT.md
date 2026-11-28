# Distributed Direct A2A Runner

The Worker installs `DirectA2aRunner` for `DirectA2a` version 1. Its
[typed SPI](../../service-api/bcs-service-api/src/core/dist_runner.rs) consumes
one authorized Agent and the immutable mode configuration `{"agent":"<id>"}`.
Each task starts one dispatch with the current session's downstream context.
The Worker supplies the context generation and owns deadlines, cancellation
fencing, checkpoint persistence and ephemeral thinking events.

The Runner projects text chunks, authoritative replacements, tool records and
snapshots from [A2A client events](../../external-clients/bcs-a2a-http/src/dist_agent/events.rs).
Its state records artifact identities, the dispatch deadline, the open question
and the number of accepted replies. The Worker persists display content.
A status-only completion keeps the existing display. Each matching user reply
continues the original remote task with the next input sequence number.

A whole-task Snapshot whose task.status.state is TASK_STATE_WORKING closes an
open authorization/input question, clears awaiting and publishes Working with
empty interactions. This also covers authorization completed outside Gateway.
Missing/unspecified status is not evidence that a question is closed; repeated
Working snapshots without an open question emit no redundant status change.

Final status messages only supply an answer when no artifact has been produced;
they never replace an existing streamed answer. Explicit Final artifacts remain
authoritative replacements. Plain text retains the string/AppendText path;
multiple text parts, file, data and text with metadata use explicit parts operations. The shared
projection accumulates parts (including promotion of existing plain text), and
the output adapter preserves their fields. Contents never enter Runner state.

For each output identity, parts_slots mirrors the committed projection's
content_format. A parts append promotes plain text and remains in parts mode;
an authoritative text replacement clears that slot's parts marker. Runner state
and display are committed together in one checkpoint, including after restore.
The format-switch regression covers Delta appends and Snapshot replacements.

The existing phase-one output profile applies: at most 128 display units,
including tool-call units, and 32 actions per decision. A Final with artifacts
uses one additional FinishTask action, so 31 artifacts fit and 32 exceed the
profile. With one answer unit, 127 distinct tools fit and the 128th exceeds it.
Exceeding a limit fails the task with OutputQuotaExceeded, preserves previously
committed output and does not silently truncate or roll over display slots.
