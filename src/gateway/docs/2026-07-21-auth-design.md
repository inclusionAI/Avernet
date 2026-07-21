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
3. 第三方"代表其用户"时身份如何传递与解析（含 `xoneid` token 转发）。**本轮先搁置，见 §15。**

### 设计原则（沿用现有微内核架构）

- **契约即权威**（Rule 1）：每个 API 的鉴权要求写在它自己的 spec 里，网关只执行，不内嵌 per-API 分支。
- **交付层薄、核心与传输无关**（Rule 7）：网关只**翻译**鉴权上下文，不拥有领域策略；资源级授权留组件 core。
- **配置驱动装配、mode 不下沉**（Rule 14）：社区/企业差异由运行时按 flavor 绑定，不出现在策略/核心代码里。
- **两例之后再抽象**（Rule 19）：认证策略集保持封闭且小，只有出现全新"凭证→身份"路径时才新增。
- **非法状态不可表达**：`Principal` 用判别联合（discriminated union）建模，字段只在其成立的形态里出现，
  避免 `T | None` 依赖运行时校验（呼应 `CLAUDE.md`/`AGENTS.md` 对 `T | None` 的约束）。

---

## 2. 现状盘点（本设计长在已有代码之上）

### 2.1 `src/gateway` 组件既有约定（必须对齐）

| 约定 | 位置 | 说明 |
| --- | --- | --- |
| **SPI（协议）** | `gateway/community/spi/<能力>/` | `_protocols.py` 协议、`_models.py` 模型、`_errors.py` 异常 |
| **Plugin（实现）** | `gateway/community/plugins/<能力>/<flavor>/` | 一个能力多份实现，按 flavor 分目录 |
| **flavor 选择** | `PluginAccessor` + `GATEWAY_RUN_MODE` 环境变量 | `bare`（默认，开源）/ `sofa`（企业，注册 entry point） |
| **已有身份模型** | `spi/auth/_models.py::AuthenticatedUser` | 工号/花名/租户等，形状同 backend `AuthenticatedIdentity` |
| **已有 auth 协议骨架** | `spi/auth/_protocols.py::AuthPlugin` | `get_login_user` / `is_allowed` / `check_permission`，第一方为主 |
| **社区/企业实现样板** | `plugins/auth/bare/_plugin.py::BareAuthPlugin` | 返回硬编码用户；企业版将是 `plugins/auth/sofa/`（BUService） |

> **术语对齐：** 前几轮讨论里的 "community / corp" 在本组件里就是 flavor **`bare` / `sofa`**。下文统一用 `bare`/`sofa`。

### 2.2 其它组件可复用能力

| 能力 | 位置 | 复用方式 |
| --- | --- | --- |
| 第三方 API-Key 校验（含 policy 白名单） | `baas .../api/api_gateway/`（`_key_validator.py` 等） | 网关 `app_key` 策略经此校验 API Key |
| baas open API server（现有第三方入口） | `baas .../adapters/web/routers/open_api/` | 上提到网关；见 §12 |
| 身份边界转换器样板 | `backend .../adapters/http/auth/dependencies.py::_to_authenticated_user` | 各组件"Principal→域DTO"投影样板 |
| 内容放行 gate | `engine .../plugin_api/auth_gate/` | 资源/内容级授权，留组件侧 |

**结论：** 网关不是另起一套鉴权，而是把"中立身份 + 边界转换"的既有模式**上提为网关统一产出、多组件各自投影**，
并在其上新增"租户 + 第三方 App"这一层。

---

## 3. 分层与两个正交的轴

- **轴 A —— 如何"构建" Principal**（哪种凭证 → 哪种身份）。**归网关。** §4~§8。
- **轴 B —— 如何"消费" Principal**（投影成各组件域 DTO）。**归各组件自持。** §9。

> 网关负责"产出 + 签名 + 转发一个规范化 `Principal`"；组件负责"验签 + 投影成自己的域对象"。
> 网关不认识任何组件的域模型；组件不接触任何第三方原始凭证。

