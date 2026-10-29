# Knowledge management

This module enriches a bounded provider catalog/grant page with public workspace
and Bot metadata. It does not store knowledge assets, canonical ACLs or grants.
Bot enablement remains owned by the existing bot_knowledge module.

The configured space_tenant identifies the current public space dataset; because
legacy spaces are not tenant-column scoped, reads outside that configured tenant
fail before SQL. Grant platform/environment and composite Bot identity are checked
before enrichment. Compatibility wire names such as kgbase_status describe the
existing browser contract, not a vendor-specific engine dependency.

## Context Boundary

```yaml
purpose: Provider-neutral workspace knowledge management and bounded display aggregation.
provides:
  - KnowledgeManagementService
  - ConversationKnowledgeService
  - KnowledgeSourceRepository
  - KnowledgeManagementConfig
consumes:
  - KnowledgeCatalog
  - DatabasePlugin
  - SpaceKnowledgeSync
  - CollaboratorServiceProtocol
  - HumanBotFriendshipServiceProtocol
internal_dependencies:
  - agentclaw.community.core.knowledge_management
  - agentclaw.community.core.bot_knowledge
  - agentclaw.community.core.bot_chat
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.spaces
  - agentclaw.community.plugin_api
  - agentclaw.community.kernel
  - agentclaw.community.utils
```

### Change impact
Management browser clients and external knowledge providers consume this contract.
Configuration is disabled by default. Providers retain native authorization,
content construction and schedule execution. No schema or publication change.

## Legacy personal Bot ownership

Candidate pages and composite Bot/Owner lookups accept historical Bots whose
`space_id` is NULL only in their Owner's existing, undeleted PERSONAL space in
the same environment. Explicit space assignments are unchanged. Team spaces
and other Owners' personal spaces never inherit NULL Bots. The two disjoint
SQL branches each apply seek, keyword, tenant and LIMIT filters before a bounded
merge (at most twice the page size plus two). A point lookup determines whether
the target is PERSONAL; Team pages retain their single Bot query. This avoids
wide OR scans and does not depend on database UNION LIMIT pushdown. No Bot is
moved and no space is created during reads.
Grant-state, grant writes and authorized-Bot display use this same lookup.


## Conversation-only references

`GET /api/knowledge/bots/{bot_id}/conversation-knowledge-bases` accepts the
composite `owner_id`, an opaque integer `cursor` (initially 0), and `limit` 1–20.
The authenticated actor must have native conversation access: owner, public Bot,
native MEMBER permission, or an accepted Human-to-Bot friendship verified by the
existing friendship service. Dependency failures deny access. A browser page or
client-supplied actor identity is never admission evidence.

The response is `{"items":[{"kb_id":"...","name":"..."}],"next_cursor":null}`.
Only currently granted, enabled and ready knowledge is projected. Grant pages,
selection reads and metadata batches are bounded; filtered pages may be empty
with a non-null cursor. Clients must follow that cursor rather than mistake an
empty page for completion. No per-KB transport fan-out or full-list scan is used.

Bot sharing does not require the chatting actor to hold a direct KB viewer ACL.
After conversation admission, provider metadata is resolved using the trusted Bot
Owner, and only reference IDs/names are returned. This never grants direct KB
asset, original-file, management or Editor access; existing management routes
retain their original checks. ACLs, grants and enablement rows are never mutated.

This contract covers personal/current Bot selections only. Service-Bot draft or
online selections are not substituted; requests for those return
`KNOWLEDGE_CONVERSATION_UNSUPPORTED` (422). No schema or runtime retrieval change.
