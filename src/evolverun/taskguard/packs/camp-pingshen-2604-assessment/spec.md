# Camp-Pingshen-2604-Assessment Workflow Spec (v2 重构)

## 目标

将 camp-pingshen-2604-skill（一站式活动风险评估解决方案 2604版）转换为可自动执行的 ClawMind Workflow。

原 skill 执行流程为 Stage 0-6 的串行流程，每个 Stage 有严格的输入门控（前置交付物必须存在）和输出验证（run_validation.py），Stage 5 完成后存在一个并行分支（result-updater 调用 + Stage 6 报告生成）。

本 workflow 对齐原 skill 的完整执行逻辑，将每个 Stage 拆为独立的 embedded-agent 节点，每个 Stage 输出后运行 cli-script 验证节点，验证不通过则 BLOCK 重试。

## 输入参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| docUrl | string | 是 | 语雀活动需求文档 URL（如 https://yuque.antfin.com/bmrm/vqp291/xxx） |
| projectName | string | 是 | 项目名称（用于 workspace 目录命名，如 "会员积分抽奖"） |
| subTaskId | string | 否 | 子任务 ID（Stage 5 调用 result-updater 推进子任务时使用） |

## 身份与去重

- **identity.key**: `{{input.params.projectName}}`
- **duplicatePolicy**: reject-active（同一项目名不允许重复创建）
- **label**: `活动评审: {{input.params.projectName}}`

## 输出产物

| 产物 | 来源节点 | 文件 | 说明 |
|------|---------|------|------|
| 需求文档 | stage0-resolve | complete_content.md | Stage 0: 合并后的需求文档 |
| 活动类型 | stage1-type | activity_type.md | Stage 1: 六大类型判断结果 |
| 活动拆解 | stage2-split | activity_split.md | Stage 2: 四维度拆解 |
| 风险识别 | stage3-risk | activity_risktype.md | Stage 3: 七大标准风险术语 |
| 评分评级 | stage4-level | activity_risklevel.md | Stage 4: 10维度评分 |
| 评估意见 | stage5-output | insights.md | Stage 5: 最终风险评估意见 |
| 评估报告 | stage6-report | report.md | Stage 6: 完整评审报告 |

## 流程设计

### 阶段划分与节点

| Phase | 节点 ID | 节点标题 | 执行器 | 依赖 | 关键输入 | 关键输出 |
|-------|---------|---------|--------|------|---------|---------|
| P1 | stage0-resolve | 解析文档URL | mcp-call | - | docUrl | doc_id, namespace, slug |
| P1 | stage0-fetch | 获取文档内容 | embedded-agent | stage0-resolve | doc_id | complete_content.md |
| P2 | stage1-type | 活动类型判断 | embedded-agent | stage0-fetch | complete_content.md | activity_type.md |
| P2 | validate-s1 | 验证Stage1 | cli-script | stage1-type | activity_type.md | 验证结果 |
| P3 | stage2-split | 活动拆解 | embedded-agent | validate-s1 | activity_type.md, complete_content.md | activity_split.md |
| P3 | validate-s2 | 验证Stage2 | cli-script | stage2-split | activity_split.md | 验证结果 |
| P4 | stage3-risk | 风险识别 | embedded-agent | validate-s2 | activity_split.md, activity_type.md | activity_risktype.md |
| P4 | validate-s3 | 验证Stage3 | cli-script | stage3-risk | activity_risktype.md | 验证结果 |
| P5 | stage4-level | 评分评级 | embedded-agent | validate-s3 | activity_risktype.md, activity_split.md | activity_risklevel.md |
| P5 | validate-s4 | 验证Stage4 | cli-script | stage4-level | activity_risklevel.md | 验证结果 |
| P6 | stage5-output | 风险评估意见 | embedded-agent | validate-s4 | activity_risklevel.md, activity_risktype.md | insights.md |
| P6 | validate-s5 | 验证Stage5 | cli-script | stage5-output | insights.md | 验证结果 |
| P6 | update-subtask | 推进子任务 | mcp-call | validate-s5 | subTaskId, insights | 子任务状态更新 |
| P7 | stage6-report | 生成报告 | embedded-agent | validate-s5 | insights.md 及所有前置交付物 | report.md + 语雀文档 |
| P7 | done | 完成 | done | update-subtask, stage6-report | - | 流程结束 |

### 数据流

```
docUrl, projectName
    │
    ▼
┌─ stage0-resolve (mcp: skylark_resolve_url) ─→ doc_id ─┐
│                                                        │
└────────────────────────────────────────────────────────┘
    │
    ▼
stage0-fetch (embedded-agent: 读取主文档+子链接) ─→ complete_content.md
    │
    ▼
stage1-type (embedded-agent: 活动类型判断) ─→ activity_type.md
    │
    ▼
validate-s1 (cli-script: run_validation.py 1)
    │
    ▼
stage2-split (embedded-agent: 四维度拆解) ─→ activity_split.md
    │
    ▼
validate-s2 (cli-script: run_validation.py 2)
    │
    ▼
stage3-risk (embedded-agent: 风险识别) ─→ activity_risktype.md
    │
    ▼
validate-s3 (cli-script: run_validation.py 3)
    │
    ▼
stage4-level (embedded-agent: 评分评级) ─→ activity_risklevel.md
    │
    ▼
validate-s4 (cli-script: run_validation.py 4)
    │
    ▼
stage5-output (embedded-agent: 风险评估意见) ─→ insights.md
    │
    ▼
validate-s5 (cli-script: run_validation.py 5)
    │
    ├──→ update-subtask (mcp: update_subtask_info, 仅当 subTaskId 存在时)
    │
    └──→ stage6-report (embedded-agent: 生成报告+语雀文档)
              │
              ▼
         done
```

