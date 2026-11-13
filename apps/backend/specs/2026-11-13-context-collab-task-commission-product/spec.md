# Context Collab 任务代办产品层（task_commission；顶层单值 Open）

- 日期：2026-11-13
- 状态：Accepted（设计 + 裁决 A–L 经作者确认 2026-11-13；builder check 真问题经作者裁定后落定）
- 拥有模块：Backend `agentclaw.community.core.task.context_collab`（products/）
- 目标消费者：products/task_commission 服务、G4 状态机、G1 白名单、DI、context-collab tests
- 关联设计：
  - [BBS 公开讨论产品层](../../2026-11-12-context-collab-bbs-public-discussion-product/spec.md)（乙案依赖矩阵、编排与读投影先例，本 spec 不重复）
  - [L6 BBS product forms](../2026-11-12-context-collab-l6-product-forms/spec.md)（method_executor 组合约束先例）
  - [创建事实全量入流](../2026-11-12-context-collab-l4-creation-event-facts/spec.md)
- 协议依据：ACCS-104 §2/§3（Work 族、Task/Activity 状态分离）、ACCS-105 §4.4（Open 方法）、ACCS-106 §2

## 1. 目标与范围

第三个产品（公开讨论之后的第二个用例型产品）：**用户发布一个带目标的代办任务，指定一个 bot 作为辅助执行 Agent，任务推进经专用 G4 状态机闭环到验收/取消**。产品形态为**顶层单值 `MethodKind.OPEN`**（协议合同扩张，见 §4），P1 **闭协作**（见裁决 C）。

- ✅ 范围内：新 profile 值与 G4 机器、G1 顶层白名单加 `Open`、状态裁决纯函数提升进 G4、产品自持编排（发布 + Work 全族六动作 + 详情/列表读投影）、DI、模块级 service 接口。
- ❌ 后置：HTTP/OpenAPI 对外交付（沿公开讨论 spec §5 裁决；P1 模块级接口）；Open 的开放参与（apply/admit 录取族，ACCS-304）——P1 闭协作（**增量裁决 2026-11-13，见 §9**：录取族中「系统内部编排对指定 bot」一段 P1 拉入发布路径；对外参与开放仍留 P2）；结帖生命周期能力操作；产物正文治理（artifacts 为字符串引用清单，`ResourceBinding/Artifact` 实体化 P2+）。

## 2. 域映射与操作族映射

| 任务代办概念 | 协议对象 |
| --- | --- |
| 一个代办任务 | 一个 **Collet**（同"帖子=Collet"先例） |
| 任务标题 / 任务正文 | `objective` / 首帖正文 **Message 公开事件**（沿公开讨论裁决；G1 由 `_is_principal` 放行——创建人即 `spec.principal_ref`） |
| 指定的执行 bot | `ContextSpec.executor_ref = AgentRef("bot", <agent_id>)`——**辅助执行者**，不承担验收权（沿 L6 先例"不把代办伪装成 Single"） |
| 协作形态 | `method_kind = MethodKind.OPEN.value`（顶层白名单新成员，§4） |
| 任务推进 | `task.started / task.progress.updated / task.submitted / task.accepted / task.rejected / task.cancelled`（Task 族控制事件，primitive_type=Task，visibility=public） |
| 交付物清单 | `task.submitted` payload 的 `artifacts` 列表（非空字符串引用；`required_artifacts_present` 守卫把门） |
| 状态权威 | G4 `task_commission_goal` 机器（§3）改写快照 `status` |

### 2a. 操作族映射表（ACCS-104 §3）

