# Internal Plugin Integration Guide

This document describes how an internal `bcs-internal` binary can run the public BCS service with internal components such as AgentPass, ZDAS, ZCache, internal LLM providers, and Layotto clients.

The public branch owns contracts, config shape, public defaults, and bootstrap assembly. Internal crates own internal SDKs, credentials, sidecar clients, and deployment-specific provider implementations.

## Boundary Rules

- Do not add internal SDK crates to the public BCS workspace.
- Do not add private service endpoints, tokens, registries, or package indexes to public config.
- Do not add public config variants such as `DatabaseType::Zdas` or `CachePluginKind::Zcache`.
- Do not use a global `[plugins]` table for DB/cache/auth/LLM selection.
- Internal database connectivity for ZDAS should be exposed as a MySQL-compatible connection/provider adapter.
- Internal cache connectivity for ZCache should be exposed as a Redis-compatible provider adapter with any required key routing and value codec.
- Internal auth plugins should implement `bcs_auth_api::AuthPlugin`.
- Internal LLM provider plugins should implement `bcs_llm_api::LlmChatCompletionPort`.
- Internal crates may be linked into `bcs-internal`, but `src/bcs-internal/main.rs` should not hand-build infrastructure objects.

## Runtime Selection Model

BCS uses static self-registration:

- Public BCS defines plugin contracts, registry contracts, and public built-in providers.
- Internal crates are binary dependencies of `bcs-internal`.
- Internal crates self-register factories for provider keys such as `zdas`, `zcache`, `agentpass`, and `internal`.
- Public bootstrap reads `BcsConfig`, looks up linked factories, and builds infrastructure/extensions.
- If config selects an unregistered provider key, startup fails fast.

This is not runtime reflection and not dynamic library loading. The selected provider crate must be linked into the binary.

## Configuration Model

Each capability chooses its own provider:

- `llm.type` selects an LLM provider.
- `database.type` selects the database backend family.
- `database.mysql.connection.type` selects a MySQL-compatible connection provider.
- `cache.type` selects the cache backend family.
- `cache.redis.connection.type` selects a Redis-compatible provider.
- `auth.chain` names auth plugin provider keys. Public built-ins are checked first, then linked internal auth factories.

There is no `runtime` key for ZDAS/ZCache. In this integration model, `type = "zdas"` and `type = "zcache"` imply the internal Layotto client path.

## Recommended Internal Repository Layout

```text
bcs-internal-repo/
+-- ocb-public/                         # git submodule pointing at the public branch
+-- Cargo.toml
+-- configs/
|   +-- bcs-config.toml                 # public BcsConfig fields
+-- src/
    +-- bcs-internal/                   # thin binary; links internal plugin crates
    +-- internal/
        +-- bcs-auth-agentpass/         # registers auth.plugins type = "agentpass"
        +-- bcs-mysql-zdas-connector/   # registers mysql connection type = "zdas"
        +-- bcs-redis-zcache-connector/ # registers redis connection type = "zcache"
        +-- bcs-llm-internal/           # registers llm.type = "internal"
        +-- bcs-layotto/                # internal Layotto/ZDAS/ZCache client crate
```

Root `Cargo.toml` example:

```toml
[workspace]
members = [
    "src/bcs-internal",
    "src/internal/bcs-auth-agentpass",
    "src/internal/bcs-mysql-zdas-connector",
    "src/internal/bcs-redis-zcache-connector",
    "src/internal/bcs-llm-internal",
    "src/internal/bcs-layotto",
]
resolver = "2"

[workspace.dependencies]
bcs = { path = "ocb-public/src/bcs/crates/bootstrap/bcs" }
bcs-auth-api = { path = "ocb-public/src/bcs/crates/plugin-api/bcs-auth-api" }
bcs-cache-api = { path = "ocb-public/src/bcs/crates/plugin-api/bcs-cache-api" }
bcs-db-api = { path = "ocb-public/src/bcs/crates/plugin-api/bcs-db-api" }
bcs-llm-api = { path = "ocb-public/src/bcs/crates/plugin-api/bcs-llm-api" }
bcs-db-mysql = { path = "ocb-public/src/bcs/crates/plugins/bcs-db-mysql" }
bcs-cache-redis = { path = "ocb-public/src/bcs/crates/plugins/bcs-cache-redis" }
async-trait = "0.1"
futures = "0.3"
inventory = "0.3"
tokio = { version = "1", features = ["full"] }
```

## Thin Internal Main

The internal binary may need link anchors so Rust keeps the internal crates in the final binary. It should not construct DB/cache/auth/LLM objects directly.