---

## 4. 身份与领域模型（全部类型）

放在 `gateway/community/spi/authn/_models.py`（与既有 `spi/auth/` 并列的新 SPI；`AuthenticatedUser` 仍从 `spi/auth` 复用）。

### 4.1 已有：`AuthenticatedUser` —— 已验证的**终端用户**身份

复用 `spi/auth/_models.py::AuthenticatedUser`。语义：**我们已在自己身份体系里认证过的一个真人**
（工号 `staffId` 为规范句柄）。它是 `UserPrincipal.subject` 的类型。

### 4.2 新增：`ThirdPartyApp` —— 第三方**应用**身份（调用程序本身）

```python
from pydantic import BaseModel

class ThirdPartyApp(BaseModel):
    """A registered third-party developer application — the calling program itself."""
    client_id: str            # 注册时下发的应用 id（对应 baas api-key record 的 app_id）
    developer_org_id: str      # 拥有该 App 的开发者 / 组织；资源归属兜底主体
    app_type: str = "UNKNOWN"  # 来自 api-key record
```

### 4.3 新增：`Principal` —— 判别联合（网关产出，下游唯一投影源）

**不再是"单结构 + 可空字段"，而是按 `type` 判别的联合。** 每个成员只带它成立时确实存在的字段——
`subject` / `app` 在各自成员里都是**必填非可选**，非法状态（如 USER 却带 `app`）无法构造。

```python
from typing import Annotated, Literal
from enum import StrEnum
from pydantic import BaseModel, Field
from gateway.community.spi.auth import AuthenticatedUser

class PrincipalType(StrEnum):
    USER            = "user"             # 第一方登录用户（前端 / 人工 curl）
    THIRD_PARTY_APP = "third_party_app"  # 第三方 App 以自身身份
    # DELEGATED（App 代表已验证真人）本轮搁置，见 §15

class UserPrincipal(BaseModel):
    type: Literal[PrincipalType.USER] = PrincipalType.USER
    tenant: str                          # 必填，来自租户令牌，见 §4.6
    scopes: frozenset[str] = frozenset()
    subject: AuthenticatedUser                    # 必填

class AppPrincipal(BaseModel):
    type: Literal[PrincipalType.THIRD_PARTY_APP] = PrincipalType.THIRD_PARTY_APP
    tenant: str                          # 必填
    scopes: frozenset[str] = frozenset()
    app: ThirdPartyApp                   # 必填
    on_behalf_of_opaque: str | None = None
    # ↑ App 替它自己的某个终端用户办事时的**不透明**句柄；对我们不透明、未经验证。
    #   None 是**有意的**缺省状态（App 纯以自身身份调用），符合 T|None 约束。
    #   仅用于归属 / 配额 / 审计，**绝不当作已认证身份做跨界资源判定**。

Principal = Annotated[UserPrincipal | AppPrincipal, Field(discriminator="type")]
```

**要点：**

- `tenant` 在两个成员里都是**必填**——每个 Principal 一定归属某个租户（来源见 §4.6）。
- pydantic 靠 `type` tag 干净地序列化/反序列化——正好用于网关签名内部头；下游按 `type` 做 `match` 投影。
- `on_behalf_of_opaque` 是唯一保留的 `| None`，且其 `None` 是**契约上真的可缺省**（App 未代表任何具体用户），
  不是"取决于别的字段"的隐式互斥——符合仓库对 `T | None` 的要求。

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
    OPTIONAL  = "optional"    # 带不带终端用户都行（当前默认）
    FORBIDDEN = "forbidden"   # 不许带终端用户（纯 App 调用）
    # REQUIRED（必须带已验证真人）随 §15 委托一起启用

@dataclass(frozen=True)
class StrategyParams:
    """Per-route parameters for one strategy — parsed from the API's `security` block."""
    scopes: frozenset[str] = frozenset()          # 该路由**要求**的权限（AND 子集校验）
    delegation: Delegation = Delegation.OPTIONAL
