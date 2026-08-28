# Avernet Frontend Nextgen

The next-generation Open Core web frontend for Avernet. It is generated from the TeamClaw Open Core source and intentionally coexists with the legacy `src/frontend` application.

## Requirements

- Node.js 20 or newer
- npm with access to the public npm registry

## Development

```bash
npm ci
TEAMCLAW_GW_BASE=http://127.0.0.1:8888 npm run dev:local
```

OAuth uses the BCS `/auth/*` routes during the compatibility phase. See [deployment documentation](docs/deployment.md) for production configuration.

## Dependency exception

`@tc-chat/ui@2.0.0` and its `@ant-design/x` dependency require the same `antd:^6.1.1` peer line. The project pins `antd@6.6.1` as an SDK-only direct exception; application code must not import `antd`. Re-audit this exception whenever `@tc-chat/ui` is upgraded.

## Verification

```bash
npm run check:open-core
npm run ci
```

## Source synchronization

`OPEN_CORE_MANIFEST.json` records the TeamClaw source commit. Changes originating in this public directory must be taken back into the TeamClaw Open Core baseline before the next export.

## License

Apache-2.0. See the Avernet repository root license and `LEGAL.md`.