| 产品合同 | 操作族 | G1 路径 |
| --- | --- | --- |
| `publish_task` | Context.create + Work.**propose/assign**（委派即创建参数 executor） | `check_create`（白名单含 Open） |
| `start_task` | Work.**start** | principal 本人 or executor（`_is_principal / _is_executor`） |
| `report_progress` | Work（progress 维持 in_progress） | 同上 |
| `submit_task` | Work.**complete**（bot 侧，携交付物清单） | 同上 |
| `accept_task` / `reject_task` | Work（验收 done / 打回 in_progress） | **仅 principal**（`actor_is_responsible_principal` 守卫） |
| `cancel_task` | 同族取消（→ cancelled 终态） | **仅 principal**（`actor_can_cancel`） |
| `detail_task` / `list_tasks` | Query.search/filter/project | 只投影 public |

## 3. G4 `task_commission_goal` 状态机

六状态六迁移与既有 `bbs_help_request_goal` 同形状，**仅 allowed_methods 与守卫语义编辑半径不同**：

```text
created ─task.started(guard: actor_can_start_task)→ in_progress
in_progress ─task.progress.updated(event_matches_task)→ in_progress    （自环）
in_progress ─task.submitted(required_artifacts_present)→ reviewing
reviewing ─task.accepted(actor_is_responsible_principal)→ done
reviewing ─task.rejected(actor_is_responsible_principal)→ in_progress
created/in_progress/reviewing/blocked ─task.cancelled(actor_can_cancel)→ cancelled
```

- `StateDefinition.allowed_methods = ("Open",)` 六状态一致；
- 守卫词**全部复用既有 `GUARD_REGISTRY` 求值器**（语义已核：`actor_can_start_task` = 发起人本人或 bot 型 principal 且 `principal_id == executor.agent_id`；`required_artifacts_present` = payload 携带非空 artifacts 列表；`event_matches_task` = payload 不得显带 task_id）——**零新守卫词**；
- 注册进 `MACHINE_REGISTRY["task_commission_goal"]`，注册期 `validate_definition` 生效。

## 4. 协议合同扩张（本 spec 的两处既有合同修改）

1. **G1 `check_create` 顶层白名单**：`Single / Open + Group` → 追加 **`Open`**（`authorization.py`）。结构性影响核过：`_build_top_level_binding` 经 `parse_method_kinds("Open")` 得单值投影，`_project_initial_roster` 因无 "Group" 自然跳过（Open 单值无 roster），L5 零改动。
2. **状态裁决纯函数提升进 G4**：`services/context_collab_service._resolve_event_status`（~35 行：规则匹配 + `GuardEvaluationInput` 构造 + `GUARD_REGISTRY` 求值 + `resolve_transition`）提升为 `g4_contracts_consistency/workflow_resolution.py` 公共纯函数；通用服务改调用，产品共用同源——乙案禁 import 下的唯一去重路径。**产品 Work 动作的快照推进不再是恒等**（区别于公开讨论裁决 J），由该函数按机器裁决。

## 5. 产品层结构（沿乙案）

```text
products/
  service_api.py                  # 公开讨论合同(既有)
  bbs_public_discussion/          # 既有
  task_commission/
    __init__.py
    service_api.py               # 本产品合同:命令/视图/TaskCommissionServiceProtocol 协议
    service.py                   # 自持编排
```

- **依赖矩阵零新开口**：`products/` import 白名单（架构测试执法）原样覆盖新产品——所需面（L2/L3/L4 值对象与 `creation_payload`、G1（含 `ActorContext` 新家）、**G4 `validate_event_body` + 新 `workflow_resolution` + `GUARD_REGISTRY`**、L5 `MethodKind`、L6 profile、三处基建端口）全部已在白名单内；
- DI：`TaskCommissionProtocol → TaskCommissionService` singleton（`task_module.py` 追加一行段），复用既有 repo/authorizer/method_store 绑定；
- 模块级交付面：`api/task/task_commission_service.py` 协议再导出（沿既有先例）。

### 5.1 产品合同（签名逐字）

