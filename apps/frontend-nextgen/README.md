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

## Group and Session invitation links

The [invitation landing hook](src/pages/Workspace/hooks/useInviteAccept.ts) first
calls the authenticated, read-only [BCS invitation preflight](../bcs/api-contracts/v1/openapi/invitations.yaml).
BCS determines whether the signed-in Human is already a participant of the exact
Group or Session. Existing members navigate directly using the returned target
and parent Group IDs and explicitly select the authenticated Human identity, not
the previously active Bot. Invitation identity resolution only observes existing
auth/capability/identity-store state; it never starts a mine/identity-loading
request, which can create Human actors. A late global authentication result
resumes navigation. While identity initialization is pending, the landing page shows
a loading state rather than an error; unresolved initialization times out after ten
seconds. The invitation history entry is replaced. Other users
still explicitly confirm joining and select a message visibility scope.

Preflight never accepts an invitation or infers membership from public visibility,
Bot management or successful detail access. Expired links remain invalid.
Authentication failures are distinct from invalid invitations; transient preflight
failures offer retry rather than an unchecked confirmation. The request renders
errors inline while leaving shared login/authentication gates enabled. A selected
Group absent from the current sidebar page/filter is resolved by ID through the
existing selected-Group detail fallback, including Session-only membership; list
absence alone never changes the participation filter or discards the requested Session.

An explicit Session deep link issues one sidebar-location request after route
identity hydration. Once the target Group and Session details are verified,
location clears obstructing sidebar filters, displays an out-of-list target,
expands the sidebar and scrolls to the Session. Once the selected Group detail
is available, its classification is confirmed by a matching entry from the
selected View Actor's paginated Group lists: the public target-independent Group
detail does not itself report `membership`. Location aligns the participation
filter with that explicit list value (`direct` or `session_only`) while preserving
the selected Session. List absence is never treated as evidence for the opposite
category. Classification-query failures offer a retry rather than guessing. List
readiness is scoped to both identity and participation filter, so a previous
category's Groups are not shown under a newly selected category during reload.
This display-only operation never joins the parent Group, changes actual Group
membership, or modifies paging/history state. It
survives transient detail reloads while the Store selection is unchanged; user
selection, newer filters or dismissal cancel even a still-pending request. A
settled target-verification failure removes the cached overlay. Internal Store-to-URL projections do
not replay location or remount the workspace. A canonical `human_` target waits
for the matching real Human in the identity list instead of falling back to a
Bot or placeholder identity. If route initialization cannot finish within ten
seconds, the page offers explicit refresh and return actions instead of loading
indefinitely under a missing identity.

Regression coverage is in [invitationService.test.ts](test/services/workspace/invitationService.test.ts),
[useInviteAccept.preview.test.ts](test/pages/Workspace/hooks/useInviteAccept.preview.test.ts),
[useInviteAccept.readonlyIdentity.test.ts](test/pages/Workspace/hooks/useInviteAccept.readonlyIdentity.test.ts)
and [InviteAcceptPanel.test.tsx](test/pages/Workspace/InviteAcceptPanel.test.tsx).
Sidebar and route regressions are covered by
[invitationSidebarNavigation.test.tsx](test/pages/Workspace/invitationSidebarNavigation.test.tsx),
[useWorkspacePage.deepLinkLocation.test.tsx](test/pages/Workspace/hooks/useWorkspacePage.deepLinkLocation.test.tsx)
and [useGroupSessionLocation.deepLink.test.tsx](test/pages/Workspace/hooks/useGroupSessionLocation.deepLink.test.tsx).

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

## Legacy friend-user deep links

New managed/collaborating Bot source lists use the flat source-window flow below.
Existing `friend=` deep links retain the managed Bot friend-group flow:
friend users load under the Bot's composite actor ID, and session/message reads
retain the owner context, normalized friend ID, pagination and runtime stage.
Other-user browsing is read-only; it does not enable session writes, a composer
or a realtime chat connection. Backend authorization still applies.

Managed and collaborating Bot other-user directories batch-query
`POST /openapi/v1/collaboration/bots/query` with `human_<userId>` identities after
relation pagination and deduplication. Nonblank Human `name` values replace
numeric labels without changing user IDs or directory order. Missing names or
lookup failures preserve existing relation names, falling back to the user ID.
No employee-directory or legacy-frontend dependency is used.

The other-user directory excludes the signed-in viewer by normalized Human ID,
not by name or Bot ownership. Both fresh responses and reused directory caches
apply this exclusion. A self-only friend result is an empty other-user list;
stale expanded self groups do not trigger other-user session reads.

