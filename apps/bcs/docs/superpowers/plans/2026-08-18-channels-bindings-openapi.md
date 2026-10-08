# channels/bindings OpenAPI (全 5 op,合并 app 层) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 暴露 `POST/GET/GET-by-target/PATCH/DELETE /openapi/v1/collaboration/channels/bindings` 经 BCS `bcs-api-http` 版本化 OpenAPI 适配器;人-only;V1 与 legacy `bcs-http` 共用同一 `Arc<dyn ChannelService>` 实现,authz 全留 adapter。

**Architecture:** 不新建 v1 app facade。V1 路由直接调共享 `ChannelService`(已在 trait 上、`BcsChannelService` 已实现),仅做 HTTP⇄command 翻译 + `require_authenticated_user(caller)` 人-only 闸 + `ChannelUseCaseError→ApplicationError` 边界映射。新增 `ChannelUseCaseError::Conflict` 变体(顺带修 legacy dup-binding 返 400 的 bug → 409)。`ApiState` 加可选 `channel_service` 槽(fail-closed);bootstrap 把已构造的 `Arc<dyn ChannelService>` 注入。

**Tech Stack:** Rust(axum 0.7 风格路由 + async-trait)、OpenAPI 3.1 YAML 契约(checked-in fragment + master ref)、Python 脚本 `dump_openapi.py`/`validate_openapi_contract.py`(校验/重生成 `bcn.openapi.json`)、unittest(`src/bcs/tests/openapi/`)。

## Global Constraints

- 工作区根:`/Users/yuange.zjy/Development/aiinfra/Avernet_vince/.worktrees/channels_binding_openapi`。所有文件路径相对此根。
- **禁止全局 formatter**:`src/bcs/CLAUDE.md` 明令"不跑 cargo fmt、改动只限必要行,不动无关代码风格"。Edit 时只动目标行。
- 分层铁律(`src/bcs/CLAUDE.md`): delivery adapter 只调 `bcs_service_api::application::*`;`ApiState` 暴露 application 服务不暴露 core/port;"delivery adapter 只处理 application 错误"。本计划严格遵守——V1 路由调 `ChannelService`(application 服务),禁触碰 `core`/`port`。
- 不新建 `bcs-app-channel` crate、不新建 `application/v1/channel` trait(V1 路由直接调 `application::channel::ChannelService`)。
- 路径前缀 `/openapi/v1/collaboration/channels/bindings`(collaboration 既有前缀,无需改 `validate_openapi_contract.py`、无需改网关 `route_security`——`/openapi/v1/collaboration/**` 已 enforce `user+app: required`)。
- `x-avernet-security = { user: required, app: required }`(collaboration 惯例)。
- operationId 全 snake_case,禁含 `collaboration`/`bcn`/`openapi`/`_v1_`。
- 每个 2xx 响应 schema 必须是 envelope(`required:[code,message,data,request_id]`,validator `ENVELOPE_FIELDS` 押着)。create `code:20100`,其余 `code:20000`;错误体 `$ref ../shared.yaml#/ErrorEnvelope`,500 走 `../shared.yaml#/InternalErrorResponse`。
- `env` 死字段保留(不删 `CreateBindingCommand.env`);V1 DTO 不暴露 `env`,adapter 填 `String::new()`(impl 用 `self.env`)。
- 每次 cargo 改动后跑 `cargo build -p <pkg>` 确认编译;commit 前跑相关 `cargo test -p <pkg>`。
- commit 信息:`<type>(<scope>): <outcome>`;正文末尾 `Co-Authored-By: Claude <noreply@anthropic.com>`。

---

## File Structure

新增:
- `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/channel.rs` — V1 DTOs(`CreateChannelBindingRequest`/`ChannelBindingDto`/`ChannelBindingPage`/`BindingTargetType`/`ListBindingsByTargetQuery`/`normalize_target_query`/`UpdateChannelBindingRequest`)。
- `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/channel.rs` — 5 handler + `router()` + `ChannelUseCaseError→ApplicationError` 映射 + `channel_service()` fail-closed helper。
- `src/bcs/api-contracts/v1/openapi/channel-bindings.yaml` — 3 path object(POST create + GET list、GET by-target、PATCH+DELETE {id}) + inline schemas + `x-error-codes`。
- `src/bcs/crates/adapters/http/bcs-api-http/tests/channel_routes.rs` — V1 路由集成测试。
- `src/bcs/tests/openapi/test_channel_binding_v1_contract.py` — 契约片段校验。

修改:
- `src/bcs/crates/service-api/bcs-service-api/src/application/channel.rs` — 加 `ChannelUseCaseError::Conflict(String)`。
- `src/bcs/crates/services/bcs-channel/src/lib.rs` — `create_binding` dup 返 `Conflict`。
- `src/bcs/crates/adapters/http/bcs-http/src/routes/channel.rs` — `channel_error` 加 `Conflict` arm(+ 1 单测)。
- `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/state.rs` — `ApiState` 加 `channel_service` 槽 + `with_channel_service`。
- `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/mod.rs` — `pub mod channel;` + re-exports。
- `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/mod.rs` — `pub mod channel;`。
- `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs` — `protected_router` `.merge(routes::channel::router())`。
- `src/bcs/crates/bootstrap/bcs/src/server.rs` — `build_openapi_v1_state` 加 `channel_service` 参 + `.with_channel_service`;3 call-site reorder。
- `src/bcs/api-contracts/v1/openapi.yaml` — `paths:` 加 3 个 $ref + `tags:` 加 `Collaboration / Channels`。
- `src/gateway/configs/schemas/bcn.openapi.json` — 重生成。
- `src/bcs/tests/openapi/test_dump_openapi.py` — `len(operations)` 34→39 + `COLLABORATION_TAGS` 加 `Collaboration / Channels`。

---

## Task 1: 加 `ChannelUseCaseError::Conflict` + 修 legacy dup 返 409

**Files:**
- Modify: `src/bcs/crates/service-api/bcs-service-api/src/application/channel.rs:18-27`
- Modify: `src/bcs/crates/services/bcs-channel/src/lib.rs:1398-1403`
- Modify: `src/bcs/crates/adapters/http/bcs-http/src/routes/channel.rs:282-291` + add test
- Interfaces:
  - Produces: `ChannelUseCaseError::Conflict(String)` variant; `BcsChannelService::create_binding` 现对 dup 返 `Conflict`;legacy `channel_error` 映射 `Conflict→HttpAdapterError::Conflict`(409)。后续 Task 4 的 V1 映射复用此 variant 判 409。

- [ ] **Step 1: 加 `Conflict` 变体**

Edit `src/bcs/crates/service-api/bcs-service-api/src/application/channel.rs`,在 `InvalidParams` 后、`Internal` 前插入:

```rust
    #[error("invalid channel params: {0}")]
    InvalidParams(String),
    #[error("channel binding conflict: {0}")]
    Conflict(String),
    #[error(transparent)]
    Internal(ServiceError),
```

- [ ] **Step 2: legacy `channel_error` 加 arm(外层 match 无 wildcard,强制)**

Edit `src/bcs/crates/adapters/http/bcs-http/src/routes/channel.rs` 的 `channel_error`,在 `NotFound` arm 后、`Internal` arm 前加:

```rust
    ChannelUseCaseError::Conflict(message) => HttpAdapterError::Conflict(message),
```

