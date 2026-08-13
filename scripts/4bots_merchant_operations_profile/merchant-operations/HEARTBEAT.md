# HEARTBEAT.md

每次状态变化前检查：

## 上下文与隐私

- 是否已完整读取 KNOWLEDGE，并使用了仍有效的门店事实？
- 当前 session 是原店主私聊、新 manager-worker 群还是状态机节点？建群后是否错误从旧私聊遥控新群？
- 最终外发参数是否通过 `matched_private_literals=[]` 与 `semantic_private_fields=[]`？
- 是否泄露成本、底线、预算/现金上限、内部余量、私聊原话或可反推结果？

## 团队与回执

- 首次群是否包含营销、数据、供应链三名 Worker？是否误用 add-member、chat/invoke 或多个 1:1？
- 每个派发是否有真实 `ok=true`、非空 task ID 与正确 session？
- 五行业务卡是否 final text 可见、版本一致、结论与正文一致？无效输出是否只格式重试一次？
- 三张有效首轮卡齐全后，`manual_dispatch_closed` 是否为 true？是否仍在 run 外反复追问 Worker？

## 人类参与与自治

- 当前缺口是否真的只能由店主决定？还是可以计算、在 owner 包络内保守选择、转执行前置条件或监控项？
- 所有店主专属事项是否在 run 前合并问完并冻结？
- one-shot 运行中是否错误插入 HumanInput、私聊或普通群提问？
- 最终 HumanInput 前是否已满足四项同版 PASS、无硬阻断和无管理决定？

## 数值与问题分类

- 营销结算、库存桥接、MOQ、服务分钟、技能/工位和私有财务是否独立复算？
- 是否把在途重复加总、把 MOQ 当包装倍数、把技能池和整体产能相加，或混用领取/核销/到店口径？
- 每个 issue 是否属于 HARD_BLOCKER、MANAGER_DECISION、EXECUTION_PRECONDITION 或 MONITORING_ITEM？
- 通过方案里的前置条件是否有 owner/观测/通过条件/失败动作/时间，监控是否有指标/频率/触发线/动作？

## one-shot 进展

- YAML 是否基于本次完整读取的 Skill/schema 动态生成？超时是否 bot_task ≥300000ms、HumanInput ≥600000ms？
- 每轮是否形成完整新版本并改变 digest，至少关闭或实质改变一个 issue？相同版本/digest 是否被 `NO_PROGRESS` 阻断？
- Worker 是否读取直接上游 revision package，而不是重复审查静态 initial input？
- 汇总是否只输出 CHECK_VECTOR，由 judge 路由？最后一轮未就绪时是否直接 BLOCKED，而不是让人类兜底？
- `collaborate run` 是否是启动激活的最后一次工具调用？

## 交付与生命周期

- final output 是否只有一个，并逐字继承 `DELIVERY_DECISION=ACCEPTED|BLOCKED`？
- 是否把前置条件和监控项如实列为 pending external actions，而没有声称已执行？
- complete 前是否有同一 run 的 HumanInput accepted、accepted marker、final output 和 terminal completed 证据？
- 是否出现 `terminate-group`、CLI `task complete` 或在失败/阻断时关闭 session？任一出现立即停止。

发现问题时先停止扩大风险，按自治决策阶梯处理；只有下一次 run 启动前的人类专属事项才交店主决定。
