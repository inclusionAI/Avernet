# `agentclaw.community.core.bot_config_surface`

The rules the public config surface enforces for the five categories manifest
apply touches, named in one place so the HTTP router and apply run the same
ones.

## Context Boundary

```yaml
purpose: "Index of the checks governing each bot-config category, so the openapi_v1 routers and manifest apply enforce one set of rules rather than two copies."
provides:
  - "BotConfigCoords"
  - "CategoryChecks"
  - "CONFIG_SURFACE"
consumes:
  - "BotService (engine_config's ownership guard resolves through it)"
  - "BotRepository (resources resolves the bot's engine through it)"
internal_dependencies:
  - agentclaw.community.core.mcp
  - agentclaw.community.core.services
  - agentclaw.community.core.skill_center
```

### Change impact

This module is an **index, and must not grow logic**. Every callable it names is
defined in the package that owns that category's domain and imported here by
reference; a rule implemented here would be a rule the routers do not run, which
is the drift the module exists to prevent. Its fan-out across six core packages
is therefore the purpose rather than a smell.

What notices a change here: manifest apply (#1472) dispatches per category
through `CONFIG_SURFACE`, and create-with-manifest (#1696) calls the `from_spec`
half at preflight, before a bot record exists. The `openapi_v1` routers for
resources, identity, skills and engine-config call the same objects directly —
`tests/community/core/bot_config_surface/` asserts with `is` that they are the
same objects, so adding a row without pointing it at the function the router
really calls fails there rather than silently.

`coords.py` is deliberately a leaf that imports nothing from the project, and
`__init__.py` re-exports nothing: the category homes import `coords`, and
`table` imports *from* those homes, so a re-export would turn importing the leaf
into a cycle.
