# API Gateway 认证与授权设计（面向第三方开发者）

**状态：** 草案 / 待评审
**日期：** 2026-07-21
**组件：** `src/gateway`（`gateway.community`，Python / FastAPI）
**范围：** 网关的认证（AuthN）与授权（AuthZ），本轮聚焦**第三方开发者**接入。
**关联约束：** 仓库架构宪法 `docs/arch/arch.rules.md`（Rule 1 / 3 / 7 / 14 / 19 / 25）。

---

## 1. 背景与目标

新增 **API Gateway** 组件统一承接入站请求，认证后转发给后端组件（`backend` / `bcs` / `engine` / `baas`）。
这批 API 除了服务自有前端，还要开放给**第三方开发者**——由**第三方的服务器代表它自己的终端用户**来调用。
因此认证要能区分并承载两类主体：

- **第三方 App 自身**（App 在做自己的事）；
- **第三方 App 代表某个终端用户**（App 替一个用户办事）。

本设计要落地：

1. 认证在网关统一完成，产出**一个中立鉴权对象**转发给下游，各组件按需消费。
2. 社区版与企业版采用不同认证实现（社区 = 开源无后端；企业 = BUService）。
3. 第三方"代表其用户"时身份如何传递与解析（含 `xoneid` token 转发）。

### 设计原则（沿用现有微内核架构）

- **契约即权威**（Rule 1）：每个 API 的鉴权要求写在它自己的 spec 里，网关只执行，不内嵌 per-API 分支。
- **交付层薄、核心与传输无关**（Rule 7）：网关只**翻译**鉴权上下文，不拥有领域策略；资源级授权留组件 core。
- **配置驱动装配、mode 不下沉**（Rule 14）：社区/企业差异由运行时按 flavor 绑定，不出现在策略/核心代码里。
- **两例之后再抽象**（Rule 19）：认证策略集保持封闭且小，只有出现全新"凭证→身份"路径时才新增。

---

## 2. 现状盘点（本设计长在已有代码之上）

### 2.1 `src/gateway` 组件既有约定（必须对齐）

| 约定 | 位置 | 说明 |
| --- | --- | --- |
| **SPI（协议）** | `gateway/community/spi/<能力>/` | `_protocols.py` 协议、`_models.py` 模型、`_errors.py` 异常 |
| **Plugin（实现）** | `gateway/community/plugins/<能力>/<flavor>/` | 一个能力多份实现，按 flavor 分目录 |
| **flavor 选择** | `PluginAccessor` + `GATEWAY_RUN_MODE` 环境变量 | `bare`（默认，开源）/ `sofa`（企业，注册 entry point） |
| **已有身份模型** | `spi/auth/_models.py::AuthUser` | 工号/花名/租户等，形状同 backend `AuthenticatedIdentity` |
| **已有 auth 协议骨架** | `spi/auth/_protocols.py::AuthPlugin` | `get_login_user` / `is_allowed` / `check_permission`，第一方为主 |
| **社区/企业实现样板** | `plugins/auth/bare/_plugin.py::BareAuthPlugin` | 返回硬编码用户；企业版将是 `plugins/auth/sofa/`（BUService） |

> **术语对齐：** 前几轮讨论里的 "community / corp" 在本组件里就是 flavor **`bare` / `sofa`**。下文统一用 `bare`/`sofa`。

### 2.2 其它组件可复用能力

| 能力 | 位置 | 复用方式 |
| --- | --- | --- |
| 第三方 API-Key 校验（含 policy 白名单） | `baas .../api/api_gateway/`（`_key_validator.py` 等） | 网关 `app_key` 策略经此校验 API Key |
| baas open API server（现有第三方入口） | `baas .../adapters/web/routers/open_api/` | 上提到网关；见 §12 |
| 身份边界转换器样板 | `backend .../adapters/http/auth/dependencies.py::_to_authenticated_user` | 各组件"Principal→域DTO"投影样板 |
| Caller 身份换取（subject + owner 委托凭证，只落 BaaS） | `backend .../core/caller_identity/service.py` | 第三方 OBO 换取复用 |
| 内容放行 gate | `engine .../plugin_api/auth_gate/` | 资源/内容级授权，留组件侧 |

