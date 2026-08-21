# OpenAPI Session File 最小 Binding 接线

## 范围

在 `dev_refactory_collaboration` 当前 Sessions OpenAPI surface 上新增六个文件操作：
upload intent、upload complete、单文件状态、ready list、content、delete。

调用链固定为：

```text
OpenAPI sessions/files router
  -> OpenApiSessionFileAdapter
       -> RuntimeBindingResolutionService.resolve(...).binding_id
       -> existing SessionResourceService.create_upload_intent(..., binding_id)
```

只有 upload intent 解析 binding；其他五个操作直接委托原 Session Resource Service。
`/files/pending` 不开放 OpenAPI，旧 `/api/session-resources/**` 保持不变。

## 禁止变更

- 不修改旧 Session Resource router/service/repository/SQL/materialization；
- 不引入 session affinity、runtime scope、device UUID 或第二套 target resolver；
- 不为新 files endpoint 生成 frozen legacy URL。

## 验收

1. 公开 schema 只新增六个 `/sessions/{session_id}/files` operation，且不含
   `/files/pending`。
2. upload intent 的 binding 由统一 resolver 得到并传给原
   `SessionResourceService.create_upload_intent`。
3. 旧资源链路回归、OpenAPI/Gateway contract、Ruff、compileall、diff 检查通过。
