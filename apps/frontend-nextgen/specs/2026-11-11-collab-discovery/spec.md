# Collab public discovery

Approved scope (2026-11-11): add public Bots and public collaboration groups to
collab exports using REL20261112's BCS-only discovery. No expert page.

## Behavior

- Sidebar: 协作群, 发现, 外部 Bot. `/collaboration-square` redirects to
  `/collaboration-square/bots`; `/collaboration-square/groups` is also available.
- Discovery tabs: public Bots and public groups only in collab. Open retains
  public Bots, experts and public groups in their existing order.
- Collab overrides `getPublicBotBackendEnabled` to false regardless of deployment
  runtime settings. Metadata/profile enhancement and Human private-session
  creation must never run in this assembly.
- Human viewing an existing friend or managed/owned Bot has no conversation
  button. Friend acceptance updates relationship state without creating a private
  session or navigating to `/workspace/chat`. Non-friends can still apply.
- Public group browsing, member details and session creation remain BCS-backed;
  successful group-session creation navigates to the retained collaboration page.
- Open behavior is unchanged. Authentication remains the existing assembly's
  responsibility, not a new anonymous access path.

## Boundaries

Reuse public Bot/group UI and logic; do not copy a collab-only UI. Split the
existing task-inclusive shell/hook/service/adapter into public-discovery modules
and legacy full facades. Preserve existing full exports for Open consumers.
Only the two explicit discovery page entries are added to the collab allowlist.
Experts, task UI/hooks/BBS transport, BotWorkshop and chat pages stay forbidden.
The new `getDiscoveryExpertsEnabled` capability defaults to true and collab
sets it false; no component reads FRONTEND_FORM.

The public source supplies routes, assembly, closure graph and type-complete
source-slice checks. OCB's private exporter/overlay must pick up these changes
and its artifact tests must expect three sidebar entries and two discovery tabs;
no private checkout/export/image validation is claimed by public CI.

## Validation

TDD for collab routes/navigation, strict BCS-only assembly (even with runtime
Backend enabled), Human owned/friend cards and accepted friendships (no session
request/navigation), and closure retention/omission. Exercise real public pages
and real services at a mocked HTTP boundary for BCS Bot/group flows. Keep Open
navigation, task and chat behavior tests. Run full CI and both production builds.
