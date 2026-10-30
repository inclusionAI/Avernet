# 故障注入验收记录：10 次随机杀掉玩家 Bot，卡死 0 次

- 日期：2026-10-30
- 关联：Issue #2610；实现见 #3334（BCS）与 #3335（主持人技能、副屏、注入脚本）
- 代码：`oss-camp2026` @ `86252c1e9`，加上 #3334 与 #3335 的提交
- 环境：与 [repro.md](repro.md) 相同。singlebox standalone（SQLite），模型 `deepseek/deepseek-v4.1-flash`，
  `6bots_undercover_game_profile`（1 位人类 + 5 个玩家 Bot，主持人为 manager），群消息队列未开启（legacy 直投）；打开队列后的复测见第 4 节
- 命令：在 profile 目录下执行 `python3 tools/fault_inject.py --injections 10 --out acceptance.csv`

## 1. 方法

- 脚本经 BCS HTTP 接口扮演人类玩家 `human_001`，与副屏调用同一组接口。每局开始时在群里说「开始」，
  之后只通过节点回复接口提交发言和投票，不再发任何聊天消息，也不用「卡住了」按钮。
- 只向每个阶段的第一次运行注入：发言或投票运行开始后，随机挑一个节点尚未派发的存活玩家 Bot，
  用 `SIGKILL` 杀掉它的 OpenClaw 网关进程。轮到它的节点时派发失败，Run 随之失败。
- Run 失败后，脚本立即用 singlebox 原命令和原环境把该 Bot 拉起，模拟守护进程。
- 判定：从 Run 失败到同一会话出现新 Run，不超过 60 秒算恢复；5 分钟内没有新 Run 算卡死。
- 另外从检查点表读出 RunResult 的状态与送达时间，把平台投递耗时和主持人反应耗时分开统计。

## 2. 结果

| # | 对局 | 阶段 | 被杀玩家 | 失败的 Run | RunResult | 平台投递 | 恢复耗时 |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 发言 | 逻辑控林工 | `sm-641c8771` | `delivered` | 3 ms | 3.2 s |
| 2 | 1 | 投票 | 戏精阿浪 | `sm-388719e3` | `delivered` | 4 ms | 4.7 s |
| 3 | 2 | 发言 | 和事佬阿和 | `sm-dd6dd0ec` | `delivered` | 3 ms | 3.9 s |
| 4 | 2 | 投票 | 戏精阿浪 | `sm-7e70e120` | `delivered` | 2 ms | 49.1 s |
| 5 | 3 | 发言 | 戏精阿浪 | `sm-21b2d0d6` | `delivered` | 3 ms | 3.4 s |
| 6 | 3 | 投票 | 戏精阿浪 | `sm-0f91ac42` | `delivered` | 3 ms | 4.1 s |
| 7 | 4 | 发言 | 戏精阿浪 | `sm-3c211c26` | `delivered` | 3 ms | 32.1 s |
| 8 | 4 | 投票 | 逻辑控林工 | `sm-efdcd667` | `delivered` | 3 ms | 4.3 s |
| 9 | 4 | 发言 | 话痨小满 | `sm-b543d6c1` | `delivered` | 5 ms | 3.5 s |
| 10 | 4 | 投票 | 戏精阿浪 | `sm-3b5e0b49` | `delivered` | 3 ms | 55.1 s |

- 平台投递：从 Run 失败到 RunResult 检查点记为已送达。
- 恢复耗时：从 Run 失败到主持人用 `uc recover` 创建的新 Run 出现。

汇总：

- 10 次注入（发言、投票各 5 次，共 4 局，历时 43.2 分钟）全部在 60 秒内恢复，卡死 0 次。
- 恢复耗时中位数 4.2 秒，最大 55.1 秒。
- 10 条 RunResult 全部是 `delivered`，平台投递耗时 2–5 毫秒。
- 改动前，同样的注入下主持人始终没有被唤醒（见 [repro.md](repro.md)）。

## 3. 慢的三次

第 4、7、10 次分别用了 49.1、32.1、55.1 秒。这三次的平台投递同样只用了 2–3 毫秒，时间都花在主持人的模型调用上：

- 测试用的 ZenMux key 每分钟只放行约 5–7 次成功请求，超出的返回 HTTP 429。本地中转每 3 秒重试一次，最多重试 120 秒。
- 以第 7 次为例：主持人会话在 Run 失败后立刻收到 RunResult，随即发起模型调用。这次调用连续 7 次被 429 挡回，
  31.7 秒后才成功，`uc recover` 随后马上重开了发言阶段。
- 第 4、10 次在投票阶段，同一时刻有多个请求一起排队，等待分别达到约 48 秒和 55 秒。
- 整段验收共 201 次模型调用，平均每分钟 4.7 次，已经贴近这个限额。

所以 60 秒的余量主要被模型限流占用，而不是被平台占用。提高限额，或者让主持人与玩家使用不同的 key，
都能缩短这段等待。

