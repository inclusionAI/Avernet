# Research: StateMachine Bot 断连故障

## Reproduction evidence

A fresh game session was started on `oss-camp2026`. During `speak_3`, the assigned Bot gateway was terminated after BCS logged `bot_accept`. BCS logged the Bot disconnect immediately, but kept the node pending until the next delivery attempt or the 420-second node timeout. The frontend showed the player as `出错` while the manager received no failure result.

## Root cause

The WebSocket disconnect cleanup only removed the Bot connection and updated Bot runtime state. No application-level callback identified non-terminal StateMachine node runs for the Bot. The Runtime's existing `fail_run` path was reachable only from timeout or terminal event handling, and it updated status/logs without the success-path chat publication barrier or a public terminal event.

## Design choice

The fix keeps transport details in the WebSocket adapter and adds an application-level `handle_bot_disconnect` operation. The Runtime performs a bounded repository lookup over pending, ready, running, and retry-scheduled nodes, then uses the same CAS/failure/run transition path as other node failures. Manager notification is sent through the existing StateMachine result publisher, rather than a direct WebSocket or UI call.
