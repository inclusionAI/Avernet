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