**结论：** 网关不是另起一套鉴权，而是把"中立身份 + 边界转换"的既有模式**上提为网关统一产出、多组件各自投影**，
并在其上新增"第三方 App / 委托"这一层。

---

## 3. 分层与两个正交的轴

- **轴 A —— 如何"构建" Principal**（哪种凭证 → 哪种身份）。**归网关。** §4~§8。
- **轴 B —— 如何"消费" Principal**（投影成各组件域 DTO）。**归各组件自持。** §9。

> 网关负责"产出 + 签名 + 转发一个规范化 `Principal`"；组件负责"验签 + 投影成自己的域对象"。
> 网关不认识任何组件的域模型；组件不接触任何第三方原始凭证。

---

## 4. 身份与领域模型（全部类型）

放在 `gateway/community/spi/authn/_models.py`（与既有 `spi/auth/` 并列的新 SPI；`AuthUser` 仍从 `spi/auth` 复用）。

### 4.1 已有：`AuthUser` —— 已验证的**终端用户**身份

复用 `spi/auth/_models.py::AuthUser`。语义：**我们已在自己身份体系里认证过的一个真人**
（工号 `staffId` 为规范句柄，`tenantId` 为租户）。它是 `Principal.subject` 的类型。

### 4.2 新增：`ThirdPartyApp` —— 第三方**应用**身份（调用程序本身）

```python
from pydantic import BaseModel

class ThirdPartyApp(BaseModel):
    """A registered third-party developer application — the calling program itself."""
    client_id: str            # 注册时下发的应用 id（对应 baas api-key record 的 app_id）
    developer_org_id: str      # 拥有该 App 的开发者 / 组织；资源归属兜底主体
    app_type: str = "UNKNOWN"  # 来自 api-key record
    tenant_id: str | None = None
```

### 4.3 新增：`Principal` —— 网关产出的**中立鉴权对象**（下游唯一投影源）

```python
from enum import StrEnum
from pydantic import BaseModel
from gateway.community.spi.auth import AuthUser

class PrincipalType(StrEnum):
    USER            = "user"            # 第一方登录用户（前端 / 人工 curl）
    THIRD_PARTY_APP = "third_party_app" # 第三方 App 以自身身份（无终端用户）
    DELEGATED       = "delegated"       # 第三方 App 代表某个终端用户

class Principal(BaseModel):
    """The single neutral auth object produced after gateway authentication.
    Every downstream API projects its own auth DTO from this."""
    type: PrincipalType
    scopes: frozenset[str] = frozenset()   # 调用方**已被授予**的权限（授权判定输入）
    tenant_id: str | None = None
    app: ThirdPartyApp | None = None       # THIRD_PARTY_APP / DELEGATED 时 present
    subject: AuthUser | None = None        # 仅"已验证的真人"时 present
    on_behalf_of_opaque: str | None = None # 第三方私有、**未经我们验证**的终端用户句柄
    auth_method: str = ""                  # 产出它的策略名（审计用）
```

**核心不变量（安全底线）：** `subject` 与 `on_behalf_of_opaque` **互斥**。

- `subject` present ⇒ 已验证是我们体系里的真人，**可用于跨界资源判定**。
- `on_behalf_of_opaque` present ⇒ 第三方自己的用户，对我们不透明，**只用于归属/配额/审计，绝不当已认证身份**。
- `T | None` 约束（仓库规则）：只有契约上确可缺省的字段用 `| None`；`type`/`scopes` 等必备项非可选。

### 4.4 新增：`CredentialBundle` —— 策略读取的请求快照（框架无关）

```python
from dataclasses import dataclass
from collections.abc import Mapping

@dataclass(frozen=True)
class CredentialBundle:
    """Framework-agnostic snapshot the adapter fills from the FastAPI Request."""
    headers: Mapping[str, str]
    cookies: Mapping[str, str]
    query: Mapping[str, str]
```

### 4.5 新增：策略参数 `StrategyParams` 与 `Delegation`

