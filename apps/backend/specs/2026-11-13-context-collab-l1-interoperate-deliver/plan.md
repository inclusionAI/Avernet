# Context Collab L1 Interoperate & Deliver 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 ACCS-101/001 落 L1 域侧资产（公共合同值对象、操作封装、A2A wire 投影、桥接记录、`A2aDeliveryPort` + Mock 实现），并把任务代办交付缝换绑经 L1 mock 交付（第一个真实消费者）。

**Architecture:** 纯值对象模块（零 IO、零权威状态）→ 端口 + 记忆化 mock → 交付缝桥（`DispatchedTaskInfo` 补发起人身份链）→ DI 双绑定（`A2aDeliveryPort→MockA2aDelivery`、`TaskDispatchPort→A2aTaskDispatch`）。

**Tech Stack:** Python 3.12、python-injector DI、pytest。

**Spec:** `apps/backend/specs/2026-11-13-context-collab-l1-interoperate-deliver/spec.md`（Accepted；§3 字段表、§4 端口签名、§5 交付缝、§6 裁决 A–H 为权威）。

## Global Constraints

- 分支 `dev_bbs_help_1019_3` 叠加 HEAD；测试 `cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/core/task/context_collab/ tests/community/di/test_task_module.py -v`（全量不跑）。
- 提交 `<type>(backend): <outcome>`，尾行 Co-Authored-By；文件 ≤1000 行；不格式化未改动行。
- 值对象纪律 = collet.py 严格面（必填非空/枚举闭包/未知键拒/round-trip）；builder实证:勿抄 ContextSpec(无未知键拒)/ProgressSnapshot(宽松)的同居件。
- ID 三不混 / 外部终态不入权威 / 桥接记录逐映射 / operation_id 幂等记忆化（spec §2 铁律）。
- 既有 wire/DDL/三北向零改动。

## Review Focus

1. **重试重复副作用**：同 operation_id 重发必须同答且桥不增。→ T1 `test_send_is_idempotent_per_operation`
2. **状态混线**：A2A Task 终态绝不出现在 AccsOperationResult.status 之外的语义位。→ T1 `test_task_lifecycle_receipts_are_line_level`
3. **身份链完整**：桥出去的 envelope 必须带 caller(AGENT)+accountable_principal_ref(发起人)。→ T2 `test_bridge_envelope_carries_accountable_principal_chain`
4. **arch 执法双点**：products 白名单的 TWO enumeration(`_ALLOWED_CONTEXT_COLLAB_PARTS` + `_layer_dirs`)都要加 l1,漏一处即红或漏trap。→ T2 架构测试零回归
5. **DI 构造路径**:injector 实证会忽略构造默认值,Protocol 参数无绑定即炸——A2aDeliveryPort 必须先绑。→ T2 DI 解析用例
6. **严格面滑坡**:L1 全部对象保 collet 级严格解码,禁 ContextSpec/ProgressSnapshot 式宽松。→ T1 未知键拒用例逐对象

---

### Task 1: L1 模块（五件值对象 + 端口 + mock）

**Files:**
- Create: `src/.../context_collab/l1_interoperate_deliver/{__init__,common,envelope,a2a_wire,bridge,delivery}.py`
- Test: `tests/community/core/task/context_collab/test_l1_wire_objects.py`、`test_l1_delivery_mock.py`（均新）

**Interfaces（Produces,签名逐字;spec §3/§4）**:
- common: `IdentityRef`、`ObjectRef`、`ContextRef`、`OperationContext`、`AccsError`（九值 category+trace_id 必填+object_ref?）
- envelope: `AccsOperationEnvelope`（layer 五值/mode 三值）、`AccsOperationResult`（status 四值）
- a2a_wire: `AgentCard`+`AccsAgentCardExtension`(g3 四值)、`A2aMessage`/`Part`(TEXT|ENVELOPE|REF)、`A2aTask`(P1 五值状态)、`A2aArtifact`
- bridge: `A2ABridgeRecord`(MESSAGE|TASK|ARTIFACT;INBOUND|OUTBOUND)
- delivery: `A2aDeliveryPort` Protocol 八法 + `MockA2aDelivery`（默认卡注册表可注入、send/start/cancel/deliver 桥+ACCEPTED 回执、query_task 只读、`declare_protocol()`、`bridge_records()`、operation_id 记忆化）

- [ ] Step 1: 失败测试两文件（对象严格面逐对象 + 端口五族/幂等/审计）→ Step 2 跑红 → Step 3 实现六文件 → Step 4 跑绿 + 目录零回归 → Step 5 commit `feat(backend): add L1 interoperate-deliver wire objects and mock port`

### Task 2: 交付缝桥 + DI 换绑 + 白名单

**Files:**
- Modify: `.../products/task_commission/service_api.py`（`DispatchedTaskInfo` + `publisher_principal_id`）、`service.py`（publish 填发起人）、`.../products/task_commission/dispatch.py`（新增 `A2aTaskDispatch`、`LOCAL_ISSUER` 常量）
- Modify: `src/agentclaw/community/di/modules/task_module.py`（`A2aDeliveryPort→MockA2aDelivery`、`TaskDispatchPort→A2aTaskDispatch` 双 singleton）、tests 侧架构白名两处、`tests/community/di/test_task_module.py`（追加/调整）
- Test: `tests/.../products/test_task_commission_dispatch_bridge.py`（新）；`test_task_commission_end_to_end.py`（捕获断言展宽第 5 字段）

**Interfaces:**
- `A2aTaskDispatch(TaskDispatchPort)`: caller=IdentityRef(AGENT, "avernet-task-commission-dispatcher", LOCAL_ISSUER)；accountable_principal_ref=IdentityRef(PRINCIPAL, publisher_principal_id, LOCAL_ISSUER)；envelope payload={title,body,assignee_agent_id}、mode=EVENT、target=ObjectRef("Collet", collet_id)；载体 A2aMessage(role="agent", parts=[Part(ENVELOPE, ...)])
- DI 测试：mock singleton 解析；`TaskDispatchPort` 解析为 `A2aTaskDispatch` 且共享同一 `A2aDeliveryPort` singleton

- [ ] Step 1: 失败测试（桥链/e2e 展宽/DI 双点）→ Step 2 红 → Step 3 实现 + 换绑 → Step 4 绿 + 架构测试零回归 → Step 5 commit `feat(backend): bridge task commission dispatch through L1 mock delivery`

### Task 3: 收尾

- [ ] Step 1: 全目录回归绿 → Step 2: README 登记 → Step 3: commit `docs(backend): register L1 interoperate-deliver spec`

## Self-Review

- 覆盖:spec §3→T1 六文件;§4→T1 端口+mock;§5→T2;§6 A–H 全数落(A/B 作者票,D/E/F/C/G/H T1/T2 实现细节);§7 测试表 1:1。
- 类型一致:端口/值对象签名 T1 产 T2 消逐字;DispatchedTaskInfo 加字段不破既有 3 构造点(默认值/dataclass 顺序)。
- Review Focus 6→归属测试齐。
