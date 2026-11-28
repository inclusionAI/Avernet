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

Both already-joined and newly accepted invitations support these optional URL
query parameters. The verified token target, not the editable path segment or
query, supplies the resource IDs and determines the allowed views.

| Invitation URL path | `view` values | Result |
| --- | --- | --- |
| `/workspace/invite/groups/{token}` | `group`, `full` | `group` opens `/workspace/collaboration/group` for the invited Group. |
| `/workspace/invite/sessions/{token}` | `session`, `group`, `full` | `session` opens `/workspace/collaboration/session` without the session list. `group` opens the parent Group and initially selects the invited Session. Neither view changes membership in the parent Group. |

`view=full` or an omitted, unknown, or unsupported `view` keeps the original
`/workspace/collaboration` destination and target selection. If the verified
target lacks the IDs required to open a focused page, the invitation uses that
same full-workspace fallback. On focused pages, `showReturn=false` hides the
**Return to full collaboration** button; `showReturn=true`, an omitted value, or
an unknown value leaves it visible. The full workspace has no such return bar.
For example, `/workspace/invite/sessions/{token}?view=session&showReturn=false`
opens the invited Session with no session list or return bar.

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
The [focused-page tests](test/pages/Workspace/FocusedCollaboration.test.tsx)
cover the return bar and preservation of its option when changing Sessions.
Sidebar and route regressions are covered by
[invitationSidebarNavigation.test.tsx](test/pages/Workspace/invitationSidebarNavigation.test.tsx),
[useWorkspacePage.deepLinkLocation.test.tsx](test/pages/Workspace/hooks/useWorkspacePage.deepLinkLocation.test.tsx)
and [useGroupSessionLocation.deepLink.test.tsx](test/pages/Workspace/hooks/useGroupSessionLocation.deepLink.test.tsx).

## Group message sender identity

The [group Provider](src/services/workspace/groupChatProvider.ts) normalizes live
SDK messages through [the sender mapper](src/services/workspace/groupLiveMessageMapper.ts)
before delivering updates or completion callbacks, including buffered hydration
events. Existing canonical sender fields take precedence; BCS
`payload.message.from` / `from_name` / `from_avatar` and SDK `senderAvatar` supply
missing `senderId` / `senderName` / `senderAvatarUrl` fields. Routing targets and
the current viewer never supply a missing author ID.

Human names, avatars and right alignment use the same explicit normalized-ID
match against the authenticated Human. An unknown author retains the message's
name/avatar or a neutral fallback; it never borrows the viewer's identity. Local
send echoes and history mapping already supply sender IDs. This keeps same-user
tabs consistent and preserves another Human's attribution in other viewers.

Regression coverage includes the real SDK parser and Provider callbacks in
[realtime identity tests](test/pages/Workspace/components/GroupChatPane/GroupChatBubble.realtimeIdentity.test.tsx)
and unknown-author rendering in
[bubble identity tests](test/pages/Workspace/components/GroupChatPane/GroupChatBubble.identity.test.tsx).

## Other-initiated conversation labels

The read-only “他人发起” list identifies human initiators only from explicit
`user:<id>:` prefixes or trailing `:user:<id>` / `:direct:<id>` segments of
Session IDs. Synthetic member-task and BCS-CLI owner segments are not human
creator evidence. Unknown IDs never fall back to the Bot owner or current viewer.
Each row shows a user avatar/icon and name, falling back to the explicit user ID
or “用户”. History user messages show the matched name/avatar, or “用户” when
no name is available; Bot messages keep the Bot's identity. Legacy friend-based
history retains its explicitly resolved friend name. Viewing remains read-only.

Names use the existing human batch-query API asynchronously, independently of
session/history loading. Batches contain at most 50 unique humans; in-flight
lookups are shared. Viewer-scoped memory caching is bounded to 500 entries with
a five-minute name TTL (30 seconds for missing/failed lookups). Late responses
cannot label a newly selected session or another viewer. No Backend permission,
session filtering, runtime-stage or chat-send behavior changes.

Regression coverage: [profile service](test/services/workspace/sessionInitiatorService.test.ts),
[hook and list](test/pages/Workspace/Chat/hooks/useSessionInitiators.test.tsx), and
[read-only history](test/pages/Workspace/components/ReadOnlyConversationPanel.test.tsx).

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