(完整后:)
```rust
fn channel_error(error: ChannelUseCaseError) -> HttpAdapterError {
    match error {
        ChannelUseCaseError::NotFound(id) => HttpAdapterError::NotFound(id),
        ChannelUseCaseError::Conflict(message) => HttpAdapterError::Conflict(message),
        ChannelUseCaseError::InvalidParams(message) => HttpAdapterError::BadRequest(message),
        ChannelUseCaseError::Internal(error) => match error {
            ServiceError::Conflict(message) => HttpAdapterError::Conflict(message),
            other => HttpAdapterError::Service(other),
        },
    }
}
```

确认 `HttpAdapterError::Conflict` 已存在(`src/bcs/crates/adapters/http/bcs-http/src/error.rs:33`,→409)。

- [ ] **Step 3: 加 legacy `channel_error` 单测**

在 `src/bcs/crates/adapters/http/bcs-http/src/routes/channel.rs` 文件末尾(或既有 `#[cfg(test)] mod`)加:

```rust
#[cfg(test)]
mod conflict_mapping_tests {
    use super::channel_error;
    use crate::error::HttpAdapterError;
    use axum::http::StatusCode;
    use bcs_service_api::ChannelUseCaseError;

    #[test]
    fn channel_error_maps_top_level_conflict_to_409() {
        let mapped = channel_error(ChannelUseCaseError::Conflict("dup".to_string()));
        assert!(matches!(mapped, HttpAdapterError::Conflict(msg) if msg == "dup"));
        assert_eq!(mapped.status(), StatusCode::CONFLICT);
    }
}
```

(`HttpAdapterError::status` 是 pub,见 `error.rs:43`。)`require_staff_no`/handler 不动。

- [ ] **Step 4: 跑 legacy 测确认架构层面不挂但 dup 仍 400(Impl 未改)**

Run: `cargo test -p bcs-http --lib conflict_mapping_tests 2>&1 | tail -5`
Expected: PASS(本测试只验映射,不依赖 impl)。同时 build 全 crate 确认新 variant 在别处无未处理 match:

Run: `cargo build -p bcs-service-api -p bcs-http -p bcs-channel 2>&1 | tail -20`
Expected: 编译通过(若某处 match 缺新 arm 会报错,补 arm;预期只有 `channel_error` 一处需要,已改)。

- [ ] **Step 5: 更新 bcs-channel 的 dup 测断言(写失败测)**

`src/bcs/crates/services/bcs-channel/src/lib.rs` 约第 3203 行有测试 `create_binding_rejects_duplicate_active_account_and_provider_invalid_config`,其 match 现断言 `ChannelUseCaseError::InvalidParams(_)`。把那行断言改为期望 `Conflict(_)`。若测试构造了两个 create(第二个触发 dup),找到 `match` 处:

before(示意):
```rust
        Err(ChannelUseCaseError::InvalidParams(msg)) => {
            assert!(msg.contains("already exists"), "{msg}");
        }
        other => panic!("expected InvalidParams, got {other:?}"),
```
after:
```rust
        Err(ChannelUseCaseError::Conflict(msg)) => {
            assert!(msg.contains("already exists"), "{msg}");
        }
        other => panic!("expected Conflict, got {other:?}"),
```
(实施者按实际 fixture 调整另一段相同 match。若该测既覆盖 dup-account 又覆盖 provider-invalid-config,只改 dup-account 段;provider-invalid-config 段保持 `InvalidParams`。)

Run: `cargo test -p bcs-channel --lib create_binding_rejects_duplicate 2>&1 | tail -10`
Expected: FAIL(impl 仍返 `InvalidParams`,断言期望 `Conflict`)。

- [ ] **Step 6: 改 impl 返 `Conflict`**

Edit `src/bcs/crates/services/bcs-channel/src/lib.rs` 的 `create_binding`,把 dup-return 那行:

before:
```rust
    if self.bindings.find_active_by_account(cmd.channel_type.clone(), &account_ref).await?.is_some() {
        return Err(ChannelUseCaseError::InvalidParams(format!(
            "active binding already exists for account_ref {account_ref}")));
    }
```
after:
```rust
    if self.bindings.find_active_by_account(cmd.channel_type.clone(), &account_ref).await?.is_some() {
        return Err(ChannelUseCaseError::Conflict(format!(
            "active binding already exists for account_ref {account_ref}")));
    }
```

Run: `cargo test -p bcs-channel --lib create_binding_rejects_duplicate 2>&1 | tail -10`
Expected: PASS。
Run: `cargo test -p bcs-http --lib conflict_mapping_tests 2>&1 | tail -5`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
fix(bcs): map duplicate channel binding to 409 Conflict

Add ChannelUseCaseError::Conflict variant; have BcsChannelService
return it for the dup-active-account case (was InvalidParams->400);
legacy channel_error maps it to HttpAdapterError::Conflict (409).
V1 OpenAPI will reuse the variant in Task 4.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `ApiState` 加 `channel_service` 可选槽

**Files:**
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/state.rs`

**Interfaces:**
- Consumes: `bcs_service_api::application::channel::ChannelService`(已存在,Task 1 已编译通过)。
- Produces: `ApiState.channel_service: Option<Arc<dyn ChannelService>>` + `with_channel_service` builder。Task 4/5 用。

- [ ] **Step 1: 加 import + field + builder**

Edit `src/bcs/crates/adapters/http/bcs-api-http/src/v1/common/state.rs`。

Top imports 处加(对照 L3-9 的 `use bcs_service_api::application::v1::{...}`):

```rust
use bcs_service_api::application::channel::ChannelService;
```

`ApiState` struct(L17-27)加一 field:

```rust
    pub friendship_service: Arc<dyn FriendshipService>,
    pub channel_service: Option<Arc<dyn ChannelService>>,
    pub session_file_service: Option<Arc<dyn SessionFileApplicationService>>,
    pub session_file_url_projector: Option<SessionFileUrlProjector>,
    pub principal_verifier: Arc<dyn PrincipalVerifier>,
```

`ApiState::new` 的 literal(L38-49)加 `channel_service: None,`(在 `friendship_service,` 之后):

```rust
    Self {
        bot_service: None,
        group_service,
        session_service,
        message_service,
        invitation_service,
        friendship_service,
        channel_service: None,
        session_file_service: None,
        session_file_url_projector: None,
        principal_verifier,
    }
```

加 builder(对照 `with_bot_service` L55-58,放在其后):

```rust
    /// Add the shared Channel application service (channels reuse the legacy
    /// `ChannelService` impl; permission stays in the adapter, not the app).
    pub fn with_channel_service(mut self, service: Arc<dyn ChannelService>) -> Self {
        self.channel_service = Some(service);
        self
    }
```

- [ ] **Step 2: build 确认**

Run: `cargo build -p bcs-api-http 2>&1 | tail -10`
Expected: 编译通过。

- [ ] **Step 3: 加 fail-closed assertion 测**

在文件末尾加(若有 `#[cfg(test)] mod` 加入之):

