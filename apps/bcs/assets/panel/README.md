# BCS Panel Asset

Open-source side-panel components bundled as a UMD asset for BCS.

## Install From npm

```bash
npm install @avernet-assets/bcs-panel
```

The prebuilt UMD bundle is installed at:

```text
node_modules/@avernet-assets/bcs-panel/dist/index.umd.js
```

## Load From CDN

Public npm releases are also available from npm-backed CDNs. Production
configurations must pin an exact package version so that deployments are
reproducible and can be rolled back safely:

```toml
[[manifest.bundles]]
name = "bcsPanel"
type = "url"
url = "https://cdn.jsdelivr.net/npm/@avernet-assets/bcs-panel@<exact-version>/dist/index.umd.js"
```

Do not use an unversioned URL or the `latest` dist-tag in production.

## Load From a Local Build

The BCS manifest can expose a locally generated bundle instead:

```toml
[[manifest.bundles]]
name = "bcsPanel"
type = "file"
file = "assets/panel/dist/index.umd.js"
```

The chat renderer opens components with names such as:

```tsx
<AixUI
  type="panel"
  component="bcsPanel.StateMachineRunView"
  params='{"runId":"sm-example"}'
/>
```

### Collaboration definition preview

The bundle exports the inline `CollaborationDefinitionCard` and the side-panel
`CollaborationDefinitionPreview`. A producer should emit the stable component
name through the BCS CLI instead of hand-building the escaped payload:

```bash
bcs-cli collaboration draft create workflow.yaml --draft-id "$draft_id" --title "Release review"
```

The CLI returns a short `message` using `bcsPanel.CollaborationDefinitionCard`.
Schema 2 contains only `schemaVersion`, `draftId`, `revision`, and `readToken`.
The card loads no YAML until opened. The existing host bridge and Agent resource
binding supply `bcsPanel`; no frontend registration changes are required.
The preview uses the host's `apiBaseUrl` or `baseUrl` (top-level props take
precedence over `data`, including the `params.data` / `payload.data` envelopes).
The card forwards an explicitly configured base when opening the preview;
otherwise the side-panel host can inject its own base. Only the preview falls
back to `/bcnproxy` when no host base is supplied. Requests append
`/collaboration/drafts/...` or `/collaboration/definitions/validate` to a BCS root
base; a base already ending in `/collaboration`, such as `/api/v1/collaboration`,
uses `/drafts/...` or `/definitions/validate` without duplicating the namespace.
Host configuration is separate from the strict four-field reference and must
expose the requested API; configuring a base does not mount service routes.
The fixed revision is read with
`X-BCS-Draft-Read-Token`, rejects redirects, and verifies UTF-8 length and SHA-256.
Validation requests use the stored version, never a substituted source body.
Validation outages preserve already verified source for copying. References are
transferable and revocable; reopening/refreshing reauthenticates without a
persistent cache. Tokens never enter tab IDs or URLs.

Opening a card selects **协作预览**. **查看定义** shows the read-only original;
**复制源码** appears beside **协作定义 YAML** only in that tab and copies the
verified original. YAML syntax highlighting uses only the YAML grammar and renders
React text nodes, preserving indentation, line endings and literal markup.
Successful validation appears inline with the header's version
information. Changing the fixed revision returns to the preview tab. Both card
versions fill their host width, including the preview button.

Loop previews show the logical structure used by group creation: one body per
Loop, a return edge, iteration limits, and distinct exit branches. Nested Loops
retain their containers. The diagram fits the panel width and supports zoom;
the projection does not change the stored YAML or validation summary.

Historical schema 1 snapshots and `collaboration preview <file>` retain their
read-only behavior. Opening a preview never creates groups, drafts, or Runs.

### Open the host's group creation modal

Schema 2 previews offer a primary **发起协作** button in the upper right. An explicit click calls the trusted
host's generic `onOpenCreateGroup({ draft: reference })`, where `reference` has
exactly `{schemaVersion: 2, draftId, revision, readToken}`. Other panels can call
`onOpenCreateGroup()` to guide ordinary group creation without a Draft. The event
only opens configuration; it never submits. The BCS Panel owns preview and source
copying, not a separate creation form or Human API client. Schema 1 stays read-only;
a missing callback leaves the preview usable with an integration notice.

The current `apps/frontend` UMD host injects this callback for panel components,
checks the options, and lazy-loads its existing `CreateGroupModal`. Independent
`selectOriginator` and optional `initialDraft` props control owned-Bot selection
and initial content. Without them, the normal entry retains its current identity.
With a Draft, the host verifies the fixed revision's bytes/hash and seeds the
existing free editor once, then automatically validates and expands role bindings
and the logical Loop preview. Draft entry fixes the state-machine group type and
hides type/template switching. Users can still choose **重新编辑** to edit and
revalidate YAML. Entries without a Draft retain all group types and templates.
Editing invalidates the prior validation and bindings; it does not write a Draft.

Owned Bots come from `/bcnproxy/collaboration/user`, independently of the current
chat Bot. Candidates use the selected originator's perspective. Switching that
Bot clears members and bindings; searching or paging preserves selections.
The same `CreateGroupParams` feed normal and Human creation. Submission snapshots
the current form, rechecks the Human, and validates the **current edited YAML**
for state-machine groups. It never reloads a Draft over edits. YAML bytes and
bindings travel separately; read tokens are only used for the initial read.

**建群并开始协作** explicitly starts a state-machine group. The other group types
use **创建协作群**. Unknown outcomes block repeat submission. Closing/reopening the
same modal preserves edits and submission state; refreshing or closing the panel
has no persistent recovery guarantee. A later share revocation cannot withdraw
text already read into the local editor.

