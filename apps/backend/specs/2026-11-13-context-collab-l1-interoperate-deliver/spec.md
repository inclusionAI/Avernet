# Context Collab L1 Interoperate & Deliver（A2A 能力，P1 全 mock）

- 日期：2026-11-13
- 状态：Accepted（方向与两项裁决经作者确认 2026-11-13；builder check 两问按 ACCS 文档权威内裁：AccsError 九值全量、交付缝补发起人身份链）
- 拥有模块：Backend `agentclaw.community.core.task.context_collab`（l1_interoperate_deliver/）
- 目标消费者：L1 端口与 mock、任务代办交付缝桥、DI、architecture 层、tests
- 协议依据（逐节转写为唯一权威）：ACCS-101 §2（Data Model）/§3（操作族）/§4（边界）/§5（映射与保留清单）/§6（Agent Card 扩展）/§9（安全与可靠性）/§10（一致性要求）；ACCS-001 §3–§6/§9（公共合同字段）

## 1. 目标与范围

按 ACCS-101 把 L1 的**域侧资产**落成代码：公共合同值对象、操作封装、A2A wire 投影对象、桥接记录、交付端口与 **Mock 实现**；并把任务代办的交付缝桥至 L1 mock（L1 第一个真实消费者）。**真实 A2A 传输（HTTP 端点、Agent Card 发现、鉴权交接）P2 由交付侧（BCS / engine adapter）经同端口整体接管**——本 spec 不建 socket、不建路由、不建鉴权（ACCS-101 §4 豁免条款与 §9"传输认证与 G1 业务授权分离"的职责划分）。

范围内：五件值对象文件 + 端口/mock + DI 绑定 + 交付缝桥（换绑 `TaskDispatchPort`）+ products 白名单加 `l1_interoperate_deliver` + 测试。

非目标（P2 留档）：真实 A2A 客户端/服务端、Agent Card 签名与来源验证、流式/回调交付形态、协商族的线上实现（P1 仅 mock 声明式）、L4 Query/L4 Message 完整绑定表实现（本 spec 只据悉例映射与 envelope 承载能力）。

## 2. 域侧边界（ACCS-101 §4 的代码化铁律）

1. **L1 不拥有权威状态**：mock 的任何回执/状态不改写 L2–L6 对象；`AccsOperationResult.status` 是**上层语义状态**，A2A 对象状态线级、禁互认（§10-3）。
2. **ID 三不混**：A2A 对象 ID、ACCS operation_id、上层对象 ID 各自独立；禁止把 A2A Task ID 复用为协作 Task/Method 内部 ID（§7 的禁止项）。
3. **桥接可验证**：每次上层操作 → A2A 载体的映射必须产出 `A2ABridgeRecord`（§2/§5/§10-5）。
4. **重试幂等**：同 `operation_id` 重发不得重复产生桥接记或线级副作用（§10-4）——mock 以 operation_id 记忆化最小满足。
5. 公共合同值对象（IdentityRef/ObjectRef/ContextRef/OperationContext/AccsError）**首居本模块**；跨层消费者出现时再评估提升（同 `ActorContext` 归位 G1 先例）。

## 3. 模块结构与对象（ACCS-001 §3–§6/§9 字段逐字）

```text
l1_interoperate_deliver/
  __init__.py
  common.py     # IdentityRef(identity_type:PRINCIPAL|AGENT, identity_id, issuer)
               # ObjectRef(object_type, object_id, version?, issuer?)
               # ContextRef(context_type:八值闭包, context_id)
               # OperationContext(request_id, idempotency_key?, caller, accountable_principal_ref?,
               #   delegation_chain_refs, space_refs, collet_ref?, method_binding_ref?,
               #   product_profile_ref?, trace_id, correlation_id?, causation_id?,
               #   requested_at, deadline?, protocol_versions, extensions)
               # AccsError(code, message, retryable, category:九值闭包
               #   VALIDATION|AUTHENTICATION|AUTHORIZATION|CONFLICT|NOT_FOUND
               #   |RATE_LIMIT|DEPENDENCY|TIMEOUT|INTERNAL(ACCS-001 §9 逐字), trace_id, object_ref?)
  envelope.py  # AccsOperationEnvelope(operation_id, layer:L2..L6闭包, operation_family, action,
               #   mode:COMMAND|QUERY|EVENT, context, target_ref?, expected_revision?,
               #   input_refs, payload?)
               # AccsOperationResult(operation_ref, status:ACCEPTED|SUCCEEDED|REJECTED|FAILED,
               #   result_refs, event_refs, error?)
  a2a_wire.py  # AgentCard(agent_id满核, endpoint, skills, protocol_version, accs_extension?)
               #   + AccsAgentCardExtension(accs_versions, identity_ref,
               #     accountable_principal_refs, capability_profile_refs, supported_method_profiles,
               #     supported_product_profiles, supported_g3_profiles:四值闭包, conformance_evidence_refs)
               # A2aMessage(message_id, role, parts), Part(part_kind:TEXT|ENVELOPE|REF, body),
               # A2aTask(task_id, status:SUBMITTED|WORKING|COMPLETED|FAILED|CANCELED, result?),
               # A2aArtifact(artifact_id, parts)
  bridge.py    # A2ABridgeRecord(bridge_id, accs_object_ref, a2a_object_type:MESSAGE|TASK|ARTIFACT,
               #   a2a_object_id, direction:INBOUND|OUTBOUND, transformation_profile_ref?,
               #   content_digest?, created_at)
  delivery.py  # A2aDeliveryPort(Protocol) + MockA2aDelivery
```

值对象纪律沿 `l4_context_primitives/collet.py` 风格：`from_dict` 严格解码（必填非空、枚举闭包校验、未知顶层键拒绝）、`to_dict` 无损 round-trip；wire 侧时间戳一律 ISO 字符串。