```python
# task_commission/service_api.py
@dataclass
class PublishTaskCommand:
    title: str
    body: str                      # 任务描述正文
    space: SpaceRef
    assignee_agent_id: str         # 指定执行 bot 的 agent_id(P1 仅 bot 型)
    resource_refs: list[ResourceRef] = field(default_factory=list)

@dataclass
class TaskAdvanceCommand:           # start/progress/submit/accept/reject/cancel 共用形
    collab_context_id: str
    note: str | None = None         # 可选备注(进 payload.note)
    artifacts: list[str] = field(default_factory=list)   # 仅 submit 用,须非空

@dataclass
class TaskCommissionCreatedView:
    collet_id: str; title: str; body_event_id: str; status: str   # "created"
    context_version: int

@dataclass
class TaskAdvancedView:             # 六 Work 动作统一返回
    collab_context_id: str
    event_id: str
    status: str                     # 推进后的新 status
    context_version: int

# 协议方法(模块级交付面,P1 无 HTTP):
publish_task(command, context) -> TaskCommissionCreatedView
start_task(command: TaskAdvanceCommand, context) -> TaskAdvancedView
report_progress(...) -> TaskAdvancedView
submit_task(...) -> TaskAdvancedView
accept_task(...) / reject_task(...) / cancel_task(...) -> TaskAdvancedView
detail_task(collab_context_id) -> TaskCommissionDetailView      # 标题+正文+当前状态+事件流公开投影
list_tasks() -> list[TaskCommissionSummaryView>               # 复用仓储两读方法,按 product_kind="task_commission"
```

### 5.2 编排要点（自持，权弃 service_api）

- **发布**：与公开讨论同骨架（`creation_payload` 六键 + 首帖正文 Message + L5 顶层投影），差异三处：`method_kind=MethodKind.OPEN.value`、`executor_ref=AgentRef("bot", command.assignee_agent_id)`、`workflow_kind=TASK_COMMISSION_GOAL.value`；
- **六 Work 动作**统一编排：`get_current_status` → 外 profile/不存在 404 拦截 → **产品时序门**（裁决 J：前置状态集越轨即 ValidationError）→ **提交权断言**（裁决 K：submit 仅 executor 值匹配）→ `validate_event_body`（task.* 合同）→ G1 `check_append`（`_is_principal or _is_executor` 双路径；事件非 posted/Message 故窄规则天然不适用——闭协作，裁决 C）→ **`task.*` 事件 append（primitive_type=Task、visibility=public）**，`snapshot_updater` = **状态裁决纯函数的结果**（§4.2，非恒等）→ 返回推进视图；`expected_version` CAS 与父事件存在性照旧由仓储把守；
- **读投影**：`detail_task` 沿公开讨论详情先例（标题=初始事件 payload objective/回退；正文=首条 public Message；**事件流公开投影含 Task 控制事件**——代办消费面要看得见推进历史；外 profile 404）；`list_tasks` 复用 `list_collets_by_product("task_commission")` + `count_message_events`。

## 6. 裁决表

