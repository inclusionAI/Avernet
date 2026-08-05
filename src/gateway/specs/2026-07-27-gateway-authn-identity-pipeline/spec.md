# Gateway 身份认证:多身份解析管线

**状态:** ready-for-agent(本地 spec,待评审;无外部 issue tracker,沿用 `specs/` 约定)
**日期:** 2026-07-27
**组件:** `src/gateway`(`gateway.community`,Python / FastAPI)
**关联:** 演进 `docs/2026-07-21-auth-design.md`;遵循架构宪法 Rule 1 / 7 / 14 / 19 / 25

---

## Problem Statement

今天的网关认证把**身份提取**和**权限校验**两件事绑在一起,且一种身份只能由一种固定的提取逻辑(`FirstPartyUserStrategy` 单写死读 `IAM_TOKEN/SSO_TOKEN/access_token` cookie)。这带来三个问题:

- **职责越界**:网关在内嵌 `scopes` 子集校验和 RBAC(`AuthPlugin.check_permission/is_allowed`)。但按设计 §11,资源级/细粒度授权本该留下游组件;网关只该"产出一个中立身份"。而现状里 `scopes` 永远是空集,这道校验实际是 no-op,既不生效又混淆职责。
- **配置错位**:per-endpoint 配置(`route_security.yaml` 的值)形如"OR-list of `{scopes, delegation}` 策略映射",把"要什么身份"和"要什么权限"耦合在一起。一个接口没法干净地表达"我同时需要一个 User、再可选地带一个 App"。
- **扩展僵化**:想给"User 身份"多加一种来源(比如除了会话 cookie,再加 OAuth bearer),只能去改 `FirstPartyUserStrategy` 内部,没有"一种身份多种提取插件、按链试、首达即返"的机制。且目前只有 `User` 一种身份落地;设计稿里的 `App` 和语境里需要的 `Bot` 都还没有真实实现。

从用户视角:平台要同时承接**人(User)**、**第三方应用(App)**、**机器人/Agent 自身(Bot)**三类调用方,一个接口还可能要求"同时带多种身份"(如某接口必须有一个真实用户、并可选携带一个 App 用于归属),而现有认证管线做不到。

## Solution

把网关认证**重塑为"纯身份提取管线"**,去掉权限校验和权限配置,引入两层提取模型:

1. **去掉权限校验**:删除 `StrategyParams.scopes`、`Delegation`、runner 的 scope 子集校验,以及网关侧对 `AuthPlugin.check_permission/is_allowed` 的依赖。网关只解析身份,不做 RBAC;资源级授权全部留下游组件(§11)。
2. **身份类型扩为判别联合 `User | Bot | App`**:落地设计稿里的 `AppPrincipal`/`ThirdPartyApp`,新增 `BotPrincipal`/`Bot`。每个 Principal 必带 `tenant`,类型相关字段必填非可选(§4.3,避免 `T|None`)。
3. **两层提取**:
   - **插件 `IdentityExtractor`(最小单位)**:先自判"认不认得这个凭证";不认得 → 返回 `None`(让链条继续);认得 → 提取,成功返 `Principal`(链返回),非法 → `raise AuthError`(硬失败、不回退,沿用 §5 这条最关键 seam)。
   - **身份 `AuthStrategy`(每类身份一个)**:跑该身份**系统启用插件**链,首达即返回 `Principal`;全链都不适用且无 raise → 返 `None`。
4. **per-endpoint 配置(重塑)**:`x-avernet-security`/`route_security.yaml` 的值从"OR-list of `{scopes,delegation}`"改为"身份集合,每个标记 `required`/`optional`"。
5. **系统级配置(新)**:每类身份启用哪几个插件及顺序(如 `user: [session_cookie, oauth_bearer]`)—— 对应"由系统配置这种身份启用哪几个插件"。
6. **per-identity 解析(替代原 OR/AND runner)**:对 endpoint 配置里的每个身份各跑其链;任意**必备**身份缺失(链返 `None` 且无 raise)→ 401;任意插件 hard-fail → 401;返回解析出的身份集合。
7. **解析出的身份集合在 forwarder seam 可用**:让 forwarder 能把身份下发下游。密码学签名(`PrincipalSigner`/JWT,§7.1)仍是独立 workstream,本 spec 只交付"解析出的集合 + seam",不交付签名。

