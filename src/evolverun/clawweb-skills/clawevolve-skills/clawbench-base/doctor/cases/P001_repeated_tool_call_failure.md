# P001: 工具调用反复失败 - 完整案例

## 案例概述

**问题模式**: P001 - Repeated Tool Call Failure (工具调用反复失败)

**场景**: Agent 在调用 MCP 工具时，连续多次使用错误的参数，直到第 N 次才成功。

**影响**:
- 浪费 API 调用额度
- 增加任务执行时间
- 降低成功率

---

## 测试数据准备

### 1. 创建 Transcript 文件

文件: `results/case_p001_demo_transcripts/task_yuque_workflow.jsonl`

```jsonl
{"type":"session","version":3,"id":"case-p001-001","timestamp":"2026-04-14T10:00:00.000Z","cwd":"/tmp/test"}
{"type":"model_change","id":"model-001","timestamp":"2026-04-14T10:00:00.001Z","provider":"antchat","modelId":"Kimi-K2.5"}
{"type":"message","id":"msg-001","timestamp":"2026-04-14T10:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"搜索包含'规范'的文档，并获取第一个文档的详情"}]}}
{"type":"message","id":"msg-002","timestamp":"2026-04-14T10:00:02.000Z","message":{"role":"assistant","content":[{"type":"thinking","thinking":"用户要求搜索文档然后获取详情。我需要先调用 skylark_search 搜索文档，然后用搜索结果中的 doc_id 调用 skylark_doc_detail。"},{"type":"toolCall","id":"tc-001","name":"skylark_search","arguments":{"q":"规范","limit":10}}]}}
{"type":"message","id":"msg-003","timestamp":"2026-04-14T10:00:03.000Z","message":{"role":"toolResult","toolCallId":"tc-001","toolName":"skylark_search","content":[{"type":"text","text":"{\"docs\": [{\"id\": 12345, \"title\": \"API规范文档\", \"book_id\": 67890, \"slug\": \"api-spec\"}]}"}],"isError":false}}
{"type":"message","id":"msg-004","timestamp":"2026-04-14T10:00:04.000Z","message":{"role":"assistant","content":[{"type":"thinking","thinking":"搜索结果返回了一个文档。现在我需要获取详情。但是我不确定 doc_id 应该填什么..."},{"type":"toolCall","id":"tc-002","name":"skylark_doc_detail","arguments":{"doc_id":null}}]}}
{"type":"message","id":"msg-005","timestamp":"2026-04-14T10:00:05.000Z","message":{"role":"toolResult","toolCallId":"tc-002","toolName":"skylark_doc_detail","content":[{"type":"text","text":"Error: doc_id is required and must be a valid integer"}],"isError":true}}
{"type":"message","id":"msg-006","timestamp":"2026-04-14T10:00:06.000Z","message":{"role":"assistant","content":[{"type":"thinking","thinking":"出错了，doc_id 不能为空。让我再试一次。"},{"type":"toolCall","id":"tc-003","name":"skylark_doc_detail","arguments":{"doc_id":null}}]}}
{"type":"message","id":"msg-007","timestamp":"2026-04-14T10:00:07.000Z","message":{"role":"toolResult","toolCallId":"tc-003","toolName":"skylark_doc_detail","content":[{"type":"text","text":"Error: doc_id is required and must be a valid integer"}],"isError":true}}
{"type":"message","id":"msg-008","timestamp":"2026-04-14T10:00:08.000Z","message":{"role":"assistant","content":[{"type":"thinking","thinking":"还是失败。让我再仔细看搜索结果...哦，id 字段在 docs[0].id！"},{"type":"toolCall","id":"tc-004","name":"skylark_doc_detail","arguments":{"doc_id":12345}}]}}
{"type":"message","id":"msg-009","timestamp":"2026-04-14T10:00:09.000Z","message":{"role":"toolResult","toolCallId":"tc-004","toolName":"skylark_doc_detail","content":[{"type":"text","text":"{\"id\": 12345, \"title\": \"API规范文档\", \"content\": \"...\"}"}],"isError":false}}
```

### 2. 创建结果文件

文件: `results/case_p001_demo_antchat-kimi-k2-5.json`

