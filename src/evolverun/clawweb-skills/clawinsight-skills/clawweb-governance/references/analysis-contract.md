# 治理分析协议 v1

## 输入字段

`bucket`包含Owner、Bot、分类、Cron通道和数量；`as_of`是证据数据日期。
`tasks`包含真实`id/session_id/task_index`、来源、消息范围内的证据和`signature_ids`。
`signatures`是程序从真实工具返回中抽取的有限集合，不能由模型新增。
`existing_actions`是已读取的同Owner+Bot历史项；`warnings`和`truncated`表示证据缺口。
`current_config`可能为空；为空时不能声称已经核对当前配置。

## 唯一输出

```json
{
  "decision": "WATCH",
  "reason": "当前缺少足以支持治理的独立证据，继续观察。",
  "signature_id": "",
  "evidence_ids": [],
  "title": "",
  "root_cause": "",
  "suggested_action": "",
  "assignment_reason": "",
  "existing_improvement_id": null
}
```

所有字段必需，不增加分数、状态、接口地址、执行命令或权限字段。
CREATE必须选取已有signature_id，并引用至少两个独立Session的同签名未完成Task。
标题不超过180字，根因和指派原因各不超过700字，方案不超过2200字。
这些是请求上限内的分析约束，不是对“置信度”打分。

如果用已有改进项去重，decision为DROP，existing_improvement_id必须是输入中的真实ID。
WATCH/DROP可以留空签名和方案；说明原因但不虚构证据。

## 程序与模型的边界

程序验证身份、索引、签名、实际数量、当前数据日、较新成功反证和已有项。
不合格提案可以进入观察或报结构错误，不能直接写入效果中心。
去重状态应区分分析过、提交过、审批过。观察缓存和驳回冷却的具体时长属于程序配置；
未实现或未提供该状态时不能由模型宣称已经执行。

正式运行提交通过门禁的CREATE，收到待审批回执后结束；没有进程内等待用户回复的阶段。