从用户视角:一个接口可以声明"我需要 User(必备)+ App(可选)";调用方带的凭证会被对应身份的插件链解析;必备身份解析不出来就 401,可选身份没带就只当它不存在;多种身份可以并存。平台运维可以为每类身份独立配置启用哪些提取插件。

## User Stories

1. 作为一个**网关开发者**,我希望认证管线只负责"提取身份"而不再做权限校验,这样网关职责干净,授权逻辑全在真正拥有领域策略的下游组件里。
2. 作为一个**网关开发者**,我希望把 `scopes`、`Delegation`、runner 的 scope 子集校验整套移除,这样不会再有"校验永远过"的 no-op 误导后来者。
3. 作为一个**网关开发者**,我希望一个接口能配置"需要多个身份"(如 User + App 同时要求),这样能表达组合式访问控制。
4. 作为一个**网关开发者**,我希望接口能标记每个身份是**必备**还是**可选**,这样"必须有一个真实用户、可选携带一个 App"这类语义能直接写在路由声明里。
5. 作为一个**平台运维**,我希望对每一类身份独立配置"启用哪几个提取插件及顺序",这样加一种新来源(如给 User 加 OAuth bearer)不用改代码,改系统配置即可。
6. 作为一个**平台运维**,我希望同一类身份的多个插件按一条链执行,有一个解析到结果就返回,这样多种来源(会话 cookie / OAuth / SSO)能自然叠加而不互相干扰。
7. 作为一个**平台运维**,我希望每个插件在解析前先自判"认不认得这个凭证",这样不相关的插件不会被错误触发,链条行为可预测。
8. 作为一个**第一方前端用户**,我希望我用登录 cookie 调网关时,我的 User 身份被解析出来并随请求下发,这样下游组件知道"是哪个租户里的哪个人在调"。
9. 作为一个**第三方应用开发者**,我希望我用 `Authorization: Bearer <api_key>` 调网关时,我的 App 身份被解析出来,这样我能以应用自身身份管理资源。
10. 作为一个**第三方应用开发者**,我希望我同时携带 api_key 和一个终端用户句柄时,App 身份和(可选)用户句柄都能被识别,这样资源归属/审计能落到正确的主体。
11. 作为一个**Bot / Agent 所有者**,我希望我的机器人能以 **Bot 自身身份**调用网关(带 bot 凭证),这样 Agent 之间的调用和"人在调"能被区分。
12. 作为一个**Bot / Agent 所有者**,我希望 Bot 身份也是判别联合里的一等成员,带稳定的 `tenant` 和 bot 标识,这样下游能按 Bot 主体做归属与配额。
13. 作为一个**网关开发者**,我希望"凭证在但非法"(如坏 token)是**硬失败**、立即 401 且不回退到别的插件/分支,这样一个坏凭证永远不会被悄悄放过(沿用 §5)。
14. 作为一个**网关开发者**,我希望"可选身份没带"被视为"该身份不存在"而非错误,这样可选项不会抬高调用门槛。
15. 作为一个**网关开发者**,我希望"必备身份没带(链全不适用)"返回 401,这样必备项的缺失被明确拒绝而不是静默放行。
16. 作为一个**网关开发者**,我希望路由表里**没有任何规则命中**当前路由时 fail-closed 拒绝,这样未声明的路由不会变成"人人可调"。
17. 作为一个**网关开发者**,我希望身份类型用判别联合(`type` tag)建模,这样"USER 却带 app 字段"这类非法状态在类型层面就无法构造。
18. 作为一个**后端组件开发者**,我希望网关下发的身份是中立的规范化 Principal 集合,我按 `type` 各自投影成我的域 DTO,这样我的 core 永不 import 网关类型(§9, Rule 7)。
19. 作为一个**后端组件开发者**,我希望每个 Principal 都带 `tenant`,这样租户隔离不必我再二次推断。
20. 作为一个**代码评审/CI**,我希望每一个对外暴露的路由都能解析到一条身份要求声明(必备/可选),否则构建失败,这样路由与鉴权声明不会漂移(Rule 1, 契约即权威)。
21. 作为一个**代码评审/CI**,我希望每个 `IdentityExtractor` 和每个身份 `AuthStrategy` 都满足同一份 conformance 契约(`None`/`Principal`/`AuthError` 三态),这样新增插件有统一的行为底线(Rule 25)。
22. 作为一个**网关开发者**,我希望身份 Strategy 和插件本身**与 flavor 无关**,社区/企业差异只下沉到它们依赖的 SPI(AuthPlugin / ApiKeyValidator / TenantResolver / BotTokenValidator),这样加 sofa 实现不动策略/插件代码(Rule 14)。
23. 作为一个**社区版(bare)使用者**,我希望 bare 自带桩实现(硬编码用户、固定租户、本地校验),这样单盒开箱即能跑通最小链路。
24. 作为一个**企业版(sofa)使用者**,我希望 sofa 通过既有 `PluginAccessor`/`register_plugin_option` 挂载 BUService 实现后,认证管线零改动就能切到企业身份源,这样 flavor 切换不引入策略分支。
25. 作为一个**网关开发者**,我希望"加一种全新身份类型"需要同时新增一个 Strategy + 至少一个 Extractor,这样身份类型集保持封闭、小而可枚举(Rule 19, 两例之后再抽象)。
26. 作为一个**网关开发者**,我希望"一种身份的多个插件"是**有序链**且顺序由系统配置决定,这样来源优先级可调、可观测。
27. 作为一个**网关开发者**,我希望一个凭证能同时被多个身份类型的链各自尝试(如某 bearer 既可能是 app_key 又可能是 bot_token),每条链独立裁决,这样一个凭证可同时满足多个身份。
28. 作为一个**网关开发者**,我希望解析出的身份集合**在 forwarder seam 可用**,这样转发到上游时身份不会像今天这样被丢掉(当前 `_forward` 是裸 `await` 然后原样转发)。
29. 作为一个**网关开发者**,我希望本 spec 把"签名转发"明确留给 §7.1 独立 workstream,这样本 spec 的范围聚焦在提取管线,不绑定密钥分发/轮换。
30. 作为一个**平台运维**,我希望身份插件配置和路由身份要求配置是两份明确分开的配置(一份系统级、一份 per-endpoint),这样"启用了哪些提取器"和"这个接口要什么身份"不会耦合。

