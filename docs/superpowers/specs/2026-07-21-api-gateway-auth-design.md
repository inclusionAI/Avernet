# API Gateway 认证与授权设计（面向第三方开发者）

**状态：** 草案 / 待评审
**日期：** 2026-07-21
**范围：** 新增 API Gateway 组件的认证（AuthN）与授权（AuthZ）设计，本轮聚焦**第三方开发者**接入。
**关联架构约束：** `docs/arch/arch.rules.md`（Rule 1 / 3 / 7 / 14 / 19 / 25）。

---

## 1. 背景与目标

我们要新增一个 **API Gateway** 组件，统一承接所有入站用户请求，认证后转发给对应后端组件
（`backend` / `bcs` / `engine` / `baas`）。与以往不同的是：这批 API 不仅服务我们自己的前端，
还要开放给**第三方开发者**，让他们基于我们的 API 构建自己的 agent 平台。

因为是**第三方的服务器代表它自己的终端用户**来调用我们，认证/授权比第一方场景复杂：
调用主体可能是"第三方 App 本身"，也可能是"第三方 App 代表某个用户"。本设计要回答：

1. 认证在网关统一完成；网关产出一个中立鉴权对象，转发给下游组件，各组件按需消费。
2. Community 与 Corp 两种部署采用不同认证实现（Community：OAuth/OIDC；Corp：BUService）。
3. 第三方"代表其用户"时，身份如何传递与解析（含 IAM/`xoneid` token 转发）。

### 设计原则（沿用现有微内核架构）

- **契约即权威**（Rule 1）：每个 API 的鉴权要求写在它自己的 spec 里，网关只执行，不内嵌 per-API 逻辑。
- **交付层薄、核心与传输无关**（Rule 7）：网关/adapter 只做鉴权上下文的**翻译**，不拥有领域策略；
  资源级授权留在各组件 core。
- **配置驱动装配、mode 不下沉**（Rule 14）：Community/Corp 的差异由 DI 在 composition root 按 profile 绑定，
  不出现在 adapter/core 代码里。
- **两例之后再抽象**（Rule 19）：认证策略集保持封闭且小，只有出现全新的"凭证→身份"路径时才新增。

---

## 2. 现状盘点（本设计长在已有代码之上，不另起炉灶）

调研后确认，本设计所需的核心模式在代码库里**均已存在**，网关是把它们**上提并统一**：

| 已有能力 | 位置 | 复用方式 |
| --- | --- | --- |
| 中立身份 `AuthenticatedIdentity` + `AuthPlugin.resolve_user_from_request()` | `backend .../plugin_api/auth.py` | 作为 `Principal.subject` 的基础形状 |
| 身份边界转换器 `_to_authenticated_user()`（插件模型 → adapter DTO） | `backend .../adapters/http/auth/dependencies.py` | 各组件"Principal → 域 DTO"投影的样板 |
| 内容放行 gate `AuthGateService.verify()` | `engine .../plugin_api/auth_gate/` | 资源/内容级授权，保留在组件侧 |
| Token 交换 `TokenExchangePlugin`；corp 换取、community 透传 | `backend .../plugins/{community,local}/token_exchange.py` | 委托换取的插件契约 |
| Caller 身份换取 `CallerIdentityService.exchange_caller_identity()`（subject iam_token + owner 委托凭证） | `backend .../core/caller_identity/service.py` | 第三方 OBO 换取直接复用，凭证只落 BaaS |
| **第三方 open API server（API-Key 认证）** | `baas .../adapters/web/routers/open_api/` | 网关把它上提泛化；见 §9 具体例子 |
| Community/Corp 插件按 profile 绑定（`Mode`/`Flavor` 注册） | `.../plugin_api/impl_registry.py` | 网关认证策略沿用同一绑定机制 |

**关键结论：** 网关不是新增一套平行鉴权，而是把 `AuthPlugin` → `AuthenticatedIdentity` → `AuthenticatedUser`
这条"中立身份 + 边界转换"的既有模式，从单个 backend **提升为网关统一产出、多组件各自投影**。

---

## 3. 核心契约：中立鉴权对象 `Principal`

网关认证完成后产出一个**部署无关、provider 无关**的 `Principal`，它是"每个 API 声明自己要什么鉴权域模型"
时的**统一源对象**。

