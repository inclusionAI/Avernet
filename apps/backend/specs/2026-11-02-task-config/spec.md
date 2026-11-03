# task_config 配置存储与匹配 — 设计文档

日期: 2026-11-02
归属: `apps/backend` (`agentclaw.community`)
状态: DRAFT,待 brainstorming 收口

## Intent & why

前端在打开一个 bot 对话框时,需要知道这个 bot 有没有装上 task 的某个 skill——
比如"安装了 task-skill-x 的 bot 集合"是一份需要维护的清单,前端拿一个 bot id 去
问"在不在清单里",再决定 UI 上要不要露出 task 入口。这类"以配置名 + 一个值做匹配"
的判断会反复出现(skill 安装态、功能灰度、白名单……),所以我们建一个通用的、DB 里
维护的配置存储,而不是每个场景各写一张表。

需求要点(来自对话):

- 新表 `task_config`,字段:配置名、配置类型、配置值;配置类型 ∈
  `{single_string, bool, set}`,类型同时表达了"值的形态"和"匹配方式"。
- 一个匹配接口:传入配置名 + 一个值,后端按类型判断该值是否与存储的配置值匹配,回
  布尔结果。
- 一个写接口:整体覆盖写入(upsert),前端和外部服务都能直接调。
- 配置要区分 `pre` / `prod` 环境;`env` 由后端按当前部署上下文自动判定,**不作为接
  口参数**暴露。
- 鉴权按最简模型:任意已认证 principal 都可读写任意配置名,不做 per-config 鉴权。
- **路由归属:挂在 task 的两个 surface 上**
  - 写接口 `set`:仅内部面 `/api/v1/collaboration/tasks`。
  - 读接口 `match`:内部面 `/api/v1/collaboration/tasks` **和** 公开面
    `/openapi/v1/collaboration/tasks` 都挂(同一 service,两条路由)。

## Goals

- 提供一张按 `(config_name, env)` 唯一键的通用配置表,值按 `config_type` 解释。
- 提供 `set` 写接口(upsert 整体覆盖)和 `match` 读接口(类型驱动匹配,只回布尔)。
- 环境隔离:同一 `config_name` 在 `pre` 和 `prod` 是两条独立记录;`env` 由后端填,调
  用方无从跨环境读写。
- `match` 在内部面与公开面都可用,两面对外契约一致;`set` 仅内部。

## Non-goals

- 不做配置版本历史 / 审计流(本版只存当前值;`creator` / `modifier` 仅用于表字段留痕,不外暴)。
- 不做配置变更通知 / 订阅。
- 不做配置项的预定义清单枚举——配置名是自由字符串,按需创建。
- 不做 per-config 粒度的读写鉴权(见 Anti-goals)。

## Anti-goals(即便技术上跑通也算失败)

- **`env` 不能成为接口参数。** 一旦调用方能传 `env`,就等于把 pre 配置写进 prod,或反
  之,环境隔离失效。
- **match 接口不能回传存储的配置值本身。** 它只回 `matched: true/false`,避免一个低
  权前端会话通过匹配探针把整张配置集合捞走。
- **不做读改写。** 写接口必须是原子整体覆盖(upsert),否则前端"读全量→改→写回"会
  丢更新。
- **不在前端/外部调用方侧硬编码 `prod`/`pre` 字面量。** env 归后端唯一拥有。

## Constraints(来自后端实际惯例,已探查核对)

1. 环境判定:`agentclaw.community.utils.env_utils.get_current_env()` 返回
   `dev | pre | prod`,在 router 内调用,不作为请求参数。`pre` 和 `prod` 是其中两个
   值——本版只需保证 pre/prod 互为独立记录;`dev` 同表共存,不特殊处理。
2. 数据库:profile 决定 SQLite(本地/test/singlebox/corp_test)或 OceanBase(MySQL
   方言,corp/local-k8s)。模型继承单一 `core.base.Base`。
3. **新模型必须登记进 `core/schema.py::import_all_models()`**,否则本地/test 的全新内
   存 SQLite 上 `create_all` 会漏建这张表(`corp` 走外部 DDL,影响小,但 test 会挂)。
4. 迁移 DDL 文件放在域内 `sql/YYYY_MM_DD_task_config.sql`,与 ORM 保持 OceanBase 平
   行(参考 `core/task/sql/` 的 `2026_08_24_oceanbase_validation.md` checklist)。已提
   交的迁移不回头改,只新增。
