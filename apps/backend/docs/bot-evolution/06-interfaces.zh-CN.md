# 接口——API、SDK、CLI 与 bot 驱动的进化

> English version: [06-interfaces.md](06-interfaces.md)

> 状态：DRAFT（讨论稿）。回答「用 API/SDK 还是 CLI，以及 bot 如何驱动递归自改进
> （RSI）？」

> **已推迟：bot 调用方。** bot 如何与平台通信尚未确定，因此 DR-3 已推迟
> （[decisions/0003](decisions/0003-bot-principal-for-evolution-surface.zh-CN.md)）。
> 下文中凡是由 bot 调用平台的内容（参与者 C 和 D、§4 中的 bot skill、§5，以及
> §6 中的 bot scope）都保留作为那项后续决策的输入，而不属于第一轮迭代范围。
> 在第一轮迭代中，调用方是管线和人类（参与者 A 和 B），进化策略以平台作业
> worker 的身份运行，而不是以 bot 的身份运行。

## 1. 一段话的建议

构建**一个资源 API**（OpenAPI，位于 `/openapi/v1/evolution/*` 之下，外加
`/openapi/v1/bots/{id}/genome/*` 下的基因组端点），其余一切都从它派生：面向确定
性代码的**生成式客户端 SDK**、面向编写进化方法的人员的独立**进化策略（插件）
SDK**，以及构建于客户端 SDK 之上、服务人类和 CI 的**轻薄 `avn` CLI**。
如果以后 bot 要调用平台（已推迟，见上文），提议是让它们通过随附的 `SKILL.md`
使用同一个 CLI，以**带窄 scope 的 bot 主体**身份认证，与当今的 `bcs-cli` 一样，
而不是单独构建一个「bot API」：bot 将只是一个权限更少的调用方。如果某个引擎需要，以后可以从同一 API 生成一个 MCP server。

## 2. 谁驱动进化——四类参与者

一旦把参与者区分开，模糊之处就消失了。它们需要的是不同的*权限*，而不是不同的
*API*。

| 参与者 | 示例 | 接入面 | 典型调用 | 能否晋升？ |
| --- | --- | --- | --- | --- |
| **A. 确定性管线** | 夜间任务、skill 变更后的 CI、某个产品后端 | 客户端 SDK / REST | 启动运行、轮询、读取报告、按策略批准 | 仅限所有者配置的自动晋升策略范围内 |
| **B. 人类操作员** | bot 所有者、租户管理员、研究人员 | UI、CLI | 全部操作、评审队列、回滚 | **能**（经评审） |
| **C. 执行者 Bot**（bot *作为优化器*）*（已推迟）* | ClawEvolve tune agent、一个进化其他 bot 的「教练」bot | 通过 CLI + skill 使用作业协议 | 认领作业、读取输入、物化沙箱、提交补丁 / 得分 | 否 |
| **D. 被改进 Bot**（bot *改进自身*）*（已推迟）* | 发现自己在退款问题上反复失败的客服 bot | CLI + skill | 读取自己的 active 基因组、记录观察、向收件箱提交补丁草稿、请求一次运行 | **永不** |

关键洞察：「由 bot 驱动的 RSI」其实是两件不同的事——bot 为一次运行做优化工作
（C），以及 bot 请求被改进（D）。两者在同一 API 上都是安全的，因为谁都不能移动
`active`。

## 3. 资源 API 草图

面向资源，长任务异步化，POST 带幂等键，可变资源带 ETag。规范性规格见工作项
RSI-07。

