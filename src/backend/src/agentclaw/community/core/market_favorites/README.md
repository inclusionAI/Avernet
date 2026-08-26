# `agentclaw.community.core.market_favorites`

Stores a Space's favorite references as
`(market_source, target_type, target_code)`. Supported sources are
`SKILLCENTER` and `TEAMCLAW`. Adding or canceling an already-converged target is
idempotent, and mutation responses expose whether persistence actually changed.
This phase intentionally does not import Skill or MCP catalogue implementations.
Catalogue names, descriptions, icons, versions and owners are not exposed at
the HTTP boundary until a governed catalogue lookup contract is implemented.

## Context Boundary

```yaml
purpose: "Own idempotent Space-scoped marketplace favorite references without owning catalogue metadata."
provides:
  - MarketFavoriteService
  - MarketFavoriteModel
  - MarketFavoriteRecord
  - MarketSource
  - FavoriteTargetType
consumes:
  - "MarketFavoriteRepositoryProtocol (core.repository) — favorite persistence"
  - "SpaceAccessService (core.spaces) — membership authorization"
consumed_by:
  - "adapters/http/openapi_v1/spaces — favorite, cancel, search and batch status operations"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.core.repository
  - agentclaw.community.core.spaces
  - agentclaw.community.plugin_api.models
  - agentclaw.community.utils.avernet_tenant_guard
  - agentclaw.community.utils.env_utils
```

### Change impact

The unique key defines favorite idempotency. Metadata enrichment must be added
through an explicit Service/Plugin contract; this module must not reach directly
into Skill Center or an HTTP client.
