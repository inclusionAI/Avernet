# BCS V1 独立 API Auth 插件链设计

- 日期：2026-09-18
- 状态：Draft / 已按 2026-09-18 审阅意见修订，待复审；不是实现授权
- Owner：BCS
- 范围：`src/bcs` 的 V1 HTTP 认证、OAuth 登录装配及相关契约
- 本轮产物：设计 spec；不修改运行时代码，不生成实施计划

## 1. 需求与目标

旧 `[auth]` 已用于旧 HTTP / WS 等认证消费方。V1 应新增独立配置，不能为了接入插件而改变旧链的配置选择、参数或执行顺序。共享 OAuth 登录/会话的安全缺陷必须同时修复旧入口，具体兼容性例外见第 8.4、8.5 和第 9 节。用户指定的配置形状为：

```toml
[api.auth]
chain = ["alipay", "github"]

[api.auth.alipay]
# 支付宝插件参数

[api.auth.github]
# GitHub 插件参数
```

本设计的目标：

1. 新配置独立控制 V1 可接受的认证来源，支持单插件、多插件及 Gateway 混合部署。
2. 每个来源在 `[api.auth.<name>]` 下接收独立、强校验的参数。
3. 复用 `crates/plugins/bcs-auth-*` 中已有能力，不要求修改每个业务 handler。
4. 认证结果统一进入 `AuthenticatedCaller`，继续执行现有身份策略及资源授权。
5. 保持旧 `[auth]` 的配置选择、插件顺序和非 OAuth 行为；未配置新链时保持当前生产 Gateway 业务入口边界。旧 OAuth 的浏览器绑定、会话写入和持久化错误语义按第 9 节一并安全收紧。

这里的“多插件”指多种可选认证来源，不指多因素认证，也不指权限取并集。

## 2. 已核实的现状与文档差异

以下文件路径均相对于 `src/bcs/`：

| 证据 | 当前行为 |
| --- | --- |
| `crates/adapters/http/bcs-api-http/src/v1/mod.rs` | OpenAPI / internal protected routes 共用 `verify_principal` |
| `crates/adapters/http/bcs-api-http/src/v1/common/principal.rs` | `PrincipalVerifier` 可注入；结果为 `AuthenticatedCaller` |
| `crates/adapters/http/bcs-api-http/src/v1/gateway_principal/verifier.rs` | 实现验证 `X-Avernet-Principal`，不调用旧 auth chain |
| `crates/bootstrap/bcs/src/server.rs` | 生产 V1 state 和单独的 connection-token router 注入 Gateway verifier |
| `crates/bootstrap/bcs/src/config.rs` | Gateway 默认信任 `gateway`、`backend` 两个 issuer |
| `crates/plugin-api/bcs-auth-api/src/chain.rs` | 旧链按 `priority()` 排序，首个成功生效，`None` 继续、`Err` 终止 |
| `crates/bootstrap/bcs/src/auth_wiring.rs` | 旧链内置 `local`、`session`、`oauth_session`，另有 factory 扩展 |
| `crates/plugins/bcs-auth-oauth/src/verify.rs` | 会话校验失败可返回 `None`；验证后没有 provider allowlist |
| `crates/plugins/bcs-auth-session/src/lib.rs` | 未知 Bot token 返回 `None`；身份字段不足以直接生成完整 V1 Bot |

`bcs-auth-alipay/github/google/wechat` 实现的是 OAuth 登录 provider；业务请求会话校验由 `bcs-auth-oauth` 负责。目录里有 provider，并不意味着它已经实现了逐请求认证插件。

已有 [OpenAPI Auth Facade 设计](2026-08-28-openapi-auth-facade.md) 描述了 Gateway 缺失时回退 cookie，但当前 `verify_principal` 与生产装配并未实现该回退。该文档还描述 best-effort logout，与仓库“持久化失败不得返回成功”的规则有冲突。本设计不把这些描述当作已经验证的运行行为。实施时必须同步修订旧设计相关章节，明确适用版本；不能静默制造两套契约。

## 3. 方案对比

### A. 独立 V1 来源链，复用现有插件能力（推荐）

使用 `[api.auth]` 装配新的 V1 认证来源链。Gateway 保留完整签名身份；OAuth provider 通过严格会话验证及来源选择接入。沿用 V1 `PrincipalVerifier` 注入点。

优点：满足配置形状；旧链配置和插件选择不受影响；新来源认证语义独立定义；业务 handler 基本不变。共享 OAuth 安全原语不能仅在新链修复。

成本：增加来源适配、严格会话结果契约及 V1 专属 OAuth 装配，并演进浏览器绑定 Service API、底层 Repository 错误契约和所有共享会话写入方。

### B. 给 V1 直接复用旧 `AuthPluginChain`

优点：初始代码量少。

不采用：排序不遵循配置顺序；无效凭据与未命中混淆；OAuth 来源没有 allowlist；输出缺少 V1 Bot 元数据。直接修正旧契约又会扩大旧 HTTP / WS 回归范围。

### C. 所有来源仍由 Gateway 完成认证

优点：BCS 只维护一个信任边界。

不采用：不能满足 BCS 独立配置和使用本地 auth plugins、无 Gateway 部署的需求。

## 4. 配置契约

### 4.1 完整示例

以下为拟新增 schema，当前代码尚不支持。密钥字段是 SecretAccessPort 中的逻辑引用，不是明文密钥。示例引用必须替换成部署实际配置。

```toml
# 旧链原样保留
[auth]
chain = ["session"]

# 新 V1 链
[api.auth]
chain = ["gateway", "alipay", "github"]
public_base_url = "https://bcs.example.com/openapi/v1/auth"
session_signing_key_secret = "bcs-api-session-signing-key"
session_idle_timeout_minutes = 30
success_redirect_path = "/"
trusted_browser_origins = ["https://workbench.example.com"]

[api.auth.gateway]
issuers = ["gateway", "backend"]
audience = "bcs"
key_id = "bare"
signing_key_secret = "principal-signing-key"

[api.auth.alipay]
client_id = "example-alipay-app-id"
private_key_secret = "bcs-api-alipay-private-key"
alipay_public_key_secret = "bcs-api-alipay-public-key"

[api.auth.github]
client_id = "example-github-client-id"
client_secret_secret = "bcs-api-github-client-secret"
```

`public_base_url` 明确为 V1 auth 路由的外部基址，callback 为 `{public_base_url}/callback/{provider}`，不得再追加 `/auth`，也不得从请求 Host / Forwarded 头推断。

### 4.2 公共字段与校验

| 字段 | 语义 |
| --- | --- |
| `chain` | 显式配置时必填、非空、有序且不重复的来源名称列表 |
| `public_base_url` | 启用任一 OAuth 来源时必填；绝对 HTTP(S) URL；无用户信息、query 或 fragment |
| `session_signing_key_secret` | 启用任一 OAuth 来源时必填；启动时解析并验证非空 |
| `session_idle_timeout_minutes` | OAuth 会话空闲超时；默认 30；必须为正且秒换算不溢出 |
| `success_redirect_path` | 默认 `/`；只允许单 `/` 开头的站内相对路径，拒绝开放重定向 |
| `trusted_browser_origins` | 启用任一 Cookie 型来源时必填的非空 Origin 列表；精确匹配 scheme/host/port，禁止通配、用户信息、path/query/fragment；用于 cookie 写请求防 CSRF |

