# Workflow 评测模板参考

本文件是 clawbench-template skill 生成 Workflow E2E 评测模板时的**通用骨架与约束规范**。
适用于各类 workflow（线性 / 含分支 DAG），生成的模板供 benchmark.py（在线）或
eval_with_benchmark.py（离线）消费。

---

## 1. 模板 Frontmatter

```yaml
---
id: task_<workflow_id>_e2e
name: <Workflow Title> 端到端链路合规性评估
category: workflow
grading_type: hybrid
timeout_seconds: <按最长节点 timeout 之和 + 余量>
workspace_files: []
benchmark_kind: workflow          # ★ 标记为 workflow 模板，benchmark.py 按此分流
workflow_id: <workflowId>         # ★ 必填，benchmark.py 按此定位 embedded-sessions 目录
grading_weights:
  automated: 0.6
  llm_judge: 0.4
---
```

`benchmark_kind: workflow` 是识别 key，`workflow_id` 是 benchmark.py 定位
`<openclaw_home>/logs/clawmind/embedded-sessions/<workflowId>/<flowId>/` 的依据。

---

## 2. Merged Transcript 格式规范 (v2.1)

**transcript 由 workflow_merge.py 合并多节点 JSONL 生成。格式与 workflow_merge.py (v2.1) 严格对应。**

### 整体结构

```
第 1 条: __manifest__ event（元信息，含 workflow_trace）
第 2~N 条: 按节点执行顺序排列的 events，节点之间有 __node_boundary__ 分隔
```

### Event 类型

1. **`__manifest__`** — 第一条，元信息
   ```json
   {"type": "__manifest__", "format_version": "2.1", "nodes": ["node-a", "node-b"], "node_count": 2, "total_events": 42, "workflow_trace": {...}}
   ```
   - `nodes`: 当前 flow 实际执行的节点名列表（按 DAG 拓扑顺序）
   - `format_version`: `"2.1"` — 含 `workflow_trace` 字段
   - `workflow_trace` (v2.1 新增): workflow 主流程流转数据，结构如下：

   ```jsonc
   { "flow_id":"...","workflow_id":"...","status":"succeeded","goal":"...",
     "started_at":...,"ended_at":...,"triggered_by":"before_agent_run",
     "dag":[
       {"node":"prepare-input","executor":"cli-script","deps":[],"title":"准备输入",
        "skill_name":"","runtime_config":{}},
       {"node":"embedded-review","executor":"embedded-agent","deps":["prepare-input"],
        "title":"审查","skill_name":"review-skill","runtime_config":{"model":"sonnet","thinking":"high"}}
     ],
     "node_executions":[
       {"node_id":"prepare-input","executor_type":"cli-script","status":"succeeded",
        "attempt":1,"duration_s":0.4,"error":null,
        "output_keys":["topic","status"],"output":{"topic":"天气","status":"prepared"}},
       {"node_id":"embedded-review","executor_type":"embedded-agent","status":"succeeded",
        "attempt":1,"duration_s":3.1,"error":null,
        "output_keys":["summary"],"output":null},
       {"node_id":"finish","executor_type":"done","status":"succeeded",
        "attempt":1,"duration_s":0.1,"error":null,
        "output_keys":["done"],"output":{"done":true}}
     ],
     "timeline":[
       {"event_type":"node_started","node":"prepare-input","time":"2025-06-25T..."},
       {"event_type":"node_succeeded","node":"prepare-input","time":"2025-06-25T..."}
     ],
     "branches":[],
     "workflow_outputs":{
       "prepared":"...", "embeddedReview":"...",
       "subagentCheck":"...", "merged":"..."
     }
   }
   ```
   - `node_executions[]` **包含所有节点**（含 cli-script/done），不再限于 agent 节点
   - `output` 字段为 best-effort 多源采集（engine.db / JSONL 日志 / session 文件），可能为 null
   - `dag[]` 描述 YAML 声明的完整 DAG（含 executor 类型、依赖关系），**不仅本次执行的节点**
   - `workflow_outputs`：**workflow 级别**对外输出（来自 `state_json.workflowData.outputs` /
     `outputContract` 声明的最终产出）。与单节点 `output`/`output_keys` **层级不同**：
     `done`/`finish` 节点自身 output 仅 `{"done":true}`（output_keys=["done"]），workflow 的
     对外键（如 prepared/embeddedReview/subagentCheck/merged）在 `workflow_outputs`，**不要**
     混到任一节点（尤其 finish）的 `output_keys` 上检查——那是节点级 vs workflow 级的类别错误。