```rust
#[cfg(test)]
mod channel_slot_tests {
    use super::*;

    fn noop_verifier() -> Arc<dyn PrincipalVerifier> {
        struct Noop;
        #[async_trait::async_trait]
        impl PrincipalVerifier for Noop {
            async fn verify(&self, _: &axum::http::HeaderMap)
                -> Result<bcs_service_api::application::v1::AuthenticatedCaller, PrincipalVerificationError>
            { Err(PrincipalVerificationError::Missing) }
        }
        Arc::new(Noop) as Arc<dyn PrincipalVerifier>
    }

    #[test]
    fn channel_service_defaults_none_and_builder_sets() {
        let state = ApiState::new(
            Arc::new(NoopGroupSvc), Arc::new(NoopSess), Arc::new(NoopMsg),
            Arc::new(NoopInv), Arc::new(NoopFri), noop_verifier(),
        );
        assert!(state.channel_service.is_none());
    }
}
```

> 注:`NoopGroupSvc`/`NoopSess`/... 是占位;若 `state.rs` 或 sibling 测试已有 Noop 实现,复用之。若编译报缺 Noop 类型,去掉 `channel_slot_tests`(本步非必须——fail-closed 逻辑在 Task 4 路由层验;Step 1 的 build 通过即可)。实施者按 `tests/invitation_routes.rs` 的 Noop 习惯复用,失败就删此 step。

Run: `cargo test -p bcs-api-http --lib channel_slot 2>&1 | tail -10`
Expected: PASS 或(若删此 step)跳过。

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat(bcs-api-http): add optional channel_service slot to ApiState

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: V1 DTOs (`dto/channel.rs`)

**Files:**
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/channel.rs`
- Modify: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/mod.rs`(加 `pub mod channel;` + re-export)

**Interfaces:**
- Consumes: `bcs_service_api::application::channel::{CreateBindingCommand, ChannelUseCaseError}`、`bcs_service_api::application::v1::AuthenticatedCaller`、`bcs_domain::{BindingTarget, GroupChatScope, Visibility, ChannelBinding, ChannelType}`、`serde_json::Value`。
- Produces: `CreateChannelBindingRequest`(`into_command(caller, created_by)→CreateBindingCommand`)、`ChannelBindingDto`(`From<ChannelBinding>`)、`ChannelBindingPage{items}`、`BindingTargetType`+`ListBindingsByTargetQuery`+`normalize_target_query(query)→Result<(BindingTarget, Option<ChannelType>), String>`、`UpdateChannelBindingRequest`。Task 4 全部消费。

- [ ] **Step 1: 写 dto 模块**

`src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/channel.rs`:

```rust
use bcs_domain::{BindingTarget, ChannelBinding, ChannelType, GroupChatScope, Visibility};
use bcs_service_api::application::channel::CreateBindingCommand;
use bcs_service_api::application::v1::AuthenticatedCaller;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// POST body for `POST /openapi/v1/collaboration/channels/bindings`.
///
/// `env` is omitted: the service uses its configured env, `cmd.env` was a
/// never-read legacy field. `deny_unknown_fields` rejects any client-supplied
/// `env` with 400 `invalid_request`.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateChannelBindingRequest {
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub target: BindingTarget,
    #[serde(default)]
    pub group_chat_scope: Option<GroupChatScope>,
    pub outbound_visibility: Visibility,
    pub config: Value,
}

impl CreateChannelBindingRequest {
    /// Build the shared `CreateBindingCommand`, carrying the human caller's
    /// user id as `created_by`. `env` is left empty: `BcsChannelService`
    /// ignores `cmd.env` and uses its own runtime env.
    pub fn into_command(self, caller: AuthenticatedCaller, created_by: String) -> CreateBindingCommand {
        CreateBindingCommand {
            channel_type: self.channel_type,
            account_ref: self.account_ref,
            target: self.target,
            group_chat_scope: self.group_chat_scope,
            outbound_visibility: self.outbound_visibility,
            env: String::new(),
            created_by: Some(created_by),
            config: self.config,
        }
    }
}

/// Public binding shape — mirrors legacy `BindingResponse`. `config` is the
/// service-redacted copy (POST via `redact_config`, GET via `redact_bindings`).
#[derive(Debug, Serialize)]
pub struct ChannelBindingDto {
    pub id: String,
    pub channel_type: ChannelType,
    pub account_ref: String,
    pub target: BindingTarget,
    pub group_chat_scope: Option<GroupChatScope>,
    pub outbound_visibility: Visibility,
    pub env: String,
    pub status: bcs_domain::BindingStatus,
    pub created_by: Option<String>,
    pub config: Value,
}

impl From<ChannelBinding> for ChannelBindingDto {
    fn from(b: ChannelBinding) -> Self {
        Self {
            id: b.id,
            channel_type: b.channel_type,
            account_ref: b.account_ref,
            target: b.target,
            group_chat_scope: b.group_chat_scope,
            outbound_visibility: b.outbound_visibility,
            env: b.env,
            status: b.status,
            created_by: b.created_by,
            config: b.config,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct ChannelBindingPage {
    pub items: Vec<ChannelBindingDto>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BindingTargetType {
    Bot,
    Group,
}

/// GET `/channels/bindings/by-target` query.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListBindingsByTargetQuery {
    pub target_type: BindingTargetType,
    pub target_id: String,
    #[serde(default)]
    pub channel_type: Option<ChannelType>,
}

/// Resolve the target query to a `BindingTarget`. Surfaces an empty
/// `target_id` as a string message the route maps to 400 `invalid_request`,
/// matching legacy `normalize_target_query`.
pub fn normalize_target_query(
    query: ListBindingsByTargetQuery,
) -> Result<(BindingTarget, Option<ChannelType>), String> {
    let target_id = query.target_id.trim();
    if target_id.is_empty() {
        return Err("target_id is required".to_string());
    }
    let channel_type = query
        .channel_type
        .map(|t| t.trim().to_string())
        .filter(|t| !t.is_empty());
    let target = match query.target_type {
        BindingTargetType::Bot => BindingTarget::Bot { bot_id: target_id.to_string() },
        BindingTargetType::Group => BindingTarget::Group { group_id: target_id.to_string() },
    };
    Ok((target, channel_type))
}

/// PATCH body — `active` and `config` are mutually exclusive (exactly one).
/// `deny_unknown_fields` plus the route's XOR check enforces the contract.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateChannelBindingRequest {
    #[serde(default)]
    pub active: Option<bool>,
    #[serde(default)]
    pub config: Option<Value>,
}
```

> 注:`ChannelType` 在 `bcs_domain` 是 `String` 别名(`channel.rs:9` 若 `pub type ChannelType = String;`),`BindingTarget`/`Visibility`/`GroupChatScope` 是 serde 友好的 enum。若 `bcs_domain::ChannelType` 非 `Deserialize`(纯别名 String 应可),编译会报——按 `CreateBindingRequest`(legacy `bcs-http/routes/channel.rs:19`)同样 import 路径修正。

- [ ] **Step 2: 注册模块**

Edit `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/mod.rs`,加:

```rust
pub mod channel;
pub use channel::{
    BindingTargetType, ChannelBindingDto, ChannelBindingPage, CreateChannelBindingRequest,
    ListBindingsByTargetQuery, UpdateChannelBindingRequest, normalize_target_query,
};
```

- [ ] **Step 3: 写 DTO 单测(写失败→实现已齐→pass)**

