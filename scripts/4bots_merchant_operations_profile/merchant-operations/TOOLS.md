# TOOLS.md

使用 BCS manager-worker 或一次性协作前，必须完整读取本文件。

## 1. 私有账本与共享 brief

每个新任务先完整读取 `KNOWLEDGE.md`，建立 `.merchant-private/tasks/<task_ref>.json`，记录目标、店主决定/冻结状态、私有字段/字面值、授权、知识来源/有效期、Worker 回执、owner 包络、候选版本/digest、issue 及 privacy/schema/run/completion evidence。

`shared_brief` 白名单只有：`task_ref`、公开目标/周期/对象、非敏感经营事实、品质要求和角色问题。禁止财务阈值、预算/现金上限、成本、内部余量、授权阈值、私聊原话和精确推导结果。

## 2. 发现与创建 manager-worker 群

1. 经营活动默认 `required_workers=[平台营销方案,平台数据分析,平台供应链]`。
2. 逐个发现并核对准确 Bot ID 与可协作状态。发现只用于拿 ID，不调用 chat/invoke 获取业务答案。
3. 三者齐全后，对最终 `shared_brief` 做字面与语义隐私扫描，记录：
   - `matched_private_literals=[]`
   - `semantic_private_fields=[]`
   - `privacy_preflight=PASS`
4. 下一条协作命令必须是店长为 manager、三者为 participants 的一次 `bcs-cli --json create-group --manager ... --context <shared_brief>`。不得 add-member，也不得先建部分群。
5. 从结构化响应机械提取 `chat_url` 与 `session_id`。URL decode 后验证 `id/session/bot_uuid` 与响应完全一致。
6. 在旧私聊只逐字输出原始 `chat_url`，随后结束激活。不得从旧私聊对新 session 做任何后续调用。

## 3. 新群初始化与派发

在新 manager-worker session 中：

1. 再次完整读取 `KNOWLEDGE.md`。
2. 验证 `group_type=manager_worker`、自己是 manager、roster 覆盖三名 required Worker、context 中有 `task_ref/shared_brief`。
3. 按 `task_ref` 读取本地私有账本；不得要求店主重述任务。
4. 为三个角色形成完整公开事实包。知识里已有的门店价格、服务分钟、库存等事实必须主动带上。
5. 对每份最终 payload 做委派前隐私扫描，再调用原生 `bcs_assign_task`。只有 `ok=true`、非空 `task_id` 且目标匹配，才登记为 `DISPATCHED`。

每份任务要求 final text 中输出五行业务卡：

```text
结论/版本：通过|需修订|阻断；contract_version=<版本>
方案：<本角色 owner 承诺或关键结果>
校验：<公式、来源、有效期、授权包络>
阻断项：无|<HARD_BLOCKER 或 MANAGER_DECISION>
交接：<复核项>；依赖=<公开字段>；失效条件=<字段>；执行前置=<可选>；监控=<可选>
```

明确要求不要 JSON、代码块、启动确认、长报告或 `NO_REPLY`。

## 4. 首轮回执验收

业务卡只有同时满足以下条件才有效：

- final text 可见且五行齐全；
- 结论属于允许集合并回显当前版本；
- 数据、单位、公式、来源、有效期和 owner 边界可核验；
- “通过”的阻断项为“无”，正文没有相反 blocker；
- 执行前置条件与监控项具备完整字段，而不是含混“待确认”。

空回复、`NO_REPLY`、只有启动确认/思考或结论冲突，登记 `INVALID_WORKER_OUTPUT`，重派一次只含原始事实、版本和模板的最短任务；仍无效才记 `WORKER_OUTPUT_BLOCKED`。

三张首轮有效卡一到齐，立即设置 `manual_dispatch_closed=true`，停止定向任务手工多轮。把各卡问题归入：`HARD_BLOCKER`、`MANAGER_DECISION`、`EXECUTION_PRECONDITION`、`MONITORING_ITEM`。

## 5. run 前收口

店长在本地完成：

- 营销结算恒等式；
- 活动库存桥接与 MOQ；
- 服务分钟、技能/工位与履约窗口；
- 私有财务，公开侧只保留 `PRIVATE_FINANCIAL_CHECK=PASS|FAIL`。

随后形成完整 `PUBLIC_CANDIDATE v1`，并按自治决策阶梯关闭所有可以自行处理的 `MANAGER_DECISION`。

若还有人类专属事项，one-shot 前一次发出 `PRE_RUN_OWNER_DECISION_CARD`：

```text
启动前需要您一次决定：<事项集合>
推荐：<推荐参数包及原因>
选项：A ...；B ...（互斥）
影响：<条款和风险>
回复方式：选择 A/B；回答后本次运行中不再打断
```

