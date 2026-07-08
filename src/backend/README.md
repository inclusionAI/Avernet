# agentclaw-community

The open-source (community) distribution of the **agentclaw** backend — bot
lifecycle, sessions, chat, and workspace management, built on a microkernel
plugin architecture.

This is the community subset of the agentclaw monorepo: the `agentclaw.community`
namespace package plus its own `pyproject.toml` / `uv.lock`. It builds, boots, and
tests with **no** company-internal (`agentclaw.corp`) code present.

## Layout

```
src/agentclaw/community/   # the community namespace package (api → core → plugin_api → plugins)
configs/                   # neutral base application.yaml + community/singlebox/test overlays
tests/community/           # the community test suite
```

## Quick start

```bash
uv sync
DEPLOY_PROFILE=community uv run python -m agentclaw.community.main
```

See `configs/application.yaml` (neutral base) and `configs/application-community.yaml`
(community overlay) for configuration.
