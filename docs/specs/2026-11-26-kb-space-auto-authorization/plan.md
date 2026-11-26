# Plan: 知识库按空间自动授权 Bot

## Approach
采用“权威 Bot 事实可靠投递，知识提供方本地计算有效权限”。Avernet 不保存第二套知识授权，也不向每个 Bot×知识库批量写人工授权；提供方用入驻区间、开关区间与稀疏人工选择表达资格。沿用现有任务队列和生命周期补偿模式，新增独立 Bot 事实合同，不能把人员成员快照当作 Bot 名册。

本方案是待实施设计，所有新增字段、表和接口均未实现。交付分为 Avernet、ECB、企业工作台/装配三部分；单独完成 Avernet 不构成功能交付。公开仓库仅放提供方中立合同和公共实现；企业认证、连接配置及现场数据留在宿主仓库或私有验证记录。

## Affected Components
下列路径相对 Avernet；行号基于本次分支基线 `e1eebb459d`。

| Current source | Evidence / impact |
| --- | --- |
| `apps/backend/src/agentclaw/community/core/repository/implementations/bot/initial_layout.py:30` | `insert` 和带初始布局的创建均需同事务捕获。 |
| `apps/backend/src/agentclaw/community/core/repository/implementations/bot/mutations.py:82` | 普通移动入口；同文件 `:199` 是软删除入口。 |
| `apps/backend/src/agentclaw/community/core/repository/implementations/bot/owner_handoff.py:306` | 转交最终事务内同时变更 Owner 与可能的空间；必须独立接入。 |
| `apps/backend/src/agentclaw/community/core/repository/implementations/bot/recycle.py:177` | 回收终结事务需检查是否还有绕过普通删除的写路径。 |
| `apps/backend/src/agentclaw/community/core/space_knowledge/membership_tasks.py:53` | 可复用队列补偿和生命周期模式，保留人员流的独立修订/确认。 |
| `apps/backend/src/agentclaw/community/core/knowledge_management/services/service.py:158` | 已授权列表要求物理行 ID、正数 revision；需新增有效授权响应模式。 |
| `apps/backend/src/agentclaw/community/adapters/knowledge_platform/grant_state_reader.py:10` | 当前拒绝 granted=true/revision=0；不能只删校验而不升级合同。 |
| `apps/backend/src/agentclaw/community/adapters/knowledge_platform/catalog.py:266` | 授权列表、状态、目录和写操作的传输适配。 |
| `apps/backend/src/agentclaw/community/adapters/http/knowledge_management/router.py:164` | 管理入口及新策略接口；业务校验留在服务/提供方。 |

现有边界：[知识管理](../../../apps/backend/src/agentclaw/community/core/knowledge_management/README.md)、[Bot 知识选择](../../../apps/backend/src/agentclaw/community/core/bot_knowledge/README.md)、[人员同步](../../../apps/backend/src/agentclaw/community/core/space_knowledge/README.md)、[架构规则](../../arch/arch.rules.md)、[协议测试](../../arch/protocol-contract-tests.md)。本次不改变 enabled、原生发布或人员权限的所有权。

宿主调查发现：ECB 开发分支仍以显式 Bot authorization store 判断访问；宿主 dev 与 ECB 分支目录布局不同，不能拿宿主 dev 的旧 ECB 副本实施。ECB 在 `0731_ecb_dev` 基线上开发，工作台/企业装配在宿主 `dev` 基线上开发；实施前分别更新并记录精确基线、读取当地规则，独立 worktree 避免切换现有工作目录。公共分支已创建，不覆盖用户未跟踪资料。

## Data Model Changes
以下为合同草图，SQL 类型和索引须对应公共 SQLite/PostgreSQL 与企业 MySQL 模式迁移；不执行生产 DDL。

```python
# apps/backend/src/agentclaw/community/kernel/knowledge_bot_membership.py (new)
@dataclass(frozen=True)
class KnowledgeBotFact:
    subject_id: str       # 持久化实例标识；不由 owner_id 或显示名称构造
    entity_id: str        # 现有稳定资源身份，仍与 bot_id、env 联合限定
    bot_id: str
    owner_id: str         # 可变化；不是执行主体或权限证明
    environment: str
    space_id: int | None  # 删除/无有效空间时允许为空，不能自动放行
    incarnation: str     # 同名重建不能复用旧实例
    residency_id: str    # 同空间转交不变，真实迁入重新生成
    revision: int        # 本实例单调递增
    deleted: bool
```

