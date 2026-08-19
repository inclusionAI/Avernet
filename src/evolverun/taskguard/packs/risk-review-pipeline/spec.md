# 营销活动风险评审全流程 — Spec

## 目标
将 marketing-review-pipeline 的 11 步流程 + overall-risk-check 内部 6 个子模块的编排链，拆解为 ClawMind Workflow 声明式 DAG。核心改动是将原 `run-pipeline` 单节点（方案 A）拆为 6 个独立节点（方案 B），实现子模块级可见性、可重试、并行执行。

## 输入参数
| 参数名 | 必填 | 说明 |
|--------|------|------|
| activityId | 是 | 营销活动ID（CP号） |
| force | 否 | 是否强制重新注入数据（默认 false） |

## 身份与去重
- key: `{{input.params.activityId}}`
- label: `评审: {{input.params.activityId}}`
- duplicatePolicy: reject-active（同一活动不重复评审）

## 输出产物
| 输出名 | 来源 | 说明 |
|--------|------|------|
| reviewResult | `{{nodeOutput.aggregate-risk}}` | 最终评审结果（final_res 39字段） |
| hasRisk | `{{nodeOutput.aggregate-risk.has_risk}}` | 是否有风险 |
| riskSummary | `{{nodeOutput.aggregate-risk.risk_summary}}` | 风险摘要 |

## 阶段划分

### P1: 数据获取与初始化
- fetch-data：按活动ID获取营销活动数据
- warm-mcp：预热 MCP 服务（与 fetch-data 并行）
- verify-data：校验活动数据是否存在（AI 判断，无数据则终止流程）

### P2: 会话与数据注入
- get-session-info：获取会话/文件路径信息
- check-init-files：检查初始化文件是否已存在
- inject-promo-info：注入活动信息（条件执行：文件缺失时才执行）

### P3: 数据预处理
- data-preprocessing：清洗 JSON、提取 25 个标准字段（后续所有节点依赖此步骤）

### P4: 特征识别（并行）
- biz-scenario-recognition：识别业务场景（网商/消金/财富/保险）
- prize-value-recognition：提取奖品真实价值、分类权益类型
- gameplay-recognition：识别 27 种玩法 + 是否大促

### P5: 风险校验（并行）
- config-risk-check：4 项配置风险校验（依赖 P4 的 scenarios + prize_values）
- biz-risk-check：6 项业务风险校验（依赖 P4 的 prize_values + gameplay_names + scenarios）

### P6: 风险汇总
- aggregate-risk：汇总配置风险 + 业务风险，转为 final_res 格式

### P7: 结果校验与修复
- validate-result：完整性校验
- auto-repair：缺失字段自动修复
- sync-result：同步最终结果到分析结果文件

### P8: 结果保存与通知
- save-to-rone：保存结果到 Rone 系统
- risk-free-callback：无风险回调（仅无风险时执行）
- send-notification：发送 Rone 卡片通知
- archive：归档已完成文件

## 节点列表

| ID | 标题 | 阶段 | 依赖 | 执行器 | 说明 |
|----|------|------|------|--------|------|
| fetch-data | 获取活动数据 | P1 | - | cli-script | `fetch_data.py --id {activityId}` |
| warm-mcp | 预热MCP服务 | P1 | - | cli-script | `mcp_adapter.py`，与 fetch-data 并行 |
| verify-data | 校验活动数据 | P1 | fetch-data | embedded-agent | 判断数据是否存在，不存在则终止 |
| get-session-info | 获取会话信息 | P2 | verify-data, warm-mcp | cli-script | `get_session_info.py` |
| check-init-files | 检查初始化文件 | P2 | get-session-info | embedded-agent | 判断是否需要注入 |
| inject-promo-info | 注入活动信息 | P2 | check-init-files | embedded-agent | 条件执行：文件缺失时才注入 |
| data-preprocessing | 数据预处理 | P3 | inject-promo-info | cli-script | 清洗 JSON、提取 25 个标准字段 |
| biz-scenario-recognition | 业务场景识别 | P4 | data-preprocessing | cli-script | 识别业务场景（网商/消金/财富/保险） |
| prize-value-recognition | 权益价值识别 | P4 | data-preprocessing | cli-script | 提取奖品真实价值、分类权益类型 |
| gameplay-recognition | 活动玩法识别 | P4 | data-preprocessing | cli-script | 识别 27 种玩法 + 是否大促 |
| config-risk-check | 配置风险校验 | P5 | biz-scenario-recognition, prize-value-recognition | cli-script | 4 项配置风险校验 |
| biz-risk-check | 业务风险校验 | P5 | prize-value-recognition, gameplay-recognition, biz-scenario-recognition | cli-script | 6 项业务风险校验 |
| aggregate-risk | 风险汇总 | P6 | config-risk-check, biz-risk-check | cli-script | 汇总风险判断 + to_final_res 转换 |
| validate-result | 完整性校验 | P7 | aggregate-risk | cli-script | `validate_result.py` |
| auto-repair | 自动修复 | P7 | validate-result | cli-script | `check_and_repair.py` |
| sync-result | 同步结果 | P7 | auto-repair | cli-script | `json_updater.py` |
| save-to-rone | 保存到Rone | P8 | sync-result | cli-script | `save_data.py` |
| risk-free-callback | 无风险回调 | P8 | save-to-rone | cli-script | `pass_risk_free.py`（仅无风险时执行） |
| send-notification | 发送通知 | P8 | save-to-rone | cli-script | `send_rone_notification.py` |
| archive | 归档 | P8 | send-notification | cli-script | `check_all_completed.py` |

