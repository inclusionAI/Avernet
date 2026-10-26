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

## Department membership rules

Department rules (`subject_type=department`) accept either a complete name
chain such as `Example-Engineering-Research` or a legacy ID path such as
`0001/0020/0030`. A rule for `Example-Engineering` includes its descendants;
`Research` alone does not match that full chain. Names use `-` as the hierarchy
separator, and ID paths use `/`. Empty levels, wildcards and values longer than
1024 characters are invalid candidates. Single-level names remain valid.

The directory's `dept_name` and `dept_path` are evaluated independently, so a
missing or invalid value does not prevent the other from matching. Comparisons
retain the database column's string collation, including case-insensitivity
where configured. Existing user-first checks and `env`, `user_list_type` and
`bot_type` scopes remain in effect. No schema or existing rule migration is
required. The `user_list.match_decision` log separates `department_names` from
`department_paths` to explain the candidates used in a decision.
