# ProductProfile 服务五表正式存储方案：ac_context_collab_*

## 1. 状态、范围与本次修订

- 状态：**In progress / 已实现存储基础；执行链路切换、迁移和部署尚未完成**。
- 本次开发进度与实际验证见 [delivery.md](delivery.md)；第 4 节运行合同门槛仍未闭合，不以已完成部分代替全方案验收。
- 日期：2026-11-27；代码基线：`d10cb044b603068a137619ff923bcce5b7ae8a63`。
- Owner：Backend `context_collab`。L6 持有产品配置和仓储适配，L5 持有执行与 MultiRollout 决策，L4 继续持有独立 Task 和原接受事件合同。
- 本文依据用户的五项业务表要求、“以 `ac_context_collab_*` 作为正式存储”、随后明确的“去掉五张配套表”，以及 Profile 审计/生命周期状态、Collet 字段简化和 StageRollout 七项字段移除及验收/错误列类型调整、Event 复用既有 event_id 而不新增 interactive_key 的要求。未读取到的外部文档不作为已核实依据。
- 前期仅更新方案和导航；用户本轮已授权开发与测试。现已增加 Python 存储基础和 SQL 文件，但未切换正式 DI/执行链路，未执行目标库 DDL、数据删除、迁移或部署。下文保留方案阶段的设计依据及尚待实施清单，实际状态以 delivery.md 为准。

### 1.1 最终范围：只建设五张业务表

| 正式表 | 计划操作 | 核心内容 |
| --- | --- | --- |
| `ac_context_collab_product_profile` | 新目标结构 | name、description、完整 config YAML、status（默认 DRAFT）、creator、modifer |
| `ac_context_collab_collet` | 扩展 | 新增 content、status；保留 product_profile_id、current_rollout_id 关联，不新增三项已排除字段 |
| `ac_context_collab_collet_rollout` | 新增 | collet_id、content、status、运行顺序和前驱 |
| `ac_context_collab_stage_rollout` | 新增 | 阶段执行者、input、session_id、acceptances（VARCHAR(8192)）、error_msg（TEXT）、产物；不建 attempt 等七项已排除字段 |
| `ac_context_collab_event` | 扩展 | 只新增 collet_rollout_id、collet_stage_rollout_id；复用既有 event_id 记录反馈与运行审计、支持反馈去重 |

**不建设以下五张表，也不换名另建同类配套表：**

- `ac_context_collab_runtime_control`
- `ac_context_collab_runtime_identity`
- `ac_context_collab_operation_receipt`
- `ac_context_collab_runtime_intent`
- `ac_context_collab_candidate_slot`

既有 `ac_context_collab_task` 仍服务独立 L4 Task，不是本次新增对象，也不为产品执行生成影子 Task。旧版 Profile 归档结构只用于兼容历史，不属于新服务的运行表组。

### 1.2 不变目标与简化边界

1. 新服务的正式读写只依赖上述五表，**不依赖 `ac_product_*`，不做长期双写，不在 GET 中回填，不在缺数据时退回旧快照**。
2. Collet、rollout、stage 的业务字段是权威数据；不持久化另一份完整 `snapshot_json`。现有 `RuntimeSnapshot` 中可由业务字段证明的部分可以重建，缺失的控制事实不伪造，完整运行合同的适配缺口见第 4.1 节。
3. 默认创建不接受 composition，内部生成 `MultiRollout@1.0 NEST Swarm@0.2`；Swarm 使用静态 stages YAML workflow。可信执行/验收失败后等待 human，再由 adaptation Agent 提议 RE_DO、OPTIMIZE、DO_NOTHING 或 SHUTDOWN。
4. Collet 不新增 runtime_collet_id、revision、execution_state，统一使用 Collet.id；业务写入并发方案改为短事务行锁。当前 rollout、session_id 仍有明确业务字段；attempt 不再存储，完整 SessionRoute、投递/租约/取消/复用及停止/策略运行事实未覆盖部分列为实施前待解决项，不把被移除字段换名藏入 metadata、creation_facts、current_snapshot 或 Event payload。
5. 不再建设通用请求回执、持久化 Outbox 和租户级候选名额设施。具体能力差异与恢复限制在第 4 节写明，不能只删 DDL 却声称旧保障全部不变。

### 1.3 修订记录与当前合同

- 第一版曾推荐继续使用旧 snapshot 权威、五表作为投影；因不符合正式存储统一目标，已放弃。
- 第二版改成五业务表加五技术表，试图原样保留旧可靠性设施；**用户已明确不需要五张技术表，本版将其从建设范围、目标 DDL 和代码依赖中移除**。
- 后续字段修订：按用户要求，从目标 Profile 移除 `config_digest`、`owner_json`，改为 `creator`、`modifer` 审计字段；`modifer` 按指定拼写保留，不自行改为 `modifier`。原有执行摘要和 Collet owner 不是本次删除对象，Profile 使用授权需配套调整。
- 本次 Collet 修订：新增字段中去掉 `runtime_collet_id`、`revision`、`execution_state` 及其专属索引，明确新增 `content`、`status`；保留未要求移除的 product_profile_id/current_rollout_id 关联。先前“公开 ID 别名 + CAS + 执行状态 JSON”的方案不再采用；历史重号、异步防陈旧和完整重启恢复不再沿用旧版已覆盖的表述。
- 本次 Profile 状态修订：新增独立的 status 字段，初始支持 DRAFT、ONLINE、OFFLINE，默认 DRAFT（以本次用户确认替代先前的 ONLINE 默认约定）；它是配置生命周期状态，不复用 Collet/rollout/stage 的执行状态，不进入执行 YAML 或摘要。
- 前次 StageRollout 修订：去掉 ordinal、lease_until、dispatch_state、reused_from_stage_rollout_id、session_route_json、cancel_state 六列，同时去掉阶段顺序/复用来源索引及复用来源外键；acceptances 改为 VARCHAR(8192)，error_msg 改为 TEXT。仅移除 Stage 的 ordinal，rollout.ordinal 继续保留；前版完整路由/投递/租约/取消/复用恢复的字段映射不再采用。
- 本轮追加移除 StageRollout 的 attempt，累计排除七项阶段辅助字段；现有执行派发、进度上报和 provider 请求关联依赖须配套演进，不增加同义字段或伪造历史 attempt。
- 本轮 Event 修订：不新增 interactive_key 及其专用唯一索引，复用既有 event_id 与 uk_context_collab_event；Event 仅新增两个关联列。既有索引包含 collab_context_id，不将其误写为跨 context 的全租户唯一约束，原反馈 UUID 合同的跨 context 仲裁列为实施前门槛。
- 本轮执行链路澄清：自动新增/更新五表是接口所触发的完整执行链路的责任，不要求写表逻辑都放进 ProductProfileService 方法体或同一次 HTTP 请求；各生命周期节点的触发方、持久化内容及事务边界见第 6.2 节。
- 本轮无物理外键修订：遵循“研发规范禁止外键”，移除两张 rollout 新表的三个物理外键，并同步移除 Collet/Event ALTER 的四个物理外键，避免后续升级重复报错。关联 ID 列、普通/唯一索引均保留，由应用事务和完整性校验维护业务关系；数据库不再提供 RESTRICT 保护。
- 本版是“五表 + 业务字段”的方案；并非现在删除实际数据库或已经实现新的运行合同。

实施前，以下文档/源仍描述当前实现。后续采纳时须同步更新合同、消费者、测试及适当的 Backend ADR，不以本文覆盖当前代码事实：

- [ProductProfileService 合同与恢复边界](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md)。
- [L5 MultiRollout 合同](../../src/agentclaw/community/core/task/context_collab/l5_methods/MULTI-ROLLOUT.md)。
- [旧版 Profile 发布存储](../../src/agentclaw/community/core/task/context_collab/l6_products/PROFILE-STORAGE.md)。
- [当前运行仓储](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/rollout_repository.py)、[内存运行字段](../../src/agentclaw/community/core/task/context_collab/l5_methods/rollout_contracts.py)。
- [架构宪法](../../../../docs/arch/arch.rules.md)、[CI 约束](../../../../docs/arch/ci.enforce.md)、[边界格式](../../../../docs/arch/context-boundary-format.md)、[Protocol 测试](../../../../docs/arch/protocol-contract-tests.md)。

## 2. 关系、身份和辅助字段

### 2.1 正式关系

```text
product_profile.id <----- collet.product_profile_id
                              |
                         collet.id（物理主键）
                              |
                     collet_rollout.collet_id
                              |
                    collet_rollout.id（公开 rollout ID）
                              |
                    stage_rollout.collet_rollout_id
                              |
                    stage_rollout.id（公开 stage ID）

collet.(id, current_rollout_id) -------------> rollout.(collet_id, id)
rollout.(collet_id, predecessor_id) ---------> rollout.(collet_id, id)
event.collet_rollout_id --------------------> rollout.id
event.(collet_rollout_id, collet_stage_rollout_id) --> stage.(collet_rollout_id, id)
```

省略共同前缀。图中箭头只表示逻辑关联，五表不定义物理外键。全部读写验证可信 tenant/env、调用者权限和完整父子链；Profile 使用授权与 Collet 运行 owner 授权分开，见第 5.4 节。关联 ID 和索引均**不等于租户授权**。旧表的大小写不敏感排序规则还要求加载后精确核验 axes。

### 2.2 ID 分配：Collet.id 即服务 ID，各业务表自增

- Profile、Collet、rollout、stage 都由自己的业务表 `AUTO_INCREMENT` 分配真实主键；新建子记录后 flush 取得 ID，不插入专门取号的假业务记录。
- 不再要求不同类型的 ID 全局互斥。定位必须携带确定类型，例如 stage→stage 表、rollout→rollout 表，不能只凭整数猜对象类型。
- **Collet.id 同时作为数据库主键、API Collet ID 和所有 rollout.collet_id 的引用值**，不再设置 runtime_collet_id、公开别名列或运行时 ID 映射表。现有 Collet 物理主键为 `BIGINT UNSIGNED`；产品路径需校验服务/宿主支持的整数范围，不静默截断。
- 迁移只在原 v2 Collet ID 未被目标其他 Collet 占用、且 context/axes/身份均一致时保留原 ID。与既有 L4 行重号时停止该记录自动迁移，不认领旧行，也不暗中改 ID：MethodBinding、`multi:{id}`、candidate namespace、plan digest 和远端 run 可能引用原 ID，另行评审迁移方式后处理。
- rollout/stage/Profile 可在不存在目标身份冲突时原样导入旧 ID；各表推进自增高水位至实际行、导入 ID 和源已消耗号段之后。离线迁移核对记录仅供 operator 使用，不成为正式 reader 的别名查找依赖。

