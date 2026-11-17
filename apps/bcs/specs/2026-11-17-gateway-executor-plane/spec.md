# Gateway of the distributed executor plane (MVP)

Status: draft for review. No runtime code exists yet for this change.
Tasks covered: G1–G6 of
[2026-11-15-distributed-contracts](../2026-11-15-distributed-contracts/tasks.md).
Evidence: [research](research.md). Approach: [plan](plan.md).

## Problem

The distributed contracts are merged as declarations only. Nothing accepts a
personal agent's A2A request, hands its input to a session, or serves the
session's output back. The Gateway is that role. It is stateless, never talks to
a Worker or a Connector directly, and exchanges everything through storage.

The Connector admits key agent events with the same write sequence, so the
shared storage pieces must land first and stay in one place.

## Who uses it

- A personal agent (PA): sends messages, streams or polls results, reconnects,
  cancels, lists tasks.
- The Connector (built in parallel): reuses key naming, version helpers and the
  admission provider.
- The Worker (another team): reads what admission writes and writes what the
  Gateway serves.

## In scope

1. **Identity.** For a new session the Gateway derives the session id, which
   carries the shard. Session, task and input ids are deterministic, so a retry
   of the same message selects the same session, task and input.
2. **Admission** of user messages, human replies and cancels, in the fixed write
   order. An input that was already admitted with the same content is accepted;
   with different content it is a conflict. There is no separate "duplicate"
   result. Any failure after the first write means "outcome unknown": the caller
   retries with the same message id.
3. **Query.** Return the stored task snapshot as is. A finished task whose
   snapshot has expired is read from the final-record store.
4. **Subscribe.** A new subscriber, or a reconnect without a usable resume id,
   gets the snapshot first, then the output after it, then live output. A
   reconnect with a valid resume id continues right after it, with no snapshot.
   The stream ends when the task needs input or is finished.
5. **Blocking send and cancel.** Wait a bounded time for the task to need input
   or finish, then return the snapshot that covers that moment; on timeout
   return the current snapshot. Cancelling a finished task is a distinct error.
6. **List** a session's tasks, live and finished.
7. **Inbound A2A over JSON-RPC and SSE.** The SSE event id is an opaque,
   compact, versioned encoding of the full task cursor. Push notifications are
   declared unsupported and rejected.
8. **Shared storage pieces**: key naming without a shard hash tag, version
   helpers, stored formats, capability check, and the admission provider.

## Out of scope

- Worker behaviour: reducer, checkpoint commit and sealing, inbox consumption,
  publishing output and snapshots, dispatch control.
- Connector-owned code: dispatch, held-call and command-feed providers, the
  Connector service, the outbound A2A client adapter.
- The final-record store's layout and retention. Records are assumed present.
- Folding later output onto the snapshot for queries.
- gRPC and WebSocket bindings, webhooks, outbound safety policy.
- A binary or bootstrap wiring; role composition is task I1.
- Authentication itself. The Gateway receives a trusted caller identity.

## Delayed contracts

Where a contract between Gateway, Connector and Worker is not final, the method
exists with its intended signature and fails explicitly with the code
`need_contract_not_implemented` ("Need contract, not implemented yet"). It
returns an error, never panics, and a test asserts the error.

- **D1, listener and quarantine read in admission.** The read keeps its place in
  the write order. Admission treats its error as "no valid listener" and always
  writes the wake hint. The quarantine check is skipped.

## Acceptance criteria

1. Admission performs its writes in exactly this order, verified by a recorded
   call sequence: read admit → create task (new tasks only; a new session's spec
   just before it) → append inbox → listener read → register active → append
   wake when there is no valid listener → write admit.
2. Admitting the same input twice returns the same receipt both times.
   Admitting the same input id with different content returns a conflict.
3. A failure injected after each write step returns "outcome unknown"; a retry
   with the same message id then returns the same task.
4. The listener read returns `need_contract_not_implemented`; admission still
   succeeds and writes exactly one wake entry.
5. Version helpers reject a commit sequence of 10^9 or more, arithmetic
   overflow, and a `read + 1` that would leave its 10^9 band.
6. Key builders produce the documented names, contain no hash tag, and reject
   ids that contain `{`, `}`, `:` or whitespace.
7. Construction fails when the store lacks a required capability.
8. A query returns the stored snapshot unchanged; an expired finished task is
   served from the final-record store; an unknown task, or one owned by another
   principal, is reported as not found.
9. Subscribe with a snapshot delivers snapshot, then every later entry of that
   task exactly once and in order, while entries are being written concurrently.
   Entries of other tasks of the session are never delivered.
10. Resume with a valid id delivers no snapshot and continues right after the
    id. A resume id that is older than the snapshot, belongs to another backend
    or log generation, or cannot be decoded, falls back to snapshot-first.
11. Two subscribers of one session share one blocking read; the read stops when
    the last subscriber leaves. A subscriber that joins behind the shared
    reader misses nothing and receives nothing twice.
12. A subscriber that reads too slowly is closed with a retryable error and
    never delays other subscribers.
13. Blocking send returns only after the stored snapshot covers the entry that
    ended the wait, or returns the current snapshot at the time limit.
14. Cancel on a finished task returns the not-cancelable error; otherwise it
    admits the cancel, waits the bounded time and returns the task.
15. Over JSON-RPC: the error codes are -32001 (not found), -32002 (not
    cancelable), -32003 (push notifications); the agent card declares
    `pushNotifications: false`; SSE ids round-trip through `Last-Event-ID`.
16. Architecture gates pass unchanged or stricter: R10, LINT-1, DEP checks, the
    1,000-line limit. No file is reformatted as a side effect.

## Open questions

Listed with a recommendation in [plan §9](plan.md#9-open-questions-for-review).
