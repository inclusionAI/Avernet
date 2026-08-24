---
agent: tc-browser-interface-test
status: failed
created: 2026-08-23T00:00:00+08:00
---

# BCS `/bots/search` 预发连通性 QA

## 范围

- 目标：预发 BCS 的 `GET /bots/search`。
- 请求：使用 Backend adapter 同样的无认证调用语义与 `offset`、`limit`、`tc_bot=true` 参数；不读取、记录或输出 Bot 条目和响应内容。
- 浏览器路径：当前 Codex 运行环境未提供 `bb-browser` 自动化能力，故未声明为已认证浏览器验收。

## 结果

| 检查 | 结果 | 说明 |
|---|---|---|
| 网络/HTTPS 可达 | PASS | 收到 HTTP 200。 |
| BCS Search 响应契约 | FAIL | 顶层不是预期的 `items`、`total`、`offset`、`limit` 响应信封。 |
| Catalog Search 可用性 | FAIL | 当前 adapter 会将该响应判定为无效上游响应并 fail closed，OpenAPI 映射为 `502000`。 |

## 结论

在当前预发域名和 Backend 现有调用语义下，`/bots/search` 尚未以 Backend 所需的 BCS Search 契约返回结果。需要由 BCS/网关确认该域名对应的路由、访问策略或目标部署后，再在已认证 Gateway 浏览器上下文复测；本次未修改业务代码、认证配置或部署。
