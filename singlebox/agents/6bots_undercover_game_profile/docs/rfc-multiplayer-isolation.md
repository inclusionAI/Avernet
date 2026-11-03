# RFC-101 谁是卧底多人版：上下文隔离与 HumanInput 编排

- **状态**：Draft（竞标并行贡献，`wt-2609-multiplayer`）。待维护者评审定稿。
- **实施状态（随本 PR 已落地）**：profile 侧（`undercover.py`）已完成 2～4 Human 配置、
  私有发词、§6 多人类**投票 Human 链**（Bot 并行 + Human 串行）；panel 侧已做多人类高亮防御纵深
  （`viewModel.ts` 仅激活 `currentViewerActorId` 匹配的待办）。BCS 服务端 P1（assignee 支持）未实现，
  单独提 PR，见第 6/8 节。
- **范围**：2～4 位 Present 人类 + M 个玩家 Bot，总人数 4～8，主持人另计，卧底数可配。
- **修改面**：profile（`undercover.py` 编排 + 指令）+ panel（多人类高亮 + 隔离护栏）。
- **需服务端改动（本 RFC P1，见下）**：前端 HumanInput 支持 `assignee.type: runtime_actor`；
  若获准，单独提 PR，不在此 profile PR 混入。

## 1. 目标（可验收）

1. 每位 Human 只在**自己的私有 instruction** 里拿到自己的词，其他任何接口/副屏/面板参数/群聊都拿不到。
2. 「访问者身份」由**认证（服务端 caller_actor_id）+ 节点 assignee** 共同决定；前端 `currentViewerActorId`
   只用于把「我是哪个座位」高亮出来，不承载任何权限判定。
3. 多人类「发言串行、投票并行」：发言在同一阶段内串行推进（传递闭包保 public 可见），
   投票在同一运行内 Bot 并行、多位 Human 按座位顺序 + requirement 闭环收集，绝不让 A 的原始票进入 B 的指令面。

## 2. 四个已确认的不可动硬约束（来自现有实现，改动不得破坏）

- **BCS 一次只支持一个待处理 HumanInput**——human_input.rs `handle_session_human_input`（WS 会话输入）显式拒绝
  `multiple pending human_input nodes are not supported`。且根目录 schema：前端 HumanInput 节点**不得**带 assignee（definition.rs 305-346）。
- 运行一次只占一个协作槽位；发言/投票必须拆两个运行（README）。
- 遗言是公开回执，遮蔽后仍留原始文本在聊天记录（C5）——只能力求少、不能抹掉。
- Bot 收不到群广播，只收到自己节点/任务；@ 对人类无效（worker 收不到）。

## 3. 发词走私有通道（P0，profile 即可达）

取代「主持人在群聊把词发给唯一人类」：

- 词只出现在**对应 Human 座位自己的 `human_input` instruction**（既有 `UNDERCOVER_UI_CONTEXT_V1` 私有块 + 节点正文）。
- 面板 **公开参数不含任何词**：`seatOrder`/`turnOrder`/`players`/`publicHistory`/`voteCandidates` 全为公共面；
  `currentViewerActorId`、`currentAction` 只表达「动作归属/高亮」，明确在文档标注**非认证证据**。
- `my-word` 命令改为接受 `--seat`（或缺省=调用者对应座位）：人类忘了词就再跑，只回答该座的词。
- 群聊开场**不发词、不念词**；主持人只在沉默场景（cache 恢复）补一句「你的词在右边副屏的输入框上方，忘了点开详情」。

> 若当前 `my-word` 的入口是「群聊机器人对会话的唯一人类」——多人类后该假设失效。必须绑定座位：
> `uc my-word --session <sid> --seat N`，只吐该座的词；服务端若不允许按人类过滤，则保持 `--seat` 为一个
> 显式的「玩家自报座位」，与 panel 的 `currentViewerActorId` 同为**非认证**面，仅对话编排时由主持人主观使用。

## 4. 访问者身份来自认证（P1，需服务端）

现状（威胁模型 §4）三个接口都对前端 HumanInput 越权：
`pending-human-nodes`、`node-detail`、`respond`。root 原因都是**定义期禁止前端节点带 assignee**。

