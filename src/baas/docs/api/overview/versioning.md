# 版本化策略

## 路径前缀

API 使用以下路径前缀体系：

| 前缀 | 用途 | 认证 |
|------|------|------|
| `/openapi/v1/*` | 对外开放 API（Bot 交互） | Bearer API Key |
| `/api/v1/*` | 内部管理 API | Buservice Cookie |
| `/internal/v1/*` | 内部服务间调用 | MOSN 网格（无应用层认证） |
| `/bcn/*` | BCN 下行链路 | Pre-shared secret |

## 版本策略

- 当前版本为 `v1`，路径中显式包含版本号（如 `/api/v1/bots`）。
- 向后不兼容的变更将触发新的 major 版本（如 `v2`），旧版本在过渡期内保持可用。
- 向后兼容的变更（新增字段、新增端点）不增加版本号。

## 环境

| 环境 | URL |
|------|-----|
| 生产环境 | `https://secbaas.alipay.com` |
| 预发环境（staging） | `https://secbaas-pre.alipay.com` |