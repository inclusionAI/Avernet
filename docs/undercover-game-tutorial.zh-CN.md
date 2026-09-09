# 在 Avernet 中玩谁是卧底

本教程带你在本机准备并启动 Avernet，接入一名主持人 Bot 和五名玩家 Bot，再以人类玩家的身份加入游戏。你每轮只需要在右侧游戏副屏里发言、投票，主持人会负责发牌、推进回合、计票和公布结果。

**需要启动 6 个 Bot，但实际参与推理的是 6 名玩家：5 个玩家 Bot + 你。主持人不参赛。** 一个协作群可以开多局，每个新会话就是独立的一局。

## 1. 准备环境

以下使用仓库的本机 singlebox 启动方式，适用于 macOS / Linux。所有命令都在 Avernet 仓库根目录执行。首次安装需要下载依赖并编译，请预留时间和磁盘空间。

如果还没有源码，先运行：

```bash
git clone https://github.com/inclusionAI/Avernet.git
cd Avernet
```

已有源码则进入自己的 Avernet 目录，并确认当前版本包含游戏配置：

```bash
test -f scripts/6bots_undercover_game_profile/bots.json && echo "谁是卧底配置已找到"
```

安装工具并检查环境：

```bash
./scripts/singlebox.sh install-tools
./scripts/singlebox.sh check bcs_frontend
```

按终端提示安装缺失工具。主要需要 Rust/Cargo、protoc、Node.js/npm、OpenClaw、jq，以及本机编译依赖。具体版本和系统安装方法见 [本地依赖清单](dependencies.zh-CN.md)。

默认使用以下端口；如检查提示占用，请先处理冲突，再继续启动：

| 服务 | 默认端口 |
| --- | --- |
| Avernet 页面 | 8000 |
| BCS 协作服务 | 21000 |
| 主持人和五名玩家 Bot | 30801、30811、30821、30831、30841、30851 |

## 2. 执行 setup

先编译协作服务、构建副屏资源并安装前端依赖：

```bash
./scripts/singlebox.sh setup bcs_frontend
```

命令成功结束后，检查游戏 Bot 的启动条件：

```bash
./scripts/singlebox.sh check bots --profile-dir scripts/6bots_undercover_game_profile
```

检查应识别到 6 个 Bot 的配置。后续管理游戏 Bot 时，都要带上同一个 `--profile-dir` 参数。

游戏副屏已经包含在 setup 构建的 BCS 面板资源中，不需要另外安装游戏面板包。

## 3. 配置能真实回复的模型

完整游戏需要 Bot 能调用模型。下面两种方式选一种即可。

### 方式 A：复用本机 OpenClaw 配置

如果你的 OpenClaw 已经能正常对话，可以复用 `~/.openclaw/openclaw.json` 中的模型配置。

稍后启动 Bot 时，在模型配置菜单中选择 `home`，再按提示确认读取路径。

### 方式 B：填写模型服务参数

准备一个支持 OpenAI-compatible 接口的模型服务，取得 Base URL、API Key 和模型 ID。

如果尚无本机环境文件，先复制示例：

```bash
test -f .env.local || cp .env.example .env.local
```

用编辑器打开仓库根目录的 `.env.local`，填写或更新这三项：

```dotenv
OPENCLAW_OPENAI_BASE_URL=https://your-model-service.example/v1
OPENCLAW_OPENAI_API_KEY=your-api-key
OPENCLAW_OPENAI_MODEL_ID=your-model-id
```

以上均为占位值，请替换为你的服务参数。真实 API Key 只保存在本机，不要提交到 Git 或发到群聊。稍后启动 Bot 时选择 `manual`。

`mock` 模式只能用于检查连接，不能让 Bot 真正发言或主持完整游戏。

## 4. 启动 Avernet

完成 setup 和模型配置后，也可以使用一键脚本管理整套游戏环境：

```bash
./scripts/undercover.sh start    # 启动 BCS、前端和游戏 Bot，自动 onboard
./scripts/undercover.sh stop     # 先停止 Bot，再停止 BCS 和前端
./scripts/undercover.sh clean    # 停止后重置数据库、Bot 身份/会话/工作区及日志缓存
./scripts/undercover.sh restart  # stop → clean → start
```

