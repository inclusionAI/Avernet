# T02 存储布局与组合事务约束

关联：[T02](01-foundation-auth.md)、[接口](interfaces.md)、[spec §9](../spec.md)。下面是**迁移编写输入**，不是已经应用的SQL；后续任务必须复用此布局，不回填已提交迁移。所有ID、列名、scope规则在T02测试里固定。

## 1. 方言和命名

- 整数用 signed BIGINT／SQLite INTEGER；应用限制 revision/sequence ≤ i64::MAX，checked_add。
- 32字节摘要 MySQL BINARY(32)、SQLite BLOB；JSON MySQL JSON、SQLite TEXT，并在Store严格decode。
- ID／scope字符串采用utf8mb4 binary no-pad比较或VARBINARY；SQLite BINARY。不允许MySQL把 `A`/`a`、尾部空白当相同身份。
- 时间统一 `_ms` epoch millis；nullable用NULL，不以0伪装缺失。
- `a2a.deployment`来自可信配置，必须非空；env使用既有BcsConfig解析值。首期同一已选DbPlugin数据源只允许一个(deployment,env)，持久化singleton绑定并在bootstrap/CLI启动核验；异deployment/env拒绝。Store构造只有db和StoreScope，不传logical_db，不支持多deployment共库。所有操作/后台扫描仍带固定deployment，Group/Session fragment带固定env。
- 复合身份`scope_hash`为带长度前缀的(deployment,issuer,subject,group_id)字节串SHA256；`reservation_hash`再包含原messageId。完整键一起保存，命中hash后必须比较全键；碰撞拒绝，不能复用错误资源。
- Task UUID全局唯一；所有其子表仍按授权后的TaskKey访问，不对外提供裸artifact读取接口。

既有迁移039中的 `bcs_a2a_tasks`、`bcs_a2a_contexts` 是Bot出站协议表；只读核对其schema与基线数据不变，禁止重建/ALTER复用。T02必须新增双方言SQL文件和include_str注册，不只新增Rust模块。

## 2. 字段清单

类型记法：`s(n)`=最多n字符、`s`=有界字符串、`j`=JSON、`b32`=32字节、`i`=BIGINT、`?`=nullable、`bool`=0/1。每条括号内字段全部非空，除非带?；未写默认值的字段必须显式INSERT。`pk`/`uk`/`ix`都是实际迁移约束，不仅代码约定。