Gateway-only 不要求 OAuth 字段；配置了仅用于 OAuth 的公共字段但未启用任何 OAuth 来源时启动报配置错误，避免无效参数造成错误预期。Cookie Secure 由基址 HTTPS 推导；非本地部署必须使用 HTTPS。`trusted_browser_origins` 不属于 OAuth 专用字段：内部 SSO Cookie 来源同样需要它；不启用任何 Cookie 型来源时不得配置该字段。

名称规则：小写字母开头，其余为小写字母、数字或下划线；公共字段名称保留，不能用作插件名。本期不提供 `kind` 别名和同类 provider 多实例功能。

每个插件独立声明 schema。公共层先解析保留字段与插件参数表，再由 bootstrap registry 对每张表执行对应 schema 校验；禁止把未校验的任意 TOML 直接传入运行时。公共未知标量、未知插件表、未知插件参数均启动失败。

`chain` 中出现的插件必须有可构造配置；允许保留已注册但未启用插件的参数表，仍检查其结构，但不读取其密钥、不初始化外部客户端。参数表本身不启用插件。

内置参数 schema：

- `gateway`：`issuers` 默认 `["gateway", "backend"]`、`audience` 默认 `bcs`、`key_id` 默认 `bare`；`signing_key_secret` 必填。issuer 非空且不重复，其余字符串非空。
- `alipay`：示例中的 `client_id`、`private_key_secret`、`alipay_public_key_secret` 必填且非空；启用时验证 RSA 密钥格式。
- `github` / `google` / `wechat`：`client_id`、`client_secret_secret` 必填且非空；provider 内部保持已有公开协议实现，本期不增加任意 endpoint 配置。
- 参数声明不得把必填字段改为 Optional 以掩盖缺失；不在配置中存放明文 client secret 或私钥。

### 4.3 默认、显式配置和隔离

- 缺少整个 `[api.auth]`：进入兼容装配，保留当前 Gateway-only protected routes、原 `[gateway_principal]` 密钥来源与登录 provider 选择。兼容模式也必须使用修复后的浏览器绑定和原子会话写入，不能保留已知的不安全 OAuth 原语。
- 存在 `[api.auth]`：`chain` 不可省略、不可为空，完全由新配置决定；不追加 Gateway，不从旧 `[auth]` 或 `[gateway_principal]` 隐式补值。
- 显式 plugin-only 配置不得因缺少未启用的 Gateway 密钥而启动失败；仍有其他消费方使用该密钥时，仅由其自身契约要求。
- 未知、重复或无法初始化的启用插件一律启动失败，不能警告后跳过。
- 新链没有 `require_authentication = false` 开关，protected routes 永远不允许匿名。
- 不修改旧链 debug/release 默认值和原有 Mock 环境变量行为；这些隐式默认不得影响新链。

## 5. 范围和插件支持边界

首期推荐正式支持 `gateway`、`alipay`、`github`、`google`、`wechat`，对应已有签名验证和 OAuth provider 能力；OAuth providers 共享严格会话校验实现，而不是复制 JWT 校验逻辑。测试提供显式注入的 fake provider / verifier，不将测试来源自动注册为生产可选插件。

配置中的 `alipay` 表示“允许支付宝登录并接受该来源的 BCS 会话”，不表示接受任意支付宝 access token。其他 OAuth 来源同理。

首期不直接提供旧 `session` / `local` 的生产 V1 注册：

- Bot session 缺少可信 tenant、app_id、agent_code，owner_id 也可能缺失；不能填空或把 Bot 转成 owner 的 Human。
- Local mock 可能无凭据生成用户，不能作为生产兜底；本地测试通过注入 fake 覆盖。

Gateway 原有 Bot/App/AccessKey 身份仍保持可用，并执行现有身份策略。直接 Bot-token 插件是单独待确认的交付范围：如要求首期纳入，必须先补充可信身份补全契约和数据源，不能仅把旧插件名加入列表。本 spec 不声称已解决所有现有 auth 插件的身份适配。

## 6. 架构与职责

```text
validated [api.auth]
    → bootstrap composition root / 来源 factory registry
        → Gateway verifier
        → OAuthProvider clients + V1 auth application facade
        → 严格会话验证、来源选择及身份转换
    → 有序 Composite PrincipalVerifier
    → VerifiedRequestIdentity { caller, authentication_context }（delivery 契约）
    → 按实际来源执行 CSRF 检查
    → 仅将 AuthenticatedCaller 传入业务用例
    → 现有 IdentityPolicy / application resource authorization
```

- **配置契约**：`bcs-config-api` 声明公共 schema；各插件声明其参数契约；配置加载和 bootstrap 解析密钥与选择实现。
- **HTTP adapter**：沿用 `PrincipalVerifier` 注入位置，但将成功结果演进为第 7.4 节的 delivery 结果；所有 Gateway/插件适配器、测试 verifier 和单独 connection-token router 同步迁移。公共有序 verifier 不导入具体 auth plugins，负责解析凭据、保存可信来源、CSRF 判断和 HTTP 错误映射。
- **Bootstrap bridge**：新增独立 `api_auth_wiring` 模块，负责把 Gateway verifier、auth Plugin API 和 V1 caller 契约连接起来；具体插件引用只留在 composition root，不搬进业务 handler。共享服务可复用对象，但不能复用旧链的选择结果。
- **OAuth plugin / Plugin API**：在 `bcs-auth-api` 增加与旧接口并存的严格会话校验结果契约，区分无凭据、有效会话、无效凭据和依赖错误。输入是已提取的会话凭据及配置，不新增 axum / JWT 类型到 application/core。`bcs-auth-oauth` 实现该契约；旧只读认证入口可以保留已声明的错误兼容映射，但也必须验证新的会话实例/版本和撤销状态。第 8.4–8.6 节的登录、刷新、撤销不得回退旧不安全语义。
- **身份目录**：从 Repository 起提供严格 `Result` 查询以及原子会话操作，不能用旧 Option 接口包装出成功结果。可信会话包含内部 user ID、provider、环境、会话实例 ID、版本和 hash；JWT 与记录一致性按第 8.5 节验证。不扩大 `bcs-service-api` 对 plugin-api 的依赖。
- **V1 AuthService**：保持 HTTP envelope，但必须按第 8.4 节增量演进登录命令/结果的浏览器绑定上下文；显式新配置下，其登录、当前用户、刷新和退出消费新装配，而非旧 OAuth route state 的配置。通用登录逻辑可以复用，不允许新 adapter 调旧 adapter 实现作为捷径。
- **Application**：资源所有权、群成员关系、Bot/Human Actor 选择保持原位置。认证来源不能直接授予业务权限。