5. 储库走端口协议模式:`core/repository/protocols/<area>.py` 的 `Protocol` +
   `core/repository/implementations/<area>/<repo>.py` 的实现,`@inject __init__(db:
   DatabasePlugin)`,每个方法 `with self._db.orm_session() as db:`;**只有 upsert
   按 `dialect.name` 分支 sqlite/mysql**——这是唯一被允许的方言分支,按运行模式分支
   是被禁的。
6. **task 两个 surface 的响应信封是 `Envelope[T]` + `@envelope_errors` 装饰器,
   `response_model=Envelope[...]`**——不是 `system_config` 的 `ApiResponse`。内部面
   `/api` 用 `Depends(get_current_user)`(`/settings` 即此模式);公开面
   `/openapi/v1/*` 用 `route_class=PublicAPIRoute` + `PrincipalDep = Annotated[Principal,
   Depends(require_principal)]`(验证 Gateway 签名的 `X-Avernet-Principal`)。
7. **公开面每条路由必须在两张表里各登记一行,否则装配期 `PublicRouteNotAuthorized`
   报错**:AUTHORIZATION 表 `adapters/http/openapi_v1/authorization_rules_task.py`(现存
   行用 `NoCheck(...)`);ADMISSION 表
   `adapters/http/openapi_v1/admission_task_routes.py::TASK_PUBLIC_ADMISSION`(
   `AdmissionMode.OPEN`)。
8. 两个 surface 通过 `Injected(同一 Protocol)` 共享同一 service 单例(现存的
   `/execute`、`/trajectory`、`/grant` 等读接口就是这样镜像的)。
9. **每条新路由必须在 `tests/community/endpoints/` 配声明式 `happy` + `error` 用例**,
   否则 `framework/test_coverage_gate.py` 会让全量测试挂 gate。**gate 按
   `(method, 完整 path)` 字面匹配**——`match` 挂两个前缀 = 两条独立活路由,各自都要
   happy+error。
10. 1000 行文件上限是 CI 强制;按职责拆文件。不格式化无关代码。

## 设计:已决定的部分

### 模块位置(我的决定,可改)

表名与配置域都叫 `task_config`,路由挂在 task 的两个 surface 下,service 由 task
router `Injected(...)` 注入。故把核心放在 task 域内自洽子包,与现存
`api/task/task_service.py`、`core/task/...` 同形:

```
core/task/task_config/
    orm.py                       # TaskConfigModel -> 表 task_config
    models.py                    # dataclass TaskConfigRecord
    service.py                    # TaskConfigService(TaskConfigServiceProtocol)
    sql/2026_11_02_task_config.sql   # OceanBase-parity DDL
api/task/task_config_service.py   # TaskConfigServiceProtocol(供 router Inject)
core/repository/protocols/task_config.py          # TaskConfigRepositoryProtocol
core/repository/implementations/task_config/task_config.py   # 实现,@inject(db)
di/modules/task_config_module.py                  # 绑定 + 注册进 container.py
adapters/http/task/task_config_router.py          # 内部 sub-router(prefix="/config"),挂入 umbrella
# 公开面:在 adapters/http/openapi_v1/task/router.py 里直接加 match handler
#         + 在 authorization_rules_task.py / admission_task_routes.py 各加一行
tests/community/endpoints/test_task_config_router.py
```

> 评审若认为应放在兄弟 `core/task_config/`,契约不变,机搬迁即可——这是放置决定,
> 不是契约决定。

#### 内部面挂载

`adapters/http/task/router.py` 是 umbrella(`prefix="/api/v1/collaboration/tasks"`),
已用 `include_router` 挂了 relay/bbs/intake/... 等子路由。新增
`adapters/http/task/task_config_router.py`(`APIRouter(prefix="/config", tags=["task-config"])`)
挂在 umbrella 上(`task_internal_router.include_router(task_config_router)`),handler
用 `user: AuthenticatedUser = Depends(get_current_user)` +
`service: TaskConfigServiceProtocol = Injected(...)`,响应 `Envelope[T]` +
`@envelope_errors`。镜像 `set_task_setting`(`adapters/http/task/router.py:262`)。

#### 公开面挂载

在 `adapters/http/openapi_v1/task/router.py` 内直接加一个 match handler:
`principal: PrincipalDep` + `service: TaskConfigServiceProtocol = Injected(...)`,
`Envelope[T]` + `@envelope_errors`,与 `/dashboard`、`/trajectory` 同形。并登记:

- `authorization_rules_task.py`:`config/match` → `NoCheck(...)`(无 addressed-bot grant,
  match 不针对某个 bot 资源)。