| 表／既有表扩展 | 字段与约束 |
| --- | --- |
| bcs_group_a2a_deployment_binding | slot i pk CHECK(slot=1), deployment s(128), env s(32)；单数据源唯一映射，第二映射启动失败 |
| bcs_group_runtime_bindings ADD | a2a_output_json j? DEFAULT NULL（读取归一为空列表），a2a_binding_revision i NOT NULL DEFAULT 0；GroupRuntimeBinding.a2a_output的权威配置，旧逻辑不能忽略更新 |
| bcs_groups ADD | support_a2a bool NOT NULL DEFAULT 0；a2a_revision i NOT NULL DEFAULT 0；旧群不批量启用 |
| bcs_group_sessions ADD | a2a_managed bool NOT NULL DEFAULT 0；托管上下文事实由context表保留，不能仅靠调用方meta |
| bcs_a2a_clients | deployment s(128), client_id s(128), enabled bool, created_at_ms i；pk(deployment,client_id)；disable不删ID |
| bcs_a2a_tokens | credential_id s(36) pk, deployment s(128), client_id s(128), digest b32 uk, purpose s(32), issued_at_ms i, expires_at_ms i, revoked_at_ms i?；ix(deployment,client_id,issued_at_ms)；secret不落库 |
| bcs_group_a2a_configs | deployment s(128), group_id s(128), group_revision i, binding_revision i, eligible bool, group_status s(24), config_hash b32, execution_json j, card_json j；pk(deployment,group_id)；a2aOutput保存在execution_json，读Card无需查询成员/所有Session |
| bcs_group_a2a_contexts | session_id s(128) pk, scope_hash b32, deployment s(128), caller_issuer s(128), caller_subject s(256), group_id s(128), source s(16), state s(24), generation i, active_task_id s(36)?, previous_task_id s(36)?, config_hash b32, initial_input_json j?, execution_json j?, isolated_reason s(64)?, last_terminal_at_ms i?, history_expired_at_ms i?, revision i；ix(scope_hash,session_id) |
| bcs_group_a2a_tasks | task_id s(36) pk, session_id s(128), scope_hash b32, deployment s(128), caller_issuer s(128), caller_subject s(256), group_id s(128), generation i, state s(24), phase s(24), revision i, status_timestamp_ms i, status_message_json j?, request_json j?, input_json j?, options_json j, selected_output_modes_json j, include_progress bool, execution_id s(36) uk, completion_json j?, execution_deadline_ms i, settle_deadline_ms i?, terminal_at_ms i?, result_deleted_at_ms i?, expires_at_ms i?, artifact_bytes i, history_bytes i, public_sequence i, reminder_sent bool, idle_accumulated_ms i, idle_since_ms i?, failure_reason s(64)? |
| bcs_group_a2a_message_reservations | reservation_hash b32 pk, scope_hash b32, deployment s(128), caller_issuer s(128), caller_subject s(256), group_id s(128), message_id s(256), fingerprint_version i, fingerprint b32, task_id s(36), result_expired bool, created_at_ms i；task可删除内容但预留不级联删除 |
| bcs_group_a2a_artifacts | task_id s(36), artifact_id s(128), context_id s(128), position i, name s(256)?, description s?, parts_json j, metadata_json j?, extensions_json j?, created_at_ms i；pk(task_id,artifact_id)，uk(task_id,position) |
| bcs_group_a2a_public_events | task_id s(36), sequence i, event_id s(36) uk, kind s(24), body_json j, created_at_ms i；pk(task_id,sequence)，ix(task_id,kind,sequence) |
| bcs_group_a2a_source_positions | task_id s(36), stream_hash b32, stream_key s, sequence i, event_id s(256), generation i；pk(task_id,stream_hash)；比较完整stream避免hash碰撞；同stream单调推进、乱序先补缺口再推进 |
| bcs_group_a2a_execution_intents | task_id s(36) pk, execution_id s(36) uk, generation i, phase s(24), lease_owner s(128)?, lease_token i, lease_until_ms i?, next_check_at_ms i, attempts i, last_error_code s(64)?, start_barrier_crossed bool, attempted_at_ms i?, dispatch_owner s(128)?, dispatch_token i?, permit_id s(36)?, permit_consumed bool, acknowledged bool, deployment s(128)；ix(deployment,next_check_at_ms,task_id) |
| bcs_group_a2a_execution_work | task_id s(36), work_id s(256), generation i, kind s(24), state s(24), proof_id s(256)?, correlation_json j, source_boundary_json j?；pk(task_id,work_id)，ix(task_id,state,work_id)；不存凭据 |
| bcs_group_a2a_capacity | deployment s(128), counter_key b32, used i；pk(deployment,counter_key)，CHECK used>=0；caller key与deployment全局key用不同前缀，不是Group grant |
| bcs_group_a2a_publications | deployment s(128), group_id s(128), desired_revision i, confirmed_revision i?, lease_owner s(128)?, lease_token i, lease_until_ms i?, next_check_at_ms i, attempts i, uncertain bool, last_error_code s(64)?；pk(deployment,group_id)，ix(deployment,next_check_at_ms,group_id) |
| bcs_group_a2a_maintenance_cursors | deployment s(128), job s(64), cursor s(256)?, revision i, updated_at_ms i；pk(deployment,job)；只存有界目录核对/purge进度，不每次启动全库扫描 |

所有按Task关联的子表必须通过已scope限定的Task或固定deployment的JOIN访问；expiry只扫当前deployment。活结果索引把 result_deleted_at_ms IS NULL 作为等值前缀，避免长期保留墓碑进入page/COUNT扫描；真实MySQL以大量旧墓碑验证EXPLAIN和rows examined，不能只声明索引存在。

Task额外索引：

