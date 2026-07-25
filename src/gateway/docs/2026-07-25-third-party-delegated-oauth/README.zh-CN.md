# 第三方代表用户接入：OAuth 授权码 + PKCE 方案

**状态：** 草案 / 待评审
**日期：** 2026-07-25
**组件：** `src/gateway`（网关承载鉴权面；对外域名 `https://teamclawgw-pre.alipay.com`）
**范围：** 第三方服务器如何**代表我方某个终端用户**调用，而**全程不持有第一方会话令牌**。
**关联：** `src/gateway/docs/2026-07-21-auth-design.md` —— 本文把该设计 **§15「模式 C」（OAuth 授权码，"用 Avernet 登录"）** 具体化；那里已埋点但暂缓。

> English version: [`README.md`](./README.md)。
> **系统流程**（corp + community 时序图、授权模型、评审议程）：[`SYSTEM-FLOW.zh-CN.md`](./SYSTEM-FLOW.zh-CN.md)。

---

## 1. 背景与要解决的问题

我们的 OpenAPI（`/openapi/v1/*`，由网关承接）对第三方开发者开放，由第三方**服务器**调用。当前一次第三方调用要带**两个令牌**：

1. `Authorization: Bearer <api_key>` —— 由 baas 校验，得到 App 及其**租户**（`APIKeyRecord.tenant`）。
2. `IAM_TOKEN` cookie —— 经 IAM/BUService 解析出**终端用户**。

现状实现见 `src/baas/src/secbaas/community/adapters/web/routers/open_api/dependencies.py`（`get_api_key_from_header`、`get_iam_token_from_cookie`、`get_bot_chat_context`）。

**为什么这是设计异味 —— 而不只是"多了一个令牌"：**

- `IAM_TOKEN` 是**第一方会话凭证**（与真人在 `iam.alipay.com` 登录我方 Web 应用时下发的是同一个 cookie）。要求**第三方服务器**去取得并转发它，就意味着第三方要以某种方式持有我方用户的活跃会话。这是典型的**混淆代理 / 令牌透传（confused deputy / token passthrough）** 反模式：第一方凭证泄漏出了我方信任边界。
- 终端用户在**每一次调用**都要重新经 BUService 解析，而不是只验证一次。
- 没有授权（consent）记录，用户也无法查看或**撤销**某个第三方的访问。

这并非不可避免。正确形态是**一个受限、可撤销的令牌** —— 标准的**三方 OAuth 2.0**。

## 2. 目标与非目标

**目标**

- 第三方服务器每次 API 调用只出示**恰好一个**凭证（`Authorization: Bearer <access_token>`），永不带 `IAM_TOKEN`。
- 真人只在首次授权时**交互式认证一次**，走既有的 `iam.alipay.com` 登录（IAM/BUService 仍是唯一身份权威）。
- 显式、可撤销的**用户授权**（撤销时延有界 —— 见 §8.3）；租户 + 用户 + App + scope 都是**令牌内的 claim**，不是并列的多个令牌。
- 资源归属仍锚定 App 的 `developer_org_id` / `tenant`，借来的用户句柄绝不能越界读到别的组织的数据。

**非目标**

- **不**新建账号/身份系统。认证（"这是谁"）仍归 IAM/BUService。
- 纯机器对机器调用（无终端用户）、以及"代表第三方**自有**用户"（不透明句柄）已由既有 `app_key` 设计覆盖（auth-design.md §5–§6）；本文只讲**代表我方用户**这一路。

## 3. 核心思路

现状是每次调用都靠转发 `IAM_TOKEN` 来解析用户。改为在**授权时解析一次**，其后由所签发的 **access token 自带该身份**。IAM/BUService 只在浏览器里验证真人一次，而不是每次机器调用都验。此后线路上只有一个 bearer 令牌，别无其它。

这就是 OAuth 2.0 **授权码 + PKCE**（"用 Avernet / teamclaw 登录"），由网关充当（或前置）授权服务器。

## 4. 角色与域