```python
# gateway/plugin_api/principal.py

class PrincipalType(StrEnum):
    USER            = "user"            # 第一方：我们自己的登录用户（前端 / 人工 curl）
    THIRD_PARTY_APP = "third_party_app" # 第三方 App 以自身身份调用（2-legged）
    DELEGATED       = "delegated"       # 第三方 App 代表某个用户调用（3-legged / OBO）

@dataclass(frozen=True)
class ThirdPartyApp:
    client_id: str            # 注册时下发
    developer_org_id: str      # 开发者 / 组织，资源归属兜底主体
    delegation_mode: str       # "app_principal" | "token_exchange" | "auth_code"

@dataclass(frozen=True)
class Principal:
    principal_type: PrincipalType
    scopes: frozenset[str]                        # OAuth scope —— 授权输入
    tenant_id: str | None                         # 租户 / BU
    app: ThirdPartyApp | None = None              # 第三方场景才有
    subject: AuthenticatedIdentity | None = None  # 仅"真正验证过的用户身份"present
    on_behalf_of_opaque: str | None = None        # 第三方私有的不透明用户句柄
    auth_method: str = ""                         # provenance，用于审计
```

**不变量（安全底线）：**

- `subject` 与 `on_behalf_of_opaque` **互斥**。
  - `subject` present ⇒ "已验证这是我们身份体系里的某个真人"（可用于跨界资源判定）。
  - `on_behalf_of_opaque` present ⇒ "这是第三方自己的用户，对我们不透明"，**只用于归属 / 配额 / 审计，
    绝不当作已认证身份去访问不属于该 App 的资源**。
- 遵守仓库对 `T | None` 的约束：只有契约上确实可缺省的字段用 `| None`
  （`app` 对第一方缺省、`subject` 对纯 App 调用缺省）；`principal_type` / `scopes` 等必备项保持非可选。

---

## 4. 两个正交的轴：构建 vs 消费

设计里有两件容易被混为一谈、但必须分开的事：

- **轴 A —— 如何"构建" Principal**（哪种凭证 → 哪种身份）。**归网关。** 见 §5、§6。
- **轴 B —— 如何"消费" Principal**（投影成各组件的域 DTO）。**归各组件自持。** 见 §7。

> 一句话：网关负责"产出 + 签名 + 转发一个规范化 Principal"；各组件负责"验签 + 投影成自己的域对象"。
> 谁都不越界——网关不认识任何组件的域模型，组件不接触任何第三方原始凭证。

---

## 5. 认证在网关：一个插件契约，两套实现

网关持有 `GatewayAuthPlugin`（形态与现有 `AuthPlugin` 同构），DI 按部署 profile 绑定：

```python
# gateway/plugin_api/gateway_auth.py
@runtime_checkable
class GatewayAuthPlugin(Plugin, Protocol):
    async def authenticate(self, ctx: AuthRequestContext) -> Principal: ...
```

| 部署 | 实现 | 认证方式 |
| --- | --- | --- |
| Community | `OAuthGatewayAuth` | 校验 OAuth2 / OIDC token（可直接复用现有 BCS 统一认证作为 authorization server） |
| Corp | `BuserviceGatewayAuth` | 走 BUService SDK：App 用服务身份；用户委托用 `xoneid` 解析（见 §8） |
| Local/Test | `LocalGatewayAuth`（`Flavor.FAKE`） | 从 header 直接造 Principal，`auth.mode=none` |

**网关 → 组件的信任：** 网关认证后把 `Principal` 序列化为**签名的内部头**
（`X-Avernet-Principal` + `X-Avernet-Principal-Sig`，网关私钥签名，叠加内网 mTLS），转发给下游。
组件**不再各自对 OAuth / BUService 说话**；其 auth 插件退化为"验证网关签名 + 反序列化 Principal"。
这与现有注释一致——*"the community gateway runs `auth.mode=none`"*（`plugins/community/token_exchange.py`）。

---

## 6. 如何编码"不同 API 用不同方式构建 Principal"（本设计核心）

**不在网关写 per-API 的 if/else**，而是采用 OpenAPI 风格的 `securitySchemes` + `security` requirement 模型
（契合 Rule 1）。分三层：

### 6.1 网关侧：封闭的命名策略集 `AuthStrategy`

每种"构建 Principal 的方式"是一个**命名策略**，本身是插件、按 profile 绑 Community/Corp 实现：

```python
# gateway/plugin_api/auth_strategy.py
@runtime_checkable
class AuthStrategy(Plugin, Protocol):
    name: str  # 稳定名字，API 契约按名字引用

    async def build(self, creds: CredentialBundle, params: Mapping[str, Any]) -> Principal | None:
        """凭证不属于本策略 → 返回 None（让下一个候选试）；
           凭证在但非法 → raise Unauthorized/Forbidden（硬失败）。"""
```

