# Plan: Gateway 认证 — 多身份解析管线

> **详细可执行步骤见 `tasks.md`**(agentic worker 用 `superpowers:subagent-driven-development` 逐任务执行,步骤使用 `- [ ]` 复选框)。本文件是设计/取舍文档。

## Approach

把网关认证**重塑为纯身份提取管线**:去掉权限校验(scopes/Delegation/RBAC),引入"一种身份 = 一条插件链、首达即返、插件先自判"的模型,并新增 `Bot`/`App` 两类身份。

两层提取:
- **`IdentityExtractor`**(插件,最小单位):`extract(creds) -> Principal | None | raises AuthError`。先自判"认不认得这个凭证":不认得 → `None`(链继续);认得且合法 → `Principal`(链返回);认得且非法 → `raise AuthError`(硬失败、不回退,沿用设计稿 §5 这条最关键 seam)。
- **`IdentityStrategy`**(每类身份一个,**泛型**链跑器,住在 core):持该身份系统启用插件链,`build(creds) -> Principal | None | raises AuthError`,顺序执行,**首个返回 `Principal` 者即返回**;全链 `None` 且无 raise → 返 `None`。

per-endpoint 配置重塑:`route_security.yaml` / `x-avernet-security` 的值从"OR-list of `{scopes,delegation}`"改为"`{identity_type: required|optional}`"。系统级新配置 `identity_extractors.yaml` 声明每类身份启用哪些插件及顺序。

管线(`core/authn/_runner.authenticate`)替代原 OR/AND runner:对 endpoint 声明的每个身份各跑其链;必备缺失 / 任意插件 hard-fail → `AuthError`(401);返回 `dict[PrincipalType, Principal]`(每个在场的身份一个)。资源级授权留下游(§11);Principal 签名转发(§7.1)仍是独立 workstream —— 本轮把解析出的身份集合**送到 forwarder seam**,签名不在此轮。

## Resolved decisions

- **单一泛型 `IdentityStrategy`,而非三个 `User/Bot/AppStrategy` 类。** 三类身份的链跑逻辑完全相同(有序 extractor、首达即返、`None`/`raise` 语义),DRY(Rule 19)。差异只在 composition root 给它装哪些 extractor。"每类身份一个 Strategy"= 泛型类按 `PrincipalType` 各实例化一次。
- **`PrincipalType.APP = "app"`**(非设计稿的 `"third_party_app"`)。配置/序列化形如 `{app: required}` 更可读;`PrincipalType` 是代码标识,`.value` 是序列化形。此为对本 spec 的实现细化。
- **可选身份 + 非法凭证仍 401**(终端)。带了坏凭证永远比没带严重,不论 required/optional,沿用 §5。`IdentityStrategy.build` 不吞 `AuthError`,自然上抛。
- **共享凭证可同时满足多身份**:每条链独立自判,一个 bearer 可同时被 `api_key` 与 `bot_token` 链解析(本轮不引入互斥)。
- **forward seam:只送解析集合到 seam,不签名注入。** §7.1 明令"组件绝不能信裸 Principal 头",故本轮用 `_attach_identities(forward, principal_set)` 这一**命名 seam 函数**:当前返回 forward 不变(不注入任何身份头),并有一条回归测试钉住"签名未落地前不得泄露身份头"。`PrincipalSigner` workstream 替换其函数体即可。
- **保留 `route_security` 现有机制**,仅改值形状 + 删除 scopes/delegation 解析。最具体匹配、`/**` 兜底、fail-closed、CI 门禁(Rule 1)全部不动。
- **`bare` 先行**,flavor 差异下沉到策略依赖的 SPI(`AuthPlugin` / `ApiKeyValidator` / `TenantResolver` / `BotTokenValidator`),均提供 `bare` 桩。sofa 实现后续 pass,策略/extractor 零改动(Rule 14)。

## Affected Components

