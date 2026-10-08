---
name: clawevolve-pack
description: "把当前 bot 的可变配置物料（persona md / MCP / 个人 Skill 实体与激活入口）打成不可变镜像（.zip），用于基线回归、恢复和版本管理。Skill 层支持 nested/sibling 个人目录、断链和布局错配；不打包 skills-repo/skills-center 公共只读 Skill，也不打包 ClawEvolve Release Skill。触发词：打包 bot、bot 镜像、复现 bot 环境、打镜像、baseline、回归、pack、image、版本快照、snapshot。"
allowed-tools: Read, Bash
---

# clawevolve-pack — agent 镜像打包（基线回归用）

## ClawWeb Task 模式

收到 `/clawevolve-pack` 指令且包含 `--task-id/--step-id` 时，按 `--mode` 调用统一 Task Handler：

```bash
# 创建 Pack
python3 -u -B ../clawevolve-workflow/scripts/handlers/clawevolve_pack_run.py \
  --mode pack --task-id <task-id> --step-id <step-id>

# 恢复 Pack
python3 -u -B ../clawevolve-workflow/scripts/handlers/clawevolve_pack_run.py \
  --mode restore --task-id <task-id> --step-id <step-id> \
  --source-task-id <source-task-id> --source-kind <baseline|snapshot|round> \
  [--source-round <round>]
```

Task 模式只负责解析并原样传递结构化参数；Pack/Restore、ClawWeb 输入读取与终态上报仍由
`clawevolve-workflow` 的统一 Handler 完成。没有 Task 参数时，继续使用本 Skill 的 `scripts/pack.sh` 独立打包能力。

把当前 bot 自身的 `workspace` 物料物化成一个**不可变、自包含**的 `.zip` 镜像。
目的不是在线 AB，是**基线回归**：开发一个版本 → 打成不可变镜像 → 部署到干净环境 → 跑同一套固定 benchmark → 出报告 → 与任意历史版本横向对比指标涨跌。镜像 `write-once`，可离线/可重放/可重算核对。

## 范围与 1:1 限定（第一步）

本步只打**配置物料层 + 自进化产物层**：`md / mcp / skill / clawevolve_results`。这是最基本的、最先交付的。
- **1:1 语义**：本步复现的是「agent 配置物料层一致」。完整运行栈（模型/记忆/插件/身份/会话）的 1:1 属已知缺口，**不在此步强求**，由 `--with-*` 扩展开关后续打 patch 补齐（见下）。
- 这套范围对**纯基线回归**是正确且足够的：模型/插件/身份由评测载体（eval-carrier bot）固定提供，被测变量恰是 md/mcp/skill。

## 物料处理

读载体 bot 自己的 `workspace`（默认 `~/.openclaw/workspace`，可 `--workspace` / `$WORKSPACE` / `$OPENCLAW_HOME/workspace` 覆盖）：

| 物料 | 来源 | 处理 |
|---|---|---|
| **md** | `workspace/*.md`（顶层） | 直接拷实体。**排除** `MEMORY.md`、`HEARTBEAT.md`（引擎运行时状态/健康检查，非配置；与 memory 延迟一致）。保留 SOUL/AGENTS/TOOLS/IDENTITY/RULES/OKR/USER/BOOTSTRAP 等 persona 配置。 |
| **mcp** | `workspace/config/mcporter.json`（兜底 `workspace/.mcporter.json`） | 按原始字节复制，**不解析、不格式化、不脱敏、不扫描、不丢字段**；重复 JSON key、原始空白及暂时非法的 JSON 均保留。无文件或源软链断链时记 skipped。 |
| **skill** | `workspace/skills/skills-local/`、`workspace/skills-local/` 及个人激活入口 | 保存 nested/sibling 个人 Skill 实体，以及 `workspace/skills/` 下语法上指向个人目录的激活软链；允许断链和布局错配。公共仓库入口、指向公共 Skill 的软链、`clawevolve-*`、`clawbench-*`、`ocb-*` 及其同步备份不打包。`.nfs*` 忽略，其余个人 Skill 内文件、空目录和软链保真。 |
| **clawevolve_results** | `/home/admin/.openclaw/workspace/clawevolve_results/<evolve-run-id>`（通过 `--evolve-run-id`、`--evolve-results-dir` 或 `$EVOLVE_RUN_DIR` 绑定） | 整目录拷入 `package/clawevolve_results/`，包含 run 级 `optimize/input/objective.md` / `run_manifest.json` 和 round 级 `optimize/output/round-001/input/spec-v0.md`、bench normalized result、tune report/diff、`optimize/output/round-001/spec/spec-v1.md` 等自进化 MVP 产物。目录采用 `{diagnose,plan,optimize}/{input,output}` 三层结构，pack 按 run 级别整体拷贝。`.DS_Store`、`__pycache__`、`*.pyc` 这类噪声跳过。未绑定 run、目录不存在，或大目录/远端挂载 I/O 竞态导致复制失败时，该 layer 记 skipped 并写明 reason，不阻断普通 pack；不要默认打包全局 `clawevolve_results/` 根目录，避免同 bot 多个 run 串包。 |