2. **`__node_boundary__`** — 节点分隔标记（第 2 个节点起，每个节点前一条）
   ```json
   {"type": "__node_boundary__", "__node__": "node-b", "__prev_node__": "node-a", "__node_index__": 1}
   ```

3. **`session`** — 会话初始化（仅第一个节点保留）
   ```json
   {"type": "session", "version": 1, "id": "...", "timestamp": "...", "__node__": "node-a"}
   ```

4. **`message`** — 对话消息（核心数据）
   ```json
   {"type": "message", "id": "...", "message": {"role": "user|assistant|toolResult", "content": [...]}, "__node__": "node-a"}
   ```
   - `role` 取值: `"user"` | `"assistant"` | `"toolResult"`
   - `content` 是列表，每项有 `type` 字段:
     - `{"type": "text", "text": "..."}` — 文本
     - `{"type": "thinking", "thinking": "..."}` — 思考过程
     - `{"type": "toolCall", "id": "...", "name": "tool_name", "arguments": {...}}` — 工具调用
   - `role="toolResult"` 时，content 为 `[{"type": "text", "text": "工具返回内容"}]`

5. **`custom`** — 自定义事件
   ```json
   {"type": "custom", "customType": "...", "data": {...}, "__node__": "node-a"}
   ```

### 关键约定

- 每条 event 都有 `__node__` 字段，标记所属节点
- `toolCall` 的 `arguments` 可能是 dict 也可能是 JSON string，需兼容处理
- `message` 事件结构是 `event["message"]["role"]` 和 `event["message"]["content"]`，不是 `event["role"]`
- 不要假设节点名称 —— 从 `manifest["nodes"]` 获取

---

## 3. Automated Checks — 通用 grade() 骨架

以下是**对各类 workflow 通用的 grade() 骨架**。业务断言（检查哪些工具/参数/输出字段）
由 clawbench-template skill 根据 workflow YAML + 各节点 SKILL.md + ground truth 填入。

### 3.1 核心设计原则（强制）

1. **板块制二值（Board-Gate Scoring）**：将检查项按节点分组为"板块"。每个板块为净二值
   gate——全过 → 板块 1.0，任一不过 → 0.0。**不使用 bonus/加分项**：单板块得分上限恒为
   1.0，加权聚合后总分限制在 0..1；想做更细的区分度把"做得更好"拆成独立 gate，不要叠加分
   （加分项会把单板块顶到 >1.0，经票数复制后均值破满）。
   **禁止均匀平均稀释失败信号**（板块制区分度 0.43 vs 均匀平均 0.12）。

2. **分支感知（Branch-Aware）**：含分支的 workflow，先用 `manifest["nodes"]` 判断
   本次实际执行的分支，只检查该分支上的节点。其他分支的节点用 `if node_name in node_entries`
   跳过（不扣分）。

3. **利用 `workflow_trace` 检查所有节点**（v2.1+）：`cli-script` 和 `done`
   类型节点的执行状态、输出键、输出值可通过 `manifest["workflow_trace"]["node_executions"]`
   直接获取。**不再需要从下游节点间接推断非 agent 节点的输出**。
   `subagent` 类型节点的 session 数据（工具调用、思考、输出）现已通过
   `workflow_trace["subagent_sessions"]` 自动发现并纳入 `node_entries`，
   可像 `embedded-agent` 节点一样做详细检查。对于旧数据
   （format_version ≤ 2.0 or workflow_trace 缺失），回退到仅检查 `node_entries` 中
   存在的节点。

4. **核心步骤布尔分**：关键工具调用/输出字段 → 0.0 或 1.0，不给部分分。
   非关键检查（如参数格式、多余步骤）可以给部分分。

### 3.2 骨架代码

