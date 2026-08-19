# `agentclaw.community.core.market_favorites`

Stores a user's favorite references as `(target_type, target_code)`, with the
Space path used only to authorize access. Adding an already-favorited target is
idempotent; canceling requires an existing favorite and reports not found when
there is nothing to remove.
This phase intentionally does not import Skill or MCP catalogue implementations.
Catalogue names, descriptions, icons, versions and owners are not exposed at
the HTTP boundary until a governed catalogue lookup contract is implemented.

## Context Boundary

```yaml
purpose: "Own idempotent user-scoped marketplace favorite references without owning catalogue metadata."
provides:
  - MarketFavoriteService
  - MarketFavoriteModel
  - MarketFavoriteRecord
  - FavoriteTargetType
consumes:
  - "MarketFavoriteRepositoryProtocol (core.repository) — favorite persistence"
  - "SpaceAccessService (core.spaces) — membership authorization"
consumed_by:
  - "adapters/http/openapi_v1/spaces — favorite, cancel and search operations"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.core.spaces
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```

### Change impact

The unique key defines favorite idempotency across tenant, environment, user,
and object. Metadata enrichment must be added through an explicit Service/Plugin
contract; this module must not reach directly into Skill Center or an HTTP client.
