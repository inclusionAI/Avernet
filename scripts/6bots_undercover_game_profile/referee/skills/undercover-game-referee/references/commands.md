# 脚本命令参考

`scripts/undercover.py` 是本局事实层的全部实现。所有命令都输出 JSON，第一个字段是 `ok`。

```bash
SKILL_DIR="${OPENCLAW_WORKSPACE_DIR:-$PWD}/skills/undercover-game-referee"
uc() { python3 "$SKILL_DIR/scripts/undercover.py" "$@"; }
```

失败时 `ok: false`，带 `error` 和 `message`，退出码 2。**看到失败就停下并向人类说明，不要绕过它继续。**

## `--session` 什么时候必须给

**一个协作群可以连着开好几个会话，每个会话是独立的一局**，而状态文件是按会话落盘的、
上一局的不会消失。所以会话 ID 是"这一局是哪一局"的唯一凭据，只能从把我叫醒的
GroupContext 里抄。

| 命令 | `--session` |
| --- | --- |
| `status` `begin` `init` `reveal` `my-word` | **必须给。** 它们是一局的入口和出口，也是新会话里第一条会被调用的命令 |
| 其余（`open-*`、`*-set`、`render-*`、`mask`、`parse-vote`） | 可以省，脚本只落到**本机唯一一局还没结束**的游戏上；有两局同时在跑就报 `AMBIGUOUS_SESSION` |

`--group` 一律可以省：取会话 ID 冒号前面那一段（`bcs_grp_xxxx:yyyy` → `bcs_grp_xxxx`）。
只有 `begin` 和 `init` 有这个参数。

参数写错时返回的是脚本自己的 JSON（`error: BAD_ARGS`），里面带 `usage`。
**不要为了查参数去跑 `--help`**，照这份文档抄就行。

### 这两档为什么不一样

同一个来源的两次事故，方向刚好相反：

- 模型会在读完文档之前就抢跑一条命令——实测开局连着两次，一次漏了 `--session`、一次漏了
  `--group`，两次 argparse 的 usage 转储都被转发进了群里。所以要能省的尽量省、报错要能直接
  照着改。
- 但"省掉会话 ID"在同一个群开第二局时是致命的：新会话还没有自己的状态文件，磁盘上"唯一
  那一局"恰恰是上一局。实测主持人因此在新会话里读到上一局的 `FINISHED`、`reveal` 出上一局
  的词和身份、还调 `bcs_task_complete` 把刚建的会话关掉了。所以入口和出口那几条不许猜。

**命令的输出也是公开的。** 我的每一次工具调用和它的输出都会被转发成群里的事件，
所以默认输出都压成了一行、只留必要字段。要详细的再加 `--full`。同理，能一条命令
做完的就别拆成三条——每多一个来回，人类就多看到一堆跟游戏无关的东西。

## 三条复合命令（日常只用这三条）

| 命令 | 顶掉了什么 | 用在哪 |
| --- | --- | --- |
| `begin` | 三条 `bcs session get \| jq` + 找自己 UUID | S0 |
| `open-round` | `collaborate permission` + `render-speak-run` + `collaborate run` | S1 / S5 / SX |
| `open-vote` | `collaborate permission` + `render-vote-run` + `collaborate run` | S3 / SX |

它们会自己调 `bcs-cli`（认证仍由 CLI 负责，脚本不碰 token）。下面的 `render-*` 是拆开的
底层命令，只在复合命令因为环境问题跑不通时手工用。

## 脱敏约定

| 命令 | 输出里有词吗 |
| --- | --- |
| `status` `speeches-set` `votes-set` `parse-vote` `render-ping` `mask` | **没有**，可以放心引用 |
| `init`（`human_word` 字段）`my-word` | 只有**人类玩家自己那个词**，只能说给他一个人听 |
| `render-speak-run` `render-vote-run` | 只有文件路径，词在文件里，我不读也不贴 |
| `reveal` | 有，且只有终局才能调 |

## 命令

### `begin`

```bash
uc begin --session S [--group G] [--difficulty medium] [--undercover 1] [--max-rounds 6]
```