| # | 事项 | 裁决（作者 ☐ / 我落 ★ 标） |
| - | --- | --- |
| A | 产品形态 | ☐ **另立新 profile**：`task_commission`（不复用 bbs_help_request） |
| B | 协作方法 | ☐ **顶层单值 `MethodKind.OPEN`**：G1 顶层白名单追加 Open（协议合同扩张）；不改 L5（无 roster、单值投影） |
| C | Open 的开放语义 | ☐ **P1 闭协作**：G1 公开 Message 窄规则维持仅 Open+Group；参与开放（apply/admit 录取族）P2 接 ACCS-304。**增量裁决（2026-11-13，§9）部分推翻**：发布路径以系统编排驱动录取面四步（publish/submit/screen/admit）至指定 bot；对外公开参与仍 P2 |
| D | 用例面 | ☐ **全 Work 族**（发布 + 开始/进度/提交/验收/打回/取消）；HTTP 后置 |
| E | 交付物表达 | ★ `task.submitted` payload `artifacts: list[str]` 字符串引用清单；实体化（Artifact/ResourceBinding 强绑定）P2+ |
| F | 事件可见性 | ★ 任务正文与全部控制事件一律 `public`——"发布"自带公开语义；闭协作由 G1 写路径把守而非可见性遮蔽 |
| G | 状态裁决去重 | ★ `_resolve_event_status` 提升 G4 层模块（§4.2），通用服务与产品同源；**不做**产品内复制 |
| H | 守卫语义 | ★ 全部复用既有 `GUARD_REGISTRY` 求值器零新词（语义已核 §3） |
| I | 进度入参 | ★ `report_progress` 的 `note` 落 payload.note（P1 不挂 percent 投影语义；进度数值投影 P2 随读面细化） |
| J | 非法时序/终态推进 | ★ **产品时序门**（builder check Q1 揭洞：G4 通用合同"无规则命中=事件接纳、状态不变"会让 done/cancelled 后照样落事件）。六动作各钉允许前置状态集：start←created、progress←in_progress、submit←in_progress、accept/reject←reviewing、cancel←created/in_progress/reviewing/blocked；越轨 `ContextCollabValidationError`（错误词指明当前状态与动作）。机器仍是唯一 status 裁决者，提升函数零语义变化，通用路径零影响 |
| K | 提交权 | ☀ **submit_task 仅指定 bot**（executor 值匹配断言，产品层）；发起人经 reject/cancel 主控，不代提交 |
| L | 命名与测试预算 | ★ 协议名统一 `TaskCommissionServiceProtocol`（沿 BbsPublicDiscussionServiceProtocol 先例）；`test_builtin_machine_pinned`（注册表恰两键）与 `test_create_rejects_bare_group_open_swarm_top_level`（裸 Open 拒绝清单）两处按**改写**预算，非纯追加 |
| M | 交办交付缝 | ★ 发布成功后经 `TaskDispatchPort` 自动送达任务信息给指定 bot（step-2 用户故事落点）：P1 默认 `LoggingTaskDispatch`，真实通道 P2 经 L1/A2A 同端口整体替换；失败如实上抛、任务已持久不回滚（ACCS"送达≠上层成功"解耦） |
| I' | (微修裁决 I) | ★ `report_progress` 启用 `percent`（0-100 整数，仅 progress 可携带）——`percent` 是既存协议词汇（快照投影 `_last_payload_percent` 读同名键），非新造；详情公开事件投影随带 `payload`（percent/note/artifacts 可查） |

**兼容与边界**：两张协议表/列/迁移/三个既有 wire 零改动；`bbs_help_request` 形态不动（新机器新键）；乙案依赖矩阵与架构测试原样执法；错误合同沿既有三类；文件 ≤1000 行；`T | None` 仅 `note`、`body_event_id` 类合法可空。

## 7. 测试策略

| 文件 | 覆盖 |
| --- | --- |
| `tests/.../test_state_machine.py`（**含改写**，裁决 L） | `test_builtin_machine_pinned` 注册表按三键改写；新机注册 conformance、allowed_methods=("Open",)、六迁移形状与 guard_refs |
| `tests/.../test_context_collab_service.py`（含改写，裁决 L） | 顶层白名单拒绝清单中移出裸 "Open"（该测试曾显式断言其被拒） |
| `tests/.../test_method_authorization.py` 或 G1 测试（追加） | 顶层白名单加 Open：`check_create(method_kind="Open")` 放行；任意配 executor 缺失规则沿 Single 既有（Open 不强制 executor——代办必有 assignee 属产品编排校验，不进 G1） |
| `tests/.../products/test_task_commission_publish.py`（新） | 发布编织（method=Open、executor=assignee、六键创建事实、首帖正文、L5 顶层单值 binding）；空 assignee/标题正文拒绝 |
| `tests/.../products/test_task_commission_work.py`（新） | 全 Work 族正反：bot 开工（值匹配放行）、陌生 actor 拒（G1）、进度自环、**提交无 artifacts 拒（守卫）**、发起人验收 done/打回 in_progress、bot 越权验收拒、**发起人越权 submit 拒（裁决 K）**、**越时序拒（裁决 J：created 上 submit、done 后任意推进）**、发起人取消、外 profile 404、CAS 409 透传；**状态推进非恒等**（status 由机器派写） |
| `tests/.../products/test_task_commission_read.py`（新） | 详情（标题读源新主+回退、正文、控制事件入公开投影、外 profile 404）、列表（profile 过滤+倒序+计数） |
| DI 测试（追加） | 产品协议 singleton 解析与共享注入 |
| 全量回归 | `context_collab` 目录 + repository + DI（含通用服务改用提升函数的零行为回归） |