```rust
use bcs_auth_agentpass as _;
use bcs_llm_internal as _;
use bcs_mysql_zdas_connector as _;
use bcs_redis_zcache_connector as _;

#[tokio::main]
async fn main() -> bcs::Result<()> {
    bcs::run_from_env().await
}
```

The public crate exposes `bcs::run_from_env()` and `bcs::run_from_env_with_config_dir(...)` as convenience wrappers around `BcsConfig::load_with_env`, logging initialization, registry-driven startup, and `BcsServer::run`.

## Static Registration API Examples

Internal crates self-register linked factories with `inventory::submit!`. The provider key in config must match the submitted `name`.

```rust
use std::sync::Arc;
use bcs::plugins::{
    CachePluginFactory, CachePluginRegistration, CachePluginKind,
    DbPluginFactory, DbPluginRegistration, DbPluginKind,
    LlmProviderFactory,
};
use futures::future::BoxFuture;

fn build_zcache(
    config: bcs::BcsConfig,
) -> BoxFuture<'static, bcs::Result<CachePluginRegistration>> {
    Box::pin(async move {
        let plugin = build_internal_zcache_plugin(config).await?;
        Ok(CachePluginRegistration {
            kind: CachePluginKind::External("zcache".to_string()),
            cache_zone: "zcache".to_string(),
            plugin: Arc::new(plugin),
        })
    })
}

inventory::submit! {
    CachePluginFactory {
        name: "zcache",
        build: build_zcache,
    }
}

fn build_zdas(
    config: bcs::BcsConfig,
) -> BoxFuture<'static, bcs::Result<DbPluginRegistration>> {
    Box::pin(async move {
        let plugin = build_internal_zdas_db_plugin(config).await?;
        Ok(DbPluginRegistration {
            kind: DbPluginKind::Mysql,
            plugin: Arc::new(plugin),
        })
    })
}

inventory::submit! {
    DbPluginFactory {
        name: "zdas",
        build: build_zdas,
    }
}

fn build_internal_llm(config: bcs::BcsConfig) -> bcs::Result<Arc<dyn bcs_llm_api::LlmChatCompletionPort>> {
    Ok(Arc::new(InternalLlmProvider::from_config(config.llm)?))
}

inventory::submit! {
    LlmProviderFactory {
        name: "internal",
        build: build_internal_llm,
    }
}
```

Auth plugins register separately:

```rust
use bcs::auth_wiring::{AuthPluginBuildContext, AuthPluginFactoryRegistration};
use bcs_auth_api::AuthPlugin;

fn build_agentpass(ctx: AuthPluginBuildContext) -> Result<Option<Box<dyn AuthPlugin>>, String> {
    if ctx.name != "agentpass" {
        return Ok(None);
    }
    Ok(Some(Box::new(AgentPassAuthPlugin::from_auth_config(ctx.config)?)))
}

inventory::submit! {
    AuthPluginFactoryRegistration {
        name: "agentpass",
        build: build_agentpass,
    }
}
```

## Public Direct MySQL + Redis Config

This config uses only public providers and local loopback services.

```toml
bind = "127.0.0.1"
port = 21000
bots_base_dir = "./data/bots"

[database]
type = "mysql"

[database.mysql]
database = "bcs"
statement_protocol = "text"
stmt_cache_size = 0
pool_size = 20
min_pool_size = 5
timeout_secs = 30

[database.mysql.connection]
type = "direct"
host = "127.0.0.1"
port = 3306
user = "bcs"
password = "bcsbcs"

[cache]
type = "redis"

[cache.redis]
timeout_secs = 5
key_prefix = "bcs:"

[cache.redis.connection]
type = "direct"
host = "127.0.0.1"
port = 6379
auth_mode = "disabled"

[auth]
chain = ["local", "session"]
require_authentication = false

[llm]
type = "none"
model = "gpt-4.1-mini"
```

## Internal ZDAS + ZCache Config

This config keeps BCS semantics as MySQL-compatible DB and Redis-compatible cache, but uses internal providers for the connections.

```toml
bind = "0.0.0.0"
port = 21000
bots_base_dir = "/data/bcs/bots"

[database]
type = "mysql"

[database.mysql]
database = "bcs"
statement_protocol = "text"
stmt_cache_size = 0
pool_size = 20
min_pool_size = 5
timeout_secs = 30

[database.mysql.connection]
type = "zdas"
host = "127.0.0.1"
port = 11306
user = "mesh-routing-user"
password = ""

[cache]
type = "redis"

[cache.redis]
timeout_secs = 5
key_prefix = "bcs:"

[cache.redis.connection]
type = "zcache"
host = "127.0.0.1"
port = 16379
app_name = "bcs"
cache_name = "bcsCache"
route_type = "G"
auth_mode = "tbase"

[cache.redis.routing]
type = "zcache_context"
default_route = "default"

[auth]
chain = ["agentpass", "session"]
require_authentication = true

[llm]
type = "internal"
model = "internal-judge-model"
timeout_ms = 120000
```