| strategy | 读什么凭证 | 产出 |
| --- | --- | --- |
| `first_party_user` | OIDC / IAM cookie | `USER`，`subject` 填满 |
| `app_key` | `Authorization: Bearer <api_key>` | `THIRD_PARTY_APP`，`subject=None` |
| `app_key_delegated` | api_key + `xoneid` header（SDK 解析） | `DELEGATED`，`app` + `subject` 都填 |
| `oauth_bearer` | OAuth access token | `USER` / `DELEGATED` |

策略集**封闭且小**（Rule 19）：只有真出现全新"凭证→身份"路径时才加，**绝不是每个 API 加一个**。

### 6.2 API 侧：每个 API 在自己契约里声明 `security` requirement

这段随 API 定义走（由拥有该 API 的组件作者书写），**不是**写在网关代码里：

```yaml
# 随组件的 API spec 走，构建期聚合进网关路由鉴权表
POST /v1/bots/{id}/chat:
  security:                      # 列表项之间是 OR，任一满足即放行
    - app_key_delegated:         # 方式一：第三方 App 代表用户
        scopes: [bots:chat]
        delegation: required     # 参数化：此 API 要求必须有被代表用户
    - first_party_user:          # 方式二：我们自己的登录用户
        scopes: [bots:chat]

POST /v1/apps/self/usage:
  security:
    - app_key:                   # 只认 App 自己，不需要用户
        scopes: [usage:read]
        delegation: forbidden
```

**per-API 的差异 = "选哪个 strategy + 传什么 params + OR/AND 组合"**，而非新增网关代码。
同一个 `app_key_delegated`，A 接口要求 delegation 必填、B 接口选填——靠 `params` 区分，策略实现只有一份。

### 6.3 网关运行时 = 通用策略运行器

```
match route ──► 取该 route 的 security requirement
           ──► 按顺序试每个候选 strategy.build(creds, params)
                 · 返回 Principal 且满足 scopes ──► 采纳，验签后下发下游
                 · 返回 None ──► 试下一个候选
                 · raise ──► 记录，硬失败或继续（按 OR 语义）
           ──► 无候选满足 ──► 401 / 403
```

网关**永远不含 per-API 分支**；上新 API = 加一段 `security` 声明，不动网关代码。

### 6.4 元数据的粒度、来源与防漂移

- **是 per-route 的元数据，但不是手工登记在网关里**：权威源是各组件 API spec 里的 `security`
  声明，构建期**聚合**成网关的路由鉴权表；网关只消费该编译产物。
- **默认 + 继承**：按前缀 / 分组给默认，endpoint 只在偏离默认时才覆盖。绝大多数 endpoint 无需单独书写。

  ```yaml
  defaults:
    /open_api/**: { security: [ app_key_delegated: {delegation: optional} ] }
    /internal/**: { security: [ first_party_user ] }
  overrides:
    POST /open_api/v1/bots/{id}/chat:
      security: [ app_key_delegated: {delegation: required, scopes: [bots:chat]} ]
  ```

- **Fail-closed**：匹配不到元数据 = 拒绝（至少拒绝第三方凭证），不是放行。
- **CI 硬门禁**（呼应 `docs/arch/ci.enforce.md` 风格）：每条对第三方暴露的 route 必须能在鉴权表里
  解析到一条 requirement，否则 CI 失败——把"route 与鉴权声明必须同步"变成结构性检查。

> **推论：** endpoint 粒度 = 策略粒度。两个 endpoint 想要不同构建方式，它们本就是两条 route，天然分开；
> 一批 endpoint 策略相同，用前缀默认收敛成一条。实际写起来是"per-分组默认 + 少量 per-endpoint 覆盖"。

---

## 7. 消费侧：Principal → 各组件域 DTO（各组件自持）

轴 B。**不采用**网关中心化投影（那会让网关 import 每个组件的域类型，变成 god-object，违反边界规则）。
采用**组件自持转换器**：

- 网关只下发规范化、签名后的 `Principal`；对具体域一无所知。
- 每个组件在自己的 delivery adapter 边界，自持一个 `Principal → 本组件 DTO` 的窄转换器——
  与现有 `_to_authenticated_user()` 同一手法，只是输入换成网关下发的 Principal。
- 组件核心 / 路由**永不 import 网关的 `Principal` 类**（Rule 7；红线："路由不得直连非 Service-API 类型"）。

各组件要的域模型可以很不一样，正是"每个 API 自己声明"的价值：

| 组件 | 它真正需要的鉴权域模型 |
| --- | --- |
| backend | `AuthenticatedUser`（staffId / tenantId / operatorName）——已存在 |
| engine  | `AuthGateService.verify(token, content, session_id)`——要 caller token + 幂等键，不需完整档案 |
| bcs     | 多半只要 `tenant_id + scopes + principal_type` |
| baas    | `developer_org_id`（App 场景）或 `subject.staffId`（用户场景）作为 owner key |

