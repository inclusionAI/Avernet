# Design: caller 独立容器实例 (ac_expert_chat_instance)

> 原始需求草稿升级为可执行设计:补充现状分析、数据模型、设计决策、
> 实现蓝图、涉及文件、开放问题与验收。后续可按仓库 specs 约定再补
> `spec.md` / `plan.md` / `tasks.md`。

## 📋 实现状态总结

**已完成 (2026-07-13):**
- ✅ 核心服务 `ExpertChatInstanceService` 实现
- ✅ Repository 协议和 SQLite 实现
- ✅ ORM 模型定义 `AcExpertChatInstance`
- ✅ 版本快速检查路径(避免重复 baas 调用)
- ✅ 容器创建流程(`auto_approve_publish=True`)
- ✅ 容器升级流程(保持 bot_uuid 不变)
- ✅ 进度轮询机制(单次 poll + 完整快照保存)
- ✅ Connection 构建(通过 bot_uuid 直接获取 ws 信息)
- ✅ Request ID 幂等性(MD5 hash)
- ✅ 异常包装(`BotNotPublishedError`, `ConnectionError`)

**待实现:**
- ❌ BOT_NOT_FOUND 自动回退到 `create_bot`
- ❌ `release` 状态主动检测和健康检查
- ❌ `ExpertChatService.get_chat_session` 集成(caller 模式触发)
- ❌ DI 模块绑定配置

**待确认:**
- ⚠️ 实例数量上限/回收策略
- ⚠️ 健康检查接口设计
- ⚠️ DDL 全局索引保留问题

**代码实现细节见第 8 节。**

## 1. 背景与目标

专家对话入口 `ExpertChatService.get_chat_session(user_id, bot_id, owner_id)`
当前只走 owner 复用路径:service bot 通过
`BaasService.get_bind_id(bot_id, owner_id, "service", PublishStatus.SUCCESS)`
取 owner 名下的在线 binding (`ext.binding.online`),所有调用者复用同一个
在线容器。当 caller (`user_id != owner_id`) 发起对话时,系统没有为其单独
拉起的容器,只能挤在 owner 的容器上。

本需求:按 `bot_id` / `owner_id` 判定这个 bot 对当前调用者是 **caller 模式**
还是 **owner 模式**;caller 模式下给该 `user_id` **单独拉起一个容器实例**,
并在 `ac_expert_chat_instance` 表里按 `bot_id + owner_id + user_id + env`
唯一记录。容器被回收(baas 状态 release)时,在 **bot_uuid 不变** 前提下
重新拉起,返回 arca 链接信息(返回结构参考 `get_chat_session`)。

## 2. 现状分析 (代码探查)

| 关注点 | 现状 | 文件:行 |
|---|---|---|
| `get_chat_session` 返回 | `{session_key, is_new, connection}` | `expert_chat_service.py:253-337` |
| connection 产出 | `_get_connection` → `DeviceContextResolver.resolve_for_binding` → `BaasConnInfoBuilder` → `baas_service.get_ws_info` | `expert_chat_service.py:382`, `baas_service.py:1712` |
| caller/owner 判定 | 隐式:`_check_chat_access` 内 `owner_id == user_id` 即 owner 本人;无显式 caller_mode | `expert_chat_service.py:465-490` |
| service bot 在线 binding | `get_bind_id(..., SUCCESS)` → `BotPublishRepository.get_by_publish_bot_id` → `ext.binding.online` | `baas_service.py:1900` |
| 发布单扩展字段 | `ext` 存 `binding.online/verify`、`migration_path`;后者来自 build 产物 | `service_bot/repository/models.py:142`, `publish_flow_service.py:153` |
| baas 容器生命周期 | `create_bot`→`{bot_uuid, publish_id}`、`approve_publish`、`get_bot(health_check=True)`(404→`RELEASED`)、`get_ws_info` | `baas_service.py:745/1099/1966/1712` |
| `ac_expert_chat_instance` | **源码中无 entity/mapper/protocol**,仅本文件 DDL;范式参考 `AcExpertChatBotSession` | `expert_chat/sqlite_models.py:16` |
| env 解析 | `get_current_env()` → dev/pre/prod/singlebox | `utils/env_utils.py:87` |