这是已有认证扩展点的演进，不引入动态库加载、热更新、通用插件市场或跨服务权限中心。来源为进程内可信实现；OAuth 网络访问只限配置 provider 所需能力，身份/密钥访问经声明的端口；禁止日志输出凭据。必需依赖初始化失败阻止提供流量，关闭时释放客户端资源，不删除持久身份或登录记录。

## 7. 请求认证语义

### 7.1 有序选择

新链严格按配置顺序执行，不使用旧 `AuthPlugin::priority()`：

| 来源结果 | 新链行为 |
| --- | --- |
| NotApplicable：无该来源凭据 | 继续下一来源 |
| Authenticated：有效完整身份 | 结束认证，采用该来源结果 |
| InvalidCredential：该来源凭据错误、过期、撤销 | 立即 401，不回退 |
| Forbidden：有效身份被认证来源策略明确拒绝 | 立即 403，不回退 |
| Unavailable：存储、密钥或外部依赖故障 | 立即 503，不回退 |
| 内部转换错误或契约违例 | 500，记录脱敏错误，不回退 |
| 全部 NotApplicable | 401 |

第一个成功之后不再验证低优先级来源；其余凭据既不增强身份也不触发失败。这不是“只要任意一个通过就忽略之前的失败”。Gateway 优先部署应将 `gateway` 放首位；如果用户选择 OAuth 优先，成功 OAuth 后不会再检查 Gateway，该信任差异必须有测试和文档。

未启用来源的专属凭据不参与认证，也不触发隐式 fallback。启用来源的重复、空白或格式错误的 credential carrier 不得当成凭据缺失。重复 Gateway header、重复会话 cookie 等歧义输入应被拒绝。

### 7.2 OAuth 共用凭据的分派

OAuth 来源共用一个 BCS session cookie，不允许支付宝来源把有效 GitHub 会话误判成坏凭据。

到达链中第一个 OAuth 来源且存在 cookie 时，执行一次严格校验，并在当前请求内复用结果：

1. 验证会话签名及有效期；不要信任未验证 `src`。
2. 经严格 Repository 路径查询并核对服务端 token hash、会话实例/版本、subject、provider、环境和有效期；DB 错误不是未匹配。
3. 验证该 provider 在新链允许列表内；有效但未启用的 provider 返回 403。
4. 当前来源与已验证 provider 不同则 NotApplicable；匹配时生成 Human caller。

无效会话在首次 OAuth 处理时立即 401；依赖故障立即 503。此校验按链执行位置触发，不是全局预先校验，因此不会改变已成功的高优先级 Gateway 请求行为。

### 7.3 身份映射与权限

OAuth 输出：`user.id` 使用身份目录内部 ID；`username` 优先使用可信非空名称，缺失时使用内部 ID 作为稳定显示标识；display_name 可缺省。`tenant` 保持缺省，不从 env、请求头或第三方账号臆造；`bot/app/access_key` 均为空。

保持既有 `(auth_source, external_user_id, env)` 身份语义，不为新配置额外创建一套用户 ID 命名空间。不同 provider / Gateway 用户不会仅因邮箱或昵称相同而自动合并；账号绑定和历史资产迁移不在本期范围。

Gateway 身份映射继续遵循原签名契约；保留其可信多身份集合和 tenant 一致性验证。所有来源输出均不得包含原始 token、cookie、私钥。

插件仅证明身份；本期不新增 roles/scopes/permissions 契约，不合并来源权限。认证成功后现有 HumanOnly / BotOnly / HumanOrOwnedBot、邀请访问门禁和资源权限仍然生效。

### 7.4 Delivery 认证来源与 CSRF 上下文（审阅 R4）

`AuthenticatedCaller` 仍是业务身份，不增加来源或 Cookie 字段。新增 delivery 专用 `VerifiedRequestIdentity`，包含：

- `caller: AuthenticatedCaller`。
- `authentication_context.source`：本次实际成功的注册来源名。
- `authentication_context.credential_kind`：本次实际使用的凭据类型，例如 `GatewayPrincipalHeader`、`OAuthSessionCookie`、`SsoCookie`；扩展来源必须映射到明确的 Cookie/非 Cookie 安全类别。

`PrincipalVerifier::verify` 的成功类型改为该结果，错误补齐 Forbidden/Unavailable/Internal 映射。来源由 bootstrap 注入的注册描述符和可信 verifier 产生；客户端 Header、未验证 claims、未命中的插件不能设置或覆盖。支持多种 carrier 的插件必须报告本次实际使用的 carrier，不能仅凭插件静态名称推断。

`verify_principal` 在首个来源成功后固定上下文；HTTP 层先读取该上下文、请求方法和 Origin 完成 CSRF 检查，再将 caller/request_id 放入后续用例所需扩展。可信来源可另存为 delivery extension 供审计，但 application/core 不消费它。

两种混合凭据行为必须分别测试：

1. Gateway 优先且成功，附带 Cookie 不触发 Cookie 专属 Origin 要求；不得按“存在 Cookie”误拦。
2. OAuth/Cookie 优先且成功，即使同时带有效 Gateway header，unsafe 请求仍必须通过 Origin 检查；不得按“存在 Gateway header”跳过。

成功后的 Origin 拒绝是 403，不能继续执行另一个认证来源来绕过策略。此来源语义覆盖普通 protected routes、`/auth/user` 和独立 connection-token router；refresh/logout 的实际 carrier 固定为其已验证 OAuth Cookie，不借用另一来源的身份放行。

## 8. 路由和 OAuth 生命周期

### 8.1 认证覆盖

以下共用同一个新 verifier 实例：

- `/openapi/v1/collaboration/**` 的 protected routes。
- `/api/v1/collaboration/**` 的 protected routes。
- 独立装配的 `POST /openapi/v1/collaboration/sessions/{session_id}/token`。

不要给整个 `/api/v1` 或 `/openapi/v1` 前缀统一加认证。manifest、公开注册、邀请领取、分享内容等保留现有公开/专用 token 契约。邀请门禁保持先认证后检查。WS 本身的握手和连接 token 消费契约不在本次变更中。

### 8.2 V1 登录接口

显式 `[api.auth]` 下：

- `/openapi/v1/auth/url` 只枚举启用的 OAuth providers，顺序与 chain 中 OAuth 子序列一致。
- `/callback/{provider}` 只接受启用 provider；state 必须绑定 provider、回调、新 V1 登录流程和发起登录的浏览器 nonce，并按第 8.4 节原子校验/一次性消费。第三方失败或身份/会话写入失败不签发成功 cookie。
- `/user` 使用新认证链返回 Human 当前身份；纯 Bot/App 等仍无权作为 Human 返回。
- `/refresh` 只刷新新配置允许的、签名和服务端绑定有效的 OAuth 会话，通过第 8.5 节条件轮换提交；Gateway 成功不能代替 cookie 刷新授权。
- `/logout` 通过第 8.5 节按会话实例原子撤销当前 OAuth 会话及其刷新后继，再清 cookie；持久化撤销失败不得报告成功。已不存在该会话实例时可幂等成功，不得撤销后来重新登录的新实例。
- 未启用任何 OAuth provider 时，URL discovery/callback/refresh/logout 返回现有 `auth_not_configured` 404；`/user` 仍可由新链解析 Human。

