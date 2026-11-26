# Plan: 知识库按空间自动授权 Bot

## Approach
TC 感知 Bot 新建、转入、移出、删除和 Owner 转交，可靠调用 ECB 的授权/撤权能力。自动操作写入现有实际 grant；ECB 在现有 KB ext 保存每库开关，继续负责实际授权、鉴权和更新记录。列表、数量、状态、分页和内容访问继续读取现有 grant，不另算继承资格。

本次按评审意见替换旧方案。删除 Bot 归属投影、入驻历史接收、区间计算、effective v2、独立游标和相关初始化迁移。TC 为可靠执行保留必要的当前操作代次、人工取消标记与待处理意图，这些是执行控制状态，不是第二套访问权限事实；不传给 ECB 建立 Bot 名册。

不预设新增表、认证体系或调度系统。先核对并复用现有 KB ext、Bot 扩展/生命周期记录、任务队列、授权 CAS、服务认证和活动能力；无法表达必要保证时，记录具体缺口并提出最小补充，禁止用“已有队列”冒充可靠捕获已经成立。

## Affected Components
路径相对 Avernet，行号为本分支基线 `e1eebb459d` 的证据，不代表新功能已存在。

| Source | Current behavior and change |
| --- | --- |
| `apps/backend/src/agentclaw/community/core/repository/implementations/bot/initial_layout.py:30` | 两个创建入口需要可靠记录授权工作。 |
| `apps/backend/src/agentclaw/community/core/repository/implementations/bot/mutations.py:82` | 移动入口；同文件 `:199` 为删除入口，需要保留旧授权目标以便撤权。 |
| `apps/backend/src/agentclaw/community/core/repository/implementations/bot/owner_handoff.py:306` | 转交最终事务变化 Owner 和可能的空间，需要保留原 grant 身份及选择状态。 |
| `apps/backend/src/agentclaw/community/core/repository/implementations/bot/recycle.py:177` | 回收终结不得丢失未确认撤权工作。 |
| `apps/backend/src/agentclaw/community/adapters/knowledge_platform/catalog.py:353` | 现有 set_grant 传 actor、Owner 和 expected_revision；优先复用。 |
| `apps/backend/src/agentclaw/community/core/knowledge_management/services/service.py:158` | 列表展示真实授权行，继续沿用 ID 和 revision。 |
| `apps/backend/src/agentclaw/community/adapters/knowledge_platform/grant_state_reader.py:10` | 状态批量读取和 CAS 校验保留，无自动虚拟授权例外。 |
| `apps/backend/src/agentclaw/community/core/task_queue/repository/models.py:129` | active-only 去重只防并发排队，不是永久操作幂等或恢复凭据。 |

边界沿用[知识管理](../../../apps/backend/src/agentclaw/community/core/knowledge_management/README.md)、[Bot 知识选择](../../../apps/backend/src/agentclaw/community/core/bot_knowledge/README.md)及[架构规则](../../arch/arch.rules.md)。现有人员同步维持原职责，不扩展为 Bot 同步通道。

ECB 在宿主 `0731_ecb_dev` 基线上核对 knowledge_tenant 授权 service/store、KB ext 更新、活动及认证入口；工作台/企业装配在宿主 `dev`。实际实现前更新基线并读取当地规则，私有配置和现场资料不进入公共仓库。

## Data Model Changes
ECB 开关存放现有 KB ext，缺字段视为 OFF。下面是建议的局部扩展键；实施时沿用实际 ext 更新/CAS 机制，不覆盖其他字段。版本字段只用于开关并发与旧任务校验，不是新授权模型。

```jsonc
// ECB KnowledgeBaseRegistry.ext — additive key sketch
{
  "bot_auto_authorization": {
    "enabled": false,
    "revision": 0
  }
}
```

实际授权使用现有 grant 行及其 revision。TC 必须能持久区分当前 Bot 实例、当前连续空间归属、人工取消与生命周期撤权：同空间 Owner 转交保留取消；真实迁出再迁回重置本次取消，但旧任务仍失效。仅有一条 false grant 不足以判断取消原因，不能扫描 false 后全部恢复；需要从已确认操作来源及 TC 控制状态判定，未知来源保守保留并报告。

