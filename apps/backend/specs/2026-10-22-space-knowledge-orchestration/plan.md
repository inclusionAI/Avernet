# Plan: Isolate Space knowledge membership persistence

## Approach

通过一个 Space 持久化内部事务扩展隔离快照副作用。默认空实现不认识同步表；启用的实现复用现有快照/身份算法。保持所有事实写入同事务，不增加事件总线、表或通用框架。先用创建/成员写入 pilot 证明缺表隔离，再迁移维护与审批入口。

用户已批准并开始实施；下列类型/文件已在本地实现，最终验证与远端交付边界见 tasks.md / verification.md。

## Affected Components

- Space/Space Management writer：只依赖事务扩展，不直接依赖知识持久化。
- Work Order manual/AUTO writer：保留既有审批与通知语义，同事务调用扩展。
- Skill Space 创建 seam：只调用中性身份扩展，不改 Skill 业务。
- Knowledge producer：保留捕获/确认算法，优化候选定位及元数据读取。
- Composition root：显式选择空/持久记录实现，保留 producer/配置检查，表结构问题延迟到实际 SQL；历史不再决定应用能否启动。

## Data Model Changes

None. 不修改源表、同步表和共享队列 schema；不增加索引或新表。原同步表 DDL 只对启用记录能力的部署强制。

## API / Interface Changes

外部 HTTP、知识 Provider/Sink 和快照 wire contract：None.

```python
# apps/backend/src/agentclaw/community/core/repository/protocols/space_persistence_effects.py (new)
class SpacePersistenceEffects(Protocol):
    def lock_space(self, session: Session, *, space_id: int,
                   environment: str) -> SpaceModel | None: ...
    def record_membership(self, session: Session, space: SpaceModel,
                          *, disabled: bool = False) -> None: ...
    def reserve_identity(self, session: Session, space: SpaceModel) -> None: ...
```

这是 Repository 内部 contract，刻意共享调用者 ORM transaction，不属于 Service API 或远端 Plugin API。`None` 只表示源 Space 不存在。公共 API 不传 Session，不新建隐藏事务。

```python
# apps/backend/src/agentclaw/community/di/space_knowledge_capture_config.py (new)
@dataclass(frozen=True)
class SpaceKnowledgeCaptureConfig:
    enabled: bool = False
```

```yaml
# user_config: strictly validated optional block
space_knowledge_capture:
  enabled: false
```

缺省为 false；拒绝未知键和非 bool。Sink.is_enabled 继续只控制投递。公共配置在专用小模块定义，避免扩大现有 config.py。

## Key Files & Functions

```python
# apps/backend/src/agentclaw/community/core/repository/implementations/spaces/source_lock.py (new)
def lock_source_space(session: Session, *, space_id: int,
                      environment: str, sqlite_write_guard: bool) -> SpaceModel | None: ...
```

共享源锁不导入知识模型。空扩展使用原有通用行锁语义；持久记录与首次激活使用 SQLite write guard，保留跨进程竞态保护。选择来自已装配实例，不在 writer 内判断环境。

```python
# apps/backend/src/agentclaw/community/core/repository/implementations/spaces/persistence_effects.py (new)
class NoKnowledgeSpacePersistenceEffects:
    # record_membership/reserve_identity: no-op; no knowledge imports or SQL
    # lock_space: delegates to neutral source lock without knowledge write guard
    ...

# apps/backend/src/agentclaw/community/core/repository/implementations/spaces/knowledge_effects.py (new)
class KnowledgeSpacePersistenceEffects:
    # Same-session snapshot capture + retained-identity protection; app is injected
    ...
```

所有受影响 writer 的 constructor 注入必需 contract。手工构造调用方需显式选择实现，不用 Optional/默认 fallback 隐藏遗漏装配。

```diff
# apps/backend/src/agentclaw/community/core/repository/implementations/spaces/space.py:44
- def __init__(self, db: DatabasePlugin) -> None:
+ def __init__(self, db: DatabasePlugin, effects: SpacePersistenceEffects) -> None:
# Same migration: spaces/management.py:28, work_orders/work_order.py:83,
# skill_center/space_skill.py:68; AUTO mixin uses its owning repository's effects.
- record_membership_snapshot(session, space)
+ self._effects.record_membership(session, space)
```