The conversation sidebar sends `f_user_id=<current Human>` for the current
user's sessions on owned, collaborating and friend Bots. Backend uses that
subject only to select an independent runtime for Caller-mode Bots; owner-mode
Bots retain their shared runtime. The frontend does not call `caller-context`
to choose a chat runtime. `user_id` remains the authenticated actor, `owner_id`
continues to locate the Bot, and service conversations still send `stage=online`.
Old clients that omit `f_user_id` keep the operator-console behavior.

AgentCoding Bots are the engine-specific exception: their mine/others/collab
lists omit `f_user_id`, request raw paginated sessions and classify each session
in the frontend with the AgentCoding ID rules (member-task is `collab`, BCS-CLI
is `others`, a missing creator is `mine`, and recognized other creators are
`others`). This preserves AgentCoding's synthetic identities. For other managed
and collaborating Bots, the “他人发起” view uses Backend
`sessions?source=others`. All views render flat lists ordered by the loaded
sessions' modified time descending (creation time is the fallback). Later
windows are merged, deduplicated by session ID and sorted again; this is not a
global sort over sessions that have not been loaded.

The current user's view keeps ordinary page-number pagination on the selected
subject runtime. “他人发起” source queries keep `page=1`, send the authenticated
Human as `user_id`, the real Bot owner as `owner_id`, and preserve runtime stage,
instance and session-key filters. Continuation uses `next_cursor`, never the
cumulative `total` or the number of returned items. A short/empty scan window
with `has_more=true` exposes “继续加载” rather than a false empty state; no
automatic unbounded scan runs in the browser. Filter revisions and user
generations reject stale responses.

Others remain read-only: history uses the current operator, not `f_user_id` or an
impersonated Owner. Favorites and friend Bots retain their separate existing
flows; old `friend=` deep links retain the legacy friend view. AgentCoding's
visibility policy is applied before its raw sessions are classified. For other
engines, deploy Backend source-window support before this frontend:
missing/mismatched source metadata produces an explicit unsupported error
instead of presenting an unfiltered list as “mine”. See the
[Backend contract](../backend/docs/openapi-v1/engine-surface.md).
## Lab tasks

The [Lab task list](src/pages/Lab/Tasks/index.tsx) searches displayed task titles within
its currently loaded page, not Bot names or all pages. Existing status and Owner
Bot filters remain available. “创建时间” shows `gmt_create`; “更新时间” shows
`gmt_modified` (the last record modification, not task completion). Missing times
display a dash. Lab rows do not claim execution duration from these timestamps;
the default MyTask presentation remains unchanged. Regression coverage lives in
[`Tasks.test.tsx`](test/pages/Lab/Tasks.test.tsx).

## Notification center detail classification

Approval details loaded through `workOrderService.getDetail` retain the approval
identity even when the detail response omits `item_type` or `event_type`, or its
event is not yet in the display catalogue. Notification details loaded through
`notificationService.getDetail` retain their recipient-notification identity,
even when they carry an originating approval event. Classification does not depend
on approval status, read state or the user's current approval permission.
Approval-result notifications display the category label “审批类”, rather than
“审批通知”; this presentation choice does not turn a notification into an
approvable work order or change its read state.
The [shared category tag](src/components/Admin/WorkOrderCategoryTag.tsx) is used
by list rows and detail drawers in every view, including pending and processed.
Approval work orders and approval-result notifications display “审批类”; other
notifications display “通知类”. All category tags use the same neutral semantic
colors, independent of event type, read state or processing status. Approval
status/action colors are unchanged. The shared event catalogue still recognizes
Skill editor requests for domain metadata.

Regression coverage lives in
[workOrderService.api.test.ts](test/services/admin/workOrderService.api.test.ts),
[notificationService.test.ts](test/services/admin/notificationService.test.ts),
[workOrderMeta.test.ts](test/domain/admin/workOrderMeta.test.ts) and
[WorkOrderDetailDrawer.test.tsx](test/components/Admin/WorkOrderDetailDrawer.test.tsx) and
[WorkOrderCategoryTag.test.tsx](test/components/Admin/WorkOrderCategoryTag.test.tsx).

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

## AgentCoding after-create actions