- `admission_task_routes.py::TASK_PUBLIC_ADMISSION`:`config/match` → `AdmissionMode.OPEN`。

### 表结构

表 `task_config`(跟 `core/task` 一系用裸名,不用 `ac_` 前缀):

| 列             | 类型            | 约束                                                          |
|----------------|-----------------|---------------------------------------------------------------|
| `id`           | BIGINT PK       | auto                                                          |
| `config_name`  | VARCHAR(128)    | NOT NULL                                                      |
| `config_type`  | VARCHAR(32)     | NOT NULL,∈ `single_string|bool|set`                            |
| `config_value` | TEXT            | NOT NULL,JSON 编码值(见下)                                   |
| `env`          | VARCHAR(32)     | NOT NULL,**`default=get_current_env`(Python callable)**        |
| `creator`      | VARCHAR(128)    | 可空,首次写入时填,upsert 不覆盖                               |
| `modifier`     | VARCHAR(128)    | 可空,每次写入时填(含首次)                                     |
| `gmt_create`   | DATETIME        | server_default now,首次写入时设,upsert 不覆盖                 |
| `gmt_modify`   | DATETIME        | server_default now,onupdate now(每次写入刷新)                |

- `UniqueConstraint(config_name, env, name="uk_task_config_name_env")`
- `Index("idx_task_config_env", "env")`

> env 列默认值用 **callable `get_current_env`**(参考 `core/task/repository/models.py`
> 同款写法),而 **不**照搬 `system_config/orm.py` 的 `default="prod"` 字面量。理由:
> router 总会显式传 env,默认值只是回退;但若回退成字面量 `"prod"`,任何漏传 env 的插
  入会静默落到 prod,恰好踩中最严重那条 anti-goal。callable 形式在插入期按当前 env 求
  值,更安全,且 core/task 已有先例。

### DDL(OceanBase / MySQL 平行,落地放 `core/task/task_config/sql/2026_11_02_task_config.sql`)

```sql
-- task_config: 按 (config_name, env) 维护的通用类型化配置存储。
-- config_type ∈ single_string|bool|set,决定 config_value(JSON 文本)的解释与匹配方式;
-- 详见 spec 2026-11-02-task-config。env 由后端按当前部署上下文填充,不入接口参数,
-- 列级无 DEFAULT(路由恒显式传 env);ORM 侧以 callable get_current_env 作回退兜底。
-- config_value 取 TEXT(64KB):set 元素为 bot_id 列表、其余为单值 JSON,均远小于 64KB,
-- 不必上 mediumtext。config_type 无 DB 枚举约束(governed 枚举在写入/匹配路径做校验)。
-- 无外键(task 域表间不建 FK,与 task_artifact / task_trajectory 同款)。
-- 唯一键 (config_name, env) 支撑 upsert 的 ON DUPLICATE KEY UPDATE /
-- ON CONFLICT(config_name, env) DO UPDATE;idx_task_config_env 支撑按环境过滤。
CREATE TABLE IF NOT EXISTS `task_config` (
    `id`           bigint(20)    NOT NULL AUTO_INCREMENT                       COMMENT '主键ID',
    `config_name`  varchar(128)  NOT NULL                                      COMMENT '配置名(自由字符串,按需创建;未做预定义清单枚举)',
    `config_type`  varchar(32)   NOT NULL                                      COMMENT '配置类型 single_string|bool|set,决定 config_value 解释与匹配方式(governed 枚举,DB 不约束)',
    `config_value` text          NOT NULL                                      COMMENT '配置值(JSON 文本):single_string=字符串,bool=布尔,set=字符串元素数组;TEXT 家族禁显式 DEFAULT',
    `env`          varchar(32)   NOT NULL                                      COMMENT '数据环境 dev|pre|prod(后端按部署上下文填,不入接口参数;ORM callable 兜底 get_current_env)',
    `creator`      varchar(128)  NULL                                         COMMENT '创建者(operatorName 或 staffId;首次写入填,upsert 不覆盖;NULL=未记录)',
    `modifier`     varchar(128)  NULL                                         COMMENT '修改者(每次写入刷新含首次;NULL=未记录)',
    `gmt_create`   timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP            COMMENT '创建时间(首次写入设,upsert 不覆盖)',
    `gmt_modify`   timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间(每次写入刷新)',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_config_name_env` (`config_name`, `env`),
    KEY `idx_task_config_env` (`env`)
) DEFAULT CHARSET = utf8mb4 COMMENT='通用类型化配置存储(spec 2026-11-02-task-config)';
```

> SQLite/test 侧不跑这份 DDL,由 ORM `Base.metadata.create_all` 建表;两者列与约束
> (PRIMARY KEY / UNIQUE (config_name, env) / INDEX env)必须保持平行,否则 test 与
> corp 行为分叉。`uk_task_config_name_env` 的列顺序与 ORM `UniqueConstraint`
> `(config_name, env)` 一致,upsert 的冲突目标以此为名。

### 值编码与匹配语义

`config_value` 一律存 **JSON 文本**,按 `config_type` 解释:

| `config_type`  | 存储形态(JSON)        | match 入参形态 | 匹配判定                            |
|----------------|------------------------|----------------|-------------------------------------|
| `single_string`| `"some string"`       | string         | `input == stored`(字符串精确相等)  |
| `bool`         | `true` / `false`       | bool           | `input == stored`(布尔相等)        |
| `set`          | `["a","b","c"]`(字符串元素)| string(单元素)| `input in stored`(成员判断)        |

- match 入参 `value` 是 JSON 值,后端按存行的 `config_type` 校验形态:
  `single_string` 收 string、`bool` 收 bool、`set` 收 string(被判定的元素)。形态不符
  → 请求体层面能判定的(缺字段、`value` 非 str|bool、`config_type` 非 enum)由
  pydantic 返回 422;依赖存行 type 才能判定的形态不符由 service 抛域异常,经
  `@envelope_errors` 映射(镜像现存 task 路由的失败处理,不构造 `Envelope(success=False)`)。
- 写入入参 `value` 同理:`set` 收字符串数组;形态不符报错。
- **写时若同名记录已存在但 `config_type` 不同,整体覆盖 `config_type` + `config_value`**
  (upsert 按 `(config_name, env)`,`config_type` 不固定)。

### 接口

| 方法 | 完整路径                                                  | surface  | 鉴权                                  |
|------|----------------------------------------------------------|----------|---------------------------------------|
| POST | `/api/v1/collaboration/tasks/config/set`                 | 内部     | `get_current_user`                    |
| POST | `/api/v1/collaboration/tasks/config/match`               | 内部     | `get_current_user`                    |
| POST | `/openapi/v1/collaboration/tasks/config/match`           | 公开     | `require_principal`(Gateway 签名)     |

三者通过 `Injected(TaskConfigServiceProtocol)` 共享同一 service。`env` 在每个
handler 内 `env_utils.get_current_env()` 取,不入参。

**set**(请求体 / 200 Envelope):

```jsonc
{ "config_name": "task_skill_installed_bots", "config_type": "set",
  "config_value": ["bot_a:owner_a","bot_b:owner_b"] }
