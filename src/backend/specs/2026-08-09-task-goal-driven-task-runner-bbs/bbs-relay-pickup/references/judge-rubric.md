# 自判判据:full / partial / skip

步③ 的 LLM 自判(确定性代码不判)。输入来自步① 的 dashboard(整图 `TaskExecutionGraph`)。

## 输入字段(从 dashboard 读)

- **根目标**:根节点(`tasks[]` 中 `node_id == task_id`)的 `task_spec.goal.objective` 与 `goal.acceptances[]`(逐条 AC)。
- **已成 DONE 的叶子**:`tasks[]` 中 `status=="DONE"` 的节点,其 `run_info.output` = 已交付产出;其 `task_spec.goal.acceptances` = 该子目标已达成。
- **前序 scoped 节点 checkpoint**:`tasks[]` 中 `run_info.run_mode=="bbs"` 且 `status in {DONE,FAILED}` 的节点:
  - `run_info.output` = 前序接力段的 checkpoint(含 FAIL 段落下的部分产出——来自其 `bbs/result` 的 `output_patch`);
  - `run_info.acceptance_result.gaps` = 该段自报的剩余差距。
- **图级**:`status`(`PLANNING` 可接 / `DONE` 已完成 / `HUNG` 硬终态)、`extend_props.bbs_relay_count` vs `BBS_MAX_DEPTH`(默认 3)、根 `run_info.extend_props.bbs_owner`(是否已被占)。

## 计算"剩余"

**剩余 = 根 `goal.acceptances` 全部 AC 的并集 − {已 DONE 节点产出的并集}**,再以前序 scoped 节点 checkpoint 细化:
- 逐条 AC 判:该 AC 是否已被某个 DONE 节点的 `output` 完全覆盖?未覆盖 → 剩余。
- 前序 FAIL+gaps 的 scoped 节点 `output_patch` 可能已**部分**覆盖某条 AC——读其 `output` 与 `gaps` 细化该 AC 的剩余(如"已做第1、2节,缺第3节"→ 剩余 = 第3节)。

## full / partial / skip

### full — 剩余我全能做
- **判据**:剩余的全部 AC 都在你的能力范围内,且你能在 harness SLA 窗口内一次做完。
- **行动**:步④ `task_spec` 覆盖**全部剩余**;步⑤ `verdict=PASS`、`acceptances_metric` 列出达成的 AC、`gaps=[]`、带 `output_patch={完整产出}`、`root_verified=true`(做完后根 acceptance 全达成时)。
- **预期**:图 `DONE`,接力收口。

### partial — 剩余里我只能做一部分
- **判据**:剩余的 AC 中,你能做一部分(某几条 AC、或某条 AC 的一部分),其余超你的能力 / 超单次 SLA 窗口。
- **行动**:
  - 步④ `task_spec` 只封装**你能做的那部分**——`goal.objective` / `instruction` 精确圈定范围,不要假装覆盖全部剩余。
  - 步⑤ `verdict=FAIL`、`gaps=[剩余你未做的 AC / 差距描述]`、`output_patch={本次部分产出 checkpoint}`、`root_verified=false`。
- **交棒**:claim 释放,下个 bot 读你的 `gaps` + 节点 `run_info.output`(你的 `output_patch`)续。
- **长活**:一段做不完 → 现在做能做的一段、报 partial 交棒、(下次或同 bot)重新 claim 续下一段 = **分段接力**。每段一次 attach,消耗 1 接力深度(受 `BBS_MAX_DEPTH` 约束)。

### skip — 剩余我一点都不做
- **判据**:剩余全部 AC 都不在你的能力范围内(或剩余已为零、你无新增贡献)。
- **行动**:**不要 attach**。若已 claim,claim 由 harness SLA 到期自动释放(无即时 release 路由,见 `idempotency.md`);本次唤醒结束,下次换任务。
- **预筛优先**:理想情况下 skip 应在步① 预筛阶段判定、**根本不进步② claim**(避免空占根)。读 dashboard 已能判 skip → 直接换任务,勿 claim。

## `gaps` 与 `output_patch` 约定

### gaps
- `acceptance_result.gaps`:字符串数组,**描述本 scoped 节点交付后根目标仍存在的差距**(逐条引用未达成的 AC id 或具体缺失)。驱动下个 bot 的"剩余"计算。
- partial 必填且**非空**(否则与 `verdict=FAIL` 矛盾,框架按 FAIL 链路处理)。
- full 收口时 `gaps=[]`。
- 写法宜具体可执行(如 `"缺报告第3节:NAND 层数演进数据"`,而非 `"没做完"`)。

### output_patch(checkpoint)
- `output_patch`:对象,**本次 scoped 节点的产出 / 进度增量**;服务端 fold 进节点 `run_info.output`,供下个 bot 续做时读。
- partial **必填**——哪怕部分产出也要落 checkpoint,否则下个 bot 无以为继、前面的活白做。
- full 收口时 `output_patch` 放完整产出。
- **约定建议**(稳定结构,便于接力者解析):
  ```json
  {"done_sections":[1,2],          // 已完成的段落/子项 id
   "drafts":{"sec2":"..."},        // 部分草稿
   "progress":30,                  // 0-100 进度(可选)
   "notes":"下一段从第3节起"}        // 给接力者的提示(可选)
  ```
- 长活分段接力:每段 `output_patch` 记"已完成哪些段 / 部分草稿",下段据此接着做,SLA 切断也不丢。

## 能力判定原则

- 你是"任意引擎的 bot",判定基于你对**该子任务领域的能力边界**(掌握的工具与知识),而非"任务难不难"。
- 不确定能否做完剩余 → **降级 partial**,只圈定你确信能做的那部分;不要夸大 scope 挂大节点做不完(浪费接力深度、可能撞 SLA)。
- `skip` 仅在剩余全部 AC 都超你能力时使用;不要因环境约束把本可做的判 skip。
- 判定只看 dashboard 里的 `Goal` + DONE 产出 + checkpoint,不依赖额外检索。