```python
from enum import StrEnum
from dataclasses import dataclass

class Delegation(StrEnum):
    REQUIRED  = "required"    # 必须带上终端用户
    OPTIONAL  = "optional"    # 带不带都行
    FORBIDDEN = "forbidden"   # 不许带终端用户（纯 App 调用）

@dataclass(frozen=True)
class StrategyParams:
    """Per-route parameters for one strategy — parsed from the API's `security` block."""
    scopes: frozenset[str] = frozenset()          # 该路由**要求**的权限（AND 子集校验）
    delegation: Delegation = Delegation.OPTIONAL
```

---

## 5. `AuthStrategy` 协议（构建 Principal 的方式）

放在 `gateway/community/spi/authn/_protocols.py`。每个**命名策略**是一种"凭证→身份"的构建方式。

```python
from typing import Protocol
from ._models import CredentialBundle, StrategyParams, Principal

class AuthStrategy(Protocol):
    name: str  # 稳定名字，API 的 `security` 按名字引用

    async def build(self, creds: CredentialBundle, params: StrategyParams) -> Principal | None:
        """Try to build a Principal from the request for THIS strategy.

        返回 None    → 本策略的凭证不在请求里（不适用），让下一个 OR 分支尝试。
        raise AuthError → 凭证在但非法（硬失败），不再回退。
        返回 Principal → 本策略认证成功（scope/delegation 由 runner 统一裁决）。
        """
        ...
```

> **`None` vs `raise` 是本设计最关键的实现语义**：`None` 让 OR 回退只在"凭证缺失"时发生；
> 非法凭证一律 `raise`，避免把一个坏 token 悄悄放过去试别的分支。

策略集**封闭且小**（Rule 19）。当前四个：

| strategy | 读什么凭证 | 产出 |
| --- | --- | --- |
| `first_party_user` | 会话 cookie（OIDC/IAM） | `USER`，`subject` 填满 |
| `app_key` | `Authorization: Bearer <api_key>` | `THIRD_PARTY_APP`，`subject=None` |
| `app_key_delegated` | api_key + `xoneid` header | `DELEGATED`（或退化 `THIRD_PARTY_APP`） |
| `oauth_bearer` | OAuth access token | `USER` / `DELEGATED`（社区 3-legged，见 §10） |

---

## 6. 策略实现（全部类型）

策略本身**与 flavor 无关**；社区/企业差异下沉到策略依赖的**底层 port**（Rule 14）。

### 6.1 底层 port（策略的依赖）

```python
# gateway/community/spi/authn/_ports.py
from typing import Protocol
from dataclasses import dataclass
from gateway.community.spi.auth import AuthUser, AuthPlugin  # AuthPlugin 已存在

@dataclass(frozen=True)
class ApiKeyRecord:
    client_id: str            # baas app_id
    developer_org_id: str
    app_type: str
    tenant: str | None
    scopes: frozenset[str]    # 由 api-key 的 policy 推导

class ApiKeyValidator(Protocol):
    async def verify(self, api_key: str) -> ApiKeyRecord | None:
        """校验第三方 API Key；无效返回 None。由 baas api_gateway 校验支撑。"""
        ...

class SubjectTokenResolver(Protocol):
    async def resolve(self, token: str) -> AuthUser:
        """把转发来的用户令牌（xoneid）解析成已验证用户；非法 raise AuthError。"""
        ...
```

- `AuthPlugin`（已存在）：`first_party_user` 用它解析登录用户。`bare` 返回硬编码用户；`sofa` 调 BUService。
- `ApiKeyValidator`：`bare` 走本地/内存 key；`sofa` 走 baas `api_gateway` 的 `APIKeyValidator.verify`。
- `SubjectTokenResolver`：`bare` 不支持（或开发桩）；`sofa` 用 BUService SDK 解析 `xoneid`。

### 6.2 `first_party_user`