// task set 元素是 `bot_uuid`（`bot_id:owner_id`），不是裸 `bot_id`。
// -> { ... Envelope, "data": { "config_id": <id> } }
```

- 校验 `config_type ∈ {single_string,bool,set}` 且 `config_value` 形态匹配 → 否则
  error。
- `actor = user.operatorName or user.staffId`:首次插入填 `creator` + `modifier`,
  冲突更新时只刷 `modifier`(不覆盖 `creator`)。
- upsert:`dialect.name == 'sqlite'` 用 `INSERT ... ON CONFLICT(config_name,env) DO
  UPDATE`(DO UPDATE 子句只刷新 `config_type` / `config_value` / `modifier` /
  `gmt_modify`,`creator` / `gmt_create` 不在更新列里);`mysql` 用
  `INSERT ... ON DUPLICATE KEY UPDATE` 同理(只更新 `config_type` / `config_value` /
  `modifier` / `gmt_modify`)。

**match**(请求体 / 200 Envelope,只回布尔):

```jsonc
{ "config_name": "task_skill_installed_bots", "value": "bot_a:owner_a" }
// -> { ... Envelope, "data": { "matched": true } }
```

- 配置名不存在 → `matched: false`(不是 error)。
- 按 `config_type` 校验 `value` 形态 → 否则 error。
- **只回 `matched` 布尔,不回存储值。**

> 三个 handler 的 Envelope/error 码细节,落地时镜像 task surface 的 `Envelope[T]` +
> `@envelope_errors` 既有约定(参考 `/settings`),不引入新的信封风格。

### 鉴权

- `set` 与内部 `match`:`Depends(get_current_user)`,任意已认证用户可读写任意配置名,
  不用 `require_operator`。
- 公开 `match`:`require_principal`(Gateway 签名 `X-Avernet-Principal`),ADMISSION 设
  为 `OPEN`(对应"任意已认证 principal 可读")。
- 不做 per-config 鉴权。

### DI 绑定(必须,否则测试与 router 都解析不到)

`di/modules/task_config_module.py` 同时把 **`TaskConfigService` 具体类** 与
**`TaskConfigServiceProtocol`** 绑到同一 singleton(参考 `SystemConfigModule`),并在
`di/container.py` 的 base 模块列表里 `import` + 实例化该模块(紧邻
`SystemConfigModule()` 那行)。只绑具体类、不绑 Protocol / 不注册 container,会导致
`Injected(TaskConfigServiceProtocol)` 与测试里
`world.get(TaskConfigServiceProtocol)` 双双 `MissingDependencyError` —— router 500、
coverage gate happy 用例挂、全量测试挂。

### 测试(声明式,配 coverage gate)

`tests/community/endpoints/test_task_config_router.py`。coverage gate 按字面
`(method, path)` 计,三条活路由要 **3 个 happy + 3 个 error**:

| 路由                                                | happy                                                | error                                              |
|-----------------------------------------------------|-----------------------------------------------------|----------------------------------------------------|
| `POST /api/v1/collaboration/tasks/config/set`       | 写入 set 配置 → 200,`data.config_id` 存在;可加 `extra_assertions` 用 `world.get(TaskConfigServiceProtocol)` 回查 | `config_type` 不合法 → 422 / 应用层 error |
| `POST /api/v1/collaboration/tasks/config/match`     | seed `set` 含 `["bot_a"]`,match `"bot_a"` → `matched:true`(兼测 `"bot_z"`→`false`) | `value` 类型与 `config_type` 不符 → error |
| `POST /openapi/v1/collaboration/tasks/config/match` | 同上 match happy,但需带**合法签名 principal**(`X-Avernet-Principal`) | **无/非法 principal → 401**(按公开面 `require_principal` 失败语义) |

- 内部面用 `{"x-user-id": "u_task_config_test"}`(本地存根鉴权读这个头)。
- **公开面 match 的 happy 用例需要一份合法的签名 principal** ——
  落地时先看现存 `/openapi/v1/collaboration/tasks/*` 路由在 `tests/community/endpoints/`
  里是怎么过 gate 的(有 cases 就照搬其 principal 生成方式;若它们都在
  `coverage_baseline.txt` 里豁免、本路由是首个,则在 `framework/fixtures.py` 复用其
  principal-verifier 的存根配置,产一个合法 token)。这是本设计唯一未完全核实的实操点,
  在 plan 阶段确认。
- **seed 必须调 `get_current_env()`**(测试 profile 下解析为 `dev`),不硬编码 `"dev"`:
  若日后 conftest 改了 `SERVER_ENV`,硬编码会让 `match` happy 静默返 `false`、gate 挂。
- model 必须登记进 `schema.import_all_models()` 才能在测试的全新内存 SQLite 上建表。

## 留给 builder 的

- 每个文件的具体行级实现(本设计给契约与拆分)。
- `Envelope[T]` + `@envelope_errors` 的具体 error 码与 body 形态,落地镜像 `/settings`。
- `bool` 配置在 match 入参时的 JSON 传输细节,按 pydantic schema 落,保持
  `json.loads(config_value)` 直接可比即可。
- 公开面 match 测试的 principal 生成方式(见上,plan 阶段先核实)。
- 是否再加 `GET /api/v1/collaboration/tasks/config/get` 回当前值:**本版不实现**,避免
  存储值泄露;如后续确需管理面再看。

## 我做的几个决定(供你过文档时一并核对)

1. 核心放 `core/task/task_config/`(task 域内自洽子包,与 `api/task/*`、`core/task/*`
   同形),不另起兄弟 `core/task_config/`——因为路由挂在 task 两个 surface、protocol
   注入也走 `api/task/`,放 task 域更内聚。
2. 表用裸名 `task_config`,跟 `core/task` 一系对齐,不用 `ac_` 前缀。
3. `config_value` 统一 JSON 编码存储,一列解释三种类型。
4. 写时允许整体覆盖 `config_type`(同名记录类型可变),靠 upsert。
5. match 对未知配置名返 `matched:false`,不报错。
6. env 列默认值用 callable `get_current_env`,不用字面量 `"prod"`(防 anti-goal)。
7. 鉴权:内部 `get_current_user`,公开 `require_principal` + `AdmissionMode.OPEN`,
   不做 per-config 鉴权、不用 `require_operator`。
8. 响应信封用 task surface 的 `Envelope[T]` + `@envelope_errors`,不用 `ApiResponse`。
9. 本版不实现"读取配置值"接口,避免存储值泄露。
