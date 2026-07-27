# CLAUDE.md

Claude Code should follow the repository-wide instructions in `AGENTS.md`.

In particular, do not introduce Python `T | None` types unless `None` is an
intentional state in the contract; required values must remain non-optional.

Keep this file intentionally small so agent instructions do not drift. Update
`AGENTS.md` when project-wide rules change.

Before pushing, follow the pre-push target contract in `AGENTS.md`. By default
the pre-push hook runs lint-only (SAST) and skips the heavier tests, coverage,
and E2E; set `OCB_PRE_PUSH_RUN_CI=1` to run the full module gates. When the
gates run, the default target is `origin/dev`; set `avernet.prePush.mergeTarget`
for a persistent override or `AVERNET_PRE_PUSH_MERGE_TARGET` for one `git push`.
The hook must fetch that target and use its merge base rather than a direct
target-to-head diff.

Before changing Git hooks, module CI entrypoints, Singlebox orchestration,
acceptance E2E tests, coverage manifests, or coverage reporting, read the
`Pre-push Module Selection` section in `AGENTS.md` and treat it together with
the referenced scripts as one contract.

For local `just test` / `just test-no-cov`, read the `Local just test Baseline`
section in `AGENTS.md` and treat `scripts/ci/resolve_test_baseline.sh`,
`scripts/ci/local_test.sh`, and the root `justfile` as one contract with the
pre-push merge-target resolution.
