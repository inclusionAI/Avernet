# 将 PydanticAI 玩家接入本地“谁是卧底”牌桌

本指南用于把一名 PydanticAI 玩家加入已有的 Avernet 本地牌桌。
组局后共有一名主持人、四名 OpenClaw 玩家、一名 PydanticAI 玩家和一个人类输入席位；
主持人不参赛。游戏使用原有发牌、轮次、计票和胜负规则。

## 开始前

准备好以下条件：

- 本地 BCS、legacy 前端和原有 OpenClaw 游戏 Bot 已能正常运行。
- 有 `uv`，并能安装 Python 3.12 及项目依赖。
- 有可用的 DeepSeek API Key。

如果还没有本地牌桌，先完成[对局教程](../../../docs/undercover-game-tutorial.zh-CN.md)。
游戏 profile 位于 `singlebox/agents/6bots_undercover_game_profile`。

在仓库根目录检查 BCS：

```bash
curl --noproxy '*' --fail http://127.0.0.1:21000/health
```

健康检查成功后继续。使用其他 BCS 端口时，后续配置同步改成对应端口。

## 1. 安装玩家

在 Avernet 仓库根目录执行：

```bash
cd singlebox/agents/pydanticai-undercover-player
uv sync --frozen --python 3.12
test -f .env || cp .env.example .env
```

如果已有 `.env`，保留已有文件，直接检查下一节的配置。

## 2. 配置模型与 BCS 地址

用编辑器打开 `.env`，填写 API Key 并核对以下项目：

| 环境变量 | 填写内容 |
| --- | --- |
| `PLAYER_MODEL_BASE_URL` | `https://api.deepseek.com` |
| `PLAYER_MODEL` | `deepseek-flash` |
| `DEEPSEEK_API_KEY` | 你的真实 API Key，只保存在本机 |
| `PLAYER_DISABLE_THINKING` | `true`，用于兼容结构化输出工具选择 |
| `PLAYER_BCS_WS_URL` | `ws://127.0.0.1:21000/ws/bot` |
| `PLAYER_BCS_API_URL` | `http://127.0.0.1:21000`，本地默认地址不加 `/api` |
| `PLAYER_NAME` | 页面中显示的玩家名称，例如 `PydanticAI玩家` |
| `PLAYER_STATE_FILE` | `.state/identity.json`，保存本 Bot 的连接身份 |

WebSocket 地址和 HTTP 地址必须指向同一套 BCS。API Key 不填写在 URL 中。
`.env` 和 `.state/` 已由本目录的 `.gitignore` 排除。

如果终端设置了全局 HTTP 代理，让本地 BCS 直连：

```bash
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"
```

模型服务仍使用终端原有的代理配置。

可先检查模型是否能正常产生结构化结果：

```bash
uv run --frozen --env-file .env pydanticai-player --check-model
```

这条命令会访问真实模型，依次检查合成发言、普通投票和 PK 投票。
出现 `Live model smoke passed (3 cases)` 表示三个模型检查通过。
检查结果写入本地忽略的 `artifacts/model-smoke.json`；实际组局步骤见第 4 节。

## 3. 启动并确认注册

保持当前目录，运行：

```bash
uv run --frozen --env-file .env pydanticai-player
```

看到下面的信息后保持终端运行：

```text
INFO PydanticAI player connected; engine ready
```

玩家会自动完成 V3 握手和 onboard。在 Avernet 页面刷新 Bot 列表，应能找到
`PLAYER_NAME` 配置的名称。也可以在另一个终端的仓库根目录执行：

```bash
./apps/bcs/target/debug/bcs-cli --url http://127.0.0.1:21000 list
```

同一身份只运行一个玩家进程。重新连接时沿用已有身份，不需要重新创建 Bot。
如果改接另一套 BCS，给 `PLAYER_STATE_FILE` 配置一个不同的文件路径。

## 4. 创建混合牌桌

在页面切换到人类视角，创建新的任务协作型群（manager-worker）：

| 设置 | 选择 |
| --- | --- |
| Manager | 原有“谁是卧底主持人” |
| Workers | 四名原有 OpenClaw 玩家，加上刚注册的 PydanticAI 玩家 |
| 人类玩家 | 你，需在当前 session 点击“加入当前会话” |

如果五个原有 OpenClaw 玩家都已启动，只选择其中四个加入本群即可。
从实际注册的 Bot 中选中 PydanticAI 玩家，不需要重命名任何 OpenClaw 玩家。

加入当前会话后，等待主持人开场，再发送 `开始`。随后：

1. 轮到人类席位时，在游戏副屏提交一句发言。
2. 投票阶段在副屏选择合法候选并确认。
3. PydanticAI 玩家的发言和票面应随原流程出现，主持人收齐后计票、推进下一轮或宣布终局。