| 角色 | 本设计中 | 真实域名 |
| --- | --- | --- |
| 第三方服务器（"client"） | 想代表其用户 Alice 办事 | （第三方自有） |
| **网关鉴权面** | 承载 `/authorize` + `/token`，签发我方令牌 | `https://teamclawgw-pre.alipay.com` |
| **IAM 登录** | 真人实际登录处 | `iam.alipay.com` |
| **BUService** | 把 IAM 会话解析成我方用户身份 | （内部） |
| 后端（agentclaw） | 消费令牌；签发下游 caller 凭证 | `agentclaw-prod.alipay.com` |

关键：我们**已经**为自有前端做的"浏览器跳转到 `iam.alipay.com`"这一步，**就是**"认证真人"的步骤。本设计复用它，而非替换它。

## 5. 端到端流程

```
首次（授权绑定 —— 每个用户一次，在浏览器里）：

  client ──把 Alice 浏览器 302──▶ teamclawgw-pre.alipay.com/authorize
                                     │
          步骤 1（身份）：我方域下有 IAM cookie 吗？
              无  ──302──▶ iam.alipay.com  ──登录──▶ 返回（cookie 已种）
              有  ──▶ 经 BUService 解析 cookie ──▶ 得知这是 Alice
                                     │
          步骤 2（授权 —— 我方 DB，键为 用户+client+scopes）：
              已授权？ ─ 是 ─▶ 跳过授权页
                      └─ 否 ─▶ 展示授权页 ─▶ Alice 点"允许" ─▶ 保存
                                     │
          步骤 3：把浏览器 302 回 client，携带一次性 CODE
                                     │
  client 服务器 ──POST /token（code + client_secret + PKCE verifier）──▶ 网关
  网关 ──▶ 返回 { access_token（短期）, refresh_token（长期）}
  client 把两个令牌存到 Alice 账号名下


稳态（其后每次 API 调用 —— 无浏览器、无 IAM cookie）：

  client 服务器 ──▶ /openapi/v1/...   Authorization: Bearer <access_token>
  网关校验令牌 ──▶ "Alice，租户 X，scope Y" ──▶ 转发


令牌刷新（access token 约 15 分钟过期时，静默进行）：

  client 服务器 ──▶ /token（grant_type=refresh_token）──▶ 新的 access_token
```

- client 是否发起绑定，取决于**它自己**的存储（"我有没有 Alice 的令牌？"），绝非窥探我方 cookie（浏览器按域隔离 cookie，client 看不到我方 cookie）。
- `/authorize` **只**在首次绑定或重新绑定（refresh 过期、授权被撤销、需要新 scope）时命中，**不是**每次 API 调用。

## 6. `/authorize` 决策树

**先身份、后授权** —— 不知道是不是 Alice，就无从问"Alice 授权了吗"。

```
步骤 1 —— 这是谁？（身份）
    teamclawgw-pre.alipay.com 上有 IAM cookie 吗？
       无  → 302 到 iam.alipay.com → 带 cookie 返回 → 继续
       有  → BUService 解析 cookie → subject = Alice

步骤 2 —— Alice 是否已就"该 client + 该 scope"授权？（我方 DB）
       是 → 跳过授权页
       否 → 渲染授权页 → Alice 允许 → 持久化 grant(user, client, scopes)

步骤 3 —— 签发一次性授权码，302 回 client 注册过的 redirect_uri
```

"已记住的授权"只是让我们**跳过页面**；每次经过 `/authorize` 仍会签发新的一次性 code（因而是新的令牌对）。

## 7. 为什么浏览器带回的是一次性 code，而非令牌

回跳 client 时携带的是短期、一次性的**授权码**，而非 access token。随后由 client 的**服务器**用该 code 走后端直连换取真正的令牌，其间出示 `client_secret` 与 PKCE `code_verifier`。

若令牌本身出现在浏览器 URL 里，会泄漏进浏览器历史、服务器日志、`Referer` 头。而 code 对窃取者无用（缺 client secret），且用一次即失效。**PKCE**（`code_challenge`/`code_verifier`，S256）把 code 绑定到发起流程的一方，封堵拦截攻击。

