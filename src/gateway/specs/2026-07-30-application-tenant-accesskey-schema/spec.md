# canonical schema：application / tenant / access_key_token — 设计 spec

- 日期：2026-07-30
- 状态：草案（待评审）
- 关联：`specs/2026-07-30-credential-issuance/spec.md`（凭证签发，建基于本组表）；
  `specs/2026-07-29-principal-signer/spec.md`（PrincipalSigner）；`specs/2026-07-27-gateway-authn-identity-pipeline/spec.md`

## 1. 背景与目标

凭证签发工作项落地时，app 与 access_key 用了两张"轻量 SPI 表"：

- `avernet_apps`（`core/app/_orm.py`）：`id, token, app_id, app_name, owners, app_type, tenant`
- `baas_access_key_token`（`core/access_key/_orm.py`）：`id, token, access_key, tenant, expire_at`

本工作项把这两张表替换为更完整的 canonical schema，并新增 `avernet_tenant` 主数据表：

- **`avernet_application`**（替换 `avernet_apps`）：补 `status / env / config` 与审计列
  (`creator / modifier / gmt_create / gmt_modified`)，并在 `app_name`、`token` 上建索引。
- **`avernet_access_key_token`**（替换 `baas_access_key_token`）：补审计列。
- **`avernet_tenant`**（新增）：租户主数据表。

三张表仍走现有约定：ORM 模型挂在共享 `Base` 上，由 `DataSourcePlugin.create_all()`
建表（`BareDatabasePlugin` 已会把 `BIGINT` 主键降级为 `Integer` 以适配内存 SQLite）。

同时把 **bot 域对 app 的引用**也对齐到 `avernet_application.id`：`bcs_bots.app_id` /
`RegisteredBot.app_id` / `Bot.app_id`（`BotPrincipal.bot`）由 `str` 改为 `int`（long），取值即所属
app 的代理主键 `id`。

### 已确认决策

1. **替换并迁移**（非并存）：三张新表是最终 canonical schema；把现有 app / access_key 的
   ORM、repository、SPI、registrar/issuer、HTTP `/admin`、authn 策略从旧表迁移到新表，移除旧表模型。
2. **`avernet_application` 不设 `app_id` 列**（严格按字段表）。app 的稳定身份改由代理主键 `id`（bigint）承担。
3. **`AppPrincipal.app.app_id` 保留但其类型由 `str` 改为 `int`（long）**，取值为行的代理主键 `id`。
   `RegisteredApp` SPI 增暴露 `id: int`。下游转发的 principal 契约**字段名不变、类型变 str→int**（破坏性，见 §9）。
4. **`tenant` 仍为字符串列**（租户 code/name），逻辑上引用 `avernet_tenant.name`；ORM 层不加 `ForeignKey`
   （gateway 现有 ORM 无 FK 先例，bare SQLite 默认不启用 FK 校验）。`avernet_tenant` 为独立主数据表。

### 非目标

- `avernet_tenant` 的 SPI / repository / HTTP CRUD（本期只建表 + ORM，无消费方，YAGNI）。
- app token 过期（`avernet_application` 无 `expire_at`）。
- `/admin` 接口鉴权（沿用现状：not-for-prod，不鉴权）。
- Alembic 迁移文件：gateway 目前无 migrations 目录，建表机制是 `create_all`。
- bot 域的 `env` / `agent_code` / `created_by` 等其它列的改造（只改 `app_id` 的类型）。
- app-token 凭证 JWT 的下游验签。

## 2. 三张表 schema

列顺序按用户给定的字段表；类型为 SQLAlchemy ORM 声明。`id` 一律 `BigInteger` 自增主键。

### 2.1 `avernet_application`（替换 `avernet_apps`）