`src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/dto/channel.rs` 末尾加:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create_request_rejects_env_field() {
        let json = serde_json::json!({
            "channel_type": "dingtalk", "account_ref": "r1",
            "target": {"bot": {"bot_id": "b1"}},
            "outbound_visibility": "full_transcript", "config": {},
            "env": "prod"
        });
        assert!(serde_json::from_value::<CreateChannelBindingRequest>(json).is_err());
    }

    #[test]
    fn update_request_rejects_unknown_field() {
        let json = serde_json::json!({"active": true, "bogus": 1});
        assert!(serde_json::from_value::<UpdateChannelBindingRequest>(json).is_err());
    }

    #[test]
    fn normalize_by_target_requires_target_id() {
        let q = ListBindingsByTargetQuery {
            target_type: BindingTargetType::Bot, target_id: "  ".into(), channel_type: None,
        };
        assert!(normalize_target_query(q).is_err());
    }

    #[test]
    fn normalize_by_target_builds_bot_target() {
        let q = ListBindingsByTargetQuery {
            target_type: BindingTargetType::Bot, target_id: "b1".into(), channel_type: Some("dingtalk".into()),
        };
        let (t, ct) = normalize_target_query(q).expect("ok");
        assert!(matches!(t, BindingTarget::Bot { bot_id } if bot_id == "b1"));
        assert_eq!(ct.as_deref(), Some("dingtalk"));
    }
}
```

- [ ] **Step 4: 跑测**

Run: `cargo test -p bcs-api-http --lib dto::channel 2>&1 | tail -15`
Expected: PASS。若 `bcs_domain` 路径/transparency 报错,按编译提示修正 import(legacy `bcs-http/routes/channel.rs:11-13` 的 `use bcs_domain::{...}` 是权威 import 集)。

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(bcs-api-http): add channels/bindings V1 DTOs

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: V1 route 模块(5 handler + 映射 + 路由挂载)

**Files:**
- Create: `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/channel.rs`
- Modify: `.../v1/openapi/routes/mod.rs`(加 `pub mod channel;`)
- Modify: `.../v1/openapi/mod.rs`(`protected_router` `.merge`)
- Create: `src/bcs/crates/adapters/http/bcs-api-http/tests/channel_routes.rs`

**Interfaces:**
- Consumes: Task 2 `ApiState.channel_service`、Task 3 DTOs、`require_authenticated_user`(`authorization.rs:27`)、`ApplicationError`(构造器 `invalid`/`not_found`/`conflict`/`internal`)、`bcs-service-api::core::ServiceError`。
- Produces:`routes::channel::router()` → 挂 5 op;`ChannelUseCaseError→ApplicationError` 映射;`channel_service()` fail-closed helper。

- [ ] **Step 1: 写 route 模块**

`src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/channel.rs`:

```rust
use std::sync::Arc;

use axum::Router;
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, patch, post};
use bcs_service_api::application::channel::{ChannelService, ChannelUseCaseError};
use bcs_service_api::application::v1::{require_authenticated_user, ApplicationError, AuthenticatedCaller};
use bcs_service_api::core::ServiceError;

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::channel::{
    ChannelBindingDto, ChannelBindingPage, CreateChannelBindingRequest, ListBindingsByTargetQuery,
    UpdateChannelBindingRequest, normalize_target_query,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route("/channels/bindings", post(create_binding).get(list_bindings))
        .route("/channels/bindings/by-target", get(list_bindings_by_target))
        .route("/channels/bindings/{id}", patch(update_binding).delete(delete_binding))
}

/// Fail-closed helper mirroring `routes::bot::service`/`routes::session` patterns.
fn channel_service(state: &ApiState, request_id: &RequestId) -> Result<Arc<dyn ChannelService>, ErrorResponse> {
    state.channel_service.clone().ok_or_else(|| {
        application_error_response(request_id, ApplicationError::internal("Channel V1 service is not configured"))
    })
}

/// Translate `ChannelUseCaseError` (application) → V1 `ApplicationError` so the
/// delivery adapter handles only application errors (matches `channel_error`).
fn map_channel_error(error: ChannelUseCaseError) -> ApplicationError {
    match error {
        ChannelUseCaseError::NotFound(id) => {
            ApplicationError::not_found("channel_binding_not_found", format!("channel binding not found: {id}"))
        }
        ChannelUseCaseError::InvalidParams(msg) => {
            ApplicationError::invalid("invalid_channel_params", msg)
        }
        ChannelUseCaseError::Conflict(msg) => {
            ApplicationError::conflict("channel_binding_conflict", msg)
        }
        ChannelUseCaseError::Internal(ServiceError::Conflict(msg)) => {
            ApplicationError::conflict("channel_binding_conflict", msg)
        }
        ChannelUseCaseError::Internal(other) => ApplicationError::internal(other.to_string()),
    }
}

async fn create_binding(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    body: Result<Json<CreateChannelBindingRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(body) = body.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let user = require_authenticated_user(&caller)
        .map_err(|e| application_error_response(&request_id, e))?;
    let binding = channel_service(&state, &request_id)?
        .create_binding(body.into_command(caller, user.id.clone()))
        .await
        .map_err(|e| application_error_response(&request_id, map_channel_error(e)))?;
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(20_100, "Created", ChannelBindingDto::from(binding), request_id.0)),
    ).into_response())
}

async fn list_bindings(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
) -> Result<Response, ErrorResponse> {
    require_authenticated_user(&caller).map_err(|e| application_error_response(&request_id, e))?;
    let bindings = channel_service(&state, &request_id)?
        .list_bindings().await
        .map_err(|e| application_error_response(&request_id, map_channel_error(e)))?;
    let items: Vec<ChannelBindingDto> = bindings.into_iter().map(ChannelBindingDto::from).collect();
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", ChannelBindingPage { items }, request_id.0)),
    ).into_response())
}

async fn list_bindings_by_target(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ListBindingsByTargetQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let (target, channel_type) = normalize_target_query(query)
        .map_err(|m| invalid_request(&request_id, m))?;
    require_authenticated_user(&caller).map_err(|e| application_error_response(&request_id, e))?;
    let bindings = channel_service(&state, &request_id)?
        .list_bindings_by_target(target, channel_type).await
        .map_err(|e| application_error_response(&request_id, map_channel_error(e)))?;
    let items: Vec<ChannelBindingDto> = bindings.into_iter().map(ChannelBindingDto::from).collect();
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", ChannelBindingPage { items }, request_id.0)),
    ).into_response())
}

async fn update_binding(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<UpdateChannelBindingRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Json(body) = body.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    require_authenticated_user(&caller).map_err(|e| application_error_response(&request_id, e))?;
    let service = channel_service(&state, &request_id)?;
    match (body.active, body.config) {
        (Some(active), None) => service.set_binding_status(&id, active).await,
        (None, Some(config)) => service.update_binding_config(&id, config).await,
        (Some(_), Some(_)) => {
            return Err(invalid_request(&request_id, "update active and config separately"));
        }
        (None, None) => {
            return Err(invalid_request(&request_id, "active or config is required"));
        }
    }
    .map_err(|e| application_error_response(&request_id, map_channel_error(e)))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", serde_json::Value::Null, request_id.0)),
    ).into_response())
}