按需只跑目录面;CI 门禁延后统一处理。

## 9. 发布驱动的 Open 方法生命周期（P1 增量裁决，2026-11-13）

**裁决背景**：原实现「发布」只投影顶层单值 Open binding，L5 Open 方法服务操作面零调用——挂 Open 之名而不进 Open 生命周期。用户裁决（2026-11-13）：发布之后底层必须走真方法生命周期，否则不叫「使用了 OPEN 这个方法」。

**编排链**（`publish_task` 内同步，发布人 ActorContext，系统代跑）：

```
create_context（L4 Collet 持久）
  → project_created_context（既有顶层单值 binding 投影）
  → 方法编排缝 TaskMethodOpsPort.admit_designated_assignee：
      publish_opportunity → submit_application → screen → admit
  → _append_message（任务正文）
  → TaskDispatchPort.dispatch_task_info（交付缝送达）
```

- **参与形态**：`participation_type=SINGLE_CONTRIBUTION`、`capacity=1`、`scope_ref=admitted_scope_ref=collet_id`、`closes_at=None`。录取产物只有 `AdmissionDecision`（SINGLE_CONTRIBUTION 不产生 roster/成员/分配行）——与「顶层单值、辅助执行者、不伪装成成员」先例一致；机会 admit 后保持 OPEN（P1 不闭合）。
- **身份面**：申请的 `applicant_agent_ref`=指定 bot、`accountable_principal_ref`=发布人（系统代投的申请由发布人担责）；publish/screen/admit 走 G1 owner 面（发布人身份与本 Collet spec.principal_ref 字符串相等即过），submit_application 走公开面。产品面之外非 owner 不可达（`check_create` 先挡），非 owner 拒绝用例不住产品测试。
- **幂等键**：`opportunity_id`/`application_id` 传 `None`（服务端生成）——`publish_task` 恒新 Collet 无重放路径；确定性键反而自陷（`opens_at` 参与机会 replay 形态比对，重试必炸形状校验）。`decision_id` 适配器生成（admit 形态面只比 `admitted_scope_ref`，确定性本可 replay-safe，统一生成口径并记录之）。
- **失败语义**：方法面任一步抛错原样上穿 `publish_task`；Collet 已持久不回滚（交付缝同款纪律）。screen/admit 中途失败在 `mb_top_{collet_id}` 上留下 OPEN 机会孤儿行是 **P1 接受的中间态**，不做 close/reject 补偿（补偿翻倍失败面；孤儿仅方法面可见、Collet 已不可用）。
- **依赖落点**（乙案白名单零改动）：窄端口 `TaskMethodOpsPort` + 命令/回执 DTO 住 `products/task_commission/service_api.py`；适配器 `TaskCommissionOpenMethodAdapter` 住 DI 层（`di/modules/task_commission_method_ops_adapter.py`，不受产品 import 法约束），`@inject` 拉 `OpenMethodOpsServiceProtocol` singleton；`TaskModule` 绑端口（同 `TaskDispatchPort` 直接构造样例）。
- **已知缝隙（后续独立改动，防当 bug 报）**：`accept_task`/`cancel_task` 终态不闭合机会（owner 面 close_opportunity 语义在但未接）；E2E 外部流程零改动——方法面由发布编排自跑，bot 收到的仍是任务信息四步故事。

**测试面**：`products/test_task_commission_method_lifecycle.py`（真链端面形态/缝次序/失败穿透/校验前置）+ E2E 方法 store 断言 + DI 接线测试。
