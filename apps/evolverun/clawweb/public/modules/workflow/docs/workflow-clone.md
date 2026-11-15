# Workflow clone contract

`POST /api/workflows/clone` creates a new saved workflow from an existing saved
workflow. The feature intentionally has no source-workflow permission filter in
this first, internal prototype iteration: `GET /api/workflows/clone-candidates`
lists summaries from `workflow_specs` for every saved workflow, while the
operation still requires a signed-in actor and grants that actor full access to
the new workflow. Source-workflow authorization must be added before this route is
enabled for a public multi-tenant deployment.

## Storage model

The clone operation creates one independent identity:

- target `workflow_id` = the user-provided ID
- target `pack_id` = target `workflow_id`
- DB `workflow_specs.spec_json` = a normalized projection of the updated YAML
- Git `wf/<targetWorkflowId>` = an isolated root Pack containing:
  - `workflow.pack.yaml`
  - `workflows/<targetWorkflowId>.yaml`
  - every source Pack file under `scripts/**`

Target YAML keeps the source nodes and configuration but replaces `id` and
`title`, and removes legacy top-level `packId` / `facade` identity fields. The
target manifest contains only the target workflow.

The source DB row, Pack manifest, Git refs, tags and facade command are never
read for writes and never modified.

## Facade identity

A clone does not inherit the source facade command. When the user supplies a new
command, it is stored once as a facade binding for the target. An omitted or
invalid command creates no binding and never rebinds the source command.

## Failure behavior

The target DB identity is reserved before the Git push. Once a push is
attempted, the Git helper treats any push exception as outcome-unknown, and the
service returns `CLONE_OUTCOME_UNKNOWN`; the caller must verify the target before
retrying. A pre-push validation failure is definite and creates no DB identity.
Target and facade conflicts are 409 responses; a retry after an outcome-unknown
push may encounter the reserved target identity instead.

## Preview and validation

Preview reads the same source draft snapshot and applies the same script
normalization and limits as clone. A source whose `scripts/**` files cannot be
copied fails preview with the `SOURCE_SCRIPTS_*` responses; the script count is
the number of files that clone will copy.

## Unexpected server failures

Unexpected internal failures are logged server-side and return only
`500 {"error":"Internal Server Error","code":"INTERNAL_ERROR"}`. Internal
exception text is never returned to the client.
