# Workflow access through collaborative Bots

Browser workflow access is the union of the authenticated user's direct workflow
grants and the exact Bot grants of Bots returned by the host's `BotDirectory`.
The host may inject `Pick<BotDirectory, "listBots">` into
`BotWorkflowPermissionRepository`; deployments without it retain direct grants.

- Match both the Bot ID and its real Owner ID. A specific-Bot grant with wildcard
  Owner also applies to an accessible Bot.
- Never inherit another Owner's personal (`NULL`/empty Bot ID) or all-Bot (`*`)
  grants through a collaborative Bot.
- Merge grants additively. A zero does not override a grant from another source.
  As in existing browser authorization, edit permission also permits reading the
  workflow. A view-only Bot grant does not permit editing.
- Inherited run visibility is restricted to the matching Bot IDs. A user's own
  direct all-Bot grant still permits viewing all runs.
- Explicit bot-runtime checks (`checkPermission`, or checks with a concrete Bot
  ID) retain their existing Bot/Owner authorization and execution-bit semantics.

The workflow detail API needs only the workflow ID and the existing authenticated
identity. No new frontend parameters are required. For authenticated browser list
requests, `botOwnerId`/`botId` select the asset scope; the result is intersected with
the caller's effective access. The existing non-browser list compatibility path
is unchanged. Lists cache workflow summaries, not authorization results, and use
`Cache-Control: no-store` so membership revocation is checked on every request.

Membership is resolved on each check. An unavailable Bot directory contributes no
inherited grants and emits a warning; direct grants continue working. Permission
database errors are not treated as grants. The host adapter must also log lookup
failures if it degrades to an empty list internally. Logs must not include query
credentials or raw database errors.

Only workflow creation writes the initial permission grant. Editing through an
inherited grant does not create a permanent user grant. Removing a collaborator
therefore removes inherited access while preserving separately assigned grants.

## Cloning exact Bot grants

The backend-facing Clone route preserves its HTTP contract and merges exact Bot
permissions without duplicate rows. See [Clone persistence and OceanBase index
preflight](bot-workflow-permission-clone.md) for its unique-key invariant,
concurrency limits and the deploy-before-code index DDL.

## Browser editor capabilities

`GET /api/workflows/:workflowId/access` retains its existing workflow identity,
`canView`, and `canEdit` fields and additively reports `canExecute` plus
`canDeploy`. Execution is the execution grant; Editor snapshot deployment publishes a
complete Pack and requires an actual edit grant. Execute-only permission remains
valid only for the separate legacy AutoHeal deployment path, not the Editor
snapshot protocol. `canDeploy` therefore reflects the Editor-specific policy. Clients must not infer execution from
editing or infer any write grant from a successful document/Pack read.

The editor treats a loaded workflow as read-only while its write capabilities
are unknown, unavailable, or denied. Readers may inspect the canvas, authored
YAML, history, and authorized Pack files without enabling graph/form/file edits,
AI modification, save shortcuts, or unauthorized deployment. A new local,
unsaved workflow retains the existing explicit creation path. Every server write
still independently authenticates and rechecks its actual authorization and CAS
preconditions; capability responses are advisory, not write credentials.

A host may provide a broader authenticated workflow-view policy without changing
these default public ACLs. The Pack routers expose the explicit GET-only
`authorizeRead` callback documented in `pack-scripts-read.md` and
`pack-version-write.md`; it cannot authorize PUT, operation reconciliation, or
runtime execution. Host-specific read-only compositions must authenticate before
reading and confine any permissive policy to documented read endpoints. Secret
configuration, permission administration, and runtime scopes are not implicitly
part of workflow viewing.

## Shared browser workflow capability

ClawWeb and Task Escort consume the same Service API. A host selecting the
shared browser policy derives scope from its verified principal. An administrator
receives all workflows; other users receive the workflow IDs returned by the
explicit `can_view` whitelist (`listBrowserViewableWorkflowIds`). The whitelist
includes direct and exact collaborative-Bot grants and explicit global grants;
`can_edit` alone does not grant access under this policy. An empty whitelist
means no workflows. The legacy non-browser and Bot-runtime policies above are
not changed by this new browser policy, and request filters cannot add grants.

For these browser consumers, a visible workflow's statistics, runs, node I/O
and evidence use its workflow-level scope, not an additional Bot-run view grant.
Normal projection, redaction, and resource-to-workflow checks still apply.
Workflow-bound repair generation, application and publication require workflow
view; Editor mutations keep the Editor edit gate. Starting a workflow, including
starting a trial, still requires execution permission; it is not implied by
being able to open the trial page. Approval, deployment validation and protected
configuration rules are independent of this browser capability.

The shared dashboard routes scope both selected and comparison intervals before
aggregation, ranking, or pagination. A logged-in viewer without grants receives
an empty result for scoped widgets, not an admin response. The host must not
expose any admin-only dashboard widget to non-admins until every data source
used by that widget can be scoped. Consumers keep existing API paths and
response fields; these rules do not implicitly connect a UI write action that
was not previously wired to a Service API.
