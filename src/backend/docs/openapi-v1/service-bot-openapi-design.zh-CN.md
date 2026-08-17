# 服务 Bot OpenAPI 实现设计

## 范围与边界

本模块实现服务 Bot 生命周期、审批配置和编辑锁，并通过 `ServiceLifecyclePort` 接入
rongzhi 的统一 Bot 列表。Flow、Channel、Skill Set、文件和空间仍复用各自 owner 的能力。

## 分层

```text
OpenAPI Router
  -> ServicePublicationFacadeProtocol
    -> Bot/Collaborator/Lock services
    -> BotPublishRepositoryProtocol
    -> BotPublish/PublishFlow/PublishApproval services

BotInventoryService
  -> ServiceLifecyclePort
    -> BotPublishRepositoryProtocol.list_by_source_bots(...)
```

Router 只负责参数、DTO 和 Envelope。Facade 负责权限、状态选择和领域编排；统一列表通过
批量 publication 查询展开多版本卡片，避免 N+1。

## 安全边界

1. 先按 `(owner_id, bot_id)` 查询 Bot，再用真实 Bot Owner 校验调用人权限。
2. 无权限与不存在统一返回 404，避免枚举 Bot。
3. 生命周期动作不接受外部 `publication_id`，而是在已授权 Bot 和当前环境内选择最新合法状态记录。
4. 发布详情内部仍校验 `source_bot_pk/source_bot_id/env`，publication ID 不是授权依据。
5. 失败信息、设备 ID、工作流 ID、内部主机和原始上游响应不进入公开响应。
6. 审批配置只允许 Owner 写严格布尔值。
7. 抢锁保持原能力的 Owner/Admin 门槛。

## 状态与卡片

| 存储状态 | 生命周期详情状态 | 统一列表展示态 |
| --- | --- | --- |
| `draft` | `draft` | `service_draft` |
| `building/built/validate_pub/online_pub/failed` | `deploying` | `service_staging` |
| `validating` | `staging` | `service_staging` |
| `success` | `running` | `service_online` |
| `released` | `offline` | `service_offline` |
| `upgraded` | 不展示 | 不展示 |

同一 Bot 最多展示两个 publication；多个 `released` 仅保留最新一个。稳定卡片身份是
`service:{bot_id}:{publication_id}`。

## 生命周期约束

- `draft -> staging`：调用 `PublishFlowService.process()`；有协作者时要求持锁。
- `staging -> running`：检查审批，未要求审批时继续发布。
- `staging -> draft`：取消预发，状态回退并登记 VERIFY 运行时销毁任务。
- `running -> offline`：检查审批；下线后沿用现有逻辑创建下一版草稿。
- `failed -> retry`：复用现有持久发布任务重试。
- `staging/running -> restart`：复用现有 crash-safe 重启任务。

转服务 Bot 复用统一组合策略，仅允许 `openclaw/claude_code/teclaw`；本地、
`aicoding/hermes/moltis` 不可服务化。

## 删除与兼容

`can_delete_bot()` 检查同一 Bot 的全部历史。存在 `success/upgraded/released` 时禁止删除，
避免下线后新草稿反向删除整个服务 Bot。

旧内部 `/api` 接口不删除。新 OpenAPI 使用 `/{bot_id}/<component>` 的 bot-first 公开包装，并包含两项领域修正：取消预发销毁
VERIFY 运行时、正式发布历史永久阻止删除。