`apps/frontend-nextgen` has not adopted this callback or editable modal parameters.
Its API bases, identity, navigation and acceptance work remain in the
[group-launch roadmap](../../specs/2026-10-22-collaboration-draft-group-launch/roadmap.md).
No API base, navigation target or user identity is accepted from message options.

For a failed Run, the panel shows a **Rerun** action. It sends an empty
`POST /state-machine-runs/{run_id}/reruns` request. The source panel remains on
the source Run; presentation of the child is driven by the new Run's opening
message (the default panel opening creates an independent tab). Repeating the
action for the same source follows the same response path, so the panel does
not need to generate an idempotency key. Hosts that provide `onInteraction`
receive `{ type: "rerun", run }` after a successful response.

## Development

```bash
npm ci
npm run verify
```

`dist/index.umd.js` is ignored by git. Build it before starting BCS when using
the local `file` bundle config above. BCS reads the file at runtime and exposes
it from:

```text
GET /assets/bcsPanel/index.umd.js
```

The bundle treats `react` and `react-dom` as host-provided globals and bundles
the panel implementation dependencies needed at runtime.

`test:umd` checks the public component exports, including
`StateMachineRunView`, `UndercoverGamePanel`, `CollaborationDefinitionCard`, and
`CollaborationDefinitionPreview`. The collaboration integration test also
checks the shared snapshot vectors, click-to-open lifecycle, UMD tab payload,
UUID v7 compatibility, repeated-tab focus, expanded Loop forward-edge layout,
and validation rendering.

## Publishing

Releases are published by GitHub Actions when a maintainer pushes a tag named
`bcs-panel-v<version>`. The tag version must exactly match the `version` in
`package.json`.

Configure `@avernet-assets/bcs-panel` with the `publish-bcs-panel.yml` trusted
publisher on npm. The workflow uses OIDC and does not require an npm token.

Review the package contents locally before opening a release pull request:

```bash
npm ci
npm run verify
npm pack --dry-run
```

The `prepack` lifecycle also runs verification before the GitHub Action creates
or publishes the package archive. See [PUBLISHING.md](PUBLISHING.md) for the
OIDC configuration, versioning, tag, verification, and troubleshooting steps.

## Undercover game panel

The same `bcsPanel` bundle also exports `UndercoverGamePanel`. The referee uses
`--panel-component bcsPanel.UndercoverGamePanel` with the current game session
and a stable, non-closable tab across speech, voting, retries and later rounds.
The default `bcsPanel.StateMachineRunView` remains available. See
[game parameters, interactions and preview](UNDERCOVER_GAME.md).

The local and example manifests serve `assets/panel/dist/index.umd.js`; build
this bundle before starting BCS locally. The production manifest retains its
existing CDN version 1.2.0, which does not include the game panel. To enable the
game in a CDN deployment, publish version 1.4.0 or later of this package before
switching the manifest URL and referee together. Previously stored messages using
the retired component name need a new panel submission from the referee. Game
state and HTTP contracts are unchanged.


### Fixed Loop runs

When the graph includes `loops` descriptors, `StateMachineRunView` defaults to
a logical Loop container with one copy of the body, a dashed continuation edge,
and break/exhausted exits. Each Loop follows the current iteration by default;
selecting a historical iteration pins it across refreshes, and “回到当前执行” resumes
following. History selection and the expanded graph include only entered executions;
a never-entered Loop shows one structural body, with no history selector. Skipped
future nodes are excluded even when they have a completion timestamp.
Unentered outer branches are also omitted from execution history; the logical
view retains the complete workflow structure. Older responses
without descriptors retain their expanded layout.

Nodes show their task names; only the outer container shows `Loop #N`.
The bottom-right role badge and node detail prefer `assignee_display_name` from
the Run graph (the saved participant's `display_name`), falling back to the binding
ID. Long role names use an ellipsis; the badge tooltip retains the full name and ID.
Route labels default to `continue` or the logical outcome, such as `approved` and
`exhausted`. An edge's optional `display_name` overrides its label; logical returns
use the Loop descriptor's optional `continue_display_name`. Authoring sets these
through `transitions.<outcome>.display_name` and `loop.continue_display_name`.
The exhausted edge uses its outer exit's name while retaining the actual continue
outcome. Tooltips show the original outcome, and edge selection never uses names.
Names come from the saved Run snapshot, including on rerun. Both logical and
expanded Loop graphs fit narrow panels. `max_iterations` is available in hover text. The writing/review
example sends approved drafts to a separately bound copy editor, exhausted
drafts to a rewrite, then joins the selected branch into a final summary.

The selected body shows actual per-iteration node statuses and retry attempts.
Exhaustion is a normal exit; early break marks later iterations unexecuted.
It selects edges using the saved source outcome, including branches that share
a target. Node details show the exact execution ID; Human input shows trusted
LoopContext separately and submits to the pending execution ID. V1 runs do not
show empty Loop sections.

The shared API fixture drives `test/fixed-loop.mjs`, included in `npm run verify`.
For visual checks, run the local Vite dev server and open
`/test/fixed-loop-preview.html`; the selector covers the two-node writing/review
Loop, exhaustion, early break, and first/later Human input. Add `&names=1` to
`?scenario=writing` to inspect custom edge names in both graph modes. This fixture page
does not submit real responses. Integration tests also cover pinned history,
current execution detail IDs, single-execution loops, retry/cancel, 100-execution
limits with only 5/10 entered executions, and v1 compatibility.