其他方法同样替换直接 helper 调用；不移动权限校验，不重新定义 Skill Grant/Owner。原 identity helper 可留给知识实现私用，删除业务 writer 对它的直接导入。

```python
# apps/backend/src/agentclaw/community/di/modules/space_knowledge_module.py:40 (modify)
# Strict config + composition root selects exactly one SpacePersistenceEffects.
# enabled=False -> NoKnowledgeSpacePersistenceEffects
# enabled=True  -> KnowledgeSpacePersistenceEffects
```

```python
# apps/backend/src/agentclaw/community/core/space_knowledge/capture_lifecycle.py (new)
# Startup validates configuration only, with no DB session or schema reflection.
# disabled -> ordinary no-op effects, regardless of outbox schema/history
# enabled -> actual SQL uses the existing transaction; DB errors propagate
# No explicit schema preflight; producer still rejects disabled capture
```

2026-10-27 后续修订：启动仅验证 capture/Sink 配置，不打开 DB session，不反射同步表结构；普通库调用同样不进行预检。构造函数参数保持兼容，guard 仅保护配置与 producer 启用关系。真实 SQL 留在原 repository/effects 及调用者事务内执行，错误正常传播/回滚，不降级为空实现。其他 DB bootstrap 行为不变，不能承诺其他基础设施错误不阻断启动。

```python
# apps/backend/src/agentclaw/community/di/container.py:271 (modify)
# Eagerly validate typed capture configuration on every profile (no DB I/O).
# Existing spaces_module.py / space_skill_repository_bindings.py retain contracts;
# wire effects once and verify WorkOrdersModule shares the same singleton policy.
```

```python
# apps/backend/src/agentclaw/community/core/repository/implementations/spaces/knowledge_sync.py:249
# At most 32 indexed seeks for candidate heads + one exhaustion check:
# env/app/acknowledged=false AND space_id > cursor
# ORDER BY space_id, revision LIMIT 1
# cursor := returned space_id (skip that space's remaining revisions)
```

不要先 DISTINCT/GROUP BY 全部 pending，再声称 LIMIT 是扫描边界。仍校验最终投递必须是 acknowledged+1；不改变 hash/版本/历史。

```python
# apps/backend/src/agentclaw/community/core/repository/implementations/spaces/knowledge_sync.py:41-53,197-225
# progress/latest acknowledgement -> scalar projections only
# next_snapshot/delivery -> fetch exact next row payload once
# repair_pending -> discover scalar head pointer, never load full snapshots to enqueue
```

更新相应 Protocol 方法和 membership_sync.py:89-118；有损坏/缺失版本仍由精确队头读取拒绝，不能假定最小 pending 就一定是可投递版本。

### Group A review correction

```python
# core/space_knowledge/capture_guard.py (new internal protocol)
class SpaceKnowledgeCaptureGuard(Protocol):
    def require_enabled(self, session: Session) -> None: ...
```

同一个 lifecycle 对象也提供 producer guard。`SpaceKnowledgeSyncRepository` 必需注入它，在 activation/读取待投递快照/ack 等既有事务内验证，再进行持久化或允许 Sink 调用。否则无 lifespan 的调用方可以绕过 source writer 的验证。已补真实 DI producer RED→GREEN 和直接 repository bypass 回归；不改变外部协议。

## Dependencies

None. 不增加包、数据库服务、事件总线或共享队列改造。

## Risks & Mitigations