RFC 主张（**若获维护者批准，单独 PR**）：

1. `definition.rs`：放开 `(None, Some(_)) => reject` 分支，允许前端（无 notification）HumanInput 用
   `assignee.type: runtime_actor, actor: <human actor>`。
2. access.rs / human_input.rs：这三处已经对 `RuntimeActor` 做了精确过滤（`list_pending`、`respond`），
   把**无 assignee** 的语义从「any Present Human」收紧为「仅当该节点无 assignee 时才 any」，有 assignee 则只该 actor。
3. `state-machine-runs/{run}/nodes/{node}`（node-detail）：加 `authorize_node_view(run, node, human)`——
   无 assignee 节点仍 any；有 assignee 节点仅 assignee 可读，否则 403/404。
4. 前端 `currentViewerActorId`：降级为「参照高亮」。即使它被改，服务端过滤仍拒绝越权 reads；
   前端只把 `pending`（认证后只返回本人）当真正「轮到谁」的来源。

**降级路径（不实现服务端时）**：本文第 8 节测试仍强行通过——把 `list_pending`/`node-detail` 的越权读
当作「已知平台限制」记录为 `⚠️`，隔离测试在**平台无法隔离的格**上标注「需服务端 P1，非本 PR 通过项」，
只对 profile 保证的格（公开参数、上游传递、群聊）做硬断言。issue 验收明确允许「服务端格不强制」。

## 5. 发言：串行（既有传递闭包不变）

- 每个存活玩家按座位顺序一个节点 `speak_N`；第 i 个节点 `targets: [followers..., collect]`。
- 人类发言节点与 Bot 相同排列，`instruction` 用 UI 上下文带上自己的词。
- **并行安全性**：串行图天然保证同一时刻只有一处人类 pending，不触发「multiple pending」；且传递闭包让后手
  Bot 只拿到**前序玩家的公开转述**，不拿到任何词。
- 多人人类在发言阶段同样各自在「轮到自己的那个节点」才动作，其他人副屏显示等待。

## 6. 投票：同一阶段内 Bot 并行 + Human 顺序收集（核心编排变更）

**问题**：既有单人版投票图是「扇出 + 汇合」，多个人类节点同时进入 running 会触发
`multiple pending human_input nodes not supported`（对 WS 会话输入路径），且 A/B 原始票会互相进对方上游。

**RFC 编排（不动 BCS 协议）**：

- 同一投票运行内仍一个 `vote_start`（Bot 预备确认）入口 → 扇出到**所有 Bot** 投票节点（并行，互不可见）。
- Human 部分：按座位顺序 `vote_ha → vote_hb → … → collect` 连成一条**链**，每个人类节点
  `targets: [下一位 Human, collect]`（即非末尾都指向下一位 + collect，末尾指向 collect）。
  任一人类节点完成后 collect 会到达——因为 collect 的「读到所有 Human 已提交」由链式保证。
  而 Bot 节点与 collect 之间，靠「Bot 节点 targets 指向 collect」实现病式 join。
  **结果：一个运行，Bot 并行、人类按序收齐、collect 只在所有人完成时触发。**
- **人类投票票面不互相污染**：`check_text` 语义上把投票规范化成 `我投N号`；但 BOT 只见「座位号键 + 票面串」，
  Human 之间天然不共享任何节点。面板在投票阶段对每个 Human 只显示自己的待办，其他 Human 显示「已投票/等待」。
- **防「A 的票串给 B」**：panel 不展示他人投票；`votes-set` 由 collect(主持人) 聚合，A/B 各自单独提交，
  主持人拿到的是「座位→票号」全局字典，从不在节点间转发 Human 原始票。

实现要点（`render_vote_yaml`）：
```text
nodes:
  vote_start(bot starter) → targets: [vote_b1, vote_b2, ..., vote_h1]
  vote_bX (each bot)      → targets: [tally]
  vote_h1 (human A)       → targets: [vote_h2, tally]
  vote_h2 (human B)       → targets: [tally]
  tally (referee)         → final_output
```
- 若仅 1 个 Human，`vote_h1.targets=[tally]`（与现状完全一致，回退零变化）。
- Bot 并行 + Human 链都汇聚到 tally，BCS join 等待全部上游完成。