关键缺口:无按 `user_id` 区分的容器实例;无 `ac_expert_chat_instance` 持久层;
baas 无「release 后原地复活、bot_uuid 不变」的现成接口(需用升级+回退组合)。

## 3. 数据模型

```sql
CREATE TABLE `ac_expert_chat_instance` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT COMMENT '主键',
  `bot_id` varchar(256) DEFAULT NULL COMMENT 'bot_id',
  `owner_id` varchar(256) DEFAULT NULL COMMENT 'owner_id',
  `user_id` varchar(256) DEFAULT NULL COMMENT 'caller_id',
  `status` varchar(32) DEFAULT NULL COMMENT '任务状态 init/success/failed',
  `ext` longtext DEFAULT NULL COMMENT '扩展字段(bot_uuid/service_bot_publish_id/baas_publish等)',
  `env` varchar(32) DEFAULT NULL COMMENT '环境标',
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '修改时间',
  PRIMARY KEY(`id`),
  UNIQUE KEY `uk_bi_oi_ui_e`(`bot_id`, `owner_id`, `user_id`, `env`) GLOBAL
) AUTO_INCREMENT = 33 DEFAULT CHARSET = utf8mb4 COMMENT = '互动容器实例表';
```

- **ORM** `AcExpertChatInstance`(对齐 `AcExpertChatBotSession` 范式):复用
  `AutoIncrementBigInteger, Base`;`ext` 用 `Text` 存 JSON,`to_dict()` 反序列化;
  唯一键用普通 `UniqueConstraint`(`bot_id, owner_id, user_id, env`)。

- **.status 状态机**(实际实现):
  - `init`:已建记录,容器创建中或等待轮询
  - `success`:容器已就绪,可通过 bot_uuid 获取 connection
  - `failed`:容器创建/升级失败(progress.status=FAILED)

  > 注:原设计 `active/release`,代码实现为 `success/failed`。`release` 状态未实现,
  > 回收复用通过版本检查快速路径处理(见 §4.1 版本快速检查)。

- **ext 结构**(完整字段):
  ```json
  {
    "bot_uuid": "baas container uuid",
    "service_bot_publish_id": 123,       // 关联的 success 发布单 ID
    "version": 3,                        // 发布单版本号,用于版本检查
    "baas_publish_id": "456",            // baas 工作流 ID(用于查询进度)
    "baas_publish": {                    // baas 工作流完整进度快照
      "status": "SUCCESS",
      "publish_id": "456",
      "devices": [...],
      // ... 其他 baas 返回字段
    }
  }
  ```

  - `service_bot_publish_id`:关联服务 bot 的 success 发布单 ID
    (用于反查构建物,实际已在主流程中通过 `publish_record` 获取,不再反查)
  - `baas_publish_id`:baas `create_bot`/`upgrade_bot` 返回的工作流 ID,
    用于调用 `get_publish_progress` 查询进度
  - `baas_publish`:保存最后一次进度查询的完整快照,用于审计和调试
  - `version`:发布单版本号,用于快速路径判断是否需要升级实例

## 4. 逻辑设计

新增独立服务 `ExpertChatInstanceService`(放 `core/expert_chat/services/`,
见 D6),承载 `ac_expert_chat_instance` 的容器生命周期编排。`ExpertChatService`
注入它:`get_chat_session` 在 caller 模式下调
`ExpertChatInstanceService.get_caller_connection(user_id, bot_id, owner_id)`
拿 `connection`,再按现有流程建/复用 `session_key`。

> `get_caller_connection` 返回结构 `{instance, connection, need_poll}`;
> `session_key` 仍由 `ExpertChatService.get_chat_session` 统一编排 (D3),
> `ExpertChatInstanceService` 只管容器实例,不碰 session。

**模式判定 (D1):**
- `user_id == owner_id` → **owner 模式**:沿用现有 `get_chat_session`,复用
  owner 在线容器,**零行为变更**。
- `user_id != owner_id` → **caller 模式**:走新方法,单独拉起。

**`get_caller_connection` 核心流程:**

### 4.1 版本快速检查路径