```json
{
  "model": "antchat/Kimi-K2.5",
  "benchmark_version": "1.2.1",
  "run_id": "case_p001_demo",
  "timestamp": 1778900000.0,
  "suite": "yuque_bench",
  "runs_per_task": 1,
  "tasks": [
    {
      "task_id": "task_yuque_workflow",
      "status": "success",
      "timed_out": false,
      "execution_time": 10.5,
      "transcript_length": 9,
      "usage": {
        "input_tokens": 1500,
        "output_tokens": 800,
        "total_tokens": 2300,
        "request_count": 5
      },
      "workspace": "/tmp/test",
      "grading": {
        "runs": [
          {
            "task_id": "task_yuque_workflow",
            "score": 0.5,
            "max_score": 1.0,
            "grading_type": "automated",
            "breakdown": {
              "search_called": 1.0,
              "detail_called": 1.0,
              "correct_params": 0.0
            },
            "notes": "Called tools correctly but had parameter issues"
          }
        ],
        "mean": 0.5,
        "std": 0.0,
        "min": 0.5,
        "max": 0.5
      },
      "frontmatter": {
        "id": "task_yuque_workflow",
        "name": "YuQue Search and Detail",
        "category": "mcp",
        "grading_type": "automated",
        "timeout_seconds": 120,
        "workspace_files": []
      }
    }
  ]
}
```

---

## 执行诊断

### 运行命令

```bash
cd doctor
python run.py --run-id case_p001_demo --model antchat-kimi-k2-5 --verbose
```

### 执行过程

```
16:14:06 - INFO - 🏥 Doctor: Starting diagnosis for run case_p001_demo, model antchat-kimi-k2-5
16:14:06 - INFO - 📁 Loading results from: results/case_p001_demo_antchat-kimi-k2-5.json
16:14:06 - INFO - 📁 Loading transcripts from: results/case_p001_demo_transcripts
16:14:06 - INFO - 🔍 Diagnosing task: task_yuque_workflow (score=0.50, status=success)
16:14:06 - INFO - 🔧 Generating optimization patches...
16:14:06 - INFO - ✅ Diagnosis complete: 1 tasks, 1 issues, 2 patches
16:14:06 - INFO - 📄 Saved diagnosis JSON: doctor/output/antchat-kimi-k2-5/case_p001_demo/diagnosis.json
16:14:06 - INFO - 📄 Saved 2 patches to: doctor/output/antchat-kimi-k2-5/case_p001_demo/patches
16:14:06 - INFO - 📄 Saved summary: doctor/output/antchat-kimi-k2-5/case_p001_demo/diagnosis_summary.md
```

---

## 诊断结果

### 摘要

| 指标 | 数值 |
|------|------|
| 诊断任务数 | 1 |
| 发现问题数 | **1** |
| 生成补丁数 | **2** |
| 问题模式 | **P001** - 工具调用反复失败 |
| 严重度 | 🔴 **HIGH** |

### 发现的问题

**问题 ID**: `e78768c0`
**模式**: P001 - repeated_tool_call_failure
**工具**: `skylark_doc_detail`
**描述**: 工具 'skylark_doc_detail' 连续调用失败 2 次

**错误详情**:
- **错误信息**: `Error: doc_id is required and must be a valid integer`
- **失败次数**: 2 次
- **失败参数**: `{doc_id: null}`
- **成功参数**: `{doc_id: 12345}`
- **根本原因**: 参数缺失或为空: doc_id

**证据分析**:
```json
{
  "failure_count": 2,
  "success_count": 1,
  "error_messages": [
    "Error: doc_id is required and must be a valid integer",
    "Error: doc_id is required and must be a valid integer"
  ],
  "failed_parameters": [
    {"doc_id": null},
    {"doc_id": null}
  ],
  "successful_parameters": {
    "doc_id": 12345
  },
  "root_cause": "参数缺失或为空: doc_id"
}
```

---

## 生成的优化补丁

### 补丁 1: Skill 优化建议

**文件**: `patches/skill/patch_001_skylark_doc_detail.yaml`

```yaml
# Skill Optimization Patch
patch_id: "patch_P001_skylark_doc_detail"
patch_type: "skill"
created_at: "2026-04-14T16:14:06.201034"

related_issues:
  - e78768c0

target:
  mcp_server: "skylarkmcpserver"
  tool_name: "skylark_doc_detail"

action:
  type: "append_description"
  content: |
    ## 常见错误提示

    ### 问题: 参数缺失或为空: doc_id

    **错误现象**: 连续 2 次调用失败

    **失败参数**:
    ```json
    {
      "doc_id": null
    }
    ```

    **成功参数**:
    ```json
    {
      "doc_id": 12345
    }
    ```

    ### 参数使用指南

    - **doc_id**: 必填参数，不能为空。
    - **来源**: 从 `skylark_search` 返回结果的 `docs[].id` 字段获取
    - **示例**: `doc_id=12345`

verification:
  affected_tasks: task_yuque_workflow
  expected_improvement: "减少 'skylark_doc_detail' 工具调用的参数错误"

status: "pending"
```

