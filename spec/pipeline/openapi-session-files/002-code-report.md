# OpenAPI Session File 最小接线实现记录

## 实现

- 在既有 Sessions router 平放六个 OpenAPI Session File 操作；
- upload intent 经 `OpenApiSessionFileAdapter` 解析一次 `binding_id`，再调用原
  `SessionResourceService.create_upload_intent`；
- 其余五个操作直接委托原 Session Resource Service；
- 新增只读统一 binding resolver，并在 EngineRuntimeModule 注册；
- 旧 `/api/session-resources/**`、Session Resource Service/Repository/SQL/物化流程未修改；
- `/files/pending` 不在公开 OpenAPI；legacy relocate 仅排除新 files URL，不影响旧前端地址。

## 验证

- Router/adapter/resolver：62 passed；
- OpenAPI endpoint 与旧 session-resource 回归：45 passed；
- 架构与 coverage gate：69 passed；
- Gateway schema/security/served schema：69 passed；
- compileall、`git diff --check` 通过。