不改变 V1 JSON envelope。登录 URL 响应新增临时 Cookie，callback 响应支持多个 Set-Cookie（设置登录会话并清临时 Cookie）；命令/结果演进见第 8.4 节。错误内容必须脱敏，不透传第三方响应中的 token 或敏感字段。

### 8.3 会话共存边界

本设计默认复用现有 `bcs_session` cookie 名、身份目录和单活跃会话模型，不引入新的账号系统或独立 session 表；但必须在原会话记录上增量加入会话实例/版本及条件写入语义，不能继续使用无条件 token hash 更新。共享存储不等于保持旧存储契约不变。这是“配置与认证选择独立”，不是“浏览器会话隔离”。

必须公开说明其影响：

- 相同 cookie 作用域下，新旧入口登录可能覆盖 cookie；相同身份行的新登录可能使旧 token 失效。
- 两条链使用不同签名密钥时，同一个 cookie 可能只被其中一条接受；不可假装透明互通。
- 复用密钥和记录时，来自旧登录入口的有效会话只有满足新链 provider allowlist、环境和绑定检查才可被接受。
- 切换新配置后不能保证旧浏览器会话仍可用，允许要求重新登录。
- 保持 HttpOnly、SameSite、Secure 规则与 OAuth state 防护；新接口允许 cookie 认证后，必须拒绝 cookie-authenticated 且未获明确允许的跨来源写操作；合法浏览器写请求应通过配置的可信 Origin 校验（含 scheme/host/port），缺失或不可信 Origin 的 cookie 写请求默认拒绝。Origin 只做 CSRF 校验，不能作为身份依据。callback GET 由绑定发起浏览器的 nonce + 一次性 OAuth state 保护，不能仅靠 provider/state 匹配；refresh/logout POST 同样需要防 CSRF。CSRF 判断读取第 7.4 节的实际成功来源，不按请求是否附带某个 header/cookie 推断。跨站 cookie 部署不能仅依赖 CORS；若部署依赖此能力，需在实现前补充明确防 CSRF 契约。

是否需要新旧浏览器会话完全独立，是本草案待确认的产品边界；如需要，应另补 cookie 名/作用域和持久会话命名空间设计，不能仅换一个 cookie 名就宣称隔离完成。

### 8.4 OAuth state 的浏览器绑定（审阅 R1）

现有 `oauth/state.rs` 只有 provider/TTL，`BuildLoginUrls` 和 `CompleteOAuthLogin` 也没有浏览器上下文，必须一起演进，不能依赖旧逻辑自动获得登录 CSRF 防护。

**发起登录：**

1. `/auth/url` 每次生成一批登录 URL 时，用 CSPRNG 生成独立的浏览器 nonce 和每个 provider 的随机 state，两者不能相同或互相推导。
2. 服务端 pending-state 记录保存 state 对应的 nonce hash、provider、精确 callback、flow namespace（legacy 或 v1）和 5 分钟过期时间。任何共享 state 存储必须保证一次性消费的原子性。
3. 通过 host-only、HttpOnly、Secure、SameSite=Lax、Path=/、Max-Age=300 的临时 `__Host-bcs_oauth_login` Cookie 向发起浏览器发送 nonce；仅本地 HTTP 开发允许使用不同名称的非 Secure cookie，生产不能降级。
4. nonce 不能出现在 provider URL、JSON providers 列表、日志或 query 参数中。响应使用 `Cache-Control: no-store`。跨 origin 获取 URL 时必须通过明确的 credentials/CORS 配置接收该 Cookie，接收失败不得另设无 Cookie fallback。

**完成登录：**

- HTTP adapter 提取唯一临时 Cookie，缺失/重复/空值直接按 invalid_state 拒绝。应用服务接收必需的、无 HTTP 类型的 `BrowserLoginBinding`；query 中的 nonce 不能替代它。
- state、nonce hash、provider、callback、flow namespace、TTL 全部匹配后，服务端原子消费该登录批次；只有一个 callback 可以进入 code exchange；nonce hash 比较使用常量时间比较，提交“消费”必须在成功响应或兑换 code 之前。先验浏览器绑定，再换 code、写身份和签发会话。
- 浏览器 B 携带浏览器 A 的合法 callback，必须失败，不调用 provider exchange、不写会话、不生成登录 Cookie；绑定不匹配不得消费 A 的合法 pending state。
- 已通过匹配并消费的 callback，无论后续成功还是失败，都清除临时 Cookie；成功时另加 bcs_session 的 Set-Cookie，不能互相覆盖。用户取消、未匹配回调不签发会话；未完成 state 和 Cookie 到 TTL 后清理。
- 本期明确每浏览器只支持一个待完成登录批次：重新获取 URL 旋转 nonce，旧批次即使尚未过 TTL 也不能在该浏览器完成；同批次 provider URL 只有一次成功机会。多标签重开需重新发起，不引入多流程管理功能。

**契约传播：** `AuthService::login_urls` 的内部结果需带非 JSON 的浏览器 challenge/过期描述，HTTP adapter 负责 Cookie 编码；`CompleteOAuthLogin` 增加必需绑定参数；callback 的结果支持设置会话和清 challenge 两项指令，错误路径也明确清理动作。命名由实现契约确定，但不能继续只返回一个 `set_cookie: String` 而丢失另一项。纯数据结果不引入 HTTP response 类型。

旧 `/auth/url`、旧 callback 和兼容模式 V1 同步使用相同浏览器绑定原语、不同 flow namespace，阻止旧入口成为向共享 bcs_session 写入攻击者身份的旁路。浏览器不附带临时 Cookie 的历史 callback 会被拒绝，需要重新发起登录。部署多实例时使用可原子消费的共享 pending-state store，或明确的单实例/固定路由约束；缺失 state 一律失败，不能靠无状态 fallback 绕过绑定。

### 8.5 原子会话轮换与撤销（审阅 R2）

“刷新前查 hash，随后按 user_id 无条件 update”不是安全轮换。所有共享写入方必须使用同一线性化会话操作，不能只修 V1。原子性由存储事务/条件更新或内存互斥保证，不能靠单进程锁保护多实例 DB。

**会话模型：** 每个 `(user_id, provider, env)` 的当前记录保存 `session_id`、单调递增 `session_revision`、当前 token hash、expiry；JWT 同时签入不可复用的 session_id、revision 和环境绑定，并验证它们与可信记录/部署环境匹配；不得仅用 JWT 自带环境选择另一个环境的存储。每次成功的新登录生成新的随机 session_id；refresh 保留 session_id、增加 revision，从而即使同一秒刷新也生成不同 token。哈希字段仍只保存 JWT 的 SHA-256，不保存原始 JWT。

新增严格 Repository/身份端口操作（名称示意，语义为契约）：