```python
# gateway/community/plugins/authn/first_party_user/_strategy.py
from gateway.community.spi.auth import AuthPlugin, AuthError
from gateway.community.spi.authn import (
    AuthStrategy, CredentialBundle, StrategyParams, Principal, PrincipalType, Delegation,
)

_SESSION_COOKIES = ("IAM_TOKEN", "SSO_TOKEN", "access_token")

class FirstPartyUserStrategy(AuthStrategy):
    name = "first_party_user"

    def __init__(self, auth: AuthPlugin) -> None:
        self._auth = auth

    async def build(self, creds: CredentialBundle, params: StrategyParams) -> Principal | None:
        if not any(k in creds.cookies for k in _SESSION_COOKIES):
            return None                                   # 无第一方凭证 → 不适用
        if params.delegation is Delegation.FORBIDDEN:
            raise AuthError("route forbids a user identity but a session cookie is present")
        cookie = creds.headers.get("cookie", "")
        user = await self._auth.get_login_user(           # 非法 → AuthPlugin 抛 AuthError
            cookie=cookie, referer=creds.headers.get("referer"),
        )
        granted = _first_party_scopes(self._auth, user)   # 由权限插件推导；见注
        return Principal(
            type=PrincipalType.USER, subject=user, tenant_id=user.tenantId,
            scopes=granted, auth_method=self.name,
        )
```

> 第一方用户的**已授予 scope** 由权限系统推导（`AuthPlugin.check_permission` / 白名单）；此处以 `_first_party_scopes`
> 占位。`scopes` 的**要求**校验统一在 §7 runner 里做（`required ⊆ granted`）。

### 6.3 `app_key`

```python
# gateway/community/plugins/authn/app_key/_strategy.py
class AppKeyStrategy(AuthStrategy):
    name = "app_key"

    def __init__(self, keys: ApiKeyValidator) -> None:
        self._keys = keys

    async def build(self, creds, params):
        api_key = _bearer(creds.headers.get("authorization"))
        if not api_key:
            return None                                   # 无 api key → 不适用
        record = await self._keys.verify(api_key)
        if record is None:
            raise AuthError("invalid api key")            # 凭证在但非法 → 硬失败
        if params.delegation is Delegation.REQUIRED:
            raise AuthError("route requires an end-user; app_key carries none")
        app = ThirdPartyApp(
            client_id=record.client_id, developer_org_id=record.developer_org_id,
            app_type=record.app_type, tenant_id=record.tenant,
        )
        return Principal(
            type=PrincipalType.THIRD_PARTY_APP, app=app, tenant_id=record.tenant,
            scopes=record.scopes, auth_method=self.name,
        )
```

### 6.4 `app_key_delegated`（第三方代表用户；`xoneid` 转发）

```python
# gateway/community/plugins/authn/app_key_delegated/_strategy.py
class AppKeyDelegatedStrategy(AuthStrategy):
    name = "app_key_delegated"

    def __init__(self, keys: ApiKeyValidator, resolver: SubjectTokenResolver) -> None:
        self._keys, self._resolver = keys, resolver

    async def build(self, creds, params):
        api_key = _bearer(creds.headers.get("authorization"))
        if not api_key:
            return None                                   # 无 App 凭证 → 不适用
        record = await self._keys.verify(api_key)
        if record is None:
            raise AuthError("invalid api key")
        app = ThirdPartyApp(
            client_id=record.client_id, developer_org_id=record.developer_org_id,
            app_type=record.app_type, tenant_id=record.tenant,
        )
        # 用户维度：partner 显式转发的 xoneid（可选，视路由 delegation 要求）
        xoneid = creds.headers.get("xoneid")
        subject = await self._resolver.resolve(xoneid) if xoneid else None  # 非法 → raise

        if params.delegation is Delegation.REQUIRED and subject is None:
            raise AuthError("route requires an end-user; no xoneid forwarded")
        if params.delegation is Delegation.FORBIDDEN and subject is not None:
            raise AuthError("route forbids an end-user identity")

        return Principal(
            type=PrincipalType.DELEGATED if subject else PrincipalType.THIRD_PARTY_APP,
            app=app, subject=subject, tenant_id=record.tenant,
            scopes=record.scopes, auth_method=self.name,
        )
```