```python
# TC existing lifecycle/task metadata — required semantics, not new table DDL
# Resolve the actual storage seam in Task 1 before implementation.
operation_id: str             # stable across retries
bot_instance: str             # derived from authoritative identity; no name reuse
control_generation: str       # changes on move/delete; not same-space handoff
source_and_target: dict       # exact old/new Bot grant identities and KB scope
expected_grant_revision: int  # captured before dispatch, never blindly refreshed
policy_revision: int         # only applicable to automatic additions
origin: str                  # automatic / manual / lifecycle
progress: dict                # confirmed / pending / failed / outcome_unknown
```

优先复用现有扩展及任务载荷；待处理撤权不得随 Bot 删除或任务历史清理消失。若现有持久载体不满足保留或原子更新，Task 1 给出具体证据和最小改动，不能先创建另一套事实表。无归属投影回填、区间迁移、全量历史 grant 改写或默认批量开放旧库。

## Execution authority
- 修改开关：仍只有 KB 创建者，ECB 后端用可信身份校验；有人工授权权限不等于有改开关权限。
- 人工授权/取消：保持现有 actor 的 KB_MEMBER_MANAGE 和 recipient/native READ 规则及 CAS。
- 自动任务：通过现有 TC→ECB 服务认证和明确的授权执行范围调用。操作发起人、执行主体和 Bot Owner 分开留痕；队列消息、开关 ON、actor_staff_no 字段都不是独立授权凭据，不能把创建者字段填上就视为委托。
- Task 1 必须验证现有服务主体是否能在限定环境、KB/空间范围内执行自动授权，以及在 Owner 离开/转交/删除后执行旧关系撤权。现有 set_grant 会校验 actor 的 KB_MEMBER_MANAGE，不能假定后台天然有权限。
- 如果已有委托/服务权限不足，记录最小执行授权缺口，补充现有能力的范围校验；不得切换为无条件管理员、冒充旧 Owner 或新建认证体系。无权时记录阻塞和恢复条件，权限恢复后重新校验再续作；不能以撤权失败冒充权限已收敛。
- 新授权仍检查接收人及原生 READ，不无条件批量创建 viewer；实际消费继续执行原生鉴权。后台控制调用的权限不转移成人员 READ/WRITE 或管理权限。

## Reliable execution and stale-work protection
1. **可靠捕获。** Bot 权威变更与持久待办/恢复标记必须可靠绑定；优先同一真实事务写入既有载体，队列负责唤醒。企业普通 ORM 的 AUTOCOMMIT 不能提供多语句原子性，复用显式事务能力。写后一次 HTTP/enqueue 不够；只扫最终 Bot 当前态也会漏掉迁出迁回。
2. **开启。** 开关保存成功与授权执行完成分开。可靠发现开关变化并建立可恢复批次，有界分页枚举当前 Bot，再逐项复核；保存开关后、创建任务前崩溃也必须被补偿发现。新增/转入通过生命周期待办接续，不依赖一次性扫描碰巧看到。
3. **关闭。** 只停止未完成的新增自动授权，已有 grant 不撤销、人工取消不清除。撤权清理不受开关 OFF 影响；关闭前已发出、结果未知的授权须先回查再收尾，不能将其计入关闭批次新增数量。重新开启是新批次，不能复活旧任务。
4. **逐次执行。** 发送前校验当前实例、空间、Owner、控制代次、开关版本、取消标记及权限。修改同一 grant 的自动/人工/生命周期操作需要可验证的并发控制；先读后写只解决已知过期，不能挡住读后发生的迁移。
5. **接收端保护。** 复用 grant CAS 作为最终写入保护。旧请求携带原 expected_revision；迁出、删除、取消等使旧授权失效的操作，必须确认撤权或拒绝状态并推进 revision（必要时是 false→false 的屏障），否则无 grant 的迟到写仍可能获权。同名重建/迁回/Owner 身份切换也要处理旧目标键；未知旧请求未收口前不允许其覆盖新选择。若现有接口的同值写不推进 revision，最小补齐现有 mutation 合同并测试，而非仅加发送前检查。
6. **冲突与未知。** 409 后重读当前事实重新决策，不能更新 expected_revision 后原样重试。超时先用现有状态/CAS及操作回执判断结果；单看 granted=true 不能认领是本任务成功。无法确认归属的成功不计数，标待核对。需要回执时复用既有操作元数据/活动能力，缺口在原接口最小补充，不新增授权读模型。
7. **恢复。** 按当前待处理意图补队列；保留撤权目标、执行版本和确认结果直到收敛。重试不重复计数/活动，失败任务可恢复；active-only 队列去重不是持久幂等。跨进程并发、任务过期、旧 worker 延迟返回及任务清理都需故障注入验证。
8. **Owner 转交。** 同空间承接人工 true/false、自动 true 和 OFF 后保留 true，按现有 owner-keyed grant 做受控迁移并撤销旧键，不靠新 Owner 查不到行就自动授权。迁移保留授权来源/取消语义，新主体仍过原生 READ；跨空间先清旧关系，再按目标库开关处理。部分成功留可恢复进度，不能伪报原子完成。

