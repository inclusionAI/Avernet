# BCS Plugins

`crates/plugins/*` contains concrete implementations of infrastructure plugin
contracts from `crates/plugin-api/*`.

## Current Crates

- `bcs-cache-local`: dependency-light in-memory cache implementation for local
  development and contract tests.
- `bcs-db-local`: SQLite-backed local DB implementation for local development
  and contract tests.
- `bcs-secret-local`: local secret-store stub for development.
- `bcs-auth-*`: OAuth provider plugins (github, google, alipay, wechat, local).
- `bcs-llm-openai-compatible`: optional OpenAI-compatible LLM judge client.

## Dependency Rule

- Outside composition roots and tests, code should depend on `bcs-cache-api` or
  `bcs-db-api`, not on these implementation crates.
- Internal SDK implementation crates are isolated so open-source distributions
  can remove them without removing local implementations.
- Plugins implement infrastructure capabilities only. They must not own BCS
  business persistence semantics such as friendship rules, actor visibility, or
  registry lifecycle policy.

## Composition Root

`crates/bootstrap/bcs` selects implementations from the current config format:

- Cache uses the local in-memory plugin.
- DB uses the local SQLite plugin.

Service migration should receive plugin handles or service-owned store
implementations from bootstrap rather than constructing plugins directly.