**优化建议说明**:
1. 在 `skylark_doc_detail` 的描述中明确标注必填参数
2. 提供失败 vs 成功参数对比示例
3. 说明参数应从何处获取

### 补丁 2: Memory 优化建议

**文件**: `patches/memory/patch_002.md`

```markdown
---
patch_id: "patch_P001_memory_skylark_doc_detail"
patch_type: "memory"
memory_type: "session"
related_issues:
  - e78768c0
status: "pending"
---

# Suggested Session Memory

### skylark_doc_detail 参数注意事项

调用 `skylark_doc_detail` 时，以下参数必须提前获取：

| 参数 | 获取方式 |
|-----|---------|
| `doc_id` | 从 `skylark_search` 返回结果的 `docs[].id` 获取 |

**工作流建议**:
1. 先调用 `skylark_search` 搜索文档
2. 从返回结果中提取 `docs[0].id`
3. 使用提取的 id 调用 `skylark_doc_detail`

**常见错误**: 直接调用 `skylark_doc_detail` 而未从搜索结果中获取 doc_id

## Root Cause Analysis

**Problem**: 调用 `skylark_doc_detail` 时参数为空: doc_id

**Impact**:
- 工具调用失败 (2次重试)
- 浪费 API 调用额度
- 增加任务执行时间
- 降低用户体验

**Recommendation**:
- 在 skill 描述中明确标注必填参数
- 提供参数获取流程说明
- 添加返回值字段与参数名的对应关系
```

**优化建议说明**:
1. 创建 Session Memory 指导 Agent 正确的工具调用顺序
2. 明确参数来源和提取路径
3. 说明常见错误及如何避免

---

## 输出文件结构

```
doctor/output/antchat-kimi-k2-5/case_p001_demo/
├── diagnosis.json                    # 完整诊断数据 (JSON)
├── diagnosis_summary.md              # 可读诊断报告 (Markdown)
└── patches/
    ├── skill/
    │   └── patch_001_skylark_doc_detail.yaml   # Skill 优化建议
    └── memory/
        └── patch_002.md                          # Memory 优化建议
```

---

## 后续步骤

### 1. 应用补丁

**手动应用 Skill Patch**:
1. 打开 MCP 工具描述文件
2. 找到 `skylark_doc_detail` 工具描述
3. 在 description 末尾添加补丁内容

**应用 Memory Patch**:
1. 将 memory 文件复制到 Agent 的 memory 目录
2. 或在 Session 开始时注入 memory 内容

### 2. 验证优化效果

```bash
# 重新运行基准测试
uv run benchmark.py --model antchat-kimi-k2-5 --benchmark yuque_bench

# 再次运行诊断
python run.py --run-id <new_run_id> --model antchat-kimi-k2-5
```

### 3. 对比结果

检查是否：
- [ ] P001 问题不再出现
- [ ] 工具调用成功率提高
- [ ] 任务得分提升

---

## 总结

### P001 检测原理

```python
# 检测逻辑伪代码
for tool_name in all_tools:
    calls = get_calls_for_tool(tool_name)
    failed_calls = [c for c in calls if c.is_error]

    if len(failed_calls) >= MIN_FAILURES:
        # 检查是否参数相似
        if are_parameters_similar(failed_calls):
            issue = Issue(
                pattern_id="P001",
                tool_name=tool_name,
                failure_count=len(failed_calls),
                root_cause=analyze_root_cause(failed_calls)
            )
```

### 优化效果预期

| 指标 | 优化前 | 优化后 (预期) |
|------|--------|--------------|
| skylark_doc_detail 成功率 | 33% (1/3) | 100% (1/1) |
| 工具调用重试次数 | 2 次 | 0 次 |
| 任务执行时间 | 10.5s | ~8s |
| API 调用成本 | 5 次请求 | 3 次请求 |

---

*此案例由 PinchBench Doctor 自动生成*
*生成时间: 2026-04-14T16:14:06*
