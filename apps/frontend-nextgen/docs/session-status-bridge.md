# Embedded session status bridge (v1)

## Boundary and compatibility

This optional callback contract is a host Plugin API: an embedded conversation
surface publishes read-only presentation data to its host. It is not a runtime
command or a new polling channel. The producer retains all existing polling
conditions, intervals, visibility handling, and session-switch timer behavior.
Old hosts/children safely omit the optional props.

The contract is `sessionStatusProtocol.ts`. `sessionStatusContextId` is a host
subscription generation; `onSessionStatusEvent` receives `version: 1`, the captured
context ID, real Bot ID, positive request-order sequence and either `reset` or
`patch`. A patch contains exact session IDs plus `{code, label, tone}` or `null`.
Labels are plain text; no tokens, paths, message bodies, CSS or HTML cross this
boundary. Unknown/unscoped statuses are null; only explicit idle means idle.

## Lifecycle

Context follows the full runtime identity (user, Bot/owner, stage/publication and
reload), not session selection or sidebar expansion. Capture identity and sequence
before awaiting a request. Duplicate, older, malformed and disposed events are
ignored. Callback exceptions cannot affect the producer's existing local updates.
Capability disabled emits reset; failed/skipped requests emit nothing.

A v1 patch can set `snapshot: true` only after the child's first successful
`/api/aicoding/sessions` status query. The ordinary session-list response used to
populate conversations is treated as a readiness gate, never as the status source.
After that list is ready, the child may make one additional early call to the existing
status query; skipped or failed calls do not open the snapshot gate. Normal v1 20-second
polls explicitly set `snapshot: false`. The host keeps the explicit snapshot gated with
surface readiness, so direct-link refreshes reveal all known first-page rows together
after the child surface is committed. For compatibility, the first accepted patch that
omits the field (the older bridge form) is promoted as the implicit snapshot.
The current session may additionally push its DDF-facing status when that state
already changes; this is a push, not a new polling channel, and the summary poll
keeps its existing 20-second conditions. Before the host commits the embedded
surface as ready, these patches are accepted into the row-scoped cache but stay
invisible. This avoids a direct-link refresh showing the selected row's DevFlow
icon while the host still renders its message-list skeleton.

The host maps real Bot identity to the explicit sidebar view identity, updates
only known rows and never creates/reorders sessions. If a status snapshot wins the
race against the host's first page load, the host caches its bounded first-page
payload invisibly and flushes matched rows as soon as host rows exist. Omitted items retain their
previous values. Explicit null clears the row indicator. The cache is nonpersistent and
bounded by known rows in one mounted surface. Leaving/reloading that surface
clears the cache (retaining inactive-Bot statuses is deliberately not required).
List refetch retaining the same rows keeps statuses; rows removed by deletion or
filtering are pruned and require a subsequent poll to regain the row indicator.

The producer still reads only the existing status query's first page (20 sessions);
this change does not promise full-list coverage. No selection means no additional
polling or early snapshot query.

## Verification / propagation

Affected consumers: embedded host, conversation sidebar. Affected producer:
the existing session status query and its polling hook. No new backend, runtime
engine, API endpoint or database change is introduced, but a bridge-capable child
may make one additional early call to the existing status query. A new host with a
pre-snapshot bridge child treats that child's first accepted patch as the implicit
snapshot; a child without the optional bridge keeps the host default.
An old host + new child keeps local behavior. Rendering uses a fixed 14px left indent activity slot before the session title.
The neutral placeholder is a 13px conversation icon in `text-content-icon`; a
DevFlow state is a 14px icon in that same slot. Swapping the two therefore never
shifts the title. DevFlow colors use the host semantic tokens: running uses
`text-info`, waiting/paused use `text-warning`, completion uses `text-success`,
failure uses `text-destructive`, terminal/neutral states use the corresponding
muted tokens. The label is exposed through `title` and the accessible name; no text chip
is added after the title. The icon mapping mirrors the embedded session status
semantics: running spinner, waiting bell, success check, failed warning,
paused/interrupted pause circle, and aborted slash. Explicit idle is intentionally
merged with the neutral conversation placeholder in TeamClaw rather than shown as
AIXCoding's dashed circle. An unknown/unrecognized coding status also renders the
neutral conversation icon. A row without a visible coding state renders that same
neutral conversation icon, so
initially expanded sessions and ordinary Bot sessions do not pop from empty to an
icon. While a normal runtime is replying, the existing reply spinner replaces the
neutral icon; once a Coding state is visible, DevFlow takes precedence.

Tests cover optional props, request capture, ordering, capability reset,
unknown semantics, local-update isolation, cache pruning, icon rendering and
read-only rendering.
