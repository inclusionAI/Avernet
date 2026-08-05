# `agentclaw.community.core.bot_chat`

Single-user bot chat domain — errors, schemas, service entry points for the per-user chat session flow.

## Context Boundary

```yaml
purpose: "Single-user bot chat domain — errors, schemas, service entry points for the per-user chat session flow."
provides:
  - "BotChatService"
  - "Errors + request/response schemas"
consumes:
  - "(low fanout — mostly self-contained today)"
internal_dependencies:
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.di.config
  - agentclaw.community.log
  - agentclaw.community.plugin_api.database
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils.env_utils
```

### Change impact

Local to the bot-chat flow. Changes here affect the single-user chat path; group chat lives separately.

## Query contract

The external read contract is exposed by the thin Public API adapter under
`/openapi/v1/bots/logs`: separate Session, Task, Group and user-Bot Trace-list
operations plus a Trace-detail operation. The adapter only translates HTTP;
query semantics remain in this package. Public Bot Logs use a separate service
and repository, so they do not depend on product Bot Chat authorization or
query behavior. The former `/api/v1/open/bot-chats/**` compatibility routes are
not registered.

`GET /api/v1/bot-chats` keeps exact matching and the existing 72-hour default
window for backward compatibility. Optional product-query capabilities are:

- `biz_scene` and `biz_task_id`: merge direct trace fields with relations
  recorded by `POST /api/bot-chat/log-relations`;
- `group_id`: resolve all BCS sessions for the group and normalize missing
  `agent:main:` prefixes before querying traces;
- `match_mode=contains`: fuzzy ID/business matching, limited to a 90-day range;
- `include_output_match=true`: include trace output in keyword matching;
- `time_scope=all`: allowed only for an exact identifier lookup.

List and detail responses may include `bot_id`, `bot_name`, `group_id`, and
`session_kind`. These are optional display fields; `session_kind` is not a
filter. A missing/deleted Bot leaves `bot_name` null so clients can fall back
to `bot_id`. Observation responses preserve raw metadata for detail rendering.

Task relations with an explicit `user_id` are isolated to that user; legacy
relations without identity remain readable for backward compatibility. ORM
index declarations create indexes for new local databases only. Existing
deployments must apply equivalent DDL through their normal database migration
process.

OpenAPI task-label enrichment is stricter than the product query: every Trace
uses only relations matching its own `user_id` and `bot_id` (including the
established `default` Bot alias). Identity-free legacy task relations are not
used by this surface. Group labels are enriched through the environment-scoped
BCS session relation; `(env, session_id)` is unique and is used only for display
enrichment, not as user or Bot authorization evidence.
