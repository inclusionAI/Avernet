# Caller runtime sessions implementation plan

**Goal:** Register all actual nonempty session IDs from existing dedicated OpenClaw Caller runtimes.
**Architecture:** Reuse Caller discovery and ownership index; capability stays opt-in. Shared strategy rejects empty-index Caller lookup before ledger access.
**Spec:** 001-spec-output.md

- [x] RED: ordinary/cron/subagent IDs plus malformed and duplicate values; Teclaw empty lookup raises if ledger touched.
- [x] GREEN: replace cron regex with nonempty string validation, preserve original IDs, deduplicate within and across runtime pages.
- [x] Keep exact filter, favorites, client pagination and real repository two-subject isolation assertions.
- [x] Expand real HTTP acceptance into ordinary/cron/subagent cases.
- [x] Verify ExpertChat and architecture suite; hand off full CI and live gate to orchestrator.

No commit/push/deployment in coding worker scope.