## API Changes
复用实际 grant 的点查、批量状态、列表/数量、整数游标和人工 CAS 写接口。Bot 配置、对话/服务阶段和实际内容读路径保持原有授权查询；用回归测试证明自动写出的 grant 能被它们直接消费。

```python
# apps/backend/src/agentclaw/community/adapters/knowledge_platform/catalog.py:353
# Existing mutation shape remains the baseline.
set_grant(kb_id=kb_id, actor_id=authorized_actor, bot_id=bot_id,
          owner_id=owner_id, granted=desired,
          expected_revision=observed_revision, bot_name=trusted_display)
# Add only demonstrated gaps to this existing mutation family:
# trusted operation origin/id, same-value fencing, verifiable receipt.
```

开关读写优先扩展既有 KB metadata/ext 的受控路径，不把通用 ext 覆盖接口直接交给浏览器。creator 校验、字段级更新/CAS、状态与授权批次进度是新增能力；URL/方法名在查明既有能力后定，不预设 effective API 或新的认证路由。进度明确区分策略已保存、任务待执行、部分完成、失败和已收敛。

## Activity and count semantics
ECB 继续拥有更新记录。一次开关动作只产生一条汇总记录；可更新同一记录的执行进度/最终数量，不能每次重试追加动态。自动单项 mutation 标明真实来源，不能调用默认人工活动路径刷一屏“人工授权”。人工取消/恢复仍逐条记活动。

| Field | Meaning |
| --- | --- |
| 自动新增成功数 | 本次开启批次从未授权变为授权、且可归因于该操作并确认成功的去重 Bot 数；同 Bot 重试一次计数。 |
| 已授权/跳过数 | 原已 true、人工取消、归属改变等分别统计，不并入新增成功数。 |
| 失败/待确认数 | 单列；部分失败不报全量成功，结果未知不推算为成功。 |
| 关闭汇总数 | 同一“本次实际新增成功数”口径，正常为 0；关闭不执行撤权。保留授权总数如需展示，另标名称并按实际 grant 查询。 |
| 后续新建/转入 | 归各自生命周期任务，不回填已经完成的开启批次数量。 |

开启批次保存分页进度与去重依据，枚举/目标校验完成且所有分项终结才结束；不宣称瞬时全空间一致快照。关闭时未确认的旧开启操作仍归旧批次，不能计入关闭结果。该口径与页面当前授权总数不同，UI 必须明确命名。

## Key Files
公共目录缩写相对 `apps/backend/src/agentclaw/community/`，测试相对 `apps/backend/`。新增文件仅为按职责拆分的候选，不新增事实表。