每次 `start` 会在 Bot 启动前，按 `bots.json` 的角色映射复制游戏技能：
主持人使用 `referee/skills/undercover-game-referee`，玩家使用各自角色目录的
`skills/undercover-game-player`。目标是各 Bot profile 配置指向的运行工作区
`skills/`；游戏技能目录会完整刷新，其他技能（例如 `bcs-coordination`）保留。

`clean` 删除本地 BCS 数据目录（包括整个数据库中的注册、协作群和会话）、
生成的 BCS 配置，以及这 6 个游戏 Bot 的运行配置、身份、会话和工作区，
同时清理服务日志、前端 Umi 临时目录和依赖缓存。源码中的角色配置和
`.env.local` 保留；下次 `start` 会重新创建并 onboard Bot，需重新创建游戏协作群。
如果服务端口仍在监听，清理会中止。`start` 的模型选择沿用下文的菜单；
使用一键启动后可直接从第 6 步创建游戏协作群。

启动 BCS 和前端，并指定本次使用的游戏配置目录：

```bash
./scripts/singlebox.sh start bcs_frontend
```

这一步启动网页和协作服务，游戏 Bot 在下一步单独启动。检查服务状态：

```bash
./scripts/singlebox.sh status bcs_frontend
curl --fail http://127.0.0.1:21000/health
```