## 8. Access token 内容

建模为签名 JWT，让网关可无状态校验（正是既有设计 §7.1 想要的非对称签名接缝）：

```
iss: teamclaw-authz
aud: teamclaw-openapi             # 面向 CLIENT 的令牌 audience（见 §8.1 —— 两个凭证）
sub: <我方用户 id / 工号>          # 被代表的真人 —— 仅用于授权 + 归属/审计，绝不作数据访问锚点
tnt: <租户 id>                    # CLIENT 的租户（App 的 developer_org）；终端用户自己的租户永不被查询
azp: <client_id>                 # 发起调用的第三方 App
org: <developer_org_id>          # 资源归属锚点
scope: "bots:chat bots:read"     # 用户授权的确切范围
exp / iat / jti
```

现状两三个令牌所携带的一切，如今都是一个凭证里的 claim。

### 8.1 两个截然不同的凭证 —— 切勿混为一谈

系统里存在两个凭证，绝不能混淆：

- **OAuth access token** —— 面向 client、短期（约 15 分钟）、`aud: teamclaw-openapi`，仅在网关边界校验；
- **转发的（内部）Principal** —— 内部、秒级有效、`aud: <具体后端>`，即 auth-design.md §7.1，由网关签名、后端校验。

后端**永不**看到或校验 OAuth access token；它只校验**网关签名的 Principal**（§9）。这样 OAuth 令牌不会越入后端信任边界，且后端校验在所有策略间保持统一。

### 8.2 `tnt` 与 `sub` 的含义（以及不含义）

`tnt` 是 **client 的**租户（App 的 `developer_org`）。终端用户自己的租户**永不**被查询，`sub` 也**不是**资源归属锚点 —— 它只用于**授权与归属/审计**。这正是让跨租户委托在构造上不可能（§2 目标）：一次委托调用只能触及 **client** 租户内、锚定于 `org`/`tnt` 的数据。

具体地，这里的"代表我方终端用户"意为 **"在 client 的租户内、以一个已验证且已授权的真人在册的方式行事"** —— 它**不**授予 client 访问该用户**自有的**跨租户数据。后续读者不得假设 `sub` 决定数据访问范围。

### 8.3 撤销模型与时延

纯无状态 JWT 在 `exp` 之前无法被拒（网关仅凭签名校验），因此"可撤销"与"无状态校验"存在真实张力。三个选项：

- **不透明令牌 + 内省** —— 即时撤销，但每次 API 调用要查一次存储（网关在热路径上有状态）。
- **JWT + 一次廉价撤销检查（推荐）** —— 保留签名校验，同时查一个按 `(user, client)` 的"最小有效 `iat`"水位（或小的 `jti` 黑名单），短 TTL 缓存 → 近即时撤销，基本无状态。
- **纯 JWT，不检查** —— 撤销只杀 refresh + 授权；已签发的 access token 活到 `exp`（≤15 分钟），故"撤销"实际意为"15 分钟内"。

推荐中间方案。无论选哪个，都要**写明由此产生的撤销时延**，以免"可撤销"（§2、§11）过度承诺。

## 9. 网关校验 → `DelegatedPrincipal`

即 auth-design.md 中暂缓的 `oauth_bearer` 策略（§5 策略表、§15）。它接入既有策略机制（`gateway/community/plugins/authn/`、`spi/authn/`）：

```python
# gateway/community/plugins/authn/oauth_bearer/_strategy.py  （示意）
class OAuthBearerStrategy(AuthStrategy):
    name = "oauth_bearer"

    async def build(self, creds, params):
        tok = _bearer(creds.headers.get("authorization"))
        if not tok:
            return None                                # 不适用 → 交给下一个 OR 分支
        claims = await self._verifier.verify(tok)      # JWKS/内省；非法 → AuthError
        return DelegatedPrincipal(
            tenant=claims.tnt,
            app=ThirdPartyApp(client_id=claims.azp, developer_org_id=claims.org, ...),
            subject=AuthenticatedUser(id=claims.sub, tenant_id=claims.tnt),
            scopes=frozenset(claims.scope.split()),
        )
```