### 2.3 本版保留的关联与业务辅助字段

| 归属 | 新增/保留字段 | 原因 |
| --- | --- | --- |
| Collet | content、status | 本次明确新增的业务内容和状态 |
| Collet | product_profile_id、current_rollout_id | 配置关联与当前 rollout 指针；本次未要求移除 |
| Event | event_id（既有，不新增） | 反馈受理事件使用确定性 ID，复用既有唯一索引做同 context 去重；原合同跨 context 的 UUID 冲突仍须明确仲裁 |

不新增产品 revision，不复用原 L4 context_version 或时间戳充当乐观锁版本；不新增 execution_state，也不复用 legacy current_snapshot 保存产品控制快照。产品运行 owner 仍使用 Collet.actor_ref 中的可信创建 PrincipalRef，独立于 Profile.creator/modifer；不凭配置创建人或修改人推测运行 owner。

## 3. 总体 DDL（后续实施稿）

### 3.1 前提与安装路径

- 以下为 **MySQL/InnoDB 方言方案稿**，没有在目标 MySQL/OceanBase 执行，不宣称生产兼容性已验证。
- 假设原 L4 Collet/Event/Task 及所需 metadata/resources 列已安装；对已有表提供 ALTER，不重复复制历史基线结构。
- 全新安装只加载 L4 基线和本版五表变更，**不创建旧六张运行表，也不创建已排除的五张配套表**。已有环境执行受评审升级脚本，历史 SQL 不倒改。
- 执行前核验实际 schema、signed/unsigned、字符集/排序规则、索引长度、禁用物理外键规范/分区支持、隔离级别和同名结构；停写、备份并记录每步。DDL 不视为可整体事务回滚。
- 不使用 `CREATE IF NOT EXISTS` 掩盖不兼容结构，不依赖版本敏感的 JSON CHECK/ENUM。闭合类型/状态由受测代码校验。
- OceanBase GLOBAL 索引/分区变体和目标库锁行为须另行验证；本版 CREATE/ALTER 均不定义物理外键，不通过关闭规范检查或忽略 DDL 错误上线。已有部分执行成功的迁移须先检查实际列/索引再续执行，不能直接重跑全部 ADD COLUMN。

### 3.2 同名旧 Profile 的避让

当前[旧版 ORM](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/profile_models.py)已经使用目标名称，但保存字符串 profile_id/profile_version/definition_json 等旧结构。**仅在该旧结构确实存在时**停写并执行一次：

```sql
RENAME TABLE ac_context_collab_product_profile
          TO ac_context_collab_product_profile_legacy_v1;
```

同步切换旧 reader/publication ORM，保留版本、canonical JSON、摘要和审计。全新环境跳过；目标结构或归档名已占用时先核对迁移记录，禁止覆盖/清空。

### 3.3 ProductProfile

```sql
CREATE TABLE ac_context_collab_product_profile (
    id BIGINT NOT NULL AUTO_INCREMENT,
    avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL,
    name VARCHAR(256) NOT NULL,
    description VARCHAR(2048) NOT NULL DEFAULT '',
    config TEXT NOT NULL COMMENT '版本化完整 YAML，唯一执行配置来源',
    status VARCHAR(32) COLLATE utf8mb4_bin NOT NULL DEFAULT 'DRAFT' COMMENT 'Profile 状态：DRAFT/ONLINE/OFFLINE',
    creator VARCHAR(1024) COLLATE utf8mb4_bin NULL COMMENT '创建人稳定标识；历史来源未知可空',
    modifer VARCHAR(1024) COLLATE utf8mb4_bin NULL COMMENT '最后修改人稳定标识；按约定拼写，历史未知可空',
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_cc_profile_scope_name (avernet_tenant, env, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

`config` 使用 `TEXT`（不是 MEDIUMTEXT），非唯一索引名为 `idx_cc_profile_scope_name`，索引列与顺序仍为 `(avernet_tenant, env, name)`。已有上一版五表结构不能靠更新建表 SQL 自动升级：停写并核验所有 `OCTET_LENGTH(config) <= 65535` 后，再将列改为 TEXT 并重命名旧索引；超限记录停止升级并人工评审，不截断配置。

name 不唯一；同名 create 仍是独立调用。name/description/执行 config 及创建归属不可原地修改，定义变更新建 ID；status 是独立生命周期元数据，不受执行配置不可变限制。本版不新增 Profile 更新接口，仍保留现有五个服务方法，状态管理入口另行评审。

Profile 状态约定：

| status | 含义 | 新建 Collet |
| --- | --- | --- |
| `DRAFT` | 草稿，尚未上线；默认值 | 不允许 |
| `ONLINE` | 已上线，可供授权使用 | 通过使用授权及运行前置校验后允许 |
| `OFFLINE` | 已下线，不再接受新建使用 | 不允许 |

- status 为非空、大小写精确的闭合字符串，代码定义独立的 `ProductProfileStatus` 类型，仅接受上述三个值；不复用执行 `StatusEnum`，新增取值须显式演进合同。未知值、空串、null 拒绝，不在读取时默认为 DRAFT 或 ONLINE 掩盖坏数据。
- 当前 create 方法不增加 status 参数或 HTTP 请求字段，由服务显式写入 DRAFT；数据库默认值与 ORM/领域默认值均为 DRAFT，响应返回实际存储值。status 只存该列，不写入 config YAML。创建 Profile 不等于上线；经授权的状态管理入口完成 DRAFT→ONLINE 前，不能创建新 Collet，服务不自动上线。
- 受控状态变更须核验可信 tenant/env 和独立配置管理/发布授权，在同一事务更新 status、modifer、gmt_modified，creator 保持不变；不允许借状态切换原地改写执行配置。本版只规定配套修改方向，不新增上下线 API，也不实施状态变更。
- 仅 ONLINE 可以创建新 Collet，且 ONLINE 本身不授予任何权限；该检查须在 Collet 创建事务中锁定并重读 Profile，与状态变更写入采用同一 Profile 行锁协调，避免先检查后下线的竞态。此创建路径按 Profile→Collet→rollout→stage 顺序加锁；已有 Collet 的业务写不反向获取 Profile 锁。
- DRAFT/OFFLINE 不删除既有 Profile/Collet，不自动停止或取消已经创建的运行，也不阻止其按原授权查询、反馈和既有运行内的后继 rollout；既有运行读取其配置时仍按原 ID 加载，不把 ONLINE 作为仓储通用读取过滤条件。原有停止/预算/恢复合同适配门槛不变。

审计字段约定：

- `creator` 表示创建人，`modifer` 表示最后修改人，均为普通字符串，不存 PrincipalRef JSON、权限快照或配置摘要。按用户指定保留 `modifer` 拼写，ORM/领域元数据/响应字段统一使用该名称。
- 新建时由服务从可信 `ActorContext.actor` 生成 `principal_type:principal_id` 稳定标识，同时写入两列；不使用展示名，也不接受请求体传入或覆盖。编码校验类型段不含冒号，按第一个冒号分隔，原 principal_id 保留；整体不得超过 1024 字符，超限明确拒绝，不截断。
- 两列 nullable 仅用于诚实表达历史来源未知；新创建路径必须非空。`creator` 写入后不覆盖，`modifer` 只代表实际写入者；新建时两者相同，后续经授权的状态变更须同步更新 modifer/gmt_modified，不能覆盖 creator。状态管理入口另行评审，仍不能原地改执行配置。
- 迁移仅在来源能证明时转换历史创建人/最后修改人，无法证明的一列保留 NULL；不以迁移操作人、当前调用者或 creator 猜填未知 modifer。
- 两列仅为审计归属，不自动授予配置使用或运行控制权限；Profile 使用授权见第 5.4 节，运行 owner 仍来自 Collet.actor_ref。

### 3.4 扩展 Collet

```sql
ALTER TABLE ac_context_collab_collet
    ADD COLUMN content MEDIUMTEXT NULL,
    ADD COLUMN status VARCHAR(32) COLLATE utf8mb4_bin NULL,
    ADD COLUMN product_profile_id BIGINT NULL,
    ADD COLUMN current_rollout_id BIGINT NULL,
    ADD KEY ix_cc_collet_profile_status (avernet_tenant, env, product_profile_id, status, id);