开局前的全部探测一次做完：人类在不在会话里、是不是 Present、有哪些 Bot 玩家、我自己的
`bot_uuid` 是什么。返回 `human_present`、`human_actor_id`、`bots[]`、`referee_uuid`，
以及一条可以原样跑的 `init_command`。

- `human_present: false` → 说"加入提示"那段，结束激活。**人类参与者的 mode 缺省是
  `absent` 不是 `present`**，字段缺失按 absent 处理，这条判断在脚本里。
- `human_present: true` → 说开场白，等人类回一句"开始"。**这一步不 init。**

**不要再去读 `BCN_BOT_UUID`。** 这套部署里它是空的，而 `bot_uuid` 实际上就等于 Bot 名称。
脚本按 `BCN_BOT_UUID` → `$BOT_DATA_DIR/.bcs/session.json` 的顺序自己解析。

### `init`

```bash
uc init --session S --human human_123 \
  --bot "玩家稳健老陈=uuid1" --bot "玩家话痨小满=uuid2" \
  [--group G] [--referee-uuid X] [--difficulty easy|medium|hard] [--undercover 1] [--max-rounds 6] [--force]
```

抽词对、随机座位、随机身份，一次做完。返回 `seating`（座位号 + 名字 + 是不是人类）、`human_seat`、
`human_word`、`referee_uuid`。同一局重复调会被拒，除非 `--force`。

**`human_word` 是人类玩家自己那个词**，也是全场唯一一个我可以说出口的词：群聊是我和他
的私密双人频道，Bot 收不到。发牌时念给他一次（S1 第 2 步），别的座位的词脚本不会给我。

`--referee-uuid` 可以不给，脚本自己解析；只有解析不出来时才需要显式传。

### `status`

默认返回压成一行的四件事：`phase`、`round`（"1/6"）、`alive`、`pending_ping`、`next_action`。
**每次被唤醒的第一件事。**

还没开局时它不报错，返回 `phase: NO_GAME` 和一条现成的 `begin` 命令——所以"被唤醒先
`status`"这条纪律在 session 刚启动时也成立，不用为它开特例。

**新会话看到 `FINISHED` 一定是会话 ID 传错了**（新会话没有自己的状态文件，只可能是
`NO_GAME`）。这种情况下返回里会多一句 `note` 提醒，看到就停下来核对 `--session`，
不要念稿、不要 `reveal`、不要结束会话。

`--full` 额外给 `eliminated`、`human_seat`、`renders`、`result`。`renders` 是本轮各运行渲染
过几次，例如 `{"speak": 1, "vote": 2}` 表示投票重开过一次——只在卡住诊断时需要。

### `open-round`

```bash
uc open-round --session S [--retry]
```

开一轮发言：查协作槽位 → 渲染 → 提交，一条命令做完。返回 `round`、`attempt`、
`submitted`、`run_id`。

合成这一条是为了把「提交」和「激活结束」之间的距离压到零：入口节点是我自己的，
提交之后我每多花一个来回，本轮开场就晚一个来回。**跑完它不要再说话，立刻结束激活**
——回合开场是入口节点的产物，我马上会被它叫醒。

槽位被占时返回 `RUN_SLOT_BUSY`，**这时状态没有任何改动**，可以安全重试。

例外是 phase 还停在 `AWAIT_NEXT_ROUND` 的那次：那说明投票运行没收尾，绝大多数时候是因为
我把 S4 和 S5 挤进了同一次激活——我此刻就是那个运行的末节点。这时返回的是 **`IN_TALLY_NODE`**，
**立刻结束激活，一次都不要重试**。

### `open-vote`

```bash
uc open-vote --session S [--retry]
```

同上。

槽位被占时脚本会自己退避重试三次（自动开投那一刻发言运行可能刚收尾），不用我在外面
"等几秒再试一次"——每试一次都是一个来回。三次仍被占才报错，而错误码分两种：

- `RUN_SLOT_BUSY`（`--retry` 那条路上）：上一个运行还活着，什么都没改，回一句还在等谁。
- **`IN_COLLECT_NODE`**（phase 还是 `AWAIT_VOTE_START`）：发言运行没收尾，绝大多数时候
  是因为我把 S2 和 S3 挤进了同一次激活——我此刻就是那个运行的末节点，它等我结束、我等
  槽位。**立刻结束激活，一次都不要重试**，照返回的 `message` 办。