| 列 | 类型 | 约束 / 默认 |
| --- | --- | --- |
| `id` | BigInteger | PK，自增 |
| `app_name` | str | **索引**（非唯一） |
| `app_type` | str | — |
| `token` | str | **唯一 + 索引**（opaque 查找键） |
| `owners` | str | — |
| `tenant` | str | — |
| `status` | str | 默认 `"ACTIVE"` |
| `env` | str | 默认 `""` |
| `creator` | str \| None | 可空（无鉴权的 admin 无调用方身份；None 为 intentional） |
| `modifier` | str \| None | 可空（同上） |
| `gmt_create` | datetime | `server_default=CURRENT_TIMESTAMP`，非空 |
| `gmt_modified` | datetime | `server_default=CURRENT_TIMESTAMP`；`onupdate=CURRENT_TIMESTAMP` |
| `config` | JSON | 默认 `{}`（gateway 首个 JSON 类型 ORM 列；bare 插件已注入 MySQL 兼容 JSON 函数） |

索引：`idx_avernet_application_app_name`（`app_name`）；`token` 由 `unique=True` 建唯一索引。

`AppRow.to_record()` → `RegisteredApp(id, app_name, owners, app_type, tenant)`（不再有 `app_id`）。

### 2.2 `avernet_tenant`（新增，ORM-only）

| 列 | 类型 | 约束 / 默认 |
| --- | --- | --- |
| `id` | BigInteger | PK，自增 |
| `name` | str | — |
| `description` | str | — |
| `owner` | str | — |
| `creator` | str \| None | 可空 |
| `modifier` | str \| None | 可空 |
| `gmt_create` | datetime | `server_default=CURRENT_TIMESTAMP` |
| `gmt_modified` | datetime | `server_default=CURRENT_TIMESTAMP`；`onupdate=CURRENT_TIMESTAMP` |
| `config` | JSON | 默认 `{}` |

无 SPI / repository / HTTP（YAGNI，待出现消费方再加）。建表即由 `create_all` 覆盖。

### 2.3 `avernet_access_key_token`（替换 `baas_access_key_token`）

| 列 | 类型 | 约束 / 默认 |
| --- | --- | --- |
| `id` | BigInteger | PK，自增 |
| `token` | str | 唯一（opaque 查找键） |
| `access_key` | str | — |
| `tenant` | str | — |
| `expire_at` | datetime | — |
| `creator` | str \| None | 可空 |
| `modifier` | str \| None | 可空 |
| `gmt_create` | datetime | `server_default=CURRENT_TIMESTAMP` |
| `gmt_modified` | datetime | `server_default=CURRENT_TIMESTAMP`；`onupdate=CURRENT_TIMESTAMP` |

`AccessKeyRow.to_record()` → `RegisteredAccessKey(access_key, tenant, expire_at)`（**SPI 不变**，审计列 DB-side，同 `bcs_bots` 的 `env`/`app_id` 处理）。

## 3. SPI / 契约变更

- **`spi/app/_ports.py` → `RegisteredApp`**：删 `app_id: str`；新增 `id: int`。字段为
  `{id, app_name, owners, app_type, tenant}`。
- **`spi/authn/_models.py` → `ThirdPartyApp`**：`app_id` 类型 `str` → `int`，取值 = 行的代理主键 `id`；
  更新注释（"the app's surrogate bigint id"）。`AppPrincipal` 结构不变（仍 `app: ThirdPartyApp`）。
- **`spi/access_key/_ports.py` → `RegisteredAccessKey`**：不变。
- **`spi/bot/_ports.py` → `RegisteredBot`**：`app_id: str` → `int`（所属 app 的代理主键 `id`）。
- **`spi/authn/_models.py` → `Bot`**：`app_id: str` → `int`（`BotPrincipal.bot.app_id`）。注释同步。

> 契约影响：转发给下游的签名 principal 里 `principals[].app.app_id` 与 `principals[].bot.app_id`
> 均由 string 变 int。gateway 内由 `BarePrincipalSigner.sign()` 经 `model_dump(mode="json")` 序列化进
> JWT `principals` claim，故下游解析这两处 `app_id` 的代码需兼容 int。

## 4. 代码改动（按模块）

### core/app
- **`_orm.py`**：`AppRow.__tablename__` = `"avernet_application"`；列按 §2.1 重写（去 `app_id`，加
  `status/env/config` + 审计列）；`to_record()` 映射 `id=self.id`。
