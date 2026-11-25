# Implementation and validation plan

1. Register accepted child run/session pairs and keep idempotent terminal state.
2. Observe child frames before parent filtering, preserving bounded early/ACK
   state and compaction retry ordering; do not forward child replies.
3. Replace resume-count decrement with identity completion; correlate v1
   announces, preserve parent terminal boundaries and mixed-path fan-in.
4. Reproduce old failures with event-driven tests, run focused client/port and
   independent full Engine regression, review diagnostics and isolation.
5. Commit on a new topic, rebase onto freshly fetched GitHub dev, revalidate,
   publish a new source PR, and resolve actual CI/review failures.

No OpenClaw source, consumer protocol, runtime deployment, merge or old-PR
closure is part of this implementation.