在启动容器前,先检查实例是否已就绪且版本未过期:
```python
if instance.status == "success":
    ext = instance.ext or {}
    bot_uuid = ext.get("bot_uuid")
    instance_version = ext.get("version") or 0

    # 版本未过期,直接返回 connection
    if bot_uuid and version <= instance_version:
        connection = _build_connection(bot_uuid, bot_id, user_id)
        return {instance, connection, need_poll: False}
```

### 4.2 完整初始化流程

1. **查/建实例**:按 `(bot_id, owner_id, user_id, env)` 查
   `ac_expert_chat_instance`;无则 `upsert_instance(status="init")`。

2. **取构建物**:按 `publish_bot_id, owner_id, env` 取服务 bot
   `publish_status=SUCCESS` 的发布记录
   (`BotPublishRepository.get_by_publish_bot_id(...)`)。`migration_path`
   从 `publish_record.ext["migration_path"]` 直接获取(build 阶段已写入);
   **`migration_path` 属发布单,不属于 caller 实例**。
   无 success 发布单抛 `BotNotPublishedError`。

3. **未拉起过**(实例 ext 无 bot_uuid):调 `_create_container()`:
   - 调用 `baas.create_bot(owner_id=user_id, auto_approve_publish=True, ...)`
     自动审批发布,**省去显式 `approve_publish`**
   - 返回 `{bot_uuid, publish_id}`
   - 保存到 ext: `{"bot_uuid", "service_bot_publish_id", "version", "baas_publish_id"}`

4. **拉起过**(ext 有 bot_uuid):调 `_upgrade_container()`:
   - 调用 `baas.upgrade_bot(bot_uuid, owner_id=user_id, ...)`
   - 保持 **bot_uuid 不变**
   - **异常直接抛出 `ConnectionError`,未实现 BOT_NOT_FOUND 自动回退**
     (原设计要求回退 create_bot,但代码中未实现)

5. **进度轮询**:若有 `baas_publish_id`
   - 调用 `baas.get_publish_progress(publish_id, include_devices=True)`
   - 根据 progress.status 更新 instance.status:
     - `SUCCESS` → status="success"
     - `FAILED` → status="failed"
     - 其他 → 保持原状态
   - 保存完整进度到 `ext.baas_publish`
   - 返回 `{instance, connection, need_poll}`:
     - status="success" → connection 有值,need_poll=False
     - 其他 → connection=None,need_poll=True (调用方需轮询)

6. **Connection 构建**:通过 `_build_connection(bot_uuid)`:
   - 调用 `baas.get_ws_info_by_bot_uuid(bot_uuid, device_affinity=user_id)`
   - 无需本地 binding 记录,直接通过 bot_uuid 拉取 ws 信息
   - 返回 `{ws_url, token, target, paas_device_id, baas_base_url, engine_port, tenant, bot_uuid}`

### 4.3 Request ID 幂等性

为保证 baas 调用幂等,通过 MD5 hash 生成 `request_id`:
```python
request_id = hashlib.md5(
    f"{entity_id}_{bot_id}_{user_id}_{env}_{stage}".encode()
).hexdigest()
```
- 长度 32 字符,符合 baas 要求(32–64 字符)
- 按 `(caller, bot, env, stage)` 确定性生成
- 不同 user_id 的请求不会互相去重

### 4.4 异常处理策略

- `BotNotPublishedError`:无 success 发布单 → 调用方应提前阻止 caller 模式
- `ConnectionError`:baas 调用失败(create_bot/upgrade_bot/get_ws_info) → 需调用方重试
- `BaasServiceError`:baas 写失败直接传播 (遵循 D5:失败显式抛出,不静默吞)

## 5. 设计决策

