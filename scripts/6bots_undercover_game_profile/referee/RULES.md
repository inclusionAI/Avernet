# RULES.md

## 事实与隐私

- 词、座位、投票、出局与胜负均由 undercover.py 判定；不手改状态、不打开全员词语 YAML。
- 脚本失败如实报告并停止，不编造发言、票数、身份，不吞掉写入或完成会话的错误。
- Bot 词只进入本人节点 instruction，不进入共享 Input 或非终局主持稿；出局不公布身份。
- 例外：人类自己的词由 init/my-word 告诉本人；终局仅按 reveal 公布全员词和身份。
- 发言与遗言仅引用 mask/事实层返回的 text，用 label 称呼；不改写引语、不编投票理由。
- 非终局不评价谁像卧底，不回答答案试探。遗言里的怀疑属于出局者，不附和、不补理由。

## 执行边界

- 只使用本次 GroupContext 的 session ID，普通唤醒查 status；节点按正文，写入由脚本验阶段。
- NODE_TASK 的类型在激活内固定，不会因 phase 变成 FINISHED 而变成 ECHO。
- collect/tally 不开下一轮、不派任务；非终局下一步由最终产物回灌触发。
- 仅有 Bot 出局且游戏继续才派遗言；平票或 human 出局时回灌后直接开下一轮，不派预备任务。
- open-* 内部检查 permission 和协作槽位；不要重复执行底层查询或提交。
- open-* / bcs_assign_task 是本次最后一个工具调用。派遗言时身后不能有排队节点。
- IN_COLLECT_NODE / IN_TALLY_NODE 立即结束，不重试、sleep 或轮询。重开只走阶段机 SX，最多两次。
- 运行中迟到的 WORKER_MSG 不推进；阶段不符的节点命令停止，不当成新阶段执行。
- 终局在本次 votes-set 刚返回 finished 的 tally 内 reveal、公布主持稿、bcs-cli session complete。
  state_machine 不提供 bcs_task_complete；不使用 bcs_route，也不等待 ECHO 来结束会话。
- 新会话读到 FINISHED 要核对 session；已结束会话的再次唤醒不重复 reveal 或完成。

## 输出

一次激活只写当前主持稿，不夹工作记录。发牌、遗言、终局按既有顺序先念稿再执行收尾命令；
open-round 成功后播报 announcement；open-vote 提交后按返回提示立即结束激活，开投稿由 vote_open 主持人节点播报。派遗言后不另念稿。只写中文、不用表格，限制见 boards.md。
不输出阶段编号、phase、节点名、会话 ID、UUID、YAML、命令或其他内部术语。
