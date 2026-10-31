# Waiver request: retain the BaaS service layout for the image-update fix

> Historical record: the repository-wide source-file line-count limit was removed on 2026-10-31. Its file-size ceilings and mandatory splitting conditions no longer apply. Other scope and correctness constraints remain unchanged.


- Status: proposed; maintainer approval required before merge
- Owner: `@iParadox-ant` (PR author)
- Reviewer: `@ws121200` (scope decision)
- Review/expiry date: 2026-11-10
- Delivery and follow-up tracking: [PR #3154](https://github.com/inclusionAI/Avernet/pull/3154)
- Scope decision: [review comment](https://github.com/inclusionAI/Avernet/pull/3154#issuecomment-6434748856)

## Exact rule and scope

Repository `AGENTS.md`, Development Guidelines / File Size, requires each
modified source file to contain at most 1,000 lines. This request covers only the
pre-existing oversized files:

- `apps/baas/src/secbaas/community/core/service/bot_manage/_bot_management_service.py`
- `apps/baas/src/secbaas/community/core/service/publish_manage/_publish_service.py`
- `apps/baas/tests/unit/core/service/publish_manage/test_publish_service.py` (the
  existing direct-UPDATE assertion must match the corrected image contract)

It permits only the deployment-config merge and update publish changes in this
PR, not additional service responsibilities or unrelated edits.

## Reason and alternative

The production files already exceed the limit at the PR base (1,750 and 4,982
lines); the existing publish test file is 11,141 lines. Its one changed UPDATE
test previously required a null clone-config override, which explicitly kept
the stale image. It now verifies image clearing and metadata preservation.
The first revision split their existing methods into mixins. The scope reviewer
explicitly requested that this update-image fix not include structural
refactoring. Retaining that split would mix a large mechanical change into the
narrow correction; this revision restores the original classes and layout.
This records the conflict rather than silently claiming file-size compliance.

## Risk and compensating controls

Existing maintainability debt remains. No allowlist, test, CI threshold, import
boundary, constructor or state-machine change is introduced. Comparison with the
PR base must verify that scale, native restart, and every other out-of-scope
method remain unchanged. Run the BaaS unit/architecture suites and focused
update/configuration-persistence HTTP and integration regressions.

## Removal plan

Before 2026-11-10, the owner will request maintainer review of this exception and
track a separate, behavior-neutral service decomposition from PR #3154. Remove
this waiver once these files meet the limit, or obtain an explicitly reviewed
renewal before expiry. This proposal is not proof of approval and does not
permit merging without the repository's required reviews and checks.