| ID | 决策 | 理由 |
|---|---|---|
| D1 | `user_id == owner_id` → owner;否则 caller | 复用 `_check_chat_access` 已有比较,零新增配置 |
| D2 | caller 构建物复用 owner success 发布单的在线构建物 | 同一已发布版本,每 caller 一个实例;避免重新 build |
| D3 | 新方法返回 `{instance, connection, need_poll}`,session 仍由 `get_chat_session` 编排 | 单一 session 管理点,caller/owner 流程统一;`need_poll` 标识供调用方判断是否需轮询 |
| D4 | 用 `upgrade_bot`(bot_uuid 不变),异常直接抛出 | 复用 baas 现有方法;**未实现 BOT_NOT_FOUND 自动回退 create_bot**(原设计要求,待实现) |
| D5 | 失败显式抛出,不静默吞 | 遵循 `AGENTS.md`:DB/baas 写失败必须传播 |
| D6 | 新建 `ExpertChatInstanceService`(放 `core/expert_chat/services/`),`ExpertChatService` 注入它编排 | 容器生命周期逻辑独立成型,与对话 session 职责分离;放 expert_chat(调用入口是专家对话、表名带 expert_chat),非 service_bot |
| D7 | `auto_approve_publish=True` 省去显式 approve | 调用方无需感知审批流程,容器 provisioning 一站式完成 |
| D8 | 版本快速检查:status="success" 且版本未过期直接返回 connection | 避免重复轮询,节省 baas 调用;已就绪实例无需等待 |
| D9 | 通过 `get_ws_info_by_bot_uuid` 直接获取 connection | 无需本地 binding 记录,简化依赖;connect-by-uuid 语义清晰 |
| D10 | 进度轮询一次并保存完整快照到 ext.baas_publish | 调用方根据 `need_poll` 决定是否继续轮询;完整进度用于审计 |
| D11 | 状态机 `init/success/failed`(非 `init/active/release`) | 与 baas 工作流状态对齐;`release` 状态未实现,通过版本检查处理复用 |

## 6. 涉及文件 (拟)

**新建:**
- `core/expert_chat/sqlite_models.py` — 新增 `AcExpertChatInstance` ORM
  (紧邻 `AcExpertChatBotSession`)。
- `core/expert_chat/repository/expert_chat_repository.py` — 新增
  `ExpertChatInstanceRepository` protocol(`get_instance` / `upsert_instance`
  / `update_instance_status`),独立 protocol 职责清晰。
- `core/expert_chat/services/expert_chat_instance_service.py` — 新建
  `ExpertChatInstanceService`:constructor 注入
  `ExpertChatInstanceRepository` + `BaasService` + `BotPublishRepository`;
  暴露 `async get_caller_connection(user_id, bot_id, owner_id) -> Dict`,
  承载 §4 四步逻辑。
- `plugins/expert_chat_repository.py`(实现期定位) — 新增 protocol 的
  unified ORM 实现(corp/SQLite 双跑,同 `AcExpertChatBotSession` 范式)。
- `tests/...` — repository + `ExpertChatInstanceService` 4 分支单测,baas mock。

**修改:**
- `core/expert_chat/services/expert_chat_service.py` — constructor 注入
  `ExpertChatInstanceService`;`get_chat_session` 在 caller 模式(D1)改调
  `get_caller_connection(...)` 拿 connection,owner 模式零变更。
- `di/modules/expert_chat_module.py` — 新增
  `binder.bind(ExpertChatInstanceService, scope=singleton)` + instance repo
  的 protocol→impl 绑定(对齐现有 `ExpertChatService`/`ExpertChatRepository` 范式)。

**仅复用、不改动:**
- `core/service_bot/services/baas_service.py` — 仅用现有
  `create_bot`/`upgrade_bot`/`get_bot`/`approve_publish`/`get_ws_info`(ARCA 路径),
  不新增接口。

## 7. 开放问题

**待确认/待实现:**
- **O3**:~~caller 新实例是否需要 `approve_publish`~~:
  **已解决**:使用 `auto_approve_publish=True`,无需显式调用。
- **O4**:是否对 caller 实例数量做上限/回收策略(避免每 caller 常驻容器)?
- **O6 BOT_NOT_FOUND 回退**:原设计要求 `upgrade_bot` 遇 `BOT_NOT_FOUND` 自动回退
  `create_bot`,**当前代码未实现**。`_upgrade_container()` 中异常捕获后直接抛出
  `ConnectionError`,未区分错误类型做回退。需要补充:
  ```python
  # 伪代码
  try:
      result = self._baas.upgrade_bot(...)
  except BaasServiceError as e:
      if e.error_code == "BOT_NOT_FOUND":
          # 回退到 create_bot
          return self._create_container(...)
      raise
  ```