只有回答已映射到明确事项后才能设 `OWNER_DECISIONS_FROZEN=true`。模糊的“继续/执行”不能凭空扩展成风险接受。

`ONE_SHOT_INPUT_READY` 必须同时满足：三张有效业务卡、完整公开候选、店主专属事项为零、决定已冻结、四类本地校验已出结果、issue 已分类、隐私扫描通过。“需修订”可进入 initial issues；缺有效卡或店主决定不能进入。

## 6. permission、schema 与候选定义

1. 执行 `bcs-cli --json collaborate permission --session <session_id>`。只认 `allowed=true` 且 caller 是当前 manager；不得隐藏错误或伪造 fallback。
2. permission 通过后、写 YAML 前，使用文件读取能力完整读取当前安装的：
   - `skills/bcs-coordination/SKILL.md`
   - `skills/bcs-coordination/references/custom-collaboration.md`
   - `skills/bcs-coordination/references/custom-collaboration-schema.md`
3. 读到 schema 末尾后记录 `schema_read_receipt`。任一文件不可用则以 `SCHEMA_REFERENCE_UNAVAILABLE` 阻断，不猜字段。
4. 根据本轮 Worker 的交接与 issue ledger 动态生成唯一 YAML；不得复制固定定义。Bot participant slot 只含 manager、营销、数据、供应链。人类不是 participant，不绑定 Bot ID。

## 7. 动态状态机的业务结构

按当次 schema 表达以下无环结构：

1. `manager_revision_v1`：基于公开 initial input 形成完整 `REVISION_PACKAGE v1`；
2. 三名 `worker_review_v1`：针对 v1 与 digest 独立复核；
3. `manager_check_v1`：汇总同版 CHECK_VECTOR 与 issue ledger；通过则去最终就绪，需修订则去 v2；
4. `manager_revision_v2`：消费 v1 的三张卡和 issue ledger，应用自治决策阶梯，输出完整 v2；
5. 三名 `worker_review_v2` 与 `manager_check_v2`；通过则去最终就绪，需修订则去 v3；
6. `manager_revision_v3`、三名 `worker_review_v3`、`manager_check_v3`；
7. `final_readiness_check`：四项同版本 PASS、owner 来源齐全、无硬阻断/管理决定/店主决定时 `ready`，否则 `blocked`；
8. `ready` 才进入唯一的 `human_final_acceptance`；接受进入 `accepted_marker`，要求修改进入 `blocked_marker`；
9. 专业检查阻断也进入同一个 `blocked_marker`；两个 marker 汇入唯一 final output。

schema 若支持，可把最终就绪判据并入第三轮 judge，但不得弱化判据和路径。

### Manager 修订节点必须做什么

- 输出完整公开契约，而不是只写 patch；
- 递增 `contract_version`；
- 对每个 issue 选择：关闭、转成完整执行前置条件、转成完整监控项、或保留为硬阻断；
- 在 owner 包络内自行选择保守参数，不写“待店主确认”；
- 输出 `revision_digest`、`closed_issues`、`remaining_issues`、`pending_external_actions`；
- 不读取私有账本、不调用工具、不暴露私有值。

### 防止原地重复

每次 Manager check 比较当前与上一版：

- `contract_version` 必须增加；
- `revision_digest` 必须变化；
- 至少一个 issue 被关闭、降级为完整前置条件/监控项，或候选字段发生能回应 issue 的实质改变。

任一不满足即产生 `NO_PROGRESS`，不再消耗下一轮，直接进入 blocked path。Worker 不得只重复“没有新输入”；它必须审查直接上游完整 revision package。

### PASS 与路由

- 营销、数据、供应链、私有财务四项必须针对同一版本精确 PASS。
- `EXECUTION_PRECONDITION` 和 `MONITORING_ITEM` 不使计划级 PASS 失败，只要字段完整并进入 `pending_external_actions`。
- `HARD_BLOCKER`、`MANAGER_DECISION`、未决店主事项、版本/digest 不匹配、owner 来源缺失均不得进入最终人类验收。
- 汇总 artifact 只输出事实 CHECK_VECTOR；裁决由同节点 judge 产生，不能一份 artifact 同时自写裁决。

### HumanInput 约束

状态机中唯一 HumanInput 必须位于 `final_readiness=ready` 之后。节点：

- 不出现在 participants 或 bindings；
- 不写 Bot assignee；
- 超时不低于 600000ms；
- 只接受 `accepted/changes_requested` 两个 outcome；
- 问题明确包含待验收 `contract_version` 和公开摘要。

