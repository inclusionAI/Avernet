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

`test:umd` checks that the generated bundle exports `StateMachineRunView`,
which is the entry name used by `bcsPanel.StateMachineRunView`.

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