随后 runner 照常裁决 `required_scopes ⊆ granted_scopes`。`DelegatedPrincipal` 是 §15 判别联合里同时携带**App 与已验证 subject** 的成员 —— 这是 `AppPrincipal.on_behalf_of_opaque`（**未验证**句柄）表达不了的形态。

**然后要重新签名 —— 不要把 OAuth 令牌透传下去。** `oauth_bearer` 校验 access token 并构建 `DelegatedPrincipal` 只是网关边界这一步。网关随后把该 `DelegatedPrincipal` **重新签名为 auth-design.md §7.1 的短期内部 Principal**（`aud: <具体后端>`，秒级有效）再转发；后端校验网关签名的 Principal，**绝不**校验 client 的 OAuth access token（见 §8.1）。从后端视角，每种策略（`first_party_user` / `app_key` / `oauth_bearer`）都收敛为"校验一个网关签名的 Principal"。

### 9.1 与配置驱动转发（PR #420）的落位

`/authorize`、`/token`、`/revoke` 与授权界面是网关**本地**端点 —— 它们*不*在 `/openapi/v1/<domain>` 之下，也**不**应被配置驱动转发的 catch-all 处理。该 catch-all 把版本基路径下的一切路由到某个域的上游、并拒绝未知域，因此这些路由必须注册在其**之前 / 被排除在外**（与 `/health`、`/docs` 同样待遇），否则会被当作未知域拒绝。

与 PR #420 的另两点对齐：

- `oauth_bearer` 在 `route_security.yaml` 里作为诸多**策略候选之一**注册（如 `[oauth_bearer, app_key]`），契合"网关拥有鉴权策略选择"。重新签名后的 `DelegatedPrincipal`（§9）才是后端 per-route 依赖用于授权的对象。
- 本设计让网关成为一个**有状态的授权服务器**（授权/grant 库、refresh 状态、授权界面），与本应无状态的代理并存。要把这份授权状态做成**有界、可分离**的组件，让转发热路径保持无状态。

## 10. 下游凭证 —— 在 *client 的租户内、归属于* 用户 行事

当请求抵达 runtime、需要调用 BaaS/MCP 时，它在 **client 的租户内、归属于 Alice** 行事（依 §8.2 —— 绝不使用 Alice 自己的租户或权限）。复用后端接缝 `CallerIdentityService.exchange_caller_identity()`（`src/backend/src/agentclaw/community/core/caller_identity/service.py:328`）。步骤 2 记录的授权**即**预授权；所签发的 caller 凭证经 `runtime_updater.update_caller_identity(...)` 装入 runtime，**绝不回吐给第三方**。该凭证必须锚定于 client 的 `org`/`tnt` —— **不得**悄悄把用户的租户或权限重新引入。

需要一处签名改动：`exchange_caller_identity` 现取 `iam_token: str`。OAuth 路径下我们不持有 Alice 的活跃 `IAM_TOKEN`，因此 `CallerTokenProviderProtocol` 需要一个从 `(service_credential, subject_id, tenant, grant_ref)` 签发的重载，而非转发用户令牌。BUService 能否在没有用户活跃令牌的情况下签发此类委托凭证，是关键外部依赖 —— 见 §12。

## 11. 从现状两令牌 baas 路径迁移

| 现状（`open_api/dependencies.py`） | 目标 |
| --- | --- |
| `Bearer <api_key>` → App + 租户 | client 成为 **OAuth client**；App + 租户 + 用户 + scope 都进 access token |
| `IAM_TOKEN` cookie → 用户（每次调用） | 用户在授权时**验证一次**；access token 携带 `sub` |
| `policy.allowed_bots` fail-closed 白名单 | 保留，作网关粗粒度 scope / 资源白名单 |
| （无授权、无撤销） | 显式授权记录；可撤销（时延有界 —— 见 §8.3）；refresh token 轮换**并带重放检测**（§13） |