## Implementation Decisions

### 移除(职责收窄)
- 删除 `StrategyParams.scopes` 与 `Delegation` 枚举;runner 不再做 `params.scopes <= principal.scopes` 子集校验。
- 删除网关侧对 `AuthPlugin.check_permission` / `AuthPlugin.is_allowed` 的调用(当前本就无调用方);这两个方法保留在 SPI 上,供企业版下游/其它场景用,但**不在网关认证路径**上。
- RBAC、资源级授权整体留下游组件(§11)。网关自身不再有"权限"概念,只有"身份"。

### 身份模型(判别联合,延续 §4.3)
`Principal` 为按 `type` 判别的联合,扩出 `Bot`:

```python
class PrincipalType(StrEnum):
    USER = "user"
    BOT  = "bot"
    APP  = "third_party_app"   # 沿用设计稿命名

class UserPrincipal(BaseModel):
    type: Literal[PrincipalType.USER] = PrincipalType.USER
    tenant: str
    subject: AuthenticatedUser          # 必填

class BotPrincipal(BaseModel):
    type: Literal[PrincipalType.BOT] = PrincipalType.BOT
    tenant: str
    bot: Bot                            # 必填;新模型,见下

class AppPrincipal(BaseModel):
    type: Literal[PrincipalType.APP] = PrincipalType.APP
    tenant: str
    app: ThirdPartyApp                  # 必填;落地设计稿 §4.2
    on_behalf_of_opaque: str | None = None   # 唯一保留的 | None,None 契约上可缺省

Principal = Annotated[UserPrincipal | BotPrincipal | AppPrincipal, Field(discriminator="type")]
```

- `ThirdPartyApp`(已设计,落地):`client_id` / `developer_org_id` / `app_type`。
- `Bot`(新增):bot 的稳定标识。字段建议 `bot_id`(规范 id)、`owner_org_id`(归属开发者/组织,资源归属兜底)、`bot_type`(可选)。`tenant` 来源见下。
- `tenant` 在三个成员里都**必填**;来源随身份类型(§4.6 思路保留):User → `subject.tenant_id or DEFAULT_TENANT`;App → 租户令牌(`X-Tenant-Token`)+ api-key record 交叉校验;Bot → bot 注册所属租户,取不到落 `DEFAULT_TENANT`。

