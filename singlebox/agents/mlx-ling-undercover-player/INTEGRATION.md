# 接入笔记：把「MLX 玩家」接进你自己的「谁是卧底」牌桌（30 分钟）

> 对应 Avernet #2611 验收第 4 条：任何同学应在 **30 分钟内** 把同一个玩家接进
> 自己的本地牌桌。本文件按 10+10+5+5 分钟分段，照抄命令即可。
>
> 玩家引擎名（认领登记）：**Apple MLX 本地端侧模型引擎（Ling-3.0-tiny LoRA）**。
> 不调用任何云端大模型 API——大脑是本机 MLX 推理的微调小模型。

## 0. 你将得到什么

一个纯 Python 的「谁是卧底」玩家 Bot：

- 支持 BCN V3 协议全部必需链路（握手/心跳/ACK/final/inject/abort/history）；
- 训练同款提示词调用本地微调模型，输出前规则清洗（≤25 字、泄词字删除、
  票面格式强制）——**主持人脚本 100% 可解析**，模型偶发跑偏也不会违规；
- 可选：脚本裁判 `referee_driver.py`（全 Bot 对局不需要 LLM 裁判时用）。

## 1.（10 分钟）起模型服务 + 玩家 Bot

前置清单（都可从 ling-cookbook recipe `guide/finetuning/ling-3.0-tiny/
undercover-player/README.md` 一步复现）：

| 项 | 说明 |
|---|---|
| 模型 | `~/models/Ling-3.0-tiny-4bit`（mlx_lm.convert 本地量化） |
| Adapter | recipe 的 `adapters/`（QLoRA，训好的权重，托管位置见 recipe README） |
| Python | ≥3.10，独立 venv：`pip install websockets openai mlx-lm`（mlx-lm ≥0.32） |
| BCS | 本机 Avernet singlebox 已起（`bcs-cli` 在 PATH 或传 `--bcs-cli`） |

```bash
# 终端 1：模型服务（OpenAI 兼容，QLoRA 挂载；serve.py 是防思考前缀的薄壳，可换 mlx_lm.server）
MLX_CPU=1 python serve.py --model ~/models/Ling-3.0-tiny-4bit \
    --adapter-path <recipe>/adapters/ --host 127.0.0.1 --port 8080 --trust-remote-code

# 终端 2：玩家 Bot（启动即自动 onboard 注册进 BCS 网络，token 落盘 ~/.mlx-ling-player.token）
python main.py --bcs-port <你的BCS端口，singlebox 默认 21000> \
    --base-url http://127.0.0.1:8080/v1 --bcs-cli <Avernet>/apps/bcs/target/debug/bcs-cli
```

验证：`bcs-cli list` 里能看到 `bot_331e67b7（玩家MLX小林）`（UUID 由服务端分配，
首次握手后固定；换名改 `main.py` 的 `BOT_NAME`）。

## 2.（10 分钟）把玩家拉上你的桌

已有牌桌（group）：

```bash
bcs-cli add-member --group <你的group_id> --bot-uuid bot_331e67b7
```

新建牌桌（推荐 manager_worker 拓扑，裁判=manager，玩家=worker）：

```bash
bcs-cli create-group --manager <裁判bot uuid> \
    --participants <其他玩家uuid,...>,bot_331e67b7 \
    --topic 谁是卧底 --context "<你的局配置>"
bcs-cli session create --group <group_id> --title 第1局 --input '"开始"'
```

然后照常开局——你的裁判（LLM 裁判或脚本）把 `speak_N` / `vote_N` 节点任务发给
玩家即可，本玩家**不需要**任何特判。它的输出契约：

| 任务 | 玩家回什么 |
|---|---|
| 发言节点 `[State Machine Task] node_id: speak_N` | ≤25 字的一句发言（已强制清洗，绝不含自己词语的字） |
| 投票节点 `node_id: vote_N` | 严格 `我投N号` 或 `我弃权`（PK 禁弃权时自动改投候选） |
| 「投票预备确认」 | 固定回 `投票开始，请大家投出一票，等待主持人公布结果。` |
| 遗言任务（bcs_assign_task 直派） | ≤35 字遗言（含全history，语气短平快） |
| 「表示你准备好了」 | `收到，我准备好了。` |
| chat.abort | 精确取消 run_id，迟到的 final 直接丢弃（见 test_loopback.py 用例 5） |
| 无须回复的群消息 | 空回执（服务端按 `bot relay skipped: empty message text` 静默丢弃） |

## 3.（5 分钟）冒烟验证

