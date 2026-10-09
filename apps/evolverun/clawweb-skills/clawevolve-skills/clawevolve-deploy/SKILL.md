---
name: clawevolve-deploy
description: "把 clawevolve-pack 产出的 agent 镜像（.zip）铺设到 bot workspace，复现原始 bot 的 md/mcp/skill 配置环境，并可同步恢复单次自进化 clawevolve_results/{evolve-run-id} 产物，用于基线回归与 AB 评测。三段式：校验镜像/OSS下载 → 备份当前 workspace 和 evolve run → 铺入镜像 → 校验铺出结果；失败回滚。触发词：部署镜像、加载 bot、复现 bot 环境、deploy、restore、还原 bot、铺镜像、image deploy、从OSS拉取镜像。"
allowed-tools: Read, Bash
---

# clawevolve-deploy — agent 镜像部署（基线回归复现侧）

把 clawevolve-pack 产出的不可变 `.zip` 镜像铺设到目标 bot 的 `workspace`，使目标 bot 的 md/mcp/skill 配置层与原 bot 一致。若镜像包含 `package/clawevolve_results/`，也可恢复到绑定的 `/home/admin/.openclaw/workspace/clawevolve_results/{evolve-run-id}`。它是 clawevolve-pack 的对偶步骤，二者共同构成「打镜像 → 上传 OSS/clawweb → 拉取/复现 → 跑同一 benchmark → 横向对比」闭环。

## 范围与 1:1 限定

本步默认铺 **配置物料层 + 自进化产物层**：`md / mcp / skill / clawevolve_results`，与 clawevolve-pack 当前范围对齐。

- `md/mcp/skill` 铺到目标 bot `workspace`。
- `clawevolve_results` 铺到单个明确 run 目录，不铺全局根目录。
- 模型/记忆/插件/身份/会话不在此步处理，归 `--with-*` 扩展，当前命中即报待 patch。

## 三段式

```text
校验镜像/下载  ──→  备份+铺入  ──→  校验铺出
      ▲                                │
      └────────── 失败即回滚 ◀─────────┘
```

### 1. 校验镜像 / 从 OSS 拉取

- 本地镜像：`--image /path/to/image.zip`。
- 远端镜像：`--image-url <clawweb-or-oss-signed-url>`，脚本下载到临时文件后按本地镜像处理。
- 解 `package/agent.image.yaml`，读 manifest（schemaVersion / layers / source / contentDigest）。
- 校验文件名内嵌 sha8 与 manifest `source.contentDigest[:8]` 一致性，防传输损坏/版本错位。
- 校验 layer 清单完整性：`md/`、`skills/` 必须存在；`mcp`、`clawevolve_results` 可 skipped。

说明：deploy 不直接依赖 OSS SDK。clawweb 可以传 signed URL 或代理下载 URL 给 `--image-url`；pack 侧仍用 `--upload-url` 上传。

### 2. 备份 + 铺入

备份：

- workspace 备份：覆盖个人 Skill 实体与激活入口、manifest md 集合和 MCP 配置；公共只读 Skill 与 Release Skill 不进入事务备份。
- clawevolve_results 备份：若镜像含 `clawevolve_results` 且目标 run dir 已存在，也备份到 `workspace/.deploy-backups/`。

清空/铺入：

- `package/md/*.md` → `workspace/*.md`
- `package/mcp/mcporter.json` → `workspace/config/mcporter.json`
- `package/skills/skills-local/*` → nested 个人 Skill 实体
- `package/skills-local/*` → sibling 个人 Skill 实体
- `package/skills/<name>` 中指向个人目录的入口 → `workspace/skills/<name>`
- 文件、目录和软链按归档类型恢复；软链仅恢复原始 target，不跟随也不要求可达
- `skills-repo`/`skills-center`、公共 Skill 激活入口及 `clawevolve-*`、`clawbench-*`、`ocb-*` Release entry 不清理、不覆盖
- `package/clawevolve_results/` → `/home/admin/.openclaw/workspace/clawevolve_results/{evolve-run-id}/`

`clawevolve_results` 目标 run 绑定优先级：

```text
1. --evolve-results-dir /home/admin/.openclaw/workspace/clawevolve_results/{evolve-run-id}
2. --evolve-run-id <evolve-run-id>
3. $EVOLVE_RUN_DIR
4. manifest 中 clawevolve_results layer 的 source basename
```

不要扫描 `/home/admin/.openclaw/workspace/clawevolve_results` 的最新目录，也不要铺到全局根目录。若目标 run dir 已存在，默认拒绝覆盖；传 `--force-overwrite` 才替换，并在失败时回滚。

### 2.5. MCP 原始恢复

`package/mcp/mcporter.json` 按原始字节恢复，不解析、不格式化、不脱敏，也不自动执行 `auth_ref`
替换。重复 JSON key、原始空白和暂时非法的 JSON 均可恢复。`--skip-resolve-auth` 仅为兼容旧命令保留，
当前不改变行为。

### 3. 校验铺出

