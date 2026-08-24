---
agent: tc-browser-interface-test
status: passed
created: 2026-08-23T00:00:00+08:00
---

# BCS `/bots/search` 预发认证态 QA

## 范围

- 目标：预发 BCS `GET /bots/search`。
- 请求参数：`offset=0`、`limit=20`、`tc_bot=true`。
- 方法：在 `bb-browser` 的现有认证会话中直接导航请求；Cookie 仅留在浏览器内存，未读取、复制、记录或转交。

## 结果

| 检查 | 结果 | 证据 |
|---|---|---|
| HTTP 请求 | PASS | HTTP 200。 |
| BCS Search 信封 | PASS | 存在 `items`、`total`、`offset`、`limit`。 |
| 当前页数量 | PASS | 返回 20 条（请求上限为 20）。 |
| 匹配总数 | PASS | `total` 为 34。 |
| 登录拦截 | PASS | 未检测到登录拦截信号。 |

## 结论

`/bots/search` 在浏览器认证态下可正常返回 BCS Search 契约。此前未认证直连收到的访问控制响应不代表 BCS Search 空结果。Bot 明细、Cookie、会话标识和响应原文均未读取或记录。