### 6.5 底层 port 的 flavor 实现（社区 vs 企业）

```python
# gateway/community/plugins/authn/subject_resolver/bare/_plugin.py
class BareSubjectTokenResolver(SubjectTokenResolver):
    async def resolve(self, token: str) -> AuthUser:
        raise AuthError("subject-token resolution unavailable in bare mode")

# 企业包：gateway/enterprise/plugins/authn/subject_resolver/sofa/_plugin.py
class SofaSubjectTokenResolver(SubjectTokenResolver):
    def __init__(self, buservice_sdk) -> None:
        self._sdk = buservice_sdk
    async def resolve(self, token: str) -> AuthUser:
        info = self._sdk.resolve_xoneid(token)            # BUService SDK；非法抛错
        return AuthUser(
            id=info.id, operatorName=info.operator_name, staffId=info.staff_no,
            nickName=info.nick_name, realName=info.real_name, tenantId=info.tenant_id,
        )
```

企业版通过 `register_plugin_option(...)`（`plugin_registry.py`）在 import 期把 `sofa` 实现挂上，
`GATEWAY_RUN_MODE=sofa` 时 `PluginAccessor` 选中——沿用组件既有机制，策略代码零改动。

---

## 7. 网关运行器（执行 OR/AND + scope/delegation）

```python
# gateway/community/core/authn/_runner.py
async def resolve_principal(
    route_security: list[dict[str, StrategyParams]],   # 见 §8 的编译结果
    creds: CredentialBundle,
    registry: dict[str, AuthStrategy],
) -> Principal:
    last_err: AuthError | None = None
    for item in route_security:                        # 列表项之间 OR
        built: list[Principal] = []
        ok = True
        for name, params in item.items():              # 项内多 scheme AND
            try:
                p = await registry[name].build(creds, params)
            except AuthError as e:
                last_err, ok = e, False; break         # 凭证非法 → 本项失败
            if p is None:
                ok = False; break                      # 凭证缺失 → 本项不适用
            if not params.scopes <= p.scopes:          # 要求 ⊆ 已授予
                last_err, ok = AuthError("insufficient scope"), False; break
            built.append(p)
        if ok:
            return _merge(built)                        # 本项全通过 → 采纳
    raise last_err or AuthError("unauthorized")         # 无一通过：401/403
```

认证成功后，`Principal` 序列化为**签名内部头**（`X-Avernet-Principal` + `-Sig`，叠加内网 mTLS）转发下游；
下游组件的 auth 退化为"验签 + 反序列化"，不再自行对 OAuth/BUService 说话。

---

## 8. per-route 鉴权配置（简化版：单表 + 具体度覆盖）

> 采纳评审意见：**取消 `defaults`/`overrides` 二分。** 只有一张表，每条是一个 path（可带 method）模式；
> **更具体的规则覆盖更一般的规则**，最一般的 `"/**"` 就是顶层默认。

### 8.1 权威源：随各 API spec 声明（作者手写）

```yaml
# 挨着 endpoint 写在组件的 API spec 里
POST /v1/bots/{id}/chat:
  security:                 # 列表项之间 OR；项内多 scheme AND
    - app_key_delegated: { delegation: required, scopes: [bots:chat] }
    - first_party_user:  { scopes: [bots:chat] }

POST /v1/apps/self/usage:
  security:
    - app_key: { delegation: forbidden, scopes: [usage:read] }
```

### 8.2 网关消费视图：单张路由表（构建期聚合）

```yaml
# 一张表；键是 (可选 METHOD +) path 模式；更具体者优先
route_security:
  "/**":                              [ first_party_user ]                         # 顶层默认
  "POST /open_api/**":                [ app_key_delegated: { delegation: optional } ]
  "POST /open_api/v1/bots/{id}/chat": [ app_key_delegated: { delegation: required, scopes: [bots:chat] } ]
```

### 8.3 匹配规则（每次请求）