- **`_repository.py`**：`store(...)` 去掉 `app_id` 参数；新增可选 `status / env / config / creator / modifier`
  （默认 status=`ACTIVE`、env=`""`、config=`{}`、creator/modifier=None）；**返回插入行的 `id`（int）**
  （`session.add` + `session.flush()` 后读 `row.id`，再 commit）；docstring 表名更新。`find_app_by_token` 不变（走 `to_record`）。
- **`_registrar.py`**：`AppRegistrar.register(...)` 去掉 `app_id` 参数；`IssuedApp` 去 `app_id`、新增 `id`；
  JWT claims `sub` = `app_name`（代理 `id` 在 insert 前未知；principal 的 `app_id` 在 authn 时由 `record.id` 取得，
  见 §5）；用 `store(...)` 返回的 `id` 填入 `IssuedApp.id`。
- **`__init__.py`**：导出不变（`AppRow/AppRepository/AppRegistrar/IssuedApp`）。

### core/access_key
- **`_orm.py`**：`AccessKeyRow.__tablename__` = `"avernet_access_key_token"`；新增审计列；`to_record()` 不变。
- **`_repository.py`**：`store(...)` 新增可选 `creator/modifier`（默认 None）；gmt 由 DB 默认；docstring 表名更新。
- **`_issuer.py`**：签名不变；docstring 表名更新。
- **`__init__.py`**：导出不变。

### core/bot（app 引用类型对齐）
- **`_orm.py`**：`BotRow.app_id` 列 `Mapped[str]` → `Mapped[int] = mapped_column(BigInteger)`
  （逻辑引用 `avernet_application.id`；ORM 层不加 FK）。`to_record()` 不变。
- **`plugins/authn/bot_token/_strategy.py`**：无代码改动（`app_id=bot.app_id` 透传，类型随之变 int）。

### 新增 core/tenant（仅 ORM）
- **`core/tenant/_orm.py` → `TenantRow`**：`__tablename__` = `"avernet_tenant"`，列按 §2.2。
- **`core/tenant/__init__.py`**：导出 `TenantRow`。
- 不加 `_repository.py` / SPI（YAGNI）。

### plugins/authn
- **`app_token/_strategy.py`**：`ThirdPartyApp(app_id=record.id, ...)``（int）。
- **`access_key_token/_strategy.py`**：不变。

### adapters/web
- **`admin.py`**：`AppRequest` 去 `app_id`，新增可选 `status / env / config`（带默认）；`register_app`
  调用与响应去 `app_id`、加 `id`（int）。`AccessKeyRequest` 与 `issue_access_key` 响应不变
  （审计列不出现在响应）。`/admin/apps` 201 body：`{id, app_name, owners, app_type, tenant, token, status, env, config?}`；
  `/admin/access-keys` 201 body 不变。

### bootstrap
- **`bootstrap/_authn.py`**：`_seed_authn` 中 `AppRow` seed 去 `app_id`，补
  `status="ACTIVE", env="dev", config={}`；`BotRow(app_id="app-1")` → `app_id=1`（int，引用 seeded app 的
  `id=1`）；可选新增 `TenantRow` seed（`name="t"`）保持 demo 自洽。模块 docstring 表名更新
  （`avernet_apps`→`avernet_application`、`baas_access_key_token`→`avernet_access_key_token`）。

### spi/database
- 无需改动（`Base` 不变；新模型自动进 `Base.metadata`）。

## 5. JWT / principal 数据流（app token）

```
register(app_name, owners, app_type, tenant, status?, env?, config?)
  → claims {iss:gateway, typ:"app", sub:app_name, tenant, iat, jti}
  → signer.sign_token(claims) → token
  → repository.store(...) -> id（store flush 后返回自增 id）
  → IssuedApp(id, app_name, owners, app_type, tenant, token)