| 操作 | 原子条件与结果 |
| --- | --- |
| `install_login_session` | 仅用于完成 provider + 浏览器绑定验证的新登录；原子安装新的 session_id、revision、hash、expiry，替换先前活跃实例。会话凭据只有存储提交后才能发给浏览器 |
| `rotate_session` | 同时匹配 scope + session_id + expected_revision + expected_hash，且绑定未撤销/过期，再原子换成新 hash/expiry 和下一版本；返回 Applied、Conflict 或持久化错误 |
| `revoke_session` | 验证请求 Cookie 签名及 subject/provider/env/session_id 后，原子撤销该 scope 当前匹配的 session_id（包括它已轮换的最新版本），清 hash/expiry、推进 revision；不同或已撤销实例为幂等 NotCurrent，不碰新登录实例 |

撤销不能只按旧 hash 做 CAS：refresh 若先将 A 轮换为 B，logout(A) 仍需撤销同一 session_id 下的 B。反之，也不能只按 user_id 清空：旧会话的迟到 logout 不得清除用户重新登录产生的 C。撤销可接受经当前信任配置验签但已经过期的该实例 Cookie，只做不可恢复的撤销；不能用它读取受保护资源、刷新或建立登录。

必须保证的并发结果：

- logout(A) 先提交：所有已读 A 但未提交的 refresh 都条件失败，不得创建有效 B。
- refresh(A→B) 先提交：随后 logout(A) 撤销同实例 B。即使 refresh HTTP 响应迟到并写回浏览器 Cookie，B 也不能通过服务端验证。
- 新登录 C 先提交：旧 A 的 refresh 条件失败；logout(A) 不影响 C。新登录是一次新的认证，不等于已退出会话被刷新复活。
- 两次 refresh(A) 最多一次提交；失败者不发 Set-Cookie，也不自动按 user_id 重试成另一次成功。

条件不匹配的 refresh 返回 401，需要重新获取有效会话；存储故障返回 503，不能当作冲突或幂等成功。读取、轮换、撤销均须检查数据库执行结果/受影响行数；不能 UPDATE 0 行仍返回 Applied。旧响应的 Cookie 覆盖属于浏览器层可用性问题，不能改变服务端已撤销判定。

**覆盖全部写入方：** `bcs-http/src/oauth/mod.rs` 中旧 callback/refresh/logout 和 AuthService 实现、V1 新 facade、`bcs-auth-api` 身份端口、bootstrap `identity_wiring`、Memory/SQLite/MySQL store，以及任何共享记录的后台/管理写入必须迁移。旧 `update_token(user_id, ...)` 不得保留为这些生产路径的有效无条件后门；删除该写入口或让兼容调用明确失败。不能用旧签名默认适配出原子语义。

本设计增加原记录的会话实例/版本 schema，并同步 JWT 契约、迁移和测试。上线前阻断旧无条件写入二进制、排空在途请求，再完成迁移和安全版本发布；本期允许统一失效无 session_id/revision 的历史会话并要求重新登录，不尝试对旧 JWT 自动补实例。未经修复的新旧二进制不能混合写入同一存储。若所有共享写入方无法一起迁移，必须先改成真正隔离的会话存储再更新本 spec，不能降低原子性要求。

### 8.6 从 Repository 到 HTTP 的依赖错误传播（审阅 R3）

现有 `UserIdentityRepoPort::get_by_token -> Option<UserIdentity>`、DB store 的 `.ok()` 和 `identity_wiring` 的 `Ok(None)` 会丢失底层错误。仅给 auth Plugin API 加 Result 不足以兑现 503。

增加明确的严格 Repository 查询，例如 `get_session_by_hash(scope, token_hash) -> Result<Option<SessionRecord>, IdentityStoreError>`，返回第 8.5 节元数据与可信身份：

- `Ok(None)` 仅表示查询成功但记录不存在/不可用；
- 数据库连接、执行、读取失败返回 typed dependency error；不能 `.ok()`、`unwrap_or_default()` 或 log-and-None；
- 损坏记录/投影契约错误保留独立错误类别，不把它拼成匿名或伪身份。

Memory、SQLite、MySQL 实现及测试 doubles 必须实现该接口；不允许 default 实现调用旧 Option 方法。bootstrap `RepoUserIdentityPort` 把 repo 错误映射到严格 auth port；新链只调用严格路径。旧只读 Option 接口可以留给明确不依赖错误分类的兼容消费者，并标记降级边界，但不能被新链、刷新或撤销内部使用。

完整传播链：`DbPlugin error → IdentityStoreError → strict identity/auth error → delivery Unavailable → HTTP 503`。通过 AuthService 的路径同时增加 transport-neutral application Unavailable 错误及 envelope 映射；业务 core 不接收 HTTP 状态类型。浏览器只收到稳定脱敏错误码，不包含 SQL、数据库地址或凭据。

验收必须把故障注入到 DbPlugin 下的真实 store 查询/执行边界，经 `DbUserIdentityStore → RepoUserIdentityPort → auth verifier/AuthService → HTTP` 验证 503；仅让高层 fake user_port 返回 Err 不算完成该要求。

## 9. 兼容、传播和回滚

| 边界 | 变更及受影响方 |
| --- | --- |
| Config | 新增 api.auth；配置样例、schema 校验和部署文档同步；旧 auth 配置选择不变 |
| Plugin API | 增量严格 OAuth 查询/来源结果及原子身份会话操作；影响 bcs-auth-oauth、identity port、所有 impl/test doubles；旧只读降级映射仅留在明确兼容消费者 |
| Repository / persistence | UserIdentityRepoPort 增加严格 Result 查询、install/rotate/revoke 契约；Memory/SQLite/MySQL、DB 错误和行数检查、schema migration 一起更新；生产路径移除无条件 update_token |
| Service API | AuthenticatedCaller 不变；BuildLoginUrls/CompleteOAuthLogin/登录结果加入浏览器绑定输入输出；AuthService 增加 Unavailable 错误传播与原子生命周期调用；不引入 HTTP 类型 |
| Delivery | PrincipalVerifier 成功结果增加可信 source/credential_kind；所有实现、test doubles、CSRF middleware、主 router 和 connection-token router 同步；支持临时 Cookie 与多项 Set-Cookie |
| Bootstrap | api_auth_wiring 及 identity_wiring 显式连接严格路径；无法满足严格查询/原子写入的实现不得被选中用于共享 OAuth |
| OAuth/JWT/state | 所有新旧 URL/callback 加入浏览器绑定；JWT 增加 session_id/revision，state 存储原子消费；旧无绑定流程/历史会话按安全迁移失效 |
| Clients / shared writers | 新旧 callback/refresh/logout 全部迁移；客户端处理临时 Cookie、并发刷新 401、依赖 503 及重新登录；不允许旧二进制继续无条件写共享记录 |

**兼容性范围修正：** “旧链不变”仅指旧配置、插件选择/顺序及非 OAuth 行为，不包括继续接受未绑定浏览器 callback、无条件刷新/撤销或 best-effort logout。即使省略 api.auth，升级后的旧 OAuth 生命周期也使用修复原语；旧 HTTP envelope 尽量保持，安全拒绝和持久化失败返回错误属于明确行为收紧。这是本次共享设计所必需的影响，不以“兼容”名义保留绕过。