```

### 4.6 新增：租户令牌与 `tenant` 字段（本轮重点）

每个租户由网关签发**一个唯一的租户令牌**。调用方**每次请求都必须带上它**（如 `X-Tenant-Token` header）；
网关**先**校验该令牌、把它映射成租户 id，再进入身份策略。`tenant` 因此是每个 `Principal` 的必填字段。

- 它与"调用方身份"（用户 / App）**正交**：租户令牌回答"这是哪个租户的流量"，身份策略回答"这个租户里的谁在调"。
- 它是租户的**权威来源**。App 场景下 api-key record 里也带 tenant，网关须**交叉校验二者一致**，
  否则拒绝（防止一个 App 被挂到错误租户下调用）。
- 缺失或非法的租户令牌 = 直接 401（租户对所有 API-gateway 流量都必需）。

```python
# gateway/community/spi/authn/_ports.py
from typing import Protocol

class TenantResolver(Protocol):
    async def resolve(self, tenant_token: str) -> str:
        """Verify the per-tenant token and map it to a tenant id.
        Missing/invalid → raise AuthError."""
        ...
```

---

## 5. `AuthStrategy` 协议（构建 Principal 的方式）

放在 `gateway/community/spi/authn/_protocols.py`。租户已由 §4.6 解析好并作为 `tenant` 传入。

```python
from typing import Protocol
from ._models import CredentialBundle, StrategyParams, Principal

class AuthStrategy(Protocol):
    name: str  # 稳定名字，API 的 `security` 按名字引用

    async def build(
        self, creds: CredentialBundle, params: StrategyParams, tenant: str,
    ) -> Principal | None:
        """Try to build a Principal (for the given, already-verified tenant).

        返回 None    → 本策略的凭证不在请求里（不适用），让下一个 OR 分支尝试。
        raise AuthError → 凭证在但非法（硬失败），不再回退。
        返回 Principal → 本策略认证成功（scope/delegation 由 runner 统一裁决）。
        """
        ...
```

> **`None` vs `raise` 是本设计最关键的实现语义**：`None` 让 OR 回退只在"凭证缺失"时发生；
> 非法凭证一律 `raise`，避免把一个坏 token 悄悄放过去试别的分支。

策略集**封闭且小**（Rule 19）。本轮启用两个：

| strategy | 读什么凭证 | 产出 | 本轮 |
| --- | --- | --- | --- |
| `first_party_user` | 会话 cookie（OIDC/IAM） | `UserPrincipal` | ✅ |
| `app_key` | `Authorization: Bearer <api_key>` | `AppPrincipal` | ✅ |
| `app_key_delegated` | api_key + `xoneid` header | `DelegatedPrincipal` | ⏸ 见 §15 |
| `oauth_bearer` | OAuth access token（3-legged） | `UserPrincipal`/委托 | ⏸ 见 §15 |

---

## 6. 策略实现（本轮启用的类型）

策略本身**与 flavor 无关**；社区/企业差异下沉到策略依赖的**依赖协议（SPI）**（Rule 14）。

> "依赖协议（SPI）"就是一个策略**调用、但自己不实现**的协议（有方法、可按 flavor 替换实现），
> 与组件现有的 `AuthPlugin`（协议）+ `BareAuthPlugin`（实现）是同一手法。注意区分：`ApiKeyValidator` /
> `TenantResolver` 是**依赖协议**（有 `verify()`/`resolve()`）；`ApiKeyRecord` 只是它们返回的**数据类**，不是协议。

### 6.1 依赖协议（SPI，策略的依赖）

```python
# gateway/community/spi/authn/_ports.py
from typing import Protocol
from dataclasses import dataclass
from gateway.community.spi.auth import AuthenticatedUser, AuthPlugin  # AuthPlugin 已存在