Bot 当前事实与不可变 outbox 分开：当前行按本地 Bot PK 和环境唯一；保留 tombstone 与 incarnation，避免 SQLite PK 重用导致旧权限复活。未知/歧义历史映射不生成推测身份。legacy NULL space 仅解析既有有效个人空间；NULL 归一化到同一有效空间不产生新入驻。

```sql
-- apps/backend/sql/2026_11_26_knowledge_bot_sync.sql (new; logical keys)
-- current: subject_id PK, UNIQUE(env, bot_pk), incarnation, residency_id,
--          entity_id, bot_id, owner_id, space_id, revision, deleted
-- outbox: event_id PK, subject_id, bot_revision, space_id, space_sequence,
--         kind, payload, payload_hash, acknowledged_at, last_error
-- UNIQUE(subject_id, bot_revision, space_id, kind)
-- UNIQUE(env, space_id, space_sequence)
-- index pending acknowledgements + environment/space/sequence
-- stream: PRIMARY KEY(env, space_id), next_sequence, initialization_status
```

提供方增加可信 Bot 投影、入驻历史区间、每库策略及策略 ON 区间、当前入驻人工选择。只保存实际人工选择，不为自动获权者创建 grant/viewer 笛卡尔积。人工选择唯一键为 KB + subject + residency；Owner 改变不会重新分配此键。旧人工记录仅在当前可信实例/空间可唯一匹配时绑定到初始入驻，历史 false 同样保留；迁出后旧记录不参加新入驻计算，来源不明的旧 true 不自动恢复。

```python
# Provider effective-authorization contract (new; implementation owned by provider)
eligible = live_subject and same_space and trusted_identity
automatic = overlaps(residency_interval, kb_on_intervals)
choice = manual_choice(kb_id, subject_id, residency_id)
granted = eligible and (choice.value if choice.exists else automatic)
# 原生 READ 与知识库可消费性仍是实际访问的附加必要条件。
# OFF 关闭 ON 区间，已有交集保留；新入驻无交集则不获权。
# 人工 false 覆盖自动资格；Owner 变化不改变 residency_interval。
```

### Ordering and policy boundaries
开关与 Bot 入驻必须共享可证明的顺序，不能拿接收时间或两机墙钟推断“ON 期间新建”。每空间 outbox 序列包含 Bot 事实和策略屏障命令；创建/迁移/删除与该序列同事务提交。迁移产生旧空间退出、新空间进入记录，并携带同一递增 Bot revision；提供方旧空间记录只结束相应入驻，不能覆盖更高 revision 的当前事实。

开关请求先通过提供方创建者校验，再以 request_id 和 expected_policy_revision 持久记录策略命令。提供方按空间顺序应用命令并再次校验权限及 CAS；失败记录明确错误，命令不会静默变成成功。ON/OFF 区间端点取命令序列；此前入驻必须先被应用。接口返回 PENDING 与 command_id 不等同于策略已生效；只有回查 APPLIED 才显示完成。重复 request_id 内容不一致拒绝；重复应用不重复动态。

初始化先启用所有权威写入口的捕获，再以逐 Bot 锁内重读建立初始事实；完成屏障前不能启用自动策略。并发迁移/删除由实例 revision 与空间序列保护，回填不能用锁外旧快照覆盖新事实。初始化进度可恢复，不在普通读取中执行全量初始化。

## API Changes
保留旧显式授权合同作为独立兼容模式；新界面与消费者显式使用有效授权版本，不用旧 grant OR 新结果兜底。接口路径草图相对已有知识管理前缀，不包含部署地址。

```ts
// apps/backend/.../adapters/http/knowledge_management/schemas.py (wire sketch)
type EffectiveGrant = {
  subject_id: string; residency_id: string;
  bot_id: string; owner_id: string; granted: boolean;
  source: 'manual' | 'automatic' | 'none';
  choice_revision: number; // 没有人工选择时为 0；不冒充 effective revision
  effective_version: string; // 投影、策略、入驻及人工版本的不可混用令牌
  grant_id: string | null; updated_by: string | null;
  updated_at: string | null; // 自动资格不伪造人工历史
};
```