```text
# Genome (Backend)
GET    /bots/{bot}/genome/revisions?status=&parent=
GET    /bots/{bot}/genome/revisions/{rev}
POST   /bots/{bot}/genome/revisions                {base, patch} | {manifest}   → candidate/draft revision
GET    /bots/{bot}/genome/revisions/{rev}/diff?against={rev}
GET    /bots/{bot}/genome/refs
PUT    /bots/{bot}/genome/refs/draft               {revision, expected_revision}
POST   /bots/{bot}/genome/promotions               {revision, reason}              (owner / gate; going back = promoting an earlier revision)
GET    /bots/{bot}/genome/content/{digest}         → bytes (authorized against the bot)
PUT    /bots/{bot}/genome/content                  bytes → {digest}

# Evolution (apps/evolution)
GET    /evolution/strategies                       ?engine=&gene=
POST   /evolution/strategies                       register / new version
POST   /bots/{bot}/evolution/runs                  {strategy, version?, params?, budget?, trigger}
                                                   Idempotency-Key header → 202 {run_id}
GET    /bots/{bot}/evolution/runs/{run}            status, iterations, budget used
GET    /bots/{bot}/evolution/runs/{run}/candidates
POST   /bots/{bot}/evolution/runs/{run}:cancel
GET    /bots/{bot}/evolution/candidates/{cand}/report   diff + eval comparison + gate decision
POST   /bots/{bot}/evolution/candidates/{cand}:approve  | :reject   (review queue)
GET    /bots/{bot}/evolution/policy                enabled strategies, schedules, auto-promote ceiling
PUT    /bots/{bot}/evolution/policy

# Experience (apps/evolution)
POST   /bots/{bot}/experience/observations         fast-loop note from subject bot (postponed)
POST   /bots/{bot}/experience/feedback             rating / correction / outcome
GET    /bots/{bot}/experience/episodes?revision=&since=&outcome=

# Inbox (fast loop; postponed with DR-3)
POST   /bots/{bot}/evolution/inbox                 draft patch from subject bot
GET    /bots/{bot}/evolution/inbox

# Evaluation (apps/evolution, read mostly)
GET    /evolution/suites/{suite}                   cases visible per caller role
POST   /bots/{bot}/evolution/evaluations           evaluate a revision on a suite (operator only)

# Jobs (Job Protocol for strategy workers) — see 05-strategy-sdk.md §9
```