Other-user group rows follow the compact sidebar density: 13px regular names,
24px circular neutral user icons, 40px rows and 8px avatar/name spacing. Loading
skeletons mirror the same geometry; hierarchical indentation and read-only
session interactions are unchanged.

Regression coverage lives in
[`managedBotConversationService.test.ts`](test/services/workspace/managedBotConversationService.test.ts),
[`teamBotSessionRequests.test.ts`](test/services/workspace/teamBotSessionRequests.test.ts),
[`ConversationSidebarSessionFilters.test.tsx`](test/pages/Workspace/Chat/components/ConversationSidebarSessionFilters.test.tsx),
[`ConversationFriendGroup.test.tsx`](test/pages/Workspace/Chat/components/ConversationFriendGroup.test.tsx),
[`useManagedBotOthers.test.tsx`](test/pages/Workspace/Chat/hooks/useManagedBotOthers.test.tsx)
and the managed/team read-only cases in
[`ConversationPage.test.tsx`](test/pages/Workspace/Chat/ConversationPage.test.tsx).

## Group session file preview

The group file panel previews text files through a sandboxed iframe at the
existing `show=true` content URL. A backend may redirect that URL to a
presigned storage address, so reading it with browser JavaScript would require
CORS on the final storage response. Direct browser navigation renders
`text/markdown`, `text/plain`, and other inline text responses without a
cross-origin body read. The iframe grants no scripts, same-origin permissions,
popups, or downloads; users can still download from the panel toolbar. Server
`Content-Type`, `Content-Disposition`, and framing policy determine whether a
particular response renders inline. Group Markdown displays as raw browser
text and JSON is not reformatted; no client-side size limit is applied.

The shared [file preview classifier](src/services/workspace/filePreview.ts)
recognizes common documentation, configuration, source-code, log, structured
text, and conventional extensionless files by name or explicit text MIME
(including JSON/XML suffixes). Preview recognition does not change the upload
allowlist. The Bot preview still renders supplied text through the existing
Markdown/plain-text UI; image, video, PDF and HTML paths are unchanged.
Regression tests cover
[format classification](test/services/workspace/sessionFilePreviewKind.test.ts),
[group URL state](test/pages/Workspace/hooks/useSessionFilePreview.test.tsx), and
[shared preview rendering](test/pages/Workspace/components/GroupChatPane/SessionFilesPreviewPane.test.tsx).

## Task Escort read-only workspace

**任务护航** is a separate management entry immediately above **智能进化**.
It reads real workflow, execution, issue, repair, experience and management
resources without changing the existing Evolution pages. Task Escort domain
requests are GET-only; write actions stay unsupported, and read failures never
fall back to demo data. Fixture modules are not reachable from the formal page.
See the [approved feature scope and validation handoff](specs/2026-11-18-task-escort-readonly/spec.md)
and its [file ownership manifest](specs/2026-11-18-task-escort-readonly/ownership-manifest.json).
## Conversation session origins

The conversation sidebar uses Backend `sessions?source=mine|others` for managed
and collaborating Bots. Both sources render flat lists ordered by the loaded
sessions' modified time descending (creation time is the fallback). Later
windows are merged, deduplicated by session ID and sorted again; this is not a
global sort over sessions that have not been loaded.

Source queries keep `page=1`, send the authenticated Human as `user_id`, the
real Bot owner as `owner_id`, and preserve runtime stage, instance and session-key
filters. Continuation uses `next_cursor`, never the cumulative `total` or the
number of returned items. A short/empty scan window with `has_more=true` exposes
“继续加载” rather than a false empty state; no automatic unbounded scan runs in
the browser. Filter revisions and user generations reject stale responses.

Others remain read-only: history uses the current operator, not `f_user_id` or an
impersonated Owner. Favorites and friend Bots retain their separate existing
flows; old `friend=` deep links retain the legacy friend view. Coding Bot
visibility policy remains applied without reclassifying Backend source results.
Deploy Backend source-window support before this frontend: missing/mismatched
source metadata produces an explicit unsupported error instead of presenting an
unfiltered list as “mine”. See the [Backend contract](../backend/docs/openapi-v1/engine-surface.md).

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

## TaskEscort sidebar

The [TaskEscort navigation](src/pages/TaskEscort/Navigation.tsx) uses 14px labels,
12px group headings, 20px icons and 44px rows. On desktop its right edge is a
focusable separator: drag right to grow, use Left/Right for 10px steps, or
Home/End for the 220–360px bounds (264px default). Width is stored locally under
`task-escort.sidebar-width`; unavailable storage leaves resizing usable. The
editor retains its 72px compact rail and mobile retains horizontal navigation
without a resize handle. No global selection styles are changed during dragging.
Regression coverage: [sidebar.test.tsx](test/taskEscortWorkspace/sidebar.test.tsx).