```bash
python test_loopback.py   # 不起 Avernet 的协议自测：握手/ACK/final/inject/history/abort 六链路
python probe.py           # 单条提示词打模型服务，确认清洗层输出形态
python preflight.py --base-url http://127.0.0.1:8081/v1 --adapter-path <recipe>/adapters/
                          # 开局前探针：长提示词冒烟（踩量化 KV 崩溃点）+ 底座/LoRA 判别 + 时延预算
```

上桌后看两处日志：玩家侧 `task received … reply=…`（每次节点任务的回话），
裁判侧票面解析结果（应全部是合法票或显式弃权）。

## 4.（5 分钟）换引擎实现同一玩家（Go/Java/Node 等）

协议事实（读 BCS 源码确认过，别改错）：

- **wire 格式**：`chat.send` 的 `message.content` = `{群上下文头}\n\n[消息内容]\n{正文}`；
  玩家只需从 `[消息内容]` 后取正文，节点任务正文以 `[State Machine Task]` 开头。
- **回话通道**：收到 `chat.send` 先回 `res`（payload 里的 `run_id` **就用请求帧的 id**，
  不要自生成），处理完发 `event: chat` 帧（`runId` 同请求帧 id，`state: final` + `content`）；
  `state: error` 会记进裁判的失败账本。服务端只按请求帧 id 注册 V3 run scope
  （bot/session 身份校验），自生成的 `run-uuid` 会以 "V3 event runId is unknown"
  被静默丢弃——节点等满超时才失败，日志里毫无痕迹（官方文档写「自行生成」，
  但 state-machine 节点投递上别名解析缺失；三局全通的 PydanticAI 玩家用的
  也是请求帧 id）。
- **空内容 = 不说话**：final 的 content 为空串时服务端不产生群消息，用它做「忽略」。
- **遗言是 RPC 派单**：裁判用 `task.dispatch`（req/res 帧）把任务发给指定 bot，
  回执走普通的 chat final。
- **节点任务里有全部上下文**：`[Input]`（JSON：round/history/rule/词）+
  `[Upstream Outputs]`（前面玩家的原话），不需要订阅群聊广播。
- **worker→manager 消息**：`task.message`（req/res 帧，必须带 `session_id`）会以
  `[from:<玩家名>] <正文>` 的 chat.send 送达主持人；全 Bot 桌的「卡住了」恢复通道就是它
  （`bcs-cli session chat` 只有人类参会者能用，Bot 调用会被 403）。

把 `player.py` 的提示词格式（与训练同款）翻译成你的语言即可，规则清洗层
（25 字截断/泄词删字/票面正则）必须保留——这是「100% 可解析」的来源。

## 时间预算参考

M4 Air 16GB、4-bit 底座 + QLoRA、CPU 推理（`MLX_CPU=1`）：单次发言/投票约
**30~90 秒**。节点超时预算建议 ≥7 分钟。GPU/M 系列加速（不设 MLX_CPU）可到
~10 秒级。本 Bot 的提示词经历史窗口化后恒定有界（≤420 字符 ≈ 数百 token）；
但共享模型服务里对手引擎每条请求自带 2 万字框架提示词（实测 15k~20k token），
投票链并行扇出时队列拥堵，节点预算 420s 不够用（坑 #9）——能改裁判配置就给
900s+。

## 常见坑（我们踩过的）

1. **websockets ≥14 默认读环境代理**，连 `127.0.0.1` 也走 SOCKS →
   `websockets.connect(url, proxy=None)`。
2. **同步推理阻塞事件循环** → 心跳超时、abort 不及时；推理务必丢线程池
   （`asyncio.to_thread`）。
3. **Ling 模板的思考块**：直接推理会吐 `</thinking>` 前缀污染输出 →
   渲染时 `thinking_option="off"`（`serve.py` 已注入），解析再剥模板残渣。
4. **贪心解码坍缩**（全弃权/万能句）→ 用固定种子采样（temp=0.7, top_p=0.9）。
5. **final 事件凭空消失、节点 420s 超时**：事件的 `runId` 用自生成的
   `run-uuid` 而不是 chat.send 请求帧 id → 服务端 run scope 校验直接拒绝，
   服务端/玩家两侧日志都无记录。`runId` 一律抄请求帧 id（见 §4 回话通道）。
6. **全 Bot 桌开局前先确认所有玩家的模型后端都就绪**：OpenClaw 玩家的 LLM 后端不可用时
   （如模型服务还在加载、端口没起），每个节点任务会在 2 秒内以 `Agent run failed before
   completing a reply.` 报废整个 run——错误串是 BCN 频道插件合成的，真因要看 OpenClaw
   日志里 `model-fetch` 的 `ECONNREFUSED`。开局前对每个后端打一次真实补全冒烟
   （`/v1/models` 可能在模型加载完成前就返回 200，不算数）——`preflight.py` 就是
   干这个的，长提示词（>2048 token）才能踩到量化 KV 崩溃点。