| Work | Files |
| --- | --- |
| Grant orchestration | `core/knowledge_management/services/auto_authorization.py`（新增）、`ports.py`、`services/service.py` |
| Durable capture | `core/repository/implementations/bot/{initial_layout.py,mutations.py,owner_handoff.py,recycle.py}`、既有任务 repository/生命周期装配；实际扩展载体在 Task 1 固定 |
| Policy/mutation adapter | `adapters/knowledge_platform/catalog.py`、`adapters/http/knowledge_management/{router.py,schemas.py,errors.py}`、`api/knowledge_management_service.py` |
| Consumers (regression first) | `core/bot_knowledge/{service.py,stage_selection.py}`、`core/knowledge_management/services/conversation.py`、`adapters/knowledge_platform/grant_state_reader.py` |
| Provider/host | ECB KB ext、Bot authorization service/store、活动、既有认证；宿主 DI/database、工作台 `internal/knowledge/` 的 panel/domain/service/controller、Avernet gitlink |
| Knowledge/tests | 知识管理 README、Backend 导航、相关合同/接口/数据库/消费者/工作台测试；本目录 `verification.md` 在实施时记录证据 |

## Alternatives Considered
- ECB 投影 Bot 归属并计算继承权限：职责扩大且改造读链，按本轮裁决移除。
- 自动任务调用现有实际 grant：采用；存在写放大，以有界批次、限流、重试和汇总活动控制，不隐瞒逐关系写入成本。
- 只依靠队列去重或发送前校验：不能覆盖崩溃和在途旧写，不采用；必须结合可靠控制状态与接收端 CAS/屏障。
- 预设新表或新服务认证：缺少必要性证据，不采用；先验证既有载体和授权能力，最小补齐真实缺口。

## Migration / Rollout
新旧 KB 缺开关字段均 OFF，保留既有 grant true/false；不上线自动授权旧库。删除旧方案的 Bot 投影初始化与数据迁移任务，开启时按需枚举现有 Bot 并写实际 grant。

先完成执行权限、可靠捕获、CAS 旧写保护和活动来源，再开放开关；公开环境保持可独立测试。扩展字段局部更新，不覆盖其他 ext。任何新增持久结构需由复用调查证明必要，不能把本方案当 DDL 授权。

回滚时停止新自动增权，保留待处理撤权、未知结果回查和恢复记录；不能清空队列或控制状态后宣称完成。真实 grant 读路径不切换，已经成功授予的访问按既有授权继续保留。发布、生产数据操作和部署另行授权。

## Test Strategy
按 spec AC01–AC12 建立实际 grant、页面与新发起内容访问断言；不以任务入队/HTTP 200 代替收敛。

```python
# apps/backend/tests/community/core/knowledge_management/test_auto_authorization.py (new)
def test_missing_ext_defaults_off_and_creator_only_toggle(): ...
def test_automatic_execution_requires_existing_scoped_authority(): ...
def test_manual_revoke_beats_delayed_auto_grant_cas(): ...
def test_same_value_revoke_fences_late_grant_with_no_prior_row(): ...
def test_reentry_invalidates_old_revoke_and_resets_current_exception(): ...
def test_same_space_owner_handoff_preserves_all_four_states(): ...
def test_unknown_receipt_is_not_counted_as_success(): ...
def test_toggle_summary_counts_only_confirmed_new_grants_once(): ...
```

另测 Bot 写后/入队前退出、开关保存后/排队前退出、grant 已提交但回执丢失、旧 worker 返回、队列清理、企业事务回滚、无权撤旧 Owner 关系和权限恢复。企业 AUTOCOMMIT/真实驱动证据单列；不能只以 SQLite 测试证明可靠性。回归沿用 `tests/community/core/bot_knowledge/`、`tests/community/endpoints/test_knowledge_management.py`、`tests/community/contracts/test_knowledge_catalog.py` 和架构/DI 检查；宿主验证现有列表、计数、整数分页及实际检索无需 effective v2。

设计文档修改本身只检查一致性、引用和格式；上述功能测试均待实施，不宣称已通过。
