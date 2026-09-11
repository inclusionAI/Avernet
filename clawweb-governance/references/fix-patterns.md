# 已知修复模式库

Governance Agent 在做 `CREATE_AUTO` / `CREATE_MANUAL` 决策时，先查本模式库。匹配命中则直接采用预定义的修复类型和建议动作；未命中再靠 ODPS + NAS 推理。

---

## 模式格式

每个模式 = `failure_class` + `component_type` + 匹配特征 → `action_type` + 修复模板。

匹配优先级：精确匹配 > 模糊匹配 > Agent 推理。

---

## 1. MCP 参数错误 → AUTO

### 1.1 run_sql_query / query_sql_status 参数名错误

| 字段 | 值 |
|------|------|
| **failure_class** | TOOL_FAILURE / PARAMETER_ERROR |
| **component_type** | mcp |
| **component_name** | `mcp.ant.rpc.dpagent.dataprocess.run_sql_query` / `query_sql_status` / `query_sql_result` / `run_sql_flows` |
| **匹配特征** | Judge reasoning 包含 `INVALID_PARAMETER` / `参数.*错误` / `argument.*invalid` |
| **action_type** | DIRECT_EVOLUTION |
| **修复模板** | 在 Bot 的 TOOLS.md 或对应 Skill 的 SKILL.md 中更新该 MCP 工具的调用参数名，使其匹配当前 MCP schema。修改范围仅限参数名映射，可回滚。 |
| **验证方式** | 触发一次使用该 MCP 的典型任务，确认不再出现 INVALID_PARAMETER |

### 1.2 dataphin / pydataphin 参数不匹配

| 字段 | 值 |
|------|------|
| **failure_class** | TOOL_FAILURE / PARAMETER_ERROR |
| **component_type** | mcp |
| **component_name** | `mcp.ant.faas.pydataphin.*` |
| **匹配特征** | Judge reasoning 包含 `参数.*缺失` / `required.*argument` / `schema.*mismatch` |
| **action_type** | DIRECT_EVOLUTION |
| **修复模板** | 更新 Skill 中调用 pydataphin 的参数字段名以匹配当前 API schema。 |
| **验证方式** | 触发一次 Dataphin 查询任务，确认成功返回数据 |

---

## 2. MCP 选型错误 → AUTO

### 2.1 语雀用错 MCP (yuque → skylark)

| 字段 | 值 |
|------|------|
| **failure_class** | TOOL_FAILURE / CONFIG_MISSING |
| **component_type** | mcp |
| **component_name** | `mcp.ant.faas.yuque.*` |
| **匹配特征** | Judge reasoning 包含 `yuque` + ( `NOT_FOUND` / `不存在` / `已迁移` ) 或 Skill 引用了已废弃的 yuque MCP |
| **action_type** | DIRECT_EVOLUTION |
| **修复模板** | 将 Bot 配置和 Skill 中所有 `mcp.ant.faas.yuque.*` 替换为 `mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.*`。语雀 MCP 已统一迁移到 skylark，旧 yuque 端点已废弃。 |
| **验证方式** | 触发一次语雀文档读写任务，确认 skylark MCP 正常返回 |

### 2.2 钉钉通知用错 MCP

| 字段 | 值 |
|------|------|
| **failure_class** | TOOL_FAILURE / CONFIG_MISSING |
| **component_type** | mcp |
| **component_name** | `mcp.ant.antdingopenapi.antdingrobotmcpserver.*` |
| **匹配特征** | Judge reasoning 包含 `sendMessage` + `失败` / `NOT_FOUND` |
| **action_type** | DIRECT_EVOLUTION |
| **修复模板** | 将 `antdingrobotmcpserver.sendMessage` 替换为 `antdingmessagemcpserver.sendSingleChatCardMessage` 或 `sendCardMessage`。旧版 robot MCP 已废弃。 |
| **验证方式** | 触发一次钉钉推送任务，确认消息发送成功 |

---

## 3. Skill 缺失/未部署 → AUTO（内容已知）/ MANUAL（内容未知）

### 3.1 已知 Skill 路径错误

| 字段 | 值 |
|------|------|
| **failure_class** | CONFIG_MISSING |
| **component_type** | skill |
| **匹配特征** | Judge reasoning 包含 `NO_SUCH_FILE` / `SKILL_NOT_FOUND`，且 NAS 上该 Skill 存在于 `skills-pool` 或 `skills-repo` 中 |
| **action_type** | DIRECT_EVOLUTION |
| **修复模板** | 将 Skill 从 skills-pool/skills-repo 复制到 Bot 的 `.openclaw/skills/` 目录，并在 openclaw.json 的 skills.entries 中启用。 |
| **验证方式** | 触发使用该 Skill 的任务，确认不再报 SKILL_NOT_FOUND |

### 3.2 未知 Skill 缺失

| 字段 | 值 |
|------|------|
| **failure_class** | CONFIG_MISSING |
| **component_type** | skill |
| **匹配特征** | Judge reasoning 包含 `SKILL_NOT_FOUND` / `SKILL_NOT_DEPLOYED`，但 NAS 和 skills-pool 中均无此 Skill |
| **action_type** | ASSIGN_OWNER |
| **修复模板** | 需要 Owner 提供或创建该 Skill 的定义文件，无法自动生成。 |
| **验证方式** | Owner 部署 Skill 后触发任务验证 |

---

## 4. MCP 认证/权限 → MANUAL

### 4.1 员工身份认证缺失