```python
def grade(transcript: list, workspace_path: str = "") -> dict:
    """
    <Workflow Title> 端到端链路自动评分。

    板块制 + 分支感知 + 零出制。
    处理 workflow_merge.py 生成的合并 transcript（v2 格式）。

    Args:
        transcript: 合并后的 JSONL 记录列表（首行 __manifest__，每行含 __node__）
        workspace_path: 工作区路径（本场景不使用）

    Returns:
        dict: {检查项名称: 0.0~1.0 分数}
    """
    import json, re

    # ================================================================
    # 0. 解析 manifest + 按节点分组
    # ================================================================
    manifest = transcript[0] if transcript and transcript[0].get("type") == "__manifest__" else {}
    actual_nodes = manifest.get("nodes", [])

    node_entries = {}
    current_node = None
    for entry in transcript:
        if entry.get("type") == "__node_boundary__":
            continue
        node = entry.get("__node__", "")
        if node:
            current_node = node
            if node not in node_entries:
                node_entries[node] = []
        if current_node and entry.get("type") != "__node_boundary__":
            node_entries[current_node].append(entry)

    # ================================================================
    # 0a. 分支检测（★ 含分支的 workflow 必须做，线性 workflow 可跳过）
    # ================================================================
    # 根据 manifest["nodes"] 判断本次走的分支。
    # 示例（emergency-duty-agent-router）:
    #   is_analysis  = "analysis-prepare" in node_entries
    #   is_handling  = "handling-process" in node_entries
    #   is_boundary  = "boundary-reply" in node_entries and not is_analysis
    # 示例（workflow-dispatcher）:
    #   is_normal    = "context-enrichment" in node_entries
    #   is_boundary  = "boundary-reply" in node_entries and not is_normal

    # ================================================================
    # 1. 可复用 helper 函数
    # ================================================================

    def extract_exec_commands(entries):
        """从节点的 assistant toolCall 中提取 exec 命令列表。"""
        cmds = []
        for entry in entries:
            if entry.get("type") != "message":
                continue
            msg = entry.get("message", {})
            if msg.get("role") != "assistant":
                continue
            for c in msg.get("content", []):
                if c.get("type") == "toolCall" and c.get("name") == "exec":
                    args = c.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if isinstance(args, dict):
                        cmd = args.get("command", "")
                        if cmd:
                            cmds.append(cmd)
        return cmds

    def extract_read_paths(entries):
        """从节点的 assistant toolCall 中提取 read 的文件路径列表。"""
        paths = []
        for entry in entries:
            if entry.get("type") != "message":
                continue
            msg = entry.get("message", {})
            if msg.get("role") != "assistant":
                continue
            for c in msg.get("content", []):
                if c.get("type") == "toolCall" and c.get("name") == "read":
                    args = c.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if isinstance(args, dict):
                        path = args.get("path", "")
                        if path:
                            paths.append(path)
        return paths

    def get_final_json(entries):
        """从节点的 assistant text 中提取最后一个合法 JSON 对象。"""
        assistant_texts = []
        for entry in entries:
            if entry.get("type") != "message":
                continue
            msg = entry.get("message", {})
            if msg.get("role") == "assistant":
                for c in msg.get("content", []):
                    if c.get("type") == "text":
                        text = c.get("text", "").strip()
                        if text:
                            assistant_texts.append(text)
        for text in reversed(assistant_texts):
            if text.startswith("{"):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    # brace matching fallback
                    depth = 0
                    end = 0
                    for i, ch in enumerate(text):
                        if ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                break
                    if end > 0:
                        try:
                            return json.loads(text[:end])
                        except json.JSONDecodeError:
                            pass
        return None

    # ================================================================
    # 0b. 解析 workflow_trace（v2.1+），获取所有节点执行数据
    # ================================================================
    trace = manifest.get("workflow_trace", {})
    trace_execs = {
        n["node_id"]: n
        for n in trace.get("node_executions", [])
        if isinstance(n, dict)
    }

    def get_trace_node_status(node_id):
        """从 workflow_trace 获取节点执行状态（含 cli-script/done）。"""
        ex = trace_execs.get(node_id, {})
        return ex.get("status")

    def get_trace_node_output(node_id):
        """从 workflow_trace 获取节点输出 dict（含 cli-script/done）。

        trace 中的 output 来自多源 best-effort 采集（engine.db / JSONL 日志 /
        session 文件）。可能为 None（全源失败时）。
        """
        ex = trace_execs.get(node_id, {})
        return ex.get("output")

    def get_trace_node_output_keys(node_id):
        """从 workflow_trace 获取节点输出键列表。"""
        ex = trace_execs.get(node_id, {})
        return ex.get("output_keys", [])

    def get_workflow_outputs():
        """workflow 级别对外输出 dict（state_json.workflowData.outputs / outputContract）。

        与单节点 output（get_trace_node_output）**层级不同**：这是整个 workflow 暴露给
        调用方的最终产出。done/finish 节点自身 output 仅为 {"done": true}，不携带这些键。
        缺失（采集失败 / 旧数据）返回 {}，调用方应按「采集缺失 → 豁免」处理，不直接判 0。
        """
        wf = trace.get("workflow_outputs")
        return wf if isinstance(wf, dict) else {}

    def node_in_trace(node_id):
        """该节点是否在 workflow_trace 有记录。

        关键：区分「trace 全局可用」与「本节点存在」。纯 trace 依赖板块
        （cli-script/done）用它判断能否评测；trace 全局有数据但缺本节点
        时 → 板块豁免（不扣分），而不是判 0。
        """
        ex = trace_execs.get(node_id)
        return isinstance(ex, dict) and bool(ex)

    def node_succeeded(node_id):
        """节点是否成功执行（不返回 None）。

        trace 有记录看 status；无记录但 agent 节点在 node_entries 有 session
        也算成功；其余 False。**不返回三态 None**：旧版 ``trace_node_succeeded``
        在「未知」时返回 None，被 ``1.0 if ... else 0.0`` 消费后变 0 → 板块误判
        归零。本版消除该 footgun。
        """
        ex = trace_execs.get(node_id)
        if ex:
            return ex.get("status") == "succeeded"
        if node_id in node_entries and node_entries[node_id]:
            return True
        return False

    def node_executed(node_id):
        """节点是否已执行（不返回 None）。

        trace 有记录 或 node_entries 有 session 任一为真即 True。
        agent 节点的存在性门禁统一用本函数，不要用 ``trace_node_*``。
        """
        if node_in_trace(node_id):
            return True
        return node_id in node_entries and bool(node_entries[node_id])

    def extract_tool_names(entries):
        """返回节点调用的所有工具名列表（不去重）。"""
        names = []
        for entry in entries:
            if entry.get("type") != "message":
                continue
            msg = entry.get("message", {})
            if msg.get("role") != "assistant":
                continue
            for c in msg.get("content", []):
                if c.get("type") == "toolCall":
                    names.append(c.get("name", ""))
        return names

    # ================================================================
    # 2. 板块制评分
    # ================================================================
    # 每个板块 = 一个节点 OR 一组关联检查
    # 板块为净二值 gate：全过 → 板块得分 = 1.0；任一失败 → 0.0。
    # 【不使用 bonus/加分项】单板块得分上限恒为 1.0，避免加权聚合后
    # 总分突破 0..1 上限（ enthusiastically 加分会把单板块顶到 3.0，
    # 经票数复制后均值 >1.0，最终 hybrid 破满）。
    #
    # 想体现"做得更好"→ 拆成独立 gate（如把 outer 字段单独列为
    # OA-04 gate），而不要叠加分。框架 lib_grading 仍对单值 min(1.0)
    # 兜底，但模板层不应依赖兜底。
    #
    # 最终分数 = 各板块得分的加权平均
    #
    # 命名规范: <板块前缀>-<序号>_<描述>
    #   板块前缀: CE (context-enrichment), IR (intent-recognition),
    #             TD (task-dispatch), WF (跨节点), E2E (全链路)
    # ================================================================

    raw = {}   # {key: 0.0|1.0}
    gates = {} # {key: bool}  True=通过, False=失败
    exempt_weights = set()  # 记录被豁免（权重归零）的板块名，加权聚合计 total_weight 时扣除

    # ====================================================================
    # ★★★ 节点板块标准写法（必须照此两式之一，禁用反模式）★★★
    # gate 条件【不得】直接包装 trace_node_* 的返回值（旧 trace_node_succeeded 会
    # 返 None，被 `1.0 if ... else 0.0` 消费后变 0 → 板块误判归零）。
    # 按节点类型选下面 agent / 非 agent 两种写法。
    # ====================================================================

    # ---------- 模式 A: agent 节点板块（embedded-agent / subagent）----------
    # 存在性门禁用 node_executed()（永不返 None）；内容优先从 node_entries 取，
    # 降级到 get_trace_node_output()（state_json.result 权威源）。
    # subagent 的独立 session JSONL 可能未被 merge 捕获，此时 trace output
    # 兜底确保评分不受 merge 覆盖率影响。
    #
    # ★单 gate 的数据源兜底（重要，避免误判 0 分）：
    #   能从 trace output 验的 gate（字段存在/JSON 合法）用 trace，不依赖 merge。
    #   只能从 merge session 验的 gate（如工具调用痕迹、调参），若 merge 完全
    #   没覆盖该节点（node_entries 无 message 事件，纯 subagent 合成占位），
    #   该 gate 标 "N/A" 并加入 exempt_weights，聚合时排除该 gate，绝不判 0。
    #   判 merge 覆盖用：bool(node_entries.get(nid)) 且含 message 事件。
    if not node_executed("embedded-review"):
        board_ER_weight = 0          # 真执行缺失 → 0 分（不是 trace 缺失）
        exempt_weights.add("ER")
    else:
        er_entries = node_entries.get("embedded-review", [])
        er_json = get_final_json(er_entries) or get_trace_node_output("embedded-review") or {}
        raw["ER-01_output_present"] = 1.0 if er_json else 0.0
        gates["ER-01_output_present"] = raw["ER-01_output_present"] > 0

        # ER-02 只能从 trace output 验 → 不受 merge 影响，无需兜底
        raw["ER-02_mode"] = 1.0 if er_json.get("mode") == "embedded-agent" else 0.0
        gates["ER-02_mode"] = raw["ER-02_mode"] > 0

        # 示例：一个只能从 merge session 验的 gate（如工具调用），未覆盖时豁免
        # er_has_merge = bool(er_entries) and any(e.get("type") == "message" for e in er_entries)
        # if er_has_merge:
        #     raw["ER-03_tool_used"] = 1.0 if extract_tool_names(er_entries) else 0.0
        #     gates["ER-03_tool_used"] = raw["ER-03_tool_used"] > 0
        # else:
        #     raw["ER-03_tool_used"] = "N/A"   # merge 未覆盖 → 豁免不判 0
        #     exempt_weights.add("ER-03_tool_used")  # 用完整 gate key，与 gate_keys 过滤对齐

        board_ER_gate_keys = [k for k in ["ER-01_output_present", "ER-02_mode"] if k not in exempt_weights]
        board_ER_passed = all(gates.get(k, False) for k in board_ER_gate_keys)
        board_ER_score = 1.0 if board_ER_passed else 0.0
        board_ER_weight = 3

    # ---------- 模式 B: 非 agent 节点板块（cli-script / done）----------
    # 纯 trace 依赖。trace 缺该节点时【整个板块豁免（权重归零）】，绝不判 0。
    if not node_in_trace("prepare-input"):
        board_PI_weight = 0          # 采集缺失 → 豁免，不扣分
        exempt_weights.add("PI")
    else:
        pi_output = get_trace_node_output("prepare-input") or {}
        raw["PI-01_status"] = 1.0 if pi_output.get("status") == "prepared" else 0.0
        gates["PI-01_status"] = raw["PI-01_status"] > 0
        raw["PI-02_topic"] = 1.0 if pi_output.get("topic") else 0.0
        gates["PI-02_topic"] = raw["PI-02_topic"] > 0
        board_PI_gate_keys = ["PI-01_status", "PI-02_topic"]
        board_PI_passed = all(gates[k] for k in board_PI_gate_keys if k in gates)
        board_PI_score = 1.0 if board_PI_passed else 0.0
        board_PI_weight = 1

    # ---------- 模式 B (done 节点): finish ----------
    # done/finish 节点自身 output 仅 {"done": true}（output_keys=["done"]），它不携带
    # workflow 的对外产出。workflow 级键（outputContract 声明的 prepared/.../merged）
    # 在 trace["workflow_outputs"]，用 get_workflow_outputs() 取。
    # ★ 类别错误防误判：禁止用 get_trace_node_output_keys("finish") 去比 workflow 级键
    #   ——finish 的节点级键只有 ["done"]，永凑不齐 workflow 级 4 键 → 板块恒挂 0 分。
    if not node_in_trace("finish"):
        board_FN_weight = 0          # 采集缺失 → 豁免，不扣分
        exempt_weights.add("FN")
    else:
        fn_output = get_trace_node_output("finish") or {}
        raw["FN-00_done"] = 1.0 if fn_output.get("done") is True else 0.0
        gates["FN-00_done"] = raw["FN-00_done"] > 0
        board_FN_gate_keys = ["FN-00_done"]
        # workflow 级对外输出（4 个 outputContract 键），与 finish 节点输出无关。
        # workflow_outputs 为空（采集缺失）→ 跳过本 gate 不扣分，归 exemption。
        wf_outputs = get_workflow_outputs()
        if wf_outputs:
            expected_wf_keys = {"prepared", "embeddedReview", "subagentCheck", "merged"}
            missing_wf = expected_wf_keys - set(wf_outputs.keys())
            raw["FN-01_all_outputs"] = 1.0 if not missing_wf else 0.0
            gates["FN-01_all_outputs"] = raw["FN-01_all_outputs"] > 0
            board_FN_gate_keys.append("FN-01_all_outputs")
        board_FN_passed = all(gates[k] for k in board_FN_gate_keys if k in gates)
        board_FN_score = 1.0 if board_FN_passed else 0.0
        board_FN_weight = 2

    # ---------- 分支感知：与当前分支无关的节点跳过 ----------
    # if "node-b-name" in node_entries and node_entries["node-b-name"]:
    #     ... 按模式 A 或 B 编写 ...
    # else:
    #     board_B_weight = 0  # 未执行该分支节点，跳过不扣分
    #     exempt_weights.add("B")

    # ---------- 跨节点数据流板块 ----------
    # [由 skill 根据 workflow YAML 的 {{nodeOutput.xxx.yyy}} 引用关系填入]
    # 节点输出来源（v2.1+）：
    #   - agent 节点：优先 node_entries 的 get_final_json()，兜底 get_trace_node_output()
    #   - 非 agent 节点：get_trace_node_output()
    # 示例：cli-script 的输出是否被下游正确消费
    #   pi_output = get_trace_node_output("prepare-input") or {}
    #   er_json = get_final_json(node_entries.get("embedded-review", [])) or {}
    #   raw["WF-01_topic_passed"] = 1.0 if er_json.get("topic") == pi_output.get("topic") else 0.0
    #   gates["WF-01_topic_passed"] = raw["WF-01_topic_passed"] > 0
    #
    # 注意：跨节点板块若依赖的节点全部豁免，则该板块也应豁免（权重归零），
    # 不应单凭节点输出缺失判 0。

    # ================================================================
    # 3. 加权聚合
    # ================================================================
    #
    # ★ 豁免板块（trace 采集缺失 / 未执行分支节点）的 weight 计 0，且【不参与】
    #   total_weight 与加权平均——即按实际可评测的板块重新归一，而非用 0 分
    #   拉低总分。exempt_weights 记录被豁免的板块前缀。
    #
    # total_weight = sum(w for name, w in [("A",5),("B",3),("C",2)] if name not in exempt_weights)
    # final_scores = {}
    # for each board (weight>0 且非豁免):
    #     for i in range(board_weight):  # 用重复票数实现整数权重
    #         final_scores[f"<板块名>[{i+1}]"] = board_score
    # return final_scores
    #
    # ── 示例（3 板块，权重 5:3:2 = 10 票；B 豁免 → 实际 5+2=7 票归一）──
    # scores = {}
    # if "A" not in exempt_weights:
    #     for i in range(5):
    #         scores[f"boardA[{i+1}]"] = score_A
    # if "B" not in exempt_weights:
    #     for i in range(3):
    #         scores[f"boardB[{i+1}]"] = score_B
    # if "C" not in exempt_weights:
    #     for i in range(2):
    #         scores[f"boardC[{i+1}]"] = score_C
    # return scores

    return {}  # 占位，由 skill 替换为实际实现
```