### 两层提取
**插件 `IdentityExtractor`**(新协议,最小单位):

```python
class IdentityExtractor(Protocol):
    name: str  # 稳定名,用于系统配置引用与可观测
    async def extract(self, creds: CredentialBundle) -> Principal | None:
        """先自判是否认得本插件对应的凭证;
        不认得 → None(链继续);认得且合法 → Principal(链返回);认得且非法 → raise AuthError(硬失败)。"""
```

> `None` vs `raise` 语义沿用设计稿 §5:缺凭证回退、坏凭证终端失败,绝不让坏 token 悄悄去试别的插件。这是整条管线最关键的实现 seam,必须保留。

**身份 `AuthStrategy`**(每类身份一个,沿用协议名):持有一条**有序 extractor 链**;`build(creds) -> Principal | None | raises AuthError` 顺序执行链,**首个返回 `Principal` 者即返回**;若某 extractor `raise` 则立即上抛(终端);若全链都 `None` 且无 raise → 返 `None`。

- `UserStrategy`(由现有 `FirstPartyUserStrategy` 重构):链由系统配置启用,如 `[session_cookie, oauth_bearer]`;`session_cookie` extractor 即原读 `IAM_TOKEN/SSO_TOKEN/access_token` 的逻辑,迁过来。
- `AppStrategy`(新):链如 `[api_key]`;沿用设计稿 §6.3 的 `app_key` 思路(`Authorization: Bearer` + `X-Tenant-Token` 交叉校验),但**重写为 extractor**:api_key extractor 自判"有没有 Bearer",有则校验 + 交叉校验租户,合法返 `AppPrincipal`,非法 raise。
- `BotStrategy`(新):链如 `[bot_token]`;bot_token extractor 自判"有没有 bot 凭证",有则校验 bot 记录,合法返 `BotPrincipal`,非法 raise。

Extractors **与 flavor 无关**;它们依赖的 SPI 仍是 `AuthPlugin`(User)、`ApiKeyValidator`/`TenantResolver`(App,§6.1)、以及新增的 `BotTokenValidator`(Bot),按 `bare`/`sofa` 由 `PluginAccessor` 注入(Rule 14)。

### per-endpoint 身份要求配置(重塑,非另立)
保留 `x-avernet-security` 扩展字段 + `route_security.yaml` 聚合 + CI 门禁(Rule 1)。**值形状改变**:

```yaml
# 原来(去除):OR-list of {scopes, delegation}
# "/open_api/v1/bots/{id}/chat": [ {app_key: {scopes: [bots:chat]}}, {first_party_user: {scopes:[bots:chat]}} ]

# 新:身份集合,每个 required|optional
route_security:
  "/**":
    user: required
  "/open_api/v1/bots/{id}/chat":
    user: required
    app: optional
  "/open_api/v1/manage/self":
    app: required          # 纯 App 调用
    user: optional
  "/internal/bot/{id}/act":
    bot: required
```

- method 前缀语法、最具体匹配、整条覆盖(不字段合并)、`/**` 兜底、fail-closed:**全部不变**(沿用 §8)。
- 缺省规则:声明里未出现的身份类型 = 不要求(等价 optional=false 且不解析)。

### 系统级插件启用配置(新)
一份**系统级**配置(建议独立文件,与路由要求分开),声明每类身份启用哪些 extractor 及顺序:

```yaml
identity_extractors:
  user: [session_cookie, oauth_bearer]
  bot:  [bot_token]
  app:  [api_key]
```

- 与 flavor 无关的"名字→Strategy/Extractor"装配在 composition root 完成;实际 extractor 依赖的 SPI 按 `GATEWAY_RUN_MODE` 选 `bare`/`sofa` 实现。

### 认证管线(替代原 OR/AND runner)
```python
async def authenticate(creds, requirement, strategies) -> dict[PrincipalType, Principal]:
    resolved: dict[PrincipalType, Principal] = {}
    for identity_type, presence in requirement.items():     # 每个声明的身份
        strategy = strategies.get(identity_type)
        if strategy is None: raise AuthError(f"unknown identity strategy: {identity_type}")  # terminal
        try:
            principal = await strategy.build(creds)         # 跑该身份的 extractor 链
        except AuthError:
            raise                                            # 硬失败,终端
        if principal is None:
            if presence is REQUIRED:
                raise AuthError(f"missing required identity: {identity_type}")   # 401
            continue                                         # optional 缺失 → 不在集合里
        resolved[identity_type] = principal
    return resolved
```