- **Risk:** 将暂停投递误当关闭记录。**Mitigation:** 两个独立状态；暂停投递仍应保持记录开启。2026-10-27 起历史不再阻断应用启动，明确关闭记录的历史缺口需由发布流程控制；暂停重启回归。
- **Risk:** 多实例滚动期间有 writer 仍为空扩展。**Mitigation:** 首次激活前所有 writer 统一启用记录装配；禁止混合策略下激活。不能只升级 API 实例而遗漏 worker/其他写入者。
- **Risk:** 初始化顺序或 lifecycle discovery 吞错误。**Mitigation:** 配置 eager 校验、实际 lifespan 测试、校验完成前写入保护；不在 DI 构造期查库。
- **Risk:** 空策略被用于真实同步 producer。**Mitigation:** 完整 DI 合同验证 producer/sync 与记录策略一致；默认 Sync/Provider 仍 unavailable。
- **Risk:** 更新手工 constructor 的测试掩盖故障。**Mitigation:** 原有业务测试明确空策略，真实同步测试明确持久策略；缺表 HTTP 测试使用完整 DI，不只手工构造。
- **Risk:** 索引 seek 降低单条 SQL 工作但增加往返。**Mitigation:** 上限 33 次、测量深积压及多 Space；MySQL/OceanBase 执行计划单独验证，不凭 SQLite 结论宣称远端通过。

## Alternatives Considered

- 只修 SQL、强制所有部署迁移：违反强制缺表可用要求。
- 事务提交后异步捕获/吞异常：可能丢撤权事件，拒绝。
- 按 Sink 状态跳过记录：暂停投递时丢历史，拒绝。
- 新通用事件总线/UoW/进度表：超出本次有限收口范围，拒绝。

## Rollout

```text
从未启用：保持默认 capture=false，不要求同步 DDL，先验证缺表完整启动及原功能。
首次启用：执行原同步表迁移 → 所有 writer 统一 capture=true → 验证装配/worker → 开放首次激活。
暂停投递：保持 capture=true，只暂停 Sink；继续保留 pending 与 retired 历史。
回滚：历史存在不再阻断显式空策略的启动/普通写入；为避免漏记撤权，应保持记录或受控暂停相关写入，绝不删除历史。同步 producer 仍受 require_enabled 保护。
```

不执行线上操作。原 contract.md 的全局 DDL 前置说法在本隔离变更完成后被限定为启用记录的部署；本地验证结果不等于远端 PR 或部署已经切换。

## Test Strategy

```python
# apps/backend/tests/community/core/space_knowledge/test_capture_isolation.py (new)
def test_default_graph_legacy_operations_persist_without_outbox_table(): ...
def test_disabled_capture_never_queries_outbox_or_uses_knowledge_write_guard(): ...
def test_retained_history_does_not_block_disabled_capture_startup(): ...
def test_paused_sink_keeps_ordered_membership_history_across_restart(): ...

# apps/backend/tests/community/adapters/http/space_knowledge/test_capture_isolation_http.py (new)
def test_full_lifespan_and_legacy_http_work_without_knowledge_schema(): ...

# apps/backend/tests/community/core/space_knowledge/test_pending_scan_bounds.py (new)
def test_deep_pending_history_has_bounded_candidate_work(): ...
def test_dispatch_progress_does_not_fetch_payload(): ...

# apps/backend/tests/community/architecture/test_space_persistence_boundary.py (new)
def test_legacy_writers_do_not_import_knowledge_persistence(): ...
```

参数化覆盖 spec AC1 的所有业务入口；HTTP 保留已有身份/权限依赖和响应 envelope。补真实 DB AUTO Service 容量失败断言，区别 repository rollback 与 Service 后续 FAILED/通知。保留真实 file-SQLite、跨连接/进程竞态与原有身份生命周期测试。测试产物不能替代统一 Singlebox、企业 adapter/DI、远端数据库及部署证据。

### Group C implementation notes

Candidate discovery uses at most 32 seeks (no extra exhaustion seek needed).
Metadata dispatch performs up to two scalar progress reads per Space, rejects
missing predecessors and skips stale candidates. Payload/hash validation moves
to execution for corrupt-but-present heads; worker failure remains Retry with no
Sink call or acknowledgement. Ack verifies the delivered full_snapshot envelope,
status and canonical hash against a locked immutable row without a second payload
read. No-op source equality retains its full-payload read.

Repository architecture gates require @abstractmethod, type-only domain imports
and declared implementation bases also for this internal contract; these are
implemented without exemptions. The lifecycle declares both internal contracts.
