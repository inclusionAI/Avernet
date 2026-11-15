# Service Bot release audit

## Scope and accepted behavior

Add an outbound audit Plugin with a database implementation. Record service publication draft creation and each accepted verify/online submission independently. Freeze Bot identity, original creator, authenticated actor, Bot/change/submission times, before and desired stage, version, system environment and extensible metadata. Fill completion time/result from successful business state transitions, including build failures. Existing publication and authorization behavior stays intact.

Use a unique change_id per accepted business action; task recovery keeps it. A version may have several changes. The system env is independent of the desired stage. Creator comes from the source Bot, never the publication owner identity. Failed historical or system records without an authenticated actor must not invent a person.

## Architecture and allowed changes

- kernel owns immutable event/snapshot values; plugin_api declares the outbound sink and shared ORM schema.
- Existing publication ORM writes append events in their own transaction, so status CAS failure or database rollback cannot emit a successful audit event.
- A dedicated publishing audit helper owns snapshots and correlation, not the router or generic queue.
- Database Plugin projects complete event snapshots by tenant/env/change_id and increasing revision. The first implementation works with SQLite and production databases through DatabasePlugin.
- Existing persistent task infrastructure drives bounded outbox delivery. Source events remain durable until confirmed by the Plugin. Delivery restores the trusted event tenant and environment; no user-facing cross-tenant query is added.
- SLS implementation, UI, new public query endpoints, runtime/engine/provider changes and OCB gitlink updates are excluded.

## Implementation plan and verification

1. Add real-SQLite tests for draft audit creation, submission snapshots, terminal update and rollback/CAS rejection; run failing tests before implementation.
2. Add DTO, schema and database sink with local/prod pairing; verify out-of-order, duplicate and conflicting revisions with behavior tests.
3. Add authenticated actor plumbing and transactional event production at publication creation/status updates; preserve original actor across asynchronous completion.
4. Add persistent outbox delivery and DI registration; verify failures/replay, tenant isolation, environment retention and actual Plugin invocation.
5. Add schema migration and operator documentation; run focused publishing tests and architecture/contract tests, followed by full Backend verification.

## Important lifecycle constraints

A business status becoming validating (verify) or success (online) is publication success. A low-level operation COMPLETED or a single device callback is insufficient. Queue deadline expiry alone is not business FAILED. Completion time is the first accepted business terminal transition time, never the later projection or retry time. Duplicate/late results must not overwrite a newer change.

## Diagnostics

Log audit event commit, sink request/success/failure and duplicate/conflict with stable event/change IDs, Bot/version/stage/env/actor and durations. Do not copy publication ext/request objects into audit data: only explicitly selected metadata belongs in the event, and credentials must not enter either audit storage or logs.