@dataclass(frozen=True)
class ApiKeyRecord:
    client_id: str            # baas app_id
    developer_org_id: str
    app_type: str
    tenant: str               # 该 App 注册所属租户（用于与租户令牌交叉校验）
    scopes: frozenset[str]    # 由 api-key 的 policy 推导

class ApiKeyValidator(Protocol):
    async def verify(self, api_key: str) -> ApiKeyRecord | None:
        """校验第三方 API Key；无效返回 None。由 baas api_gateway 校验支撑。"""
        ...

# TenantResolver 见 §4.6
```

- `AuthPlugin`（已存在）：`first_party_user` 用它解析登录用户。`bare` 返回硬编码用户；`sofa` 调 BUService。
- `ApiKeyValidator`：`bare` 走本地/内存 key；`sofa` 走 baas `api_gateway` 的 `APIKeyValidator.verify`。
- `TenantResolver`：`bare` 把开发令牌映射到固定租户；`sofa` 查租户令牌注册表。

### 6.2 `first_party_user`

```python
# gateway/community/plugins/authn/first_party_user/_strategy.py
from gateway.community.spi.auth import AuthPlugin, AuthError
from gateway.community.spi.authn import (
    AuthStrategy, CredentialBundle, StrategyParams, UserPrincipal, Delegation,
)

_SESSION_COOKIES = ("IAM_TOKEN", "SSO_TOKEN", "access_token")

class FirstPartyUserStrategy(AuthStrategy):
    name = "first_party_user"

    def __init__(self, auth: AuthPlugin) -> None:
        self._auth = auth

    async def build(self, creds, params, tenant):
        if not any(k in creds.cookies for k in _SESSION_COOKIES):
            return None                                   # 无第一方凭证 → 不适用
        if params.delegation is Delegation.FORBIDDEN:
            raise AuthError("route forbids a user identity but a session cookie is present")
        user = await self._auth.get_login_user(           # 非法 → AuthPlugin 抛 AuthError
            cookie=creds.headers.get("cookie", ""), referer=creds.headers.get("referer"),
        )
        granted = _first_party_scopes(self._auth, user)   # 由权限插件推导；见注
        return UserPrincipal(tenant=tenant, subject=user, scopes=granted)
```

> 第一方用户的**已授予 scope** 由权限系统推导（`AuthPlugin.check_permission` / 白名单）；`scopes` 的**要求**
> 校验统一在 §7 runner 里做（`required ⊆ granted`）。

### 6.3 `app_key`（含租户交叉校验）

```python
# gateway/community/plugins/authn/app_key/_strategy.py
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    AuthStrategy, StrategyParams, AppPrincipal, ThirdPartyApp, Delegation,
)
from gateway.community.spi.authn._ports import ApiKeyValidator

class AppKeyStrategy(AuthStrategy):
    name = "app_key"

    def __init__(self, keys: ApiKeyValidator) -> None:
        self._keys = keys

    async def build(self, creds, params, tenant):
        api_key = _bearer(creds.headers.get("authorization"))
        if not api_key:
            return None                                   # 无 api key → 不适用
        record = await self._keys.verify(api_key)
        if record is None:
            raise AuthError("invalid api key")            # 凭证在但非法 → 硬失败
        if record.tenant != tenant:                       # 与租户令牌交叉校验
            raise AuthError("api key does not belong to the presented tenant")
        if params.delegation is Delegation.FORBIDDEN:
            # 纯 App 调用被显式允许；只有携带用户句柄时才拒（本轮 on_behalf_of 由 header 提供）
            pass
        app = ThirdPartyApp(
            client_id=record.client_id, developer_org_id=record.developer_org_id,
            app_type=record.app_type,
        )
        return AppPrincipal(
            tenant=tenant, app=app, scopes=record.scopes,
            on_behalf_of_opaque=creds.headers.get("x-end-user-id"),
        )
