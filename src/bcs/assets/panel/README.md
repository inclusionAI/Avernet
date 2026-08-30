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
