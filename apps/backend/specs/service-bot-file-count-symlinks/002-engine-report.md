# Engine implementation and verification

## Boundary

Existing chain: FileService → OpenClawFileAdapter → filesystem port → fixed isolated worker. Only the file-count worker, its lifecycle timeout/diagnostics, matching contract text and tests changed. No router, auth, binding selection, general HTTP timeout, other file methods, deployment or Bot files changed.

## Implementation

- Engine admission-to-output timeout is 120 seconds; concurrency and cancellation/terminate/kill/reap semantics are retained.
- The worker resolves links using explicit readlink and directory-relative O_NOFOLLOW opens, retaining ancestry and validating inode/device identity. Relative target parents are resolved after preceding links, not via lexical normpath.
- Allowed roots are the configured Engine root and sibling openclawExt; both must be real directories with non-link ancestry. Normal file and directory aliases count per logical entry; active ancestor checks cover both linked and ordinary directory transitions.
- Dangling, outside and cyclic links contribute zero, including requested links. Ordinary missing targets, permissions, races and scan failures remain failures. Worker output carries only bounded aggregate skip counts, never individual targets.
- Event engine.file_count.scan_completed records request_id, file_count, timeout_seconds, elapsed_ms and skipped_links. The existing HTTP boundary redaction and response/error events remain unchanged. No resolved target or child filenames are logged.

## TDD evidence

- Baseline existing file-count suite: 36 passed.
- Initial link tests: 6 failed / 1 passed against the old worker; failures showed excluded file/dir aliases and requested-link path_forbidden behavior.
- Budget/log test failed with observed timeout 10.0, then passed at 120.0 with aggregate reasons and request correlation.
- Symlink followed by parent component test failed 0 != 1 before removing lexical normalization; passed after component resolution.
- Ancestor back-link through ordinary child test failed 3 != 2, then passed after checking ordinary-child active identity.
- Focused suite after cycle fix: 50 passed. Two additional race/root tests are included in final full run.
- Preliminary full suite: 2858 passed, 5 skipped, 17 existing warnings; this run predates final ordinary-child cycle fix and is not the terminal gate.

## Validation

Engine interpreter reused from the previous worktree, with cwd at this worktree's src/engine and PYTHONPATH=src so imports use current source.

- Focused coverage before final tests: file_count.py 74/74 (100%); worker 153/157 (97%).
- uvx --offline flake8 for both implementations and both test files: PASS for unused import/variable, E203/E211/E265 and the repository blocking Python rules.
- Final full CI-shaped run: 2861 passed, 5 corp-only cases deselected exactly as scripts/ci_test.sh, zero skipped/failed, 17 existing deprecation warnings, 100.24 seconds. Flags: `--cov=src --cov-report=xml:pytest_report/symlink-engine-cov.xml --cov-report=json:pytest_report/symlink-engine-cov.json --junitxml=pytest_report/symlink-engine-junit.xml`. These final reports include the ordinary-child cycle fix and all 52 focused cases.

No commits/push/deployment performed by this subtask. Parent agent owns final report, fixed-tree changed coverage and PR.
