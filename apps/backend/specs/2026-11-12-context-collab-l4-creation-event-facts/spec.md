# Context Collab L4 创建事实全量入事件流 + 标题读源切换

- 日期：2026-11-12
- 状态：Accepted（方向 B 经作者确认 2026-11-12；两条执行裁决见 §3，如不同意随时推翻）
- 拥有模块：Backend `agentclaw.community.core.task.context_collab`
- 目标消费者：创建路径 payload、BBS 公开讨论产品层（title 读源合同）、context-collab tests
- 关联设计：[L4 Collet ACCS 对齐](../2026-11-12-context-collab-l4-collet-accs-alignment/spec.md)、[ResourceBinding 事件承载](../2026-11-12-context-collab-l4-resource-binding/spec.md)、[BBS 公开讨论产品](../2026-11-12-context-collab-bbs-public-discussion-product/spec.md)（随本 spec 同步修订）
- 协议依据：ACCS-104 §7"关键输入…必须固定版本或内容摘要"；§2 provenance

## 1. 目标与范围

"撤载体"演进的第二刀（第一刀是 ResourceBinding 事件承载）：**创建事实全量写入初始事件 payload**（事件因果记录从此完整），并确立**标题（objective）的读源合同 = 初始事件**；BBS 公开讨论产品 spec/plan 的 ContextSpec 读源句子随即修订。

不可回填性论证（为什么无读者也要先写）：事件流 append-only——**今天不写入，存量行永远缺失**，未来任何读径迁移都有不可弥补的洞。这与 store 不同（store 可随时补建），故 payload 全量化不适用"无读者先不建"的薄形态纪律。

**范围内**：
- ✅ 初始事件 payload 扩容：`method_kind` / `executor_ref` / `product_kind` / `workflow_kind`（objective、resource_refs 已在）；
- ✅ BBS 详情读源切换：title ← 初始事件 payload（NULL-payload 外部行回退伴随 spec，P3 撤列时移除）；
- ✅ BBS spec/plan 读源句子修订。

**范围外**（沿用既有裁决）：
- ❌ space / principal_ref 不进 payload——聚合已持久（`scope` / `initiator_ref`），事件不重复记录聚合级事实；
- ❌ method_kind / executor_ref 的**读依赖切换**（G1、L5 自愈源仍读伴随 spec；事实先入流，读方分批迁）；
- ❌ 列表读源切换（见 §3 裁决 2）；`context_spec` 列退役（P3）；wire/DDL 零变化。

## 2. payload 形态（新行）

```python
payload = {
    "objective": objective,                    # 第一天就有,存量行也有
    "resource_refs": [...],                   # ResourceBinding 轮已入
    "method_kind": spec.method_kind,           # 本轮新增
    "executor_ref": spec.executor_ref.to_dict() if ...,  # 本轮新增;None 恒写 null
    "product_kind": spec.product_kind,         # 本轮新增;None 恒写 null
    "workflow_kind": spec.workflow_kind,       # 本轮新增;None 恒写 null
}
```

新行恒带全部 key（与 ResourceBinding 轮"空也写"同款判别原则）；存量行无新增 key——读取侧只在新概念消费者出现时按 key 判别，objective / resource_refs 两键存量行也有。

## 3. 执行裁决（作者选定方向 B 后我落的两刀，可推翻）

1. **title 读源合同 = 初始事件**；消费者（BBS 详情）读 `events[0].payload["objective"]`；**NULL-payload 外部行回退**伴随 `context_spec.objective`（容错而非吞错——该行 spec JSON 明有标题；P3 撤列时移除回退）。
2. **列表不切换读源**（对既定方向"列表 join 初始事件"的修订）：列表的本质是 product_kind 认领过滤，该过滤必须解 collet 行 JSON；title 从同一 JSON 顺手给出。为 title 单独 join 事件表只增加第三条查询、零独立性收益——纯仪式。列表视图在 BBS spec 里如实标注"过渡期伴随读（P3 随过滤源升级）"。

## 4. 测试策略

| 面 | 覆盖 |
| --- | --- |
| 服务层 pin（更新） | `test_creates_collet_with_initial_event` payload 断言扩全量；空 refs 恒带空列表 key 用例随之全量断言 |
| 解析合同（追加） | objective 读源合同由 BBS 产品层测试钉（无本轮独立消费者，合同句子落在 BBS spec/plan） |
| 回归 | `context_collab` 目录 + repository + endpoints 全绿 |

## 5. BBS spec/plan 修订清单（随本 spec 一并落）

1. §2 域映射表"帖子标题"行：`ContextSpec.objective`（wire 既有字段）→ **初始事件 payload 的 `objective`**（读源新主；列表视图过渡期伴随读）；
2. §3.1 详情组装：title 读源 = 初始事件 payload，NULL-payload 回退伴随 spec（P3 移除）；
3. §4 `ContextSummaryRecord` 保持带 `context_spec` 伴随字段（过滤源 + 过渡期 title），加注说明；
4. plan Task 3 Interfaces 的投影规则同步改一行。
