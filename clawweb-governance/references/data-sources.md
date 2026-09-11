# AIStudio 数据源与工具分工

## 1. ODPS 事实数据

必须通过 `$odps-aistudio` 使用 PyODPS。生产项目只读，每条探索性 SELECT 必须有限定分区和 `LIMIT`。

| 数据 | 表 | 作用 |
|---|---|---|
| Judge Task 结果 | `asec_aigame.dws_sec_log_teamclaw_arca_openclaw_judge_result_di` | Task 切分、失败分类、reasoning、Session、时间、Skill/MCP 执行摘要 |
| Session 全量 | `asec_aigame.dws_sec_log_teamclaw_arca_openclaw_session_full_di` | Bot 名称、时长、来源、工具与完整会话补充 |
| Task 分类长表 | `asec_aigame.dws_sec_teamclaw_arca_task_complete_cate_di` | 全部 Task 分母、能力完成率和失败率 |
| 配置文件快照 | `aseccdm.dwd_sec_log_teamclaw_arca_openclaw_bot_config_file_dd` | T+1 的 AGENTS/TOOLS/IDENTITY/BOOTSTRAP 快照，适合批量初筛 |

进入治理召回的分类：

```text
CAPABILITY_BOUNDARY
TOOL_FAILURE
WORKFLOW_FAILURE
CONFIG_MISSING
PERMISSION_NETWORK
DATA_ISSUE
OUTPUT_WRONG
PARAMETER_ERROR
```

等待用户、异步未完成、无回复、任务中断等默认不创建治理项，除非 NAS 证明根因实际是当前配置或 Skill 缺陷。

## 2. Bot 与生产权重

| 表 | 作用 |
|---|---|
| `asecods.ods_ac_bots_agentclawdb` | Bot 类型、Owner、状态 |
| `asecods.ods_ac_bot_publish_agentclawdb` | 发布阶段和当前线上版本；若未导出，需要继续申请 |
| `asecods.ods_ac_entity_device_binding_agentclawdb` | 线上实例、设备绑定和存活状态 |
| `sec_teamclaw_dim_high_value_bot_dd` | 高保/高价值标签 |

生产服务 Bot 判定建议：

```text
bot_type = service
status = ACTIVE
is_delete = 0
env = prod
publish.status = success
publish.env = prod
publish.ext.binding.online 非空
```

始终以 `owner_id/user_id + bot_id` 关联，不能只用 `bot_id`，尤其是 `default` Bot。

## 3. 当前能力配置

| 表 | 作用 |
|---|---|
| `asecods.ods_ac_skill_set_agentclawdb` | 当前激活 Skill Set |
| `asecods.ods_ac_skill_set_skill_agentclawdb` | Skill Set 与 Skill 关系 |
| `asecods.ods_ac_skill_agentclawdb` | Skill 定义、版本、状态 |
| `asecods.ods_ac_skill_set_mcp_agentclawdb` | MCP 配置关系 |
| `asecods.ods_ac_harness_scan_record_agentclawdb` | Harness 扫描问题、健康分、发布版本 |
| `asecods.ods_ac_harness_patch_record_agentclawdb` | Patch 应用、失败和回滚状态 |

离线表用于大规模判断，NAS 用于确认当前真实状态。

## 4. NAS 深度证据

调用：

```bash
bash /ossfs/workspace/jiangshen_skills/openclaw-nas-locator/locate_bot.sh USER_ID BOT_ID
```

需要检查：

- 两个共享 NAS 根；
- `openclaw`、`claude_code` 和 `_DEVICE-*` 变体；
- 所有 `.openclaw/agents/<agent>/sessions/`，不只 `main`；
- 明确 Session 的 `.jsonl` 和必要的 `.trajectory.jsonl`；
- 当前 `openclaw.json` 中非敏感的 Skill/MCP 路径和启用状态；
- 相关 Skill 的 `SKILL.md`；
- 工作区 `AGENTS.md`、`TOOLS.md`；
- 修复后或最新 Session 是否已恢复。

安全要求：原始文件先检查字节数、行数和最长行；只抽取目标 Task、相关工具调用和必要配置字段。禁止输出配置中的凭据。

## 5. 日志工具

`$kb-log-query` 只用于 ClawWeb：

- Governance API；
- Admin 审批；
- OSS/Mist；
- 钉钉通知；
- Verification 状态机。

普通 Skill、MCP、OpenClaw Runtime 或平台工具共因不应固定查询 `clawweb/start.log`；应优先用 ODPS 跨 Bot 统计、NAS 工具返回和对应应用的可观测日志。