```

Collet 本版共新增四列：content、status、product_profile_id、current_rollout_id，其中前两列为本次明确的业务字段，后两列保留配置/当前 rollout 关联；不新增 runtime_collet_id、revision、execution_state 及其索引。普通 L4 行新增列保持 NULL，不把未知历史统一填成 CREATE。产品行在提交前要求这四列及 actor_ref 完整；创建中的 current_rollout_id 可临时为 NULL，但提交时必须指向已插入的本 Collet rollout。

### 3.5 ColletRollout

```sql
CREATE TABLE ac_context_collab_collet_rollout (
    id BIGINT NOT NULL AUTO_INCREMENT,
    avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL,
    collet_id BIGINT UNSIGNED NOT NULL COMMENT '物理 Collet 主键',
    ordinal INT NOT NULL COMMENT '本 Collet 内稳定顺序，从 1 开始',
    predecessor_id BIGINT NULL,
    content MEDIUMTEXT NOT NULL,
    status VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_cc_rollout_order (collet_id, ordinal),
    UNIQUE KEY uk_cc_rollout_collet_id (collet_id, id),
    KEY ix_cc_rollout_predecessor (collet_id, predecessor_id),
    KEY ix_cc_rollout_scope_collet (avernet_tenant, env, collet_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

ALTER TABLE ac_context_collab_collet
    ADD KEY ix_cc_collet_current (id, current_rollout_id);
```

current/前驱属于同一 Collet 的约束改由应用维护：新建在单事务中插入真实父子记录、再填写 current；读取校验 current 属于当前 Collet、ordinal 连续且前驱恰为本 Collet 的上一条 rollout。前驱无值写 NULL，自环、跨 Collet 或不存在的关联明确拒绝。API rollout.collet_id 直接使用其关联的 Collet.id，不再做公开/物理 ID 转换。普通/唯一索引保留，但不负责验证父记录是否存在。

### 3.6 StageRollout

```sql
CREATE TABLE ac_context_collab_stage_rollout (
    id BIGINT NOT NULL AUTO_INCREMENT,
    avernet_tenant VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    env VARCHAR(20) COLLATE utf8mb4_bin NOT NULL,
    collet_rollout_id BIGINT NOT NULL,
    name VARCHAR(256) NOT NULL,
    status VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
    assignee_type VARCHAR(64) COLLATE utf8mb4_bin NOT NULL DEFAULT '',
    assignee_uuid VARCHAR(256) COLLATE utf8mb4_bin NOT NULL DEFAULT '',
    `input` MEDIUMTEXT NOT NULL,
    session_id VARCHAR(512) COLLATE utf8mb4_bin NOT NULL DEFAULT '',
    acceptances VARCHAR(8192) NOT NULL COMMENT '严格 JSON 数组字符串，序列化后最多 8192 字符，空集合写 []',
    error_msg TEXT NOT NULL,
    artifacts MEDIUMTEXT NOT NULL COMMENT '严格 JSON 数组，空集合写 []',
    gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_cc_stage_name (collet_rollout_id, name),
    UNIQUE KEY uk_cc_stage_parent_id (collet_rollout_id, id),
    KEY ix_cc_stage_scope_rollout (avernet_tenant, env, collet_rollout_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

- Collet/rollout/stage 的 status 沿用 [StatusEnum](../../src/agentclaw/community/core/task/context_collab/l4_context_primitives/product_status.py)：`CREATE`、`RUNNING`、`RUN_FINISHED`、`RUN_FAILED`、`ACCEPTANCE_SUCCEED`、`ACCEPTANCE_FAILED`。`WAITING_FEEDBACK/SUCCEEDED/FAILED/STOPPED` 不是本版业务 status，不把它们直接混入此枚举以代替已移除的控制存储；相关运行事实的适配缺口见第 4.1 节。
- 本表不包含 ordinal、lease_until、dispatch_state、reused_from_stage_rollout_id、session_route_json、cancel_state、attempt；移除 uk_cc_stage_order、ix_cc_stage_reused_from、fk_cc_stage_reused_from。不新增 attempt 的同义列或 JSON 落点；保留阶段名称唯一键和 `(collet_rollout_id, id)` 唯一键，后者作为父子关联索引保留，不创建 Event 复合外键。
- 未分配的 assignee/session/error_msg 可为空字符串；input、error_msg、acceptances、artifacts 均由应用显式填写。acceptances/artifacts 仍使用闭合 JSON 数组字符串，空集合写 `'[]'`，禁止 Python repr、重复键、非有限数和未知字段，不因 SQL 类型调整改变领域的 `list[AcceptanceResult]`/`list[Artifact]` 语义。
- acceptances 使用 `VARCHAR(8192)`：以最终规范 JSON 序列化结果计数，包含括号、引号和转义字符，最多 8192 字符；写入及迁移前校验，边界超限明确失败，不截断 JSON、不丢验收项，不默默改回大文本列。
- error_msg 使用 `TEXT`：按实际 UTF-8 编码字节数校验；MySQL 方言稿按最多 65535 字节设计，目标数据库需验证容量/字符集行为。超限明确失败并回滚，不按 65535 个中文字符判断容量，也不静默截断。artifacts 仍为 MEDIUMTEXT，未在本次调整范围。
- 阶段展示/内存列表顺序按该 Collet 关联的确切 Profile 原 stages YAML 节点顺序，以唯一 name 对应业务行；不按 SQL 未指定顺序、stage.id 或字母顺序猜测。执行先后仍依据 DAG 依赖，不把展示顺序当执行许可。缺配置、未知/重复阶段名或无法对应时明确拒绝，不另增阶段顺序列；rollout.ordinal 不受本次影响。
- session_id 仅保存真实会话标识，不等于完整 SessionRoute 或访问凭据。移除路由 JSON 后，provider/bot_id/owner_id/lifecycle_stage/run_id 的可信定位来源需配套评审，不能从 assignee_uuid、当前 Bot 配置或 stage ID 拼造；缺少完整路由/权限依据时不读取 provider 消息、不发起取消，不返回伪造空消息作为成功。
- 不再承诺从本表恢复投递 phase、有效租约、复用来源或取消状态，不将这些事实塞进 input、error_msg、artifacts、验收数组或 Event 控制快照。默认 MultiRollout 后继仍完整重跑，不复用旧 session；不能仅凭相同验收/产物自动认定可复用。相关运行与迁移缺口见第 4.1 节。
- session ID 建议上限 512 UTF-8 字节；历史值和 provider 合同不满足则停止自动迁移并单独评审，禁止静默截断。

### 3.7 扩展 Event

需求字段严格保留 `collet_stage_rollout_id` 的拼写。Event **只新增下面两个关联字段**，不新增 interactive_key 或其专用唯一索引；反馈去重改为复用既有 event_id，不建设通用操作回执。

```sql
ALTER TABLE ac_context_collab_event
    ADD COLUMN collet_rollout_id BIGINT NULL,
    ADD COLUMN collet_stage_rollout_id BIGINT NULL,
    ADD KEY ix_cc_event_rollout (avernet_tenant, env, collet_rollout_id, id),
    ADD KEY ix_cc_event_stage (avernet_tenant, env, collet_stage_rollout_id, id),
    ADD KEY ix_cc_event_stage_parent (collet_rollout_id, collet_stage_rollout_id);
```

Event 的两个字段仅为逻辑关联，不定义物理外键。后续 stage/反馈 writer 必须在同一业务事务中验证 rollout 存在、stage 属于指定 rollout，拒绝“stage 非空而 rollout 为空”，并精确校验 context/axes/actor；这些 writer 尚未实现，不声称移除外键后已有等价 Event 阶段保护。当前 creation Event writer 校验 Collet/current/rollout 父子链，stage 关联为 NULL。旧 L4 事件两个新增关联列均为 NULL，原 event_id 保持不变。数据库不再提供 ON DELETE/UPDATE RESTRICT 或级联行为；本版未开放删除 API，离线维护必须自行检查引用并保留审计历史。

既有 `event_id` 是 VARCHAR(128) NOT NULL，现有唯一索引是 `uk_context_collab_event (avernet_tenant, env, collab_context_id, event_id)`。本版不重复 ADD event_id、不修改该索引，也不增加全表 `(avernet_tenant, env, event_id)` 唯一约束以收紧原 L4 的跨 context 合法重号语义。只有按第 4.3 节确定性赋值时才能用于请求去重，随机事件 ID 本身不能让相同反馈自动重放；唯一范围差异与迁移要求须随消费者一起验收。

### 3.8 已安装旧外键结构的升级边界

本次修改 bootstrap SQL/ORM 不会自动删除实际数据库的旧约束。若已有前版五表结构，需停写、备份并通过实际 schema 确认以下旧外键是否存在，再由评审过的升级操作仅移除已存在约束：

- Collet：`fk_cc_collet_profile`、`fk_cc_collet_current`。
- ColletRollout：`fk_cc_rollout_collet`、`fk_cc_rollout_predecessor`。
- StageRollout：`fk_cc_stage_rollout`。
- Event：`fk_cc_event_rollout`、`fk_cc_event_stage_parent`。

关联字段及本方案索引不得同时删除；本次不修改既有 L4 或旧 snapshot v2 的历史迁移。直写 SQL/绕过仓储的维护操作不再受数据库引用检查保护，必须校验父子链、scope 和审计保留。尚未开放的后继 rollout、stage Event 或删除操作须先实现对应事务/校验，不把索引视为外键替代品。

## 4. 去掉五张配套表后的实现方案与能力边界

### 4.1 去掉控制字段后的边界：不声称完整运行态已经有落点

本版按要求移除三项 Collet 字段和七项 Stage 字段，不另增控制表、同义列或控制状态大 JSON。五表仍保存业务记录、阶段 session_id 和已提交反馈审计，但不再具备原 attempt/完整路由/投递/租约/复用/取消列；**仅凭这些字段不能证明已覆盖当前 L5 的完整运行/重启恢复合同**。默认 human-gated MultiRollout NEST Swarm 仍是服务行为目标，不因删列改成失败后自动重跑。

| 当前内存/源事实 | 本版落点或实施前缺口 |
| --- | --- |
| Collet ID / 当前 rollout | Collet.id / current_rollout_id，不再需要公开 ID 别名 |
| revision / 外部 expected_revision | 无目标版本列；短事务行锁只解决数据库写串行化，不等同于旧 CAS/异步陈旧结果校验。HTTP 响应、ReportContext 和宿主进度入口须明确合同演进，不能返回固定 0、拿 context_version/时间戳充数或直接忽略旧参数 |
| stopped、bindings、relations | 没有本版认可的完整持久化落点；不可把 stopped 默认为 false，也不能从今天的默认配置补造历史 MethodBinding/MethodCompositionEdge。真实停止事件可作审计，但本版未定义从 Event 自动重建运行控制态的 reducer |
| run_id、controller_binding、body_binding、nest_edge、composition_digest、policy、descriptor | 不再承诺写入执行状态 JSON；Profile 可提供确切 composition/policy，但运行绑定、实际 run 与策略 descriptor 不能仅凭配置证明，需先补充适配合同 |
| created_at、deadline、generation、phase、outcome、reason、winner、waiting_until | rollout 次序/前驱和业务 status 不能完整替代这些运行事实；原绝对时间、胜出/等待和结束依据不能在重启时重置或猜测 |
| dispatches、strategy_calls、no_progress、failure_digest、peak_concurrency | 单 Collet 预算/证据的跨重启保存未在本版落实，不能声称去掉字段后仍可无损恢复 |
| candidates、plans、decisions、operation、strategy_attempts、strategy_elapsed_ms、notifications | rollout/已接受反馈可保存其业务结果，但不等于完整策略状态、在途 Agent operation fence 或通知已送达事实；缺失时禁止自动恢复受影响操作 |
| tenant_quota、candidate.slot | 仍不建设/执行租户级名额语义，不能因此声称真实远端运行已结束 |
| StageRecord.stage_id | stage.id 仍为阶段业务身份，不等于独立执行尝试凭据或 provider 幂等键 |
| StageRecord.attempt、ReportContext.attempt、RolloutExecutionPort.start 的 attempt | 本版不持久化 attempt；计划移除其读写、生成及上报校验依赖，并同步进度请求/签名调用方与执行适配器。不能用空串、stage.id、session_id 或派生 UUID 伪造原尝试标识，不将其藏入业务列、Event 或新表；旧协议兼容、防重复派发与陈旧回调处理须先评审 |
| StageRecord.lease_until、dispatch_state、reused_from、route | lease_until/dispatch_state 无目标列，reused_from 不再映射到复用来源外键，route 不再映射到路由 JSON；不能用 0、pending、None 等默认值冒充未知历史 |
| SessionRoute.session_id、provider、bot_id、owner_id、lifecycle_stage、run_id | 仅 session_id 保留；其余五项暂无已评审的可信重建来源，不把 assignee、Collet owner 或当前配置当原始执行路由 |
| cancellations / cancel_state | 不再有 Stage 取消状态列；真实取消确认/未知副作用的处理合同待适配，不由错误消息、业务 status 或 session_id 推断已取消 |

对源嵌套类型也逐项登记缺口，而不是删去校验后直接兼容：candidate 的 rollout_id/generation/ordinal/predecessor/binding/edge/plan_digest/namespace/started/incomplete，RunPlan 的 version/digest/profile_digest/previous/patches/rerun/evidence_digest，StagePatch 的 stage/instruction/candidates，以及 StrategyOperation 的 key/payload_digest/fence/lease_until/generation/kind，均不再宣称具有原执行状态 JSON 落点。真实存在的执行/证据摘要仍严格核验；未持久化的原事实不重新计算冒充历史。

**实施门槛：** 先评审上述未覆盖控制事实、完整会话定位/读取授权、无 attempt 的执行关联、投递/租约/取消/复用、异步操作防陈旧、进度协议兼容及故障恢复的处理范围，再授权受影响的运行切换。本次不替用户默认选事件溯源、新表或同义字段，也不把进程内缓存当跨进程持久化。事实不完整时拒绝自动续跑/新调度；业务历史可独立对账，不表示当前服务已能完整接管运行。

### 4.2 不建 identity 表：业务行直接定位

get_collet 直接使用 Collet.id，并核验 product_profile_id 非空及可信 axes/actor，防止把普通 L4 行当产品运行。locate(rollout) 查 rollout→Collet；locate(stage) 查 stage→rollout→Collet。L5 创建后继需要的 ID 改由实际业务行 flush 后返回，配套调整 append_rollout/append_candidate 的初始化接口，不再调用独立 `_identity` 分配器。

### 4.3 不建通用 receipt 表：只保留反馈事件的业务去重

公开 create 本就是独立调用，去掉内部为这些调用生成 UUID/保存通用回执的路径。**不承诺任意 create 请求的持久化结果重放**。

当前公开 interactive 合同则要求同 actor/scoped UUID 的相同输入重放原结果，改变 rollout/content 要冲突。该业务合同可由“已受理反馈事件”承担，而不是另建 receipt：

1. 沿用现有 canonical actor SHA-256 算法生成 actor_key；对带版本的闭合 canonical JSON `{schema: "product-feedback-id@1", avernet_tenant, env, actor_key, interactive_uuid}` 计算 SHA-256 小写十六进制摘要，直接形成既有列的值 `event_id = "product-feedback-v1:" + digest`（84 个 ASCII 字符，小于 128 字符上限）。UUID 是原样校验的 opaque 字符串，不做大小写折叠或截断。ID 不含 Collet/context/rollout/content/decision，避免同 UUID 换目标或正文时绕过冲突校验；不另存反馈专用键。
2. 该确定性 event_id 仅用于成功提交的已受理反馈事件；普通审计用独立命名空间的事件 ID，不从每次提交随机 UUID 生成反馈受理 ID。已受理事件使用固定业务类型，如 `product_runtime.feedback.accepted`，payload 保存 schema、actor_key、原 interactive_uuid、对应 rollout、请求摘要、反馈事实、最终 decision（type/message）。private 保护反馈正文；这些是业务受理事实，不新增通用回执列或控制快照。
3. 查询/重放必须先做当前权限与父子链核验；按可信 axes、确定性 event_id 和真实 Collet.collab_context_id 定位，并严格核验 event_kind、actor/UUID 指纹、父级、operation/rollout/content 摘要。同一请求返回事件中确切 decision，不重新调用 Agent；同 ID 内容/归属不同、多个匹配或身份指纹不符则冲突，不误当合法重放。event_id 不是访问凭据。
4. 反馈 Event 与决策状态、后继 rollout/stage 在同一事务提交。**同 context** 的并发由既有 uk_context_collab_event 仲裁，不能仅“先查后写”；唯一冲突后先回滚本次业务变化，再重读并核验同一已提交业务事件，不重复后继，也不吞其他完整性错误。
5. **唯一范围缺口：** 现有反馈合同的键是 tenant/env/actor/UUID，源 receipt 唯一索引不包含 Collet/context；现有 Event 索引却包含 collab_context_id。同一个确定性 event_id 可以合法出现在不同 context，单 Collet 行锁或一次跨 context 预查询不能阻止并发双写。本版不悄悄把合同缩为每 Collet 独立 UUID，也不默认新建索引/表、虚构统一审计 context 或改变事件真实归属。跨 context 的 UUID 冲突检查和并发仲裁必须另行评审并通过消费者测试，再开放受影响反馈写入；在此之前不能声称完整替代原全 scope 幂等保障。
6. 不再声称第 4.1 节已有可持久化的 operation fence。Agent 事务外调用后必须重新加锁并核验原目标及完整决策依据；本版无持久化 attempt，不能沿用旧尝试标识核验假设；该核验的跨进程方案未评审前，不开放新的自动后继提交。既有唯一键仅防止同 context 重复提交同一已接受反馈，不保证 Agent 仅调用一次，也不覆盖不同 UUID 的策略竞争。

Event 不保存通用 create/replay 结果，不增加通用 operation-receipt API。签名进度继续严格核验 actor/evidence；原 attempt 及 expected_revision 的协议依赖按第 4.1 节显式评审和切换，不静默丢弃检查。移除 attempt 不等于忽略旧请求中的该字段，需同步请求 schema、签名调用方和消费者测试，未评审防陈旧合同前不开放受影响进度写入。不能借移除 receipt/版本列放松签名或把未知请求响应当成功。

### 4.4 不建 Outbox：受字段简化约束的业务状态扫描

后续可由显式宿主调度扫描业务表，但不引入通用 intent 队列或替代七项 Stage 字段的状态包。**不能仅看到 CREATE、RUN_FAILED 或已有 session_id 就决定启动/重跑、接受进度或发起取消**；先明确第 4.1 节所需真实事实的可信来源和合同，无法证明时不自动派发。

| 现有业务事实 | 允许的处理边界 |
| --- | --- |
| CREATE stage / 依赖看似已满足 | 没有持久化投递 phase/lease，不能沿用旧“登记 attempt/lease/dispatch_state 后启动”流程；重新设计并评审防重复投递/恢复合同前，不开放自动派发 |
| 已有 session_id | 只能证明已记录该会话标识；provider 消息、执行观察、进度受理仍须有完整可信路由、调用者权限和尝试有效性依据 |
| 未记录 session_id、实际投递未知 | 不把空值解释为从未发送，也不盲目重发；若宿主有受测幂等查询/外部对账能力则按合同使用，否则隔离人工处理 |
| 真实终态或已接受反馈 | 保存真实业务结果；缺少完整控制事实时，不自动推导后继、停止完成、可复用证据或策略已结束 |
| 取消请求/结果的源证据 | 目标无 cancel_state；不能仅记录一句 error_msg 就表示请求已受理/取消已确认，无法表达和核验的副作用保留源证据并人工处理 |
| 缺少 deadline / 有效租约 / 在途 Agent fence / 策略预算 | 本版不承诺重启自动恢复，不以重置时间、预算、默认 phase 或重新调用 Agent 代替恢复 |

- worker 不再 claim/finish 任意 intent；外部 IO 不放在数据库锁内。重复扫描不意味着可以重复外部 dispatch，行锁也不代替跨外部调用的有效性核验。
- 提交后崩溃、发出后未保存 session、晚到进度和未知取消都不宣称 exactly-once。L5/G1、worker、会话读取/路由、签名进度和宿主合同完成第 4.1 节适配前，不开放受影响的自动扫描调度。
- 通知仅在已配置宿主能力与真实结果可证时调用；本版不承诺必达通知，不把审计事件当通用消息投递回执。

### 4.5 不建 slot 表：取消租户级 SQL 准入

- 默认策略仍以每个 Collet 一次运行一个候选为目标；已有 ParallelBestOf 的单 Collet 上限不能在删字段后声称仍靠 CAS/冻结运行态自动保证，须随第 4.1 节的调度依据和短事务行锁方案一并验收。
- **不再提供跨 Collet/跨 worker 的租户级候选名额硬保证**，不以进程 semaphore 冒充这种保证，也不把旧 tenant_quota 悄悄当作已执行的 SQL 限额。
- 隔离 capability 继续证明 session/workspace/artifact/外部副作用安全，但其返回值/配置不再承担旧 SQL 配额合同；需同步调整 L5 Setup/record/ports/DI。
- unknown 与未确认取消仍是实际执行风险，不因移除配额而被标记为失败/已结束。对于同一自适应 Collet，未能证明旧候选安全停止时不自动创建冲突后继。

## 5. Profile YAML、运行重建和事务

### 5.1 Profile 配置与 description

config 只保存一份完整执行定义，闭合结构为 schema/composition/stages；composition 是内部生成的完整现有 codec 输出，示意空对象不能直接运行：

```yaml
schema: product-profile/config@1
composition: {}  # 服务填入真实 MultiRollout NEST Swarm 编码
stages: |
  stages:
    - name: implement
      candidates:
        - type: bot
          uuid: example-bot
      depends_on: []
      acceptance:
        - condition: tests pass
```

- 禁止重复键、别名/循环、不支持 tag、未知字段和过深/超限配置。stages 保留原 YAML 字符串，外层 dump/load 不改写其字节。
- 不再生成、持久化或校验独立的 config_digest；仍执行完整的 YAML/schema/精确版本及大小校验。完整 config 序列化后的 UTF-8 编码上限为 65535 字节，与 `TEXT` 容量对齐；原 stages 仍保留 256 KiB 输入上限，但必须同时满足完整 config 的更小容量约束，超限拒绝且不截断；description 最多 2048 字符，显式 null/超限拒绝。
- 读取确切存储的版本/策略，不调用今天的 default_composition 或 latest 回填。不存在能力/版本时 fail closed。
- 创建方法可新增 keyword-only `description: str = ""`，仍只要求 name/stages，不恢复 composition 参数。creator/modifer 由可信上下文自动填充，不新增为方法参数或 HTTP 请求字段；HTTP 严格拒绝调用者伪填审计字段。
- 固定旧 execution encode_profile 的 `id/name/composition/stages` 四字段，新增独立 response encoder 返回 description/status/creator/modifer（审计历史未知为 null），避免改动已有 plan.profile_digest。这四项元数据均不参与执行摘要，status 上下线变化不改变 plan.profile_digest；删除 config_digest 不删除现有 plan.profile_digest、composition_digest 或 evidence_digest，也不放松其校验。

### 5.2 业务读取与 RuntimeSnapshot 适配

| 内存/业务字段 | 从五表读取或待适配项 |
| --- | --- |
| collet | Collet.id/product_profile_id/content/status |
| owner | 产品 Collet.actor_ref 中的可信 PrincipalRef，不从 Profile.creator/modifer 推导 |
| current_rollout_id | Collet 同名指针，不按最大 ID 推断 |
| revision、stopped、bindings、relations、multi | 不能直接按当前 RuntimeSnapshot 完整重建，缺口见第 4.1 节；不伪造默认值或旧兼容响应 |
| rollouts | rollout 按其 ordinal 排序；stage 以 name 对应确切 Profile 原 YAML 节点顺序，不读取 stage.ordinal；collet_id 即 Collet.id |
| records | 仅 stage.id→stage_id、业务 session_id 有直接来源；StageRecord 的 attempt/lease_until/dispatch_state/reused_from/route 及取消事实无完整目标映射，见第 4.1 节，不按旧 codec 默认值补齐 |
| specs | 从该不可变 Profile 的原 stages 字符串 compile_stages |
| execution_ref、acceptance_ref、overall_acceptance | 从精确已存 SwarmStagesPolicy 派生，迁移时与源逐项核验 |
| parents | rollout.predecessor_id 的显式关系，无前驱不生成条目 |

多表查询必须使用**同一显式一致快照读事务**：目标 MySQL 验证 REPEATABLE READ 一致读；SQLite 测试显式 BEGIN。不能用多个 session/过期 identity-map 拼混合版本。缺必要行、缺/错可信 route、实际可核验的 Method/plan digest 或 axes 不一致时 fail closed，不回退旧表或 GET 修复。

保留 12 MiB 逻辑聚合保护及各 YAML/JSON 列字节限额；单 Collet 运行预算在第 4.1 节未补齐前不得默认重置或当作已验证。不宣称关系化即支持无限历史。

### 5.3 创建与业务状态保存

**Profile 创建：** 校验可信 actor/独立发布权、参数、默认 composition 及 host capabilities；在一个事务中插入新 Profile 的 name/description/config/status=DRAFT，并以可信 actor 稳定标识初始化 creator=modifer，失败回滚。不写 owner_json/config_digest，也没有旧配置表或通用回执写入。

**Collet 创建的业务写入计划：**

1. 按可信 tenant/env 加载 Profile，完成第 5.4 节的配置使用授权；在本创建事务内锁定并重读 Profile.status，仅 ONLINE 可继续，状态变更与该检查按第 3.3 节协调。运行创建还须先通过第 4.1 节的合同适配门槛。插入物理 Collet，current_rollout_id 暂为 NULL，运行 owner 取本次可信创建 actor。
2. flush 获取真实 id，API 和 rollout 关联列直接使用该 id；填 `collab_context_id=product-v2:{id}`，不写公开别名或版本列，冲突/超限明确失败。
3. 插入初始 rollout/stage 实际行并取得各自自增 ID；Stage 不写七项排除字段，验收字符串/错误文本先按第 3.6 节校验容量。不能以这些业务行已存在为理由，宣称完整运行 bindings/relations/multi、执行尝试/有效租约/路由/投递/取消事实已持久化。
4. 填 current_rollout_id，追加真实创建审计，然后统一提交；不先填指向不存在后继的关联 ID，不写执行状态快照。

原 Collet 必填兼容列诚实填写：scope 为合法 JSON `null`，creation_facts 为合法 `{}`，root_task_ref/current_snapshot 为 NULL，actor_ref 来自真实创建者；不虚构 L3 授权、L4 Task 或历史时间，也不借这些列重新存入被移除的控制态。已有 context 冲突不得认领别人的 L4 行。

**并发业务写入：** 不再使用 Collet.revision CAS，计划改为根 Collet 行锁串行化；以下只描述业务落库互斥，不声称替代完整运行控制协议。

```text
BEGIN
  按 Collet.id、可信 tenant/env 加载并锁定根行（MySQL SELECT ... FOR UPDATE）
  在锁内重新核验产品归属、owner、current_rollout_id、实际 stage 归属与业务状态
  若本次为反馈，检查已接受反馈事件；一致则重放，不再写后继
  核验本次业务转换依据；涉及异步决策时还须满足已评审的防陈旧合同
  必要时插入完整后继 rollout/stage，flush 后取得真实 ID
  写本次 Collet/rollout/stage 业务变化，最后填写有效 current 指针
  同事务追加业务事件（反馈复用确定性 event_id 并保存确切决策结果）
COMMIT
事务外才调用外部能力；不得持锁等待 human、Agent 或 provider
```

所有产品业务写入者均须遵守相同根行锁顺序、锁内重新读取与授权，固定根→rollout→stage 的锁顺序；死锁/超时失败回滚，重试时重读，不重放旧的内存覆盖。任一业务行/审计写失败全部回滚，自增号允许有空洞。SQLite 不支持同等的 SELECT FOR UPDATE，测试需显式写事务（如 BEGIN IMMEDIATE）及锁等待边界，并另在目标数据库验证。

**行锁不解决的问题：** 锁释放后的外部结果可能陈旧，不能仅比较 status/current 指针（可能往返相同状态）就接受结果，stage.id/session_id 也不等于已移除的执行尝试或策略 fence。旧 expected_revision、策略 operation、防陈旧与停止/预算合同须按第 4.1 节适配，未满足时拒绝受影响提交；不以长事务持锁跨网络绕过该问题。

### 5.4 移除 owner_json 后的 Profile 使用授权

- 当前 `ports.py.get_profile` 返回 `(ProductProfile, PrincipalRef)`，`service.py.create_product_collet` 再调用运行授权的 `check("create", context, owner)`。删列后不能留下该依赖，也不能把 owner 替换成当前调用者、modifer 或凭空构造的 PrincipalRef。
- 计划将 `get_profile` 改为返回含 description/status/creator/modifer 的 Profile，取消配置 owner 元组；读取始终受可信 tenant/env 隔离。新增配置使用的 G1 授权合同 `ProductProfileUseAuthorizer.check_use(profile_id, context)`，由服务在创建 Collet 前调用，具体宿主策略经 DI 注入，依据资源 ID 和可信 scope/actor 判定；无策略或授权失败时拒绝，不退化为同租户任意使用。
- `creator`/`modifer` 不充当 ACL 或运行 owner；不把审计字段改动解释为转移权限。原发布授权 `ProductProfilePublicationAuthorizer.check_publish` 保留，发布权也不等于运行权；新授权合同及其宿主策略必须一起评审，未明确配置使用规则前不开放新写，不隐式扩大原发布者限定的使用范围。此调整不新增授权数据库表。Profile.status=ONLINE 只是新建使用的业务前提，不能替代该权限检查；状态管理权限与运行 owner 控制权限也不混用。
- 已创建 Collet 的查询、反馈、停止和消息读取仍按 Collet.actor_ref 的运行 owner 及现有候选/资源授权校验；不因 Profile 审计缺失、修改或跨用户使用而改写运行 owner。

## 6. 代码改造清单（部分实施，实际状态见 delivery.md）

以下路径相对 `apps/backend/src/agentclaw/community/`。

| 位置 | 计划修改 |
| --- | --- |
| `core/task/context_collab/l6_products/persistence/profile_models.py`、`profile_repository.py` | legacy 模型指向 `_legacy_v1`，保留旧字符串版本、canonical JSON/摘要/权限；与目标 YAML 模型分离。 |
| 新增 `core/task/context_collab/l6_products/persistence/product_relational_models.py` | 仅定义目标 Profile/rollout/stage；Profile.config 使用 Text，scope/name 索引名为 idx_cc_profile_scope_name；Profile 无 config_digest/owner_json，新增非空 status（默认 DRAFT）及 nullable creator/modifer（新建审计必须非空）；Stage 移除七列（含 attempt）/阶段顺序与复用来源索引/复用 FK，acceptances 用 String(8192)、error_msg 用 Text（MySQL TEXT 而非 MEDIUMTEXT）；各表真实自增、普通/唯一索引/tenant guard，不定义物理外键。**不新增五张技术表模型。** |
| `core/task/context_collab/l4_context_primitives/persistence/models.py` | Collet 新增四列：content/status/product_profile_id/current_rollout_id；不新增 runtime_collet_id/revision/execution_state 及专属索引。Event 仅新增 collet_rollout_id/collet_stage_rollout_id 两列，复用既有 event_id/唯一索引，不新增反馈专用键；物理 unsigned/SQLite 整数变体正确。共享 metadata，由 L6 注册关联索引而非字符串 FK，不让 L4 导入 L6 实现。 |
| `core/task/context_collab/l6_products/product_profile.py`、`product_profile_service/contracts.py`、`ports.py`、`service.py` | description/status/creator/modifer 元数据与响应，独立 ProductProfileStatus 校验；create 默认 DRAFT 并从可信 actor 写审计，get_profile 不再返回配置 owner，创建 Collet 调用独立配置使用授权并在事务内检查 ONLINE，状态变更的受控写入同步审计、管理入口另评审；去掉内部 create 的通用回执 UUID/参数；interactive 专用反馈查重/重放，保持默认 human-gated workflow。 |
| `core/task/context_collab/g1_secure_govern/` 授权合同/实现及配置绑定 | 增加 ProductProfileUseAuthorizer 与 fail-closed 宿主策略，不依赖已移除的配置 owner；保留发布授权和基于 Collet.actor_ref 的运行授权，不以 creator/modifer 自动放权。 |
| `core/task/context_collab/l6_products/product_profile_service/configuration.py`、新增 `profile_config_yaml.py` | 闭合 YAML、精确版本、四字段执行摘要与独立响应编码；不引入独立 config_digest，status/description/审计元数据均不进入执行编码或执行 YAML。 |
| `core/task/context_collab/l6_products/persistence/rollout_repository.py` | 全部改为五表 UoW；Collet.id 直接定位，删除别名映射/版本列/控制 JSON 依赖；业务写入根行锁、一致读与基于确定性 event_id 的反馈查重，跨 context 唯一范围缺口先评审；不再读旧 snapshot 或写 receipt/intent/slot。运行态缺失不得伪造。 |
| 新增 `core/task/context_collab/l6_products/persistence/runtime_relational_codec.py`、`runtime_event_writer.py` | 五表业务读取、按原 YAML/name 恢复阶段列表顺序、验收 JSON 序列化后 8192 字符/错误 TEXT 字节容量校验、反馈业务事件 event_id 确定性编码/已有唯一索引复用、重放及同事务审计；跨 context 仲裁按第 4.3 节先评审；仅对有来源字段解码，完整 RuntimeSnapshot/StageRecord/SessionRoute 缺口按第 4.1 节处理。没有控制大 JSON、Event checkpoint、独立 commit 或通用事件任务队列。 |
| `core/task/context_collab/l5_methods/rollout_contracts.py`、`rollout_lifecycle.py`、`multi_rollout_state.py` | Repository 去掉通用 replay、receipt/intents、acquire_slot/release_slots、pending/claim/finish_intent 依赖；配套评审 revision/CAS、RuntimeSnapshot/ReportContext 的合同演进与防陈旧，未覆盖控制事实禁止自动恢复；不再计划给 StageRecord 增加 cancel_state，移除对七项 Stage 数据库列的读写假设，去掉 StageRecord/ReportContext/执行端口的 attempt 依赖及其生成/校验路径；attempt/lease/dispatch/reuse/route 的契约适配先评审，初始化使用真实业务行 IDs。 |
| `core/task/context_collab/l5_methods/multi_rollout_contracts.py`、`multi_rollout_controller.py`、`multi_rollout_execution.py` | 移除 tenant_quota/candidate.slot 及租户 SQL 准入；human-gated workflow、单 Collet 上限/预算和 operation fence 的适配先解决第 4.1 节缺口，不宣称删字段后已保留完整恢复；可信隔离与 evidence 不放宽。 |
| `core/task/context_collab/l6_products/product_profile_service/snapshot_codec.py`、`multi_snapshot_codec.py` | 不再建设 execution_state schema；现有内存控制类型须与简化业务仓储显式适配，禁止伪造缺失值。旧完整 snapshot decoder 仅供迁移源/历史测试，不无声吞掉删减事实。 |
| `core/task/context_collab/l6_products/persistence/runtime_outbox.py`、L5 `rollout_worker.py`、`rollout_engine.py` 及进度入口 | 从正式路径移除原 Outbox/slot mixin；worker 的业务扫描须受事实完备性门槛限制；只按目标可证明的 session_id 和真实业务结果读取；去掉 attempt 持久化/生成/比较，不用 stage.id 或 session_id 派生替代 token；完整路由、投递/租约/取消/复用及进度 expected_revision/异步返回核验须显式演进，不把行锁等同 CAS/fence。 |
| `core/task/context_collab/l5_methods/openapi_rollout_execution.py`、执行能力适配、`adapters/http/task/product_progress.py` 及签名进度调用方 | 配套移除 attempt 参数/DTO 字段与 `product_stage_attempt` metadata 依赖，解除 `message_id=attempt` 耦合；provider 请求关联/幂等、旧请求兼容与晚到回调的可信校验单独评审，不宣称仅有 stage.id/session_id 就保留原防重复/防陈旧保障。 |
| `core/task/context_collab/l4_context_primitives/event_envelope.py`、`persistence/row_codec.py` | 本地记录只新增两个关联，复用原 event_id，不增加 interactive_key；ACCS EventEnvelope 不暗增产品控制字段；区分产品审计和 L4 接受事件。 |
| L4 `persistence/repository.py`、`task_storage.py`，G3 `observation.py` 及 legacy 产品安装入口 | 产品运行行拒绝旧 Work/Method writer；产品审计不驱动 L4 Task/context_version/current-event/计数，普通旧 L4 行行为不变。 |
| `adapters/http/task/product_profiles.py`、`di/modules/task/product_profile_runtime.py`、隔离/通知适配及数据库事务边界 | 薄 DTO 响应补 description/status/creator/modifer，当前 create 请求不开放 status 或审计字段；同一五表仓储及配置使用授权绑定、一致读合同、状态扫描宿主入口；不安装五张配套表/旧六表，不把私有实现导入公共路径。 |
| `core/task/context_collab/l6_products/persistence/rollout_models.py`、安装清单、迁移工具、owning README/ADR/测试 | 旧六表模型退出正常 metadata/create_all/DI，仅迁移源显式加载；新增五表 SQL 及控制/恢复合同变更，历史 SQL 和源证据保留。 |

### 6.1 五个服务方法对应关系

本表描述服务入口所触发的业务范围，不限定 SQL 必须由该方法直接执行，也不表示运行全过程在接口返回前完成；后续调度、进度与验收链路的自动持久化责任见第 6.2 节。

| 方法 | 目标依赖 |
| --- | --- |
| create_product_profile | 发布授权后一次事务写 name/description/config/status=DRAFT/creator/modifer，无独立配置/回执表 |
| create_product_collet | 独立 Profile 使用授权 + 事务内 Profile.status=ONLINE 检查 + Profile/Collet/rollout/stage/Event，记录本次 actor 为运行 owner，完整初始化后提交 |
| query_collet_rollout | 五表业务行一致读；授权后按 current_rollout_id/all_rollout 返回。现有 revision/stopped 响应兼容待合同评审，不伪造缺失控制字段 |
| interactive | rollout→Collet + 确定性 event_id 定位已接受反馈 Event 查重；Agent 事务外，业务结果事务内根行锁提交；新策略决策需先解决第 4.1 节的防陈旧/控制事实及第 4.3 节跨 context 仲裁缺口 |
| query_stage_rollout_details | stage→rollout→Collet，保留调用者授权；去掉路由 JSON 后须先明确完整可信 SessionRoute 来源，session_id 不作为访问凭据。来源不全则拒绝 provider 读取，不伪造空消息 |

### 6.2 接口执行链路中的自动持久化责任

**自动落库覆盖接口触发的整个执行链路，而不只覆盖 ProductProfileService 方法体。** 创建、调度、执行结果观察/上报、验收、human 反馈和后继 rollout 的业务事实，均应由对应链路节点自动通过同一套五表仓储写入或更新；不能仅在创建时写一份初始记录、后续只改内存，也不要求调用方或运维额外手工写表。以下均为实施要求，不代表当前实现已切换。

```text
ProductProfileService 创建/交互入口
  → 领域编排与初始化 → Repository / Unit of Work → 五表业务记录
宿主调度 / worker / 独立签名进度入口（可以发生在原接口返回后）
  → L5 执行、观察、验收与状态转换 → 同一仓储协议 → 五表业务更新与审计
human 反馈 → adaptation Agent 提议 → 重新核验 → 同一仓储事务提交决策及必要后继
```

- **责任分层：** ProductProfileService 负责产品入口、授权和编排；L5 RolloutEngine/MultiRolloutController 及生命周期逻辑负责业务转换与策略判断；生命周期计算函数不自行提交数据库。通过仓储协议调用 L6 持久化适配，由 Repository / Unit of Work 完成业务行与 Event 的原子写入，不让 L5 直接依赖 L6 ORM，也不把 SQL 散落到 HTTP handler。
- **外部能力与宿主：** provider/执行/验收适配器返回真实会话、观察结果或验收证据，不各自绕过状态转换另开事务更新业务表。宿主显式配置调度、worker/状态扫描和进度入口；“接口链路自动更新”不等于在公共应用中隐式启动后台调度器，也不等于依赖查询请求驱动执行。

下表省略表名前缀 `ac_context_collab_`。每一行表示一个业务事实受理节点，并非一个覆盖整个执行过程的长事务：

| 链路节点 / 触发条件 | 业务责任方 | 自动新增或更新的业务记录 | 提交与事实边界 |
| --- | --- | --- | --- |
| 创建 Profile | ProductProfileService 配置创建链路 | 新增 product_profile 的 name/description/config/status=DRAFT/creator/modifer | 一个配置创建事务；不因创建成功自动上线，不虚构没有运行 context 的产品运行 Event |
| 创建 Collet 并初始化静态 workflow | 产品创建编排、L5 初始化、仓储 | 新增 collet、初始 collet_rollout、各 stage_rollout、创建 event；设置 current_rollout_id | 第 5.3 节统一事务；只读取/校验 Profile，不将配置写成运行快照；返回成功表示初始化已提交，不表示远端执行已完成 |
| DAG 条件满足且已完成执行者授权，准备派发 | L5 执行引擎与宿主调度链路 | 按真实分配更新 stage_rollout.assignee_type/assignee_uuid/input，追加相应业务审计 | 本地已受理事实先提交，外部派发在事务外；这些字段不证明投递成功，无 attempt/投递/租约列时须先完成第 4.1 节的调度合同适配 |
| 执行端返回可信真实会话 | 执行适配器返回、L5 核验与仓储提交链路 | 更新对应 stage_rollout.session_id，并记录已确认的业务事实 | 重新核验归属及受理有效性后提交；只绑定真实会话，不能仅因 start 返回就把状态改成 RUNNING/RUN_FINISHED；晚到或归属不符不得覆盖当前记录 |
| 运行观察或进度上报被验证受理 | L5 执行引擎、独立签名进度入口 | 按真实结果更新 stage_rollout.status/error_msg；按已评审的 L5 汇总规则更新 collet_rollout.status/collet.status，并追加 event | 一次受理中的阶段变化、父级业务汇总和审计同事务；不把 provider 通信异常、未知执行或不可信报告伪装为业务失败/成功 |
| 验收结果及产物通过身份、条件与证据校验 | L5 验收受理链路及验收能力 | 更新 stage_rollout.acceptances/artifacts/status 及有真实来源的 error_msg；更新受影响父级业务汇总，追加 event | 验收 JSON 与错误容量校验先于提交，业务状态与证据同事务；拒绝或超限不得部分保存，不让阶段验收冒充另有授权要求的 Collet 最终验收 |
| 可信执行失败或验收失败，进入 human 反馈等待 | L5 MultiRollout 失败处理链路 | 保存真实失败结果及对应 event，保留可查询的已有 rollout/stage | 不因失败自动创建后继；不新增 WAITING_FEEDBACK 等业务 status，不以审计替代本版尚未解决的等待期限/策略控制态 |
| human 调用 interactive，Agent 返回提议并经核验受理 | ProductProfileService、MultiRolloutController 与仓储 | 保存确定性 event_id 的受理 event；按决策更新业务记录；RE_DO/OPTIMIZE 需要后继时新增 collet_rollout/stage_rollout，设置 predecessor_id/current_rollout_id | Agent 在事务外调用；重加锁核验后将受理事实、业务变化及后继原子提交。DO_NOTHING 不新增 rollout；SHUTDOWN 不虚构业务 STOPPED 状态或取消成功；重复反馈只重放原结果 |
| query_collet_rollout / query_stage_rollout_details | 只读查询链路及会话读取能力 | 读取既有五表业务记录，必要时在完整可信路由与授权具备时读取 provider 消息 | 查询不更新状态、不补写审计、不修复缺行、不触发重跑；完整路由缺失时不返回伪造空消息 |

**事务边界与实施门槛：** 自动持久化按每次已受理业务转换提交，而不是把 HTTP 请求、多个阶段、human 等待和外部调用包进一个事务。外部调用前后的本地业务提交分开，后一次提交必须有已评审的防陈旧与权限依据；业务行、同次父级汇总和 Event 不能分开成功。数据库提交成功不等于 provider 已执行，也不能保证无 Outbox 时提交后一定被唤醒。第 4.1 节的运行控制/路由/异步关联缺口及第 4.3 节的跨 context 反馈仲裁仍是放行门槛，本节不能替代这些合同或宣称重启恢复已完成。Profile 上下线仍走另行评审的管理入口，不借执行链路自动将 DRAFT 改成 ONLINE。

## 7. Event 与 L4 边界

- 采用闭合 `product_runtime.*` 类型，记录真实创建、状态、反馈决策、停止、投递/取消观察等；Event 是审计，不是待执行命令或第二套状态机。
- Collet 级两个关联列可为 NULL，rollout 级填写 rollout，stage 级两个都填；反馈的确定性 event_id 只用于成功提交的受理事件，与普通审计命名空间分开。
- 除反馈受理事件外，普通审计 event_id 可按 `product-v2:{Collet.id}:{本次提交UUID}:{稳定ordinal}` 生成，不再依赖提交 revision；UUID 只是本事务审计分组，不建设通用请求回执。事件 ID 须校验不超过既有 128 字符上限；稳定顺序不使用无序遍历，重复接受反馈不再插事件/后继。原 L4 event_id 不改写，产品事件的 collab_context_id 仍填写真实 Collet context，不为去重伪造共享 context。
- 产品审计 `primitive_type/task_ref/task_version_after/context_version_after` 为 NULL，parent_event 不伪接 L4 Work 主干；visibility 默认 private，actor 来自可信上下文。
- 明确从 L4 reducer/current-event、context_version 增量、Task/普通消息计数中排除；本地 EventRecord 扩展不是 ACCS Envelope 协议升级。
- 反馈正文与决策同事务保存，DO_NOTHING/SHUTDOWN 也留真实受理证据；未接受提议不得记为已受理。保留/读取权限和脱敏同步设计，不存 token/签名/凭据。

## 8. 迁移、退出与回滚计划

### 8.1 旧表去向

| 迁移来源 | 五表方案的处理 |
| --- | --- |
| `ac_product_configuration_v2` | 原 ID/name/确切 composition/stages 转到目标 Profile，description 为空，源 v2 无生命周期字段时按兼容规则显式置 status=DRAFT（迁移默认，不自动上线），有可信 DRAFT/ONLINE/OFFLINE 来源则保留，不因旧配置曾被使用而推断当前 ONLINE；保留四字段执行摘要；源 owner 仅在核验为真实创建人时转 creator，modifer 仅有可验证最后写入人来源时填，否则各自 NULL；不保留配置 owner 或独立 config_digest 列 |
| `ac_product_runtime_v2` | 可验证业务字段拆到 Collet/rollout/stage；不导入别名/版本列/执行快照。未覆盖的运行控制事实保留源证据，不自动迁为可续跑运行；见第 4.1 节 |
| `ac_product_identity_v2` | 只作为离线旧 ID/父级/高水位证据；Collet 原 ID 无冲突才导入，同号不同对象停止自动迁移，不建正式别名映射或 identity 表 |
| `ac_product_receipt_v2` | 不复制成通用回执；历史 interactive 的可验证受理结果按下节转换为有来源的反馈迁移记录，其他回执冻结归档 |
| `ac_product_intent_v2` | 不复制通用队列；切换前排空/对账，能由目标业务状态重建的继续扫描，不能证明的副作用冻结并人工处理 |
| `ac_product_candidate_slot_v1` | 不复制/新建名额表；旧占用用于切换前外部执行对账，不因删设施宣称真实执行已停止 |

### 8.2 停写、回填和验证顺序

1. 在真实目标 schema 预检并备份；停止旧新 writers、worker、deadline sweep，记录所有 allocator 下一值及未知外部副作用。旧五张配套表尚未实施时本就无需 DROP；如外部环境已创建，由独立盘点/审批决定处理，不包含在本次文档操作中。
2. 更名同名旧 Profile，部署 legacy 兼容读取；创建三张目标表、完成 Collet/Event ALTER。普通 L4 行保持新增 NULL，不认领、不虚构状态。
3. 导入配置与有来源的 creator/modifer；无源状态的旧 v2 配置显式设置 DRAFT，可信已有生命周期状态保留，非法状态停止导入，不补造历史上线事件；审计未知保留 NULL，不用迁移人补造历史，不生成独立配置摘要。核验原 Collet ID 不与现有行冲突后直接保留该 ID，同号不同对象不自动导入；按 rollout 前驱依赖导入可验证业务事实，Stage 按确切 Profile YAML/name 核对；不导入阶段 attempt/ordinal/租约/投递/复用/路由 JSON/取消列，验收序列化超 8192 字符或错误文本超 TEXT 容量时停止并人工评审，不截断证据。最后填有效 current 指针。各表推进自增高水位，不建公开别名。
4. 逐项登记旧 multi/Method/计划/预算/停止、StageRecord attempt/投递/租约/复用/取消与完整 SessionRoute 在目标中的缺口；不把源记录直接换名塞进现有列，不重算原 profile/plan/Method/attempt/route/deadline/预算冒充迁移。未能按已评审合同表达的运行冻结并人工处理，不自动放行续跑。
5. 维持旧 interactive UUID 重放时，仅从经严格核验且能唯一关联的 source interactive 回执生成 `product_runtime.feedback.imported` 迁移事实：保存原 request_digest、确切 decision、actor_key/UUID 指纹、对应 rollout 和 source 摘要；按第 4.3 节相同规则生成确定性 event_id，imported/accepted 共用同一反馈 ID 命名空间，不生成随机导入 ID 绕过去重。若目标已有同 ID 则核验原请求/身份/结果，冲突或跨 context 歧义停止自动迁移，不覆盖旧行。源通用回执没有 rollout/Collet 字段，不能反解请求摘要；必须结合可验证的 multi.decisions（key/source/actor）或经审查的外部映射完成关联。缺失/歧义时停止自动切换，不编造关联，也不让正式 reader 回查旧表。occurred_at 只代表本次导入事实时间，不冒充未知历史发生时间；actor_ref 是真实迁移操作人，不伪造原反馈者。重放核验原身份指纹与权限，不能把迁移操作人的身份当业务 owner。
6. 对源未完成 intent、claim、取消和名额逐项对账。无法由真实业务行表达或外部结果无法证明的运行**不自动放行执行**，保留源证据并隔离人工处理；不把未发送通知标为已送达，不把未知 run 标为已取消。
7. 对账 Profile 原四字段执行摘要（不是已移除的 config_digest）、Profile.status 实际值/迁移默认依据、审计身份来源/未知值、Collet.id/各级关联、业务字段/rollout 次序与前驱/阶段原 YAML 顺序、验收与错误完整文本、session_id、current 指针和反馈重放。源 attempt、完整 route、lease/dispatch/reuse/取消及 stopped/等待期限/预算等缺失事实须单独登记为不可续跑或不可读取会话差异，不使用忽略全部控制态的“对账通过”。
8. 先只读验证/授权，确认独立 Profile 使用策略不会隐式放宽旧使用权限；第 4.1 节缺口和进度协议演进完成评审、测试后，才开放受影响运行写入及自动状态扫描。新普通进程无旧六表访问/注册；可在完全不存在旧六表及五张配套表的隔离库完成验收，迁移来源访问限独立 operator。

原 Event 默认不改写；没有可信源就不补造历史 Event/操作者。回填 gmt_create/gmt_modified 若无历史来源只表示此次落库时间。旧字符串 profile_id@version 不自动转新整数配置，保留旧归档和现有显式迁移边界。

### 8.3 回滚与最终退出

- 切换前失败维持停写，按实际 schema/备份回退；不向旧 writer 暴露新结构同名表，不以 DROP/删关联审计让旧程序启动。
- 切换后旧六表只可作为冻结历史来源。产生新写后旧表即已落后，不能直接切回旧二进制/旧 snapshot；优先采用仍读写五表的兼容回滚版本，确需逆向迁移则停写、专门转换、全量对账。
- 不因回滚把未知执行当已终止、覆盖已提交业务事实/反馈或重置预算。旧表保留期结束后再经审批归档/下线；本次不执行删表。

## 9. 实施分解与验收

### 9.1 后续实施批次

1. 五表 DDL/ORM、legacy 避让、Collet.id 单一身份/历史重号检查与自增高水位、安装/离线迁移工具。
2. Profile YAML/description/status/creator/modifer、默认 DRAFT/闭合枚举/新建可用性校验与状态审计规则、可信 actor 审计及未知历史处理、精确版本/四字段执行摘要、移除配置 owner 后的独立使用授权、业务反馈 event_id 确定性编码/已有唯一索引复用、跨 context 仲裁评审与重放。
3. Collet 只新增 content/status 及保留的两个关联列；Stage 移除七列（含 attempt）、改验收/错误类型、按原 YAML/name 排序；业务一致读、短事务根行锁，去掉别名/版本列/执行 JSON 依赖；先评审无 attempt 的执行关联、完整会话定位、租约/投递/取消/复用、控制事实、异步防陈旧和进度协议，不宣称完整重建已解决。
4. 合同适配明确后，再删除正常路径的通用 receipt/Outbox/slot 合同和实现，重构受事实完备性限制的状态扫描，明确恢复能力变化并同步 host/DI/诊断。
5. L4 隔离、目标数据库迁移/故障/回滚与无旧表运行验收；通过合同评审再放新写。不得把“本方案不建设”误写成“已删除部署表”。

### 9.2 必须补充的测试

- 正式安装只涉及五表，不出现五张排除表或旧运行表依赖；普通启动/metadata/create_all 不建回旧表。
- ORM/DDL 的字段、nullable、signed/unsigned、普通/唯一索引一致；五表 metadata、MySQL 方言编译、实际 SQLite 反射及迁移 SQL 均无物理外键。SQLite 保持 PRAGMA foreign_keys=ON，不靠关闭检查通过；原子创建/审计校验、坏 current/前驱/缺父记录/孤立 stage 拒绝及唯一键竞争有真实数据库测试；目标库另验 DDL/隔离/锁行为。
- 全部原始需求字段、status 枚举、闭合 JSON/YAML、description、旧四字段执行摘要/plan digest 正确；独立 config_digest/owner_json 不在目标 Profile ORM/DDL/读写中，审计元数据不影响执行摘要；未知 schema/缺状态 fail closed。
- Profile.config 的 DDL/ORM 均为 TEXT；非唯一索引为 idx_cc_profile_scope_name，列顺序保持 avernet_tenant/env/name；完整 YAML 序列化后 UTF-8 65535 字节可往返，65536 字节创建/读取拒绝且不截断，失败创建不遗留记录；ASCII 与中文均覆盖。
- Profile.status 的 DDL/ORM/领域默认均为 DRAFT，新建服务显式写入 DRAFT、响应为草稿且不自动上线，三个枚举值与响应往返正确；未知/空/null/错误大小写拒绝，旧 v2 无状态按显式兼容规则迁为 DRAFT，可信已有生命周期状态保留，不虚构上线事件。status 不进入 config/执行摘要，状态变化不改变原四字段 plan.profile_digest。
- DRAFT/OFFLINE 不能创建新 Collet，ONLINE 仍须使用授权；Profile 行锁协调并发下线/新建，失败不留下 Collet/子行/审计。后续状态受控写入须授权、更新 modifer/时间且保留 creator，不自动停止既有运行或阻止其原授权操作；不擅自新增上下线 API。
- 新建 creator=modifer 且来自可信 actor，伪填审计字段被拒绝，标识长度/类型校验、事务回滚、历史未知 NULL/真实来源转换及响应拼写一致；不存在擅自新增的 Profile 更新接口。
- 配置发布权/使用权/运行控制权分离：使用授权 hook 确实被调用，拒绝和未配置不落库，不凭同租户/creator/modifer 自动放行，跨 tenant/env 拒绝；Collet owner 始终为本次创建 actor，原运行授权不回退到 Profile 审计字段。
- Collet 新增列恰为 content/status/product_profile_id/current_rollout_id，目标 DDL/ORM 无 runtime_collet_id/revision/execution_state 或同义控制快照；content/status 读写、普通 L4 行 NULL 兼容正确。
- Stage DDL/ORM 不含七项排除字段（含 attempt）及阶段顺序/复用来源索引、复用来源 FK；acceptances=VARCHAR(8192)、error_msg=TEXT、artifacts=MEDIUMTEXT；Event 关联阶段不定义复合 FK，阶段父唯一键保留，rollout.ordinal 不被误删。
- attempt 不在目标 DDL/ORM/编码/读写中，也不藏入 JSON/Event 或换名列；执行端口、StageRecord/ReportContext、HTTP 请求与签名调用方同步移除依赖。旧 attempt 不默认为空值、不由 stage.id/session_id 派生；旧请求切换、provider 请求关联、防重复派发/陈旧回调及崩溃恢复有明确合同与消费者测试，未具备依据时拒绝自动处理。
- acceptances 最终 JSON 的 8192/8193 字符、转义/多字节边界、空数组与非法 JSON；error_msg UTF-8 65535/65536 字节边界和事务回滚。迁移超限不截断、不丢验收项、不偷换回 MEDIUMTEXT；目标库验证实际字符/字节与行容量。
- 阶段列表依确切 Profile 原 YAML/name 恢复稳定顺序，不依赖插入顺序/ID；未知、重复或缺失阶段有明确错误。session_id/assignee 不能替代完整 SessionRoute，缺路由/权限/有效尝试/取消或复用证据时拒绝受影响操作，不用默认值伪造历史。
- 类型范围 ID 可重号，但 Collet.id 即 API/关联身份；历史同号不同对象阻止自动迁移，既有 L4 行不被认领；各表自增高水位正确，不用假记录取号或正式别名映射。
- 目标库多连接根行锁、固定锁顺序、锁超时/死锁和重试重读；任一子表/Event 失败完全回滚，current 只指向已存在的同 Collet rollout，并发读取不混版；SQLite 写事务检查不能替代目标库行锁证据。
- Event 新增列恰为 collet_rollout_id/collet_stage_rollout_id，不新增 interactive_key/uk_cc_event_interactive 或同义键；event_id 仍为 VARCHAR(128) NOT NULL，uk_context_collab_event 的四列及原 L4 跨 context 重号语义不变。
- 同 actor/UUID 生成相同的确定性 event_id；换 actor/tenant/env 隔离，换内容/rollout 不换 ID 而报冲突，大小写不同的 opaque UUID 不被折叠；前缀/84 字符/128 字符上限、版本化 canonical 编码、非法碰撞与权限先于重放均有测试。
- 同 context 的唯一键竞争不重复已提交反馈/后继、失败事务完全回滚；相同 event_id 跨 context 可通过旧索引的反例须覆盖，不能把它当全 scope 仲裁成功。原 tenant/env/actor/UUID 跨 context 冲突合同另有评审方案及并发验收，未解决不得开放受影响反馈写入；imported/accepted 使用相同确定性 ID，迁移冲突/身份来源/旧请求摘要核对，不被迁移 actor 混淆。
- 执行链路自动落库验收：从创建 Collet 到 DAG 派发准备、真实会话绑定、运行观察/签名进度受理、验收证据、失败等待 human、交互决策与后继 rollout，逐节点核对业务行、父级汇总、Event 和事务回滚；不只测接口方法直接调用。覆盖接口返回后 worker/进度入口仍落库、无额外调用方写表、外部适配器不绕过仓储独立提交、查询零业务写入及显式宿主调度要求。依赖未解决运行事实的恢复/防陈旧与跨 context 场景仍按前述门槛验收，不凭此用例名宣称已有完整保障。
- human 反馈前不自动 rollout；缺少停止/预算/Agent fence 等事实时不自动恢复或提交陈旧提议；旧 expected_revision/查询控制元数据兼容有显式合同与消费者测试，不用行锁/默认值冒充。适配后再验收 DO_NOTHING 不延长等待、后继不复用 session 和单 Collet 上限。
- 无 Outbox 时重启扫描、重复扫描、提交后未唤醒、provider 响应前后崩溃、unknown/晚到 route、取消未确认均按真实事实处理；不宣称 exactly-once，不盲目重发。
- 旧 L4 writer 无法操作产品行，产品审计不影响独立 Task/current-event/context_version/计数；session/provider/Bot/owner/lifecycle/run 路由和访问授权完整。
- 迁移对账/幂等/冲突、不伪造历史、未完成源副作用隔离、自增推进、停写/回滚和完全无旧六表/配套表运行验收。

实现完成后覆盖现有 `tests/community/core/task/context_collab/product_profile_service/`、`multi_rollout/`、`tests/community/repository/task/context_collab/`、HTTP/DI/architecture 测试和 Backend full CI，并另行验证真实 host/provider。单测通过不能替代目标库和远端副作用证据。

### 9.3 前期文档阶段的验证与缺口（保留历史，当前进度见 delivery.md）

以下是前期文档阶段记录，其中“7 个外键”及目标库 FK 能力要求已被本次无物理外键修订替代，不代表当前 schema；最新实际验证见 delivery.md 的“移除物理外键”记录。

- 本轮仅修订本方案，两个既有 Proposed 导航保持不变，保留前两版替代方案理由；未修改生产代码、SQL 文件、数据库或部署。
- 本版文档级静态检查通过：13 个方案/新增导航相对链接可解析，3 个 Markdown 文件围栏、结尾换行及尾随空白正常；目标 DDL 为五张正式表（3 个 CREATE、3 个 ALTER），Collet 新增四列且不含三项排除字段，Event 仅新增两个关联列且不含 interactive_key/新增反馈唯一索引，7 个外键名唯一（已去掉阶段复用来源 FK），引用列/父唯一键和 ID signed/unsigned 对齐。五张排除表、旧运行表、完整 snapshot_json 和 DROP TABLE 均未出现在目标 DDL 中。
- 原始需求字段的存在性、新增 creator/modifer、Profile 三值生命周期配套说明、目标 Profile 不含 config_digest/owner_json、Collet/Stage 字段简化（含移除 attempt）与 Stage 验收/错误类型、Event 复用 event_id/既有唯一范围及跨 context 缺口、审计/授权/迁移及控制态/会话路由缺口说明、业务 StatusEnum、示例 YAML 语法，以及源 RuntimeSnapshot/StageRecord/SessionRoute/MultiRollout/RunPlan/StrategyOperation 字段的文档映射或明确删减说明检查通过；`git diff --check` 通过。这只是文档结构/字段覆盖检查，不是 SQL 解析器、数据库执行或实现正确性证明；上一版“十张表”、Collet 七新增列/CAS/控制 JSON、Stage 仍保留辅助控制列（含 attempt）或八外键版本的验证不作为本版证据。
- 已补充第 6.2 节的执行链路自动持久化责任矩阵及对应验收要求，明确入口编排、L5 转换、仓储事务、宿主调度、外部能力和只读查询的边界；本轮静态检查只验证说明覆盖，不证明 worker/provider/进度调用已完成接入。
- 已对照当前 Event ORM 的 AST 核验 event_id=VARCHAR(128) NOT NULL 及既有四列唯一索引声明，并检查反馈 ID 示意编码的确定性、84 ASCII 字符长度及 axes/actor/opaque UUID 区分；这不是仓储实现或数据库并发测试，跨 context 仲裁仍未解决。
- Profile 默认值已按用户确认统一为 `DRAFT`：目标 DDL、计划中的 ORM/领域默认、新建服务写入、无源状态迁移及测试要求一致；可信已有生命周期状态仍保留，仅 ONLINE 可创建新 Collet。此前按先前 ONLINE 约定检查而出现的默认值告警已解决，本轮重新执行静态检查通过；这不代表代码或数据库已改造。
- **尚未执行**目标 DDL、代码改造、业务测试、迁移/回滚、目标库隔离/外键测试或 host/provider 集成；本机无 MySQL/Docker 执行环境。
- 待实施确认：目标数据库版本/分区与 FK 能力、allocator 高水位/历史 Collet ID 重号、无版本列的进度协议与异步防陈旧、无执行状态列的完整运行/恢复合同、历史审计身份的可信来源和配置使用授权策略、Profile 状态管理入口及管理授权、历史 session 长度及移除 attempt 后的执行/进度关联、复用 event_id 后原反馈 UUID 的跨 context 并发仲裁与源回执映射、验收 8192 字符/错误 TEXT 缩容与阶段顺序可表达性、无路由/租约/投递/取消/复用列的 SessionRoute/StageRecord 适配、源反馈回执和未完成副作用的可转换性、宿主状态扫描及幂等/对账能力。
- 现有 owning README 继续描述当前实现。后续采纳本版时须明确更新 receipt/Outbox/租户配额和恢复边界合同，不能仅删表或改导航就声称完成统一。