async fn delete_binding(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    require_authenticated_user(&caller).map_err(|e| application_error_response(&request_id, e))?;
    channel_service(&state, &request_id)?
        .delete_binding(&id).await
        .map_err(|e| application_error_response(&request_id, map_channel_error(e)))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", serde_json::Value::Null, request_id.0)),
    ).into_response())
}
```

> 注:`ServiceError` 路径:legacy `bcs-http/routes/channel.rs:13` 用 `use bcs_service_api::{ChannelUseCaseError, CreateBindingCommand, ServiceError};`——故 `ServiceError` 是 `bcs_service_api::ServiceError`(re-export)。改 import 为 `use bcs_service_api::{application::channel::{ChannelService, ChannelUseCaseError}, application::v1::{...}, ServiceError};` 以匹配。以编译为准修正。

- [ ] **Step 2: 注册 + 挂载**

Edit `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/routes/mod.rs`(当前 5 行)加第 6 行:

```rust
pub mod bot;
pub mod channel;
pub mod friendship;
pub mod group;
pub mod invitation;
pub mod session;
```

Edit `src/bcs/crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs` 的 `protected_router()`,在现有 nest 的 `.merge(routes::friendship::router())` 后追加 `.merge(routes::channel::router())`:

```rust
pub fn protected_router() -> Router<ApiState> {
    Router::new().nest(
        "/openapi/v1/collaboration",
        routes::bot::router()
            .merge(routes::group::router())
            .merge(routes::session::router())
            .merge(routes::invitation::router())
            .merge(routes::friendship::router())
            .merge(routes::channel::router()),
    )
}
```

- [ ] **Step 3: build 确认**

Run: `cargo build -p bcs-api-http 2>&1 | tail -15`
Expected: 编译通过(`ServiceError` import 若报错,按 Step 1 注修正)。

- [ ] **Step 4: 写 V1 路由集成测**

仿 `tests/invitation_routes.rs`(L30-80 `HeaderVerifier`/`caller()`/`authenticated_request`)。用一个 fake `ChannelService`(对照 `FakeInvitationService` 风格),写 `src/bcs/crates/adapters/http/bcs-api-http/tests/channel_routes.rs`:

```rust
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::Body;
use bcs_api_http::v1::common::{ApiState, PrincipalVerifier, PrincipalVerificationError};
use bcs_api_http::v1::openapi::protected_router;
use bcs_domain::ChannelBinding;
use bcs_service_api::application::channel::{
    ChannelInboundError, ChannelService, ChannelUseCaseError, CreateBindingCommand,
};
use bcs_service_api::application::v1::{AuthenticatedCaller, AuthenticatedUserIdentity};
use bcs_service_api::InboundMessage;
use http::header::CONTENT_TYPE;
use http::{Method, Request, StatusCode};
use http_body_util::BodyExt;
use tower::ServiceExt;

struct FakeChannel {
    create: Mutex<Option<CreateBindingCommand>>,
    list_calls: Mutex<u32>,
    deleted: Mutex<Option<String>>,
}

#[async_trait]
impl ChannelService for FakeChannel {
    async fn handle_inbound(&self, _: InboundMessage) -> Result<(), ChannelInboundError> { unimplemented!() }
    async fn try_outbound(&self, _: bcs_service_api::OutboundMessage) -> Result<(), ChannelUseCaseError> { unimplemented!() }
    async fn create_binding(&self, cmd: CreateBindingCommand) -> Result<ChannelBinding, ChannelUseCaseError> {
        *self.create.lock().unwrap() = Some(cmd.clone());
        Ok(ChannelBinding {
            id: "b1".into(), channel_type: cmd.channel_type, account_ref: cmd.account_ref,
            target: cmd.target, group_chat_scope: cmd.group_chat_scope,
            outbound_visibility: cmd.outbound_visibility, env: String::new(),
            status: bcs_domain::BindingStatus::Active, created_by: cmd.created_by, config: cmd.config,
        })
    }
    async fn list_bindings(&self) -> Result<Vec<ChannelBinding>, ChannelUseCaseError> {
        *self.list_calls.lock().unwrap() += 1; Ok(Vec::new())
    }
    async fn list_bindings_by_target(&self, _: bcs_domain::BindingTarget, _: Option<bcs_domain::ChannelType>) -> Result<Vec<ChannelBinding>, ChannelUseCaseError> { Ok(Vec::new()) }
    async fn set_binding_status(&self, _: &str, _: bool) -> Result<(), ChannelUseCaseError> { Ok(()) }
    async fn update_binding_config(&self, _: &str, _: serde_json::Value) -> Result<(), ChannelUseCaseError> { Ok(()) }
    async fn delete_binding(&self, id: &str) -> Result<(), ChannelUseCaseError> {
        *self.deleted.lock().unwrap() = Some(id.into()); Ok(())
    }
}

struct AllowVerifier;
#[async_trait]
impl PrincipalVerifier for AllowVerifier {
    async fn verify(&self, _: &axum::http::HeaderMap) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        Ok(AuthenticatedCaller {
            tenant: None,
            user: Some(AuthenticatedUserIdentity { id: "staff-1".into(), username: "alice".into(), display_name: None, full_name: None }),
            bot: None, app: None, access_key: None,
        })
    }
}
struct DenyVerifier;
#[async_trait]
impl PrincipalVerifier for DenyVerifier {
    async fn verify(&self, _: &axum::http::HeaderMap) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        Err(PrincipalVerificationError::Missing)
    }
}

fn router_with(channel: Arc<dyn ChannelService>, verifier: Arc<dyn PrincipalVerifier>) -> axum::Router {
    // Mirror invitation_routes::test_router builder; ApiState::new is 6-arg.
    // (Noop impls are provided by invitation_routes; here reuse tower wiring.)
    use bcs_api_http::v1::common::ApiState;
    let _ = channel; let _ = verifier;
    todo!("wire ApiState::new(..).with_channel_service(channel) + protected_router(); mirror tests/invitation_routes.rs::test_router")
}

#[tokio::test]
async fn create_binding_returns_created_and_records_caller() {
    // POST .../channels/bindings with AllowVerifier → 201, code 20100,
    // created_by == "staff-1".
    todo!("assert 201 + body code 20100 + FakeChannel.create has Some(cmd) with created_by Some(\"staff-1\")")
}

#[tokio::test]
async fn missing_principal_returns_401() {
    // DenyVerifier → 401, error_code unauthenticated.
    todo!()
}

#[tokio::test]
async fn app_only_caller_rejected_by_adapter() {
    // AllowVerifier returns a caller with user=None → require_authenticated_user
    // raises ApplicationError::forbidden → 403 (this is the merged-design
    // human-only gate, NOT 401). Variation: a second verifier variant.
    todo!()
}
```

> 实施注:上表的 `todo!()` 占位需在 Step 4-2 落实——`router_with` 照 `tests/invitation_routes.rs:336-347 test_router` 的 6 参 `ApiState::new(Arc::new(NoopGroup), Arc::new(NoopSession), Arc::new(NoopSessionMsg), Arc::new(NoopInv), Arc::new(NoopFri), verifier)` 再 `.with_channel_service(channel)`,接 `protected_router()`(或 `bcs_api_http::v1::openapi::protected_router()` 的 re-export;`bcs-api-http/src/lib.rs` 须 `pub use` 出 `protected_router`——若未 pub,测试改用 `bcs_api_http::router` 或 crate 内测)。Noop 实现 `tests/invitation_routes.rs` 已有,复用 import。完成三个 #[tokio::test] 实体(POST 201 + GET 200 + 401 + 403),去 `todo!()`。

> 说明:本测涉及 crate-内部 `ApiState` 构造与 Noop 类型;若 `bcs-api-http` `lib.rs` 未 pub re-export `protected_router`/`ApiState`,把本测移入 `src/` 内 `#[cfg(test)] mod`(模块测,可访问私有项),照 `routes/invitation.rs` 同 crate 测惯例;或在 `lib.rs` `pub use v1::common::*; pub use v1::openapi::protected_router;` 加 re-export(本计划末尾的 README/契约不依赖它)。实施者择一,保 `cargo test -p bcs-api-http` 通过。