```

### 6.4 租户解析器的 flavor 实现（社区 vs 企业）

```python
# gateway/community/plugins/authn/tenant_resolver/bare/_plugin.py
class BareTenantResolver(TenantResolver):
    async def resolve(self, tenant_token: str) -> str:
        if not tenant_token:
            raise AuthError("missing tenant token")
        return "tenant-bare"                              # 开源单租户

# 企业包：gateway/enterprise/plugins/authn/tenant_resolver/sofa/_plugin.py
class SofaTenantResolver(TenantResolver):
    def __init__(self, registry) -> None:
        self._registry = registry
    async def resolve(self, tenant_token: str) -> str:
        tenant = self._registry.lookup(tenant_token)      # 查签发记录
        if tenant is None:
            raise AuthError("invalid tenant token")
        return tenant
```

企业版通过 `register_plugin_option(...)`（`plugin_registry.py`）在 import 期把 `sofa` 实现挂上，
`GATEWAY_RUN_MODE=sofa` 时 `PluginAccessor` 选中——沿用组件既有机制，策略/端口代码零改动。

---

## 7. 网关运行器（先解析租户，再执行 OR/AND + scope）

```python
# gateway/community/core/authn/_runner.py
_TENANT_HEADER = "x-tenant-token"

async def authenticate(
    creds: CredentialBundle,
    route_security: list[dict[str, StrategyParams]],   # 见 §8 的编译结果
    registry: dict[str, AuthStrategy],
    tenant_resolver: TenantResolver,
) -> Principal:
    # ① 先验证租户令牌（对所有流量必需）
    tenant = await tenant_resolver.resolve(creds.headers.get(_TENANT_HEADER, ""))

    # ② 再跑身份策略
    last_err: AuthError | None = None
    for item in route_security:                        # 列表项之间 OR
        built: Principal | None = None
        ok = True
        for name, params in item.items():              # 项内多 scheme AND
            try:
                p = await registry[name].build(creds, params, tenant)
            except AuthError as e:
                last_err, ok = e, False; break         # 凭证非法 → 本项失败
            if p is None:
                ok = False; break                      # 凭证缺失 → 本项不适用
            if not params.scopes <= p.scopes:          # 要求 ⊆ 已授予
                last_err, ok = AuthError("insufficient scope"), False; break
            built = p                                  # AND 项当前恒为单 scheme；多 scheme 见注
        if ok and built is not None:
            return built                               # 本项通过 → 采纳
    raise last_err or AuthError("unauthorized")        # 无一通过：401/403