AgentCoding template creation keeps the open-claw-compatible after-create
contract: only the architectural Bot onboarding action is emitted. A template may
declare `afterCreate` or `after_create`, and when the template exposes an
`architect_name` field the frontend adds the fixed `update_architect_bot_ext`
action automatically. The action sends

```http
PATCH /api/public/bots/{bot_id}/ext
```

with `arch_domain` and `is_domain_bot: true` after direct creation or
authorization succeeds. An empty submitted architecture-domain value is ignored.
Only the whitelist actions for the architectural ext request are supported; the
creation flow does not emit workspace or cron initialization. Regression tests
cover action resolution, the ext request, and the authorization-completion path.

## Community followed-author view

The [follow list](src/components/Community/CommunityPersonalCenter/FollowList.tsx)
separates followed Humans and Bots into compact identity cards. Selecting one
keeps its profile and server-supplied statistics in a desktop sidebar beside
its topics. The profile column is 200px and the topic column takes the remaining
width; when the content area is narrower than 640px, they stack. Topic rows omit
redundant author avatars. “返回我的关注列表” restores the identity cards and their
pagination. The community search and publish controls remain visible in “我的”;
Enter on a nonempty topic search opens the topic list.
The [topic header](src/components/Community/CommunityTopicDetail/TopicDetailHeader.tsx)
places delete/close controls on the title row below the breadcrumb. The detail
header omits topic tags and the decorative poll icon; status and poll results
remain visible. Delete confirmation uses the primary “确定” button; cancel does not delete.
Regression coverage is in [PersonalCenter.test.tsx](test/components/Community/PersonalCenter.test.tsx)
and [CommunityTopicDetail.test.tsx](test/components/Community/CommunityTopicDetail.test.tsx).

## Community replies

[Topic details](src/components/Community/CommunityTopicDetail/index.tsx) load
adjacent batches at the scroll edges instead of showing page-navigation buttons.
Previously loaded floors stay visible. A link to a specific reply opens its
batch, and scrolling up can load earlier floors without displacing the current
reading position. A failed batch can be retried without losing loaded floors.
The community's top-level tabs reuse the Bot Management tab component.
The reply counter uses the topic's total (including nested replies) when known.
With no cached total it counts the server's root-floor total plus loaded nested
replies. A successful direct or nested reply increments the displayed count;
failed submissions do not change it.
The interaction bar is outside the scrolling area. Closing replies does not
hide likes, favorites or the reply count. Notice topics have no extra badge in
the detail header and support direct replies, not nested replies.
“我的回帖” lists the signed-in user's replied-to topics using the same row as
“All Posts”. Browsing History and the other activity lists use the same
borderless row layout. “我的回帖” collapses multiple replies to the same topic on each activity
page; the activity API still paginates reply records. Rows open the topic itself.

## Optional Coding Bot restart preparation

`AppCapabilities.prepareCodingBotRestart` is an optional asynchronous preparation
hook for ordinary cloud Coding Bot restart. It receives bot, owner and caller
identities and returns extra restart configuration; rejection prevents submission.
A deployment that returns a `template_config` must also provide
`submitCodingBotRestart`, which routes the engine-owned `extra_configs` envelope to a
backend contract that persists it before restart. Rejection aborts without a
fallback restart. Both hooks must not poll themselves, bypass identity permissions,
or target published service instances. Local restart, non-Coding engines,
engine-only refresh and restart-publication are excluded. Without an extension,
confirmation-only restart behavior is unchanged. See
[capability types](src/capabilities/types.ts) and
[workshop service](src/services/botWorkshop/botWorkshopService.ts).

## RSI final strategy output