- `spi/authn/` — `Principal` 判别联合扩 `Bot`/`App`;新增 `Presence`、`IdentityExtractor` 协议、`_ports.py`(`ApiKeyRecord`/`ApiKeyValidator`/`TenantResolver`/`BotRecord`/`BotTokenValidator`);移除 `Delegation`/`StrategyParams`/`UserPrincipal.scopes`。
- `core/authn/` — `Requirement` 改为 `dict[PrincipalType, Presence]`;`_runner.authenticate` 改 per-identity;新增 `_strategy.IdentityStrategy`;`_route_security` 解析改形。
- `core/forwarding/_openapi.py` — `x-avernet-security` 标记改 dict 形;删 `_params_to_dict`。
- `plugins/authn/` — 新增 `user/`(`SessionCookieExtractor`,由 `FirstPartyUserStrategy` cookie 逻辑迁移)、`app/`(`ApiKeyExtractor`)、`bot/`(`BotTokenExtractor`);新增 `bare` 桩 `api_key_validator/`、`tenant_resolver/`、`bot_token_validator/`;删除 `first_party_user/`。
- `bootstrap/_authn.py` — `build_authenticator` 用 `IdentityStrategy` + extractor 工厂表 + `identity_extractors.yaml`;`Authenticator.authenticate` 返回 `dict[PrincipalType, Principal]`;`strategies` 键改 `PrincipalType`。
- `adapters/web/_forward.py` — 捕获解析集合 + `_attach_identities` seam。
- `configs/route_security.yaml` — 值改 `{identity: required|optional}`;新增 `configs/identity_extractors.yaml`。

## Data Model Changes

无数据库变更。类型层:`Principal = Annotated[UserPrincipal | BotPrincipal | AppPrincipal, Field(discriminator="type")]`;新增 `Bot`、`ThirdPartyApp`、`BotPrincipal`、`AppPrincipal`、`Presence`、四个 port 协议 + 两个 record dataclass;移除 `Delegation`、`StrategyParams`、`UserPrincipal.scopes`。

## API / Interface Changes

- `AuthStrategy.build(creds) -> Principal | None | raises AuthError`(去掉 `params` 形参)。
- `IdentityExtractor.extract(creds) -> Principal | None | raises AuthError`(新)。
- `RouteSecurity.resolve(method, path) -> dict[PrincipalType, Presence] | None`。
- `authenticate(creds, requirement, strategies) -> dict[PrincipalType, Principal]`。
- `Authenticator.authenticate(method, path, creds) -> dict[PrincipalType, Principal]`。
- `x-avernet-security` 序列化形:`{"user": "required", "app": "optional"}`(原 `[{"first_party_user": {scopes:...}}]`)。
- `configs/identity_extractors.yaml`(新):`identity_extractors: {user: [session_cookie], bot: [bot_token], app: [api_key]}`。

## Key Files & Functions

- `spi/authn/_models.py` — Principal 联合 + `Presence` + `Bot`/`ThirdPartyApp`/`BotPrincipal`/`AppPrincipal`。
- `spi/authn/_protocols.py` — `AuthStrategy.build(creds)` + 新 `IdentityExtractor`。
- `spi/authn/_ports.py`(新)— `ApiKeyRecord`/`ApiKeyValidator`/`TenantResolver`/`BotRecord`/`BotTokenValidator`。
- `core/authn/_strategy.py`(新)— `IdentityStrategy` 链跑器。
- `core/authn/_runner.py` — per-identity `authenticate`。
- `core/authn/_route_security.py` — `Requirement`/解析改形(匹配/具体度不变)。
- `core/forwarding/_openapi.py` — `_with_security` 标记改 dict。
- `plugins/authn/{user,app,bot}/` — 三个 extractor。
- `plugins/authn/{api_key_validator,tenant_resolver,bot_token_validator}/bare/` — 三个 bare 桩。
- `bootstrap/_authn.py` — composition root 重写。
- `adapters/web/_forward.py` — `_attach_identities` seam。
- `configs/route_security.yaml`、`configs/identity_extractors.yaml`(新)。

## Dependencies

无新运行时依赖(`pyyaml`/`pydantic` 已在)。`bare` 桩全用内存/固定值,不引入外部身份源。

## Risks & Mitigations