```

> 项内多 scheme（AND）当前无实际用例；若将来出现（如 `app_key` + `mtls_client`），在此处合并成一个 Principal。

认证成功后，`Principal` 由网关**签名**并转发下游；下游组件的 auth 退化为"验签 + 反序列化"，
不再自行对 OAuth/BUService 说话。签发/验签机制见 §7.1。

### 7.1 转发与信任：Principal 的签发与验签

**问题：** 网关把它生成的 `Principal` 转发给下游组件，组件凭什么相信"这确实来自网关、且没被篡改"？
威胁包括：绕过网关**直连**组件并伪造 `X-Avernet-Principal` 头；篡改/重放截获的 Principal。
**组件绝不能信任一个裸的 Principal 头。**

**建议：两层纵深防御，都要。**

**① 传输层 —— mTLS / 网络隔离。** 组件只对网关可达（网络策略 / service mesh），mTLS 认证信道
（网关客户端证书）并加密。挡住任意客户端直连。但单靠它不够（SSRF、同网段其它服务、被攻陷的 sidecar 仍可伪造）。

**② 载荷层 —— 网关对 Principal 非对称签名（JWT 风格短时令牌）。** 网关用**私钥**签名，
每个组件用网关**公钥**验签——非对称意味着组件能验、但**造不出** Principal（缩小 blast radius）。声明：

| claim | 作用 |
| --- | --- |
| `iss` | 签发方 = 网关 |
| `aud` | 目标组件（baas/engine/…）——**绑定受众**，发给 baas 的令牌无法重放到 engine |
| `iat`/`exp` | 短 TTL（秒级）——限制重放窗口 |
| `kid` | 签名密钥 id——支持轮换 |
| `jti`（可选） | + 组件侧 nonce 缓存 → 更强防重放 |
| payload | 序列化的 `Principal`（判别联合，含 `type`/`tenant`/…） |

**组件侧 = 一个"网关信任"插件：** 验签 → 校验 `aud`/`exp` → 反序列化 `Principal` → 投影成域 DTO（§9）。
这正是"组件跑 `auth.mode=none`"的**确切含义**——组件不再认证第三方，只验证"这是网关签发、未被篡改的 Principal"。

**做成 SPI（沿用 bare/sofa）：**

- 网关侧 `PrincipalSigner`：`bare` = HMAC 共享密钥（单盒够用，但持密方能造令牌）；`sofa` = 非对称 + 密钥管理。
- 组件侧 `PrincipalVerifier`：`bare` = 同一 HMAC；`sofa` = 拉网关公钥 / JWKS 验签，按 `kid` 缓存轮换。

**防重放补充：** 短 `exp` + `aud` 绑定通常够；要防"同一令牌换个请求重放"，可把 `method+path`（或 body 摘要）
纳入签名 claims，或依赖 mTLS 通道绑定。

---

## 8. per-route 鉴权配置（单表 + 具体度覆盖）

> **一张表**，每条是一个 path（可带 method）模式；**更具体的规则覆盖更一般的**，最一般的 `"/**"` 就是顶层默认。
> 无 `defaults`/`overrides` 二分。

### 8.1 权威源：随各 API spec 声明（作者手写）

```yaml
# 挨着 endpoint 写在组件的 API spec 里
POST /v1/bots/{id}/chat:
  security:                 # 列表项之间 OR；项内多 scheme AND
    - app_key:           { scopes: [bots:chat] }
    - first_party_user:  { scopes: [bots:chat] }

POST /v1/apps/self/usage:
  security:
    - app_key: { delegation: forbidden, scopes: [usage:read] }
```

### 8.2 网关消费视图：单张路由表（构建期聚合）

```yaml
# 一张表；键是 (可选 METHOD +) path 模式；更具体者优先
route_security:
  "/**":                              [ first_party_user ]                # 顶层默认
  "POST /open_api/**":                [ app_key: {} ]
  "POST /open_api/v1/bots/{id}/chat": [ app_key: { scopes: [bots:chat] } ]
