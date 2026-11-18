# Knowledge-base deletion forwarding

Issue: #4146. Target: `dev`. Pattern: #4129.

## Scope and contract

The existing knowledge-content delivery adapter exposes:

| Browser operation | Fixed provider operation |
| --- | --- |
| `DELETE /api/knowledge/spaces/{space_id}/knowledge-bases/{kb_id}` | `DELETE /api/v1/knowledge/kb/{kb_id}` |
| Same route with `immediate=true` | Same provider route with `immediate=true` |
| `POST .../{kb_id}/cancel-delete` | `POST /api/v1/knowledge/kb/{kb_id}/cancel-delete` |

Omission or explicit false preserves the provider's configurable grace period;
true explicitly skips it. The adapter does not encode a timer duration. Provider
acceptance is not physical resource cleanup completion. Pending/deleted state,
cleanup state, deadline and warnings are returned without projecting success.
Existing dependency-error sanitization remains in place.

Both operations reuse authenticated actor/session preparation, local space
membership, strict KB identity/path encoding, and native provider Owner/KB checks.
Cancellation has no active-catalog/registry preflight, because a pending KB can
be hidden there. Protected KBs, expired cancellation and permanent deletion
remain governed by the provider. No worker, scheduler, database, retry, frontend
or deployment behavior is added.

## Implementation and compatibility

Add methods to the existing `KnowledgeContentServiceProtocol`, its remote
implementation, and the existing resource router; no parallel service or
bootstrap wiring. Existing source/book operations keep their paths and defaults.
Update the owning [knowledge-management contract](../../src/agentclaw/community/core/knowledge_management/README.md).
The paired provider capability must be available before enabling callers; no
superproject gitlink is changed by this public PR.

## Verification

- Native framework DI, real membership/database and actual resource/service
  composition; only external HTTP transport and authenticated-session boundary
  are controlled.
- Default/false/immediate modes, cancel without preflight, 401/403/404/409,
  sanitized 503, invalid boolean, single provider call and local rejection with
  no outbound call.
- 17 focused contract cases and 6 registered endpoint runner cases passed.
- Adjacent knowledge-management, framework and architecture: 1005 passed.
- This is adapter contract evidence, not provider cleanup, live identity,
  production deployment or end-to-end model/retrieval acceptance.


## Management-list lifecycle follow-up

The existing space knowledge-base list must also forward the provider fields
`status`, `delete_at_ms` and `can_cancel_delete` through both `items` and
`knowledge_bases`. Preserve `active` / `pending_delete`, the exact persisted
millisecond deadline, explicit null/false and the original cursor. Do not infer
capability from management permission or a local clock; native Owner/ACL/deadline
policy remains upstream. Older provider responses that omit all three fields
remain compatible and must not gain synthesized fields. The adapter keeps its
private-field whitelist and makes one bounded catalog call with no preflight,
per-item request, schema migration or lifecycle implementation.

Verify at the remote transport boundary and through native DI/service/HTTP
serialization, including current/legacy provider pages and cancellable and
non-cancellable pending states. This follow-up does not claim a new live
cross-service or Simulator run.
