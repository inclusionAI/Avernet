# Connector of the BCN cluster executor plane — spec

Status: draft for review. No code yet. Plan: [plan.md](plan.md). Evidence and the
store-trait mapping: [research.md](research.md). Implements tasks C1–C6 of
[2026-11-15-distributed-contracts](../2026-11-15-distributed-contracts/tasks.md).

## Terms

| Term | Meaning |
| --- | --- |
| Connector | A stateless process (several instances) that makes downstream A2A calls to supply agents and reports their events back |
| call / dispatch (`did`) | One downstream call to one supply agent. Everything known about it is in its `disp` record |
| holder | The Connector instance that owns a call right now, with a holder epoch that goes up on every claim |
| Worker | The process that runs sessions (executor team). It creates calls, writes cancel and reply requests into `disp`, and enforces deadlines |
| key event | An agent event the Worker must see exactly once: input required, auth required, final, error |
| intermediate event | Delta, thinking, tool, snapshot |
| unresolved call | A call whose send may or may not have reached the agent, or definitely did not, and that the Connector may not retry on its own |
| delayed contract | A contract between Gateway, Connector and Worker that is not final (walkthrough §16) |

## Problem

The merged contracts (#4020) declare the ports a Connector uses but no Connector:
there is no service contract, no loop, no rule for turning an agent's events into
session inputs, no adapter over the existing A2A client and no storage provider.
Until it exists, a Worker can create a call but nothing sends it.

## Goals

1. A written Connector service contract: the order of steps for one call, the rules
   for reporting events, and the rules for uncertain sends, as code and tests.
2. A Connector engine that runs calls end to end against the merged ports.
3. An adapter that implements `DistAgentClientPort` over the existing outbound A2A
   client.
4. TBase-backed providers for `DispatchQueuePort`, `CommandFeedPort` and
   `HeldDispatchPort`, written only against `bcs-store-api`.
5. Health and drain for operations.

## Not in scope

- The Worker side of `disp` (`DispatchControlPort`), the reducer, deadlines.
- The Gateway, and the shared admission code, key names and ts helpers (Gateway
  session). The Connector calls them; it does not write its own.
- The TBase client itself (internal repository, behind `bcs-store-api`).
- `sched` (decision 21), a reply as a new `disp` (post-MVP), outbound SSRF policy
  (decision 19), final-record storage layout.
- Deciding the delayed contracts D2, D3, D8. They are stubbed.

## Requirements

### One call

- R1. A Connector learns about a call from the command stream, decides by reading
  `disp`, and acknowledges the hint only after that decision. The stream is a hint;
  `disp` is the truth. A lost, repeated or stale hint changes nothing.
- R2. At most one actor runs a call per holder epoch. If the process holds a call
  but has no running actor for it, it takes a new holder epoch before resuming.
- R3. Before any network call that might reach the agent, the intent to send is
  stored. A later holder can always tell "may have been sent" from "never sent".
- R4. After the agent accepts, the remote task id is stored before any event of that
  task is reported.
- R5. A call ends in this order: the final (or error) event is admitted, then the
  call is marked ended. An uncertain outcome is never recorded as an end.
- R6. The Connector stops working on a call as soon as it learns it is no longer
  the holder, and writes nothing more for it.

### Reporting to the Worker

- R7. Every reported event carries `(did, holder_epoch, seq)`. `seq` starts at 1
  after each claim and goes up by one per reported event.
- R8. Key events are admitted with an `input_id` that does not contain the holder
  epoch and does not need an agent-supplied event id: final = digest(did, "final"),
  error = digest(did, "error"), input/auth required = digest(did, kind, n) for the
  n-th such event of the call. The interaction id is derived the same way. Two
  holders reporting the same key event produce one input.
- R9. Intermediate events are appended, not admitted. A retry keeps the same
  `(did, holder_epoch, seq)`.
- R10. A transport failure is never reported as an agent result.
- R11. Content above the inline limit is stored as an immutable blob and reported by
  reference.

### Uncertain sends

- R12. The agent client keeps three outcomes apart: not sent, rejected, outcome
  unknown. It never retries a send on its own.
- R13. A send with an unknown outcome is repeated only when the agent's declared
  capabilities make that safe, or resolved by asking the agent when it can be
  asked. Otherwise the call is unresolved.
- R14. An unresolved call stays in its stored phase, is logged, is visible in the
  health snapshot, and is still watched for a cancel. (Telling the Worker is delayed
  contract D2.)

### Control from the Worker

- R15. The Connector never cancels on its own and enforces no deadline. It carries
  out a cancel the Worker wrote to `disp`, and records what the agent answered,
  keeping "request accepted", "confirmed canceled", "already finished", "rejected"
  and "unknown" apart.
- R16. A human reply continues the same call on the same remote task. Replies are
  sent in order; a reply is confirmed only when the agent definitely accepted it,
  and a later reply is never confirmed over an unconfirmed earlier one.
- R17. The holder learns about new replies and cancels by re-reading each held
  call's `disp` every 1–2 s. It does not depend on being re-notified.
- R18. A re-post of a call by the Worker (not ACCEPTED by its deadline) is
  tolerated: it never causes a second send.

### Failover

- R19. A call whose recorded holder is dead can be claimed by another Connector,
  which gets a higher holder epoch. A holder that is alive, or whose state is
  unknown, keeps its call.
- R20. After taking over an accepted call, the new holder re-reads the remote task
  (query or re-subscribe, as the agent's capabilities allow) and reports again
  under the same `input_id`s.
- R21. Each Connector keeps an index of the calls it holds: a call is added before
  the claim and removed after it ended. The index may list too much, never too
  little.

### Storage

- R22. Storage code uses only `bcs-store-api` and lives in a `*-store` crate. No
  internal names, endpoints or raw commands.
- R23. A claim writes holder `{instance, epoch + 1}` with ts = (epoch + 1) × 10^9.
  Every other write reads, checks the holder is still this instance and epoch, and
  writes with ts = read ts + 1.
- R24. The Connector writes only the execution part of `disp` (holder, phase, remote
  task, confirmed-input watermark, cancel observation, outcome). It never writes
  `cancel` or `pending_input`.
- R25. Required store capabilities are checked at startup; a missing one stops the
  start.
- R26. A failed or uncertain store write is an error to the caller, never a silent
  success.

### Operations

- R27. A health snapshot reports the instance, readiness, draining, running calls
  and unresolved calls.
- R28. Drain stops claiming and waits for running calls up to a grace period, then
  reports how many are left. (Handing those over is delayed contract D8.)

### Delayed contracts

- R29. Each delayed step is a method with its intended final signature that returns
  an explicit error with code `need_contract_not_implemented`, a doc comment naming
  the §16 item, and a test asserting that error. It never panics and never reports
  success.

## Acceptance

1. With in-memory fakes of the ports and a scripted agent, one call runs from hint
   to ENDED, and the fakes record exactly the order in R1–R5.
2. Two engines given the same hint: one actor, one send.
3. Kill the holder at each point (after claim, after SENT, after ACCEPTED, between
   final admission and end). A second engine finishes the call or leaves it
   unresolved as R13 says; the Worker's inbox holds each key event once.
4. Agent client: for each of connect failure, 4xx rejection, 5xx, timeout after
   send, and a dropped stream, the outcome is the one R12 names.
5. Human reply and cancel written into `disp` are carried out within one poll
   interval without any re-notification.
6. Providers pass the reusable contract tests in
   `bcs-test-support::contract::distributed::dispatch` and the ts rules in R23
   against an in-memory `bcs-store-api` model, including two concurrent claims and a
   write by a former holder.
7. Every delayed stub returns `need_contract_not_implemented` in a test.
8. `apps/bcs` architecture checks pass, R10 covers the new service crate, and no
   file exceeds 1,000 lines.

## Open questions

Listed with a recommended answer in [plan.md §13](plan.md#13-decisions-needed-from-the-owner).