authn app_token:
  find_app_by_token(token) → RegisteredApp(id, ...)
  → AppPrincipal(tenant=record.tenant, app=ThirdPartyApp(app_id=record.id, app_name, owners, tenant, app_type))
  → signer.sign(principals) → 转发 JWT，principals[].app.app_id 为 int(id)
```

- `sub` 用 `app_name`（mint 时拿不到自增 `id`；token 仍按串 opaque 查库，`sub` 仅供下游/调用方自校验）。
- 如下游要求 `sub` = `id`，需改成"先 insert 拿 id、再 mint、再 update token"的两次写流程——本期不做。

## 6. 测试影响

需更新的测试（`app_id` str→int / 旧表名 / `IssuedApp` 形状）：

- **app 域**：`tests/unit/plugins/test_app_registrar.py`、`test_app_registry_db.py`、
  `test_app_token_strategy.py`、`tests/contracts/spi/test_app_ports.py`、
  `tests/integration/test_admin_issuance.py`、`tests/integration/test_forward_signs_principal.py`、
  `tests/test_authn_models.py`、`tests/test_auth_runner.py`、`tests/test_forward_seam.py`、
  `tests/unit/plugins/test_principal_signer.py`
- **bot 域**：`tests/unit/plugins/test_bot_token_strategy.py`、`test_bot_registry_db.py`
- **同时含 app + bot fake**：`tests/contracts/spi/test_auth_strategy.py`（`_FakeAppRegistry` / `_FakeBotRegistry` 都改 int）
- **access_key**：`tests/unit/plugins/test_access_key_registry_db.py`（仅 docstring 表名）；
  `test_access_key_issuer.py` / `test_access_key_token_strategy.py` 无需改动
- **无需改动**：`tests/integration/test_identity_pipeline.py`（仅断言 identity 存在）、
  `tests/unit/plugins/test_plugin_registry.py`（与本主题无关）

新增：`avernet_tenant` 建表的最小冒烟（`TenantRow` 在 `Base.metadata` 且 `create_all` 能建表）。

> 断言技巧：seeded app 的代理 `id` 由自增决定——首次 `build_database()` seed 时 `app-key` 行为
> `avernet_application` 的第一行 → `id=1`，故 `test_app_registry_db` 断言 `RegisteredApp(id=1, ...)`、
> `test_bot_registry_db` 断言 `app_id=1`。但 registrar/HTTP 注册的 app 其 `id` 随测试顺序递增，故
> `test_app_registrar` / `test_admin_issuance` 应断言 `isinstance(id, int)` 且 `rec.id == issued.id`，
> 不要写死具体数值。

## 7. 风险与后续

1. **下游 principal 契约破坏**：`principals[].app.app_id` 与 `principals[].bot.app_id` 均 str→int。
   需与下游（bcs/backend 投影 `AppPrincipal` / `BotPrincipal` 的组件）确认兼容；若下游按 string 解析需同步改造。
2. **bot↔app 引用无 DB 级 FK**：`bcs_bots.app_id` 与 `avernet_application.id` 之间仅逻辑引用，无 `ForeignKey`
   （gateway 无 FK 先例）。seed 中 bot `app_id=1` 与 app `id=1` 靠自增顺序对齐——无 FK 不影响运行，但后续若加 FK
   需显式声明并处理存量。
3. **`sub`(app_name) 与 `app_id`(int id) 不同源**：凭证 JWT `sub` 是 app_name、转发 principal `app_id` 是 int。
   若需统一为 id，见 §5 两次写流程。
4. **首个 JSON ORM 列**：`config` 引入 `JSON` 类型；bare SQLite 经插件注入的 JSON 函数兼容，但需在测试中验证
   `create_all` + 读写 `{}`/复杂对象无误。
5. **审计列 server_default**：`CURRENT_TIMESTAMP` 在 SQLite/MySQL 均可用；`gmt_modified` 的 `onupdate` 仅在
   ORM 更新时触发（本期无 update 路径，列预埋）。
6. **`creator/modifier` 可空**：与 `AGENTS.md` 的 `T | None` 规则一致（None 为 intentional：无鉴权 admin 无调用方）。