HumanInput 不用于补数；专业检查未通过时直接 BLOCKED。它是 demo 的强制节点：保持 `kind: human_input` 且无 Bot assignee；禁止删除、改成 `bot_task`、manager 代答或让 ready 直达 accepted marker。

## 8. shape、超时与隐私预检

具体字段以当次 schema 为准，同时强制验证：

- 顶层仅使用 schema 允许字段；节点均在 state machine graph 内；
- 图无环，恰有一个入口和一个 `final_output: true` 的 sink；
- 所有 bot_task 有效超时不低于 300000ms，HumanInput 不低于 600000ms；
- 无未定义节点、占位文本、多个 final output、额外 finalizer 或 Human Bot binding；
- 每版三名 required Worker 都真实复核；
- 只有通过最终就绪检查才能进入 HumanInput；
- accepted/blocked marker 互斥并汇入唯一 final output；
- final output 复制直接上游 `DELIVERY_DECISION`，不能内置成功结论。

validate 前和 run 前都扫描完整 YAML/input，覆盖 metadata、instruction、judge criteria 和输入字段；文件变化即重扫。

执行 `bcs-cli --json collaborate validate <yaml_path>`。它返回的结构化 `valid/errors/warnings/graph` 是 schema 合法性与图结构的权威结果；必须保留真实退出码并解析字段，不能从自然语言摘要推断通过。validation error 不等于 CLI 不可用。最多按 schema 修复两轮；不得删除 Worker、修订轮、最终验收或唯一 final output 求通过。

profile 额外要求（例如超时下限、HumanInput 无 Bot assignee、participant 白名单）若需要本地复核，必须用 YAML 解析器读取准确字段路径：检查 `participants` 的 key、目标 node 对象的 `kind/assignee/node_timeout_ms` 等。禁止通过全文关键词、`split/index` 字符串切片或节点名称第一次出现的位置判断结构；instruction、description、transition target 中的普通文本不算结构字段。

任何本地复核出现 `FAIL` 时，不得同时输出 `ALL PASS`，也不得提交 run。先对照结构化 validate 结果和解析后的字段定位原因：若定义有错则修 YAML 并重新 validate；若检查器误报则修正或移除该检查器并重新执行，记录 `LOCAL_CHECK_IMPLEMENTATION_ERROR`。只有权威 validate 通过且所有保留的本地检查都真实通过，才能进入 run。

## 9. run 提交

run 前确认 session 仍 running、Bot roster/binding 准确，并从当前 roster/context 或入群系统事件确认 `actor_kind=human && mode=present`，记录 `PRESENT_HUMAN_PREFLIGHT=PASS`。不得靠猜测、旧账本或聊天文本认定。

没有 Present Human：保留 YAML/input，只提示入群并等待系统事件；不试跑、不删改 HumanInput。入群后文件未变可复用校验，否则重做 schema/隐私预检。

按候选定义中的 Bot participant slots 构造 bindings，执行 `bcs-cli --json collaborate run <yaml_path> --session <session_id> ... --input @<public_input.json>`。

仅非空 `run_id` 算成功。若返回 `requires a Present Human`，原样报告并等待，禁止改图绕过。`collaborate run` 必须是本次激活最后一次工具调用；成功后简短报告并结束，不再查 run、写账本、清理或发消息。

候选文件在 run 前必须无敏感；精确清理留到下一次独立激活。

## 10. 终态和完成

成功 marker 首行固定 `DELIVERY_DECISION=ACCEPTED`；阻断 marker 首行固定 `DELIVERY_DECISION=BLOCKED`。唯一 final output 基于 marker 输出：

- 当前公开版本和简短契约；
- 检查结果；
- `pending_external_actions`；
- 若阻断，剩余 issue、责任方和下一次 run 的最小输入。

不得把计划批准说成真实投放、采购、支付、到货或持续监控。

complete 前只认同一 run 的服务端证据：run completed、`kind=human_input` 由真实 human actor 回复且 accepted、accepted marker/final output completed、blocked marker 未执行。事后入群、bot_task、manager 自述或本地账本无效。summary 仅含 `public_contract_version`、`run_id`、`delivery_status=SOP_ACCEPTED_PENDING_EXTERNAL_EXECUTION`、数组 `pending_external_actions`。

任何字段缺失、阻断、失败、超时或要求修改，都不得调用 complete，也不得 terminate group/session。

只使用本手册列出的原生能力；禁止 add-member、chat/invoke、通用 subagents、`.bcs/session.json`、探测式 help/list、后台催动、关闭 session、隐藏错误和本地文档兜底。
