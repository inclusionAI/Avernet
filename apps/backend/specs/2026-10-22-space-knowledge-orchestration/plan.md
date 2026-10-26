# Plan: Isolate Space knowledge membership persistence

## Approach

通过一个 Space 持久化内部事务扩展隔离快照副作用。默认空实现不认识同步表；启用的实现复用现有快照/身份算法。保持所有事实写入同事务，不增加事件总线、表或通用框架。先用创建/成员写入 pilot 证明缺表隔离，再迁移维护与审批入口。

本方案等待 tasks 评审后实施；下列类型和文件是拟新增/修改内容，不是已实现接口。

## Affected Components

- Space/Space Management writer：只依赖事务扩展，不直接依赖知识持久化。
- Work Order manual/AUTO writer：保留既有审批与通知语义，同事务调用扩展。
- Skill Space 创建 seam：只调用中性身份扩展，不改 Skill 业务。
- Knowledge producer：保留捕获/确认算法，优化候选定位及元数据读取。
- Composition root：显式选择空/持久记录实现，并拒绝有历史时误关闭记录。

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
# user_config: proposed strictly validated optional block
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
# After Database bootstrap, before serving requests/allowing source writes:
# disabled + missing table -> safe no-op, no SQL against the absent table
# disabled + table exists + same-environment history (any app) -> reject unsafe downgrade
# enabled -> validate original outbox schema; no silent fallback on errors
```

启动验证使用 DatabasePlugin 的 session/bind；不得窥探 CommunityDatabase 私有 engine。DI configure/constructor 不做 DB I/O。必须保证验证异常被正常 lifespan 传播，不靠会吞 provider 异常的 lifecycle discovery；校验未完成前不得放行可能丢事件的源写入。沿用公共 Lifecycle，不修改它的阶段语义。若 startup 使用者涉及提前写入，先验证这一顺序，不以默认已就绪绕过。实现采用同一个幂等 validation guard：正常 startup 在 DB bootstrap 后验证，startup writer/库调用在首个受保护事务内验证，失败在外部副作用或提交前传播，不以已就绪默认值跳过。

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

## Dependencies

None. 不增加包、数据库服务、事件总线或共享队列改造。

## Risks & Mitigations

- **Risk:** 将暂停投递误当关闭记录。**Mitigation:** 两个独立状态；有历史时拒绝关闭记录；暂停重启回归。
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
回滚：有历史时不得切空策略；按受控回滚流程暂停相关写入，绝不删除历史来绕过检查。
```

不执行线上操作。原 contract.md 的全局 DDL 前置说法在本隔离变更完成后被限定为启用记录的部署；修复尚未完成时不能将新规格当成当前行为。

## Test Strategy

```python
# apps/backend/tests/community/core/space_knowledge/test_capture_isolation.py (new)
def test_default_graph_legacy_operations_persist_without_outbox_table(): ...
def test_disabled_capture_never_queries_outbox_or_uses_knowledge_write_guard(): ...
def test_retained_history_rejects_disabling_capture(): ...
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