### 特殊逻辑

1. **Stage 0 的 MCP 调用**: 先用 skylark_resolve_url 解析语雀链接，再用 skylark_doc_detail 获取内容。需要识别子链接并递归获取。
2. **验证节点**: 每个 Stage (1-5) 输出后运行 `python3 run_validation.py {stage} workspace/{project}/`，通过则继续，BLOCK 则标记失败。
3. **并行执行**: Stage 5 验证通过后，update-subtask 与 stage6-report 并行执行。
4. **subTaskId 可选**: 如果未提供 subTaskId，update-subtask 节点跳过（通过 onResult 条件判断）。
5. **知识注入**: 各 embedded-agent 节点的 prompt 需包含对应 guides/ 和 frameworks/ 文件的读取指令。
6. **验证脚本路径**: 位于 `~/.openclaw/workspace/skills/skills-local/camp-pingshen-2604-skill/resources/validators/`

### 各节点详细设计

#### stage0-resolve (mcp-call)
- 工具: skylark_resolve_url
- 输入: docUrl 参数
- 输出: doc_id, namespace, slug
- outputContract: { doc_id: number, namespace: string?, slug: string? }

#### stage0-fetch (embedded-agent)
- 输入: stage0-resolve 的 doc_id 输出
- 执行逻辑:
  1. 用 MCP skylark_doc_detail 获取主文档内容
  2. 扫描主文档中的语雀子链接
  3. 对每个子链接执行 resolve_url → doc_detail
  4. 合并所有内容写入 complete_content.md
- outputContract: { contentFile: string, subLinksCount: number }

#### stage1-type (embedded-agent)
- 必读: guides/stage1_type.md, frameworks/camp_type.md, complete_content.md
- 执行逻辑:
  1. 读取 camp_type.md 匹配六大标准类型
  2. 识别是否为复合活动（≥2种玩法需拆分）
  3. 输出 activity_type.md
- outputContract: { activityType: string, isCompound: boolean }

#### validate-s1 ~ validate-s5 (cli-script)
- 命令: `python3 {VALIDATORS_DIR}/run_validation.py {stage} workspace/{project}/`
- 注意: cli-script 的 cwd 是 pack 根目录，需要用绝对路径指向 validators 目录

#### stage2-split (embedded-agent)
- 必读: guides/stage2_split.md, frameworks/camp_split.md, activity_type.md, complete_content.md
- 输出: activity_type.md（四维度：准入、完成活动、发奖、核销）
- 关键约束: 四同判定放在【完成活动】中

#### stage3-risk (embedded-agent)
- 必读: guides/stage3_risk.md, frameworks/camp_risktype.md, activity_type.md, activity_split.md
- 输出: activity_risktype.md（七大标准风险术语）

#### stage4-level (embedded-agent)
- 必读: guides/stage4_level.md, frameworks/camp_risklevel.md, activity_split.md
- 输出: activity_risklevel.md（10维度评分表+风险等级）

#### stage5-output (embedded-agent)
- 必读: guides/stage5_output.md, frameworks/camp_advice.md, activity_risklevel.md, activity_risktype.md, activity_split.md
- 输出: insights.md（主要风险 + 风险评估意见）
- 关键约束: 禁止与文档已有规则重复；实时风控与机刷人群ID互斥

#### update-subtask (mcp-call)
- 工具: update_subtask_info
- 服务器: mcp.ant.agentix.165147.activityReviewManagementV2
- 条件: 仅当 subTaskId 非空时执行

#### stage6-report (embedded-agent)
- 必读: guides/stage6_report.md, references/report_standards.md, insights.md 及所有前置交付物
- 输出: report.md + 语雀文档

## 技术约束

1. **语雀文档读取**: 必须使用 MCP (skylark_resolve_url + skylark_doc_detail)，禁止 web_fetch/browser
2. **验证脚本**: `python3 ~/.openclaw/workspace/skills/skills-local/camp-pingshen-2604-skill/resources/validators/run_validation.py {stage} workspace/{project}/`
3. **知识库路径**: `~/.openclaw/workspace/skills/skills-local/camp-pingshen-2604-skill/frameworks/`
4. **指南文件路径**: `~/.openclaw/workspace/skills/skills-local/camp-pingshen-2604-skill/guides/`
5. **输出目录**: `workspace/{projectName}/`
6. **embedded-agent cwd**: 默认是 `~/.openclaw/workspace/`，prompt 中需 cd 到正确目录
7. **验证等级**: BLOCK 级违规必须重试，WARN 级继续执行

## 产出文件结构

```
~/.openclaw/workspace/workspace/{projectName}/
├── complete_content.md    # Stage 0: 合并需求文档
├── activity_type.md       # Stage 1: 活动类型
├── activity_split.md      # Stage 2: 活动拆解
├── activity_risktype.md   # Stage 3: 风险识别
├── activity_risklevel.md  # Stage 4: 评分评级
├── insights.md            # Stage 5: 评估意见
└── report.md              # Stage 6: 完整报告
```