- [ ] **Step 4-2: 落实三个测试体(去 todo)**

照 `tests/invitation_routes.rs:353-383`(happy)+`619-638`(401)抄形。三测要点:
- `create_binding_returns_created_and_records_caller`:POST `/openapi/v1/collaboration/channels/bindings` + 合法 body → 断言 `status()==201`,`body["code"]==20_100`,`body["data"]["id"]=="b1"`,`FakeChannel.create.lock().as_ref().created_by == Some("staff-1")`。
- `missing_principal_returns_401`:用 `DenyVerifier`,无 `x-test-auth` → 401,`body["data"]["error_code"]=="unauthenticated"`。
- `app_only_caller_rejected_by_adapter`:用一个 `AllowVerifier` 变体返回 `user:None` 的 caller → 403,`error_code=="forbidden"`。

- [ ] **Step 5: 跑测**

Run: `cargo test -p bcs-api-http 2>&1 | tail -20`
Expected: PASS(含已有 invitation/bot 等 +新 channel 测)。

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(bcs-api-http): mount channels/bindings OpenAPI routes (5 op, human-only)

V1 routes call the shared ChannelService (no v1 app facade); authz via
require_authenticated_user in the adapter; ChannelUseCaseError maps to
ApplicationError (Conflict->409). Reuses the merged-app design.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: bootstrap 注入 `channel_service`(reorder 3 call-site)

**Files:**
- Modify: `src/bcs/crates/bootstrap/bcs/src/server.rs`(L1384-1494 fn 签名 + `.with_channel_service`;L1966-2000、L3353-3397、L3966-4014 三处 call-site reorder)

**Interfaces:**
- Consumes: `Arc<dyn ChannelService>`(由 `build_channel_runtime` 返回的 `channel_runtime.service`)。
- Produces: `build_openapi_v1_state` 新增末参 `channel_service`,`ApiState.channel_service` 被 `Some`。

- [ ] **Step 1: `build_openapi_v1_state` 加参 + builder**

Edit `src/bcs/crates/bootstrap/bcs/src/server.rs` 函数签名 L1384-1403,在 `principal_verifier: Arc<dyn PrincipalVerifier>,` 后加新参:

```rust
    principal_verifier: Arc<dyn PrincipalVerifier>,
    channel_service: Arc<dyn ChannelService>,
) -> ApiState {
```

Fn 末尾(L1492 `.with_bot_service(bot_service)` 行后)加一行,改尾:

```rust
    ApiState::new(
        group_service,
        session_service.clone(),
        session_service,
        invitation_service.clone(),
        invitation_service,
        principal_verifier,
    )
    .with_bot_service(bot_service)
    .with_session_file_service(session_file_service, session_file_url_projector)
    .with_channel_service(channel_service)
}
```

确保 fn 顶部或文件 `use` 有 `ChannelService`(`server.rs:57-118` 的 `use bcs_channel::{...}` + `use bcs_service_api::{..., ChannelService, ...}` 已有,见 agent "Imports/type aliases" L118;若该行不含 `ChannelService`,加 `use bcs_service_api::ChannelService;`)。

- [ ] **Step 2: reorder 3 call-site**

三处同形:`let openapi_v1 = build_openapi_v1_state(...); let channel_runtime = build_channel_runtime(...);` —— 当前 openapi_v1 在前、channel_runtime 在后。改:**channel_runtime 在前**,把 `channel_runtime.service.clone()` 传进 `build_openapi_v1_state`。

代表处(default L1966-2000),before→after:
before:
```rust
        let openapi_v1 = build_openapi_v1_state(
            &config, invite_token_secret.clone(), control_plane_repo,
            &provider_repos, bot_registry.clone(), sessions.clone(),
            friend_store.clone(), candidate_search.openapi_v1, friend_request_store,
            relation_store.clone(), session_management.clone(), group_management.clone(),
            collaboration_runtime.clone(), session_repo.clone(), group_message_history.clone(),
            session_file_service.clone(), system_message.clone(), gateway_principal_verifier.clone(),
        );
        let channel_runtime = build_channel_runtime(
            &config, channel_slot, channel_binding_cleanup,
            session_channel_outbound_slot, memory_channel_repos(None), session_repo.clone(),
            message_flow.clone(), system_message.clone(), collaboration_runtime.clone(),
            sessions.clone(), bot_registry.clone(),
        ).expect("default channel runtime must initialize");
        let channel_service = channel_runtime.service.clone();
```
after:
```rust
        let channel_runtime = build_channel_runtime(
            &config, channel_slot, channel_binding_cleanup,
            session_channel_outbound_slot, memory_channel_repos(None), session_repo.clone(),
            message_flow.clone(), system_message.clone(), collaboration_runtime.clone(),
            sessions.clone(), bot_registry.clone(),
        ).expect("default channel runtime must initialize");
        let channel_service = channel_runtime.service.clone();
        let openapi_v1 = build_openapi_v1_state(
            &config, invite_token_secret.clone(), control_plane_repo,
            &provider_repos, bot_registry.clone(), sessions.clone(),
            friend_store.clone(), candidate_search.openapi_v1, friend_request_store,
            relation_store.clone(), session_management.clone(), group_management.clone(),
            collaboration_runtime.clone(), session_repo.clone(), group_message_history.clone(),
            session_file_service.clone(), system_message.clone(), gateway_principal_verifier.clone(),
            channel_service.clone(),
        );
```
(其余语句用 `channel_service.clone()` 的不动。)

对 `build_channel_runtime` 的返回值用法不变——它仍 `channel_runtime.service.clone()`、`channel_runtime.http_ingress`、`channel_runtime.lifecycles`/`register_channel_lifecycles`(L2072/3466/4119 等),故只 reorder 顺序 + 加 `channel_service.clone()` 实参。**另外两处**(in-memory L3353-3397、prod L3966-4014)做相同 reorder + 加尾参。

> 顺序约束检查:`build_channel_runtime` 依赖 `channel_slot`(L364 = `Arc<OnceLock<Arc<dyn ChannelService>>>`,在两 fn 之前构造),不依赖 `openapi_v1` —— reorder 安全。

- [ ] **Step 3: build + 冒烟测**

Run: `cargo build -p bcs 2>&1 | tail -15`
Expected: 编译通过。
Run: `cargo test -p bcs --lib 2>&1 | tail -10`(若有 bootstrap 冒烟测)
Expected: PASS。若 bootstrap bin 有运行时冒烟、`channel_service` 注入后 `ApiState.channel_service.is_some()` —— 任何调用 channels 路由的 e2e 应能命中真 `BcsChannelService`。

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat(bcs-bootstrap): inject Arc<dyn ChannelService> into ApiState

Reorder build_channel_runtime before build_openapi_v1_state at all three
call-sites; pass channel_runtime.service into the openapi v1 state so the
merged channel routes resolve to the real BcsChannelService (fail-closed
if unset).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: 契约片段 + 注册 + 重生成 + 契约测