异步模式：启动一次运行会返回 `202` 和一个**运行 id**。提交是幂等的，并由平台
保证：使用相同 `Idempotency-Key` 的重复 POST 会返回同一个运行 id，不会启动任何
新内容。此后，运行 id 是唯一的句柄；调用方通过 `GET …/runs/{run}` 查询状态。
不需要回调通道。CLI 的 `--wait` 只是重复这一查询。运行在崩溃和重启后仍以同一
id 存续（[05-strategy-sdk.zh-CN.md §7](05-strategy-sdk.zh-CN.md#7-运行生命周期)）。

## 4. CLI 设计（`avn`）

让一个 CLI 同时适合人类和 agent 的要求：

- **轻薄**：每条命令都是一到两次 SDK 调用。不包含 API 所没有的逻辑——否则 bot
  与管线的行为会分化。
- **机器优先的输出**：stdout 不是 TTY 时默认 `--output json`；字段名稳定；
  **退出码稳定**（0 成功、2 用法错误、3 未找到、4 冲突/CAS、5 策略拒绝、6 超出
  预算、7 暂时性错误）。
- **非交互**：除非指定 `--interactive`，否则不弹提示；破坏性操作需要 `--yes`；
  凡有意义之处都支持 `--dry-run`。
- **自描述**：`avn <cmd> --help --output json` 输出命令 schema，使 agent 无需
  灌入整份文档即可发现参数。
- **像 `bcs-cli` 那样从环境获取认证**：人类和 CI 使用 OAuth device flow / token；
  明确记录优先级。（bot 使用 `$BOT_DATA_DIR` 下的会话文件随 DR-3 已推迟。）
- **可交付到每个引擎** *（当 bot 调用它时；已推迟）*：单一静态二进制（Rust，与 `bcs-cli` 一样）或 Python
  zipapp；通过 Manifest（Bot 配置清单）的 `cli_tools` 交付给 bot，该通道已在
  teclaw 上可用。

命令树（示意）：

```text
avn genome  show|log|diff|refs|promote|content get|put|patch apply --dry-run
avn evolve  run start|status|cancel|report  ·  strategies list|show  ·  policy get|set
avn evolve  review list|show|approve|reject
avn evolve  inbox submit|list                     # 被改进 Bot（已推迟）
avn evolve  observe "<note>" --episode <id>       # 被改进 Bot（已推迟）
avn experience episodes|feedback
avn job     claim|input|heartbeat|upload|complete|fail   # 进化策略作业 worker
avn strategy dev|test|publish                     # 进化策略作者（封装策略 SDK 测试环境）
```

### bot skill *（随 DR-3 已推迟）*

随附 `skills/avernet-evolution/SKILL.md`（agentskills.io 格式），告诉 bot *何时*
以及*如何*使用它被允许的那部分命令：

- 被改进 Bot 的 skill：「当任务失败或用户纠正你之后，运行
  `avn evolve observe`，附上一行教训和片段 id。如果你认为某个人设或 skill 的变
  更可以防止重复失败，用 `avn evolve inbox submit` 起草它。你不能将变更应用到自
  己身上。」
- 执行者 Bot 的 skill：作业循环、沙箱规则和输出契约。

渐进式披露使其在上下文中保持低成本：描述始终加载；正文仅在相关时加载。

## 5. 对 bot 而言，为什么选 CLI（+skill）而不是 MCP 或原生工具 *（随 DR-3 已推迟）*

| 选项 | 优点 | 缺点 |
| --- | --- | --- |
| **CLI + SKILL.md**（推荐首选） | 有先例（`bcs-cli` + `bcs-coordination` skill）；交付通道已存在（`cli_tools`，含 teclaw）；人类和 CI 使用同一二进制；引擎中立；可测试（singlebox 已对 `bcs-cli` 叶子命令覆盖率设门禁） | 需要引擎提供 `exec` 工具；需要模型解析输出 |
| MCP server | 类型化工具；部分引擎更偏好它 | 又一处部署 + 按引擎的 MCP 配置；teclaw 的 MCP 只能经由 Center；接入面重复 |
| 引擎原生工具（如 Hermes `skill_manage`） | 体验最紧凑 | 需按引擎实现；违反引擎中立；未经评审的自我写入正是我们必须避免的 |

如果某个引擎缺少 `exec`，以后（P6）再从 OpenAPI 生成 MCP 适配器。

## 6. 认证与授权

OpenAPI v1 目前有意拒绝 `bot` 主体，第一轮迭代也保持这一点。调用方是使用现有
凭据的用户、租户管理员和管线；进化策略作业 worker 是平台服务，而不是 bot。下表
是 DR-3 提议的 scope，用于**仅**在进化接入面上接纳 bot。它已推迟，直到 bot 与
平台的通信方式确定为止：

| Scope | 授予对象 | 允许 |
| --- | --- | --- |
| `genome:read:self` | 被改进 Bot | 读取自己的 `active` 修订版和 diff 历史 |
| `experience:write:self` | 被改进 Bot | 为自身提交观察和反馈 |
| `inbox:write:self` | 被改进 Bot | 为自身提交补丁草稿 |
| `run:request:self` | 被改进 Bot（由所有者选择开启） | 在预算内请求运行一个*已启用*的进化策略 |
| `evolution:runner` | 执行者 Bot / worker | 为已注册的策略 id 认领作业；针对其已认领的运行调用作业协议 |
| — | 仅限所有者/管理员/策略 | 晋升、回滚、修改策略、启用进化策略、触碰锁定基因 |

bot 凭据来自现有的 Passport/AgentPass 签发；网关以 `kind: bot` 和 scope 签署
`X-Avernet-Principal`；Backend 与 Evolution 通过 R12 授权钩子检查 scope。`self`
绑定到凭据中的 bot id，永远不绑定到请求参数。

## 7. 面向确定性管线的 SDK 易用性

```python
# 仅作示意
from avernet_evolution import Client

c = Client.from_env()
run_id = c.runs.start(bot="bot_123", strategy="clawevolve/bot-evolution",
                      budget={"max_usd": 10}, idempotency_key="nightly-2026-10-08")
# 可以安全重复：相同的键返回相同的 run_id
run = c.runs.get(run_id)            # 按 id 查询状态；重复查询直到 run.status 进入终态
for cand in run.candidates(accepted=True):
    report = cand.report()
    if report.risk_tier <= policy.auto_tier and report.gate.passed:
        cand.approve(reason="nightly auto-policy")
```

与 CLI 发出的调用相同；与 UI 后端发出的调用也相同。

## 8. 开放问题

- Q1：`avn` 是一个新的二进制，还是某个现有 CLI 的子命令组？（目前没有通用的
  Avernet CLI；`bcs-cli` 仅限 BCS 范围。）建议：用 Rust 新建 `avn`，遵循
  `bcs-cli` 的约定，并为日后的其他平台命令留出空间。
- Q2 *（随 DR-3 已推迟）*：是否默认允许被改进 Bot 拥有 `run:request:self`？
  建议默认关闭，由所有者按策略开启并设置每日预算。
