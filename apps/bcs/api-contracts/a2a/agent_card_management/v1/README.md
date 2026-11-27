# AgentCard Management v1 model contract

This directory is the authoritative source for the shared AgentCard management
model used by the REST v1 management interface and the `bcnlib-service-facade`
generated Java models.

## Publication and compatibility

- `agent_card_management.proto` is a model/descriptor contract, not a promise
  that `AgentCardManagementService` is published as a Triple service.
- AgentCard management is REST-only in this phase. OCB keeps the management
  service off its host publication path.
- Generated model/source and descriptor tags remain important cross-language
  compatibility assets and are not deleted by the Triple publication change.
- A facade copy must remain byte-identical to this file and record the
  canonical source commit plus SHA-256 before publication.

## REST v1 operations

| Proto RPC | REST |
| --- | --- |
| `RegisterAgent` | `POST /api/v1/collaboration/a2a/agents` |
| `GetAgentCard` | `GET /api/v1/collaboration/a2a/agents/{bot_id}/agent-card` |
| `FindAgentCards` | `GET /api/v1/collaboration/a2a/agents?provider_bot_ref=...` |
| `UpdateAgentCard` | `PUT /api/v1/collaboration/a2a/agents/{bot_id}/agent-card` |
| `UpdateAgentCardMetadata` | `PATCH /api/v1/collaboration/a2a/agents/{bot_id}/agent-card/metadata` |
| `RefreshAgentCard` | `POST /api/v1/collaboration/a2a/agents/{bot_id}/agent-card:refresh` |

## Identity and metadata semantics

- The public field is `agent_id`; generated Java models use `agentId`.
- The `agent_id` model is a presence wrapper so callers can distinguish
  omission, JSON null, empty string, and a nonempty string without client-side
  validation.
- Registration accepts omission, JSON null, empty string, and nonempty string.
  Server-side storage reuses `bcs_bots.agent_code` without adding a new column,
  migration, uniqueness constraint, backfill, or cleanup.
- Card PUT omission preserves the stored value. NULL or empty stored identity
  may be filled. Re-submitting the same nonempty value is idempotent. Clearing
  or replacing a nonempty value returns `AGENT_IDENTITY_CONFLICT`.
- Metadata PATCH accepts only `catalog_labels`, `status`, `visibility`, and
  `user_visibility`. Omission preserves the value; present values replace the
  field. An empty label object clears the custom label collection.
- `status` supports `online` and `hidden` only. It is projected into AgentCard
  Fuse labels exactly like `visibility` and `user_visibility`.
- Fuse synchronization stays asynchronous after the local management commit.
  It does not add exactly-once or restart-recovery guarantees to this change.
- `agent_id` is never projected to Fuse.
- Known Bot ID direct calls, group routing, queued delivery, and notification
  paths continue to use existing `hidden` behavior. This contract does not add
  a status-specific branch to them, and it does not decide Fuse query filters.
- GroupCard does not gain an Agent status field.
