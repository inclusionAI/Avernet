# Teclaw shared Caller refresh session validation

## Requirement and scope

Remove the Backend owned-session ledger requirement from shared Teclaw Caller IAM refresh across draft, verify and online, as explicitly requested. A nonblank session key and an ACTIVE Teclaw binding remain required before signing. Existing caller authentication, friend-subject matching, initial target selection and required signing/installation failures remain in force.

This deliberately allows refresh when the supplied session has no matching ACTIVE owned-session row, including deleted or differently scoped ledger rows. It does not claim to establish session ownership. Remove the validator's now-unused repository dependency and update its DI provider. Reuse existing AUTO resolution for the requested stage; do not add a source allowlist in the refresh branch. No HTTP schema, repository behavior or provider transport changes.

## Plan and verification

1. Update real-DI/SQLite/HTTP-boundary regression cases: ledger mismatches or deletion during signing allow success and one APPEND to the supplied session; first verify these fail against the old implementation.
2. Remove the ownership guard and unused imports/dependency. Retain validated/rejected logs and mark ownership validation as not required on successful shared validation.
3. Update domain documentation, run affected tests, full Backend tests and lint/diff checks.
4. Rebase the isolated topic on latest GitHub inclusionAI/Avernet REL20261112; create a PR to that base and inspect/fix actionable remote checks and reviews. No merge or deployment.

## Risk and compatibility

This intentionally relaxes session authorization for this shared refresh branch. Bot ownership or a user suffix does not prove ownership of a supplied key. The remaining authenticated Caller/target policy governs access, and provider-side session isolation is not established by these unit tests. The internal validator constructor drops the chat repository argument; the public DI provider and tests must change together. Existing friend preparation retains its own subject/ownership checks. The historical teclaw-caller-refresh spec describes the prior behavior; this spec supersedes its shared owned-session requirement.

## Additional requirement

Remove the online-only shared-branch condition. Draft and verify use the same shared refresh flow, selecting their own existing stage binding. Remove the SERVICE_ONLINE source guard and keep the initial target selection; do not redirect draft/verify to online. Add real-DI success tests for each stage.

## Signing-time simplification

Per the explicit follow-up request, remove the post-signing resolver lookup, changed-target rejection, repeated session validation and fenced event. Install into the initially selected binding with the supplied session key. Do not promise target revalidation across signing; initial binding/key checks and required exchange/install failures remain.