运行配置切换需要重启，不提供热更新。回滚可删除 `[api.auth]` 返回兼容模式，前提是原 Gateway 配置/密钥仍有效；旧二进制不承诺接受新配置表，二进制回滚必须同步配置、JWT/schema 兼容性和写入隔离；禁止直接回滚到仍会无条件写共享会话的版本。若只能回到该版本，应先关闭共享 OAuth 入口、撤销相关会话并采用 Gateway-only 安全运行，不能继续宣称第 8.5 节保证成立。回滚不撤销已经产生的用户和资源，也不保证原浏览器会话自动恢复。

现有大型 `server.rs` / `config.rs` 不应继续堆积实现；新逻辑按配置解析、来源装配、会话验证拆分。新增/修改源码遵守 1,000 行门禁，不运行全局 formatter，不借此重构无关逻辑。

## 10. 验收标准

| ID | 场景与必须观察的结果 |
| --- | --- |
| C1 | 无 api.auth：保留 Gateway-only 业务认证和原登录 provider 选择；旧 OAuth 仍执行浏览器绑定/原子会话安全收紧，旧非 OAuth HTTP/WS 测试通过 |
| C2 | 显式单 OAuth：无 Gateway 密钥可启动；无关旧 auth 参数不影响新链 |
| C3 | 空链、未知/重复来源、未知字段、必需参数/密钥缺失：启动失败 |
| C4 | 有参数表但不在 chain：不启用、不读取密钥、不暴露登录入口 |
| A1 | Gateway 有效、错误签名、重复 header、错误 issuer/audience/tenant：保持原契约 |
| A2 | 每种内置 OAuth 来源：完成假 provider 登录后，经真实会话验证调用 protected route 成功 |
| A3 | 多 OAuth：有效 GitHub 会话跳过支付宝来源；provider 未启用返回 403 |
| A4 | 无 cookie、空/重复/伪造/过期/撤销 cookie 分别得到未匹配或明确 401，不意外降级 |
| A5 | 从真实 DB store 的 DbPlugin 查询/执行边界注入故障，经 RepoUserIdentityPort 和实际 HTTP 消费方得到 503；不存在记录仍为 401，不能将故障折叠为 None；同时覆盖跨环境/来源不一致绑定 |
| A6 | chain 顺序优先于旧 priority；先失败不回退、先成功不检查低优先级来源 |
| A7 | 不跨来源合并身份；Bot 不提升为 owner Human；跨 owner/资源访问仍拒绝 |
| R1 | OpenAPI/internal protected routes 和 connection-token route 都真实调用新来源并传递同一规范化身份 |
| R2 | public routes 保持原有契约，认证后仍执行邀请门禁和资源授权 |
| O1 | 登录枚举、callback、user、refresh、logout 都消费新装配；禁用 provider 无法登录或刷新 |
| O2 | state/provider/callback/flow 不匹配、state 重放、nonce 缺失/重复/过期、错误 redirect 和持久化失败均拒绝；无秘密进入日志 |
| O3 | 新旧 cookie 覆盖/单会话失效、不同密钥下重登录行为有明确回归测试 |
| O4 | 浏览器 A 生成且尚未消费的合法 callback 在 B 中拒绝，provider exchange/会话写入调用数为 0，A 的 state 不被 B 消费；A 正常完成后临时 Cookie 清理且登录 Cookie 保留 |
| O5 | 新批次旋转 nonce 使旧 URL 无效；并发 callback 最多一次兑换；成功与兑换失败均清理已匹配批次，TTL 清理和多 Set-Cookie 正确 |
| O6 | 用 barrier 精确控制 logout(A)→refresh 提交、refresh(A→B)→logout(A) 两种时序；logout 成功后 A/B 均不能访问；覆盖 legacy/V1 交叉写入组合 |
| O7 | refresh 与重新登录 C 竞争、旧 A 迟到 logout、双 refresh 竞争；C 不被旧会话覆盖/撤销，双 refresh 最多一个提交，失败者无 Set-Cookie；同秒刷新不生成相同凭据 |
| O8 | DB 持久化/事务故障不返回 logout 成功或 refresh 成功；UPDATE 0 行不是 Applied；Memory/SQLite conformance 和真实 MySQL 事务验证分别记录，不以构造器类型相同替代原子性测试 |
| O9 | 全部共享生产写入方改用新操作；无条件 update_token 调用被检测阻止；无 sid/revision 历史会话要求重登；发布演练不混用旧写入二进制 |
| S1 | Cookie unsafe 请求缺失/非法 Origin 返回 403；读取 delivery 实际成功来源，AuthenticatedCaller 结构保持不变 |
| S2 | Gateway 优先成功 + 附带 Cookie + 无 Origin 不被 Cookie 策略误拦；OAuth 优先成功 + 有效 Gateway + 无/非法 Origin 仍 403，且不回退 Gateway；覆盖主与 connection-token router |

Conformance 测试必须经过消费方并记录 fake 插件/身份服务调用，证明来源被调用、低优先级被正确跳过、拒绝请求未进入业务服务。基础测试使用 fake OAuthProvider、内存身份服务和测试 SecretAccessPort，不依赖真实第三方网络；A5/O8 必须额外通过真实 DB-backed store 与故障注入 DbPlugin，不能只测高层 fake。并发测试用明确同步点而不是 sleep 推测时序，跨两入口/两个 store 实例验证 DB 原子性。真实 provider 登录 smoke test 是单独人工证据，不能以 mock 通过代替。

实施时至少运行 bcs-api-http、bcs-auth-api、bcs-auth-oauth、相关 provider、身份服务及 bootstrap 的相关 Cargo 测试；变更契约补充 conformance suite；运行仓库架构/配置门禁和涉及 BCS 的统一 singlebox gate。具体命令与执行结果由后续 implementation plan / PR 记录，本草案不声称已运行这些测试。

## 11. 待评审确认的边界

本草案推荐以上默认方案，但以下两项不是已经批准的需求缩减：

1. 首期是否接受 Gateway + 四种 OAuth 来源（alipay/github/google/wechat），将直接 Bot-session 插件的可信身份补全及 Local-mock 的显式开发模式接入留给后续？
2. 是否接受复用 bcs_session 带来的登录互相覆盖，以及第 8.4–8.6 节要求的全部共享写入方安全迁移、schema/JWT 演进与统一重登，还是要求新旧会话完全隔离？

在确认前，不进入实施计划；如果答案改变，先修订第 5 节或第 8.3 节及相应验收标准。跨站 cookie 需求若存在，也必须先落实防 CSRF 方案再实现。

## 12. 下游 OCB 内部 overlay 迁移评估

### 12.1 核查范围和限制

已静态核查相邻 OCB 仓库的 `src/bcs-internal/configs`、内部 bootstrap 和 auth plugins。本节只记录对公共设计的消费方影响，不把内部实现、SDK、服务地址或部署密钥引入 public BCS。示例 Secret 名称为公开逻辑示例，不是内部环境的实际引用。

OCB 通过 `ocb-public/src/bcs` 的 Cargo path dependencies 链接公共实现，不是直接依赖本次编辑的 Avernet 工作目录。检查时 OCB 的 `ocb-public` 子模块尚未初始化（git submodule status 为前导 `-`，目录为空），因此不能确认其锁定版本与本工作区的 API 完全一致，也不能编译内部 workspace。以下合并分析使用当前 public `config_loader` 的“对象深合并、数组整体替换”规则；内部 bootstrap 测试注释也确认该约定。

