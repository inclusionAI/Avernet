# OUTPUT.md

## 默认表达

- 对店主：先给决定、状态或需要他选择的事项，再给影响和下一步。
- 对 Worker：只给公开事实、当前版本、角色责任、校验要求和失效条件。
- 默认自然语言与紧凑业务卡，不输出大段 JSON、YAML、代码块或内部状态机说明。
- 数字必须附单位、口径、来源和有效期；事实、推断、建议和承诺要分开。
- 不输出 `NO_REPLY`、伪 task_id、无工具依据的“已派发/已执行/已完成”。

## 店主私聊：需求确认卡

```text
我已记录：<公开目标与优先级>
硬约束：<品质或经营边界；不展开私有数值>
我可自主决定：<授权范围>
启动协作前还需您决定：无|<真正的人类专属事项>
下一步：<发现完整团队并创建 manager-worker 群>
```

不要在这里预置店主尚未说出的预算、毛利或风险偏好。

## 店主私聊：启动前最小决策卡

仅在确有不可代理的人类决定时使用，一次合并问完：

```text
启动前需要您决定：<事项集合>
我的推荐：<一个参数包或选项>；原因=<与目标的关系>
可选：A <互斥方案>；B <互斥方案>
影响：<会改变的公开条款与风险>
回复：选择 A/B；确认后 one-shot 运行中不再打断
```

不得把计算题、授权内选择、平台 owner 条款或执行时事实塞进此卡。

## 建群成功

旧私聊只输出工具响应中的原始 `chat_url` 一行，不用 Markdown 包裹，不追加说明。随后立即结束当前激活。

## Worker 定向任务

```text
任务：复核 <contract_version> 的 <角色范围>
公开事实：<来源明确的最小事实集>
需要你确认：<owner 条款、公式、授权包络与失效条件>
计划级通过规则：执行前置条件/监控项可保留；硬阻断或未决 owner 承诺不可保留
输出：不要 JSON、代码块或启动确认；把以下五行放在 final text
结论/版本：通过|需修订|阻断；contract_version=<版本>
方案：<关键结果>
校验：<来源、公式、有效期、授权包络>
阻断项：无|<HARD_BLOCKER 或 MANAGER_DECISION>
交接：<最终复核项>；依赖=<字段>；失效条件=<字段>；执行前置=<可选>；监控=<可选>
```

## Manager 完整修订包

状态机每次修订输出完整公开候选，不只输出 patch：

```text
REVISION_PACKAGE
contract_version=<vN>
revision_digest=<digest>
公开目标与周期：<...>
营销条款：<...>
需求与容量：<...>
供应与 Plan A/B：<...>
品质门禁：<...>
执行前置条件：<owner/观测/通过条件/失败动作/最迟时间>
监控项：<指标/口径/频率/触发线/动作>
本版已关闭问题：<issue_id + 处理>
剩余硬阻断：无|<issue_id>
下一轮需复核：<按角色列出>
PRIVATE_FINANCIAL_CHECK=PASS|FAIL
```

禁止出现私有阈值、成本、预算、毛利、余额、差额或其推导过程。

## Manager 检查向量

汇总节点只陈述事实，不自写 judge outcome：

```text
CHECK_VECTOR
checked_version=<vN>
revision_digest=<digest>
MARKETING_CHECK=PASS|FAIL
DATA_CHECK=PASS|FAIL
SUPPLY_CHECK=PASS|FAIL
PRIVATE_FINANCIAL_CHECK=PASS|FAIL
HARD_BLOCKERS=<无|issue ids>
MANAGER_DECISIONS=<无|issue ids>
EXECUTION_PRECONDITIONS=<列表>
MONITORING_ITEMS=<列表>
PROGRESS_CHECK=PASS|NO_PROGRESS
ISSUE_LEDGER=<每项类别、owner、状态、下一动作>
```

Worker 的版本或 digest 不一致时，对应检查为 FAIL。完整执行前置条件和监控项不使计划级检查失败。

## one-shot 启动状态

`collaborate run` 返回非空 run ID 后只输出：

```text
一次性协作已启动：<run_id>。专业节点将先完整运行；全部就绪后会在本群请求店主最终验收。
```

随后结束当前回复，不再调用任何工具。

## 最终 HumanInput

只有最终就绪检查通过后才能出现：

```text
请验收公开方案 <contract_version>：<一句话摘要>
专业检查：营销 PASS｜数据 PASS｜供应链 PASS｜私有财务 PASS
执行前置条件：<列表；无则写无>
监控项：<列表；无则写无>
请选择：接受当前版本 / 要求修改并说明修改点
```

不得在这里请店主补证、替 Worker 放行或接受未解决风险。

## 唯一 final output

成功路径：

```text
DELIVERY_DECISION=ACCEPTED
公开版本：<contract_version>
最终方案：<营销、数据、供应与品质关键条款>
专业检查：营销 PASS｜数据 PASS｜供应链 PASS｜私有财务 PASS
待外部执行：<pending_external_actions；可为空数组>
交付状态：SOP_ACCEPTED_PENDING_EXTERNAL_EXECUTION
```

阻断路径：

```text
DELIVERY_DECISION=BLOCKED
最后检查版本：<contract_version>
已完成：<真实完成项>
剩余阻断：<issue_id、类别、责任方、原因>
下一次运行前最小输入：<事实或决定；没有则写无>
不得执行：<被门禁拦截的动作>
```

阻断路径禁止出现“全部通过”“已接受”“可执行”或“缺口为零”。

## `bcs_task_complete.summary`

仅在成功证据齐全后提交，键集合必须恰为：

```json
{
  "public_contract_version": "vN",
  "run_id": "<run_id>",
  "delivery_status": "SOP_ACCEPTED_PENDING_EXTERNAL_EXECUTION",
  "pending_external_actions": []
}
```

不得添加解释字段或私有信息。真实外部执行尚未发生时，不能把状态改成已投放、已采购或已监控。
