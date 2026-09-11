---
name: clawweb-governance
description: 在 AIStudio 中基于 OpenClaw Judge、Session、Bot/Skill/MCP 离线数据和 NAS 当前状态，筛选真正值得治理的 user_id + bot_id 问题，判断自动修复或手动修复，并将高置信结论提交到 ClawWeb Admin Gate。用户要求运行 Governance Agent、生成治理候选、分析哪些 Agent 应修复、从失败 Session 创建改进项、判断自动/手动修复，或在 AIStudio 执行每日治理时使用。不要用它审批、绕过 Admin Gate、直接修改服务 Bot、验收修复结果或扫描无边界的大量 NAS 文件。
---

# ClawWeb Governance Agent

在 AIStudio 完成“离线召回 → NAS 核验 → 修复方式判断 → 创建候选”。ClawWeb 只接收最终候选，所有候选必须从 `PENDING_ADMIN` 开始。

## 运行环境

- AIStudio 运行时为 **Python 3.12**；脚本仅使用 Python 标准库，不依赖 Node.js。
- Skill 根目录：`/ossfs/workspace/jiangshen_skills`。

### ODPS 连接（必读）

使用 PyODPS 自动注入方式，无需手动配置 AK：

```python
from pypai.utils import env_utils
odps = env_utils.get_odps_instance()
# 默认项目: asec_aigame_dev，跨项目读生产表用 project 参数
```

不要使用本地 `odps` skill (`/ossfs/workspace/jiangshen_skills/odps/tools/execute_odps.py`)——那是为本地开发环境（~/.odps_config.json）设计的，AIStudio 中 PyODPS 开箱即用。

不要使用 Mac 的 `dataphin` CLI。

### NAS 路径（两个共享 NAS 根）

Bot 的当前配置和完整对话日志在以下只读 NAS 挂载：

```text
/home/admin/.bot_shared_nas/arca/arcaagentclaw/prod       # ~7000 bot
/home/admin/.bot_shared_nas_2/arca/arcaagentclaw/prod     # ~3000 bot
```

定位脚本：`bash /ossfs/workspace/jiangshen_skills/openclaw-nas-locator/locate_bot.sh USER_ID BOT_ID`

Bot 目录格式：`prod_staff_<USER_ID>_openclaw_<BOT_ID>`（含 `_DEVICE-*` 变体）

### 召回与日志

- 全量召回脚本：`python3 /ossfs/workspace/jiangshen_skills/openclaw-capability-governance-candidate/analyze_governance_candidates.py`。其 HIGH 置信只代表”值得深查”，不代表可以直接派发。
- `$kb-log-query` 只用于 ClawWeb 自身的 API、审批、OSS、通知和状态机异常，不用于普通 OpenClaw Runtime、Skill 或 MCP 故障。

## 不可违反的边界

- 生产 ODPS 表只读；写入只能是明确批准的 `_dev` 表。
- 不打印或保存 Cookie、Token、AK、MCP 凭据及 `openclaw.json` 中的敏感值。
- 先用 ODPS 缩小范围，再读 NAS；不得全量展开所有 Session。
- 平台共因只作为提示，不阻止给具体 Owner 创建候选；按 `user_id + bot_id` 推进。
- NAS 已证明存在明确、受控、可回滚修复方案时，优先产出 `DIRECT_EVOLUTION`。
- 涉及权限、凭据、业务规则、不可逆操作或方案不明确时，使用 `ASSIGN_OWNER`。
- 无论自动还是手动，提交后都必须是 `PENDING_ADMIN/PENDING`；不批准、不派发、不启动 Evolve。

## 核心流程

1. **取数据水位**：以 Judge 的 `MAX(dt)` 为结束日期，不假设当天分区已就绪。
2. **批量召回**：运行召回脚本（`analyze_governance_candidates.py`），得到 50～100 个粗候选，输出 `candidates.json`、`report.md`、`candidates.csv`。
3. **补 Bot 权重**：关联生产服务 Bot、发布、设备绑定、高保标签和当前 Skill/MCP 配置（如 `asecods` 表无权限则用召回数据中的 bot_name/failure 信息替代）。
4. **按根因拆分**：粒度必须是 `user_id + bot_id + failure_class + canonical_root_cause`，不能把一个 Bot 的不同根因合并。
5. **同时看跨度和数量**：
   - 跨度判断是否为持续问题，避免把同一天 Debug 产生的大量错误当成长期治理项；
   - 数量、失败 Session 和失败率衡量成功率收益，决定优先级；
   - 同日高量但之后成功恢复的进入观察；同日高量且 NAS 证明当前缺陷仍存在的可继续创建候选。