```jsonc
// Proposed BFF policy contract: .../spaces/{space}/knowledge-bases/{kb}/bot-policy
// GET: creator capability and actual provider state
{"enabled":false,"policy_revision":0,"can_update":true,"sync_status":"APPLIED"}
// PUT: creator-only; return 202 while durable command awaits application
{"enabled":true,"expected_policy_revision":0,"request_id":"opaque-client-id"}
// response / command status
{"command_id":"opaque","sync_status":"PENDING","policy_revision":0}
// manual choice v2: stale residency or effective version returns conflict
{"subject_id":"opaque","residency_id":"opaque","granted":false,
 "expected_choice_revision":0,"expected_effective_version":"opaque"}
```

分页使用提供方有效主体/目录索引，不以可空 grant_id 作为游标。新增 v2 opaque string cursor，绑定 KB/空间、查询版本与排序键；过期游标明确返回刷新错误，不把变化中的两页拼成完整快照。每页最多 20，批量状态一次最多 20；BFF 只批量补当页展示信息。列表 total 与同一有效策略查询计算；不得在前端遍历 Bot 逐个授权。旧 int cursor 路径在兼容期保留，但不能用于新自动策略显示。

```python
# apps/backend/src/agentclaw/community/core/knowledge_management/ports.py (additive)
def read_bot_policy(self, *, space_id, kb_id, actor_id) -> dict: ...
def validate_bot_policy(self, *, space_id, kb_id, actor_id, command) -> dict: ...
def effective_bots(self, *, space_id, kb_id, actor_id, cursor, limit) -> dict: ...
def effective_states(self, *, space_id, kb_id, actor_id, subjects) -> dict: ...
def set_choice(self, *, space_id, kb_id, actor_id, command) -> dict: ...
```

同一提供方策略必须接入：点查、批量过滤、已授权列表/数量、Bot 可用目录、启用前检查、draft/verify/online 目录及 MCP/内容请求。可信执行主体由现有认证链提供，不能用 Owner ID、非可信 Header 或显示字段替代。原生人员 READ 与 Bot 资格分开判断；人员/非 TC 调用保持原生流程。

## Key Files
新增文件的具体模块边界见下表；修改来源位置见 Affected Components，实施前再次核对移动后的行号。

| Work unit | Files / ownership |
| --- | --- |
| Public contract | `kernel/knowledge_bot_membership.py`、`plugin_api/knowledge_bot_membership.py`、`core/knowledge_management/ports.py` |
| Durable capture | `core/knowledge_bot_sync/{README.md,models.py,repository.py,capture.py}`、`core/repository/protocols/bot/knowledge_effects.py`、`sql/2026_11_26_knowledge_bot_sync.sql` |
| All writers | `core/repository/implementations/bot/{initial_layout.py,mutations.py,owner_handoff.py,recycle.py}`、`core/repository/implementations/spaces/space.py`（有效个人空间创建/退休的必要影响） |
| Delivery/bootstrap | `core/knowledge_bot_sync/{delivery.py,tasks.py,bootstrap.py}`、`di/modules/knowledge_bot_sync_module.py`、`configs/` 中既有 user-config 装配 |
| BFF/provider adapter | `adapters/knowledge_platform/{catalog.py,grant_state_reader.py,bot_policy.py}`、`adapters/http/knowledge_management/{router.py,schemas.py,errors.py}`、`core/knowledge_management/services/service.py` |
| Consumers | `core/bot_knowledge/{service.py,stage_selection.py}`、`core/knowledge_management/services/conversation.py`、`api/knowledge_management_service.py`；若公共接口纯透传，使用合同测试证明无需改动 |
| Knowledge | 新模块 README、既有知识管理/Bot 知识 README、Backend README 导航、`verification.md` |

表中 `core/`、`kernel/`、`api/`、`di/`、`plugin_api/`、`adapters/`、`configs/` 均相对 `apps/backend/src/agentclaw/community/`；`sql/` 相对 `apps/backend/`。

宿主文件分别在正确基线定位：ECB 的 knowledge_tenant Bot authorization/catalog/store、实际内容权限门禁与 router/schema；工作台 `internal/knowledge/components/AuthorizedBotsPanel.tsx`、授权对话框、`domain.ts` 和管理 service/controller；企业 database adapter、身份 resolver、DI 及 Avernet gitlink。不要将这些私有实现复制进公开仓库。

