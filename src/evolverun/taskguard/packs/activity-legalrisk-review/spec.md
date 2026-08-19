# Activity-LegalRisk-Review Workflow Spec

## 目标

有奖促销/抽奖类活动法务风险评审工作流，所有审查规则内化在 prompt 中，不依赖外部 skill。

原 skill 执行流程为：文档读取 → 关键词命中 → 9类风险全面审查 → 格式化输出 → 用户确认 → 结果上报 + 语雀文档创建。

本 workflow 对齐原 skill 的完整执行逻辑，将流程拆为独立的 embedded-agent / human 节点，支持条件跳过（关键词未命中时终止）和人工确认循环。

## 输入参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| docUrl | string | 是 | 活动规则文档 URL（语雀链接或可访问的文档链接） |
| projectName | string | 是 | 项目名称（用于 workspace 目录命名和身份去重） |
| subTaskId | string | 否 | 子任务 ID（步骤3 推进风控评审状态时使用，可选） |
| domain | string | 否 | 风控评审域，默认同时评审 COMPLIANCE + BUSINESS_RISK_CONTROL |

## 身份与去重

- **identity.key**: `{{input.params.projectName}}`
- **duplicatePolicy**: allow（同一项目名允许重新创建）
- **label**: `法务评审: {{input.params.projectName}}`

## 输出产物

| 产物 | 来源节点 | 文件 | 说明 |
|------|---------|------|------|
| 需求文档 | fetch-doc | complete_content.md | 读取并合并后的活动规则文档 |
| 法务评审结果 |legal-review | legal_review.md | 9类风险的完整审查记录 |
| 格式化输出 | format-output | formatted_review.md | 按规定格式输出的评审意见 |
| 语雀文档 | create-yuque-doc | (语雀在线文档) | 存档评审结果 |

## 风险审查类别

### 风控评审域

- COMPLIANCE：合规评审
- BUSINESS_RISK_CONTROL：业务风控评审

### 9类风险指标

**禁止行为（1-7）**

| 编号 | 风险类型 | 关键词/检测规则 |
|------|---------|----------------|
| 1 | 虚假宣传 | 奖项/奖品/价值/中奖率的虚构、夸大或无法验证陈述 |
| 2 | 非公平投放 | 奖品未全部投放、仅特定渠道/区域投放且未公平告知 |
| 3 | 不明确时间 | 隐藏关键时间节点（大奖投放/开奖/活动起止时间） |
| 4 | 不兑奖 | 不合理或过于严苛的兑奖条件 |
| 5 | 内定中奖 | 内部员工/关联方/特定人员可参与并中奖；药品/医疗器械促销 |
| 7 | 最终解释权 | "最终解释权归XX所有"类霸王条款 |
| — | (6已合并至5) | 药品/医疗器械有奖销售（法律禁止） |

**金额限制（8）**

| 编号 | 风险类型 | 检测规则 |
|------|---------|---------|
| 8 | 奖品金额超限 | 单项奖品>5万/单次抽奖总金额>5万/累计可获奖金>5万 |

**信息展示完备性（9）**

| 编号 | 风险类型 | 检测规则 |
|------|---------|---------|
| 9.1 | 奖项信息不完备 | 种类/数量/条件/范围/方式 |
| 9.2 | 奖品信息不完备 | 金额/品名/数量（特殊商品豁免：票务/定制款/虚拟权益） |
| 9.3 | 发奖/兑奖信息不完备 | 时间/条件/方式/交付 |
| 9.4 | 主办方信息不完备 | 名称/联系方式 |
| 9.5 | 弃奖条件不完备 | 逾期未兑付视为放弃等 |
| 9.6 | 多账号获奖限制不完备 | 同一用户判定条件 |

### 风险输出规则

| 风险类型 | 发现风险时 | 未发现风险时 |
|---------|-----------|-------------|
| 1, 2, 5, 9.6 | 法务调整意见 | "本次活动未发现明显风险，请业务侧在实际展业过程中重点关注" |
| 3, 4, 6, 7, 8, 9.1, 9.2, 9.4 | 法务调整建议 | "未发现明显风险" |
| 3, 9.3, 9.5 | 法务调整建议 + **"明确的表述可以降低客诉及监管问询"** | "未发现明显风险" |