### 3.3 板块权重指南

| 位置 | 建议权重 | 说明 |
|---|---|---|
| 首个 embedded-agent 节点 | 30-40% | 输入解析/上下文整合，错误会级联 |
| 中游节点 | 各 15-25% | 核心业务逻辑 |
| 末尾节点 | 15-25% | 分发/持久化，副作用操作关键 |
| 跨节点数据流 | 10-15% | 节点间数据传递正确性 |

### 3.4 分支处理模式

**线性 workflow**（如 workflow-dispatcher 的 normal 分支）:
- 所有节点都在一条路径上,直接检查即可。

**含分支 workflow**（如 emergency-duty-agent-router）:
- grade() 开头做分支检测（见骨架 0a）。
- 与当前分支无关的节点检查，用 `if node_name in node_entries`
  跳过（不设 gate，不扣分）。
- **分支检测依据**: `manifest["nodes"]`（workflow_merge.py 只列出实际执行节点）。

**clawbench-template 生成模板时的分支处理**:
1. 解析 workflow YAML 的 `onResult.branches[].branchId` + 各节点 `branchId` →
   识别所有分支路径。
2. 如果用户指定了评测分支 → 只生成该分支的检查项。
3. 如果用户提供了 sample session → 从 `manifest["nodes"]` 自动推断分支。
4. 如果用户未指定分支且无 sample → 生成全分支模板（每个分支的节点都有
   `if node_name in node_entries` 守卫）。