```
1. 取所有 pattern 命中当前 (method, path) 的规则
2. 选**最具体**的一条：
     - 带 method 的胜过不带 method 的
     - 字面前缀更长 / 通配符更少者更具体（/open_api/v1/** 胜过 /open_api/**，胜过 /**）
3. 命中规则整条生效（不与更一般规则做字段合并——行为可预测）
4. 理论上 "/**" 兜底必命中；若刻意留空且无命中 → fail-closed 拒绝
```

- **单一默认**：`"/**"` 就是那条"顶层默认"，天然被更具体规则覆盖，无需单独的 defaults/overrides 概念。
- **整条覆盖，不合并**：命中 `/open_api/v1/bots/{id}/chat` 就只用它那份 `security`。
- **CI 门禁**（呼应 `docs/arch/ci.enforce.md`）：每条对第三方暴露的 route 必须能解析到一条 requirement，否则构建失败——防止 route 与鉴权声明漂移。

> **粒度直觉：** endpoint 粒度 = 策略粒度。要不同构建方式的两个 endpoint 本就是两条 route，天然分开；
> 策略相同的一批 endpoint 用一条前缀模式收敛。实际写起来是"一条顶层默认 + 少量更具体规则"。

---

## 9. 消费侧：Principal → 各组件域 DTO（各组件自持）

轴 B。**不做**网关中心化投影（否则网关要 import 每个组件的域类型，变 god-object，违反边界）。

- 网关只下发规范化、签名后的 `Principal`；对具体域一无所知。
- 每个组件在自己的 adapter 边界自持 `Principal → 本组件 DTO` 的窄转换器——与现有 `_to_authenticated_user()` 同一手法。
- 组件核心/路由**永不 import** 网关的 `Principal` 类（Rule 7；红线："路由不得直连非 Service-API 类型"）。

| 组件 | 它真正需要的域模型 |
| --- | --- |
| backend | `AuthenticatedUser`（staffId/tenantId/operatorName）——已存在 |
| engine  | `AuthGateService.verify(token, content, session_id)`——要 caller token + 幂等键 |
| bcs     | 多半只要 `tenant_id + scopes + principal_type` |
| baas    | `developer_org_id`（App 场景）或 `subject.staffId`（用户场景）作为 owner key |

---

## 10. 第三方"代表其用户"的委托模式

**注册时为每个 App 固定一种委托模式**，网关据此走不同策略。

```
待操作用户是"我们身份体系里的人"吗？
├─ 是，且第三方能转发用户令牌（xoneid）
│     → 模式 A：Token Resolve（strategy=app_key_delegated + xoneid）
│       partner 显式转发 xoneid → 网关 sofa SubjectTokenResolver 用 BUService SDK 解析 → subject
│       典型：企业内集成方、ISV 帮企业客户接入
├─ 是，但用户在浏览器、愿意授权（"用 Avernet 登录"）
│     → 模式 C：Authorization Code + PKCE（strategy=oauth_bearer）
│       用户在同意页授权 → 第三方拿 user 维度 token；第三方全程不碰 IAM token
└─ 否（第三方的用户对我们不透明）—— 默认
      → 模式 B：App 主体（strategy=app_key，或 app_key_delegated 且 delegation!=required）
        principal_type=THIRD_PARTY_APP；end-user 仅作不透明 on_behalf_of_opaque
        资源归属 = developer_org_id
```

**`xoneid` 链路与安全：** 浏览器带 `xoneid` → partner 显式转发 → 网关 sofa 用 SDK 解析出
entity_id/nickName/tenant → 填 `subject`。`xoneid` 属敏感凭证，网关一次性解析、**不回吐**、不落明文日志。
若下游 runtime 需要可持续的"代表用户"凭证，走现有 `CallerIdentityService.exchange_caller_identity()`
（subject + owner 预授权委托凭证换取 caller token，**只写 BaaS，绝不回吐 partner**——沿用 COSEC 约束）。
**前置确认：** BUService subject token 是否 sender-constrained（audience/mTLS/DPoP），决定 `xoneid` 透传边界。

---

## 11. 授权分层

