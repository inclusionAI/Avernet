# OpenAPI v1 resources：目录打包下载（download-dir）

> 2026-08-31 · 分支 `feat/backend-openapi-v1-resources-download-dir` · 目标合入 `REL20260901`

## Problem

公开 API（`/openapi/v1/bots/{bot_id}/resources`）只有单文件下载
（`GET .../download` 返回 octet-stream）。文件夹下载能力只存在于 console 内部 API
（`GET /api/resources/files/download-dir`，zip 打包），公开面上没有等价物——调用方要
导出一个目录只能逐文件 list + download。

## Goals

- G1：`GET /openapi/v1/bots/{bot_id}/resources/download-dir?path=` 返回
  `application/zip`，path 可省略（= 整个 workspace 根）。
- G2：走 `ResourceFileService`（openapi 的统一服务缝），不碰 console 那套直接操作
  device_fs 的实现。
- G3：限额在服务层强制：单文件 100MB / 5000 文件 / 总量 500MB——超限 413，消息固定
  （`DirectoryTooLargeError` → "Directory too large to download"），不复用写着
  preview 的旧映射。
- G4：失败语义对公开 API 诚实：目录不存在 404；空目录返回仅含根条目的合法 zip
  （与「不存在」区分开，console 此处返回 404 是有意不照搬的点）；中途读取失败整体
  中止——绝不返回 200 的残缺 zip。

## Behavior changes

| | 之前 | 之后 |
| --- | --- | --- |
| 目录下载 | 公开面不存在 | 新端点，见契约 |
| legacy 面 | — | **不**新增遗留地址：download-dir 是新操作，`deprecated/resources.py` 用 `skip` 排除（`test_legacy_parity` 的条数钉住这条规则：新操作不得长出「编造出来的退役地址」） |

### Contract change

```
GET /openapi/v1/bots/{bot_id}/resources/download-dir?path=<可选>
200  application/zip（Content-Disposition: attachment; filename*=UTF-8''<目录名>.zip，
     根目录时 workspace.zip；条目扁平于目录名之下，带 Unix 类型位）
400  path 含 '..'（既有 InvalidResourcePathError 映射）
404  目录不存在（既有 ResourceNotFoundError 映射）
413  超限额（新 DirectoryTooLargeError → "Directory too large to download"）
```

鉴权与既有 resources 端点一致：OWNER_SCOPED + GRANT_CHECKED_OWN_BOT。

## Non-goals

- 不改 console 的 `/api/resources/files/download-dir`（它有自己的限额与「空目录 404」
  语义，前端在用）。
- 不做真正的流式 zip 直出（zip 先落临时文件再 FileResponse——console 同款；每文件
  内容仍是整读，那是 device 传输层自身的形状）。
- 不动既有 `FileTooLargeError` 的「preview」映射文案（另案）。

## Success criteria

1. 服务层 walk：一次 device 解析、逐层 list_dir（不信 recursive 标志）、dotfile 与
   根级隐藏名过滤与 list_dir 一致、限额双检（listing 预检 + 实际字节复核）。
2. 端点：zip 可解析、条目名正确、临时文件经 background task 删除、失败不留半成品。
3. 登记完整：AUTHORIZATION（OWNER_SCOPED）+ ADMISSION（GRANT_CHECKED_OWN_BOT），
   库存/契约/门禁测试全绿。

## Known limitation

- 每文件内容整读进内存（≤100MB 上限挡住极端值）；zip 构建在事件循环内同步压缩，
  与 console 现状一致，成为瓶颈再优化。
- baas/local/teclaw 的 `enforce_download_limit` 标志是空操作（只有 Arca 强制），
  所以服务层自己按 listing size + 实际字节双重执行限额。