### 12.2 当前五份配置的认证职责

| 文件 | 当前旧 auth | 建议的新 V1 配置变更 |
| --- | --- | --- |
| `bcs-config.toml` | agentpass/cookie/session；含 auth_sdk、Gateway 签名 Secret 引用 | 初始保持兼容；显式迁移时在此新增 api.auth gateway-only 和 api.auth.gateway |
| `bcs-config-local.toml` | local/session；Mock user/header；Secret provider 为 env | 不把旧 Mock 隐式复制到新链；继承 Gateway 时显式提供本地 env Secret 映射 |
| `bcs-config-dev.toml` | local/agentpass/cookie/session；允许 Mock header | 首先继承 gateway-only；内部插件完成适配后才覆盖 api.auth.chain 灰度验证 cookie/agentpass |
| `bcs-config-pre.toml` | 未覆盖 auth，继承 base | 可不新增 auth 段；在 dev 验收后选择性覆盖新的 api.auth.chain |
| `bcs-config-prod.toml` | 显式 agentpass/cookie/session，与 base 相同 | 保留旧段；默认继承 gateway-only，验证后才启用内部直连来源 |

所有现有配置均没有 api.auth。模拟 base + 各环境 overlay 后，四个环境均继承原 Gateway Secret 引用；local 只是把 Secret provider 切成 env，并没有自动删除原 Gateway 引用。

`alipay` 是公众支付宝 OAuth 登录来源；内部员工 SSO 的插件名是 `cookie`，内部 Bot JWT 的插件名是 `agentpass`，不能因为都在蚂蚁环境就把这两个来源配置成 `alipay`。

### 12.3 迁移路径一：继续由 Gateway 认证（推荐先落地）

**可以不改五份配置**：新 public runtime 保留第 4.3 节的缺省兼容模式即可。

希望显式使用新 key 时，在 base 增加以下配置形状：

```toml
[api.auth]
chain = ["gateway"]

[api.auth.gateway]
issuers = ["gateway", "backend"]
audience = "bcs"
key_id = "bare"
signing_key_secret = "principal-signing-key"
```

部署时 `signing_key_secret` 应复制原 `[gateway_principal].signing_key_secret` 的实际逻辑引用，而非直接使用示例值；issuer/audience/key_id 应与升级后实际签发方和验证配置一致。默认相同并不免除兼容性检查。

保留原 `[auth]`、`[auth_sdk]`、`[gateway_principal]` 与 `[group_session_ws]`：旧链和 SDK 仍有消费方，connection JWT 签发配置也不是 Gateway 入口验证配置。两个用途即使当前引用同一密钥，也不能直接删掉其中一段。

- dev/pre/prod 无需重复完整 api.auth 配置，按 base 继承即可；有差异时按环境覆盖。
- local 可继承新的 gateway-only，但需要 env SecretAccessPort 能解析该逻辑引用。本地未提供密钥时应启动失败，不能因旧 local mock 已启用就跳过 V1 鉴权。
- 新段启用后不再使用旧 `[gateway_principal].signing_key_env` 兜底；若部署过去只依赖该环境变量，须显式配置 Secret provider 映射，不能只复制字段名。
- 在 base 新增 api.auth 会影响所有 overlay；从某份环境文件中删掉 api.auth，并不会移除 base 的新段。环境级试点宜先仅向该环境 overlay 增加完整新段；整体回滚应移除 base 中的新段及相关 overlay 覆盖。

这条路径不要求内部 cookie/agentpass 业务实现变更，但需要升级 OCB `ocb-public` gitlink 到已实现并验证新契约的版本，重建内部 runtime，并执行配置/编译回归；如依赖图变化，同步内部 Cargo.lock。只修改当前 Avernet 工作树不会让 OCB 二进制获得新 key。

### 12.4 迁移路径二：V1 直接接受内部 Cookie / AgentPass

目标 chain 可以是 `['gateway', 'cookie']`，进一步支持完整 Bot 身份后才是 `['gateway', 'agentpass', 'cookie']`。这些是目标来源组合，不是当前可直接生效的配置。

参数位置应遵循用户要求：

- `[api.auth.cookie]`：独立的 client_id、app_key、app_name、secret_key_secret、remote_server_domain、use_remote_login_check 等内部 SDK schema。
- `[api.auth.agentpass]`：独立的 app_name、app_key、secret_key_secret、remote_server_domain 及该插件确实需要的认证参数；不得将任意内部配置字段塞入公共 BcsConfig。
- `[api.auth].trusted_browser_origins`：启用 cookie 来源时必须配置；IAM_TOKEN 同样带来写操作 CSRF 风险，不仅 bcs_session 需要防护。
- 保留旧 `[auth_sdk]` 给旧消费者，新插件参数不能以旧配置作为隐式 fallback。

具体字段是否全部必填由内部 SDK 严格适配契约确定；该路径尚不是完成定义的实施 schema。特别是 agentpass 的可信 tenant/app/agent_code 来源尚待补全，不在没有证据时承诺直接 Bot 接入。

这条路径需要 OCB 代码修改，主要触点如下：

| 内部文件/模块 | 已观察到的限制 | 所需适配 |
| --- | --- | --- |
| `crates/bootstrap/bcs-internal/src/main.rs` | 加载 public BcsConfig、解析 secrets，随后无条件按 auth_sdk 初始化两套 SDK | 保留旧初始化；增加新 registry/配置校验衔接，必要时校验 SDK 配置冲突；保持 composition root 薄，不复制 public router 装配 |
| `crates/plugins/bcs-auth-cookie/src/lib.rs` | 只注册旧 AuthPluginFactoryRegistration；参数来自全局 ConfigManager；认证失败返回 None | 保留旧注册；新增 V1 factory/schema；严格区分缺 cookie、拒绝、SDK 故障；规范化可信 staff identity |
| `crates/plugins/bcs-auth-agentpass/src/lib.rs` | 只注册旧 factory；OnceLock 保存 SDK 参数；多种错误被折叠为 None；Bot 元数据不足 | 新 V1 注册、可观测错误分类、配置注入和可信 Bot 身份补全；明确 tcauthmng→ACM 何时允许 fallback，拒绝或故障不能伪装未命中 |
| `Cargo.toml` / `Cargo.lock` | path 依赖 public 子模块；只链接当前注册 | 升级 public 契约；按新边界补依赖；不能通过把内部 SDK 加到 public workspace 来解决 |
| bootstrap / plugin tests | 当前主要覆盖旧链及旧配置 | 增加新链注册命中、真实消费方调用、配置隔离、错误终止、身份和 CSRF 回归 |

其中 Cookie SDK 返回 Option、AgentPass resolver 将超时/错误响应也折叠为 Option，不能在最外层把每个 None 都称作“无凭据”。SDK 若无法区分故障与无效用户，需要修改适配入口获得错误证据，或明确 fail-closed 的统一失败映射并修订错误契约；不能宣称已实现 401/503 精确分类。