### clawevolve_results run 绑定优先级

`clawevolve-pack` 不再默认打包 `/home/admin/.openclaw/workspace/clawevolve_results` 整个根目录，因为同一个 bot 可能同时有多个 evolve run。绑定优先级：

```text
1. --evolve-results-dir /home/admin/.openclaw/workspace/clawevolve_results/<evolve-run-id>
2. --evolve-run-id <evolve-run-id>
3. $EVOLVE_RUN_DIR
4. 未绑定则 clawevolve_results layer skipped
```


Skill root 除 Release entry 和 `.nfs*` 外不做内容分类；其他 layer 仍按各自表格中的既有边界处理。

## MCP 明文策略

当前本地自进化流程允许镜像保留源 `mcporter.json` 内容；Pack 不再执行脱敏和零明文扫描。该策略只适用于受控的本地演化链路，发布到共享/线上存储前仍需由调用方单独评估访问控制和敏感信息风险。

## 用法

```bash
# 最基本：从当前 bot workspace 打一个镜像到当前目录
./scripts/pack.sh --version 1.0.0

# 指定源 workspace / 输出目录 / bot id
./scripts/pack.sh --version 1.0.0 --workspace ~/.openclaw/workspace --out-dir ./images --bot-id mybot

# 绑定单次自进化 run 后，会纳入该 run 的 clawevolve_results
./scripts/pack.sh --version 1.0.0 --evolve-run-id ev_20260723_151230_a3f91c

# 或直接传 run_dir，优先级最高
./scripts/pack.sh --version 1.0.0 --evolve-results-dir /home/admin/.openclaw/workspace/clawevolve_results/ev_20260723_151230_a3f91c

# 也可由 orchestrator 设置环境变量
EVOLVE_RUN_DIR=/home/admin/.openclaw/workspace/clawevolve_results/ev_20260723_151230_a3f91c ./scripts/pack.sh --version 1.0.0

# 打完即上传 clawweb（可选）
./scripts/pack.sh --version 1.0.0 --upload-url https://clawweb/api/agent-images

# 明知某 skill 里有占位符样例（如 api_key=YOUR_KEY）想放行
./scripts/pack.sh --version 1.0.0 --ignore-secret-pattern 'YOUR_KEY|<.+>' --ignore-secret-pattern 'example'
```

产物：`<id>__v<version>__<sha8>.zip`，内含 `package/agent.image.yaml`（清单 + sha + 原文保留策略）、`README.md`、`md/`、`mcp/mcporter.json`、`skills/`、`skills-local/`、`clawevolve_results/`。文件名 sha8 = manifest `source.contentDigest` 前 8 位（各 layer name+sha256 摘要的前 8 位），deploy 侧读 manifest 与文件名 sha8 比对一致性，防传输损坏/版本错位。zip 通过 `ZipInfo.external_attr` 高 16 位保存目录、普通文件及 symlink 的 Unix 类型/必要权限；软链条目内容是原始 `readlink` target，与 .tgz 行为对等。

验证：解包后检查 `package/agent.image.yaml` 的 schema v3 个人 Skill 快照；新 Pack 声明 `snapshot.scope: personal-skills` 和 `snapshot.digestAlgorithm: personal-skill-snapshot-v1`。校验归档路径安全、文件可读取、文件/目录/软链类型和产物摘要一致，不检查软链可达性、Skill 实体布局或 `SKILL.md`。

## 扩展点（留 patch 洞，本步不实现）

`pack.sh`/`pack.py` 用「按物料类型 handler 注册表」驱动，manifest `layers` 为开放数组。当前 Pack/Deploy 使用 schema v3 同代契约，不与 schema v1 交叉使用。后续 patch 新增打包项 = 加一个 handler + 一个 `--with-<layer>` 开关 + 一条 manifest layer，不改既有逻辑。预留开关**当前命中即报『未支持/待 patch』**：

| 开关 | 含义 | 补齐优先级 |
|---|---|---|
| `--with-model` | 模型 id+参数（apiKey→auth_ref），来自 openclaw.json `.defaults.models` / agents models.json | ① 最高（对行为影响最大） |
| `--with-memory` | `workspace/memory/` 与 MEMORY.md | ② |
| `--with-plugins` | 插件/扩展 + 运行时旋钮（compaction/tools.profile/skills.entries/plugins.entries） | ③ |
| `--with-activeset` | 活跃技能集元数据（skill_sets.json/.current_skill_set/skill_parameters.json） | ③ |
| `--with-identity` | device identity（跨机器需重新配对） | ④ |
| `--with-sessions` | 会话历史 `.jsonl` | ④ |

部署（clawevolve-deploy，三段式存档/铺 vN/回滚）与对话回归属后续步骤，不在本 skill 范围。

## 安全提示

镜像含 persona/技能/MCP 原文（可能包含凭证和其他敏感信息）。必须按敏感文件对待，OSS 仓库设访问控制。`write-once`：同版本号重复上传应被 clawweb 拒绝。