## 4. 操作接口（ACCS-101 §3 五族 → 端口方法）

```python
class A2aDeliveryPort(Protocol):
    def resolve_agent_card(self, *, agent_id: str) -> AgentCard: ...          # ① 发现/读取
    def send_message(self, card, message, envelope) -> AccsOperationResult    # ② 发送 Message
    def start_task(self, card, task, envelope) -> AccsOperationResult          # ③ 生命周期
    def query_task(self, card, task_id, envelope) -> AccsOperationResult
    def cancel_task(self, card, task_id, envelope) -> AccsOperationResult
    def deliver_artifact(self, card, artifact, envelope) -> AccsOperationResult  # ④ 交付
    def declare_protocol(self) -> dict[str, str]                              # ⑤ 协商(声明式)
    def bridge_records(self) -> tuple[A2ABridgeRecord, ...]                   # 桥接簿记(审计面)
```

- 每个 send/start/cancel/deliver 调用:产出桥接记(direction=OUTBOUND、accs_object_ref=envelope 对象锚、a2a_object_type 按载体)、返回 `AccsOperationResult(operation_ref=ObjectRef("Operation", envelope.operation_id), status=ACCEPTED, result_refs=[载体 ObjectRef])`;
- **记忆化**:同 `envelope.operation_id` 重发 → 返回首答、不新增桥接记(一致性 §10-4);
- `query_task` 只读不桥;cancel 走桥;
- `MockA2aDelivery` 持内置默认卡注册表(测试可注入),不触任何 IO。

## 5. 交付缝桥（任务代办 → L1，裁决二）

- **身份链补全（builder check Q1 内裁）**：`DispatchedTaskInfo` 增第 5 字段
  `publisher_principal_id: str`（发起人 principal_id；`publish_task` 自 `context.actor` 填入）；
- products/task_commission/dispatch.py 新增 `A2aTaskDispatch(TaskDispatchPort)`:
  `dispatch_task_info(info)` → 构建 `AccsOperationEnvelope(operation_id=f"op_{uuid4}", layer="L4", operation_family="message", action="publish", mode="EVENT", context=OperationContext(caller=IdentityRef(AGENT, 本地调度者常量, issuer=LOCAL_ISSUER 常量), accountable_principal_ref=IdentityRef(PRINCIPAL, info.publisher_principal_id, issuer=LOCAL_ISSUER)——ACCS-001 §5 规则 1 的可追溯链, collet_ref=ContextRef(COLLET, collet_id)), target_ref=ObjectRef("Collet", collet_id), payload={title,body,assignee_agent_id})` → `A2aMessage(role="agent", parts=[Part(ENVELOPE, envelope)])` → `A2aDeliveryPort.send_message`;
- 失败语义沿既有裁决:如实上抛、任务已持久不回滚;
- **DI 换绑**:`TaskDispatchPort → A2aTaskDispatch`(singleton,依赖注入 `A2aDeliveryPort` mock singleton);`LoggingTaskDispatch` 保留为构造默认,不再是 DI 绑定实现;
- products 架构测试白名单加 `l1_interoperate_deliver`(层模块直进允许集)。

## 6. 裁决表（作者 ☐ / 我落 ★）

| # | 事项 | 裁决 |
| - | --- | --- |
| A | 模块名 | ☐ `l1_interoperate_deliver`(沿层全名命名) |
| B | 交付缝 | ☐ 本轮接通:`TaskDispatchPort` DI 绑定换 `A2aTaskDispatch` 经 L1 mock |
| C | envelope mode | ★ 交办送达= `EVENT`(事实事件:任务已创建,信息送达;不命令 bot 现态)——若错,改 `COMMAND` 一处字面量 |
| G | 交付发起人链 | ★(builder Q1) `DispatchedTaskInfo` 加 `publisher_principal_id`;wire 上下文 caller=本地 AGENT 调度者 + accountable_principal_ref=发起人(ACCS-001 §5 规则1 合规唯一解)；issuer 用本地实现常量 `LOCAL_ISSUER`(P2 签名验收随真实 A2A 补) |
| H | AccsError 全量 | ★(builder Q2) 九值 category + trace_id 必填 + object_ref 可选(ACCS-001 §9 逐字,spec 前稿误写六值) |
| D | 公共合同首居 | ★ IdentityRef 等五件落 L1 模块;跨层消费者出现再评估提升(ActorContext 先例) |
| E | wire 时间戳 | ★ ISO 字符串(A2A 线级形态;域内 datetime 不混入) |
| F | 幂等实现 | ★ mock 按 operation_id 记忆化(重试同答、桥不增)——最小满足一致性 §10-4 |

## 7. 测试策略

| 文件 | 覆盖 |
| --- | --- |
| `tests/.../test_l1_wire_objects.py`（新） | 各值对象严格 round-trip/未知键拒/枚举闭包(IdentityRef 两值、layer 五值、mode 三值、status 四值、direction 两值、g3 四值) |
| `tests/.../test_l1_delivery_mock.py`（新） | 五族端口行为:send→ACCEPTED+桥(OUTBOUND/MESSAGE)、**同 operation_id 重试同答且桥不增**、task 生命周期回执、deliver_artifact、declare_protocol、agent card 解析 |
| `tests/.../products/test_task_commission_dispatch_bridge.py`（新） | 经 DI 全链:publish → A2aTaskDispatch → envelope(EVENT/Collet target/payload 三字段)→ mock 收到 A2aMessage 含 envelope part;LoggingTaskDispatch 退位仅存构造默认 |
| 架构测试 | 白名单加 l1 后既有用例零回归 + products 引 l1 仍绿 |

按需只跑 `context_collab` 目录 + DI;CI 门禁延后统一处理。
