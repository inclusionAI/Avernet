# Public API — Admit the App-Principal-Only Caller Against an Owner's Grant

## Summary

A registered application holding only its own machine credential — no end user
anywhere on the wire — can drive a bot it has been authorized for. The owner's
authorization record, written by the half that shipped in #937, is what stands in
for the absent human: given the calling application and the bot named in the
request, the record yields the owner the call acts for.

This is deliberately not a general relaxation. A named set of operations accepts
this caller; every other operation on the public surface refuses it exactly as
today, and a route added tomorrow refuses it too unless someone adds it to the
list on purpose. The owner is read from the grant and from nowhere else — never
from a parameter, never from anything the caller sends.

## Motivation

#937 shipped the record. Nothing reads it. A platform team can be authorized by a
bot's owner, can see the authorization in both directions, and still cannot make
a single operational call, because every route over that record — and every
operational route on the surface — requires an end user. The feature exists for a
server with no human at the keyboard, and that server is exactly the caller the
surface refuses.

The shape here is the inverse of what shipped. There, both parties were on the
wire and the record was the *output*: consent, witnessed by an owner who was
present. Here only the application is present, and the record is the *input*: the
standing consent of an owner who is not. That inversion is why the guard cannot
simply be dropped. Today's rule — a verified identity set must name an end user —
is enforced during verification rather than per handler precisely so it holds for
routes nobody has written yet. Relaxing it in the same place would relax it for
all of them at once.

**Trigger:** GitHub issue #950, carrying the second half of #928 (auto-closed by
#937).

## User Stories

- As a platform integrator holding only my application's credential, I want to
  operate a bot an owner authorized me for, so that my server can drive it
  without a person signing in for every call.
- As a platform integrator, I want to name only the bot in my request, so that I
  never have to know, store, or send the owner's user id — a value the API has
  never told me and should not require me to guess.
- As a bot owner, I want an application's machine-only access to end the moment I
  delete the bot, so that deletion means what it says.
- As a bot owner, I want an application's machine-only access to be limited to
  operating the bot, so that authorizing an integration cannot let it delete the
  bot, re-authorize other applications, or reach my other bots.
- As a security reviewer, I want an operation that has not explicitly opted in to
  refuse a user-less caller, so that the safe answer is the one a route gets by
  saying nothing.
- As a security reviewer, I want the grant lookup to be impossible to point at
  another application, so that a stolen bot id buys nothing.
- As an engineer adding a route next month, I want to be unable to inherit this
  relaxation by accident, so that the surface does not widen while nobody is
  looking.

## Acceptance Criteria

### Who is admitted

- [ ] A request carrying a verified **application identity and no end user** is
      admitted on the operations named below, and refused with `401` on every
      other operation of the public surface.
- [ ] An operation not named below refuses the user-less caller **without any
      code in that operation** — the refusal is a property of the surface, not
      something each handler remembers.
- [ ] Adding a new route to the public surface does not admit the user-less
      caller. A test fails if a route becomes admissible without being added to
      the declared list.
- [ ] A request carrying an end user is unaffected on every operation of the
      surface, including the named ones: same parameters, same scoping, same
      refusals, same responses.
- [ ] A caller carrying neither an end user nor an application — an access-key or
      bot identity alone — is refused everywhere, including on the named
      operations.

### Which owner the call acts for

- [ ] On an app-only call, the owner is resolved from the authorization record
      alone, keyed on the calling application and the bot named in the request,
      within the request's tenant and environment.
- [ ] The application is read from the verified credential and is never a
      parameter, so no request can resolve against another application's grants.
- [ ] Nothing the caller sends can widen or redirect the resolution. The tenant
      comes from the record's anchor rather than from the wire.
- [ ] With no live authorization for that application and bot, the call is
      refused, and the refusal does not distinguish "no such bot" from "not
      authorized for it" — a caller learns nothing about bots it has no grant for.
- [ ] An app-only call **does not accept** a `user_id` parameter. Supplying one is
      refused rather than ignored, so a request can never appear to name an owner
      it does not select.
- [ ] A user-bearing call still requires `user_id` and still refuses one naming
      anybody but the caller, unchanged.

### Which operations a grant admits

- [ ] A grant is all-or-nothing for the bot it names. It carries no per-operation
      or per-group scopes.
- [ ] An app-only caller may, for a bot it holds a live grant on: read the bot,
      read its runtime status, restart it, drive its sessions (list, create, read,
      update, delete), drive that session's messages (list, clear), and download a
      file resource belonging to it.
