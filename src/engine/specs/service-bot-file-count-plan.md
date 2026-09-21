# Engine File Count Implementation Plan

> Execute inline with superpowers:executing-plans; no commit or deployment in this subtask.

**Goal:** Count recursive regular directory entries inside the configured OpenClaw root.
**Architecture:** HTTP → FileService → native port → bounded cancellable worker. Shared errors live in kernel; OS traversal lives in plugins.
**Tech Stack:** Python asyncio subprocess, POSIX directory descriptors, FastAPI, pytest.
**Spec:** ../../backend/specs/service-bot-file-count/001-spec-output.md

## Constraints

Preserve requested path; no shell, symlink following, ignore filtering, file contents, or partial successful counts. Two concurrent workers, ten-second deadline, no pending queue. Unsupported engines return 501. No deployment or commit.

## Steps

- [x] Write and run real filesystem tests: `_FilePortMixin().count_files("workspace")` must count hidden/ZIP/hardlink entries and reject invalid targets; observed 9 missing-method failures.
- [x] Add `kernel/file_count.py` stable error contract, `plugins/file_count_worker.py` fd traversal and `plugins/file_count.py` subprocess lifecycle; OpenClaw port uses `workspace_root().parent`.
- [x] Add `CountFilesResult` and `FileService.count_files(path, auth=None)`, native port contract and adapter conversion; Claude Code explicitly raises unsupported until it has a canonical main engine root.
- [x] Add HTTP tests before `GET /api/file/count`, stable error mapping and structured redacted request/response/failure logs.
- [x] Add real worker timeout/cancellation/busy tests and injected filesystem race/permission failures; verify worker reaping, next request recovery and no out-of-root reads.
- [ ] Run closest existing contracts and architecture checks, line coverage and lint. Record exact results in the delegated engine report.
