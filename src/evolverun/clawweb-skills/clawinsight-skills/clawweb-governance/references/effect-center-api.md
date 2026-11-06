# 治理接口边界

服务地址来自受信任的独立部署配置。以下为已有后端接口，不是模型的工具白名单。

| 能力 | 接口 | 约束 |
|---|---|---|
| 历史项查询 | GET `/api/insight/v1/internal/governance/actions` | 精确Owner+Bot、分页、失败不当空历史 |
| 驳回反馈 | GET `/api/insight/v1/internal/governance/rejections/all` | 仅在部署允许时读取，并保留Owner、Bot与时间范围 |
| 创建待审批项 | POST `/api/insight/v1/internal/governance/actions` | 固定幂等键、冻结请求、提交前再去重 |

创建请求字段：ownerUserId、sourceOwnerUserId、botId、title、sourceRuleId、actionType、
assignmentReason、rootCauseSummary、suggestedAction、userGuidance、selectedTasks。
selectedTasks中的sessionId与taskIndex必须来自真实证据和目标环境的任务索引。

当前程序固定ASSIGN_OWNER，创建结果要求PENDING_ADMIN/PENDING，不接受隐式自动审批。
DIRECT_EVOLUTION命中已有Owner授权时可能自动执行，因此不能把它当成必定待人工审批的等价替代。

首次创建通常201，精确幂等重放200；同key不同内容可能409。
网络结果不明时先读取回执/已有记录；不得生成新key盲目重试。
只写了请求文件或dry-run通过，不等于服务端接受创建。

审批、用户通知、启动修复、mark-handled、停用Cron、规则发布不属于本治理任务。
认证及内部访问限制以实际部署契约为准，不伪造用户头、不复制登录Cookie、不绕过受保护接口。