| 字段 | 值 |
|------|------|
| **failure_class** | PERMISSION_NETWORK |
| **component_type** | mcp / skill |
| **匹配特征** | Judge reasoning 包含 `NO_EMPLOYEE_IDENTITY` / `BOT_TOKEN_NOT_APPLIED` / `AUTHENTICATION_FAILURE` / `无员工身份` / `token.*缺失` |
| **action_type** | ASSIGN_OWNER |
| **修复模板** | Bot 使用的 MCP 服务缺少有效的认证凭据。需要 Owner 在 Bot 平台更新 token 或联系服务管理员授权。涉及凭据管理，不可自动修复。 |
| **验证方式** | 更新 token 后触发任务，确认 MCP 调用成功 |

### 4.2 ODPS 表无读权限

| 字段 | 值 |
|------|------|
| **failure_class** | PERMISSION_NETWORK / DATA_ISSUE |
| **component_type** | mcp |
| **匹配特征** | Judge reasoning 包含 `权限.*不足` / `Access Denied` / `No permission` / `无.*权限` |
| **action_type** | ASSIGN_OWNER |
| **修复模板** | 需要 Owner 或管理员在 ODPS 上对 Bot 使用的表授予 SELECT 权限。 |
| **验证方式** | 授权后触发 SQL 查询任务验证 |

---

## 5. 数据问题 → MANUAL

### 5.1 数据源过期/超保留期

| 字段 | 值 |
|------|------|
| **failure_class** | DATA_ISSUE |
| **匹配特征** | Judge reasoning 包含 `超出.*天` / `数据.*过期` / `不在.*有效期` / `超出.*保留` |
| **action_type** | ASSIGN_OWNER |
| **修复模板** | Bot 依赖的数据源有保留期限制，查询时间超出可查范围。需 Owner 协调数据源扩展保留期或迁移到支持历史数据的替代源。 |
| **验证方式** | 扩展保留期或切换数据源后，用典型查询时间范围验证 |

### 5.2 SQL Schema 不匹配

| 字段 | 值 |
|------|------|
| **failure_class** | DATA_ISSUE / TOOL_FAILURE |
| **component_type** | skill |
| **匹配特征** | Judge reasoning 包含 `no such column` / `SCHEMA_MISMATCH` / `字段.*不存在` / `column.*cannot be resolved` |
| **action_type** | DIRECT_EVOLUTION（字段名确定性修正）/ ASSIGN_OWNER（DDL 变更） |
| **修复模板** | 如仅字段名变更（`gmt_create` → `gmt_created`）：自动更新 Skill SKILL.md 中的 SQL 模板。如涉及表结构 DDL 变更：需 Owner 联系 DBA。 |
| **验证方式** | 触发 SQL 查询任务，确认字段正常解析 |

---

## 6. 网络/连接 → MANUAL

### 6.1 外部 API 不可达

| 字段 | 值 |
|------|------|
| **failure_class** | PERMISSION_NETWORK |
| **匹配特征** | Judge reasoning 包含 `exit code 7` / `Failed to connect` / `Connection refused` / `timeout` |
| **action_type** | ASSIGN_OWNER |
| **修复模板** | Bot 无法连接外部 API。需 Owner 排查防火墙规则、网络策略或 API endpoint 变更。 |
| **验证方式** | 从 Bot 运行环境 curl 目标 endpoint，确认可达 |

---

## 使用方式

Governance Agent 决策流程：

```
候选 → 查 fix-patterns.md
  ├── 精确匹配 → 直接用预定义 actionType + 修复模板
  ├── 模糊匹配 → 结合 ODPS+NAS 确认后使用
  └── 无匹配 → 按 decision-policy.md 通用规则推理
```

每条候选的 `sourceRuleId` 指向本模式库中的模式编号（如 `gov-mcp-param-fix`），方便追溯和统计哪些模式命中率最高。

---

## 维护方式

1. **新增模式**：发现新的可复用修复模式时，在本文件中按格式添加一行
2. **废弃模式**：MCP/Skill 大版本升级导致旧模式不再适用时，标记 `[已废弃]` 并注明废弃时间
3. **模式效果追踪**：通过 ClawWeb `GET /actions?sourceRuleId=xxx` 统计每个模式的通过率，通过率低的模式需要复查准确性
4. **后续**：当模式积累到一定数量（>20），迁移到 ClawWeb 的 `governance_rules` 表中，支持 Admin UI 管理和动态配置

### [从驳回学习-升级] 声纹专家 — PERMISSION_NETWORK 持续14天
| 字段 | 值 |
|------|------|
| **failure_class** | PERMISSION_NETWORK |
| **component_type** | 待补充 |
| **匹配特征** | 发现如果当前Agent由于搜索外网而失败，则需要将正确的方式写入TOOLS.md。当前错误由于Agent处于生产网，绝大部分外网的搜索都已经被禁用，如果需要搜索外网请使用 web-search-asap 技能 其中他的{baseDir} 通 |
| **action_type** | DIRECT_EVOLUTION（Admin 驳回时给出自动修复方案） |
| **修复模板** | 发现如果当前Agent由于搜索外网而失败，则需要将正确的方式写入TOOLS.md。当前错误由于Agent处于生产网，绝大部分外网的搜索都已经被禁用，如果需要搜索外网请使用 web-search-asap 技能 其中他的{baseDir} 通常在 /home/admin/.openclaw/workspace/skills 他其实是软链了 /home/admin/.openclaw/workspace/skills/skills-repo/infra/info/web-search-asap 通常一个人可能没有默认申请他的mcp的权限你可能需要走兜底方案 这里agent可能误判baseDir} 需要明确提出他在 /home/admin/.openclaw/workspace/skills/web-search-asap |
| **验证方式** | 触发同类任务，确认不再出现原错误 |
| **来源** | 驳回项 #90, 2026-08-25T10:16:12.000Z |