### 12.5 公共 spec 必须补齐的外部注册入口

第 6 节的来源 registry 必须支持下游注册独立 V1 factory，而不是 public bootstrap 对 gateway/alipay/github 做封闭 match。建议新增与旧 AuthPluginFactoryRegistration 并存的注册契约（最终名称在实现计划确定），至少声明：

1. 唯一来源名和配置 schema / validator；schema 发现必须发生在配置最终校验之前，不能配置先拒绝内部插件，后面才加载 factory。
2. 该来源支持的凭据能力及本次成功的实际 credential_kind，按第 7.4 节进入 delivery metadata；不能仅凭静态插件能力或请求附带 Cookie 触发/跳过 CSRF。
3. 已验证的插件专属配置和受限构造上下文；密钥经 SecretAccessPort 解析，不由运行时插件临时读 env。
4. 返回实现新成功结果签名的公共 PrincipalVerifier 对象或对应严格认证适配器，保留 source/credential_kind；内部实现不依赖 public HTTP 私有模块，不向 core 引入 SDK。
5. 对重复注册、未知来源、初始化失败的启动期错误语义；旧 chain 与新 chain 的注册命名空间独立，旧注册不会自动启用新来源。

公共 SDK 初始化前仍需配置解析，因此必须设计两阶段“发现 schema→解析/验证配置→解析 Secret→构造插件”顺序。不能只给构造函数增加 TOML map，却继续让现有 pre-bootstrap 秘密解析器忽略新参数。

### 12.6 进程全局 SDK 是独立参数的实际约束

当前 Cookie SDK 使用进程全局 ConfigManager 和初始化标记；AgentPass 使用 OnceLock 保存 SDK 参数。第二次初始化不会切换成新配置。因此仅新增 `[api.auth.cookie]` 和 `[api.auth.agentpass]` 并再调用一次 init，会出现“新配置看起来生效，实际使用旧参数”的错误。

推荐最小过渡策略：新旧链需要相同 SDK 时，只允许显式参数相同的配置共享该进程 SDK，并在启动时验证；比较解析后的有效参数且不输出秘密，不能仅比较 Secret 引用字符串。若有效参数不同，启动报配置冲突，不能静默忽略新值。新配置仍必须显式提供参数，不意味着可以隐式继承 auth_sdk。

真正支持同进程不同 SDK 参数，需要确认 SDK 有实例化客户端能力并注入每个来源；如果 SDK 只有全局态，必须保持上述约束或另进程隔离。该能力未经 SDK 验证，本节不承诺能通过薄 wrapper 消除限制。Global SDK 还可能有旧链以外的消费者，判定是否使用旧参数不能只检查 `[auth].chain`。

### 12.7 验证和发布顺序

1. 先完成 public 新配置/registry/严格结果契约；保持缺省兼容和旧 auth 测试。
2. 初始化并升级 OCB public 子模块到正确版本，再运行内部 workspace 构建；不要把子模块指向开发者绝对目录绕过依赖边界。
3. 先在单环境 overlay 试点显式 gateway-only，验证 Secret 映射、原 Gateway 调用、主 V1 和 session-token router。
4. 若需要内部直连，完成 cookie/agentpass 的上述代码与身份契约，再逐环境启用；不直接把旧 auth.chain 复制到 api.auth.chain。
5. 测试最终合并的 base + local/dev/pre/prod 配置，而非只独立反序列化每份 overlay；检查数组替换、旧链配置/顺序不变且共享 OAuth 安全收紧生效、Local 不读取 Mist、未启用插件不解析 Secret。
6. OCB 至少运行 check-architecture.sh、bcs-internal 的 cargo check/test、内部 auth plugin conformance 和 check-p4.sh；真实 SDK/服务验证需内部环境另行提供证据。

本轮实际运行内部 check-architecture.sh：文档标记检查通过，随后以 `missing submodule checkout: ocb-public` 失败；未运行 Cargo 或内部真实服务测试，未初始化子模块、未修改 OCB 配置/代码。

## 13. 审阅意见处理记录（2026-09-18）

依据用户提供的 `2026-09-18-v1-api-auth-plugin-chain-design-review.md` 修订；该审阅文件保留在用户临时目录，不建立依赖个人目录的仓库链接。

| 审阅项 | 本次设计修订 | 验收对应 |
| --- | --- | --- |
| R1 / P1 浏览器绑定缺失 | 第 8.4 节：nonce 临时 Cookie、state 记录、必需 Service API 输入/输出、原子匹配/消费及清理；旧入口同步 | O2、O4、O5 |
| R2 / P1 共享会话并发复活 | 第 8.5 节：session_id/revision、CAS rotate、按实例 revoke、全部共享写入方迁移；第 9 节明确兼容和发布限制 | O6–O9 |
| R3 / P2 Repository 吞错误 | 第 8.6 节：严格 repo Result、Memory/SQL/bridge 传播；第 9 节加入底层变更，真实 DB 故障到 HTTP | A5、O8 |
| R4 / P2 实际来源缺失 | 第 7.4 节：delivery 专用结果与可信 source/credential_kind；不污染业务 caller，按实际来源做 CSRF | S1、S2 |

四项均已转成明确的设计约束、受影响契约和验收场景；这是文档整改，不表示实现已完成或风险已通过运行时测试。第 11 节产品边界仍待确认，不因本次审阅自动视为批准。

## 14. 参考与验证证据

- [Gateway Principal wire contract](../../../api-contracts/v1/gateway-principal/contract.md)
- [既有 OpenAPI Auth Facade 设计](2026-08-28-openapi-auth-facade.md)
- [BCS 模块规则](../../../CLAUDE.md)
- [HTTP adapter context](../../../crates/adapters/http/bcs-api-http/CONTEXT.md)
- [Auth Plugin API context](../../../crates/plugin-api/bcs-auth-api/README.md)
- [Service API context](../../../crates/service-api/bcs-service-api/CONTEXT.md)
- [架构宪法](../../../../../docs/arch/arch.rules.md)、[CI 约束](../../../../../docs/arch/ci.enforce.md)
- [Context boundary 格式](../../../../../docs/arch/context-boundary-format.md)、[Conformance 要求](../../../../../docs/arch/protocol-contract-tests.md)

已检查系统 ADR 目录：现有条目主要涉及 Skills/Engine 生命周期，未发现专门约束本次 BCS auth 装配的已接受 ADR；本草案不替代系统 ADR。实际边界依据上述架构规范、模块契约和代码。

历史调研证据包括仓库静态阅读、文档一致性检查、Markdown 相对链接检查、OCB 五份 TOML 解析及四种环境深合并模拟；内部架构脚本的阻塞结果见第 12.7 节。本次审阅整改另静态核对 OAuth state、AuthService 命令、UserIdentityRepoPort、DB/Memory store、identity_wiring、旧 callback/refresh/logout 及 delivery verifier，验证文档中四项整改的契约传播范围；仅运行文档检查。未修改运行时代码、未执行 Cargo 或 E2E、未验证真实 OAuth 登录。文档待评审，没有实现 PR/commit。
