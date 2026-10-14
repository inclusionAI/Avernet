# Avernet Frontend Nextgen

The next-generation Open Core web frontend for Avernet. It is generated from the TeamClaw Open Core source and intentionally coexists with the legacy `apps/frontend` application.

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

### QwenWork external Bot onboarding

The external Bot dialog includes a QwenWork tab with four steps: download the
version-pinned bridge installer and checksum file, register with a provider-scoped
plugin token, initialize local settings and fill the printed WeCom channel information, then
start the bridge and check connectivity. No shell commands run inside the browser.
Download and install are separate commands; SHA-256 is verified before the
installer executes. The recommended version is explicitly shown as
`0.1.4-beta.2`. Users can open the release page for other versions. The page does
not query the GitHub API or claim that its pinned version is the latest.
Maintainers update the public distribution links after validating a new release.

Deployments can override `getQwenWorkBridgeInstallation` with an installation
command and executable. The panel then replaces the release download instructions
and uses that executable for registration, initialization, start/status and stop.
The default is `null`, preserving the public GitHub Release installation flow.
Optional `profiles`, `guideUrl` and `personalizeBotName` settings enable isolated
environment configurations, a required channel-setup document step and a default
Bot name based on the signed-in user. Internal values stay in deployment overlays.

`getQwenWorkBotRegistrationConfig` supplies the deployment-specific provider slug
(enabled explicitly through `QWENWORK_PROVIDER_SLUG`) and API URL. Generic Open Core
builds leave this capability unsupported. Use the minimal validation profile in
[`singlebox/QWENWORK.md`](../../singlebox/QWENWORK.md) to provision the Provider,
authorize ordinary-user registration and start the four required services. The generated command uses the public bridge's
`static-bearer` resolver and explicit BCS endpoints. It writes to a separate
`~/.bcn-bridge/qwenwork.toml` by default. One command runs registration,
initialization and daemon startup in order, stopping if any stage fails. Users
then configure and enable the desktop channel with the printed connection values.
The working directory is controlled by the desktop app, so the panel does not
accept a project directory or emit `--cwd`. Initialization owns secrets,
database detection, backups and Hook merging. The recommended beta.2 distribution includes `init`; its native archives and
installer initialization smoke were verified before updating download links. The local health endpoint
checks QwenWork connectivity; the external Bot list reports BCN reachability.

The deployed container entrypoint writes optional `QWENWORK_PROVIDER_SLUG` into
`runtime-config.js`. Runtime values (including an explicit empty value disabling
onboarding) take precedence over build defaults. WebSocket registration preserves
reverse-proxy endpoint prefixes. Use the `Check:` URL printed by initialization
for health probes after choosing a custom listener.

The workspace now prepares beta.3 for optional failure-Hook migration and deinit.
The verified download pin remains beta.2 until beta.3 is actually published and
its platform archives/installer pass release gates; then update all pinned links.

Closing the registration guide reloads the external Bot list and reachability.
OpenClaw setup separates Bot type selection from terminal installation versus
sending onboarding instructions to the Bot.
