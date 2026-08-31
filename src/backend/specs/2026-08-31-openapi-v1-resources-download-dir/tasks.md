# Tasks — download-dir

## Group A — 服务层

- [x] `core/resources/service.py`：`DirectoryTooLargeError`（固定 413 文案的类型载体）
- [x] `resource_file_service.py`：`iter_directory_files`——一次解析、BFS 逐层 list_dir、
  dotfile/根级隐藏名过滤与 `list_dir` 一致、限额双检（listing 预检 + 实际字节复核）、
  消失跳过 / 出错中止的 race 规则、404 与空目录区分
- [x] 限额常量：`DIRECTORY_DOWNLOAD_MAX_FILES=5000` /
  `DIRECTORY_DOWNLOAD_MAX_TOTAL_BYTES=500MB` / `DIRECTORY_DOWNLOAD_MAX_FILE_BYTES=100MB`

## Group B — 传输层与路由

- [x] `openapi_v1/resources/zip_build.py`：`_zip_file_entry` / `_zip_dir_entry`（Unix 类型位，
  macOS 归档工具需要）+ `build_directory_zip`（临时文件、失败删半成品）
- [x] `router.py`：`download_directory` 端点（path 可选 = 整个 workspace 根）、
  `DirPathQuery`、`_DIR_TOO_LARGE_RESPONSE`、`FileResponse` + `BackgroundTask` 清理
- [x] `responses.py`：`ENVELOPE_ERRORS` 加 `DirectoryTooLargeError → (413, ...)`；
  不动 `FileTooLargeError` 的旧文案

## Group C — 登记

- [x] `authorization.py`：`("GET", ".../resources/download-dir")` → OWNER_SCOPED
- [x] `admission.py`：同地址 → GRANT_CHECKED_OWN_BOT
- [x] `deprecated/resources.py`：`skip` 排除 download-dir（新操作不长遗留地址；
  `test_legacy_parity` 的 42 条数不变）

## Group D — 测试

- [x] 服务层 12 条（walk 语义、限额三分支、race 规则）
- [x] 处理器 8 条（zip 内容、根下载、400/404/413、空目录、UTF-8 文件名、失败传播）
- [x] `test_zip_build.py` 3 条
- [x] `test_openapi_resources.py` `_HAPPY_CASES` +1（403 扫描自动获得）
- [x] `test_openapi_legacy_routines_resources.py`：配对 zip 过滤 download-dir
- [x] 库存/契约全绿：admission、authorization、legacy parity、error schema、
  path convention、gateway namespace、endpoint runner

## Out of scope

- console 的 `/api/resources/files/download-dir` 不动（30MB 限额、空目录 404 语义保留）。
- 流式 zip 直出、worker 线程压缩——成为瓶颈再做。