- 返回"解析出的身份集合"(每个在场的身份类型一个 Principal)。
- 任意必备缺失 / 任意 extractor 硬失败 → 401。
- 未命中任何路由规则 → fail-closed(沿用 `RouteSecurity` 返 `None` → `Authenticator` 拒)。
- 注意:这是 per-identity **各跑各的链**,而非原 OR/AND;一个凭证可同时被多条链解析,满足多身份并存(用户故事 27)。

### forwarder seam 与下游
- 解析出的 `dict[PrincipalType, Principal]` 在 forwarder seam 可用,以便后续转发。
- **签名/JWT(§7.1)不在本 spec**:本 spec 只交付"解析出的集合",并保证它在转发处可被取到;`PrincipalSigner`/`PrincipalVerifier` 留给独立 workstream。

### flavor
- `bare`:桩实现 `StubAuthPlugin`(已有)、`bare` 的 `ApiKeyValidator`/`TenantResolver`(落地设计稿 §6.1、§6.4 的 bare 形态)、新增 `bare` 的 `BotTokenValidator`(固定/内存 bot 记录)。
- `sofa`:通过 `register_plugin_option` 挂 BUService 实现(后续 pass);Strategy/Extractor 零改动。

### 与设计稿的关系
本 spec **演进**`docs/2026-07-21-auth-design.md`:重塑 §5(策略集)、§6.2/§6.3(策略实现解构为 extractor 链)、§7(runner→per-identity)、§8(配置形状,去 scopes/delegation);**保留并延续** §3/§4(模型、两轴)、§4.3(判别联合,扩 Bot)、§4.6(tenant 随身份)、§9(下游投影)、§11(授权分层,但网关去掉粗粒度 scope gate);**继续推迟** §7.1(签名)、§15(委托)。

## Testing Decisions

### 什么是好测试
只测**外部可观察行为**:给定一组凭证 + 路由身份要求 + 系统插件启用配置,解析出哪个身份集合 / 是否 401。不测内部插件调用顺序实现细节(除"首个成功即返回"这一条合同行为)。

### 主接缝(已与用户确认):In-memory 管线接缝
在 `CredentialBundle` 层断言,用 fake extractor 注入,零 HTTP、零 upstream mock。覆盖:
- 每类身份的链:首个适用的 extractor 返回 Principal 即停;不适用 extractor 返 `None` 后续试;全不适用 → 链返 `None`。
- 必备身份链返 `None` → `AuthError`(401);可选身份链返 `None` → 不在结果集、不报错。
- 任意 extractor `raise AuthError` → 终端上抛(401),即便该身份是 optional。
- 多身份并存:user required + app optional 都带 → 集合含两者;仅 user 带 → 集合只含 user;两者都缺 → 401。
- 一个凭证同时满足多身份(同一 fake 凭证被两条链解析)→ 集合含两者。
- 未知 identity 类型 → 401(terminal misconfig);路由无规则命中 → fail-closed 401。
- 沿用 `tests/test_auth_runner.py` 的 fakes + 内存 `CredentialBundle` 模式。

### 复用接缝 1:Conformance 契约(Rule 25)
扩展 `tests/contracts/spi/test_auth_strategy.py` 的契约基类到**每个 `IdentityExtractor`** 与**每个身份 `AuthStrategy`**:稳定 `name`;不适用 → `None`;适用 → `Principal`;非法 → `AuthError`。新增插件/Strategy 必须挂到此基类下。

### 复用接缝 2:路由身份要求解析
扩展 `tests/test_route_security.py`:断言新形状(`required`/`optional` 映射)的最具体匹配、`/**` 兜底、method 前缀、整条覆盖,行为与原 §8 一致。

### 薄冒烟(集成,1–2 例)
`tests/integration/test_forward_route.py` 加:必备身份缺失 → 401;齐全 → 正常转发。仅作端到端 sanity,主断言不在此层。

