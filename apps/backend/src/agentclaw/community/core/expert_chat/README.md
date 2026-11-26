# `agentclaw.community.core.expert_chat`

Expert-chat domain — bot-to-bot chat session orchestration for the expert workflow.

## Context Boundary

```yaml
purpose: "Expert-chat domain — bot-to-bot chat session orchestration for the expert workflow."
provides:
  - "ExpertChatService"
  - "Expert-chat SQLAlchemy models"
consumes:
  - "BotRepository"
  - "DeviceService + DeviceContextResolver"
  - "ServiceBot repo + BaasService"
  - "CommonConfigService (legacy ARCA image policy resolution)"
  - "SkillSync guard"
  - "CollaboratorService (chat 权限校验)"
internal_dependencies:
  - agentclaw.community.core.engine_runtime.stage
  - agentclaw.community.core.engine_runtime.errors
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.chat    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.devices    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.publishing    # repository contracts consumed by this module
  - agentclaw.community.core.bot_collaborator
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.caller_identity
  - agentclaw.community.core.common_config
  - agentclaw.community.core.devices
  - agentclaw.community.core.service_bot
  - agentclaw.community.core.skill_center
  - agentclaw.community.log
  - agentclaw.community.plugin_api.device_adapter_transport
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils
```

### Change impact

Schema changes require SQLite migration. Cross-domain dependencies make refactors risky — start with read-only impact analysis.

### Owner inspection of friend sessions

Friend service chat accepts explicit online addressing; legacy draft selectors
also mean online, never the source workspace. The online binding is selected by
the exact source Bot primary key/environment using Engine Runtime's canonical
publication rule. Verify and services without an online publication are refused
before device work. Caller instances remain subject-owned. Personal friends keep
draft-only addressing. Read-only files and explicit friend IAM use the same
stage/owned-session boundary. This does not add friend upload/delete privileges.

The legacy expert-chat get and connect session HTTP endpoints translate an
unavailable online publication to the existing unpublished error (4001).
OpenAPI stage addressing retains its own not-live response contract.

The OpenAPI friend-session read routes (list/favorites/detail/messages and file
reads) separate the session subject (`f_user_id`) from the authenticated actor
(`user_id`). The subject continues to select the Backend-owned session index;
it must never be replaced with the owner while filtering sessions. Reads pass
the verified actor to ExpertChat as `actor_user_id`. Omission preserves the
legacy subject-as-actor behavior. A different actor is accepted only for the
Bot owner after explicit BCN friendship authorization. For shared runtimes the
connection resolver uses that owner actor; ordinary friends retain their own
identity and do not gain device-management permission. Caller-mode allocation
and connection identity remain tied to the subject's independent instance.
This does not grant owner-on-friend session mutation or broaden public Bot
visibility. Existing clients need no new query parameter.

### Application Caller connection policy

The BaaS-authenticated `POST /api/v1/expert-chats/app-caller-connection`
may access an existing Caller in the server default tenant, including another
user's instance on a private Bot. Its HS256 JWT requires `iss=baas`, `iat/exp`
and validates optional `nbf` with the existing 5-second leeway. It ignores
`principals`, `app_id`, `tenant` and `aud` and never uses them for tenant routing.
It reuses the configured `SecretNamesConfig.gateway_principal_signing_key`
value, with no separate BaaS key or deployment setting. BaaS must securely load
the same key; issuer separates protocols, not holders of the shared key. This endpoint does not require an application grant, ownership, or current
collaborator membership. The Bot must exist in the tenant, and the Caller must
already have an instance with a nonempty bot UUID. No administrator privilege or
first-time provisioning is granted. User-authenticated endpoints retain their
existing access policies.

### Teclaw service chat runtime

Teclaw service Bots, including Caller-identity Bots, create and use sessions on
an existing successful online publication binding. Engine strategy capability
`requires_caller_chat_instance` disables independent Caller runtime provisioning
for Teclaw; other engines retain their existing allocation and polling behavior.
Authorization precedes runtime selection. Backend-owned session indexes remain
scoped to the session user, while adapter session creation and subsequent
connection preparation use the same published runtime. No draft fallback or new
container is introduced by creating a session.


### Caller credential expiry ledger

