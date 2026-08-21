# PR Report — OpenAPI Session File 最小接线

## Problem

Session File 需要提供 OpenAPI 生命周期能力，但不能改变原前端
`/api/session-resources/**` 的调用链，也不能在 router 重复 binding/target 业务逻辑。

## Solution

- 新增六个 bot-first Session File OpenAPI operation；
- upload intent 由 adapter 调用统一 resolver 取得 `binding_id`，随后复用原 Service；
- 其他操作仅委托原 Service；
- legacy 冻结 URL 不暴露新 files operation；`/files/pending` 不公开。

## Validation

- Backend 聚焦、旧链路回归、架构/coverage、Gateway schema/security/served-schema 均通过；
- OpenAPI compatibility gate 成功发布 candidate schema；
- 已确认生成 schema 包含六个 files path、不含 `/files/pending`。

## Compatibility and risk

旧前端路由及 Service/Repository/SQL/物化调用均无语义修改。公开面只增 bot-first
OpenAPI operation；generated Gateway schema 的文本 diff 包含稳定键排序变化，但 gate
确认对已发布 contract 向后兼容。

## Spec

- `014-minimal-openapi-binding-wiring.md`
- `002-code-report.md`