## 4. 打开群消息队列后的复测

第 1–3 节用的是 singlebox 默认配置：群消息队列关闭，RunResult 走直投。为了覆盖「走现有 Send 队列」这条路径，又在打开队列的情况下重跑了 10 次。

- 开关：`PUT /admin/message-delivery/policy`，把 `flow_enabled.group` 和 `flow_enabled.system` 设为 true，`defaults.mode` 设为 `enforce`。
  这样每个 Bot 同时只处理一条消息，两次发送至少间隔 1 秒，其余配置不变。跑完已恢复为关闭。
- 其他环境和注入脚本都与第 1 节相同。

### 4.1 复测发现的问题（已修复）

第一次打开队列，第 1 次注入就卡死了：

- RunResult 在 Run 失败后 3 毫秒入队。85 毫秒后，这条投递还没发出就变成了 `failed`。
  检查点在入队时已经记为 `delivered`，所以不会补投，主持人一直没被唤醒。
- 原因：队列发送前用 `canonical_text()` 取正文，而一次性结果只认 `chat` 类型，`run_result` 被判为
  「canonical message text invalid」。
- 修复：#3334 的 c77f97f66 让 `run_result` 和 `chat` 一样按一次性结果读取。
  bootstrap 测试原来只断言入队成功，现在会对入队的那一行真正执行一次准备和发送。

### 4.2 修复后的结果

| # | 对局 | 阶段 | 被杀玩家 | 失败的 Run | RunResult | 队列投递 | 入队→开始发送 | 恢复耗时 |
|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 发言 | 逻辑控林工 | `sm-632b0f92` | `delivered`，3 ms 入队 | `group/send` → 主持人 `completed` | 46 ms | 3.3 s |
| 2 | 1 | 投票 | 稳健老陈 | `sm-2aa390da` | `delivered`，2 ms 入队 | `group/send` → 主持人 `completed` | 3815 ms | 7.0 s |
| 3 | 1 | 发言 | 逻辑控林工 | `sm-15001158` | `delivered`，2 ms 入队 | `group/send` → 主持人 `completed` | 63 ms | 3.5 s |
| 4 | 1 | 投票 | 和事佬阿和 | `sm-ca4adad5` | `delivered`，5 ms 入队 | `group/send` → 主持人 `completed` | 56439 ms | 71.6 s |
| 5 | 1 | 发言 | 和事佬阿和 | `sm-6581e0cd` | `delivered`，3 ms 入队 | `group/send` → 主持人 `completed` | 850 ms | 53.4 s |
| 6 | 1 | 投票 | 逻辑控林工 | `sm-9d8e2713` | `delivered`，3 ms 入队 | `group/send` → 主持人 `completed` | 37 ms | 58.4 s |
| 7 | 2 | 发言 | 和事佬阿和 | `sm-7995c8ef` | `delivered`，2 ms 入队 | `group/send` → 主持人 `completed` | 77 ms | 3.4 s |
| 8 | 2 | 投票 | 稳健老陈 | `sm-6e2e4df9` | `delivered`，2 ms 入队 | `group/send` → 主持人 `completed` | 557 ms | 4.0 s |
| 9 | 3 | 发言 | 逻辑控林工 | `sm-296831e5` | `delivered`，4 ms 入队 | `group/send` → 主持人 `completed` | 78 ms | 2.9 s |
| 10 | 3 | 投票 | 和事佬阿和 | `sm-5fae5bb4` | `delivered`，3 ms 入队 | `group/send` → 主持人 `completed` | 30 ms | 16.3 s |

汇总：

- 10 次注入（发言、投票各 5 次，共 3 局，历时 53.9 分钟），卡死 0 次。
  9 次在 60 秒内恢复，第 4 次用了 71.6 秒，恢复耗时中位数 5.5 秒。
- 10 条 RunResult 都在 Run 失败后 2–5 毫秒入队，10 条队列投递全部 `completed`，目标都只有主持人。
- 第 4 次超时的原因：RunResult 在队列里等了 56.4 秒，等主持人手上那一轮（开投票）结束。
  那一轮的模型调用先被 429 挡了 45 秒，之后又碰上几次上游 SSL 错误和 500。
  主持人收到 RunResult 后 15 秒就重开了投票。
  关闭队列时 OpenClaw 也是按会话串行处理，这段等待不会消失，只是从 BCS 的队列挪到了 OpenClaw 内部。
- 第 5、6 次（53.4 秒、58.4 秒）也是模型限流。它们在队列里只等了 0.85 秒和 37 毫秒，
  时间都花在主持人收到之后的模型调用上。
- 第 2 局开局时，主持人发完牌没有调用 `open-round`（模型没按技能执行）。
  脚本在 120 秒后按开局兜底再说了一次「开始」。这只发生在开局，恢复过程中没有任何人类消息。
  第一次复测时同样的情况让对局一直停在开局，这是加兜底的原因（#3335 的 a8c3bd48a）。
