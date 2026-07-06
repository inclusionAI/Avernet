# BCS Panel Asset

Open-source side-panel components bundled as a UMD asset for BCS.

The BCS manifest exposes the generated bundle at:

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
npm install
npm run build
npm run test:umd
npm run scan:public
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
