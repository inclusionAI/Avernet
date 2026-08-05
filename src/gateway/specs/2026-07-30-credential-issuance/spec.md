# 凭证签发与注册（access_key / app）— 设计 spec

- 日期：2026-07-30
- 状态：草案（待评审）
- 关联：`specs/2026-07-29-principal-signer/spec.md`（PrincipalSigner）；`baas_access_key_token` / `avernet_apps` 表

## 1. 背景与目标

网关目前只能读取已存在的 access_key / app 凭证（按 `token` 查库），没有签发/注册能力——
demo 行靠 bootstrap 手动 seed。本工作项补齐：

- **签发 access_key**：给定 `access_key_id` + `tenant` + `expire_at`，生成一个 JWT 作为
  `token`，写入 `baas_access_key_token`，返回记录 + token。
- **注册 app**：给定 `app_id` / `app_name` / `owners` / `app_type` / `tenant`，生成 JWT 作为
  `token`，写入 `avernet_apps`，返回记录 + token。

两者都暴露为 core 层服务方法 + 网关 HTTP 接口（`/admin/...`）。

### 已确认取舍

1. 暴露形态：core 服务方法 + 网关 HTTP 接口。
2. 签发密钥：复用 `PrincipalSigner` 的 HMAC 密钥。~~（`AVERNET_PRINCIPAL_SIGNING_KEY`）~~
   PR #673 起该密钥改由 `SecretResolver` 按
   `user_config.principal_signer.secret_name`（默认 `principal_signing_key`）解析；
   community 味型读 `AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE`。那个 env 变量已不再
   被读取。
3. 接口鉴权：暂不鉴权（文档标注 not-for-prod；生产再加 admin 令牌）。

### 非目标

- 组件侧/下游对凭证 JWT 的验签（凭证 JWT 的签名/claims 供调用方与下游自校验；网关本身仍按
  `token` 串精确查库，opaque 查找，策略不动）。
- 凭证吊销/轮换 API、列表/删除 API（仅交付签发/注册）。
- app token 过期（`avernet_apps` 无 `expire_at` 列；如需另议）。
- 注册/签发接口的鉴权（本期不做）。

## 2. 组件与放置

- **`core/access_key/_issuer.py` → `AccessKeyIssuer`**
  `async issue(access_key_id: str, tenant: str, expire_at: datetime) -> IssuedAccessKey`。
  只负责构造 claims + 调 `PrincipalSigner.sign_token(claims)` 生成 JWT，再委托
  `AccessKeyRepository.store(...)` 持久化（**所有 DB 操作集中在 repository**，issuer 不碰
  `orm_session`/ORM 行）。

- **`core/app/_registrar.py` → `AppRegistrar`**
  `async register(app_id, app_name, owners, app_type, tenant) -> IssuedApp`。
  同样只 mint + 委托 `AppRepository.store(...)` 持久化。

- **`core/access_key/_repository.py` / `core/app/_repository.py`** 在原有
  `find_*_by_token`（读）基础上新增 `async store(...)`（写）——读 + 写 DB 操作都落在 repository。

- **SPI 增量**：`PrincipalSigner` Protocol 增加一个通用签名方法：

  ```python
  async def sign_token(self, claims: Mapping[str, object]) -> str: ...
  ```

  `BarePrincipalSigner` 实现 `sign_token`（`jwt.encode(claims, key, HS256,
  headers={"kid": kid})`），并把现有 `sign(principals, audience)` 重构为「构造 principal claims
  → 委托 `sign_token`」。一把密钥、一个签名器，principal 转发与凭证签发共用。

- **返回类型**（core 层 frozen dataclass）：
  - `IssuedAccessKey(access_key_id, tenant, expire_at, token)`
  - `IssuedApp(app_id, app_name, owners, app_type, tenant, token)`

