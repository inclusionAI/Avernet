# BCS Panel Asset

Open-source side-panel components bundled as a UMD asset for BCS.

## Install From npm

```bash
npm install @avernet-assets/bcs-panel@1.2.0
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
url = "https://cdn.jsdelivr.net/npm/@avernet-assets/bcs-panel@1.2.0/dist/index.umd.js"
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

Review the package contents before publishing a public release:

```bash
npm pack --dry-run
npm publish --access public
```

The `prepack` lifecycle runs type checking, creates a fresh UMD bundle, verifies
its runtime export contract, and scans the public package content before either
command creates the package archive.