---

## 4. LLM Judge Rubric

### 4 个固定维度（各类 workflow 通用）

#### Criterion 1: 链路完整性 (Weight: 25%)

评估 workflow 各节点是否按正确顺序执行，无跳步、无遗漏。

**Score 1.0**: 所有节点按 DAG 顺序完整执行，无跳过、无重复，分支判断正确。
**Score 0.75**: 核心节点完整，存在非关键节点的轻微顺序偏差。
**Score 0.5**: 缺少一个关键节点或分支判断错误。
**Score 0.25**: 缺少多个关键节点或执行顺序严重错误。
**Score 0.0**: 链路严重断裂或完全未执行。

#### Criterion 2: 工具调用规范性 (Weight: 30%)

评估各节点是否调用了正确的脚本/工具，参数是否完整，是否遵循 SKILL 规范。

**Score 1.0**: 所有节点工具调用完全正确。脚本选择、参数完整性均符合 SKILL 规范，无废弃脚本。
**Score 0.75**: 核心脚本调用正确，个别参数格式轻微偏差。
**Score 0.5**: 存在明显问题（如缺少关键参数、使用了废弃脚本）。
**Score 0.25**: 多个节点工具调用有问题。
**Score 0.0**: 工具调用严重错误或完全未调用必要脚本。

