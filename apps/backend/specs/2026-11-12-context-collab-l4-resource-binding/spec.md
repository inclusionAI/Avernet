# Context Collab L4 ResourceBinding 升格（事件承载）

- 日期：2026-11-12
- 状态：Accepted（薄形态 + 全部裁决经作者确认 2026-11-12）
- 拥有模块：Backend `agentclaw.community.core.task.context_collab`
- 目标消费者：ContextCollabService 创建路径、ResourceBinding 投影/自愈缝、DI、context-collab tests
- 关联设计：[L4 Collet ACCS 对齐](../2026-11-12-context-collab-l4-collet-accs-alignment/spec.md)、[L5 method objects](../2026-11-12-context-collab-l5-method-objects/spec.md)
- 协议依据：ACCS-104 §2“ResourceBinding：Resource 被带入 Collet 的上下文绑定，**不是 Grant**”；§8.4“ResourceBinding 不被当作 Grant”

## 1. 目标与范围

上一轮 L4 对齐把 ContextSpec 的六字段权威卸给了 Collet 聚合，但 `resource_refs` 仍压在 `context_spec` JSON 里，ACCS-104 的一等对象 **ResourceBinding** 在实现中不存在。**正主 = 事件流本身**：本 spec 让创建资源事实完整落进事件因果记录，ResourceBinding 立为 L4 值对象 + 解析纯函数——零新表、零新列、零 wire 变化、零新 DI 面。

**薄形态**（作者裁决 2026-11-12；比 store 方案再瘦身一档）：

- ✅ 初始事件 payload 扩容携带 `resource_refs`（事件承载，权威落点）；
- ✅ `ResourceBinding` L4 值对象 + `resource_bindings_from_initial_event` 纯函数解析（第一批读方出现前的解析合同）；
- ❌ （P2，等第一个真读方）：`InMemoryResourceBindingStore`（缓存，无读者即死代码）、`ensure_*` 自愈缝与存量行 `context_spec` 回退、DI 绑定、读路径接线——预留读者顺位：bbs_help_request 产品面 > Artifact 操作族 > G1 策略审计；
- ❌ （本轮不做，维持前裁决）：初始事实全量事件化（method_kind / executor_ref / product_kind 不进 payload 扩容）、objective / method_kind 读依赖切换、`context_spec` JSON 物理降格（P3）、bind/unbind 操作族与独立 ID/版本/状态机（P2）。

**兼容边界**：两张表、全部列、既有迁移、三个北向 API wire、`ContextSpec` 值对象与落库 JSON 形态**全部不动**——本轮只做"加法正主"，不做减法撤除；存量行的 payload 无 `resource_refs` key，读投影走一次回退（见 §3）。

## 2. 初始事件 payload 扩容（事件承载）

`context_collab_service.py` 创建路径构造 `InitialEventDraft` 处（现 `payload={"objective": objective}`）扩为：

```python
payload={
    "objective": objective,
    "resource_refs": [ref.to_dict() for ref in spec.resource_refs],
}
```

- **新行恒带 key**（空引用也写 `"resource_refs": []`）——"有 key 空列表"与"存量行无 key"可判别；
- payload 是 DB 内部形状而非 wire 合同，扩容不触发 conformance 变更；
- 事件流的因果记录从此完整含创建时的资源绑定事实。

## 3. ResourceBinding 值对象 + 解析函数（新文件 `l4_context_primitives/resource_binding.py`）

```python
@dataclass(frozen=True)
class ResourceBinding:
    """Resource 被带入 Collet 的上下文绑定(ACCS-104 §2;不是 Grant)。
    P1 是创世事实的解析片段:无独立 ID/版本/状态(bind/unbind 操作族与
    绑定级审计属 P2);G1 资源授权永远逐资源回所属 Space 的 Grant/Policy,
    ResourceBinding 自身不产生任何权限。"""
    resource_ref: ResourceRef
    # from_dict/to_dict:单键 resource_ref;未知键拒绝(沿 collet.py 模块风格)

def resource_bindings_from_initial_event(event: EventRecord) -> tuple[ResourceBinding, ...]:
    """解析合同(第一批读方出现前先钉死):payload 必须是 dict 且含
    resource_refs 列表;key 缺失 / payload 为 NULL / 列表项非法 →
    ContextCollabValidationError 响亮失败,NTBF(不 try/except 兜底伪装存量行)。"""
```

判定语义（builder check Q5 裁决）：本函数只认事件事实，**不含**对存量行的 `context_spec` 回退——那是 P2 自愈缝出现时的职责（届时以 `"resource_refs" in payload` 探针区分存量/新行，垃圾 payload 必须响亮失败）。

**明确不做**：store、ensure 缝、services 接线、DI 绑定、`ContextCollabService` 构造签名——零改动；`ResourceBinding 不是 Grant`，G1 面零触碰。

## 4. 测试策略

| 文件 | 覆盖 |
| --- | --- |
| `tests/.../context_collab/test_resource_binding.py`（新建） | 值对象 round-trip / 未知键拒绝；解析函数正反例（合法 payload → 绑定元组；key 缺失 / NULL payload / 列表项非法 → `ContextCollabValidationError`） |
| `test_context_collab_service.py` 既有 pin（约 :192 `payload == {"objective": OBJECTIVE}`） | 机械适配新形态：`resource_refs: []` 恒在 |
| `tests/community/repository/task/context_collab/`（追加） | 真 SQLite：带 `resource_refs` create 后初始事件 payload 含序列化引用；空 refs 也带空列表 key |

按需只跑 `context_collab` 目录 + repository;CI 门禁延后统一处理。

## 5. 已裁定（作者 2026-11-12；实现者不需再问）

1. **薄形态**：正主 = 事件流本身；store/ensure/DI/接线全部推迟到第一个真读方（预留顺位 bbs_help_request 产品面 > Artifact 族 > G1 审计），解析合同先钉死;
2. 解析函数对 key 缺失 / NULL payload / 非法项一律响亮失败，存量行回退属 P2 自愈缝职责;
3. 初始事件 payload 是 DB 内部形状而非 wire 合同；新行恒带 `resource_refs` key（空即 `[]`）,与存量行（无 key）判别有据;
4. order:先本演进、后执行 BBS 公开讨论产品计划——该计划读源（title←context_spec.objective）不受本轮影响,payload 扩容对它是白送的普适行为,计划无需改动。