```sql
CREATE INDEX ix_a2a_tasks_scope_status_time
  ON bcs_group_a2a_tasks(deployment, scope_hash, result_deleted_at_ms, state, status_timestamp_ms, task_id);
CREATE INDEX ix_a2a_tasks_scope_time
  ON bcs_group_a2a_tasks(deployment, scope_hash, result_deleted_at_ms, status_timestamp_ms, task_id);
CREATE INDEX ix_a2a_tasks_context_time
  ON bcs_group_a2a_tasks(deployment, scope_hash, result_deleted_at_ms, session_id, status_timestamp_ms, task_id);
CREATE INDEX ix_a2a_tasks_retention
  ON bcs_group_a2a_tasks(deployment, result_deleted_at_ms, expires_at_ms, task_id);
```

这些短摘要索引避免caller完整复合key超过MySQL3072字节；查询先通过hash缩小范围，再逐字段精确匹配完整scope。Query示例中完整scope条件保留，同时增加scope_hash谓词。COUNT与page使用同一条件。

## 3. 迁移实现片段与测试要求

SQLite新增表可用如下确定模式；MySQL对应purpose／JSON／binary类型调整，不复用SQLite语句直接执行：

```sql
CREATE TABLE bcs_group_a2a_capacity (
  deployment TEXT NOT NULL COLLATE BINARY,
  counter_key BLOB NOT NULL,
  used INTEGER NOT NULL CHECK (used >= 0),
  PRIMARY KEY (deployment, counter_key)
);
CREATE TABLE bcs_group_a2a_artifacts (
  task_id TEXT NOT NULL COLLATE BINARY,
  artifact_id TEXT NOT NULL COLLATE BINARY,
  context_id TEXT NOT NULL COLLATE BINARY,
  position INTEGER NOT NULL CHECK (position >= 0),
  name TEXT, description TEXT, parts_json TEXT NOT NULL,
  metadata_json TEXT, extensions_json TEXT, created_at_ms INTEGER NOT NULL,
  PRIMARY KEY (task_id, artifact_id), UNIQUE (task_id, position)
);
```

各字段清单逐表转DDL，schema-test断言PRAGMA/INFORMATION_SCHEMA列与索引、默认值及唯一性；SQLite存储层验证JSON、byte长度和enum。禁止cascade清除reservation／recovery／publication墓碑。子表引用关系在组合写中强制，物理FK是否启用保持现有方言策略并通过一致性测试，不能靠可选FK替代Store校验。

## 4. 锁序与事务断言

固定锁序：Group/config revision → deployment/caller capacity（counter_key排序）→ context → Task → reservation／intent／events。仅查询或清理未必需所有锁，但涉及同一对象时不反序。MySQL deadlock可以有界重试**整个尚未提交事务**；绝不重试已经可能派发的业务动作。

- 新migration同时创建与登记SQLite/MySQL独立SQL、更新版本断言，完整迁移chain回归；不得把WHERE逻辑改成复用既有出站表。
- deployment启动：同映射可多实例，另一deployment/env同数据源拒绝；后台claim/expiry/token/ack不能读写外部scope。
- admit新context：插入session后、reservation后、commit前分别故障，全表无残留且capacity不增。
- duplicate：完整键＋指纹相同直接返回原Task，不加capacity、不加generation，support关闭也可命中。
- begin_dispatch：与取消同Task锁序，owner/token/generation/revision/lease有效且未越barrier才CAS一次写attempted_at+permit；consume_dispatch把permit与根work/queue项原子关联。renew不改变generation、不延长执行预算，不续已过期lease。
- terminal：Task+Artifact+public terminal+Session CAS+context+capacity一起提交；failure/unknown不释放capacity。
- release isolation：证明所有work终结，更新Session对应activation和context/counter；原Task仍FAILED。
- expiry：标逻辑删除＋reservation.result_expired同事务；未完成／隔离／续聊依赖跳过；purge只删大内容，不删最小所有权和去重键。
- publication：资格变化与desired_revision同事务；ack用lease_token+revision条件；旧owner不能标最新状态已同步。

ExecutionSnapshot的context/opening_message/routing_policy（带默认值）和definition/bindings/output一起生成config_hash；label-only不改变该hash。a2a_output_json是管理写入真源，execution_json只是派生快照。

Runtime既有Run表不强行改外部ID格式：A2A execution_id→内部Run ID的唯一映射保存在execution_intents/work中，T10的创建逻辑通过组合fragment确保“Run创建+映射”原子。如果内部Run ID可直接接受预分配ID则复用它，不再引入第二套无作用映射。
