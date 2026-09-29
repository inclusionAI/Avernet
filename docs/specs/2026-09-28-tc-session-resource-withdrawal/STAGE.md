# Submission stage 2 — single-chat wiring and operations

This branch contains the foundation plus single-chat withdrawal event emission
and the operator CLI. Group and unknown session scopes remain excluded.

- Head: `codex/tc-session-resource-withdrawal`
- Initial review base: `codex/tc-resource-withdrawal-foundation`
- Foundation commit: `0e53dded56b5ed2156e14d31b39ccb1350b6a39c`
- Source/test/SQL additions relative to stage 1: 382 lines (docs excluded).
- Combined source/test tree is byte-identical to the final tested snapshot.
- Combined Backend suite: 20,131 passed / 43 skipped; changed-line coverage 98.64%.
- Unified Singlebox: failed locally; repository CI must still pass.
- Delivery stays OFF; ECB, MySQL and UI release qualification remain open.

After stage 1 merges, rebase this stage onto `dev` and retarget its PR to `dev`
to trigger the required Singlebox PR workflow. Account for squash-merge ancestry
rather than accidentally reintroducing stage 1 as new changes. A stacked PR
against a `codex/` branch alone does not trigger that workflow.

The user creates the merge requests. No remote PR creation, merge, production
configuration change or service restart is performed by this submission.