---

## 8. 第三方"代表其用户"的委托模式

**在 App 注册时为每个 App 固定一种 `delegation_mode`**，网关据此走不同分支。

### 8.1 决策矩阵

```
待操作的用户是"我们身份体系里的人"吗？
├─ 是，且第三方能拿到并转发用户令牌（xoneid / IAM token）
│     → 模式 A：Token Resolve / Exchange
│       partner 转发 xoneid header → 网关用 BUService SDK 解析 → Principal.subject 填满
│       典型：企业内集成方、ISV 帮企业客户接入
│
├─ 是，但用户此刻在浏览器、愿意授权（"用 Avernet 登录"）
│     → 模式 C：Authorization Code + PKCE (3-legged)
│       用户在我们的同意页授权 → 第三方拿到 user 维度 token；第三方全程不碰 IAM token
│
└─ 否（第三方的用户是他们自己的用户，对我们不透明）—— 默认
      → 模式 B：Client Credentials (2-legged, app_principal)
        principal_type=THIRD_PARTY_APP，subject=None
        end-user 仅作为不透明 X-End-User-Id → on_behalf_of_opaque
        资源归属 = developer_org_id
```

### 8.2 模式 A 的具体链路（`xoneid` 已确认可转发）

浏览器带 `xoneid` header 到 partner server → partner **显式转发** `xoneid` 给网关 →
网关（Corp）用 BUService SDK 从 `xoneid` 解析出 entity_id / nickName / tenant → 填 `Principal.subject`。
形状与现有 `AuthPlugin.resolve_user_from_request()` 一致，仅凭证从 cookie 换成 `xoneid` header。
Community 侧同名策略底层换成 OAuth userinfo 解析——上层 API 声明不变。

**安全注记：**

- `xoneid` 属敏感凭证，网关侧一次性解析、**不回吐**、不落日志明文。
- 若下游 runtime 需要"代表用户"的可持续凭证，走现有 `CallerIdentityService.exchange_caller_identity()`：
  以 `subject`（用户令牌）+ owner 预授权的委托凭证（passport）换取 caller token，**只写入 BaaS，绝不回吐给
  partner**（沿用代码里的 COSEC 约束）。
- 前置确认项：BUService 的 subject token 是否 sender-constrained（audience / mTLS / DPoP 绑定）。
  若绑定，则仅"最初被签发方"能用，模式 A 的透传边界需与 BUService 团队对齐。

---

## 9. 授权分层

**不要把所有授权堆到网关。** 清晰分工：

- **网关（粗粒度，App / 策略级）：**
  - App 是否被允许调这个路由 / API 版本；
  - scope → route 映射（`bots:chat` 才能打对应接口）；
  - 配额、限流、租户隔离、审计。
- **组件（细粒度，资源级）—— 保留在 core（Rule 7）：**
  - "这个 principal 能否访问 bot X / entity Y"——已有 `AuthPlugin.authorize_entity_access()`、
    engine `AuthGateService.verify()`。
  - 第三方场景**必加的越权防线**：App 模式（`on_behalf_of_opaque`）下，owner key **锚定到
    `developer_org_id` / `tenant`**，防止 A 开发者借一个 end-user id 去读 B 的资源。

---

## 10. 具体落点：baas open API server 的收编

`baas .../adapters/web/routers/open_api/` 已有第三方 API-Key 认证，是本设计最直接的落点。现状：

```python
# baas open_api/dependencies.py（现状）
BotChatContext.from_api_key(
    app_id   = record.app_id,       # App 身份（= bot_id），API Key 验出
    app_type = record.app_type,
    tenant   = record.tenant,
    iam_token= <IAM_TOKEN cookie>,  # 用户身份（可选）
)
# 再用 policy.allowed_bots 做 fail-closed 白名单校验
```

它已经把"模式 B（App 主体）"与"少量模式 A（用户维度）"混在一起。收编方式：

| 现状字段 | 目标 `Principal` |
| --- | --- |
| `app_id` / `app_type` / `tenant` | `Principal.app` + `Principal.tenant_id` |
| API Key 的 `policy.allowed_bots` | 网关粗粒度 scope / 资源白名单（保留，fail-closed 好设计） |
| `IAM_TOKEN` / `xoneid`（验证过） | `Principal.subject`（模式 A） |
| 第三方自有用户标识（未验证） | `Principal.on_behalf_of_opaque`（模式 B） |

