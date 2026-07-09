# tests/acceptance/ — 路 B live-backend acceptance

Route-B tests start a **real backend** (`--local`, in-memory SQLite) and assert
both API responses and **physical artifacts** (symlinks/files/dirs) on disk.
The business flow is shared with route-A e2e via `tests/_flows/<module>/`.

## Run

```bash
cd src/backend
RUN_ACCEPTANCE=1 uv run pytest tests/acceptance/skill_center/ -v -s
```

Default-off: every `@pytest.mark.acceptance` test is skipped unless
`RUN_ACCEPTANCE=1`, so CI/the normal suite never spawn a backend.

## vs e2e (路 A) / vs unit

| | unit | e2e (路 A) | acceptance (路 B) |
|---|---|---|---|
| backend | mocked | in-process TestClient | real subprocess |
| filesystem | none | none/skip | real symlinks asserted |
| speed | fast | fast | slow (boots backend) |
| gate | RUN — | RUN_E2E_TESTS=1 | RUN_ACCEPTANCE=1 |

The two gates are independent: each defends a distinct failure mode (API
contract vs physical hot-reload artifacts). FsAssert is skipped in route A.

## baseline_*.tree

The `*.tree` snapshots are the empirical "correct shape" of
`~/.openclaw/workspace/skills` after a full lifecycle. Any code change that diverges them gets
caught — physical-artifact drift can't happen silently. Regenerate only when
the new shape is genuinely intended, and review the diff.

## Caveat

8888 must be free. If a session backend owns it, the fixture fails loudly
rather than clobbering its state. Symlinks land in the unified global root
`~/.openclaw/workspace/skills` (fs_root=$HOME; see findings/skill-center-dual-
skills-dir.md), where the global skills-repo already supplies git skills — no
MOLTIS_HOME needed.
