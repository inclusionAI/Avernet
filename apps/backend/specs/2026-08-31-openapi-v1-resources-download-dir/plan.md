# Plan — download-dir

## Target architecture

```
GET /openapi/v1/bots/{bot_id}/resources/download-dir?path=
  → download_directory (openapi_v1/resources/router.py)
      _safe_path（'..' → 400）→ _file_coords → file_svc.iter_directory_files(...)
  → ResourceFileService.iter_directory_files（core/services/resource_file_service.py）
      一次 _resolve_ctx + _device_fs → 逐层 list_dir 遍历（BFS）
      → 限额双检（listing 预检 + 实际字节复核）→ 逐文件 read_file → yield (name, bytes)
  → build_directory_zip（openapi_v1/resources/zip_build.py）
      临时文件落盘；任何失败删半成品
  → FileResponse(application/zip, BackgroundTask(os.unlink))
```

## Changes

| 文件 | 改动 |
| --- | --- |
| `core/resources/service.py` | 新增 `DirectoryTooLargeError`（独立于 FileTooLargeError——ENVELOPE_ERRORS 按类型映射固定文案） |
| `core/services/resource_file_service.py` | `iter_directory_files` 异步生成器 + 三个限额常量（5000 文件 / 500MB / 100MB） |
| `openapi_v1/responses.py` | `ENVELOPE_ERRORS` 加 413 行 |
| `openapi_v1/resources/zip_build.py` | 新模块：ZipInfo 条目助手（照 console 的 macOS 类型位经验）+ `build_directory_zip` |
| `openapi_v1/resources/router.py` | `download_directory` 端点；`DirPathQuery`（可选 path）；`_DIR_TOO_LARGE_RESPONSE` |
| `openapi_v1/authorization.py` | OWNER_SCOPED 行 |
| `openapi_v1/admission.py` | GRANT_CHECKED_OWN_BOT 行 |
| `openapi_v1/deprecated/resources.py` | `skip` 排除 download-dir——新操作不长遗留地址 |

## Testing

- 服务层 walk：`tests/community/core/resources/test_resource_file_service.py`
  新增 12 条（嵌套树、一次解析、根级过滤、404、空目录、三类限额（monkeypatch 小数值）、
  流式复核抓 listing 谎报、中途消失跳过 vs 中途错误中止）。
- 处理器：`tests/community/adapters/http/openapi_v1/resources/test_resources_handlers.py`
  新增 8 条 + 路由数 tripwire 7→8。
- zip 构建：`tests/community/adapters/http/openapi_v1/resources/test_zip_build.py`（新）
  3 条——条目元数据、空目录、失败不留半成品。
- 端到端：`tests/community/endpoints/test_openapi_resources.py` `_HAPPY_CASES` 加一行
  （403 扫描自动获得）；legacy 配对测试过滤掉 download-dir（无遗留地址）。
- 库存/契约：admission、authorization、legacy parity（42 不变）、error schema、
  path convention、gateway namespace。

## Risks

- **事件循环内同步压缩**：每文件 ≤100MB 的 DEFLATED 压缩在 writestr 里同步执行，
  大文件会阻塞事件循环约秒级。与 console 现状一致；真成瓶颈再挪 worker 线程。
- **zip  tempfile 落盘**：依赖实例本地磁盘可写且有 500MB 量级余量——console 同款
  假设，不是新增风险。

## Follow-up

- 如需真正的流式 zip（边下边出），需要管道式 fileobj + StreamingResponse，另行立项。
- console 侧的 30MB 限额与本端点无关，不对齐。