- **Risk:** 重塑是跨切面 refactor(改 `Requirement`/`AuthStrategy.build`/`Authenticator` 一处即牵动 route_security/runner/openapi/bootstrap/forward/多份测试)。**Mitigation:** 增量先行(任务 1-6 全部**加法**,旧系统不动,每步绿);**任务 7 是唯一原子 cutover**(一次性改契约+布线+配置+受影响测试,红→绿);任务 8 单独清理死代码。每步配 `ruff`/`mypy`/`pytest` 门禁。
- **Risk:** forward seam 如果误注入未签名身份头,违反 §7.1。**Mitigation:** `_attach_identities` 当前为命名 no-op seam,并有回归测试钉住"签名未落地前不得向下游注入身份头"。
- **Risk:** 架构测试(`test_all_exports_valid`/`test_protocol_exports` Rule 12)对 `__all__` 强约束。**Mitigation:** 每个新包 `__init__.py` 必带 `__all__`;`IdentityExtractor` 等协议入 `_protocols.py` 并加进 `__all__`;新 `_ports.py` 协议亦导出且可解析。验证步含架构测试。
- **Risk:** `bare` 桩放过任意凭证(不真验内容),可能误导。**Mitigation:** 文档明示 bare = 单盒开箱桩;真校验在 sofa。doctest/测试钉住"bare 返回固定 record"以显式其桩性质。

## Alternatives Considered

- **三个独立 `User/Bot/AppStrategy` 类**(spec 字面命名)。更贴 spec 文本,但三类链跑逻辑完全同 → DRY 违反、Rule 19 倒退。拒,改单一泛型 `IdentityStrategy`。
- **strangler 双 runner 并存**(新/旧 `authenticate` 共存,最后删旧)。可每步绿但引入临时双 `Requirement`/`AuthStrategy` 协议面,比 cutover 更乱。拒,走单次原子 cutover。
- **签名注入随本轮一起做**。范围爆炸(密钥分发/JWKS/轮换 §7.1)。拒,本轮只交 seam,签名独立 workstream。

## Rollout

全在 `src/gateway` 内,无前后端协同、无 DB、无部署。顺序:任务 1-6 加法 → 任务 7 原子 cutover → 任务 8 清理 → 任务 9 forward seam → 任务 10 集成冒烟 → 任务 11 全量门禁。每任务一次提交。cutover(任务 7)是唯一中间会红的任务(测试先改形→红,实现改形→绿,一提交)。

## Test Strategy

(与 spec `Testing Decisions` 一致;主接缝 = In-memory 管线,用户已确认。)

- **单元(extractor):** `SessionCookieExtractor`/`ApiKeyExtractor`/`BotTokenExtractor` 各自 None / Principal / AuthError 三态 + 租户/默认租户/交叉校验。
- **单元(strategy):** `IdentityStrategy` 链:首达即返、全 None→None、hard-fail 上抛。
- **单元(runner,主接缝):** `authenticate` 的 per-identity 裁决 —— 必备缺失 401 / 可选缺失跳过 / hard-fail 终端 / 多身份并存 / 未知身份 401;fakes + 内存 `CredentialBundle`,沿用 `test_auth_runner.py` 模式。
- **单元(route_security):** 新 `{identity: required|optional}` 解析 + 最具体匹配/兜底/fail-closed 不变,沿用 `test_route_security.py`。
- **contract(Rule 25):** `IdentityExtractor` 契约基类,每个 extractor + `IdentityStrategy` 挂其下。
- **单元(openapi):** `x-avernet-security` 新 dict 形,沿用 `test_served_openapi.py`。
- **单元(seam):** `_attach_identities` 不泄露未签名身份头(§7.1 回归)。
- **集成(薄冒烟):** 真 `Authenticator` + `IdentityStrategy` + `SessionCookieExtractor`(Bare)经 `forward_request`:必备缺失→401、齐全(带 cookie)→转发 200。
- 门禁:`ruff check src tests`、`mypy src`、`pytest -m "not e2e"`(含架构/导出测试)全绿。