- **网关（粗粒度，App/策略级）：** route 是否允许该策略；`scopes` 校验（§7 runner）；配额、限流、租户隔离、审计。
- **组件（细粒度，资源级）—— 留 core（Rule 7）：** "这个 principal 能否访问 bot X / entity Y"——
  已有 `AuthPlugin.authorize_entity_access()`、engine `AuthGateService.verify()`。
  **第三方必加防线：** App 模式（`on_behalf_of_opaque`）下 owner key **锚定 `developer_org_id`/`tenant`**，
  防止 A 开发者借一个 end-user id 去读 B 的资源。

---

## 12. 具体落点：baas open API server 的收编

`baas .../open_api/dependencies.py` 现状：API Key（Bearer）验出 `app_id/app_type/tenant/policy` +
`IAM_TOKEN` cookie（用户，可选），`policy.allowed_bots` 做 fail-closed 白名单。字段映射：

| 现状 | 目标 `Principal` |
| --- | --- |
| `app_id` / `app_type` / `tenant` | `app`（`ThirdPartyApp`）+ `tenant_id` |
| `policy.allowed_bots` | 网关粗粒度 scope / 资源白名单（保留，fail-closed） |
| `IAM_TOKEN` / `xoneid`（验证过） | `subject` |
| 第三方自有用户标识（未验证） | `on_behalf_of_opaque` |

**当前风险收口：** 现状用户来自 cookie、与 api_key 各走各、未把"该 user 属不属于此 App/tenant"绑死——
一个 App 若能任意塞用户标识即可越界读他人资源。收编后必须按 §11 防线：被代表 user 要么经模式 A 验证（`subject` 可信），
要么只能是 App 私有不透明句柄。收编后 baas 退成"验签 Principal → 投影成 `BotChatContext`"。

---

## 13. 落地路径（增量，不破坏现有 Rule）

1. 新增 `spi/authn/`：`Principal` / `ThirdPartyApp` / `AuthStrategy` / port 协议 + conformance test（Rule 25）。
2. **bare 先行**（单盒优先，Rule 20）：`app_key` 策略打通最小链路（API Key → 签名 Principal → baas 投影 → owner=org）。
3. 加 `app_key_delegated`（含 bare 桩 resolver）与 §7 runner、§8 路由表 + CI 门禁。
4. 企业包 `sofa`：`SofaAuthPlugin` + `SofaSubjectTokenResolver`（BUService/xoneid），经 `register_plugin_option` 挂载。
5. 逐组件补 `Principal → 域 DTO` 投影器；把 baas open_api 收编到网关。
6. 模式 C（`oauth_bearer` + 同意页）按需补。

---

## 14. 待拍板的开放问题

1. **网关↔组件信任**：内网 mTLS 是否足够，还是叠加 Principal 头应用层签名？（建议两者都要。）
2. **模式 B 资源归属粒度**：归 `developer_org_id` 还是 `client_id`？（同一开发者多 App 是否共享资源。）
3. **模式 A 适用范围**：是否只对企业自建集成开放，外部 ISV 一律禁走 xoneid 解析？（安全上建议是。）
4. **BUService subject token 是否 sender-constrained**：决定 `xoneid` 透传边界，需与 BUService 团队确认。
5. **`AuthStrategy` 归属**：作为独立 `spi/authn/`，还是并入现有 `spi/auth/`？（建议独立，避免与第一方 `AuthPlugin` 语义混淆。）

---

## 附录：术语

- **`AuthUser`**：已验证的终端用户身份（组件既有模型）。
- **`ThirdPartyApp`**：第三方应用身份（调用程序本身）。
- **`Principal`**：网关产出的中立鉴权对象，`subject`/`on_behalf_of_opaque` 互斥。
- **`AuthStrategy`**：网关侧"构建 Principal 的方式"的命名策略；`build()` 用 `None`/`raise` 区分"不适用"/"非法"。
- **flavor `bare` / `sofa`**：社区（开源无后端）/ 企业（BUService），由 `GATEWAY_RUN_MODE` 选择。
- **`xoneid`**：partner 从浏览器取得并显式转发的用户令牌 header，`sofa` 侧用 BUService SDK 解析为 `AuthUser`。
