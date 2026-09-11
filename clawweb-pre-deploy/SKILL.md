---
name: clawweb-pre-deploy
description: 通过用户已登录的浏览器完成 ClawWeb GitHub PR 合并后到 AntCode 镜像 dev、LinkEX 预发流水线的流程，确认目标 Commit 后执行 clawweb-pre-deploy（dev），等待全部阶段成功，并在预发完成后使用 strong-reminder 强提醒用户。用户提到“发布 Clawweb 到预发”“执行预发流水线”“等流水线完成后提醒我”时使用。
---

# ClawWeb GitHub 到预发部署

本技能只操作预发，不操作生产发布。新流程为：**GitHub PR 合并并通过检查 → AntCode 镜像自动同步 dev → LinkEX 项目流水线执行 → 等全部流水线通过 → 强提醒**。

## 固定入口

- AntCode 镜像仓库：`https://code.alipay.com/mirrors/Avernet`
- LinkEX 项目流水线：`https://linkex.alipay.com/project/201400026/pipeline?tenant_path=alipay`
- 流水线模板：`clawweb-pre-deploy`
- 执行分支：`dev`
- 强提醒技能：`/Users/zhaosenlin/workspace/jiangshen_skills/strong-reminder`

## 核心变化（必须遵守）

- 不再进入旧的 ClawWeb CI/持续交付页面寻找构建交付物。
- 不再手动点击镜像仓库右上角的“刷新同步”。镜像仓库每日自动同步，只读取页面上的 dev HEAD/Commit 做核对。
- 不自行切换到`master`构建；新的预发流程使用 LinkEX 项目流水线的`clawweb-pre-deploy`模板和`dev`分支。
- 不使用“最新一条”“第一行”替代业务匹配，必须按 GitHub PR、目标 Commit、流水线模板和分支精确核对。
- 预发流水线尚未全部成功时，不发送成功提醒；失败、取消或版本不一致时停止并准确报告。

## Dry run

用户要求 dry run 时，只做只读验证，不产生外部写操作：

1. 打开 GitHub PR，确认目标分支为`dev`，读取合并状态和 required checks。
2. 打开 AntCode 镜像仓库，读取`dev`当前 HEAD Commit；禁止点击“刷新同步”。
3. 打开 LinkEX 项目流水线页面，确认项目、模板`clawweb-pre-deploy`和分支`dev`字段可用；不要点击执行。
4. 只有 PR 已合并、GitHub 检查成功、镜像 dev 已包含目标合并 Commit、模板和分支准确时才输出“可执行”。否则输出等待原因并停止。

## 正式执行流程

### 1. 锁定 GitHub PR

- 用户提供 PR 编号时只跟踪该 PR；否则只跟踪用户明确指定的目标 PR。
- 记录 PR 编号、标题、源分支、最新 Commit、目标分支和锁定时间。
- 必须等待 PR 状态为已合并，且 required checks 全部成功。仅“可合并”、审核通过或部分检查成功不能进入下一步。

### 2. 核对 AntCode 镜像 dev

- 打开镜像仓库读取分支`dev`的 HEAD Commit。
- 不点击“刷新同步”，不使用旧人工同步步骤。
- 镜像 dev 应为 GitHub 合并 Commit 或明确包含该 Commit 的后继 Commit。若页面只显示短 SHA，进入提交详情核验，不能猜测。
- 镜像尚未同步时每 30 秒检查一次；单阶段超过 60 分钟报告等待超时并保留页面状态。

### 3. 执行 LinkEX 预发流水线

- 进入项目流水线 URL，点击“执行流水线”。
- 弹窗/表单必须核对：工程为`ocb`（若页面默认显示该值）、流水线模板为`clawweb-pre-deploy`、分支为`dev`。
- 不点击“保存为当前工程常用的快捷执行方式”，除非用户另行明确要求。
- 只执行一次，记录新流水线编号、触发时间、分支和页面显示的 Commit/版本字段。

### 4. 等待并核验

- 只轮询本次触发的流水线。
- 排队、运行中、等待人工属于中间状态，不退出、不重复执行。
- 只有所有必需阶段成功，且流水线版本/Commit 与已核验的镜像 dev 一致时，才判定预发成功。
- 任一阶段失败、取消、超时或 Commit 不一致时停止，报告流水线编号、失败阶段和页面错误；禁止自动重试写操作。

## 完成后的强提醒

预发流水线全部成功且 Commit 一致后，调用：

`zsh /Users/zhaosenlin/workspace/jiangshen_skills/strong-reminder/scripts/strong_remind.sh --title "ClawWeb 预发部署完成" --subtitle "PR #<PR号> · 流水线 #<流水线号>" --message "dev Commit <短Commit> 已通过全部预发阶段。"`

只在最终活动流水线成功后提醒一次，并记录通知、声音、置前弹窗三层结果。失败或需要用户处理时也提醒，但标题必须明确写“失败”或“需要处理”。

## 结果输出

成功时报告 PR 编号、GitHub 合并 Commit、镜像 dev Commit、LinkEX 流水线编号、全部阶段状态、强提醒三层结果。等待或失败时报告当前锁定的 PR/Commit/流水线、具体阶段和下一步，不声称已部署成功。