**提交完立刻结束激活，一个工具调用都不要再加。** 开投稿同样是入口节点的产物，不用我说。
这里以前还要 `bcs_assign_task` 派一个看门狗，那条已经删了——`vote_open` 入口节点此刻正排在
我后面，而派单和它的回执都会往我自己的会话里回灌一条 `[任务状态]`，回灌会打断正在跑的节点。
见 [runs.md](runs.md#派任务会打断我自己)。

### `render-speak-run`

```bash
uc render-speak-run --session S [--retry]
```

只能在 `AWAIT_START` 或 `AWAIT_NEXT_ROUND` 调。**渲染即推进**：它自己把轮次加一、建好本轮记录、把 phase 推到 `SPEAK_RUNNING`。

返回 `yaml_path`、`input_path`、`binding_args`、`attempt`，以及一条拼好的 `run_command`。
**只在 `open-round` 跑不通、需要手工提交时才用它。**

`--retry` 用来重开当前这一轮：只能在 `SPEAK_RUNNING` 且本轮发言还没收齐时调，**轮次不推进、本轮记录不重建**，只是把同一份 YAML 重渲染一次。上一次提交的运行失败了才用它——运行失败不会唤醒我，所以这条路只会从 SX 卡住诊断走进来。

### `speeches-set`

```bash
uc speeches-set --session S --json '{"1":"...","2":"..."}' [--flag 3=谈论了身份]
```

只能在 `SPEAK_RUNNING` 调。座位号必须和本轮存活名单完全一致。

对每条发言做泄词判定并遮蔽，返回 `speeches[]`：`seat` / `player` / `label` / `kind` / `text`（**遮蔽后的可展示文本**）/ `violation`。phase 推到 `AWAIT_VOTE_START`。

**phase 变成 `AWAIT_VOTE_START` 不等于现在就能开投。** 这条命令只可能在「本轮发言汇总」
节点里跑，而那个节点是发言运行的末节点：念完稿子结束激活，开投是下一次（回灌）激活的事。
所以它的 `next_action` 是「结束激活」，不是 `NEXT_ACTION["AWAIT_VOTE_START"]` 那句通用文案。

**`label` 是念稿用的称呼**（`和事佬阿和（1号）`），每个人第一次出现都要用它。人类在副屏里
投的是号码、在群里听到的是名字，中间那次映射不该由他来做。

泄词判定三种，和告诉玩家的规则一字不差：说出完整词、说出词里连续两个字、把词拆散了说全。单个常用字命中不算违规。

`--flag` 用来补脚本判不了的语义违规（谈身份、点评他人）。

### `render-vote-run`

```bash
uc render-vote-run --session S [--retry]
```

只能在 `AWAIT_VOTE_START` 调，同样渲染即推进，phase 到 `VOTE_RUNNING`。返回字段同
`render-speak-run`。**只在 `open-vote` 跑不通、需要手工提交时才用它。**

`--retry` 用来重开当前这一轮的投票，只能在 `VOTE_RUNNING` 调。**没有它，卡住诊断走不通**——不带 `--retry` 时阶段卫兵只认 `AWAIT_VOTE_START`，而这时 phase 早就是 `VOTE_RUNNING` 了。重开后之前投过的票作废，要向人类说明。

### `votes-set`

```bash
uc votes-set --session S --json '{"1":"我投3号","2":"我弃权"}'
```

只能在 `VOTE_RUNNING` 调。一次做完解析、计票、平票规则、出局、胜负判定。返回：

- `votes[]`：`seat` / `player` / `label` / `text`（**规范化后的票面**）/ `target_seat` / `target_player` / `target_label` / `violation` / `note`
- `counts[]`：按票数降序的 `seat` / `player` / `label` / `votes`
- `tie`、`eliminated`（带 `label`）、`alive[]`（带 `label`）
- `verdict`：`continue` 或 `finished`；`winner`、`win_reason`
- `ping`：继续时给出该派谁、派什么类型
- phase 推到 `AWAIT_NEXT_ROUND` 或 `FINISHED`

`text` 一律是规范化的票面——`我投N号`、`我弃权` 或 `无效票`，**永远不是玩家的原话**。
玩家这一轮只被要求交票号，但指令是软的：万一有谁多写了半句理由，这里也会被抹掉，
所以主持稿里不可能出现投票理由。我拿不到原话，也不许去猜他们为什么这么投。

投票内容如果泄词，该票直接作废（`target_seat` 为 null，`text` 是 `无效票`）。
超字不作废，只在 `violation` 里记一笔。

**`tie` 为真就是平票：本轮没有人出局，直接进下一轮。** 没有重投，也不会在平票者里
随机挑人——`eliminated` 一定是 null。这是有意的：平票不减员但照样烧掉一轮，而轮数
用完判卧底赢，所以平票是纯粹的平民损失。要调平衡就调 `--max-rounds`。

### `render-ping`

只能在有 `pending_ping` 时调。返回 `kind`（`eulogy` 遗言 / `standby` 预备）、`seat`、`target_bot`、`label`、`message`。
用 `bcs_assign_task` 把 `message` 原样发给 `target_bot`。**这条回执是下一轮的唤醒源。**

遗言那条任务里已经带好了全场公开发言当素材，并要求出局者**点一个怀疑的座位号 + 说清是
他哪句话让自己在意**，同时禁止提自己的词、禁止说自己是不是卧底、禁止拿自己的词去和别人
比。这不是装饰：遗言是整局唯一一条只流向人类玩家的线索通道（Bot 收不到群广播，也看不到
别人的任务回执），以前那条"可以喊冤也可以放狠话"的 20 字任务换回来的是一句纯情绪。

回执拿到之后先过 `mask` 再念，见下条。

**不要在开票节点里做这件事。** 开票节点只负责念稿；派唤醒源在开票稿发出去之后的那次
激活里做（S4b）。判据是 `status` 里 `phase = AWAIT_NEXT_ROUND` 且 `pending_ping` 非空。

派任务只有 `bcs_assign_task` 这一个办法。**不要去查 `bcs --help`，不要用 `bcs chat`**
——那是一对一会话，会脱离本局，而且它的回执不是任务回执。

### `my-word`

```bash
uc my-word --session S
```

人类玩家忘了自己的词时用。返回 `human_seat` 和 `human_word`，只有他自己那一个词，
随时可以调、次数不限。**只说给他一个人听**——虽然群聊本来就只有他看得到。

### `mask`

```bash
uc mask --session S --seat N --text "遗言原话" [--max-chars 35]
```

把一句自由文本过一道和发言、投票一模一样的泄词判定：返回遮蔽后的 `text` 和 `violation`。

发言和投票都走协作节点，我在人类看到之前就能遮蔽；**遗言不走节点，是公开的任务回执**，
这条路上以前没有任何机器兜底。念遗言之前先跑这一条，只念返回的 `text`。

### `reveal`

只能在 `FINISHED` 调，**而且一局只能调一次**：第二次返回 `ALREADY_REVEALED`。
返回 `winner`、`win_reason`、`words`、每个座位的身份与词、每轮的发言与投票流向。

公布答案是不可撤销的，所以这里额外上了一道闸：真相已经公布过还想再公布，多半是认错了
局（在新会话里读到了上一局的状态）。看到 `ALREADY_REVEALED` 就核对 `--session`，
终局稿已经发过就别再发一遍。

### `parse-vote`

```bash
uc parse-vote --session S --voter 1 --text "我投4号"
```

单独解析一句投票，调试和兜底用。正常流程里不需要——`votes-set` 内部用的就是同一套解析。

## 状态文件

路径是 `$BOT_DATA_DIR/undercover-game/<session_id>.json`，一个 session 一局。
渲染出来的 YAML 和输入放在 `$BOT_DATA_DIR/undercover-game/work/<session_id>/`。

**永远不要手工编辑状态文件，也不要用 `cat` 把它贴出来** —— 里面有全部答案。需要什么信息就调对应命令。