6. **定向 NAS 核验**：只读 3～5 个代表 Session、相关 Skill/MCP、`AGENTS.md`、`TOOLS.md` 和必要的非敏感配置字段。
7. **检查当前是否仍有问题**：确认失败后是否已经发布新版本、配置是否已修复、后续 Session 是否成功、是否已有相同改进项。
8. **决定修复方式**：按 [references/decision-policy.md](references/decision-policy.md) 输出 `CREATE_AUTO`、`CREATE_MANUAL`、`WATCH` 或 `DROP`。
9. **形成 Evidence Pack**：必须包含指标、当前配置事实、代表 Evidence、Owner 归属、建议动作和验收计划。见 [references/evidence-pack.md](references/evidence-pack.md)。
10. **反馈学习**：调用 `scripts/learn_from_rejects.py` 查询 ClawWeb 中最近 30 天的驳回项，读取 Admin 驳回理由，分类后追加到 [references/fix-patterns.md](references/fix-patterns.md)：
   - 驳回理由含"误判/不是问题/已恢复" → 添加 DROP 模式，下次同类不再创建
   - 驳回理由含"可以自动/应该是自动" → 升级为 DIRECT_EVOLUTION
   - 驳回理由含"无法自动/环境不支持/沙箱销毁" → 降级为 ASSIGN_OWNER
   见 [references/fix-patterns.md](references/fix-patterns.md)。
11. **候选不足放宽**：如本次去重后**新增候选**（与上轮 candidates.json 不重叠的 HIGH）< 5 个，执行放宽重跑：
   - 窗口 14 天 → **7 天**（聚焦最近一周）
   - 置信层级 HIGH → **HIGH ∪ MEDIUM Top 50**（按 governance_score 降序）
   - 最低阈值 `capability_fail_task_count >= 3`（原为 >= 10）
   重跑后再次去重，取新增的提交。如果仍不足 5 个，不再放宽（避免质量下降）。
12. **生成 HTML 报告**：在输出目录生成 `report.html`，包含完整的决策概览、候选人详情（含 NAS 核验状态）、WATCH/DROP 理由和全量 HIGH 候选清单。报告应自包含 CSS、按决策分类、每条候选带代表 Session 证据。见 [references/report-html.md](references/report-html.md)。
13. **去重检查（防重复打扰）**：提交前对每个 CREATE_AUTO/CREATE_MANUAL 候选，调用 ClawWeb GET API 查询同一 `ownerUserId + botId` 过去 15 天的改进项。如果存在同根因的 `REJECTED` 项、`PENDING_ADMIN` 项或 `IN_PROGRESS` 项 → **DROP**，不重复提交。见 [references/api.md](references/api.md) 第 2 节。
14. **提交候选**：只有通过去重检查的 `CREATE_AUTO/CREATE_MANUAL` 调用 `scripts/submit_candidate.py`，先 dry-run 再正式提交。返回必须为 `status=PENDING_ADMIN`、`adminReviewStatus=PENDING`。

## AIStudio 常用命令

**Step 0: 取数据水位**
```python
from pypai.utils import env_utils
odps = env_utils.get_odps_instance()
with odps.execute_sql("SELECT MAX(dt) FROM asec_aigame.dws_sec_log_teamclaw_arca_openclaw_judge_result_di").open_reader() as r:
    for row in r: print(row['max_dt'])
```

**Step 1: 粗召回**（输出 candidates.json / report.md / csv）
```bash
python3 /ossfs/workspace/jiangshen_skills/openclaw-capability-governance-candidate/analyze_governance_candidates.py \
  --end-date YYYYMMDD \
  --window-days 14 \
  --output-dir /ossfs/workspace/openclaw_capability_governance_candidates
```

**Step 2: 深查候选失败详情**（ODPS）
```python
from pypai.utils import env_utils
odps = env_utils.get_odps_instance()
sql = """
SELECT user_id, bot_id, session_id, dt,
    llm_first_task_failure_class, llm_first_task_reasoning,
    llm_skill_names, llm_mcp_names, llm_skill_failure_category
FROM asec_aigame.dws_sec_log_teamclaw_arca_openclaw_judge_result_di
WHERE dt >= 'START' AND dt <= 'END'
  AND (user_id='UID' AND bot_id='BID')
  AND llm_task_failure_classes IS NOT NULL
ORDER BY dt DESC LIMIT 50
"""
```

