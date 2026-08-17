# 服务 Bot 已实现 OpenAPI 接口清单

本文记录 `yipu_refactor_v2` 分支已实现的服务 Bot 公开接口。详细字段、请求和响应示例以
[`service-bot-openapi-contract.zh-CN.md`](./service-bot-openapi-contract.zh-CN.md) 为准。

## 通用约定

- Base URL: `/openapi/v1/bots`
- `user_id`: 必填 Query，表示实际操作人。
- `owner_id`: 可选 Query，访问协作 Bot 时传 Bot Owner。
- 生命周期动作不接收 `publication_id`；后端在已授权 Bot 内选择当前可操作版本。
- 成功与失败均使用 OpenAPI v1 Envelope。异步动作成功受理返回 HTTP `202`。

## 接口总览

| # | Method | URL | 用途 |
| --- | --- | --- | --- |
| 1 | GET | `/all` | 统一 Bot 卡片列表；服务 Bot 最多展开两张版本卡片 |
| 2 | POST | `/{bot_id}/lifecycle/upgrade` | 将支持的云端个人 Bot 转为服务 Bot |
| 3 | GET | `/{bot_id}/lifecycle` | 查询最多两张可见生命周期卡片 |
| 4 | DELETE | `/{bot_id}/lifecycle` | 删除从未正式发布的初始草稿 Bot |
| 5 | GET | `/{bot_id}/lifecycle/approval` | 查询发布/下线审批开关 |
| 6 | PUT | `/{bot_id}/lifecycle/approval` | Owner 修改发布/下线审批开关 |
| 7 | POST | `/{bot_id}/lifecycle/advance` | 将当前版本推进到 `staging` 或 `online` |
| 8 | POST | `/{bot_id}/lifecycle/restart` | 重启 `staging` 或 `online` 运行时 |
| 9 | POST | `/{bot_id}/lifecycle/cancel-staging` | 销毁预发运行时并退回草稿 |
| 10 | POST | `/{bot_id}/lifecycle/offline` | 下线当前线上版本 |
| 11 | POST | `/{bot_id}/lifecycle/retry` | 重试最新失败的发布任务 |
| 12 | GET | `/{bot_id}/edit-lock` | 查询编辑锁 |
| 13 | POST | `/{bot_id}/edit-lock` | 获取编辑锁 |
| 14 | DELETE | `/{bot_id}/edit-lock` | 释放自己持有的编辑锁 |
| 15 | POST | `/{bot_id}/edit-lock/steal` | Owner/Admin 抢占编辑锁 |

## 关键语义

- 稳定产品状态：`draft/deploying/staging/running/offline`。
- 服务 Bot 卡片以 `card_id=service:{bot_id}:{publication_id}` 作为稳定标识。
- 同一 Bot 最多展示两张卡；`upgraded` 不展示，多个 `released` 只保留最新一个。
- 存在 `success/upgraded/released` 历史时不允许删除 Bot。
- 无权限与 Bot 不存在统一返回 `404`，避免通过错误差异枚举 Bot。

## 兼容性

本次不删除存量 `/api` 内部接口。新 OpenAPI 是新增的 bot-first 公开面；未调用新接口的
现有前端和调用方行为不变。