#### Criterion 3: 输出质量 (Weight: 25%)

评估各节点输出 JSON 的格式正确性、字段完整性、值域合法性。

**Score 1.0**: 所有节点输出合法 JSON，字段完整，类型正确，值在允许范围内。无 Markdown code fence。
**Score 0.75**: 输出基本正确，存在轻微格式偏差或非关键字段缺失。
**Score 0.5**: 缺少关键字段或类型不匹配，但核心信息可用。
**Score 0.25**: JSON 格式错误或关键字段严重缺失。
**Score 0.0**: 输出非 JSON 或完全无法解析。

#### Criterion 4: 错误处理与边界条件 (Weight: 20%)

评估 workflow 对边界条件（boundary_triggered）和异常情况的处理。

**Score 1.0**: boundary 触发时正确进入对应分支。无重复调用、无幻觉编造。错误有合理处理。
**Score 0.75**: 边界处理基本正确，存在轻微偏差。
**Score 0.5**: 边界处理有问题但未导致严重后果。
**Score 0.25**: 边界处理错误（如 boundary 触发时仍执行完整流程）。
**Score 0.0**: 完全忽略边界条件或产生严重副作用。

**Weight 总和 = 100%**（25+30+25+20=100）。

---

## 5. 与 SKILL.md 的关系

