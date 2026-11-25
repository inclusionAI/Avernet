# Distributed Connector runtime (BCN cluster executor plane)

Status: implemented in this change, not wired into the `bcs` binary (composition is task I1). Evidence: [validation](validation.md).

Design authority (Notion, workspace Work / Projects / Teamclaw): the decisions
page (rows 1–31), "Gateway & Connector — design" §5.1, "Worker & TBase — v2.9"
§5/§6.10/§6.11/§6.14, the PR #4020 walkthrough §12/§15/§16. Type and trait
shapes come from the merged contracts in `dev` (`bcs-dist-model`,
`bcs-service-api`), which win over every page.

## Glossary

| Term | Meaning |
| --- | --- |
| Connector | Stateless process that makes downstream A2A calls to supply agents and reports their events to the session's inbox |
| dispatch (`did`) | One downstream call; its whole state is the `disp` document |
| holder | The Connector instance (node + incarnation) that currently runs a dispatch |
| holder epoch | How many times a dispatch was claimed; 0 at creation, +1 per claim |
| command stream | Partitioned hint stream "dispatch `did` needs attention"; `disp` is the truth |
| held index | Per-instance set of the dispatches that instance holds |
| key event | `input_required`, `auth_required`, `final`, `error`: applied exactly once by the Worker |
| intermediate event | `delta`, `thinking`, `tool`, `snapshot`: best-effort, filtered by `(did, holder_epoch, seq)` |
| unresolved call | A call whose send outcome cannot be established and may not be resent |

## Problem

The merged contracts (D0) declare the Connector's ports but nothing runs them.
There is no written rule for the order of operations of one call, for how a
remote event becomes an `AgentEvent`, for what a new holder does with a call it
finds in each phase, and no storage provider for `disp`, the command stream or
the held index. Without them the Worker's dispatches reach no agent.

## Goals

1. A written Connector core contract: the order of one call and the pure rules
   (event classification, stable identities, recovery by capability, terminal
   mapping), so the executor team can rely on it.
2. A Connector engine that claims dispatches, runs one actor per held call,
   reports events, forwards human replies and cancels, and ends the call.
3. Storage providers for the dispatch queue, the held dispatch, the command
   feed and the held index, written only against the vendor-neutral store trait.
4. An A2A client adapter over the existing outbound client that keeps "not
   sent", "rejected" and "outcome unknown" distinct and never retries blindly.
5. Health and drain for operations.

## Behaviour (what must be true)

### One call

- B1. A dispatch is run by at most one holder generation at a time. Two
  Connectors claiming the same unclaimed dispatch: exactly one wins.
- B2. A command-stream entry is acknowledged only after the claim decision
  (claimed, or nothing to do), never before and never at call end.
- B3. Possible-send intent is durable before any network call to the agent.
- B4. The agent's task reference is durable before events are reported.
- B5. A call ends as: final event admitted, then the dispatch is finished.
  A crash in between loses nothing: a later holder reports the same final event
  under the same identity and finishes.
- B6. The held index lists a dispatch from before its claim until after it
  ended. It may list too much, never too little.

### Reporting

- B7. Every reported event carries `(did, holder_epoch, seq)`; `seq` starts at
  1 after each claim and rises by one per reported event.
- B8. Key events are admitted with an identity that excludes the holder epoch
  and exists even when the agent supplies no event id: final and error once per
  call; input-required / auth-required once per question round. The interaction
  id is derived the same way. The content digest excludes holder and transport
  metadata.
- B9. Intermediate events are appended, not admitted.
- B10. A2A `canceled` and `rejected` are reported as an error event with the
  remote state in the payload.
- B11. Every successful admission answer is the same success, whether or not
  the input was admitted earlier. A content conflict is surfaced, never
  swallowed. A transport failure is never reported as an agent terminal.

### Replies and cancels

- B12. A human reply continues the same call on the same remote task. The
  Connector confirms an input only when the agent definitely accepted it, and
  only as a gap-free prefix. It never removes pending inputs (the Worker does).
- B13. The Connector never cancels on its own and enforces no deadline. It
  carries out cancels found in `disp`; "request accepted" and "confirmed
  canceled" stay distinct.
- B14. The holder learns of replies and cancels by re-reading its held calls
  every 1–2 s; it does not depend on a re-notification reaching it.

### Recovery

- B15. A new holder decides by the stored phase and the declared capabilities:
  claimed only → send; accepted → re-read the remote task (subscribe or query)
  and re-report; possibly sent → resend only if the agent declares the send
  idempotent, else query by request if supported, else the call is unresolved.
- B16. A definite "not sent", which includes a connection that could not be
  established, is retried at most three times, then unresolved.
- B17. An unresolved call stays in its phase, stays held, is still polled for a
  cancel, is logged, and is never promoted to an ended call.
- B18. A stale holder can change nothing: its `disp` writes are rejected.
- B19. Repeated or late command entries are harmless.

### Operations

- B20. `drain` stops claiming and waits up to the grace period; leftovers stay
  recoverable and are reported as remaining. At the end it stops the actors
  still running and retires the registration, so no call of the drained
  process keeps acting once another Connector may take it over.
- B21. Startup fails fast when the store lacks a required capability.

## Delayed contracts (explicit stubs, not designed around)

Each is a method with its intended signature that returns the code
`need_contract_not_implemented`, with a test asserting it.

| Item | Stub | Meanwhile |
| --- | --- | --- |
| D2 | report an unresolved / not-sent call to the Worker | log; the call stays in its phase; `disp` keeps the truth |
| D3 | resolved, no stub: the Worker's supervisor re-posts a hint for every open dispatch (`recheck_interval`, 1 s); a Connector receiving it takes the call over once membership answers `Dead` | — |
| D8 | resolved, no stub: `drain` stops leftover actors and retires the registration (B20); the D3 path then takes the calls over | — |
| Membership | register / renew / liveness provider (executor team) | provided by `CoordinationStore`, injected by the composition outside this repository; `PendingMembership` remains for tests and setups without it |
| Agent resolution | what resolves `CredentialRef` / `BindingVersion` | injected resolver trait; no real implementation shipped |

## Non-goals

- Worker side of `disp` (create, cancel, push input, notify, reducer), `sched`.
- Gateway code; the shared admission provider (used, not changed).
- Reply as a new dispatch; batching of intermediate events; SSRF policy.
- Result/blob store layout; bootstrap wiring; deployment.
- Removing dead consumers from the group.

## Acceptance

- The rules B1–B21 are each covered by a test against in-memory ports or the
  exported in-memory store model, including lost-response and definite-failure
  write injections on every `disp` write.
- The existing port harnesses for the dispatch queue, command feed, held
  dispatch, agent client and runtime control pass against the new providers.
- Every stub returns `need_contract_not_implemented` under test.
- Architecture checks, the 1,000-line limit and the affected crates' tests pass.
