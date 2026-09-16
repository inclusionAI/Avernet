# TC resource-ready notification

## Summary

When an authoritative TC session resource is observed in `READY`, Avernet
publishes one bounded, non-blocking, best-effort resource-ready event. The
cross-service event contains exactly three fields:

```json
{
  "schema_version": "1",
  "event_id": "tc.resource.ready:<res_id>",
  "res_id": "<res_id>"
}
```

Avernet does not send transfer, download, identity, file, session, group, member,
or permission data. The OCB/ECB integration resolves all authoritative context
from storage by `res_id`, obtains `transfer_id`, creates a share link, downloads
the file, and enters the existing 0731 knowledge ingestion flow.

## Motivation

The original sidecar accepted browser-supplied conversation, group, and member
fields and retained those values in an unbounded in-memory map. That made request
context look like a permission fact. It also notified only from status polling,
so a resource that became `READY` during upload completion or callback could be
missed. Finally, Core owned a concrete ECB endpoint and an unbounded detached
execution path.

The target boundary is deliberately smaller:

```text
Avernet TC state machine -> READY observer -> three-field outbound port
                                             |
                                             v
                                     HTTP delivery adapter
                                             |
                                             v
OCB/ECB: res_id -> authoritative context -> transfer_id -> sharelink -> ingest
```

## User Stories

- As a TC user, upload and materialization results are unaffected by knowledge
  notification failures.
- As an ECB integrator, I receive a stable resource identifier without trusting
  caller-provided authorization or topology fields.
- As an operator, detached work and dedupe state are bounded.
- As a reviewer, the Core/adapter boundary, production entrypoints, and coverage
  limitations are declared and tested truthfully.

## Acceptance Criteria

### Event contract

- [ ] The payload is exactly `schema_version`, `event_id`, and `res_id`.
- [ ] `schema_version` is the string `"1"`.
- [ ] `event_id` is deterministic: `tc.resource.ready:<res_id>`.
- [ ] `res_id` is the authoritative `SessionResourceRecord.resource_id`.
- [ ] The event contains no `transfer_id`, URL, inline bytes, user/bot identity,
  file metadata, session/conversation, scope, group, member, or permission data.
- [ ] Browser request schemas do not accept special sidecar conversation, group,
  or member fields.

### Trigger completeness and primary-flow isolation

- [ ] Non-`READY` resources do not publish.
- [ ] Legacy `upload-complete` and `materialize-status` invoke one shared observer.
- [ ] The internal materialization callback invokes the observer when a state
  transition is applied.
- [ ] Formal OpenAPI session-file completion and status operations invoke the
  same observer.
- [ ] Scheduling, overload, and downstream delivery failures do not change the
  primary TC response.

### Bounded best-effort semantics

- [ ] In-flight publications have a hard configurable cap.
- [ ] Repeated/concurrent observations of one resource create at most one attempt
  while it is in flight and during a bounded dedupe TTL.
- [ ] Dedupe state is capacity-bounded with deterministic eviction.
- [ ] Overload does not create a tombstone; a later observation may try again.
- [ ] This phase provides no durable outbox, retry worker, or restart-safe
  delivery guarantee.

### Architecture and verification

- [ ] Core depends on a typed outbound publication port and owns no HTTP path or
  HTTP client.
- [ ] The concrete HTTP publisher lives under the HTTP adapter layer and uses a
  small dedicated executor with a short timeout.
- [ ] Routers depend on a Service API observer protocol, not the concrete
  coordinator.
- [ ] Port and Service API implementations are registered in conformance gates.
- [ ] The new Core module is registered as boundary-significant and its README
  declares dependencies.
- [ ] Singlebox exemption text names the real production entrypoints, current
  best-effort semantics, owner, test evidence, and drain condition.

## ECB Ownership

The receiving OCB/ECB adapter owns compatibility with the unpublished 0731 ECB
implementation. It must resolve the following from authoritative state by
`res_id`: resource state, `transfer_id`, owner, bot, file metadata,
session/conversation, normalized direct/group scope, group, and typed members.
It then creates a share link internally, downloads and validates the file, and
builds the existing 0731 `VerifiedUpload` input.

Avernet must not add a generic resource resolver merely to move this lookup into
the sender. The detailed ECB implementation handoff is maintained outside this
repository in the OCB `0731_ecb_dev` worktree.

## In Scope

- Exact three-field resource-ready event.
- All current production READY observation paths.
- Bounded in-process scheduling and dedupe.
- Typed Service API and outbound port boundaries.
- HTTP adapter and DI configuration.
- Unit, adapter, contract, DI, architecture, and coverage metadata tests.

## Out of Scope

- ECB/OCB implementation in this repository.
- Durable delivery, persistent retry, or an inbox/outbox.
- Guaranteed delivery across process restarts.
- Share-link generation or file downloading in Avernet.
- Changes to the primary session-resource upload/materialization contract.