改造是增量的：auth-design.md 的 `app_key`（纯 App / 不透明 on-behalf-of）路径不动；本文只新增**代表用户**这一路。

## 12. 需优先拍板的开放问题

1. **阿里 IAM 是否已作为第三方应用的 OAuth/OIDC 授权服务器**（"开放平台"能力）？这是**配置 vs 自建**的分岔：
   - **是** → 在 IAM 注册 client；由 IAM 跑 `/authorize` + 授权 + `/token`；网关只**校验** IAM 签发的令牌。自建量最小。
   - **否** → 网关自建本文的轻量 `/authorize` + `/token`，`iam.alipay.com` 跳转仅用于真人登录这一步。
2. **令牌格式** —— 签名 JWT（无状态校验，契合 §7.1 签名接缝）vs 不透明 + 内省（更易撤销）。建议：JWT access token + 服务端 refresh/授权状态。
3. **委托凭证签发** —— BUService 能否从 `(服务凭证 + subject + 已记录授权)` 签发"代表 subject"的凭证，而无需用户活跃令牌（RFC 8693 令牌交换 / on-behalf-of）？若不能，则在授权时存一份可赎回的委托 grant；用户令牌依赖完全留在我方信任边界内。（auth-design.md §15 也标注了同一"sender-constrained 令牌"疑问。）
4. **授权粒度与有效期** —— 按 scope 授权、授权过期、以及 client 申请新 scope 时的重新授权触发。
5. **scope 词表是硬前置。** 按 scope 授权、令牌的 `scope` claim、runner 的 `required ⊆ granted` 校验都依赖一份已定义的 **scope 词表**（合法 scope 串 + 各自授予什么）。该词表目前在 auth-design.md 与 gateway-v1 spec 里被暂缓；它必须**先于/随** 本设计落地 —— 没有它就无法渲染有意义的授权页，也无法强制 scope。

## 13. 增量落地

1. **MVP：** 只实现 `authorization_code + PKCE` 的 `/authorize` + `/token`（不做 implicit / client-credentials / device）。用网关 Principal 密钥对签发 JWT access token。接上 `oauth_bearer` 策略。仅此一步即可把 `IAM_TOKEN` 从第三方面上移除。
2. 加 refresh token 轮换**并带重放检测** + `/revoke` + 授权管理界面（用户可查看/撤销 App）。重放检测正是让轮换有意义的机制：既然每个轮换后的 refresh token 只能用一次，对一个已消费的 refresh token 的重放就意味着两方持有它（其一被盗）→ **吊销该 grant 的整条令牌家族**（所有 refresh + access）并强制重新授权（OAuth 安全 BCP）。没有它，被盗的 refresh token 会在其整个生命周期内静默可用。
3. 改造 `CallerTokenProviderProtocol` 支持无令牌签发，退掉委托路径里最后的 `iam_token` 依赖。

## 14. 术语

- **认证（Authentication）** —— "这是谁？" 归 IAM/BUService，不变。
- **授权（Authorization）** —— "该用户是否允许该 App 代表其行事，并给出证明令牌。" 即新增的薄层。
- **授权码（authorization code）** —— 浏览器回跳里携带的短期、一次性值；由服务端后端直连换取令牌。
- **access token** —— API 调用所用的短期、面向 client 的 bearer 凭证；以 claim 携带租户 + 用户 + App + scope。仅在网关边界校验（§8.1）。
- **转发的（内部）Principal** —— 短期、网关签名的凭证（`aud: <具体后端>`，auth-design.md §7.1），后端实际校验的就是它；与面向 client 的 access token 不同（§8.1、§9）。
- **refresh token** —— 长期、轮换的凭证，服务端直连用它签发新 access token，无需用户参与；轮换与重放检测配套（§13）。
- **PKCE** —— `code_challenge` / `code_verifier`（S256），把 code 绑定到发起方。
- **`DelegatedPrincipal`** —— 同时携带发起 App 与已验证终端用户 subject 的网关 principal（auth-design.md §15）。
