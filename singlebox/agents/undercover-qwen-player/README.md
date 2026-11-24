# Qwen-Agent 谁是卧底玩家

独立的 Python 玩家，使用 Qwen-Agent 和百炼模型生成「稳健老陈」的发言与投票。通过 BCN V3 接入现有牌桌。同局成员为主持人、四名 OpenClaw 玩家、一名 Qwen-Agent 玩家和一名真人玩家。

## 启动与接入

前置条件是原版牌桌已能按[对局教程](../../../docs/undercover-game-tutorial.zh-CN.md)运行，已安装 uv，运行玩家的终端中已设置 `DASHSCOPE_API_KEY`。Python 依赖版本和下载哈希由本目录的 `uv.lock` 固定。

在仓库根目录执行：

```bash
export QWEN_PLAYER_DATA="$HOME/.avernet/undercover-qwen-player"
uv run --project singlebox/agents/undercover-qwen-player --locked \
  python singlebox/agents/undercover-qwen-player/run_player.py \
  --bcn-url ws://127.0.0.1:21000/ws/bot \
  --identity-file "$QWEN_PLAYER_DATA/.bcs/session.json"
```

首次连接会保存独立的 Bot 身份。另开终端执行以下命令（`bcs-cli` 使用自己的玩家身份文件进行认证）。

```bash
export QWEN_PLAYER_DATA="$HOME/.avernet/undercover-qwen-player"
BOT_DATA_DIR="$QWEN_PLAYER_DATA" apps/bcs/target/debug/bcs-cli \
  --url http://127.0.0.1:21000 onboard \
  --name '玩家稳健老陈（Qwen-Agent）' \
  --summary '谁是卧底玩家，谨慎发言，按证据投票' \
  --scopes local,undercover-game \
  --domains undercover-game,social-deduction \
  --skills one-line-word-description,conservative-voting
```

如果建群时报主持人与新玩家尚未建立好友关系，先使用各自的身份目录完成一次好友确认：

```bash
BOT_DATA_DIR="$QWEN_PLAYER_DATA" apps/bcs/target/debug/bcs-cli \
  --url http://127.0.0.1:21000 friend request --bot-uuid '<主持人 Bot ID>'
BOT_DATA_DIR='<主持人 Bot 数据目录>' apps/bcs/target/debug/bcs-cli \
  --url http://127.0.0.1:21000 friend accept --request-id '<上一条返回的 Request ID>'
```

在工作台新建任务协作型群，选原主持人为主节点。选择四名其他 OpenClaw 玩家和「玩家稳健老陈（Qwen-Agent）」，跳过原 OpenClaw 老陈。按原教程开一个新会话和牌局。已经开局的参赛名单固定。若要切回原版，在下一局的新群中选择原 OpenClaw 老陈。

默认模型为 `qwen-flash`，可用 `--model` 选择同一百炼账户可调用的模型。BCN 身份文件和百炼凭据均放在仓库外。重启时使用同一个身份文件，单个身份只启动一个进程。

## 协议与玩家规则

| 责任 | 实现 |
| --- | --- |
| BCN V3 | 协商 canonical session 和 unified run events，派发当前节点输入，接收 ACK 和终态产物。 |
| 玩家传输 | 立即 ACK，维持心跳与重连。按会话和 run 精确取消，屏蔽取消后的模型结果，拒绝已过截止时间的任务。连接范围内按会话和幂等键去重。 |
| 玩家决策 | 只使用本次任务数据。发言保留轮次和前序产物，投票只读公开历史与合法候选。只有普通投票可以按规则弃权。 |
| 输出校验 | 提交前校验投票候选、单行、长度，并筛除原词及连续字面片段。拆字、拼音、英文、谐音和描述尺度仍需要结合三局的公开记录审查。 |

Qwen-Agent 同步模型调用在有界线程池执行。取消时先在本地关闭该 run 的发出路径。底层已发出的模型请求可能仍在结束，产生的结果会被丢弃。玩家按节点独立调用，`chat.history` 返回空记录；私词和模型输出保留在处理当前任务所需的内存中。

## 验证

在本目录运行：

```bash
uv run --locked python -X utf8 -m unittest discover -s tests -v
```

离线测试覆盖 BCN 握手、ACK 与终态关联、精确取消、迟到结果、去重、普通弃权、PK 候选和发言限制。混合牌桌验收以主持人的真实对局结果为准。

[实测记录](VALIDATION.md)包含真实 BCN 节点和混合牌桌的验证状态。
