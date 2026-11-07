# Workflow saving to wf and deployment by tag

This is the current source contract as of 2026-11-07, not a declaration that a
particular host or Bot has been upgraded. It replaces saved-as-draft /
wf-as-production. No new table, permissions product or transaction protocol is
introduced.

## Identities

- `workflow_specs` stores the saved workflow document.
- `wf/<workflowId>` stores the latest saved **complete Pack**.
- `deploy/<workflowId>/#N` fixes an explicitly confirmed saved commit. Existing
  release tags must not be overwritten.
- `workflow_deploy_history` stores the release document, tag and active state.
- Unsubmitted browser edits remain local. Saving is not deployment.

## Save

Editor and AutoHeal use the existing common Git preparation and DB save core.
Prepare and validate against the observed complete Pack, then persist the DB
draft, and only after its acknowledgement push wf using its observed lease.
YAML-only saves also update the Git YAML. Update only the selected YAML and
explicit file changes; preserve unchanged files, binary bytes and executable
modes. Do not rebuild a Pack from database YAML or a scripts-only inventory.

Ordinary AutoHeal `/save`, unified save, first-draft restoration and create-only
saves have the same DB-plus-Git outcome. An existing workflow requires a wf baseline: if it is missing, reject before DB writes rather than constructing a YAML-only Pack from default HEAD or an old branch. Only create-only may initialize a new Pack. Git configuration is required. A
failed/unknown acknowledgement is not success: retain local edits and expose
the known phases instead of rolling back or retrying blindly.

The separate `/api/workflows/save` compatibility endpoint remains the DB step
used by existing ClawMind, which already writes Git itself. Do not add a second
Git write to that callback.

## Deploy

Publish the exact saved candidate. Do not overlay the current DB YAML during
deployment, manufacture a new release commit, move wf, or copy missing scripts
from another version. Create the tag and record/activate the corresponding
release using the existing history checks. Compare production changes against
the active history **tag**, not against wf. A later saved draft is not a change
to the production baseline. Existing permissions, DB baseline checks and
uncertain-result handling remain in force.

Editor and AutoHeal use this same publication core. An exact reviewed repair
candidate is also published without replacing the latest saved branch.

## Reads and UI

New editing sessions default to draft. Workflow and scripts tabs share the same
Save and Deploy actions. Production files come from the active tag, even when
wf is newer or absent; with no active deployment, production is unavailable
rather than silently replaced by draft. A conflicting/failed read is not an
empty Pack.

### DB / Git divergence in the editor

A difference between the persisted DB document and the workflow YAML in the
observed Git Pack does not by itself prevent saving the current candidate, but
**direct deployment is currently rejected**. TaskGuard currently prefers the DB
draft, so publishing a different Git workflow could make execution differ from
the released tree. The server must fail closed with HTTP 409
`SNAPSHOT_DB_MISMATCH` when those documents differ or cannot be safely compared.
The user can explicitly save the chosen current candidate to synchronize DB and
wf, then review and deploy again. Saving can immediately affect runtimes that
read the DB draft; it is not an automatic repair or deployment.

Unsaved canvas edits do not prevent reviewing and deploying the already saved
tree only when the saved DB and Git workflow match and all other server checks
pass. The UI must warn that unsaved edits are excluded. Bind review and
confirmation to the exact local draft and layout positions as well as DB and
Git baselines; changes during the decision invalidate it. Existing unknown
result, concurrency, CAS and authorization protections remain unchanged.

The initial candidate remains the DB workflow plus existing editor changes;
never silently replace it with Git. Users may compare that candidate with the
Git YAML and explicitly choose the Git document only if decoding, parsing,
lossless conversion and workflow identity validation are safe. Before loading that document into the editor,
confirm that it will overwrite the current candidate. Cancelling, closing or
failing leaves that candidate intact, and loading is not a save. If Git cannot
be safely interpreted, explain why it cannot be selected; DB/the existing
candidate remains usable. An uncomparable Git document is not evidence of a
match, and other Pack files must not be silently dropped or misrepresented as
reviewed.

Saving still writes the explicitly chosen current candidate using the verified
DB token and observed wf lease, with the complete Pack preserved. Deploying
still reviews and publishes the exact saved wf tree only after the server has
verified its workflow against the DB draft. Neither DB nor unsaved browser edits
are overlaid onto the release. Existing validation, identity, authorization,
Pack integrity, CAS and error/unknown outcome guards remain in force. A comparison and overwrite confirmation are
bound to their workflow, editor session, baselines and local changes; invalidate
stale or timed-out results, and do not let late responses replace edits or
unlock a newer operation. Failure never means automatic retry or success.

For wire compatibility, `expectedSavedHead` and `draftHead` retain their names
but represent the wf write lease; `sourceCommit` is the observed wf commit for draft saves. `savedCommit` means the saved candidate, not a saved/ branch. Existing
protocol names are retained; new browser code validates the actual ref type.

## Existing ClawMind compatibility

Keep the deployed ClawMind save/deploy/run semantics. New root Pack commits
carry the already-supported saved-candidate metadata with the real YAML SHA256
and complete tree ID. Do not add a header without correct metadata. Nested
Packs stay ordinary commits because existing ClawMind metadata parsing does
not retain a nested Pack root. Verify remote materialization and native save /
deploy against unchanged ClawMind source.

ClawWeb does not query, read, compare, fall back to, write or delete saved/
branches. wf is the only saved-workflow source and write target. Missing wf
returns an unavailable save baseline, not an automatic choice of another
branch. ClawMind source remains unchanged; its own historical behavior is not
a new policy or migration mechanism in ClawWeb. In particular, the unchanged ClawMind reader still has its own legacy-ref selection: a nested Pack with an old saved branch may be shadowed there. This limitation is recorded, not handled by a ClawWeb fallback, conflict guard, synchronization or migration.

## Verification and rollout

Required regressions cover Editor and AutoHeal DB+wf saves, YAML-only and script
changes, preservation of other resources/modes, exact tag publication with wf
unchanged, active-tag production reads, first deployment, concurrent ref
changes, partial writes, permissions, and current ClawMind save/deploy.

Deploy the matching public module and enterprise host together. No saved/ migration or synchronization is part of rollout. Local tests do not prove
real pre-production UI acceptance or the version installed on every Bot.