BCS 和 Frontend 应显示 `Running`，健康检查应成功返回。随后可以在浏览器打开 [Avernet 本地页面](http://127.0.0.1:8000/)。如果首页有“进入 Avernet”入口，点击进入工作台。

## 5. 启动并 onboard 游戏 Bot

运行：

```bash
./scripts/singlebox.sh start bots --profile-dir scripts/6bots_undercover_game_profile
```

如果出现模型配置菜单，根据第 3 步的准备选择：

| 菜单选项 | 使用场景 |
| --- | --- |
| `manual` | 已在 `.env.local` 填好模型服务参数 |
| `home` | 复用本机 OpenClaw 模型配置 |

脚本会为游戏角色准备运行环境、启动 Bot、连接 BCS，并自动执行 **onboard（注册 Bot 的名称和能力）**。看到 `Dynamic bots onboarded` 表示这一步成功；不需要逐个手工执行 onboard 命令。

检查进程状态和注册结果：

```bash
./scripts/singlebox.sh status bots --profile-dir scripts/6bots_undercover_game_profile
./src/bcs/target/debug/bcs-cli --url http://127.0.0.1:21000 list
```

六个 Bot 都应显示 `Running`，注册列表中应能找到以下角色：

| Bot 名称 | 在游戏中的角色 |
| --- | --- |
| 谁是卧底主持人 | 发牌、组织发言、计票和宣布胜负 |
| 玩家稳健老陈 | 谨慎、稳健 |
| 玩家话痨小满 | 喜欢场景和联想 |
| 玩家和事佬阿和 | 温和、倾向共识 |
| 玩家逻辑控林工 | 关注属性与逻辑 |
| 玩家戏精阿浪 | 表达生动、敢于指认 |

`Running` 只说明进程在运行；要继续开局，还应确认 onboard 成功且使用了真实模型配置。

## 6. 创建游戏协作群

回到 Avernet 页面，切换到**人类视角**。如果页面提示“加入 BCN”，先完成加入。

点击“拉起协作”，按下面填写：

| 设置项 | 填写内容 |
| --- | --- |
| 协作群名称 | `谁是卧底` |
| 协作目标 | 使用下方示例 |
| 协作类型 | **任务协作型**（主从模式） |
| 主节点（Manager Bot） | **谁是卧底主持人** |
| 成员 Bot | 1 名主持人 bot（谁是卧底主持人） + 5 名玩家 bot（稳健老陈、话痨小满、和事佬阿和、逻辑控林工、戏精阿浪）|

可直接复制这个协作目标：

```text
谁是卧底 · 6人局（5 Bot + 我）· 卧底1名 · 词语难度中等
```

主节点兼任群主，必须选主持人。若界面要求先选择成员再指定主节点，就先选齐六个 Bot，再指定主持人为主节点。

首次体验可以保留上述默认规则；想增加难度时，可将协作目标中的 `中等` 改为 `困难`。

## 7. 加入当前会话并开始

创建协作群后，进入游戏会话：

1. 在页面底部找到“用户协作”区域。
2. 点击 **“加入当前会话”** 并确认。
3. 等待主持人开场，在当前会话发送 `开始`。

**进入群页面不等于加入当前会话。** 主持人需要确认你已加入，才能安排你的发言和投票。游戏过程中请保持加入状态。

主持人会随机分配座位和词语，并告诉你“你是几号、你的词是什么”。每个人只知道自己的词，开局不会直接得知自己是平民还是卧底。记住座位号，再根据大家的描述判断谁拿到了不同的词。

## 8. 在游戏副屏中发言、投票

右侧会打开谁是卧底游戏副屏，显示玩家座位、当前轮次、公开发言和轮到你时的操作区。整局复用同一个副屏，发言和投票阶段会自动切换。

### 发言

轮到你时，在副屏的发言框输入一句描述并提交：

- 默认不超过 25 字，不能直接包含自己的词。
- 第一轮说得宽泛一些，后续轮次再逐渐增加线索，以副屏和主持人给出的要求为准。
- 可以通过“显示”查看自己的词，查看后再隐藏。
- 可以阅读其他玩家已经公开的发言，比较它们与你的词是否相符。

发言要在副屏提交；在普通聊天框说一句话，不能代替本轮的发言操作。

### 投票

所有人发言结束后，主持人会汇总发言并自动开启投票，你不需要另外发送“开投”。

在副屏中选择你怀疑的玩家，再确认提交；也可以选择明确弃权。副屏只提供当前可投的候选人，不能投自己或已经出局的玩家。收票期间不会显示其他人的投票目标，等主持人统一公布结果。

### 出局与下一轮

主持人计票后宣布结果，有玩家出局时按流程处理遗言，再推进下一轮。出局玩家会保留原座位并标记出局。如果你出局，就继续观看剩余玩家，不再参与后续发言和投票。

如果超过 5 分钟没有进展，可以点击副屏中的“告诉主持人卡住了”，或在当前会话发送 `卡住了`。

## 9. 胜负规则与再开一局

默认规则为 6 名玩家、1 名卧底、无白板、最多 6 轮：

| 情况 | 结果 |
| --- | --- |
| 卧底全部出局 | 平民获胜 |
| 场上只剩两名玩家，卧底仍在 | 卧底获胜 |
| 用完最多轮数，卧底仍未全部出局 | 卧底获胜 |
| 本轮最高票平票 | 无人出局，直接进入下一轮；本轮仍计入轮数上限 |

游戏结束后，主持人会公布词对、每个人的身份和词语，以及本局结果。

想再玩一次，在同一个协作群里**新建会话**，重新点击“加入当前会话”，再发送 `开始`。新会话会重新发牌，不必重新启动六个 Bot，也不必重新建群。

## 10. 常见问题与停止服务

| 问题 | 处理方法 |
| --- | --- |
| 创建群时找不到游戏 Bot | 检查 Bot 状态和 `bcs-cli list`，确认使用了游戏的 `--profile-dir` 且 onboard 成功，然后刷新页面。 |
| Bot 显示运行但没有回复 | 检查是否选择了 `mock`，以及真实模型配置是否可用。修改配置后重启游戏 Bot，再新建会话开局。 |
| 主持人一直提示加入会话 | 在页面底部点击“加入当前会话”；仅加入 BCN 或进入群页面还不够。 |
| 没有游戏副屏或副屏加载失败 | 确认使用包含游戏副屏的仓库版本，重新执行 `setup bcs_frontend` 并重启服务，再刷新页面、新建会话。 |
| 输入后游戏不继续 | 确认已经在副屏点击提交，并查看是否有字数、词语或投票校验提示。 |
| 游戏卡住 | 先发送 `卡住了` 让主持人处理；仍无法恢复时，新建会话重新开局。 |

需要重启游戏 Bot 时使用：

```bash
./scripts/singlebox.sh restart bots --profile-dir scripts/6bots_undercover_game_profile
```

玩完后停止这套游戏 Bot：

```bash
./scripts/singlebox.sh stop bots --profile-dir scripts/6bots_undercover_game_profile
```

如果也不再使用 Avernet 页面和协作服务，再运行：

```bash
./scripts/singlebox.sh stop bcs_frontend
```

更多角色说明和游戏实现细节见 [谁是卧底配置说明](../scripts/6bots_undercover_game_profile/README.md)。
