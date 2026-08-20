---
agent: tc-review
status: completed
created: 2026-08-20T00:00:00+08:00
iteration: 2
---

# 系分 Spec: Public Bot Catalog BCSFuse 元信息内连接

## 需求概述

`GET /openapi/v1/bots/catalog/search` 保持 Backend 的公开 Bot 搜索语义，使用每个候选的
`(bot_id, owner_id)` 生成 worker ID，批量查询 BCSFuse 的 Bot 元信息。仅当 BCSFuse 返回同一
worker ID 时该 Backend Bot 才能返回给前端；所有公开响应字段仍由 Backend 白名单投影生成。

BCSFuse 的 batch 合约需要已有 worker ID，因此实现顺序是 Backend 产生当前租户的候选 key，
再批量查询 BCSFuse，最后才向调用方返回 join 结果。这不是 Backend-only fallback。

## 编码 Spec

- [x] `BotPublicService.search_public_bots_by_keyword` 先读取完整、稳定排序的 Backend public
  candidate set；禁止先分页后 join。
- [x] 以 Backend 记录生成唯一 `{bot_id}:{owner_id}`，每批最多 100 个调用
  `POST /v1/workers/batch`，仅承认本批请求中出现在 `data` 的 key。
- [x] 仅保留 BCSFuse 和 Backend 都存在的记录，在完成交集后计算 `total`、再应用页面切片。
- [x] BCSFuse 非 2xx、超时、坏 JSON 或不成功响应 fail closed，OpenAPI 返回 `502000`，不返回
  Backend 单边数据。
- [x] BCSFuse 响应和 worker ID 不写日志、不透传。日志只含候选数、join 数、页大小和失败类别。
- [x] 对 BCSFuse 使用独立的、配置 base URL 的 `HttpClient` qualifier，5 秒单批超时；测试环境
  使用无网络的 LocalHttpClient。

## 验收标准

- Backend-only 和 BCSFuse-only Bot 均不出现在 `items`；join key 是完整 `(bot_id, owner_id)`。
- 大于 100 个候选时按批查询，输出仍保持 Backend 排序；分页和 `total` 均针对完整交集。
- BCSFuse 故障返回固定 `502000`，空成功结果返回 `200000` 的空列表。
- 公开响应不含 BCSFuse 原始字段，也不含 binding、设备、ext、token 或环境数据。

## QA Spec

| 编号 | 用例 | 预期 |
|---|---|---|
| TC-01 | 101 个 Backend 候选，BCSFuse 两批均确认 | 两个 batch 请求；第 2 页只有第 101 个 Bot；`total=101`。 |
| TC-02 | BCSFuse 返回额外 worker ID | 额外 key 不会出现在前端结果。 |
| TC-03 | BCSFuse 调用异常 | `/search` 返回 `502000`，不降级为 Backend-only。 |
| TC-04 | 多个 owner 使用同一 bot_id | 仅完整 `{bot_id}:{owner_id}` 匹配的记录返回。 |

## Ship Spec

- 分支：`feat/openapi-bot-public-catalog`
- 发布前确认目标环境的 `bcsfuse.base_url` 已配置并可访问 `/v1/workers/batch`。
- 如需回滚，回滚本功能提交可恢复原 Backend-only 目录搜索；不要在线改为静默降级。