- **HTTP**：`adapters/web/admin.py` 路由 `POST /admin/access-keys`、`POST /admin/apps`，在
  `app.py` 显式注册（先于 catch-all `/{full_path:path}`——FastAPI 显式路由优先）。issuer /
  registrar 经 `app.state.access_key_issuer` / `app.state.app_registrar` 注入。

## 3. 凭证 JWT claims

`token` DB 列 = 签出的 JWT 字符串。**下游策略不动**（仍 `find_*_by_token(token)` 按串精确查，
opaque 查找——JWT 的签名/claims 给调用方/下游自校验，网关只按串匹配）。

| | claims | JOSE header |
| --- | --- | --- |
| access_key | `iss`=gateway, `typ`="access_key", `sub`=access_key_id, `tenant`, `iat`(now), `exp`=入参 expire_at(epoch), `jti`(uuid4) | `kid` |
| app | `iss`=gateway, `typ`="app", `sub`=app_id, `tenant`, `iat`(now), `jti`(uuid4) | `kid` |

- app 表无 `expire_at` 列 → app token 不含 `exp`、不过期。
- `jti` 保证每次签发出唯一串 → 作为 `token` PK 天然唯一，支持轮换（同 `access_key_id` / `app_id`
  再签 → 新行新 token）。
- `iat`/`jti` 由 issuer/registrar 用注入式 `clock`（默认 `time.time`）与 `uuid.uuid4` 生成，便于
  测试固定。

## 4. 数据流 / HTTP

- `POST /admin/access-keys`  
  body：`{access_key_id: str, tenant: str, expire_at: str(ISO8601)}`  
  → `issuer.issue(...)` → `201 {access_key_id, tenant, expire_at, token}`。

- `POST /admin/apps`  
  body：`{app_id, app_name, owners, app_type, tenant}`  
  → `registrar.register(...)` → `201 {app_id, app_name, owners, app_type, tenant, token}`。

- 暂不鉴权（not-for-prod）。
- 这些路由不走 `forward_request` / `route_security`（FastAPI 显式路由优先于 catch-all，
  `route_security` 只在 `forward_request` 内查），天然绕开 fail-closed 默认。

## 5. 组装

- `bootstrap` 新增 `build_access_key_issuer(db, signer) -> AccessKeyIssuer` 与
  `build_app_registrar(db, signer) -> AppRegistrar`（接收同一 `PrincipalSigner` 与
  `DataSourcePlugin`）；挂 `app.state.access_key_issuer` / `app.state.app_registrar`。
- `app.py` 在 catch-all 之前 `include_router(admin_router)`，路由前缀 `/admin`。

## 6. 错误

- 字段缺失 / `expire_at` 非法日期 → `422`。
- 签名或写库失败 → `500`（fail-closed，不部分写）。
- 重复 `access_key_id` / `app_id`：**不拦截**，每次签发/注册生成新 token 新行（轮换语义）。

## 7. 测试

- 单测：
  - `AccessKeyIssuer.issue`：写入行；token 用签名密钥解码，断言 claims（`typ`/`sub`/`tenant`/
    `exp`/`jti`）与 `kid` 头；行 `token` == JWT 串。
  - `AppRegistrar.register`：同理（无 `exp`）。
  - `BarePrincipalSigner.sign_token`：通用签名正确；原 `sign(principals, audience)` 回归不破。
- HTTP/集成：
  - `POST /admin/access-keys` → 201 带 `token`；**该 token 随即可通过真实 access_key 策略鉴权**
    （签发→使用闭环）。
  - `POST /admin/apps` → 201 带 `token`；该 token 随即可通过真实 app 策略鉴权。
  - 字段缺失 / 非法日期 → 422。

## 8. 后续

- 注册/签发接口鉴权（admin 令牌）。
- 凭证列表/吊销/轮换 API。
- app token 过期（需表结构加 `expire_at`）。
- 下游对凭证 JWT 的验签（如改为非 opaque 查库）。