## Alternatives Considered
- 每个 Bot×KB 写自动 grant：放大数据与活动量，掩盖来源，不采用。
- 当前同空间且开关 ON 即获权：关闭会丢资格，不满足 AC05，不采用。
- 将 Owner 纳入例外主键：同空间转交重置人工取消，不采用。
- 仅发写后事件/定时扫描当前态：崩溃丢事件且看不到迁出迁回，不采用；采用同事务 outbox 与入驻历史。
- 仅按提供方收到事件的时间计算开关区间：网络延迟会改变结果，不采用；采用空间序列与策略屏障。代价是开关可能异步完成，需要命令状态和失败恢复。
- 扩充现有人员整表快照：职责、限额和修订语义不同；复用队列/生命周期模式，不改变现有人员合同。

## Migration / Rollout
1. 先交付 provider v2 合同及默认关闭的存储能力，再部署公共/企业捕获代码和 DDL；所有写进程切换到真实事务和 capture 后才初始化。
2. 企业普通 orm_session 的 AUTOCOMMIT 不能承担该写单元。使用既有 transactional_orm_session，源变更、当前事实、空间序列及 outbox 一起提交；事务失败必须抛错且全部回滚。源码观察不代替真实驱动测试。
3. 回填可唯一解析的既有 Bot/人工选择并完成同步屏障；所有库仍 OFF。歧义数据生成定向报告并拒绝自动复活，不全量清 false。
4. 提供方与全部消费者先支持有效判断，再启用界面；迁移/撤权保护不能靠旧路径 OR 兜底。版本能力不足时显示不可用，不返回空列表伪装成功。
5. 回滚首先关闭新增策略命令入口，保持事实捕获与有效权限执行。不能通过全库 OFF 恢复旧语义（OFF 保留资格），不能删 tombstone/outbox 或退回 grant-only 放行；无法保留安全链路时停用受影响消费。
6. 生产 DDL、初始化、部署和 gitlink 发布单独授权。本阶段只写迁移文件和受控验证方案；不执行线上操作。

## Test Strategy
先按行为补失败测试，再实现。测试必须覆盖真实持久化/消费者调用，不能只验证 DTO 或 mock 自己的方法。

```python
# apps/backend/tests/community/core/knowledge_bot_sync/test_capture.py (new)
def test_source_and_outbox_rollback_together(): ...
def test_same_space_handoff_preserves_residency(): ...
def test_move_out_and_back_rotates_residency_even_before_delivery(): ...
def test_delete_recreate_never_reuses_subject(): ...
def test_bootstrap_reloads_under_lock_and_never_overwrites_delete(): ...
def test_policy_barrier_orders_before_and_after_arrivals(): ...
# apps/backend/tests/community/core/knowledge_bot_sync/test_delivery.py (new)
def test_crash_after_apply_before_ack_is_idempotent(): ...
def test_stale_ack_and_reordered_moves_do_not_restore_old_space(): ...
```

提供方测试独立覆盖 ON/OFF 区间交集、false 优先、稀疏 grant、同空间 Owner 承接、原生 READ、策略创建者权限、活动幂等、版本冲突和分页。企业驱动测试注入第二写失败，检查 Bot 与 outbox 同回滚，并验证真实隔离级别、行锁与提交；公共 SQLite 结果不能代替该证据。

```bash
# apps/backend/ — 既有回归；新增 suite 在实施时加入同一次运行
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/space_knowledge tests/community/core/bot_knowledge \
  tests/community/endpoints/test_knowledge_management.py \
  tests/community/endpoints/test_bot_knowledge_router.py \
  tests/community/contracts/test_knowledge_catalog.py \
  tests/community/architecture
```

增加 `tests/community/contracts/test_knowledge_bot_membership.py` 的消费者调用证明、新增模块边界注册及 DI 完整装配测试；HTTP 变更另跑 Backend README 的 fast gate。受控组合场景逐项覆盖 spec AC01–AC12，保存运行版本与结果；至少区分公共测试、企业驱动、工作台、实际内容访问、发布/部署状态。未运行项标 UNVERIFIED，不能仅用 HTTP 200 或 grant 行存在表示通过。