7. **节点静默死亡 = 全桌干等 4 小时**：run 报废后（玩家超时/报错）没有任何机制推进
   下一步，裁判只会在 `game_timeout`（默认 4h）后超时崩掉。日志特征：phase 卡在
   `*_RUNNING`，但该 run 的 collaboration status 已不是 `running`。两个件配套的
   可靠性模式：
   - **自愈通道**：玩家以 worker 身份发 `task.message`「卡住了」→ 裁判 `sx_recover`
     重开本轮（每轮预算 2 次，超预算只能作废重启 driver——预算在 driver 进程内存里）；
   - **停滞哨兵**：240s 轮询 driver+玩家+**模型服务**三方日志静默时长（健康链路每
     ≤5 分钟必有真实行——≥1000-token 预填才算模型侧心跳，几行的探测噪音不算），
     三方同静 20 分钟且 phase 在 `*_RUNNING` 才报警——只比对 run status 会误报
     （投票 run 是新 run_id，driver 日志里要等计票节点才有痕迹）；
   - **恢复期间保持玩家在线**：重开提交（裁判侧 `open-round/open-vote --retry`，或
     `sx_recover` 的自动重开）会把新 run 的首个节点投递给本 bot，bot 离线直接 400
     （`Bot 'bot_xxx' is not connected`）。「停玩家 → 发卡住了 → 重启玩家」老流程在
     本方节点排首位时必翻车；`task.message` 只能由 worker 发、且 BCN 拒绝同一身份
     重复连接——要以玩家身份发 nudge/回灌时才短暂停玩家，发完立刻重启。
8. **`task.dispatch` 必须带 `session_id`**：漏了不报参数错，而是服务端存储层
   `transaction parameter 3 references missing row 0 from step 1`（会话消息序号行按
   session_id 查，查不到整笔事务崩）——错误信息完全看不出真因。遗言派单这类
   manager→worker 的 RPC 派单，`group_id` + `session_id` + `target_bot` + `message`
   四个都要带（同 `task.message`）。
9. **420s 节点预算 × 肥上下文拥堵 = 投票链结构性必死**：投票节点是**并行扇出**
   （vote_start → [vote_N…] → tally），4~6 个 vote 节点同时砸向共享的单实例模型
   服务，串行预填 15k~51k token 的后期提示词——最慢的那张票几乎必超 420s
   `node_timeout_ms`，整个 sm run 判死，计票节点永远不派发（三局实战死 4 次）。
   **肥上下文归属（模型服务请求体日志实测）**：肥请求几乎全是对手引擎的——
   OpenClaw 每条请求自带 `You are a personal assistant running inside OpenClaw`
   框架系统提示词（单条 2.2 万字），26 条抽样里 20 条是 21.6k~24.5k 字符；本 Bot
   的请求 ≤420 字符（历史窗口化后有界）。所以「压缩己方上下文」只能救自己这侧
   的延迟抖动，队列大头在对手框架里（不在本 PR 范围）。
   迟到的票面还在（BCS 日志 `session_complete_result=chat_preserved`），**别 re-vote
   碰运气**，两步治标：
   - **计票节点回灌**：以玩家身份 `task.message` 伪造 `[State Machine Task]\nnode_id:
     tally` + `[Upstream Outputs]` 的 `[vote_N]` 票面原文（全员都要，缺票
     `votes-set` 报 VOTE_SET_MISMATCH）+ `--round N --stage regular --attempt M`，
     driver 走设计内 `s4_tally→reveal→finish` 全链。票面不齐的从 BCS 日志捞。
   - **回灌后补一句「卡住了」**：`task.message` 的回复不回流成 ECHO，verdict=continue
     时 driver 不会自己开下一轮（真实计票节点的回复落在会话 chat 里才会被 ECHO 唤醒）。
     AWAIT_NEXT_ROUND 下的「卡住了」走 `sx_recover` 的 else 分支直接 `on_phase_wake`
     → 开下一轮，是合法路径。
   治本要压的是**对手框架的 2 万字提示词**（己方上下文已窗口化到 ≤420 字符）；
   你的桌能改裁判配置的话，把 `node_timeout_ms` 提到 900s+ 也能兜住（我们这轮的
   验收边界是不改主持人脚本和对手框架，所以做了回灌 + 己方窗口化）。
