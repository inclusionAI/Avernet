# Build-ignore Integration Implementation Plan

**Goal:** Freeze database rules once and exclude their exact artifact subtrees from every build copy phase.
**Architecture:** Inject the repository into BotBuildService. Keep rules separate from existing build-plan excludes, map extra roots, and pass the actual snapshot through ArcaSnapshotProducer.
**Tech Stack:** Python, pytest, rsync.
**Spec:** 001-spec-output.md

## Steps

- [ ] Add failing filesystem tests for main copy, extra includes, and extra-root mapping; run pytest and observe missing filtering.
- [ ] Add `build_ignore_rules.py` pure `is_excluded`, `extra_root_rules`, and `validate_required_paths`; add anchored rsync arguments and skip excluded extra includes before source probes.
- [ ] Add failing build snapshot tests: one read, immutable snapshot, empty snapshot, database failure and mandatory path conflicts.
- [ ] Inject repository/env; normalize captured paths and validate before copying, return `publish_ignore` in build result.
- [ ] Add failing producer snapshot tests; propagate only successful build snapshots into artifact ext.
- [ ] Run focused tests and existing build/runtime regression; report exact results. No branch, commit, deployment, or runtime mutation from this worker.