## 流程设计

### 阶段划分与节点

| Phase | 节点 ID | 节点标题 | 执行器 | 依赖 | 关键输入 | 关键输出 |
|-------|---------|---------|--------|------|---------|---------|
| P1 | fetch-doc | 读取活动规则文档 | embedded-agent | - | docUrl | complete_content.md |
| P2 | keyword-check | 关键词命中检测 | embedded-agent | fetch-doc | complete_content.md | keywordsHit, hitKeywords |
| P3 | legal-review | 9类法务风险审查 | embedded-agent | keyword-check | complete_content.md | legal_review.md |
| P4 | format-output | 格式化评审输出 | embedded-agent | legal-review | legal_review.md | formatted_review.md |
| P5 | human-confirm | 用户确认 | human | format-output | formatted_review.md | confirmed |
| P6 | report-result | 结果上报 | embedded-agent | human-confirm | formatted_review.md | 任务步骤更新 |
| P6 | create-yuque-doc | 创建语雀存档文档 | embedded-agent | human-confirm | formatted_review.md | 语雀文档URL |
| P7 | done | 完成 | done | report-result, create-yuque-doc | - | 流程结束 |

### 数据流

```
docUrl, projectName
    │
    ▼
fetch-doc (embedded-agent: 读取语雀/Word文档) ─→ complete_content.md
    │
    ▼
keyword-check (embedded-agent: 关键词命中检测)
    │
    ├── keywordsHit=false → 流程结束（"需要进行人工审核"）
    │
    ▼ (keywordsHit=true)
legal-review (embedded-agent: 9类风险全面审查) ─→ legal_review.md
    │
    ▼
format-output (embedded-agent: 格式化输出) ─→ formatted_review.md
    │
    ▼
human-confirm (human: /confirm 确认)
    │
    ├── /revise → rerun legal-review
    ├── /reject → fail-flow
    │
    ▼ (/confirm)
    ├──→ report-result (embedded-agent: 上报task-worker-skill)
    │
    └──→ create-yuque-doc (embedded-agent: 语雀文档创建)
              │
              ▼
         done
```

### 特殊逻辑

1. **关键词未命中提前终止**: keyword-check 节点检测到未命中关键词时，通过 onResult 条件判断直接结束流程，输出"需要进行人工审核"。
2. **语雀文档读取**: 如果 docUrl 以 `https://yuque.` 开头，使用 MCP (skylark_resolve_url + skylark_doc_detail) 读取，禁止 web_fetch。
3. **双重验证机制**: 每个风险点必须经过关键词匹配验证 + 语义理解验证。
4. **用户确认严格性**: 只有 `/confirm` 算确认，其他输入不算。`/revise` 可打回修改，`/reject` 终止流程。
5. **结果上报**: 确认后摘取【法务评审意见】、【风险列表】、【文案瑕疵】调用 task-worker-skill 上报。
6. **语雀文档创建**: 参考模板文档，在同一知识库创建同级文档存档。

## 技术约束

1. **语雀文档读取**: 必须使用 MCP (skylark_resolve_url + skylark_doc_detail)，禁止 web_fetch/browser
2. **输出目录**: `workspace/{projectName}/`
3. **embedded-agent cwd**: 默认是 `~/.openclaw/workspace/`，prompt 中需使用完整绝对路径
4. **输出格式**: 严格遵循【法务评审意见】→【风险列表】→【文案瑕疵】→【其他审查项说明】格式
5. **风险判定逻辑**: 严格按照各风险类型的判定规则和输出规则执行

## 产出文件结构

```
~/.openclaw/workspace/workspace/{projectName}/
├── complete_content.md     # 文档内容
├── legal_review.md          # 9类风险审查记录
└── formatted_review.md      # 格式化评审输出
```