- **O7 状态机完整性**:当前实现用 `init/success/failed`,未实现 `release` 状态。
  若容器被 baas 回收(状态变为 RELEASED),目前通过版本检查判断,但缺少主动感知机制。
  需确认:是否需要健康检查接口主动探测回收状态?
- **DBA**:DDL 的 `UNIQUE KEY ... GLOBAL`(OceanBase 全局索引)是否保留?
  ORM 侧用普通 `UniqueConstraint`。

**已解决:**
- ~~O1 migration_path 来源~~:
  从 `publish_record.ext["migration_path"]` 直接获取,
  build 阶段已写入(`publish_flow_service.py:490`)。
- ~~O2 4.2 回退判定~~:见 O6,待实现。
- ~~O5 表名笔误~~:全文统一 `chat`:
  表 `ac_expert_chat_instance`,ORM `AcExpertChatInstance`,
  repo `ExpertChatInstanceRepository`,服务 `ExpertChatInstanceService`。

## 8. 代码实现说明

**核心文件:**
- `core/expert_chat/services/expert_chat_instance_service.py` — 主服务实现
- `core/expert_chat/repository/expert_chat_instance_repository.py` — Repository 协议
- `plugins/expert_chat_instance_repository.py` — Unified ORM 实现
- `core/expert_chat/sqlite_models.py` — ORM 模型定义

**关键实现点:**

1. **方法签名**:
   ```python
   async def get_caller_connection(
       self,
       user_id: str,
       bot_id: str,
       owner_id: str,
   ) -> Dict[str, Any]:
       # 返回: {"instance": dict, "connection": dict, "need_poll": bool}
   ```

2. **版本快速检查**(`expert_chat_instance_service.py:117-135`):
   - 在执行容器操作前,先检查 `instance.status == "success"` 且版本未过期
   - 满足条件直接返回 connection,跳过所有 baas 调用

3. **容器创建**(`_create_container`, L281-339):
   - 使用 `auto_approve_publish=True` 自动审批
   - owner_id 参数传递的是 `user_id`(调用者)
   - 返回 `{bot_uuid, publish_id}`

4. **容器升级**(`_upgrade_container`, L344-398):
   - 调用 `baas.upgrade_bot` 保持 bot_uuid 不变
   - **注意**:当前实现未做 BOT_NOT_FOUND 自动回退,异常直接抛出

5. **进度轮询**(`get_caller_connection` L191-206):
   - 单次调用 `get_publish_progress(publish_id, include_devices=True)`
   - 根据 status 更新 instance 状态:`SUCCESS`→success, `FAILED`→failed
   - 保存完整进度到 `ext.baas_publish`

6. **Connection 构建**(`_build_connection`, L403-451):
   - 调用 `baas.get_ws_info_by_bot_uuid(bot_uuid, device_affinity=user_id)`
   - 无需本地 binding 记录,直接通过 bot_uuid 拉取

7. **request_id 幂等**(`_request_id`, L454-477):
   - MD5 hash: `entity_id_bot_id_user_id_env_stage`
   - 长度 32 字符,符合 baas 要求(32–64 字符)

**与设计的差异:**
- ✅ 已实现:快速路径、版本检查、progress 轮询、完整 ext 保存
- ❌ 未实现:`release` 状态主动检测、BOT_NOT_FOUND 自动回退 create_bot
- ⚠️ 变更:状态机从 `init/active/release` 改为 `init/success/failed`

## 9. 验收标准

### 8.1 功能验收

**owner 模式:**
- 行为与现状完全一致(回归现有 `get_chat_session` 用例)
- 零行为变更,caller 模式代码路径完全不触发

**caller 首次创建:**
- 建实例(status="init",ext 为空)
- 调用 `baas.create_bot(auto_approve_publish=True)`
- 保存 `bot_uuid`, `baas_publish_id`, `version` 到 ext
- 调用 `get_publish_progress`,状态变为 "success"
- 返回 `{instance, connection, need_poll=False}`
- bot_uuid 与 owner 容器不同

