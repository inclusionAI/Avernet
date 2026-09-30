# File count: bounded symlink traversal and longer scans

Status: user approved, including skip-not-fail for abnormal links.

## Existing chain and change boundary

Backend file-count router → service/binding resolution → HttpFileCountRuntime → Engine file route → OpenClaw file port → isolated worker. Keep routers, auth, binding selection, schema and replica behavior unchanged. Changes are limited to file-count timeout/traversal, diagnostics, tests and relevant contract documentation. No deployment, Bot file changes, restart, general HTTP timeout changes or OCB gitlink updates.

## Coding spec

- Engine scan budget 120 seconds; Backend per-instance resolution/request budget and both BaaS/ARCA transport timeout 150 seconds. Preserve two-worker bound, timeout/cancellation cleanup and actual process reaping. Multi-replica total request time is not capped at 150 seconds.
- Follow file and directory symlinks at requested path components and during recursive traversal. Allow the existing Engine root (.openclaw in production) and sibling openclawExt only. Resolve using bounded, safe component traversal; never blindly remove O_NOFOLLOW or validate then reopen an unprotected pathname.
- Count ordinary files by logical entry, including hidden files and file symlinks; directory symlinks recurse, aliases/hardlinks count per entry. Detect active ancestor inode cycles rather than global inode deduplication.
- Per user correction, cyclic, dangling and out-of-root symlinks do not fail the request: skip them with zero contribution and bounded diagnostics. A requested symlink with these problems also contributes zero. Invalid direct request paths, ordinary missing target, inaccessible ordinary directories and worker/timeout failures retain explicit failure rather than masquerading as a completed scan. Document exact behavior.
- Preserve non-following safe opens and replacement/race checks. No shell, file-content reads, credential logging or extra dependencies.
- Keep response fields unchanged. Log aggregate skipped counts by stable reason (no resolved target or raw exception), correlated request ID and timeout budget. Existing inbound/outbound success/failure logging remains.

## Review spec

Verify allowed roots cannot escape through links/parent components/replacement; normal subtrees still use efficient traversal; no infinite recursion/worker leak; skip semantics do not swallow global scan failure; unchanged auth/stage/provider semantics. Test request/success/failure diagnostics and credential absence. Production file size <1000 lines.

## QA spec

Test file/dir links, entry/middle links, relative/absolute multi-hop links, allowed openclawExt, root-outside targets, dangling/self/mutual/ancestor cycles, aliases, hardlinks, special entries, deep paths, permissions, replacement races, unchanged normal count, skipped diagnostics, timeout/cancellation cleanup, and Backend 150-second transport for both providers. Run real filesystem→Engine HTTP→Backend integration, affected full module tests, lint, architecture and coverage gates. Shorten deadlines in cancellation tests; do not wait 120 seconds per test.

## Ship spec

Worktree: service-bot-file-count-symlinks, initial topic feat/service-bot-file-count-symlinks. GitHub inclusionAI/Avernet source remote github; base REL20260922 (initial cf20a597b2e058c9baa0d017d0b432a7235f08d3). Preserve old dirty worktree. After validated commit, refresh base, create approved result branch rebase/service-bot-file-count-symlinks-on-REL20260922, rebase topic onto base, push non-protected branch and create new PR. Inspect real head CI/reviews, fix scoped test failures; pending is not pass. No automatic merge/deploy.

## Compatibility

Service/Plugin API signatures remain stable; counting semantics intentionally add link targets and skip abnormal links. Backend timeout covers both providers. Only filesystem-backed OpenClaw implementation gains traversal; unsupported runtimes stay unsupported. Consumers should treat success as count under documented skip rules, not a complete snapshot or physical inode count. No migration required; revert code to roll back.