Do not commit real internal endpoints or credentials.

## ZDAS as a MySQL Connection Provider

ZDAS is not a separate public database backend in this design. It is the MySQL/OceanBase-compatible connection provider selected for the single BCS database:

```toml
[database.mysql.connection]
type = "zdas"
host = "127.0.0.1"
port = 11306
user = "mesh-routing-user"
password = ""
```

Public bootstrap sees `database.type = "mysql"` and, when `database.mysql.connection.type = "zdas"`, asks the linked DB factory named `zdas` to build the DB plugin. The current first phase registers a full `DbPlugin`:

```rust
fn build_zdas(
    config: bcs::BcsConfig,
) -> futures::future::BoxFuture<'static, bcs::Result<bcs::plugins::DbPluginRegistration>> {
    Box::pin(async move {
        let plugin = bcs_db_zdas::ZdasDbPlugin::from_bcs_config(&config).await?;
        Ok(bcs::plugins::DbPluginRegistration {
            kind: bcs::DbPluginKind::Mysql,
            plugin: std::sync::Arc::new(plugin),
        })
    })
}

inventory::submit! {
    bcs::plugins::DbPluginFactory {
        name: "zdas",
        build: build_zdas,
    }
}
```

The next refactor should move this one step further: `bcs-db-mysql` should own `DbPlugin` behavior (`DbValue` conversion, row conversion, transaction result mapping, health checks, and contract semantics), while the ZDAS connector only adapts `AsyncZDASManager` to a MySQL manager/provider trait.

`bcs-layotto` should remain a client crate. Do not put public BCS bootstrap logic in it. If a crate is needed for registration, use a thin integration crate such as `bcs-mysql-zdas-connector`.

## ZCache as a Redis Provider

ZCache is not a public cache backend variant in this design. It is the Redis-compatible provider selected for the single BCS cache:

```toml
[cache.redis.connection]
type = "zcache"
host = "127.0.0.1"
port = 16379
app_name = "bcs"
cache_name = "bcsCache"
route_type = "G"
auth_mode = "tbase"

[cache.redis.routing]
type = "zcache_context"
default_route = "default"
```

The ZCache connector must preserve provider-specific behavior that is not represented by a bare Redis connection:

- key routing through ZCache context encoding
- optional legacy value codec if existing ZCache data used one
- ZCache auth payload construction
- known conditional-write limitations if the backend cannot provide an exact Redis primitive

Public `bcs-cache-redis` should own Redis-compatible `CachePlugin` behavior. Internal ZCache should register a provider/key-routing adapter under `type = "zcache"` rather than duplicating the entire cache contract implementation unless a backend semantic gap makes that unavoidable.

## AgentPass Auth Plugin

AgentPass should be an internal implementation of `bcs_auth_api::AuthPlugin` and self-register under `type = "agentpass"`.

```rust
use async_trait::async_trait;
use axum::http::{header::AUTHORIZATION, HeaderMap};
use bcs_auth_api::{AuthError, AuthPlugin, AuthPrincipal, AuthSource};

pub struct AgentPassAuthPlugin {
    client: AgentPassClient,
}

impl AgentPassAuthPlugin {
    pub fn new(client: AgentPassClient) -> Self {
        Self { client }
    }
}

pub struct AgentPassClient;

impl AgentPassClient {
    async fn verify(&self, _token: &str) -> Result<AgentPassIdentity, String> {
        todo!("call the internal AgentPass SDK")
    }
}

pub struct AgentPassIdentity {
    pub user_id: String,
    pub user_name: Option<String>,
    pub bot_uuid: Option<String>,
    pub owner_id: Option<String>,
}

fn extract_agentpass_token(headers: &HeaderMap) -> Option<String> {
    if let Some(value) = headers.get("x-agentpass-token").and_then(|v| v.to_str().ok()) {
        let token = value.trim();
        if !token.is_empty() {
            return Some(token.to_string());
        }
    }

    let value = headers.get(AUTHORIZATION)?.to_str().ok()?;
    let token = value.strip_prefix("Bearer ").unwrap_or(value).trim();
    (!token.is_empty()).then(|| token.to_string())
}

#[async_trait]
impl AuthPlugin for AgentPassAuthPlugin {
    fn can_authenticate(&self, headers: &HeaderMap) -> bool {
        extract_agentpass_token(headers).is_some()
    }

    async fn authenticate(
        &self,
        headers: &HeaderMap,
    ) -> Result<Option<AuthPrincipal>, AuthError> {
        let Some(token) = extract_agentpass_token(headers) else {
            return Ok(None);
        };

        let identity = self
            .client
            .verify(&token)
            .await
            .map_err(AuthError::InvalidToken)?;

        let mut principal = AuthPrincipal::new(AuthSource::AgentPass);
        principal.user_id = Some(identity.user_id);
        principal.user_name = identity.user_name;
        principal.bot_uuid = identity.bot_uuid;
        principal.owner_id = identity.owner_id;
        principal.token = Some(token);
        Ok(Some(principal))
    }

    fn priority(&self) -> u8 {
        1
    }

    fn name(&self) -> &'static str {
        "agentpass"
    }
}
```