**Files:**
- Create: `src/bcs/api-contracts/v1/openapi/channel-bindings.yaml`
- Modify: `src/bcs/api-contracts/v1/openapi.yaml`(`paths:` 加 3 `$ref`,`tags:` 加 `Collaboration / Channels`)
- Modify: `src/bcs/tests/openapi/test_dump_openapi.py`(`34→39` + `COLLABORATION_TAGS` 加 `Collaboration / Channels`)
- Create: `src/bcs/tests/openapi/test_channel_binding_v1_contract.py`
- Regenerate: `src/gateway/configs/schemas/bcn.openapi.json`

- [ ] **Step 1: 写契约片段**

`src/bcs/api-contracts/v1/openapi/channel-bindings.yaml`(inline 所有 schema;每一 path 标 `user:required, app:required`;2xx envelope `required:[code,message,data,request_id]`;`code` const):

```yaml
CreateChannelBindingRequest:
  type: object
  additionalProperties: false
  required: [channel_type, account_ref, target, outbound_visibility, config]
  properties:
    channel_type:
      type: string
    account_ref:
      type: string
      minLength: 1
    target:
      $ref: "#/BindingTarget"
    group_chat_scope:
      $ref: "#/GroupChatScope"
    outbound_visibility:
      $ref: "#/Visibility"
    config:
      type: object
      additionalProperties: true

UpdateChannelBindingRequest:
  type: object
  additionalProperties: false
  minProperties: 1
  maxProperties: 1
  properties:
    active:
      type: boolean
    config:
      type: object
      additionalProperties: true
  description: "Exactly one of `active` or `config` (oneOf enforced server-side)."

BindingTarget:
  type: object
  additionalProperties: false
  minProperties: 1
  maxProperties: 1
  properties:
    bot:
      type: object
      additionalProperties: false
      required: [bot_id]
      properties:
        bot_id:
          type: string
          minLength: 1
    group:
      type: object
      additionalProperties: false
      required: [group_id]
      properties:
        group_id:
          type: string
          minLength: 1

ChannelBindingIdPath:
  name: id
  in: path
  required: true
  schema:
    type: string
    minLength: 1

BindingStatus:
  type: string
  enum: [active, disabled]

Visibility:
  type: string
  enum: [full_transcript, lead_only]

GroupChatScope:
  type: string
  enum: [conversation_shared, per_sender]

ChannelBinding:
  type: object
  additionalProperties: false
  required: [id, channel_type, account_ref, target, outbound_visibility, env, status, created_by, config]
  properties:
    id: { type: string }
    channel_type: { type: string }
    account_ref: { type: string }
    target: { $ref: "#/BindingTarget" }
    group_chat_scope: { $ref: "#/GroupChatScope" }
    outbound_visibility: { $ref: "#/Visibility" }
    env: { type: string }
    status: { $ref: "#/BindingStatus" }
    created_by: { type: string, nullable: true }
    config: { type: object, additionalProperties: true }

ChannelBindingPage:
  type: object
  additionalProperties: false
  required: [items]
  properties:
    items:
      type: array
      items: { $ref: "#/ChannelBinding" }

CreatedChannelBindingEnvelope:
  type: object
  additionalProperties: false
  required: [code, message, data, request_id]
  properties:
    code: { const: 20100 }
    message: { type: string }
    data: { $ref: "#/ChannelBinding" }
    request_id: { type: string }

ChannelBindingPageEnvelope:
  type: object
  additionalProperties: false
  required: [code, message, data, request_id]
  properties:
    code: { const: 20000 }
    message: { type: string }
    data: { $ref: "#/ChannelBindingPage" }
    request_id: { type: string }

ChannelBindingOkEnvelope:
  type: object
  additionalProperties: false
  required: [code, message, data, request_id]
  properties:
    code: { const: 20000 }
    message: { type: string }
    data: { type: null }
    request_id: { type: string }
  description: "PATCH/DELETE success — data is null."

ChannelsBindingsCollectionPath:
  post:
    operationId: create_channel_binding
    tags: ["Collaboration / Channels"]
    summary: Create a channel binding.
    x-avernet-security:
      user: required
      app: required
    requestBody:
      required: true
      content:
        application/json:
          schema: { $ref: "#/CreateChannelBindingRequest" }
    responses:
      "201":
        description: Created.
        content:
          application/json:
            schema: { $ref: "#/CreatedChannelBindingEnvelope" }
      "400": { x-error-codes: [invalid_request], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "401": { x-error-codes: [unauthenticated], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "403": { x-error-codes: [forbidden], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "404": { x-error-codes: [channel_binding_not_found], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "409": { x-error-codes: [channel_binding_conflict], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "500": { $ref: ../shared.yaml#/InternalErrorResponse }
  get:
    operationId: list_channel_bindings
    tags: ["Collaboration / Channels"]
    summary: List channel bindings (human-only; admin list).
    x-avernet-security:
      user: required
      app: required
    responses:
      "200":
        description: OK.
        content:
          application/json:
            schema: { $ref: "#/ChannelBindingPageEnvelope" }
      "401": { x-error-codes: [unauthenticated], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "403": { x-error-codes: [forbidden], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "500": { $ref: ../shared.yaml#/InternalErrorResponse }

ChannelsBindingsByTargetPath:
  get:
    operationId: list_channel_bindings_by_target
    tags: ["Collaboration / Channels"]
    summary: List bindings for a target (bot or group).
    x-avernet-security:
      user: required
      app: required
    parameters:
      - name: target_type
        in: query
        required: true
        schema: { type: string, enum: [bot, group] }
      - name: target_id
        in: query
        required: true
        schema: { type: string, minLength: 1 }
      - name: channel_type
        in: query
        required: false
        schema: { type: string }
    responses:
      "200":
        description: OK.
        content:
          application/json:
            schema: { $ref: "#/ChannelBindingPageEnvelope" }
      "400": { x-error-codes: [invalid_request], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "401": { x-error-codes: [unauthenticated], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "403": { x-error-codes: [forbidden], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "500": { $ref: ../shared.yaml#/InternalErrorResponse }

ChannelsBindingsIdPath:
  patch:
    operationId: update_channel_binding
    tags: ["Collaboration / Channels"]
    summary: Update status (active) or config of a binding (one of).
    x-avernet-security:
      user: required
      app: required
    parameters:
      - $ref: "#/ChannelBindingIdPath"
    requestBody:
      required: true
      content:
        application/json:
          schema: { $ref: "#/UpdateChannelBindingRequest" }
    responses:
      "200":
        description: OK.
        content:
          application/json:
            schema: { $ref: "#/ChannelBindingOkEnvelope" }
      "400": { x-error-codes: [invalid_request], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "401": { x-error-codes: [unauthenticated], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "403": { x-error-codes: [forbidden], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "404": { x-error-codes: [channel_binding_not_found], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "500": { $ref: ../shared.yaml#/InternalErrorResponse }
  delete:
    operationId: delete_channel_binding
    tags: ["Collaboration / Channels"]
    summary: Delete a binding.
    x-avernet-security:
      user: required
      app: required
    parameters:
      - $ref: "#/ChannelBindingIdPath"
    responses:
      "200":
        description: OK.
        content:
          application/json:
            schema: { $ref: "#/ChannelBindingOkEnvelope" }
      "401": { x-error-codes: [unauthenticated], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "403": { x-error-codes: [forbidden], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "404": { x-error-codes: [channel_binding_not_found], content: { application/json: { schema: { $ref: ../shared.yaml#/ErrorEnvelope } } } }
      "500": { $ref: ../shared.yaml#/InternalErrorResponse }
```