`AcExpertChatInstance.caller_token_expires_at` is nullable UTC time for the token
successfully installed on the current dedicated Caller runtime. It is independent
of ASC authorization expiry and session expiry. A new/replaced runtime starts with
NULL, and progress-only writes preserve it. The repository's `caller_token_update`
scope serializes the decision, outbound install and expiry write for one addressed
instance; ordinary chat skips installation for an equal/later installed expiry.
After provisioning reaches SUCCESS with an IAM credential, initialization explicitly
forces reinstallation under that same lock and records the actual installed expiry,
including a shorter/equal value. Already-ready connection reuse does not force it;
missing IAM retains the existing skip behavior. Teclaw retains its shared-runtime
bypass. External installation and SQL commit are not atomic; see the
[Caller installation policy](../caller_identity/README.md) for failed-recovery handling.
Initial schema creation includes the field; existing MySQL/OceanBase deployments apply
[the additive migration](sql/2026_11_13_caller_token_expiry.sql) first.

### Read-only independent Caller status

`GET /openapi/v1/bots/{bot_id}/caller-instance/status` requires `user_id`
(the authenticated user, or the application-granted acting user) and accepts
`entity_id` addressing, with `owner_id` as its deprecated alias. Existing
owner/public/collaborator chat access applies; application-only credentials
also require an addressed-Bot grant. The standard public envelope's `data`
contains exactly two required booleans: `exists` and `alive`.

`exists` means a nonempty Caller UUID is registered in the scoped instance
ledger; a recycled or released Caller remains registered. `alive` requires a
fresh provider observation of an ACTIVE device with `health="true"`; a released
Bot or an empty device list is not alive. No row/UUID returns both false without
a provider call. Upstream failure is an error, not a false health observation.
The query resolves the actual owner, reads only the acting user's independent
Caller and never substitutes the source Bot's shared runtime. It never creates,
revives, upgrades, polls publishing, exchanges identity or updates ledger/binding
rows. Provider health observation may refresh the provider's own health cache.
Provider payload logging is disabled for this query because details can carry
credentials; diagnostic events project only status and health fields.


### BaaS read-only Caller status

`GET /api/v1/expert-chats/app-caller-instance/status` accepts the required,
nonempty query values `bot_id`, `owner_id` and `user_id`. BaaS authenticates with
its HS256 service JWT in `X-Avernet-Principal`, using the same signing-key and
`iss=baas`, `iat`, `exp` and optional `nbf` verification as the application Caller
connection endpoint above. Missing or invalid credentials return HTTP 401.
The verified BaaS service may query another user's private Bot Caller without
an application grant or current chat membership. Tenant and environment remain
server-controlled; token identity, tenant and audience claims do not select them.

The ordinary `ApiResponse` envelope's successful `data` contains only `exists`
and `alive`, with the read-only allocation/live-health meanings above. No Caller
record or missing UUID returns false/false without provider access. The query
never provisions, restarts, upgrades, publishes, exchanges identity or writes
instance/binding state. Source-Bot access failure returns the ordinary error
code 403; upstream failures return a sanitized non-success 5999 envelope rather
than reporting false health. Public
`GET /openapi/v1/bots/{bot_id}/caller-instance/status` retains its existing user,
application-grant and chat-access policy; a BaaS service JWT is not a public
identity exception. Diagnostics identify `app_caller_instance_status` and never
include the service JWT, signing key or full provider response.

### Independent Caller runtime sessions

Independent Caller sessions are authorized by the authenticated subject's existing
successful instance binding, selected only after Bot access and online publication
checks. Lists, details, history, connection, favorites, rename, deletion and history
clearing do not read or write the Backend-owned session index. Reads never create,
restart or upgrade an instance. Missing instances yield an empty list; pending or
failed instances remain pending. Exact operations confirm the session exists in
that fixed runtime; supplied keys cannot select another user's container.

Runtime lists accept all nonempty session IDs, including ordinary, cron and
subagent sessions, without a user filter. Results are deduplicated, sorted by
runtime timestamps with an ID tie break, then paginated. Favorites intersect that
runtime result. Default-session get/create retain their original lifecycle, current-session pointer
and ownership registration; independent Caller queries and operations do not
consume that index. Shared runtimes,
including Teclaw, retain existing ownership and lifecycle rules.
