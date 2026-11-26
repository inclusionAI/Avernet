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
