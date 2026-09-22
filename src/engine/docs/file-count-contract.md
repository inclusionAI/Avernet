# File count v1

Service API: `FileService.count_files(path, auth=None) -> CountFilesResult`.
Plugin API: `OpenClawFilePort.count_files(path) -> dict` with the same fields.
Delivery: `GET /api/file/count?path=...&request_id=...`, existing FILE_LIST capability and runtime authentication.

Success uses ApiResponse with data `{path, file_count, elapsed_ms}`. The path is the original request, count and elapsed are nonnegative integers. Relative paths use `workspace_root().parent`; absolute paths inside that root or its sibling `openclawExt` are accepted. Hidden files, ZIP files and each hardlink entry count once. File symlinks count their regular target; directory symlinks recurse, including symlinks in requested path components. Each logical alias counts independently. Special files are excluded. Ignore rules do not apply. No contents or file lists are returned. Counts observe a live tree and are not atomic snapshots.

Only the real OpenClaw filesystem port supports v1. Local in-memory OpenClaw and Claude Code adapters explicitly report unsupported: the former has no physical root, the latter exposes multiple roots without a canonical primary engine root. This is an additive contract; existing file methods retain their behavior. Consumers must not downgrade to list_dir or represent failures as zero.

The worker opens `/`, then directory components using descriptors and O_NOFOLLOW. Symlinks are read explicitly and resolved component by component within the two allowed roots, retaining and verifying ancestors. Link expansion is bounded at 40 hops; active ancestor inode detection prevents directory cycles without globally deduplicating aliases. Cyclic, dangling and out-of-root links contribute zero, including when requested directly; no request error is raised for those skipped links. Permission errors, ordinary missing targets, directory replacements and worker failures still fail the request. Links in the configured root ancestry remain forbidden. Do not pass a symlink alias for the configured workspace; configure its actual absolute path. User-supplied `..` components are rejected, but relative link targets resolve `..` after preceding symlinks. `.` is the root. Empty, NUL and >4096 character inputs are invalid; request_id is required and at most 128 characters.

Limits per Engine process: two concurrent workers, no pending queue (immediate 503 on saturation), 120 seconds from admission through worker output. Timeout/cancellation terminates the process, allows 0.5 seconds to exit, then kills and reaps it before releasing capacity. Cleanup can add this grace interval and process-reaping time to response time. No thread or process continues scanning after cleanup. Worker executable/module are fixed and user paths are argv values, never shell text. The scan-completed log includes request correlation, timeout budget and aggregate `skipped_links` counts (`outside`, `dangling`, `cycle`), never resolved targets or individual child paths. IPC remains a constant-size aggregate; skipped diagnostics do not change HTTP response fields.

| HTTP | detail |
| --- | --- |
| 400 | invalid_path, not_directory |
| 403 | path_forbidden, permission_denied |
| 404 | path_not_found |
| 408 | scan_timeout |
| 409 | directory_changed |
| 500 | scan_failed |
| 501 | unsupported |
| 503 | busy |

FastAPI query schema violations return 422. Error details never contain raw exceptions or additional filesystem paths. Structured request/response/failure events include request_id, path, engine, status and elapsed time. Recursive key-based credential redaction covers nested dictionaries and lists. Cleanup inherits request correlation through ContextVar; cancellation produces a terminal failure event after child cleanup.

Contract tests: `api/tests/test_file_router.py::TestCount` exercises the consumer, adapter and real filesystem port; `plugins/tests/test_file_count.py` covers confinement, worker lifetime, races and unsupported implementations. Existing OpenClaw/Claude Code protocol-shape suites remain required.