## 数据流

```
fetch-data ──→ activityId, data_count
warm-mcp   ──→ mcp_ready
     ↓
verify-data ──→ dataExists (false → 流程终止)
     ↓
get-session-info ──→ input_file, output_file, references_file
     ↓
check-init-files ──→ needsInject (false → workflowData.skipInject=true)
     ↓
inject-promo-info ──→ event_property_{id}.json, data_complete
     ↓
data-preprocessing ──→ 预处理后的标准字段 JSON (25个字段)
     ↓  ┌────────────────────┬────────────────────┐
     ↓  ↓                    ↓                    ↓
biz-scenario-recognition  prize-value-recognition  gameplay-recognition
  (scenarios)             (prize_values)           (gameplay_names, is_dapro)
     ↓                    ↓                    ↓
     └────────┬───────────┘                    │
              ↓                                │
     config-risk-check                        │
     (4项配置校验)                              │
                                           │   │
              ┌────────────────────────────┘   │
              ↓                                ↓
     biz-risk-check ←─────────────────────────┘
     (6项业务校验)
              ↓
     aggregate-risk ──→ final_res (39字段), has_risk, risk_summary
              ↓
     validate-result ──→ is_complete, missing_steps
              ↓
     auto-repair ──→ repaired_fields
              ↓
     sync-result ──→ synced
              ↓
     save-to-rone ──→ record_id, risk_level, activity_name
              ↓                    ↓
     risk-free-callback   send-notification ──→ notification_sent
                              ↓
                          archive ──→ archived
```

## 特殊逻辑

### 条件执行
1. **verify-data**（onResult）：当 `dataExists: false` 时，`complete: true` 直接终止流程
2. **check-init-files**（onResult）：当 `needsInject: false` 时，`saveAs workflowData.skipInject: "true"`
3. **inject-promo-info**（embedded-agent）：检查 `workflowData.skipInject`，为 `"true"` 时跳过
4. **auto-repair**：校验通过时脚本内部为 no-op
5. **risk-free-callback**：有风险时脚本内部返回 `skipped: true`

### CLI 包装脚本（新增）
以下子模块目前只有 Python API（`from xxx import enrich`），需创建 CLI 入口脚本以供 cli-script 调用：

| 子模块 | 现有入口 | 需新增 CLI 脚本 | 说明 |
|--------|---------|---------------|------|
| data-preprocessing | `processor.py` | `cli_enrich.py` | 读入 event_property JSON，输出预处理后 JSON |
| biz-scenario-recognition | `test_runner.py` | `cli_enrich.py` | 读入预处理 JSON，追加 scenarios 字段 |
| prize-value-recognition | `test_runner.py` | `cli_enrich.py` | 读入预处理 JSON，追加 prize_values 字段 |
| gameplay-recognition | — | `cli_enrich.py` | 读入预处理 JSON，追加 gameplay_names 字段 |
| config-risk-check | — | `cli_enrich.py` | 读入含 scenarios+prize_values 的 JSON，输出 config_checks |
| biz-risk-check | — | `cli_enrich.py` | 读入含全部字段的 JSON，输出 biz_checks |
| overall-risk-check | — | `cli_aggregate.py` | 读入 config_checks+biz_checks，汇总 + to_final_res |

每个 CLI 脚本统一接口：
```bash
python3 cli_enrich.py --input <input.json> --output <output.json>
# 或 stdin/stdout:
python3 cli_enrich.py < input.json > output.json
```

### 沙箱执行约束
所有 cli-script 节点必须使用绝对路径调用 Python 脚本，禁止 `cd &&` 组合命令和管道操作。

## 产出文件
- `workflow.pack.yaml`
- `workflows/risk-review-pipeline.yaml`
- 各子模块的 CLI 包装脚本（7 个 `cli_enrich.py` + 1 个 `cli_aggregate.py`）