```

### 8.3 匹配规则（每次请求）

```
1. 取所有 pattern 命中当前 (method, path) 的规则
2. 选**最具体**的一条：
     - 带 method 的胜过不带 method 的
     - 字面前缀更长 / 通配符更少者更具体（/open_api/v1/** 胜过 /open_api/**，胜过 /**）
3. 命中规则整条生效（不与更一般规则做字段合并——行为可预测）
4. "/**" 兜底必命中；若刻意留空且无命中 → fail-closed 拒绝
```

- **单一默认**：`"/**"` 就是那条"顶层默认"，天然被更具体规则覆盖。
- **整条覆盖，不合并**。
- **CI 门禁**（呼应 `docs/arch/ci.enforce.md`）：每条对第三方暴露的 route 必须能解析到一条 requirement，
  否则构建失败——防止 route 与鉴权声明漂移。

> **粒度直觉：** endpoint 粒度 = 策略粒度。实际写起来是"一条顶层默认 + 少量更具体规则"。

---

## 9. 消费侧：Principal → 各组件域 DTO（各组件自持）

轴 B。**不做**网关中心化投影（否则网关要 import 每个组件的域类型，变 god-object，违反边界）。

- 网关只下发规范化、签名后的 `Principal`；对具体域一无所知。
- 每个组件在自己的 adapter 边界自持 `Principal → 本组件 DTO` 的窄转换器，按 `type` 判别：

```python
# 某组件 adapter 边界（示意）
def project(p: Principal) -> AuthenticatedUser:
    match p:
        case UserPrincipal(subject=u, tenant=t):
            return AuthenticatedUser(staffId=u.staffId, tenantId=t, operatorName=u.operatorName)
        case AppPrincipal(app=a, tenant=t, on_behalf_of_opaque=eu):
            return AuthenticatedUser.for_app(org=a.developer_org_id, tenantId=t, acting_for=eu)
```

- 组件核心/路由**永不 import** 网关的 `Principal` 类（Rule 7；红线："路由不得直连非 Service-API 类型"）。

| 组件 | 它真正需要的域模型 |
| --- | --- |
| backend | `AuthenticatedUser`（staffId/tenantId/operatorName）——已存在 |
| engine  | `AuthGateService.verify(token, content, session_id)`——要 caller token + 幂等键 |
| bcs     | 多半只要 `tenant + scopes + type` |
| baas    | `developer_org_id`（App 场景）或 `subject.staffId`（用户场景）作为 owner key |

---

## 10. 第三方主体形态（本轮）

本轮第三方**只以自身身份**调用（`AppPrincipal`）。两种子形态：

- **纯 App 调用**：`on_behalf_of_opaque = None`。资源归属 = `developer_org_id`。
- **App 替它自己的终端用户**：`on_behalf_of_opaque = <X-End-User-Id>`——**对我们不透明、未经验证**，
  仅用于归属/配额/审计。资源归属**仍锚定 `developer_org_id`/`tenant`**（见 §11 防线）。

> "App 代表**我们身份体系里的真人**"（`xoneid` 解析出可信 `subject` 的 `DelegatedPrincipal`）本轮搁置，见 §15。

---

## 11. 授权分层

- **网关（粗粒度，租户/App/策略级）：** 租户令牌校验（§7①）；route 是否允许该策略；`scopes` 校验；
  配额、限流、租户隔离、审计。
- **组件（细粒度，资源级）—— 留 core（Rule 7）：** "这个 principal 能否访问 bot X / entity Y"——
  已有 `AuthPlugin.authorize_entity_access()`、engine `AuthGateService.verify()`。
  **第三方必加防线：** `AppPrincipal` 下 owner key **锚定 `developer_org_id`/`tenant`**，
  `on_behalf_of_opaque` 绝不用于跨界资源判定——防止 A 开发者借一个 end-user id 去读 B 的资源。

---

## 12. 具体落点：baas open API server 的收编

`baas .../open_api/dependencies.py` 现状：API Key（Bearer）验出 `app_id/app_type/tenant/policy` +
`IAM_TOKEN` cookie（用户，可选），`policy.allowed_bots` 做 fail-closed 白名单。本轮映射：

| 现状 | 目标 |
| --- | --- |
| API Key → `app_id` / `app_type` | `AppPrincipal.app`（`ThirdPartyApp`） |
| `tenant`（api-key record） | 与**租户令牌**交叉校验后作为 `AppPrincipal.tenant` |
| `policy.allowed_bots` | 网关粗粒度 scope / 资源白名单（保留，fail-closed） |
| 第三方自有用户标识（未验证） | `AppPrincipal.on_behalf_of_opaque` |
| `IAM_TOKEN` / `xoneid`（验证成可信 subject） | ⏸ 见 §15 |

**当前风险收口：** 现状用户来自 cookie、与 api_key 各走各、未把"该 user 属不属于此 App/tenant"绑死。
收编后按 §11：被代表 user 本轮只能是不透明句柄，绝不当已认证身份。收编后 baas 退成"验签 Principal → 投影成 `BotChatContext`"。

---

## 13. 落地路径（增量，不破坏现有 Rule）

1. 新增 `spi/authn/`：`Principal` 判别联合、`ThirdPartyApp`、`AuthStrategy`、`TenantResolver`/`ApiKeyValidator` 依赖协议
   + `PrincipalSigner`/`PrincipalVerifier`（§7.1）+ conformance test（Rule 25）。
2. **bare 先行**（单盒优先，Rule 20）：`TenantResolver`(bare) + `app_key` 策略打通最小链路
   （租户令牌 → App 认证 → 签名 Principal → baas 投影 → owner=org）。
3. 加 `first_party_user`、§7 runner、§8 路由表 + CI 门禁。
4. 企业包 `sofa`：`SofaAuthPlugin` + `SofaTenantResolver`，经 `register_plugin_option` 挂载。
5. 逐组件补 `Principal → 域 DTO` 投影器；把 baas open_api 收编到网关。
6. 委托（§15）按需解冻。

---

## 14. 待拍板的开放问题

1. **网关↔组件信任**：方案已定（§7.1，mTLS + 非对称签名 Principal 双层）；待定的是**密钥分发/轮换**细节
   （JWKS vs 配置注入）与是否加入请求绑定（`method+path`/body 摘要）防重放。
2. **租户令牌的签发与轮换**：由谁签发、有效期、轮换与吊销机制？承载方式（`X-Tenant-Token` header 还是 mTLS 客户端证书）？
3. **App 与租户的绑定**：一个租户下多个 App 如何注册；`app_key` 与租户令牌交叉校验的失败语义（拒绝 vs 告警）。
4. **模式 B 资源归属粒度**：归 `developer_org_id` 还是 `client_id`？（同一开发者多 App 是否共享资源。）
5. **`AuthStrategy` 归属**：独立 `spi/authn/` 还是并入现有 `spi/auth/`？（建议独立，避免与第一方 `AuthPlugin` 语义混淆。）

---

## 15. 已推迟：委托（DelegatedPrincipal，App 代表已验证真人）

本轮搁置，但设计思路记录在此，解冻时直接接回。届时新增：

- **`DelegatedPrincipal`**（判别联合第三成员）：`type=DELEGATED`，同时带 `app: ThirdPartyApp` 与
  `subject: AuthenticatedUser`（两者必填），外加 `tenant`。
- **`PrincipalType.DELEGATED`** 与 **`Delegation.REQUIRED`** 启用。
- **策略 `app_key_delegated`**：`api_key` + partner 显式转发的 `xoneid` header；用
  **`SubjectTokenResolver`** 依赖协议（`bare` 抛不支持、`sofa` 用 BUService SDK 解析 `xoneid` → `AuthenticatedUser`）产出 `subject`。
- **下游可持续凭证**：若 runtime 需"代表用户"的持久凭证，复用 backend
  `CallerIdentityService.exchange_caller_identity()`（subject + owner 预授权委托凭证换取，**只写 BaaS，绝不回吐 partner**）。
- **前置确认**：BUService subject token 是否 sender-constrained（audience/mTLS/DPoP），决定 `xoneid` 透传边界。
- 另一路 **模式 C**：`oauth_bearer` + 授权码同意页（"用 Avernet 登录"），第三方全程不碰 IAM token。

---

## 附录：术语

- **`AuthenticatedUser`**：已验证的终端用户身份（组件既有模型）。
- **`ThirdPartyApp`**：第三方应用身份（调用程序本身）。
- **`Principal`**：网关产出的中立鉴权对象，判别联合 `UserPrincipal | AppPrincipal`，每个成员必带 `tenant`。
- **租户令牌 / `TenantResolver`**：每租户唯一令牌；网关先验证并映射成 `tenant`（必填），与身份策略正交。
- **`AuthStrategy`**：网关侧"构建 Principal 的方式"的命名策略；`build()` 用 `None`/`raise` 区分"不适用"/"非法"。
- **flavor `bare` / `sofa`**：社区（开源无后端）/ 企业（BUService），由 `GATEWAY_RUN_MODE` 选择。
- **`xoneid`**（§15）：partner 显式转发的用户令牌 header，`sofa` 侧用 BUService SDK 解析为 `AuthenticatedUser`。
