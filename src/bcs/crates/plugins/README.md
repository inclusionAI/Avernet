# BCS Plugins

`crates/plugins/*` contains BCS-owned plugin implementations.

Most children are Rust crates that implement infrastructure plugin contracts
from `crates/plugin-api/*`. The channel packages are TypeScript delivery
adapters that connect external Bot runtimes to BCS over the BCN protocol.

## Current Crates

- `bcs-cache-local`: dependency-light in-memory cache implementation for local
  development and contract tests.
- `bcs-db-local`: SQLite-backed local DB implementation for local development
  and contract tests.
- `bcs-secret-local`: local secret-store stub for development.
- `bcs-auth-*`: OAuth provider plugins (github, google, alipay, wechat, local).
- `bcs-llm-anthropic`: Anthropic Messages API LLM judge client.
- `bcs-llm-openai-compatible`: optional OpenAI-compatible LLM judge client.
- `openclaw-channel-bcn`: OpenClaw channel plugin package for connecting
  OpenClaw bot runtimes to BCS.
- `deepseek-harness-channel-bcn`: DeepSeek Harness Cordis bundle for connecting
  DSH agents to BCS with Bot WebSocket V2.

## Dependency Rule

- Outside composition roots and tests, code should depend on `bcs-cache-api` or
  `bcs-db-api`, not on these implementation crates.
- Internal SDK implementation crates are isolated so open-source distributions
  can remove them without removing local implementations.
- Plugins implement infrastructure capabilities only. They must not own BCS
  business persistence semantics such as friendship rules, actor visibility, or
  registry lifecycle policy.
- The TypeScript channel packages are not Rust infrastructure plugin-api
  implementations. Keep their package/runtime identities stable and do not add
  them to the Cargo workspace.

## Composition Root

`crates/bootstrap/bcs` selects implementations from the current config format:

- Cache uses the local in-memory plugin.
- DB uses the local SQLite plugin.

Service migration should receive plugin handles or service-owned store
implementations from bootstrap rather than constructing plugins directly.