**caller 已有实例 - 快速路径(版本匹配):**
- instance.status="success" 且 `version <= instance.ext.version`
- 直接调用 `get_ws_info_by_bot_uuid` 返回 connection
- **不调用 baas create/upgrade/get_publish_progress**
- 返回 `{instance, connection, need_poll=False}`

**caller 已有实例 - 版本升级:**
- instance.status="success" 但 `version > instance.ext.version`
- 调用 `baas.upgrade_bot(bot_uuid)` (保持 bot_uuid 不变)
- 调用 `get_publish_progress` 查询进度
- 更新 ext.version, ext.baas_publish
- 返回 `{instance, connection, need_poll=False/True}`(取决于 poll 结果)

**caller 实例失败:**
- `get_publish_progress` 返回 status="FAILED"
- instance.status 更新为 "failed"
- 返回 `{instance, connection=None, need_poll=True}`

**caller 无发布单:**
- 找不到 success 发布记录
- 抛出 `BotNotPublishedError`

**baas 调用失败:**
- `create_bot`/`upgrade_bot`/`get_ws_info` 失败
- 抛出 `ConnectionError`(包装原始异常)
- instance 保持原状态

**补:BOT_NOT_FOUND 回退(待实现):**
- `upgrade_bot` 返回 `error_code="BOT_NOT_FOUND"`
- 自动回退调用 `create_bot` 创建新容器
- 更新 ext.bot_uuid 为新的 bot_uuid

### 8.2 数据验收

**ext 字段完整性:**
- 成功创建后 ext 必包含:`bot_uuid`, `service_bot_publish_id`, `version`, `baas_publish_id`
- poll 后 ext 必包含:`baas_publish`(完整快照)

**幂等性:**
- 相同 `(bot_id, owner_id, user_id, env)` 的并发请求应返回相同 bot_uuid
- request_id 按 MD5(entity_id, bot_id, user_id, env, stage) 确定性生成

**版本控制:**
- version 取自 `publish_record.version` 或默认 1
- 版本比较逻辑正确:仅当 `version > instance_version` 时触发升级

### 8.3 异常场景

- 无 success 发布单 → `BotNotPublishedError`
- bot_info 查询失败 → `ConnectionError(error_code="5001")`
- baas create_bot 失败 → 抛出 `BaasServiceError` 或包装为 `ConnectionError`
- baas upgrade_bot 失败 → 抛出 `ConnectionError`
- get_ws_info_by_bot_uuid 失败 → 包装为 `ConnectionError("无法连接到Bot服务")`

### 8.4 单元测试覆盖

- Repository:`get_instance` / `upsert_instance` / `update_instance`
- Service:
  - 首次创建(init→success,包含 create + poll)
  - 快速路径(status=success 且版本匹配,无 baas 调用)
  - 版本升级(status=success 但版本过期,调用 upgrade)
  - 失败路径(create/upgrade 抛异常,包装为 ConnectionError)
  - 无发布单(抛 BotNotPublishedError)
  - request_id 幂等性验证
- Baas mock:`create_bot` / `upgrade_bot` / `get_publish_progress` / `get_ws_info_by_bot_uuid`

## 附:原始需求草稿(归档)

```
## 需求
agentclaw.community.core.expert_chat.services.expert_chat_service.ExpertChatService.get_chat_session
返回arca的链接信息, 调用一个方法bot_id,owner_id, 返回这个bot是caller,还是owner模式,
如果是caller, 要给这个user_id单独拉起一个容器

## 逻辑说明 新建一个方法
1. 根据bot_id,owner_id, user_id查询ac_expert_chat_instance记录, 没有的话创建一个
2. 根据bot_id, owner_id, 获取服务bot发布单是success的发布记录
3. 如果没有发布过, 根据服务bot最近发布单的构建物, 调baas, 创建一个容器, 返回拉起容器发布单
4. 如果发布过, 检查baas的状态,
   4.1 active的 返回链接信息 返回信息参考 ExpertChatService.get_chat_session
   4.2 回收了, 那重新拉起容器, bot_uuid不变  参考 baas_service.py
```