**当前需重点收口的风险：** 现状 `IAM_TOKEN` 从 cookie 拿、与 api_key 各走各，未把"该 user 是否真属于此
App/tenant"绑死——一个 App 若能任意塞用户标识，可能借它读**不属于自己的用户**资源。网关必须补 §9 的越权
防线：被代表 user 要么经模式 A 验证过（`subject` 可信），要么只能是 App 私有不透明句柄
（`on_behalf_of_opaque`，绝不跨界读数）。

收编后：这段 `from_api_key + iam_token + policy` 逻辑上提到网关产出统一 Principal；baas 退成
"验签网关 Principal → 投影成 `BotChatContext`"。

---

## 11. curl / cookie 直连场景

区分两类调用方，不可混：

- **第一方 / 真人用 curl（今天粘浏览器 cookie）——保留不动。** cookie 本质是用户自己的会话凭证，网关
  `first_party_user` 策略读 cookie 的方式与读浏览器请求一致，产出 `principal_type=USER`。
- **第三方 / 程序化接入——不用 cookie，用 API Key / OAuth token。** cookie 会过期、绑用户、不可安全下发。

网关 `GatewayAuthPlugin` 同时接受多种凭证呈现，统一归一到同一 `Principal`：

```
Cookie (IAM_TOKEN / OIDC)        → Principal(type=USER, subject=...)
Authorization: Bearer <api_key>  → Principal(type=THIRD_PARTY_APP, app=..., subject=None)
Authorization: Bearer <oauth AT> → Principal(type=USER | DELEGATED, subject=...)
xoneid header (+ api_key)        → Principal(type=DELEGATED, app=..., subject=...)
```

规矩：cookie 路径只给第一方 / 人工；第三方程序化一律走 api_key / OAuth。网关按 `principal_type` 即可约束
（第三方 App 路由拒绝纯 cookie 凭证）。

---

## 12. 第三方 App 注册与凭证模型

- **开发者门户**：注册 App → 下发 `client_id` + `client_secret`（或非对称密钥对）；声明 `delegation_mode`、
  申请 scopes、回调 URL（模式 C 用）。
- **Community**：把现有 BCS 统一认证 / OIDC 扩成 OAuth2 authorization server，签发 App token
  （client_credentials）与用户委托 token（auth code + PKCE）。
- **Corp**：在 BUService 注册 App 为服务身份 / 应用；模式 A 用 `xoneid` 解析。
- 凭证轮换、scope 收敛、按 App 限流，均在网关统一做。

---

## 13. 落地路径（增量，不破坏现有 Rule）

1. 定义 `Principal`、`GatewayAuthPlugin`、`AuthStrategy` 三个契约（Plugin API）+ conformance test（Rule 25）。
2. **Community 先行**（单盒优先，Rule 20）：`OAuthGatewayAuth` + `app_key` 策略打通最小链路
   （App 认证 → 签名 Principal → baas 投影 → 资源 owner=org）。
3. 加 `app_key_delegated`（含 Community `oauth_bearer` 解析）与模式 C 同意页。
4. Corp：`BuserviceGatewayAuth` + `xoneid` 解析（模式 A），复用 `CallerIdentityService` / `TokenExchangePlugin`。
5. 逐组件补 `Principal → 域 DTO` 投影器；把 baas open_api 收编到网关。
6. 上 per-route `security` 元数据编译 + fail-closed + CI 门禁。

---

## 14. 待拍板的开放问题

1. **网关↔组件信任**：内网 mTLS 是否足够，还是叠加 Principal 头应用层签名？（建议两者都要。）
2. **模式 B 资源归属粒度**：归 `developer_org_id` 还是 `client_id`？（同一开发者多 App 是否共享资源。）
3. **模式 A 适用范围**：是否只对企业自建集成开放，外部 ISV 一律禁走 token resolve/exchange？（安全上建议是。）
4. **BUService subject token 是否 sender-constrained**：决定 `xoneid` 透传边界，需与 BUService 团队确认。
5. **engine 内容 gate**：第三方消息是否走与第一方相同的 `AuthGateService.verify()` 放行。

---

## 附录：术语

- **Principal**：网关产出的中立鉴权对象，见 §3。
- **AuthStrategy**：网关侧"构建 Principal 的方式"的命名策略，见 §6.1。
- **security requirement**：随 API spec 声明的、选择 strategy + 参数的鉴权要求，见 §6.2。
- **模式 A/B/C**：第三方委托的三种模式，见 §8。
- **`xoneid`**：partner 从浏览器取得并转发给我们的用户令牌 header，corp 侧用 SDK 解析为用户身份。