**不引入「等待信号」伪节点**：当 Human 数量≥2，链的好处是 A 提交后立刻激活 B 的节点，
不需向主持人自派任务，符合「末节点不开下一运行」纪律。

## 7. 面板多人类适配（public）

- `players[].isHuman` 已存在；多人类时 B 的节点应以 `Hx（你）` 标记，A 的节点不显示「你」。
- 高亮：`currentViewerActorId` 命中该视图的 `isHuman` 座位才加「当前玩家」框；无命中（观战/他人面板）不框。
- 投票阶段：human 座位显示“已投票/等待”，**绝不在 dock 列出他人投票目标**——现状已是如此（只列表本人待办），保持。
- 公开参数 v1 主题：多人类时 `voteCandidates` 不含任何人类座位（人类不能投人类？——规则：可投却不可投真人？需与规则一致：
  卧底版通常可投任何人）。**决策：与单人一致，投票对象是「所有存活玩家」，含其他人类**；即 `voteCandidates` 列出其他
  存活人类。这条会在面板可见性矩阵里标 `✅`（公开面）。

## 8. 隔离测试（自动化「偷看者」）

见 `tests/test_peeker_isolation.py`（referee 层）与 panel `test/undercover-game/isolation.mjs`（界面层）。断言方向：

1. **公开参数**：对含 2 个 Human 的 state 渲染 speak/vote，`panel` 全量序列化后逐字扫描，不含任一 `word` 或 `role`。
2. **upstream 传递**：渲染多个 Human + Bot 的 vote YAML，断言没有任何一个 Human/Bot 节点的 instruction 里出现
   **其他玩家**的词（`speak_N.word` 只存在于自己节点；投票不出现任何词）。
3. **群聊 / my-word**：`my-word --seat A` 只含 A 词；`status/render-*` 输出不含词（沿用既有纪律）。
4. **node 隔离（标 ⚠️，需 P1）**：断言 `pending`/`node-detail` 在**无服务端 assignee 支持**时返回给任一 Present Human——
   **故意让隔离测试“抓住”这个格**，作为已知待修；若服务端放行则标记为需 P1。
5. **人为注入泄露抓得住**（issue 验收 2）：在测试里写一个 helper 把 A 的词临时塞进 panel `players[].publicFields.word`，
   断言隔离扫描 **必须报错**——证明测试有力量，不会永远绿灯。

> invariant：隔离测试永远不允许「我改别处时把词写进公开信道而不断言失败」静默通过。

## 9. 配置：总人数 4～8、卧底数可配

- `bots.json`：固定 3 个玩家 Bot（老陈/林工/阿浪）；总人数 = used Humans + 该默认数。
- begin/init：`--undercover N`（默认 1，必须 `< 总人数`，否则 BAD_CONFIG）。
- 开局校验：Present Humans 数 2～4（`session_roster` 返回全部人类，不再 `len>1` 报错）；总人数在 4～8。
- 若 Present Humans+默认3个Bot > 8，拒绝开局提示调整。

## 10. 范围外（明确不做）

- 观战、断线重连、>8 人。
- BCS 服务端（除 P1 单独 PR 外）。
- 不改群聊广播机制、不改 `multiple pending` 平台行为、不新增存储/通知渠道。

## 11. 验收对照

| issue 验收 | 本文 |
| --- | --- |
| 威胁模型+可见性矩阵+现状泄露点 | `docs/threat-model-multiplayer.md` |
| RFC 发词走私有通道/身份认证/发言串行投票并行 | 本 RFC |
| 2～4 Human+N Bot 总人数 4～8、卧底可配 | undercover.py + bots.json + 面板 |
| 隔离测试覆盖可见性矩阵每格 + 注入可抓 | test_peeker_isolation.py + panel isolation.mjs |
| referee tests/*.py 与 panel verify 通过、单人类不回归 | 全套 + flow_baseline 对拍 |
| 复盘日志 3 局多人 | 真实对局留档（另文） |