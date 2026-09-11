# HTML 治理报告规范

Governance Agent 完成决策后必须生成 `report.html`，放在召回 `output-dir/{dt}/` 目录下，与 `candidates.json` 同级。

## 设计原则

- **自包含**：CSS 内联在 `<style>` 中，不依赖外部文件。
- **按决策分层**：CREATE_MANUAL 候选 → WATCH → DROP，每层独立 section。
- **可操作**：每条候选包含根因、代表 Session、NAS 核验状态，Admin 可直接据此评审。
- **一屏可读**：1200px 最大宽度，移动端友好。

## 报告结构

```
┌─ Header（渐变背景）
│  title: "OpenClaw 能力类失败治理候选报告"
│  meta: 分析周期 / 生成时间 / 数据源 / 流程摘要
├─ Stats Row（5-6 个统计卡片）
│  召回 Bot 总数 / 有能力类失败 Bot / 任务数 / 浪费时长 / HIGH 候选 / 最终决策数
├─ 失败类分布表
│  失败分类 | Bot 数 | 任务数 | 占比
├─ CREATE_MANUAL 候选详情（每个一条 candidate card）
│  决策 badge | 标题 | 评分/任务数/持续天数/浪费时长 | top_failure_class
│  根因摘要 | 指派理由 | 建议动作
│  NAS 核验状态（✅找到 / ❌未找到 / 📝备注）
│  Evidence Box: 2-3 个代表 Session（sessionId、日期、failureClass、reasoning）
├─ WATCH 候选表
│  # | User | Bot | Name | Score | Tasks | Wasted | Class | Watch Reason
├─ DROP 候选 card（红色左边框）
│  DROP badge | Name | Score | 原因
├─ 全量 HIGH 候选清单（可收缩表格）
│  # | User | Bot | Name | Score | Tasks | Days | Wasted | Class | Decision
└─ Footer
   分析方法说明 / 生成时间
```

## 视觉规范

| 元素 | 样式 |
|------|------|
| 决策 badge | CREATE_MANUAL=#f59e0b, WATCH=#3b82f6, DROP=#ef4444, CREATE_AUTO=#10b981 |
| 失败类 badge | PERMISSION_NETWORK=#f59e0b, TOOL_FAILURE=#ef4444, CONFIG_MISSING=#3b82f6, DATA_ISSUE=#10b981, WORKFLOW_FAILURE=#8b5cf6, CAPABILITY_BOUNDARY=#6366f1, OUTPUT_WRONG=#ec4899, PARAMETER_ERROR=#f97316 |
| NAS 状态 | 找到: #d1fae5/#065f46, 问题: #fef3c7/#92400e |
| 背景色 | body #f8fafc, card #fff, evidence box #f1f5f9 |
| 主文字 | #1e293b, 次级 #64748b, 代码 #475569 |

## 数据来源

从以下文件读取：
- `candidates.json` — 召回全量数据（含评分、失败类、Skill/MCP）
- `evidence/governance_decisions.json` — 决策结果（CREATE_MANUAL/WATCH/DROP 理由）
- `evidence/evidence_pack.json` — 提交候选（含根因、理由、建议动作、selectedTasks）
- `evidence/{user_id}_{bot_id}.json` — 代表 Session 明细

NAS 核验结果在执行过程中收集，写入 HTML 时按候选 key 匹配。

## 示例参考

参考 `/ossfs/workspace/openclaw_capability_governance_candidates/20260817/report.html`。