**Step 3: NAS 核验**（定位 Bot + 检查配置 + 读 Session）
```bash
bash /ossfs/workspace/jiangshen_skills/openclaw-nas-locator/locate_bot.sh USER_ID BOT_ID
```
NAS 上必须检查：Skill SKILL.md 是否存在、openclaw.json 的 MCP 配置、代表 Session 的 `.jsonl`（非 trajectory）中的具体错误。

**Step 4: 去重检查**（防止重复打扰同一 Owner）

`scripts/dedup_candidates.py` 支持三种模式：

```bash
# auto 模式（推荐）：先尝试 ClawWeb API，不可用时自动回退本地对比
python3 /ossfs/workspace/jiangshen_skills/clawweb-governance/scripts/dedup_candidates.py \
  --new /ossfs/workspace/openclaw_capability_governance_candidates/20260823/candidates.json \
  --old /ossfs/workspace/openclaw_capability_governance_candidates/20260817/candidates.json

# local 模式：强制只做本地对比
python3 .../dedup_candidates.py --new ... --old ... --mode local

# api 模式：强制只查 ClawWeb（需 GET /actions 已部署）
python3 .../dedup_candidates.py --new ... --mode api --base-url https://clawweb-pre.alipay.com
```

去重规则：
- API 模式：同 user+bot 在过去 15 天有 REJECTED / PENDING_ADMIN / IN_PROGRESS → 跳过
- Local 模式：同 user+bot 在上次 candidates.json 的 HIGH 中已出现 → 跳过
- 输出覆盖原 candidates.json，HIGH 去重后与非 HIGH 合并

**Step 5: 生成 HTML 报告**（输出到召回目录，与 candidates.json 同级）

报告必须自包含 CSS、按决策分层（CREATE_MANUAL → WATCH → DROP）、每条候选带代表 Session 证据和 NAS 核验状态。结构：
- 统计卡片（Bot 总数、浪费时长、各决策数量）
- 失败类分布表
- CREATE_MANUAL 候选详情（根因、指派理由、建议动作、NAS 状态、代表 Session）
- WATCH / DROP 表（含理由）
- 全量 HIGH 候选清单
- 分析方法说明

**Step 5: 提交候选**（先 dry-run 验证格式）
```bash
export CLAWWEB_URL="https://clawweb.alipay.com"
python3 /ossfs/workspace/jiangshen_skills/clawweb-governance/scripts/submit_candidate.py \
  --input /tmp/candidate.json --dry-run
# 正式提交
python3 /ossfs/workspace/jiangshen_skills/clawweb-governance/scripts/submit_candidate.py \
  --input /tmp/candidate.json
```

## 输出物

每次运行在 `{output-dir}/{dt}/` 下生成：

| 文件 | 说明 |
|------|------|
| `candidates.json` | 召回脚本输出的全量候选（含评分） |
| `candidates.csv` | CSV 格式 |
| `report.md` | 召回阶段的 Markdown 报告 |
| `report.html` | **治理决策 HTML 报告**（自包含 CSS，按决策分层，含 NAS 核验） |
| `evidence/` | 逐候选证据（代表 Session、根因分析） |
| `submit/` | ClawWeb 提交候选 JSON（仅 CREATE_AUTO/CREATE_MANUAL） |

## 成功标准

- 根因不是只由失败分类推断，而是得到代表 Session 与当前配置事实支持。
- `assignmentReason` 能具体解释为什么这个 Owner 可推进，而不是通用文案。
- HTML 报告完整展示决策分层、NAS 核验状态和代表 Evidence，可直接用于 Admin 评审。
- 自动修复方案明确到目标文件/Skill/MCP/工作流配置、修改范围、风险和回滚方式。
- 候选附带修复后验收标准。
- 返回必须为 `status=PENDING_ADMIN`、`adminReviewStatus=PENDING`、`createdBy=governance-agent`。

详细数据源见 [references/data-sources.md](references/data-sources.md)，完整判断规则见 [references/decision-policy.md](references/decision-policy.md)，HTML 报告规范见 [references/report-html.md](references/report-html.md)。
