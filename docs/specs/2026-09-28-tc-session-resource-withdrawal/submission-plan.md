# Review / submission plan

On 2026-09-29 the user authorized committing and pushing the prepared change
for repository CI, while reserving merge-request creation for themselves.
This does not waive the failed Singlebox gate or authorize production enablement.

The reviewed implementation is submitted as two dependent branches:

| Stage | Head branch | Review base |
| --- | --- | --- |
| 1 | `codex/tc-resource-withdrawal-foundation` | `dev` |
| 2 | `codex/tc-session-resource-withdrawal` | stage 1, then `dev` after stage 1 lands |

See `STAGE.md` for the implementation present in this branch. The original
review-only patches remain a historical preparation artifact. Remote SHAs and
actual push outcomes are verified separately; this document is not proof of a
successful push or CI run. No remote PR is created by this submission step.

The Singlebox workflow targets PRs into `dev`, `dev_refactory_collaboration`,
`main` and `REL*`, not an arbitrary `codex/` base. Therefore a stage-2 PR stacked
on stage 1 does not by itself trigger that workflow. After stage 1 merges,
rebase stage 2 onto `dev` (accounting for squash merge), retarget it to `dev`,
and verify the resulting PR runs the required repository CI.

The selected workflow counts tests and SQL against a 1,500-added-line budget,
excluding docs. Combined source/test/SQL adds 1,859 lines, so two commits inside
a single 1,859-line PR would not solve the PR-range limit.

## Stage 1 — paused durable delivery foundation

Title: `feat(backend): add durable resource withdrawal delivery foundation`

- Add table/migration, immutable event types, repository protocol/implementation,
  opt-in transactional delete hook, worker, HTTP/test plugins, strict config,
  secret resolution and application lifecycle composition.
- Keep SessionResourceService unchanged: ordinary deletion does **not yet**
  request outbox insertion. Low-level tests exercise the opt-in hook directly.
- Include repository/worker/plugin/config/consumer tests and existing resource
  regression. Operator CLI and user-entrypoint emission arrive in stage 2.
- **1,477** added non-doc lines. Isolated tree regression: **135 passed**, with
  a further **314 architecture tests passed**; loaded
  source paths were verified to belong to that stage, not the final worktree.
- Feature docs in a stage-1 patch describe the final target; a stage banner must
  make clear that automatic event generation and CLI are not yet available.

## Stage 2 — single-chat wiring and operations

Title: `feat(backend): sync single-chat file deletion to withdrawal outbox`

- Base the stacked PR on stage 1 (not directly on dev while stage 1 is unmerged).
- Wire the three single-chat scope values, maintain the existing service fake,
  add trusted operator inspect/stats/replay CLI and its tests.
- Add formal/legacy endpoint tests, actual HTTP redirect qualification and
  extended crash-budget/capped-backoff qualification.
- **382** added non-doc lines relative to stage 1. The final combined tree is
  covered by the **20,131 passed / 43 skipped** full suite.

Both stages must satisfy normal repository CI. Stage 1's isolated targeted test
is not represented as a separate full-suite run. Run per-stage CI after creating
real commits/branches; no hook bypass or fabricated successful pipeline record.

## Before submission / merge

- Commit/push authorization was received on 2026-09-29; PR creation stays with
  the user. Preserve the failed/unrun gates in the PR description.
- Recreate patches after any source edit; validate patch application and the
  line count for each actual commit, push range and PR range.
- Preserve the user's unrelated untracked files; never stage all via `git add .`.
- Use the repository's PR sections and the drafts in `pr.md`; leave failed or
  unrun checks visible. Request independent review.
- Re-run the unified Singlebox/artifact gate in an isolated, fully provisioned
  environment. It did not pass in this task; these artifacts support review or
  Draft PR preparation, not an unconditional ready-to-merge claim.
- Delivery remains OFF until ECB/MySQL/UI/operations release gates pass even
  if code review and repository CI subsequently pass.