### 净新增接缝
**0**(主接缝扩展既有 `test_auth_runner` 模式;conformance 与 route 接缝复用;集成只加薄冒烟)。

### Prior art
`tests/test_auth_runner.py`(fakes + CredentialBundle)、`tests/test_first_party_user_strategy.py`(策略隔离)、`tests/contracts/spi/test_auth_strategy.py`(conformance 基类)、`tests/test_route_security.py`、`tests/integration/test_forward_route.py`。

## Out of Scope

- **Principal 密码学签名 / `PrincipalSigner` / `PrincipalVerifier` / JWT / JWKS / 密钥轮换**(§7.1)—— 独立 workstream;本 spec 只交付解析出的集合 + 在 forwarder seam 可用。
- **资源级授权 / RBAC / scope 词汇**—— 全部下游;网关不再做任何权限校验。
- **委托 `DelegatedPrincipal` / `app_key_delegated` / `oauth_bearer` / `xoneid` 解析**(§15)—— 继续推迟。
- **sofa flavor 的真实 SPI 实现**(BUService 接入、真实 ApiKeyValidator/TenantResolver/BotTokenValidator)—— 仅 `bare` 桩实现在范围内;sofa 实现是后续 pass。
- **下游各组件的 `Principal→域 DTO` 投影器**(§9)—— 各组件自持,不在网关 spec。
- **CI 门禁的具体构建实现**—— 只要求"每个对外路由能解析到一条身份要求";构建器改造不在本 spec。
- **OAuth 3-legged 流程、mTLS 客户端认证、租户令牌签发/轮换/吊销机制**。
- **登录/登出/令牌签发端点**—— 网关从不签发凭证(沿用现行设计)。
- **具体 extractor 插件全集**(sofa 侧的业务插件)—— 本 spec 只定义插件机制 + bare 桩 + User/Bot/App 三类各至少一个 extractor 落地;sofa 业务插件后续。

## Further Notes

### 开放问题(评审时拍板)
1. **Bot 的 tenant 来源**:提案为"bot 注册记录的 tenant,取不到落 `DEFAULT_TENANT`"(类比 User)。是否需要 Bot 也支持租户令牌交叉校验?默认否(单租户社区够用,多租户 corp 再加)。
2. **可选身份 + 非法凭证的语义**:本 spec 决定**仍 401**(终端,沿用 §5),即"带了坏凭证"永远比"没带"更严重,不论 required/optional。评审若认为"可选身份的坏凭证应忽略",需显式推翻。
3. **插件启用配置位置**:`application.yaml` 内嵌 vs 独立 `identity_plugins.yaml`。提案独立文件,与路由要求配置职责清晰分离。
4. **共享凭证的多身份判定**:同一 bearer 既可是 `app_key` 又可是 `bot_token`。提案:每条链独立自判,允许一个凭证同时满足多个身份(用户故事 27)。若需互斥(一个凭证只能落一种身份),需额外约定 extractor 之间的优先与互斥规则,本 spec 不引入。
5. **`AuthStrategy` vs `IdentityExtractor` 命名稳定性**:两者都将出现在系统配置里(`identity_extractors:` 引用 extractor 名;Strategy 仍按 `PrincipalType` 对齐)。建议 extractor 名用 `kebab-case`(如 `session-cookie`、`api-key`、`bot-token`)。

### 与现行代码的衔接点
- 现有 `FirstPartyUserStrategy` 的 cookie 读取逻辑迁移为 `session_cookie` extractor,不丢弃 `bare` 的硬编码用户行为。
- 现有 `StubAuthPlugin` 保留并作为 User 侧 SPI 桩。
- 现有 `route_security.yaml`/`x-avernet-security` 机制保留,仅改值形状;`test_route_security.py` 随之改形。
- 现有 forwarder 的"裸 await 丢 Principal"(`_forward`)在本 spec 之后**至少**改为"把解析出的身份集合传到 forwarder seam 可取到";真正签名注入仍属 §7.1。

### 标注
- `ready-for-agent`:本 spec 已满足可被实现 agent 直接领走的条件(模型、配置、管线、接缝、范围、开放问题均明确)。无外部 issue tracker,故以本地 `specs/` 文件 + 本状态标注替代打标签;如启用 AntCode issue 跟踪,创建 issue 并打 `ready-for-agent` 标签即可。
