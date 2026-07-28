# 消金融事业部 OpenAPI 接入设计 — 文件/定时任务/Identity 三板块

- **日期**: 2026-07-27
- **版本**: v0.2(合并版,设计 + 实现合一)
- **范围**: 在 `src/backend` 现有 `openapi_v1` 公共命名空间下,把**文件(resources)、定时任务(routines)、identity** 三个 category 的 stub handler 接到真实 service,并补齐方向 A 的租户隔离
- **负责人**: lucas-xzp(对应交接文档 §分工):resources(P1,9 端点)、routines(P1,7 端点)、identity(P2,3 端点)。mcp/bots/channels 归 totalfrank,skills 共担,均不纳入本轮
- **性质**: 设计 + 精确到方法的实现计划,基于代码事实,每条标 `文件:行号` 可核
- **硬约束**: ① 不影响 legacy `/api/...` 线上接口 ② service/core 尽量共用,不合理处可在不改 signature 前提下优化 ③ 不影响线上生产——尤其不能因加 ORM guard 改变现有线上查询结果
- **关联**: PR #456(tenant isolation Stage 1);交接文档 `src/backend/docs/openapi-v1/README.zh-CN.md`;SDD `src/backend/specs/2026-07-26-tenant-isolation-foundation/`

---

## 1. 价值与目标

### 1.1 为什么做这件事

OCB 是多模块 monorepo,backend 的 `openapi_v1` 公共 API 已有路由定义但 handler 全是 stub。它的调用方是**外部注册租户**,内部 `/api/...` 调用方不是。**两者共享同一批表、repository、service**。所以:

- 一个能返回真实数据的公共端点,若底下没隔离,**第一个接通的对外 endpoint 就会读到内部租户数据**(PR #456 body 原话)。
- 隔离不能 endpoint-by-endpoint 加,**必须在两套 API 界面之下同时生效**。

PR #456 已交付 `ac_bots` 单表隔离 + 全局 tenant ContextVar + middleware + 两个 seam(`require_principal`/`resolve_avernet_tenant`)。后续"按 category 接 handler"= Session 1/N,**本设计就是 resources/routines/identity 这三个 category 的 Session**,必须先把这三个 category 涉及的数据表隔离补到 Session 0 同款水平,handler 才能安全接出来。

### 1.2 三板块的两个本质差异(已代码级确认)

| 板块 | 数据落在哪 | 隔离方式 | 本期 DB 改动 |
|---|---|---|---|
| **resources** | `ac_resource` 表(`plugin_api/models.py:189`) | 表加列 + 复制 Session 0 同款 ORM 双 guard | **要加列 + guard** |
| **routines** | backend **无表**(纯 HTTP 中继到 engine,数据在 device JSON) | 隐式靠 `ac_bots`(已 guard)——`cron_relay.py:810` `get_bot(bot_id)` 跨租户拿不到 bot 就转发不了 | **不动 DB**,验证间接隔离链路有效 |
| **identity** | 设备 FS 的 `identity/<file_type>.md`(无表) | 隐式靠 `ac_bots`——`identity.py:266` `resolve_engine_for_bot` → bot_repo(已 guard)→ `resolver.resolve_for_bot` 抛错 | **不动 DB**,验证间接隔离链路有效 |

**核心结论**:你原本担心的"给三张表加 `avernet_tenant`"——实际**只有 `ac_resource` 一张表要加**,另外两个板块根本没有可加列的表,它们的隔离已在 Session 0 交付 `ac_bots` guard 时自动生效,只是当时没人核对这两个间接路径。

### 1.3 方向 A 在本期边界内意味着什么

方向 A = gateway 转发 signed principal → `resolve_avernet_tenant` 从 principal 取 tenant → set 进 ContextVar。但 gateway 的 PrincipalSigner/Verifier seam 当前是 **0 行代码**(gateway 团队未交付)。

**本期边界**:只负责 backend 侧——保证"换一个非 `teamclaw` 的 tenant 上来,`ac_resource` 隔得住;间接路径依然成立"。`resolve_avernet_tenant` 的真实填写依赖 gateway seam,**不在本期可交付范围**,但要留好 drop-in 点(§7)。在 gateway seam 落地前,`resolve_avernet_tenant` 维持返回 `DEFAULT_AVERNET_TENANT`,所有数据仍属 `teamclaw`,线上行为零变化。

### 1.4 不做什么(边界)

- **不做** OAuth 授权码流程、API 编排 DSL、生产级 WAF、复杂租户计费
- **本期不接 mcp/bots/channels**(归 totalfrank)、**skills 共担暂不纳入**(P3,待 P1/P2 跑通后另起子计划)
- **不重写已有 backend 业务逻辑**:legacy `/api/...` 路由和其认证原样保留,只在 `openapi_v1` 这套补对接
- **本期不动 `ac_bot_publish` 表加列/guard**:该表只被 cron(verify/online 运行态)和 identity(带 publish_id)读取,而 openapi_v1 的 routines 走 DRAFT 运行态(`forward_request:601` 不经 `_publish_repo`)、identity 不暴露 publish_id——故本期三个板块的 handler **不会触发对 `ac_bot_publish` 的读取**。该表隔离留给其真正 owner(totalfrank / service_bot 工作线),或等 routines/identity 的 handler 真要支持 verify/online 运行态时再处理。**YAGNI**
- 消金适配本设计理念,**所有产品契约按当前设计定,不向消金二次确认**

---

## 2. 现状精查(代码级事实)

### 2.1 resources 板块

**数据表**:`ac_resource`,`ResourceModel`(`plugin_api/models.py:189-230`)。字段:`user_id`(`:208`)、`created_by`(`:209`)、`source`(`:210`)、`bolt_id`(`:211`,关联的 bot_id)、`env`(`:212`)。**无 `avernet_tenant` 列**。

**Repository**:`ResourceRepository`(`plugins/resource_repository.py`),`ResourceRepositoryProtocol`(`core/resources/repository/protocol.py`)唯一 DI 实现(`di/modules/resources_module.py:54-58` singleton)。写路径**全部 ORM,0 处裸 SQL**:

| 方法 | 位置 | 形态 | 加 guard 后 |
|---|---|---|---|
| `create` | `resource_repository.py:117-137` | `db.add(row)+db.flush()` | ✅ 触发 `before_insert` |
| `update` | `resource_repository.py:139-162` | `query().filter(id=).first()` + `setattr` + `flush` | ✅ SELECT 走 read guard |
| `delete`(软) | `resource_repository.py:164-180` | `Query.update({...})` | ✅ `do_orm_execute` 覆盖 |
| `hard_delete` | `resource_repository.py:182-191` | `Query.delete()` | ✅ `do_orm_execute` 覆盖 |

**Service**:`ResourceService`(`core/resources/services/resource_service.py`)per-request 构造(`:102-150`,接 `bot_id/entity_id/user_id/engine_type/entity_type`)。`list_resources`(`:201-224`)用 `bolt_id=self._bot_id` 过滤——现有归属维度,与 tenant 正交。

**注入点**:`ResourceServiceFactoryProtocol.create(*, bot_id) -> ResourceServiceProtocol`(`api/resource_service.py:31`),legacy `/api/resources` router 用的同一 factory(`adapters/http/resources/router.py:59,187`)。

**legacy HTTP 入口**:`adapters/http/resources/router.py`(prefix=`/api/resources`,`:92`)。download/preview 先 `legacy_svc.get_resource(id)` → repo `get_by_id` → 设备 FS 读字节(`:791,814`)。另有 `/files/...`(`file_router.py:51`)是**纯 FS、不经 DB**(`:94`)。

### 2.2 routines 板块

**数据表**:**backend 无 routine 表**(精查全库 `__tablename__`,无 `ac_routine`/`ac_cron`/`RoutineModel`/`CronJobModel`)。`core/cron/` 下只有 `protocols.py`/`services/`/`dependencies/`,无 models。routine 真实持久化在 engine 侧 device 上的 JSON(`src/engine/.../claude_code_gateway/src/cron/store.ts`),backend 完全不碰。

**backend 是纯 HTTP 中继**:`CronRelayService`(`core/cron/services/cron_relay.py:60`)。`CronRelayServiceProtocol`(`api/cron_relay_service.py:8`,7 方法 loose `*args`)。真实实现 `CronRuntimeOperationsMixin`(`core/cron/services/cron_runtime_operations.py`):

| protocol 方法 | 实现位置 |
|---|---|
| `get_cron_status` | `cron_runtime_operations.py:310` |
| `get_cron_detail` | `:326` |
| `create_cron` | `:345` |
| `update_cron` | `:363` |
| `delete_cron` | `:415` |
| `run_cron` | `:436` |
| `get_cron_runs` | `:478` |

**隔离点(关键)**:`forward_request`(`cron_relay.py:780-875`),第 810 行 `bot = self._bot_provider.get_bot(bot_id, user_id)`,注释明写"获取 bot 信息(隐式权限检查)"。`get_bot` 走 `BotRepository` → ac_bots 已 Session 0 guard。**跨租户拿不到 bot → `resolver.resolve_for_bot`(`:845`)抛错 → 无法转发 → routine 不可见**。`list_bots_by_owner_or_collaborator`(`:120-125`)同样走 guard。

> **与交接文档 Track A 阶段 6 的关系**:交接文档把"例程"列为 Track A 阶段 6(lucas-xzp,P1,完成判据"列+守卫+测试")。但那套判据**只适用于有表的类别**;routines 无表故判据特殊化:= §6.4 `test_routine_cross_tenant_rejected_at_bot_resolve` 绿。Session 0 spec.md:83 把 routines 列为"待隔离数据"也是指这条间接链路,不是加列。

**潜在漏点**:`cron_relay.py:542,713` 调 `self._publish_repo.get_latest_by_source_bot_id_and_owner_and_status` 读 `ac_bot_publish`。该表(`core/service_bot/repository/models.py:149 BotPublishModel`)**不在 Session 0 guard 范围**,跨租户构造 publish_id 能漏。

### 2.3 identity 板块

**数据表**:**无 identity 表**。`IdentityFileType` 枚举(`openapi_v1/identity/schemas.py:10-28`)16 种(RULES/OKR/SAFETY/SOUL/OUTPUT/MEMORY/IDENTITY/AGENTS/USER/TOOLS/HEARTBEAT/BOOTSTRAP/KNOWLEDGE/CLAUDE/GREETING/README),物理存储 = 设备 FS 的 `identity/<file_type>.md`(`schemas.py:11`)。

**Service**:`IdentityService`(`core/services/identity.py`)直接注入单例。核心公共方法:
- `get_bot_file(entity_type, entity_id, bot_id, file_type, operator_id, publish_id=None, engine_type=None)`(`identity.py:457-489`)
- `update_bot_file(entity_type, entity_id, bot_id, file_type, content, operator_id, ...)`(`identity.py:544-546+`)
- `read_identity_file` / `write_identity_file`(`:250-288`,provider-blind coordinate-based,干净公共方法)

**隔离点(关键)**:`get_bot_file:463` `resolve_engine_for_bot(bot_id, entity_id, ..., bot_repo=self._bot_repo)` → 读 ac_bots(已 guard)。**跨租户构造 bot_id → bot_repo 返回 None → `resolver.resolve_for_bot(bot_id, owner_id)`(`:214`)抛 `DeviceNotBoundError` → identity 文件不可读不可写**。

**legacy HTTP 入口**:`adapters/http/identity/router.py`(prefix=`/api/identity`,`:40`)。bot-level 端点 `/api/identity/{entity_type}/{entity_id}/bot/{bot_id}/{file_type}`(`:46,78`),4 个参数全在 path,`operator_id = user_id(query) or ctx.user_id`(`get_bot_identity_file` 函数体)。

**潜在漏点(同 routines)**:`identity.py:513` `self._publish_repo.get_by_id(int(publish_id))` 读 `ac_bot_publish`,不在 guard 范围。

---

## 3. 设计方案(三板块)

### 3.1 resources:加列 + guard + handler 接通

#### 3.1.1 `ac_resource` 加 `avernet_tenant` 列(同款 Session 0)

`plugin_api/models.py:189` `ResourceModel` 加列,与 `BotModel`(`:67`)完全一致:

```python
avernet_tenant = Column(
    String(64), nullable=False, server_default="teamclaw",
    comment="data-isolation tenant; see utils/avernet_tenant",
)
```

**不进 `to_dict()`**(与 `BotModel` 决策一致,API 响应 body 零变化)。不进不影响 `ResourceService` 用 dict 重建 `Resource(**item)`,`avernet_tenant` 本就不是 `Resource` pydantic 字段。

#### 3.1.2 扩展 guard 到 `ResourceModel`(工厂化,见 §4)

把 `_GUARDED_MODELS = (BotModel, ResourceModel, BotPublishModel)`,read guard 内遍历各加 `with_loader_criteria`,insert guard 按 model 各注册一份。

#### 3.1.3 handler 接通(9 端点 → service 方法)

注入 `ResourceServiceFactoryProtocol`,`svc = factory.create(bot_id=...)` per-request,与 legacy 同一工厂同一 service 类,行为零变化。

| openapi_v1 op | service 调用 | service 位置 |
|---|---|---|
| `GET ""` list_resources | `svc.list_resources(resource_type=type.value if type else None)` | `:201-224` |
| `GET /check-name` | `svc.check_name_exists(name=name, resource_type=ResourceType.FILE)` | `:152-178` |
| `POST ""` create_resource | 按 `body.type`:LINK→`create_url_resource`、FOLDER→`create_directory`、URL 归并 LINK、FILE 走 upload | `:543,448` |
| `POST /upload` | `svc.upload_file(data=content, filename=name, device_fs=<resolved>)` | `:302-375` |
| `GET /{id}` | `svc.get_resource(resource_id)`(None→404) | `:617` |
| `PUT /{id}` | `svc.update_resource(id, ResourceUpdate)`(R2 新增统一入口) | 新增 |
| `DELETE /{id}` | `svc.delete_resource(id)` 软删 / `delete_file_resource`(FILE) | `:434-446` |
| `GET /{id}/download` | `svc.get_resource(id)` → `device_fs.read_file` 流式(**裸 Response,不包 envelope**) | legacy `router.py:772-814` |
| `GET /{id}/preview` | legacy preview 逻辑,映射 `Preview` | 待核 legacy |

**字段映射**(写 `_to_openapi_resource(legacy) -> openapi.Resource`):legacy `Resource`(`core/resources/models.py:34-49`)是 `id`/`resource_type`(FILE/URL/NODE/LINK/DATABASE/API)/`attributes` dict/`gmt_created`;openapi `Resource`(`openapi_v1/resources/schemas.py:18-28`)是 `resource_id`/`type`(FILE/LINK/FOLDER)/`source`/`url`/`size`/`gmt_create`。映射:`resource_id=str(id)`、`type=resource_type`、`url=attributes.url`、`size=attributes.size`、`gmt_create=gmt_created.isoformat()`。

**枚举对齐(已定)**:openapi 契约固定 FILE/LINK/FOLDER;外部 URL 归并进 LINK(url 写进 attributes);NODE/DATABASE/API 不对外暴露。

### 3.2 routines:不动表 + handler 接通(7 端点)

#### 3.2.1 不动表,隔离已生效

无 Track A(无表)。隔离靠 `forward_request:810` `get_bot` → ac_bots guard。本期不动 cron_relay 代码,只加 conformance test(§6.4)。(`ac_bot_publish` 漏点见 §1.4 说明,本期不做。)

#### 3.2.2 handler 接通

注入 `CronRelayServiceProtocol`。所有写操作内部转 `forward_request(bot_id, user_id, ...)`。

| openapi_v1 op | service 调用 | bot_id 来源 |
|---|---|---|
| `GET ""` list_routines | `list_crons(bot_id, user_id, status)`(C1 新增) | query `bot_id`(建议改必填) |
| `POST ""` create_routine | `create_cron(bot_id, user_id, body)` | `body.bot_id`(`routines/schemas.py:35` 已含) |
| `GET /{routine_id}` | `get_cron_detail(bot_id, user_id, routine_id)` | **C3**:加必填 query `bot_id` |
| `PATCH /{routine_id}` | `update_cron(bot_id, user_id, routine_id, body)` | 同 C3 |
| `DELETE /{routine_id}` | `delete_cron(bot_id, user_id, routine_id)` | 同 C3 |
| `POST /{routine_id}/run` | `run_cron(bot_id, user_id, routine_id)` | 同 C3 |
| `GET /{routine_id}/runs` | `get_cron_runs(bot_id, user_id, routine_id)` | 同 C3 |

**C3 处理(已定)**:`Routine` schema(`routines/schemas.py:18-29`)`routine_id` 与 `bot_id` 独立,path 只含 `routine_id`,但 forward 要 bot_id。backend 无 routine 表无法反查,**采用方案 (b)**:`GET/PATCH/DELETE /{routine_id}` 加必填 query `bot_id: str`。openapi router 是 stub 未上线,加 query 不影响线上(legacy `/api/cron`)也不断契约。调用方按设计理念每次带 bot_id。

**C1 处理(已定)**:无 `list_cron`,在 `CronRuntimeOperationsMixin` 新增 `list_crons(bot_id, user_id, status=None)`,内部 `forward_request(GET /api/cron, bot_id, ...)` 再按 status 过滤(service 优化)。

### 3.3 identity:不动表 + handler 接通(3 端点)

#### 3.3.1 不动表,隔离已生效

无 Track A(无表,靠 bots 间接)。本期不动 identity service 代码,只加 conformance test(§6.4)。

#### 3.3.2 handler 接通

注入 `IdentityService`(单例),复用 `get_bot_file`/`update_bot_file`。

| openapi_v1 op | service 调用 |
|---|---|
| `GET /bot/{bot_id}` list_bot_identity_files | `list_bot_files(bot_id, entity_type, entity_id, operator_id)`(I1 新增) |
| `GET /bot/{bot_id}/{file_type}` | `get_bot_file(entity_type, entity_id, bot_id, file_type, operator_id)` |
| `PUT /bot/{bot_id}/{file_type}` | `update_bot_file(entity_type, entity_id, bot_id, file_type, content, operator_id)` |

**I1 处理(已定)**:无 bot-level list 方法,在 `IdentityService` 新增 `list_bot_files(bot_id, entity_type, entity_id, operator_id)`,内部对 `IdentityFileType` 16 种枚举循环 `read_identity_file` 探存在性(空串=不存在),返回 `<IdentityFileRef>{file_type, exists}` 列表。

**I2 处理(已定)**:openapi 路径 `/bot/{bot_id}/{file_type}` 缺 `entity_type`/`entity_id`(legacy 在 path)。方案 (b):从 principal 解析(方向 A),本期 principal=None 走 fallback `entity_type="staff"`、`entity_id=operator_id=<bot 的 owner_id>`(从 `bot_repo.get_bot(bot_id).owner_id`,对齐 legacy `identity.py:482` `owner_id = entity_id if entity_id else operator_id`)。

**I3 处理(已定)**:openapi_v1 不暴露 `publish_id`;消金只读 draft bot identity(走 `get_bot_file` 默认分支,不带 publish_id)。若后续需读已发布 bot,再开 query 参数。

---

## 4. guard 工厂(PR #456 模板 + 多 model 扩展)

PR #456(`45b71f1b`)的 guard 实现是权威模板,完整 diff 已核。read guard 注册在 `Session` 类(全局一条),insert guard 按 model 各注册一份(per-mapper)。

```python
# plugin_api/models.py 扩展(关键:用直接表达式,绝不用 lambda)
class CrossTenantInsertError(RuntimeError): ...

_GUARDED_MODELS = (BotModel, ResourceModel)  # 后续表追加(如需 ac_bot_publish 等)

def _avernet_tenant_read_guard(orm_execute_state) -> None:
    if orm_execute_state.is_column_load or orm_execute_state.is_relationship_load:
        return
    if not (orm_execute_state.is_select or orm_execute_state.is_update or orm_execute_state.is_delete):
        return
    if orm_execute_state.execution_options.get("skip_avernet_tenant_guard"):
        return
    stmt = orm_execute_state.statement
    for m in _GUARDED_MODELS:
        # 直接表达式!lambda 会被缓存钉死第一个 tenant 造成泄漏(Stage 1 踩坑)
        stmt = stmt.options(with_loader_criteria(
            m, m.avernet_tenant == get_current_avernet_tenant(), include_aliases=True))
    orm_execute_state.statement = stmt

def _make_insert_guard(model_cls):
    def _guard(_mapper, _connection, target):
        current = get_current_avernet_tenant()
        if target.avernet_tenant is None:
            target.avernet_tenant = current
        elif target.avernet_tenant != current:
            raise CrossTenantInsertError(f"{model_cls.__name__} insert names tenant ...")
    return _guard

def _install_avernet_tenant_guards() -> None:
    global _AVERNET_TENANT_GUARDS_INSTALLED
    if _AVERNET_TENANT_GUARDS_INSTALLED:
        return
    event.listen(Session, "do_orm_execute", _avernet_tenant_read_guard)  # 单 listener
    for m in _GUARDED_MODELS:
        event.listen(m, "before_insert", _make_insert_guard(m))  # 每 model 一份
    _AVERNET_TENANT_GUARDS_INSTALLED = True

_install_avernet_tenant_guards()
```

`ac_bot_publish`(`core/service_bot/repository/models.py:149`)**本期不加列**(见 §1.4:openapi_v1 handler 不读该表)。若未来 routines/identity 需支持 verify/online 运行态或 publish_id,再按同款方式加。

---

## 5. 缺口汇总(全部已定)

| 缺口 | 板块 | 状态 | 解法 |
|---|---|---|---|
| R1 字段映射 | resources | ✅ 已定 | `_to_openapi_resource()`:id→resource_id、attributes flatten、gmt_created→gmt_create |
| R1a 枚举对齐 | resources | ✅ 已定 | openapi 固定 FILE/LINK/FOLDER;URL 归并 LINK;NODE/DATABASE/API 不暴露 |
| R2 update 通用入口 | resources | ✅ 已定(service 优化) | `ResourceService` 新增 `update_resource(id, ResourceUpdate)`,LINK 走 `update_link_resource`,通用走 `repo.update` |
| R3 device_fs 解析 | resources | ✅ 已定 | 复用 legacy `DeviceFilesystemDispatcher.for_bot(bot_id, owner_id)`;本期 owner_id 从 `bot_repo.get_bot(bot_id).owner_id` |
| C1 list_cron 缺失 | routines | ✅ 已定(service 优化) | `CronRuntimeOperationsMixin` 新增 `list_crons(bot_id, user_id, status)` |
| C2 RoutineCreate 含 bot_id | routines | ✅ 已核实(非缺口) | `routines/schemas.py:35` 已含 `bot_id: str` |
| C3 routine_id→bot_id 反查 | routines | ✅ 已定(方案 b) | `GET/PATCH/DELETE /{routine_id}` 加必填 query `bot_id` |
| I1 bot-level list 缺失 | identity | ✅ 已定(service 优化) | `IdentityService` 新增 `list_bot_files`,循环 16 种 file_type 探存在性 |
| I2 entity 参数来源 | identity | ✅ 已定(方案 b) | principal 解析(方向 A),本期 fallback `entity_type="staff"`+owner from bot |
| I3 publish_id | identity | ✅ 已定 | openapi 不暴露 publish_id,只读 draft |
| 漏点:ac_bot_publish | routines+identity 共用(仅 verify/online 运行态或带 publish_id 时读) | ⏸ **本期不做** | 本期 openapi_v1 handler 不触发该表读取(§1.4 YAGNI);留给 service_bot owner 或后续阶段 |

**0 项待拍板。** 消金适配本设计理念,可直接进实现。

---

## 6. 生产安全(硬约束③)

### 6.1 加 ORM guard 不改线上查询结果

Session 0 plan.md:96-100 已论证:单租户 `teamclaw` 下,`avernet_tenant` 列 cardinality=1,`WHERE avernet_tenant='teamclaw'` 是 free residual filter(被 `owner_id`/`bot_id`/`status`/`binding_id` 等更选择性谓词主导)。扩到 `ac_resource` 同理。

**Gate**:现有 internal API suite **全量绿不修改**作 acceptance。本期同款要求:`pytest tests/community` 全量绿,无任何现有测试逻辑改动。

### 6.2 索引策略(本期不做,标记 F1)

`ac_resource` 加列后,tenant-leading 索引是**强制 corp policy**(交接文档 §横切事项 F2)。但本期同 Session 0 决策:**不在本期加索引**——cardinality=1,索引建了也不被选中。待消金(第二个真实租户)上线前做 create-before-drop 的 tenant-prepended composite 索引(plan §F1 跟踪)。**消金上线前必须补**,否则 tenant-list 查询会全表扫。

### 6.3 DDL 上线顺序(硬约束)

`ALTER TABLE` **必须先于读列代码部署**。`NOT NULL DEFAULT 'teamclaw'` 使反向顺序 safe(DDL 对当前线上代码 inert)。代码先于 DDL 部署会让 `SELECT avernet_tenant` 报错导致线上读全挂。遵循 Session 0 决策,DDL 由平台 out-of-band 执行,不入库 migration 文件,本文件记录 DDL shape:

```sql
ALTER TABLE ac_resource
  ADD COLUMN avernet_tenant VARCHAR(64) NOT NULL DEFAULT 'teamclaw'
    COMMENT 'data-isolation tenant; existing rows are the internal teamclaw tenant';
```

### 6.4 conformance 测试

| 测试 | 目的 | 红→绿 |
|---|---|---|
| `test_resource_cross_tenant_isolation` | tenant A resource 对 tenant B 不可见/不可改 | 加列+guard 前红,后绿 |
| `test_routine_cross_tenant_rejected_at_bot_resolve` | 跨租户 `forward_request(bot_id)` 在 `get_bot` 抛错 | Session 0 后即绿(固化契约) |
| `test_identity_cross_tenant_rejected_at_bot_resolve` | 跨租户 read/write_identity_file 在 resolve 阶段抛错 | Session 0 后即绿 |

(`ac_bot_publish` 的跨租户隔离测试本期不做——见 §1.4,本期内 handler 不读该表。)

### 6.5 不动 legacy `/api/...` 线上行为

- legacy 路由文件**零改动**
- legacy 用 `get_current_user`/`require_operator`/`get_request_context` 老 auth seam;openapi_v1 用 `require_principal`/`resolve_avernet_tenant` 新 seam,**两套独立**
- ORM guard 对 legacy 同样生效,但 legacy 请求走 `AvernetTenantMiddleware`(`middleware.py:144-160`,path 非 `/openapi/v1/` 时落 `DEFAULT_AVERNET_TENANT="teamclaw"`),**legacy 看到的就是 `teamclaw`,与历史一致**

### 6.6 团队已踩的坑与硬约束(from 交接文档)

1. **`with_loader_criteria` 用直接表达式,不用 lambda**(§4 已对齐)。lambda 会被缓存钉死第一个 tenant 造成跨租户泄漏,Stage 1 已验证。任何后续重构不可改 lambda。
2. **架构边界 README 声明**(`tests/community/architecture/` 硬校验)。新增跨模块 import(尤其 `utils.avernet_tenant` 进原本没它的模块)**必须在模块 `README.md` 的 `## Context Boundary` 声明**,否则 arch test 红。Stage 1 因未声明的 `utils.avernet_tenant` 导入两次 CI 失败。若 `core/resources/services/`、`core/services/identity.py`、`core/cron/services/` 新增这类 import,要同步改 README。
3. **guard 注册在 `Session` 类**(覆盖所有 runtime,含树外 corp DatabasePlugin),不注册在某个 plugin 上。
4. **`before_insert` 在 `server_default` 之前触发**,未设置 insert 在 guard 里是 `None` → stamp;`raise` 只在显式冲突 tenant 触发。保证列与 guard 不打架。
5. **本地开发坑**:`uv sync --default-index https://pypi.org/simple`(沙箱屏蔽 aliyun 镜像);pre-push 钩子沙箱跑不了 singlebox,`--no-verify` 推送依赖远端 CI(对 force-push 也适用);`git` 命令后 cwd 漂到仓库根,跑 `uv run` 前先 `cd src/backend`。

### 6.7 路径与端点形态对齐(交接文档标注的分歧)

- **路径以路由桩为准**:`/openapi/v1/bots/resources`、`/bots/routines`、`/bots/identity`(非 PR #363 顶层形态)
- **resources upload 用 `application/octet-stream`**(桩形态,非 PR #363 的 multipart)
- **identity 无 Track A 阶段**:靠 bots 间接隔离(Stage 1 ✅)

---

## 7. 方向 A 的 drop-in seam(本期留点,gateway 落地后填)

- **`require_principal`**(`adapters/http/openapi_v1/dependencies.py:21-23`)当前 `return None`。gateway PrincipalSigner 落地后换 JWT verifier。本期不动,handler 用 `PrincipalDep` 占位。
- **`resolve_avernet_tenant`**(`dependencies.py:26-39`)当前 `return DEFAULT_AVERNET_TENANT`。gateway seam 落地后从 principal 取 tenant。本期不动,落地前所有请求 tenant=`teamclaw`,§6 论证成立。
- **tenant 在 request 生命周期流动**:请求进 `/openapi/v1/bots/...` → `AvernetTenantMiddleware` 调 `resolve_avernet_tenant`(本期=`teamclaw`,方向 A=`consumer_finance`)→ `avernet_tenant_scope` set ContextVar → `require_principal`(本期 None)→ handler → `factory.create(bot_id)` → service → repository → ORM `do_orm_execute` read guard 自动加 `WHERE avernet_tenant=<ContextVar>`。**方向 A 落地后 handler 零改动**,正是 seam 设计意图。
- **消金 tenant 身份来源**(跨团队依赖):消金服务端 API Key → gateway `app_key` 策略 → `AppPrincipal.tenant="consumer_finance"` → 转发 → backend §resolve_avernet_tenant 取。本期 backend 不涉及,`teamclaw` 永不分配给消金(Session 0 已约束)。

---

## 8. 实现顺序(5 Phase,每步独立 PR 可回滚)

```
Phase 0(DB 层,不碰 handler,生产零影响):
  ac_resource 加列 + guard 工厂扩展(BotModel + ResourceModel) + conformance test
  ← handler 仍全 stub,legacy 走老链路 + guard 是 free residual,结果集不变

Phase 1(简单读 handler,无依赖):
  identity: GET/PUT /bot/{id}/{file_type}(I2 fallback)  ← 最简单
  resources: GET/{id} + GET "" + GET /check-name(R1 映射函数)

Phase 2(需 service 优化):
  resources: POST ""(type 分流,R1a)+ DELETE/{id} + PUT/{id}(R2)
  identity: GET /bot/{id}(I1)

Phase 3(需 device_fs):
  resources: POST /upload + GET/{id}/download + GET/{id}/preview(R3)

Phase 4(routines):
  C1 service → POST "" + GET "" → GET/PATCH/DELETE/{id} + /run + /runs(C3 加 bot_id query)
```

每 Phase 一个 PR,每 PR 自带单测 + `pytest tests/community` 全绿不修改 + `tests/community/architecture/` 绿。

**Phase 0 最关键**:它落地时 handler 全 stub,生产看到的结果集和今天一模一样。这是"不影响线上生产"约束能拿到最强保证的阶段,可放心先做。

---

## 9. 不在本设计范围

- gateway 侧 PrincipalSigner/Verifier(方向 A 的 gateway 段,gateway 团队)
- 消金 API Key 的 gateway 侧校验链路
- mcp/bots/channels 的隔离与 handler(归 totalfrank)
- skills(共担,本轮不纳入,P3 待 P1/P2 跑通后另起子计划)
- engine 侧 cron JSON 的 tenant 深度防御(物理隔离已足够,F2)
- 索引补齐(F1,待消金上线前)