Use the intended priority when AgentPass may handle JWT-shaped bearer tokens before the public session plugin.

## Internal LLM Provider

BCS LLM-based judge evaluation consumes `bcs_llm_api::LlmChatCompletionPort`. The internal provider should translate the public request shape to the internal model gateway and return a content string that `bcs_judge::LlmJudgeService` can parse as JSON.

If the internal model gateway is OpenAI Chat Completions compatible, an internal connector can register an OpenAI-compatible provider using internal-only config. If the gateway needs a native SDK or a different protocol, implement `LlmChatCompletionPort` in an internal crate and register it under `llm.type = "internal"`.

```rust
use async_trait::async_trait;
use bcs_llm_api::{
    LlmChatCompletionPort, LlmChatCompletionRequest, LlmChatCompletionResponse, LlmError,
};
use serde_json::{Value, json};

#[derive(Clone)]
pub struct InternalLlmProvider {
    client: InternalModelClient,
}

impl InternalLlmProvider {
    pub async fn connect(config: InternalLlmConfig) -> Result<Self, LlmError> {
        let client = InternalModelClient::connect(config)
            .await
            .map_err(LlmError::Config)?;
        Ok(Self { client })
    }
}

pub struct InternalLlmConfig {
    pub endpoint: String,
}

#[derive(Clone)]
pub struct InternalModelClient;

pub struct InternalModelResponse {
    pub content: String,
    pub raw: Value,
}

impl InternalModelClient {
    async fn connect(_config: InternalLlmConfig) -> Result<Self, String> {
        todo!("create the internal model gateway client")
    }

    async fn complete(
        &self,
        _model: &str,
        _messages: Value,
        _response_format: Option<Value>,
    ) -> Result<InternalModelResponse, String> {
        todo!("call the internal model gateway")
    }
}

#[async_trait]
impl LlmChatCompletionPort for InternalLlmProvider {
    async fn complete(
        &self,
        request: LlmChatCompletionRequest,
    ) -> Result<LlmChatCompletionResponse, LlmError> {
        let LlmChatCompletionRequest {
            model,
            messages,
            response_format,
            stream,
        } = request;

        if stream {
            return Err(LlmError::Config(
                "internal LLM provider does not support streaming judge calls".to_string(),
            ));
        }

        let response = self
            .client
            .complete(&model, json!(messages), response_format)
            .await
            .map_err(LlmError::Request)?;

        Ok(LlmChatCompletionResponse {
            content: response.content,
            raw: response.raw,
        })
    }
}
```

For state-machine judge calls, `content` must be a JSON object string matching the requested `response_format` schema. Provider failures should return `LlmError::Request` or `LlmError::Response`; do not return synthetic success for malformed model output.

## Required Tests

Every internal provider should run the public contract harnesses or an equivalent compatibility suite.

```rust
#[tokio::test]
async fn zdas_provider_passes_db_plugin_contract() {
    let server = start_bcs_with_zdas_test_config().await.expect("server");
    bcs_test_support::db_plugin_contract_tests(server.db_plugin()).await;
}

#[tokio::test]
async fn zcache_provider_passes_cache_plugin_contract() {
    let server = start_bcs_with_zcache_test_config().await.expect("server");
    bcs_test_support::cache_plugin_contract_tests(server.cache_plugin()).await;
}
```

Recommended internal CI:

```bash
cargo test -p bcs-mysql-zdas-connector
cargo test -p bcs-redis-zcache-connector
cargo test -p bcs-auth-agentpass
cargo test -p bcs-llm-internal
cargo test -p bcs-internal
```

Recommended public compatibility checks after updating the submodule:

```bash
cargo test -p bcs-auth-api
cargo test -p bcs-cache-api
cargo test -p bcs-db-api
cargo test -p bcs-llm-api
cargo test -p bcs-judge llm_judge
cargo check -p bcs
```
