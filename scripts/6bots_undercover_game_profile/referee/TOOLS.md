# TOOLS.md

## 技能

- `skills/undercover-game-referee`：本局主持的全部内容。`scripts/undercover.py` 是事实层，`references/` 是阶段机、命令参考、协作形状和主持稿要求。**每次被唤醒都按它执行，不即兴发挥。**
- `skills/bcs-coordination`：BCS 命令行的用法。认证、`collaborate permission`、`collaborate run`、`session` 系列命令一律以它为准。

## 事实层脚本

```bash
SKILL_DIR="${OPENCLAW_WORKSPACE_DIR:-$PWD}/skills/undercover-game-referee"
uc() { python3 "$SKILL_DIR/scripts/undercover.py" "$@"; }
```

`status` / `begin` / `init` / `reveal` / `my-word` **必须带 `--session`**（从这次的
GroupContext 里抄）；中途那些命令可以省，`--group` 一律可以省。一个群能开很多局，
会话 ID 是唯一能分清"这一局是哪一局"的东西。

日常只用这几条：`begin` 开局探测、`init` 发牌、`status` 看阶段、**`open-round` 开一轮发言**、
**`open-vote` 开投**、`speeches-set` / `votes-set` 收产物并判定、`render-ping` 渲染遗言任务、
`reveal` 终局公布。`render-speak-run` / `render-vote-run` 是拆开的底层命令，只在 `open-*`
跑不通时手工用。详见 `references/commands.md`。

`open-round` / `open-vote` / `begin` 会自己调 `bcs-cli`（认证仍由 CLI 负责，脚本不碰 token）。
把几步合成一条不是为了省事：我的每一次工具调用都会被转发成群事件，来回越多人类看到的
无关内容越多；而且入口节点是我自己的，提交之后每多一个来回，本轮开场就晚一个来回。

第一次使用前确认 `python3 --version` 可用；不可用就直接告诉人类，不要退化成手工主持。

## 协同工具

- `bcs_assign_task(target_bot, message)`：派遗言或预备任务。目标用 Bot 名称。
  **派任务只有这一个办法。** 不要去查 `bcs --help` 找别的路子，也不要退回用 `bcs chat`
  ——那是一对一会话，会脱离本局，回执也不是任务回执。
  **这个工具会打断我自己这次激活**：每派一条任务，BCS 就往我自己的会话里回灌一条
  `[任务状态]`，回执到达时再回灌一条，回灌会把当时正在跑的激活打断。所以它必须是本次激活的
  最后一个工具调用，**而且派的那一刻身后不能有节点在排队等我让路**。全流程只有 S4b 一处用它。
- `bcs_task_complete(summary)`：**只在整局结束时调用一次**，它会结束当前 session。中途绝不调用。
  判据是"我自己刚跑完的 `votes-set` 返回了 `finished`"，**不是"`status` 说 FINISHED"**——
  这次激活里我一轮都没主持过就看到 FINISHED，那是会话 ID 传错、认到了上一局，
  这时候调它等于把一个刚建的新会话当场关掉。
- 具体调用面（`mcporter call`、原生 MCP 还是原生 tool）以 BCS 注入的本群协同上下文为准。

## 工具边界

- 不读取、不打印、不传递 token；不设置 `BCN_BOT_TOKEN`，不传 `--token`。
- 不覆盖已有的 `BOT_DATA_DIR`。
- 不用 `curl` 直接打 BCS HTTP 接口绕过 CLI。
- 不用 `cat` / `head` 打开渲染出来的 YAML 和状态文件。
- 不用 `bcs chat` 给 Bot 玩家发消息（那是一对一会话，会脱离本局）。
