# Workflow editor: database drafts and Apply/save boundaries

## Source of truth

Workflow orchestration and Pack-file editing are separate capabilities. Opening,
editing, validating, or saving an existing workflow document must not require a
Pack snapshot, Git availability, or permission to write Pack files.

The actual database table is `workflow_specs` (the draft table). The effective
release is the row in `workflow_deploy_history` marked `is_active = 1`.
Neither the greatest history version nor a Git `wf` branch is evidence of which
database version is effective.

## Opening an editor session

1. Read the workflow draft from the database. If it exists, edit that draft.
   Preserve the complete stored document; API-envelope metadata must not replace
   the source YAML version or discard custom fields.
2. Only an explicitly recognized missing draft permits fallback to database
   release history. Read the top-level `active` record and its exact
   `deployNumber` snapshot. The active record can be outside the paginated
   history list, including after a rollback.
3. If there is neither a draft nor an active release, report that no editable
   source exists. Do not guess from the latest history row or from Git.

A database read failure, denied access, malformed document, or generic proxy 404
is not proof that a draft is absent. Such failures retain the current editor
session and must not silently select another source. An effective-version
fallback is not itself a database write: the draft still does not exist until
an explicit successful save.

The editor tracks the displayed document separately from the database draft
baseline used by save preflight. A null database baseline deliberately means
that no draft existed; it must not be replaced with the effective snapshot.
This distinction survives initial loads and workflow/history navigation. Late
responses must not overwrite a newer editor session or in-progress changes.

## Editing, Apply, and save

| Action | In-memory editor | `workflow_specs` | Effective history | Git |
| --- | --- | --- | --- | --- |
| Type YAML | Changes the unapplied YAML text buffer | No write | No write | No access |
| Apply / validate and apply | Validates and applies the YAML to the editor model; marks applied changes unsaved | No write | No write | No access |
| Save | Saves the already-applied editor document and verifies the stored draft | Update the existing draft, or create the first draft for an authorized workflow with an active database release | No write or activation | No access |
| Deploy | Separate explicit publication action | Existing deployment contract | Existing deployment contract | Existing deployment contract |

Apply may call the existing read-only workflow-validation endpoint. That request
is not persistence. Failed validation leaves the YAML text editable and does not
replace the last applied document. Server normalization that changes fields
still requires the existing explicit review/confirmation.

Save must not silently Apply unapplied YAML or other configuration fields. Ask
the user to Apply or discard pending buffers first. Successful Apply must not
trigger save or deployment. A successful draft save does not change the active
release, append a release-history row, allocate a release version, or return an
invented Git commit.

Save re-reads the draft before writing and verifies it after acknowledgement.
Changes made during the request remain dirty rather than being discarded. A
failed write or unverifiable/lost response is not success. These browser checks
are optimistic conflict detection, not an atomic server-side document CAS.

## Creating a draft from an effective version

When no draft exists, the existing database-save endpoint may create one only
for a workflow that has an active database deployment and for an actor with
existing edit permission. Derive its Pack identity from the database release;
do not use this path to take over an arbitrary new workflow ID or rename a Pack.
For the first save, the editor supplies `expectedDraftAbsent: true` together
with `expectedActiveDeployNumber`, the positive integer `deployNumber` read from
the exact active database release. An existing or concurrently inserted draft
returns `409 DRAFT_ALREADY_EXISTS` rather than being overwritten. The draft
precondition accepts only `true`, is never combined with `createOnly`, and is
forwarded by both public and internal host adapters. Inside the save
transaction, the service re-locks and re-reads the active release; if its
`deployNumber` no longer equals the submitted baseline, the save returns `409`
`BASELINE_STALE` and inserts nothing. The editor must then reload before
retrying, instead of writing a draft derived from the old release. Ordinary
existing-draft saves omit both preconditions and retain their existing
semantics.
Persistence
failures, ignored inserts, permission failures, and invalid input remain errors.

This is not `createOnly: true` Pack provisioning. Brand-new workflow/Pack
creation and explicit deployment keep their separate contracts. No new table,
Git credential, or migration is needed for saving a draft of an already deployed
workflow.

## Script-service failure isolation

- Do not eagerly fetch Pack versions merely because the orchestration editor
  mounted. Load them on an explicit script-file action or deployment action.
- Keep Pack loading/errors and file-write capability warnings in the script
  area. They must not replace the workflow's database-read or save status.
- The workflow Save button and its keyboard shortcut persist only the applied
  workflow document. They do not implicitly save scripts, require a clean Git
  candidate, or clear file drafts or unresolved file-save outcomes.
- Preserve genuine unsaved-content navigation protections; isolating errors is
  not permission to discard an upload, YAML buffer, canvas changes, or file
  candidate.
- Explicit deployment still enforces its existing permissions, candidate/ref
  preconditions, and unknown-outcome protections. Database-only save is not a
  shortcut around publication.

## Required regression cases

- Draft B, effective release A, and Git C differ: the editor loads B.
- No draft exists: load the exact active database release, then Save creates a
  draft without changing the active record or invoking Git.
- Missing-draft fallback does not run after a network error, denied access,
  invalid source, or unrelated 404.
- Typing YAML and clicking Apply do not issue save/Pack-write/deploy requests;
  only the later Save persists the applied document.
- Save with unapplied YAML neither silently applies it nor persists it.
- Script reads return 503 or file saving is blocked: workflow editing and
  database saving remain available, without erasing file state.
- Save failures, concurrent draft creation, and late responses preserve local
  work and do not falsely report success or mark later edits clean.