The selected Run in the RSI side panel separately loads
`GET /openapi/v1/bots/{bot_id}/evolution/runs/{run_id}/strategy-output` through the
shared authenticated Backend client. It displays the redacted final rationale,
proposed file replacements and expandable final response text (never rendered as
HTML/Markdown). Proposed changes do not imply a submitted candidate, passed
verification or Apply. Missing historical output and read errors are distinct.
The current attempt refreshes while running and is fetched once again on terminal
transition; terminal output is not downloaded on every Run-list poll. Bot/Run keyed
mounting discards late responses from a different scope. Server schema and
availability follow the [Evolution contract](../evolution/src/avernet_evolution/core/evolution_run/README.md#protected-final-strategy-output).

## Genome baseline reconciliation and candidate Diff

The RSI side panel's [baseline manager](src/components/rsi/GenomeBaselinePanel.tsx)
uses the existing authenticated Genome API, separately from RSI candidate review.
Opening or refreshing reads refs, live drift and recent promotions; it does not
capture or change a Bot. A missing `active` is distinct from a null drift result.
An explicit capture records the current **whole Genome** and previews its Diff
against the exact active revision, without moving refs. Existing content keeps
its revision status: capturing a candidate or rejected revision cannot turn it
into a promotable owner draft.

Reconciliation requires a reason, confirmation of the full snapshot and the
server's Owner/edit-lock permission. Before POST, the UI rechecks active, observed
content and in-flight promotions; it sends the reviewed hashes as target and CAS
expectation. This reduces stale confirmation but is **not an atomic runtime-file
lock**. Pause chat/other edits while reconciling. The existing promotion service
runs Apply and settles refs; `applying`, `applied_partial` and `apply_failed` are
not presented as successful reconciliation. Even `applied` is rechecked against
active, partial labels and current drift. Nothing automatically reruns RSI.

Promotion POST has no idempotency guarantee. The client never replays it and
stores only uncertain target/expected hashes, a random operation correlation ID
and a returned promotion ID in
session storage, scoped to actor/owner/Bot. Closing/reopening the panel or
refreshing the tab retains that marker. Unknown outcomes block a new submission;
a generated `[client-operation:…]` tag is appended to the audit reason (not an
idempotency guarantee). Users may explicitly track a recent promotion matching
that tag and both hashes, then read its final state. An older same-target
promotion is not a receipt for this operation.
An absent record in the latest 20 entries does not prove failure: unresolved
outcomes require server-side investigation, not resetting the marker or retrying
from another tab. Credentials, reasons, configuration and captured content are
not persisted by this store.

[Candidate reports](src/components/rsi/RsiReview.tsx) load the actual Genome Diff
between the recorded candidate and its immutable parent hash. This is independent
of the strategy's final output/proposed replacements. File paths and Diff text
are rendered as text; missing textual hunks and read errors are not treated as
no change. Neither Diff viewing nor baseline reconciliation is a behavior-quality
verdict, and neither enables RSI candidate Apply or bypasses its gates.

Regression tests: `npm test -- --runInBand test/components/rsi test/services/rsi`.

## Audited human RSI intervention

Candidate details expose **human authorization → explicit Apply** when the server
reports the capability. Inspect Diff, enter a reason and confirm authorization;
this records `basis=human`, preserving the verdict and leaving the Bot untouched.
A second confirmation applies that exact candidate through Backend. The original
authorizer must still have ADMIN access. Reject/known safety failures, drift,
stale parent and platform freeze/kill cannot be overridden.
The UI keeps one authorization request/key after a lost reply, reads authoritative
state on refresh and polls delivery without automatically replaying writes.
Run submissions, risk reviews and manual authorization use the same 128-bit
`crypto.getRandomValues` request-key generator, including HTTP deployments where
`randomUUID` is unavailable. Random-source failure is reported as unsent; it
never falls back to weak randomness. After POST, unknown outcomes still freeze
the authorization key and body for same-request recovery.
Pending/applying, applied, applied_partial and apply_failed are distinct; partial
is not full acceptance. This intervention does not implement sandbox benchmark
quality evaluation. Record-only risk-review approval remains separate.
See [ADR 0027](../../docs/adr/0027-human-promotion-authorization-is-not-evaluation.md)
and `test/components/rsi/RsiManualPromotion.test.tsx`.


## RSI submission recovery

Run submission keeps the same idempotency key and binding after an uncertain
reply, including network failures, 5xx and unclassified 422 responses. Reopening
or refreshing the panel does not abandon that request. Only HTTP 422 with the
exact domain code `binding_check_failed` releases the matching pending submission:
this validation rejection precedes Run creation. The UI shows a safe instruction
to check registered strategy versions and binding configuration, then allows
editing again. It does not automatically migrate bindings or change the baseline.
An existing pending request can be retried with its original key to receive this
definite rejection; a late response cannot clear a different pending key.
See [`RsiPanel.test.tsx`](test/components/rsi/RsiPanel.test.tsx) and
[`errors.test.ts`](test/services/rsi/errors.test.ts).
