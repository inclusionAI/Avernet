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

## Discovery public Bots

The [public Bot cursor](src/services/collaborationSquare/publicBotCursor.ts)
requests BCS candidates with `limit=20`. This is independent of the UI's 24-item
batch: filtering and lookahead may require multiple requests, and the next page
resumes from the original BCS offset without dropping the lookahead item.

The “我的 Bot” badge compares the signed-in user's ID with the candidate's
`owner_user_id`, not `bot.created_by`. Older services may omit `owner_user_id`;
the frontend then leaves ownership unconfirmed. When Backend enhancement is
enabled and a matching metadata record supplies a nonblank `owner_id`, that ID
takes precedence, including when it clears a candidate's ownership flag. Missing
metadata or an unavailable enhancement service preserves the candidate-based
judgment; authentication failures still propagate. Owner names are independent
of this check. The shared mapping also applies to discovery search results.

Regression coverage lives in
[`bcsBotMapper.test.ts`](test/domain/collaborationSquare/bcsBotMapper.test.ts),
[`publicBotMetadata.test.ts`](test/services/collaborationSquare/publicBotMetadata.test.ts)
and [`publicBotCursor.test.ts`](test/services/collaborationSquare/publicBotCursor.test.ts).

## Discovery public groups

The [public group card](src/components/CollaborationSquare/GroupCard/index.tsx)
shows the driver Bot, not the Owner user (the public directory does not return
that user). The member dialog still uses Owner information from group details.
[Member loading](src/services/collaborationSquare/collaborationSquareApiAdapter.ts)
preserves each participant's `avatar_url`. The
[member dialog](src/components/CollaborationSquare/GroupMembersModal/index.tsx)
uses that avatar when present; Bots without one use the shared
[Bot avatar resolver](src/domain/botAvatar.ts), seeded by participant `actor_id`,
to match Bot management. Human members without an avatar retain their initial.
Regression coverage lives in
[`GroupMemberAvatar.test.tsx`](test/components/CollaborationSquare/GroupMemberAvatar.test.tsx)
and [`GroupOwnerDisplay.test.tsx`](test/components/CollaborationSquare/GroupOwnerDisplay.test.tsx).

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

## Evolution pages

The [evolution pages](src/pages/EvolutionFactory/index.tsx) keep the existing
space-scoped API and task controls. Task filters expose only supported category
and status values. Missing scores remain unassessed; missing deltas are not
replaced by zero. Bot avatars use the shared deterministic fallback when the
version data has no custom avatar. The version Bot column shows its current
space-directory name above the Bot ID, not the owner ID. Names are matched by
owner and Bot ID across directory pages; unavailable names fall back to the Bot
ID without blocking version display. Version search also matches these names.
The version list exposes the source task as
its own column (name when returned, otherwise task ID), linking to task details.
Version-source filters use `initial`, `round`, and `snapshot`; Review results
remain table content, not a filter. Task targets show the directory Bot name
followed by the object type `(Bot)`, with the real Bot ID as the name fallback.
Text search also matches the returned source task name. Version generation time
(`createdAt`) and raw `reviewStatus` / `specVersion` are visible table columns;
missing values display a dash rather than inferred time or review results.

[Domain details](src/pages/EvolutionFactory/DomainDetail.tsx) separate owner,
Domain ID, description and template count; long titles and metadata retain
full text on hover and keyboard focus. Missing detail counts use the loaded
template list length, not a fabricated count. Associated runs reserve space
for decimal scores and keep numeric text inside its own table cell.

[Domain templates](src/pages/EvolutionFactory/DomainTemplates.tsx) paginate the
returned template list, keep draft selections across pages, and preserve
read-only and published-template restrictions. The diagnosis form uses three equal columns for model and case counts, with
a full-width focus field. Exact-Session mode keeps the case-count controls
visible but disabled because those values do not participate in that request.
Task details use a centered content column; task-specific versions remain available in the expandable
section below the execution information. Artifact cards still open the full
output from the selected real step, rather than synthetic report content.

Presentation and interaction regression coverage lives in
[`test/evolution`](test/evolution), including template pagination, task actions,
score display and output inspection. Run `npm test -- --runInBand test/evolution`
in a configured frontend workspace.