- [ ] Every other operation refuses the app-only caller, including — named
      explicitly because refusing them is the point — creating, updating or
      deleting a bot; granting, listing or withdrawing authorizations; reading bot
      logs; and every skills, MCP, routines, identity, engine-config, models,
      approvals, connection and resource-mutation operation.
- [ ] The admitted set is stated in one place, and the published API description
      says of each admitted operation that it accepts this caller.

### The deletion invariant

- [ ] Deleting a bot withdraws every authorization standing against it, as part of
      the deletion.
- [ ] After a bot is deleted, an application that held a grant on it is refused,
      by the same code path that refuses an application that never held one.
- [ ] The withdrawal is recorded in the authorization history, so "when could this
      application reach that bot" still answers honestly for a bot that is gone.
- [ ] Deletion continues to succeed for a bot with no authorizations, and a
      failure to withdraw fails the deletion rather than being swallowed.

### Nothing else changes

- [ ] No existing operation gains, loses, or reshapes a parameter, a response
      body, or a status code for a caller that names an end user.
- [ ] The tenant isolation guard remains the sole enforcement of tenancy; this
      feature adds no second comparison that could disagree with it.
- [ ] Refusals stay indistinguishable to the caller: which half of admission
      failed is logged for an operator and never returned.

## In Scope

- Making "an application with no end user" an expressible, per-operation opt-in on
  the public surface, fail-closed by default.
- Resolving the acting owner from the authorization record on `(application, bot)`
  within the request's tenant and environment.
- Deciding and enforcing what `user_id` means when no user is on the wire.
- The named allow-list of operations, and the explicit refusal of everything else.
- Revoking authorizations when a bot is deleted, as part of bot lifecycle.
- Documenting the admitted set in the published API description.

## Out of Scope

- **Delegation** — an application acting for a *verified human* (auth design §15,
  issue #911). That path names a person; this one deliberately does not, and the
  two must not be collapsed into one relaxation.
- **Per-group or per-operation grant scopes.** The record has no room for them and
  no caller has asked for them; adding the column now would be speculative
  abstraction. The allow-list is a property of the surface, identical for every
  grant.
- **Widening `owner_id` beyond 256 characters.** Held from #937: widening pushes
  the unique key past InnoDB's 3072-byte cap. If a real owner id can exceed 256
  this needs a hash column in the key, which is its own change.
- **The `COLLATE utf8mb4_bin` drift** between the deployed DDL and the checked-in
  `.sql`. A separate follow-up; this feature assumes byte-exact comparison, which
  is what the deployed collation already gives.
- **Singlebox coverage-manifest registration.** Unchanged from #937: singlebox has
  no gateway to mint principals, so an app-only caller cannot be minted there
  either. These routes land on `coverage_baseline.txt` (tracked by #651).
- **WebSocket planes.** The message socket authenticates by a credential in the
  handshake query and is exempt from route security today; nothing here changes
  it.

## Decisions Made Without Asking

Recorded here so they are easy to overturn at review rather than buried in the
plan.

1. **The allow-list is exactly the issue's three families**, read as: bot
   lifecycle → read / status / restart (not create, update or delete); sessions
   and messages → the whole `sessions` group; file download → the resource
   download operation only. Listing, creating, updating or deleting resources is
   *not* admitted, because "file download" is what the issue named.
2. **`user_id` is not accepted at all on an app-only call**, rather than accepted
   and validated against the resolved owner. The issue called this the possibly
   cleanest answer; it is also the only one that does not require the application
   to know a value the API never tells it — the app's own view of its grants
   returns bot ids and grant times, never an owner id.
3. **A grant is all-or-nothing per bot.** See Out of Scope.
4. **Deleting a bot revokes its grants inside bot deletion**, not by a filter each
   reader re-implements, and a revocation failure fails the deletion.

## Open Questions

1. Should `POST /openapi/v1/bots/{bot_id}/restart` really be admitted? It is the
   one *mutating* lifecycle operation on the list. The case for it: an integration
   whose bot is wedged cannot ask a human to press the button, which is the whole
   premise. The case against: it is the only admitted operation that changes the
   bot's state rather than a session's. Cheap to strike — one line of the
   allow-list and one test.
2. Should an app-only caller be able to *list* a bot's resources, not only
   download one? Downloading requires knowing a `resource_id` the API would
   otherwise never hand it. Reading the issue narrowly says no; usability says the
   list is implied. Struck for now, and named here rather than assumed away.
