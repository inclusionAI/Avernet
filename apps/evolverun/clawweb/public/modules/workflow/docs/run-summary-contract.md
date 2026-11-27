# Run summary launch input and lineage

Owner: `@avernet/workflow`. These are existing stored fields on `run` in the
read-only `GET /api/runs/:flowId` Service API or its underlying stored run, not
a new engine Plugin API. The `{ run, nodes, availableInterventions }` envelope
is unchanged. Read behavior **v2 (2026-11-26)** adds independently authorized
lineage only to eligible reader-backed ordinary detail; it adds no wire-version
field or client opt-in. See [the detail contract](run-detail-contract.md).

## Module-local detail metadata

`shared/run-launch-metadata.ts` exports `RunLaunchMetadata` with nullable string
fields `params_json`, `input_json`, `parent_flow_id`, and `root_flow_id`. Consumers
use `FlowRun & Partial<RunRuntimeMetadata> & Partial<RunLaunchMetadata>`: the
shared `FlowRun` stays unchanged, and legacy or redacted fields may be absent.
The public detail read passes stored launch JSON strings and SQL nulls through
unchanged; it does not parse, normalize, or synthesize launch input.

## Launch input semantics

For the normal main-controller path:

- `params_json` records the parsed launch parameters snapshot. It is the source
  for original launch parameters, not a raw request body or command transcript.
- `input_json` is a mutable launch/execution input summary. Completion may
  overwrite it with current state, including later `params`, and may drop
  launch-only fields such as `command`. Never use later `input_json.params` as
  a fallback for missing original launch parameters.
- `input_json.message` is recorded input text; `input_json.command` is a recorded
  command that may have been reconstructed or trimmed. Neither guarantees the
  original raw, byte-exact request.
- Some adapter paths store a `_clawmind_state` envelope in `params_json` for
  internal state persistence. Summary consumers must not render that envelope
  or its contents as user launch input. Malformed or unsupported JSON is not
  evidence of original input; show it as unavailable rather than guessing.

## Lineage, compatibility, and authorization

Display `parent_flow_id` and `root_flow_id` only from their database columns.
Treat absent fields or SQL nulls as unavailable. Do not infer lineage from the
current flow ID, workflow definitions, input parameters, or runtime state. Older
SQLite schemas without either lineage column still work: the reader omits only
missing optional columns and the readerless full-row detail remains compatible.
This projection introduces no schema or deployment migration. The existing
[engine lineage contract](flow-run-lineage.md) retains its schema requirements.

The safe base projection continues stripping both IDs. For reader-backed
ordinary detail only (inspection exists, not managed, restricted, or sensitive),
`readVisibleRunLineage` authorizes each valid non-null stored ID through the
existing `reader.inspectRun(actor, id)` and requires the returned `run.flow_id`
to match exactly. Equal parent/root candidates share one lookup, with no
recursive ancestry read. Null stays null; absent/invalid IDs are omitted.
Only forbidden/not-found ancestors are omitted independently; other failures
propagate through the existing HTTP error handler. No client query/header can
supply an ancestor ID or opt into this projection.

Readerless/default detail, managed/restricted fanout views, sensitive fanout
children, and lists remain redacted. A child run's permission never grants
ancestor access, including an ancestor from another Bot in the same workflow.
The panel renders encoded, same-origin links only for fields supplied by this
safe projection; it never fetches trusted engine endpoints to bypass redaction.
Navigation retains only `from=workspace` provenance and uses the router's
basename. A link or returned ID is not a permission grant or proof of ancestry;
destination navigation must reauthorize access.

The summary preserves `RunMarkers`, runtime metadata, and all `readOnly` action
protections while keeping launch details visible. Stateful confirmations are
scoped to `run.flow_id`, so cached parent/root navigation cannot carry an armed
delete or abort confirmation into another run or back into the source run.

This additive contract consumes the existing stored fields and reader
authorization API without a broader authentication or host-mount change.
`credentials_json` remains excluded, and the reader does not select credentials.
Runtime metadata remains default-off and follows the verified run-view
authorization constraints in [run-detail-contract.md](run-detail-contract.md).

Regression coverage: `server/routes/__tests__/run-detail-launch-input.test.ts`
uses real HTTP requests to Express with current and legacy in-memory SQLite
fixtures, checking exact launch values, mutable input independence, nulls,
missing lineage columns, readerless/fanout lineage redaction, independently
authorized ancestors, same-workflow cross-Bot/owner denials, read failures, and
credential exclusion. Component and utility tests cover legacy/internal-state guards, semantic surfaces,
encoded lineage navigation, read-only actions, and cached-navigation
confirmation reset.


## Summary presentation

The header groups the existing fields into run identity, execution context,
launch input/lineage, and execution metrics. This is a layout change, not a
projection or authorization change. Workflow/version, creator, originating Bot
and plugin version, runtime metadata, traffic marker (`env`), independent tag,
start/duration, Token usage, and success/failure/total progress remain visible
under the same data conditions. Skipped nodes are not painted as successful.
Input text and stored original launch parameters remain independent readable
previews; authorized parent and root links remain independently available.
The layout preserves all existing action conditions, read-only rules, and
flow-ID-scoped destructive confirmations.