- Skill 层按包内 `skills/` 和 `skills-local/` 的实际文件树校验普通文件 hash、目录 entry 和软链 target。
- 新 Pack 声明 `snapshot.digestAlgorithm: personal-skill-snapshot-v1` 时，个人 Skill 范围与联合摘要属于强契约；旧 Pack 以归档实际内容为输入，但其中公共 Skill 入口兼容跳过。
- Deploy 对内容保持宽松：不检查 Skill 语义、软链可达性、MCP JSON 合法性或历史布局字段。严格校验仅保留归档路径穿越、既有软链 parent/no-follow、根目录类型，以及新契约明确声明的摘要一致性。
- 不判断软链目标是否存在，不要求 `SKILL.md`，不推断 active/layout/broken 状态。
- `.nfs*` 是运行中进程持有已删除文件时产生的瞬态句柄，铺入和最终校验均忽略，不作为 Pack 差异或恢复失败依据。
- `clawevolve-*`、`clawbench-*`、`ocb-*` Release 管理 entry 不被清理、覆盖或校验为镜像内容。
- root `present: true`（包括空目录）必须恢复；`present: false` 时删除非受保护 entry，若仍有 Release entry 则保留必要父目录。
- Restore/Deploy 逐级 `lstat` 检查 parent，禁止通过既有软链写出 Workspace；`--force-overwrite` 才允许先删除冲突软链或普通文件。
- md 集合与镜像 `package/md/` 对齐。
- mcp 存在且原始字节与镜像一致（若镜像含 mcp layer）。
- clawevolve_results 若恢复，则应完整复制到绑定 run dir；文件树摘要在日志中打印。

任一校验失败 → 自动回滚 workspace 和本次恢复的 clawevolve_results。

## 用法

```bash
# 本地镜像部署到当前 bot
./scripts/deploy.sh --image /path/to/<id>__v1.0.0__<sha8>.zip

# 从 clawweb/OSS signed URL 拉取后部署
./scripts/deploy.sh --image-url "https://clawweb/api/agent-images/download?..."

# 指定目标 workspace
./scripts/deploy.sh --image /path/to/img.zip --workspace ~/.openclaw/workspace

# 恢复镜像中的 clawevolve_results 到指定 run
./scripts/deploy.sh \
  --image /path/to/img.zip \
  --evolve-run-id ev_20260723_151230_a3f91c

# 或直接传 run_dir，优先级最高
./scripts/deploy.sh \
  --image /path/to/img.zip \
  --evolve-results-dir /home/admin/.openclaw/workspace/clawevolve_results/ev_20260723_151230_a3f91c

# 不恢复 clawevolve_results，只铺 md/mcp/skill
./scripts/deploy.sh --image /path/to/img.zip --skip-evolve-results

# 干跑：不写真实 workspace，也不写真实 clawevolve_results
./scripts/deploy.sh --image /path/to/img.zip --dry-run

# 已知干净环境，跳过备份
./scripts/deploy.sh --image /path/to/img.zip --no-backup

# 目标已存在时强制覆盖，包括 clawevolve_results run dir
./scripts/deploy.sh --image /path/to/img.zip --force-overwrite
```

## 与 clawevolve-pack 的契约

deploy 信任 pack 产出的镜像满足下列约定（manifest `layers[].path` 字段）：

- `md/` → 顶层 md，含 `names` 列表。
- `mcp/mcporter.json` → MCP 配置；skipped 时该 bot 无 MCP。
- `skills/skills-local/`、`skills-local/` 与个人激活入口 → 个人 Skill 快照；公共只读 Skill 和 Release 管理 entry 不属于 Artifact。
- `clawevolve_results/` → 单个 evolve run 目录的内容，采用 `{diagnose,plan,optimize}/{input,output}` 三层结构，例如：

```text
clawevolve_results/optimize/input/objective.md
clawevolve_results/optimize/input/spec-v0.md
clawevolve_results/run_manifest.json
clawevolve_results/optimize/output/round-001/input/spec-v0.md
clawevolve_results/optimize/output/round-001/round_state.json
clawevolve_results/optimize/output/round-001/tune/tune_report.md
clawevolve_results/optimize/output/round-001/tune/diff.patch
clawevolve_results/optimize/output/round-001/spec/spec-v1.md
```

Spec 命名必须保持数字版本：round N（1-based）消费 `input/spec-v{N-1}.md`（如 round 1 消费 `spec-v0.md`），产出 `spec/spec-vN.md`（如 round 1 产出 `spec-v1.md`）。不要使用未版本化 spec 别名。

## 安全/边界

- 软链 target 逐字保存和恢复，允许相对、绝对、外部或断链；Pack/Deploy 不跟随其目标。
- `skills-repo`、`skills-center` 及指向公共仓库的 Skill 入口不进入镜像，也不参与清理、恢复、备份和校验。
- Release 管理的 `clawevolve-*`、`clawbench-*`、`ocb-*` entry 不进入镜像，Deploy/Restore 不清理也不覆盖。
- 镜像内 MCP 配置按原始字节恢复，Deploy 不解释或改写凭证字段。
- `clawevolve_results` 只恢复一个绑定 run，避免同 bot 多个 evolve run 串目录。
- 镜像内 `MEMORY.md`/`HEARTBEAT.md` pack 侧已排除，deploy 不涉及。

## 扩展点（留 patch 洞，本步不实现）

预留 `--with-*` 开关与 pack 对齐，当前命中即报待 patch：

| 开关 | 含义 |
|---|---|
| `--with-model` | 模型 id+参数还原 |
| `--with-memory` | memory/ + MEMORY.md 铺回 |
| `--with-plugins` | 插件/扩展 + 运行时旋钮还原 |
| `--with-activeset` | 活跃技能集元数据还原 |
| `--with-identity` | device identity 还原 |
| `--with-sessions` | 会话历史还原 |