默认规则是六名玩家、一个卧底、最多六轮。新开下一局时创建新的 session，
重新加入当前会话，再发送 `开始`；不需要重启玩家进程。

## 5. 对局期间与停止

同一进程内断线重连会保留按 session 隔离的本人座位和词，并取消旧连接中的执行任务。
旧连接的迟到结果不会补发到新连接。若服务端返回 `already_connected`，玩家会沿用原 token 退避重试。

进程退出后，本人词和座位的内存缓存会丢失。对局期间保持玩家进程运行，
尤其不要在出局遗言之前退出。结束使用时，在玩家终端按 `Ctrl+C`。

对局记录示例包含 session、公开发言、票面、计票、违规统计和异常记录：
[第 1 局](validation/game-01.zh-CN.md)、[第 2 局](validation/game-02.zh-CN.md)、[第 3 局](validation/game-03.zh-CN.md)。
分享自己的对局记录时，保留公开过程与统计，隐去认证信息、私有词牌和完整私有任务提示。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 启动时报 `ValueError` | 核对 `.env` 中模型地址、模型名、API Key 都已填写；使用带 `--env-file .env` 的命令 |
| 健康检查成功，但玩家 HTTP 注册失败 | 检查 API 地址和 WebSocket 是否同端口、API 地址是否误加 `/api`，以及 `NO_PROXY` 是否包含本地地址 |
| 模型拒绝输出工具选择或合成检查失败 | DeepSeek 使用 `PLAYER_DISABLE_THINKING=true`，确认 API Key 可用且模型名正确 |
| 页面找不到玩家 | 等待 `engine ready`，刷新 Bot 列表，检查筛选条件和 `PLAYER_NAME` |
| 主持人提示人类未加入 | 在当前 session 点击“加入当前会话”，仅打开群页面不算加入 |
| 重连期间提示已有连接 | 等原连接清理；保持单个玩家进程，不删除身份文件来替换 Bot |
| 进程重启后无法留遗言 | 缓存仅在同一进程内保留；新建游戏 session，后续保持玩家进程运行 |
| 主持人无法派发遗言 | 检查现有 BCN 插件和主持人运行环境是否提供 `bcs_assign_task`，以及主持人是否为 manager |
| OpenClaw 网关提示 `config.loadConfig` 不存在 | 检查网关与 BCN 插件版本；三局对局使用的组合是 OpenClaw `2026.5.12` 和仓库 BCN 插件 `1.0.26` 源码 |

## 协议负责什么，玩家引擎负责什么

| 行为 | 协议/原主持人 | PydanticAI 玩家 |
| --- | --- | --- |
| 身份与连接 | 认证 token、协议协商、路由与在线状态 | 握手、能力检查、身份保存、心跳与重连 |
| 可见信息 | 下发当前节点 Input、直接父节点产物和本人 instruction | 按完整 session ID 隔离，不借群广播或其他玩家私有信息补上下文 |
| 发牌与游戏推进 | 座位、词牌、存活名单、阶段、计票和胜负 | 完成当前节点，不自行推进阶段或修改游戏状态 |
| 发言与投票格式 | 提供规则并判定票面和违规 | 类型化输出、字数/本人词/合法候选检查，有限重试后只发布最终结果 |
| 取消与迟到 | 下发带精确 run/session 的取消请求 | 确认本地取消后响应，丢弃该 run 的迟到结果 |

玩家收到 `chat.send` 后先 ACK，再回传一个 V3 `chat` 终态事件，包含 `runId`、
`sessionId`、递增 `seq`、毫秒时间戳 `ts` 及最终 content。不发送模型中间推理。
`chat.inject` 只 ACK；`chat.history` 返回空消息列表。
模型执行失败或校验重试耗尽时，回传 `error` 终态。

## 移植后的玩家规则

- 人格自定义为稳健、简洁；当前节点指令优先于人格。
- `speak_N` 使用公开 Input 和当前可见前序产物，按本轮钝度、角度要求发言；默认最多 25 字。
- `vote_N` 只根据公开历史选择存活、非本人的合法候选。PK 仅投给指定候选，禁止弃权。
- 对外票面严格为 `我投N号`，允许弃权时为 `我弃权`；不附理由或解释。
- `vote_start` 返回固定预备句，不执行实际投票。遗言遵守任务给出的长度、素材与保密要求。
- 不泄露本人词、连续两字或拆字，不主动查询其他玩家状态，不宣称知道自己的身份。

PydanticAI `Agent` 使用结构化输出与校验重试检查字数、本人词的字符泄露和合法票号。
描述钝度、拼音及谐音等语义要求由模型按当前节点指令执行；对局违规统计采用原主持人的判定。

需要了解底层协议时，可阅读 [BCN 接入指南](../../../docs/bot-integration.zh-CN.md)；
游戏规则对应原有[玩家 skill](../6bots_undercover_game_profile/player-laochen/skills/undercover-game-player/SKILL.md)。