本文件提供**骨架、约束和格式规范**。生成流程（输入解析、节点分类、检查项派生、
板块划分）的决策逻辑全部在 clawbench-template 的 SKILL.md（## Workflow 评测模板 章节）。

两个文件的职责分界：
- **SKILL.md**：告诉 Agent「怎么生成」—— 系统化的 6 步流程、检查项派生规则、
  权重分配策略、Ground Truth 注入方式
- **本文件**：告诉 Agent「按照什么格式输出」—— 模板 Frontmatter、grade() 骨架、
  helper 函数签名、LLM Judge Rubric 固定模板、代码规范

生成模板时，先理解 SKILL.md 的流程完成分析，再在本文件的骨架中填入具体内容。

---

## 6. 代码规范（极其重要）

- **绝对不要在 grade() 代码中使用三个反引号**（会破坏 Markdown 解析）
- `message` 事件结构: `entry["message"]["role"]` 和 `entry["message"]["content"]`
- `content` 是列表，每项有 `type` 字段（`text` / `toolCall` / `thinking`）
- `toolCall` 项的 `arguments` 可能是 dict 或 JSON string，需兼容处理
- `role="toolResult"` 不是 `"tool"`
- 提取 exec 命令的正确方式: 遍历 content 找 `type=="toolCall"` 且 `name=="exec"` 的项
- 从 `manifest["nodes"]` 获取节点名和顺序，**不要硬编码**
- 板块制: gate 全部通过 → 板块得分；gate 任一失败 → 板块 0 分