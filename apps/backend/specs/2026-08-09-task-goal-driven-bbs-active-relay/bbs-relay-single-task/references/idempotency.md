# 幂等与接力约定(bbs-relay-pickup)

## 1. 任务级 claim CAS(防双做)

- `POST /bbs/claim` 对根 `bbs_owner`(`root.run_info.extend_props["bbs_owner"]`)做**条件写**:空 → 写自己(200);非空(他人)→ `TaskStateError` → **409**。服务端 per-task `RLock` + 态机裁,**恰一赢**,无并发双占。
- **同 bot 重 claim 返 200**(幂等,视为已占有);只有**别的 bot** 占着才 409。
- claim **仅校验 `bbs_mode==true`**;不判图空闲 / 根 PLANNING / 深度闸——那些由 `bbs/attach` 裁(见 §6)。
- **409 → skip 换任务**:输者不重试同任务,回步① 取下一个候选。无双做窗口(CAS 把"同时占根"收敛为"一赢一 409")。

## 2. 写口持有者校验(防绕过 claim)

- `bbs/attach` 与 `bbs/result` 服务端校验调用者 `bot_id == root.bbs_owner`;非持有者 → **409**。
- 你没有任何绕过 claim 的写口:未占根不能 attach、不能 result。这是天然护栏——claim 成功才允许 attach / 干活。

## 3. harness SLA lease(不续租、崩溃到期被清)

- claim **不会续租**;claim 生命周期 = 你这一次 scoped 节点的生命周期。
- **正常**:步⑤ `bbs/result` 返回 200 → 服务端 `finally` **无条件清根 `bbs_owner`** = 释放 claim。
- **崩溃**:你 claim 后 attach 了节点,但进程崩溃未 `bbs/result` → 节点长 `RUNNING` 不报 → `TaskHarness` SLA 到期 → harness:**清 `bbs_owner` + 把死节点标终态**(**不重派**、不自动再做)。
- 释放后下个 bot 可重新 claim(见 §4)。无卡死、无根被永占。
- BBS 节点**不被**框架 dispatch/drain 自动消费(守卫:dispatch/drain 跳过 `run_mode=="bbs"`),只有持有 claim 的 bot 自驱。

## 4. 接力级幂等(只读不重做)

- 下个 bot 重新 claim 同一任务根 → 重新读 dashboard → 读「根 `goal` + 已 DONE 叶子 `output` + 前序 scoped 节点 `run_info.output`(checkpoint,含 FAIL+gaps 段的部分产出)」→ 重算剩余 → 挂**新** scoped 节点续做。
- **已 DONE 不重做**:DONE 节点的产出已并入根目标的已覆盖集合;新 scoped 节点只做尚未覆盖的剩余。
- partial 交棒(`FAIL+gaps+output_patch`):下个 bot 读 `gaps` 知差距、读 `run_info.output`(你的 `output_patch`)知已做部分 → 续做未完成部分,**不重复**你已做的。

## 5. 一次 pass = 一次 result(中途不打 checkpoint)

- `bbs/result` 在服务端 `finally` 无条件清 `bbs_owner`:**发了 result = 结束本次 pass + 释放 claim**。
- **禁止在干活中途调 `bbs/result` 仅为打 checkpoint**——它会立即释放 claim、结束你的 pass;之后你不再是持有者,继续写回会 409。
- checkpoint 的正确姿势:把 `output_patch` 随你**这一次** result 一起发(终态 PASS 或 FAIL+gaps),让产出落进 `run_info.output` 供下个 bot 续。
- **长活 = 分段接力**:一段做完 → 报 partial(checkpoint)释放 → (同 bot 或他 bot)重新 claim → attach 新节点续下一段。每段独立 claim/result,每段 checkpoint 都 durably 落库,SLA 切断也不丢。

## 6. 深度闸 → HUNG(唯一人工入口)

- 每次成功 `bbs/attach` 消耗 1 个 `bbs_relay_count`(图 `extend_props["bbs_relay_count"]`,初始 0,每次 attach +1)。
- `BBS_MAX_DEPTH`:per-task 可配(图 `extend_props.execution_config.BBS_MAX_DEPTH`),默认 3。
- **`bbs_relay_count >= BBS_MAX_DEPTH` → 图被标 `HUNG`,`hung_reason="bbs_relay_exhausted"`,并拒后续 attach(409)。** 此为唯一人工介入入口,**无可自动恢复**。
- 区别于 `loop_round`(升级计数,由 `MAX_LOOP` 兜底,属既有 BBS 升级机制)——`bbs_relay_count` 才是接力深度闸。
- 实操:scoped 节点宜少宜准;partial 分段会累积深度,**总段数受 `BBS_MAX_DEPTH` 上限**。

## 7. 无独立 release 路由

- 服务端**无** `bbs/release` 之类路由。释放 claim 的唯一写口是 `bbs/result`(正常)或 harness SLA 到期(崩溃 / 让出)。
- 故步① **预筛极其重要**:只在确信"能 attach 且能做"时才 claim,避免空占根只能等 SLA 到期(期间该任务被 pin 住、他 bot 无法接)。
- 已 claim 但发现:attach 409(非深度闸)/ 判 skip → **不要 no-op attach**(白耗接力深度、推向 HUNG);让 claim 经 SLA 到期自然释放,本次唤醒结束。
- 若步① 读完 dashboard 已能判 skip(能力不匹配)→ **根本不 claim**,直接换任务,是最干净的做法。
