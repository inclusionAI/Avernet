---
agent: tc-review
status: superseded-by-004
created: 2026-08-20T00:00:00+08:00
iteration: 3
---

# 系分 Spec：Public Bot Catalog BCS 元信息端口

> 当前实现与发布约束以 [004-implementation-plan.md](004-implementation-plan.md) 为准。本文件只保留
> 本次已批准的端口设计；不得据此推断任何 BCS HTTP URL、路径、请求体、凭据、超时或网络调用。

## 需求概述

`GET /openapi/v1/bots/catalog/search` 保持 Backend 的公开 Bot 搜索语义。Backend 从当前租户的
公开候选构造有序、去重的 `(bot_id, entity_id)` 地址，并将这些地址、已验证的
`tenant_id` / `user_id` / `app_id` caller 投影及 request ID 交给 BCS 元信息端口决定成员资格。
Backend 是全部公开响应字段的唯一权威来源。

当前 production、local 和 test 均绑定为 unavailable 端口实现。因此 Catalog Search 固定返回
`502000 / Catalog service unavailable`，不会退化为 Backend-only 结果，也不会猜测或调用未实现的
BCS HTTP 接口。

## 编码 Spec

- [x] 在 Backend Service API 定义 frozen `BotCatalogAddress`、`BotCatalogCaller`、
  `BotCatalogMetadata`、runtime-checkable metadata protocol 及 unavailable error。
- [x] Catalog 专用 service 方法先读取完整、稳定排序的 Backend public candidate set，生成精确
  `(bot_id, entity_id)` 地址后才做内连接；`total` 与分页均基于完整 join 后结果。
- [x] 即使地址列表为空也调用 metadata protocol；当前 unavailable 实现仅记录 request ID、候选数
  与 `unconfigured` 类别后 fail closed。
- [x] 任何非 `kind == "bot"`、重复、未请求、无效或空白地址的 metadata 结果均 fail closed。
- [x] OpenAPI router 只从 verified principal 投影 caller；固定错误 envelope 为
  `502000 / Catalog service unavailable`。
- [x] Legacy `/api/v1/bot-public/search` 保持 Backend-only；Discover 保持既有 BCSFuse 行为与日志。

## 验收标准

- 任何未配置或不可用的 metadata port（包括空候选）均令 `/search` 返回固定 `502000` envelope。
- 未来配置端口时，只有 exact `(bot_id, entity_id)` metadata 命中的 Backend Bot 可以出现；同一
  `bot_id` 的不同 entity 不得混淆。
- 公开响应不含 metadata 原始字段，也不含 binding、设备、ext、token 或环境数据。
- 机器可读 OpenAPI 的 502 response 使用 `ErrorEnvelope`，示例固定为
  `502000 / Catalog service unavailable`。

## QA Spec

| 编号 | 用例 | 预期 |
|---|---|---|
| TC-01 | 空与非空地址调用 unavailable port | 均 fail closed，接口返回 `502000`。 |
| TC-02 | metadata 含非 Bot、重复、未知或空白地址 | fail closed，不返回部分结果。 |
| TC-03 | 同一 bot_id、不同 entity_id | 仅 exact address 可 join。 |
| TC-04 | 502 OpenAPI response | 有 `ErrorEnvelope` 与固定 `502000` 示例。 |

## Ship Spec

- 当前阶段不得配置或发布任何推测的 BCS HTTP integration。
- 若要启用 Catalog Search，先单独批准并实现 tenant/caller-scoped metadata protocol、DI binding、
  contract tests 和 deployment configuration；完成前保持当前 fixed-502 行为。
- 回滚只需恢复本功能改动；绝不能在线改为静默 Backend-only fallback。