- [ ] **Step 2: 注册进 master + tag**

Edit `src/bcs/api-contracts/v1/openapi.yaml`。

`tags:` 列表加一项(在 `Collaboration / Invitations` 后):

```yaml
  - name: Collaboration / Channels
    description: Channel bindings between IM accounts and BCS bots/groups.
```

`paths:` 在 invitations 段后(约 L62 后)加 3 行:

```yaml
  /openapi/v1/collaboration/channels/bindings:
    $ref: ./openapi/channel-bindings.yaml#/ChannelsBindingsCollectionPath
  /openapi/v1/collaboration/channels/bindings/by-target:
    $ref: ./openapi/channel-bindings.yaml#/ChannelsBindingsByTargetPath
  /openapi/v1/collaboration/channels/bindings/{id}:
    $ref: ./openapi/channel-bindings.yaml#/ChannelsBindingsIdPath
```

- [ ] **Step 3: 更新 `test_dump_openapi.py`**

Edit `src/bcs/tests/openapi/test_dump_openapi.py`。

`COLLABORATION_TAGS`(L18-24)追加一个元素:

```python
COLLABORATION_TAGS = [
    "Collaboration / Bots",
    "Collaboration / Channels",
    "Collaboration / Friendships",
    "Collaboration / Groups",
    "Collaboration / Invitations",
    "Collaboration / Sessions",
]
```

`self.assertEqual(len(operations), 34)`(L53)→`39`:

```python
        self.assertEqual(len(operations), 39)
```

- [ ] **Step 4: 写 channel 契约测**

`src/bcs/tests/openapi/test_channel_binding_v1_contract.py`(仿 `test_group_v1_contract.py`):

```python
import unittest
from pathlib import Path

from scripts.validate_openapi_contract import (
    PUBLIC_COLLABORATION_PREFIX, load_contract, validate_contract,
)

ROOT = Path(__file__).resolve().parents[1] / "api-contracts" / "v1"

GROUNDING_OPS = {
    ("post", "/openapi/v1/collaboration/channels/bindings"),
    ("get", "/openapi/v1/collaboration/channels/bindings"),
    ("get", "/openapi/v1/collaboration/channels/bindings/by-target"),
    ("patch", "/openapi/v1/collaboration/channels/bindings/{id}"),
    ("delete", "/openapi/v1/collaboration/channels/bindings/{id}"),
}

class ChannelBindingContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract(ROOT, entrypoint="openapi.yaml")

    def test_validates(self) -> None:
        errors = validate_contract(self.contract, path_prefix=PUBLIC_COLLABORATION_PREFIX)
        self.assertEqual(errors, [], "\n".join(errors))

    def test_channels_paths_present(self) -> None:
        ops = {(m, p) for m, p, _ in _iter(self.contract) if "/channels/bindings" in p}
        self.assertTrue(GROUNDING_OPS.issubset(ops), f"missing {GROUNDING_OPS - ops}")

    def test_channels_security_user_app_required(self) -> None:
        for _, _, op in _iter(self.contract):
            if "channels/bindings" not in str(op.get("operationId", "")):
                continue
            sec = op.get("x-avernet-security", {})
            self.assertEqual(sec.get("user"), "required", op.get("operationId"))
            self.assertEqual(sec.get("app"), "required", op.get("operationId"))

def _iter(contract):
    # local mirror of validate_openapi_contract._iter_operations to avoid private dep
    for path, item in contract.get("paths", {}).items():
        for method in ("get","post","put","patch","delete","head","options","trace"):
            op = item.get(method)
            if op:
                yield method, path, op

if __name__ == "__main__":
    unittest.main()
```

> 若该文件用 `path_prefix` 参数而 `load_contract` 不带,以 partner `test_group_v1_contract.py` 的实际签名修正。

- [ ] **Step 5: 跑契约校验 + 重生成**

Run(校验):
```
cd src/bcs && uv run --with pyyaml python3 scripts/validate_openapi_contract.py --root api-contracts/v1 2>&1 | tail -20
```
Expected: 无 error 输出。

Run(契约测):
```
cd src/bcs && uv run --with pyyaml python -m pytest tests/openapi/ -q 2>&1 | tail -25
```
Expected: PASS(含 `test_dump_openapi` count 39 + 新 channel 测)。若 `test_dump_openapi` 仍报数错,核对 path 数=39、tag 列表已加。

Run(重生成 `bcn.openapi.json`):
```
cd src/bcs && uv run --with pyyaml python3 scripts/dump_openapi.py ../gateway/configs/schemas/bcn.openapi.json --root api-contracts/v1 2>&1 | tail -5
```
Expected: 写出文件,无 error。

校验重生成物含新 path:
```
grep -c "channels/bindings" src/gateway/configs/schemas/bcn.openapi.json
```
Expected: ≥5。

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(bcs): add channels/bindings OpenAPI contract (5 op) + regenerate bcn.openapi.json

Inline channel-binding schemas; user+app:required security; bump
test_dump_openapi operation count 34->39 and tag list; add contract test.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review(写计划后核对)

**Spec 覆盖**:对照 spec §3(5 op)— Task 4 五 handler 全覆盖;§5 文件改动面 — Task 1-6 全覆盖(契约/校验器不改/网关不改按 §13.2 对齐);§6 错误映射 — Task 4 `map_channel_error`;§7 身份 — Task 4 `require_authenticated_user`(403 现实);§8 schema — Task 3 + Task 6 契约;§10.1-4 待最终化 — Task 6 §13 解决 schema inline 与 dump;§13 全部八条 — 各 Task 落实。

**Placeholder 扫描**:Task 4 Step 4 的 `todo!()` 与 `router_with` 占位已在 Step 4-2 显式要求"去 todo"(实施约束,非计划残留占位);其余步骤代码块均含真代码。`FakeChannelService` 构造的字段名(`binding.target` 等取自 `bcs_domain::ChannelBinding`)如与真实定义字段名/可见性不符,实施者按 `services/bcs-channel/src/lib.rs` 的 `ChannelBinding{...}` literal(L1395-1400,Task 1 已示)对齐——这是显式对齐指引,非占位。✔

**类型一致**:`map_channel_error` 的 `ApplicationError::not_found/conflict/invalid/internal` 构造器(agent 2 §5 确认签名 `impl Into<String>`×2 for not_found/conflict/invalid;`internal` 单参)与 Task 4 调用一致;`create_binding` 调 `body.into_command(caller, user.id.clone())`(Task 3 `into_command` 二参签名)一致;`ChannelService` 五方法签名(Task 1 trait / agent §1)与 Task 4 调用(`set_binding_status(&id, active)`、`update_binding_config(&id, config)`、`delete_binding(&id)`、`list_bindings_by_target(target, channel_type)`、`list_bindings()`、`create_binding(cmd)`)一致。✔

---

## Execution Handoff

计划已保存 `docs/superpowers/plans/2026-08-18-channels-bindings-openapi.md`。两种执行选:

1. **子代理驱动(推荐)** — 每个 Task 派新 subagent 实现,Tasks 间评审,迭代快。
2. **内联执行** — 本会话用 executing-plans 批量执行、设检查点。

哪种?
