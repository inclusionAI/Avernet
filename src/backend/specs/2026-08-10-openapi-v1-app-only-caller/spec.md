# Public API — Admit the App-Principal-Only Caller Against an Owner's Grant

## Summary

A registered application holding only its own machine credential — no end user
anywhere on the wire — can act for an owner who authorized it, across the public
surface, confined to the bots that owner granted it.

The request still names the owner. `user_id` stays exactly where #692 and #911 put
it: a required query parameter on every user-scoped operation. What changes is
what that parameter is checked *against*. Today it must equal the verified
caller's own id. For an application there is no such id to compare with, so the
check becomes the grant: `(app_id, bot_id, user_id)` must have a live
authorization. The parameter is never trusted — an application naming an owner
who granted it nothing is refused exactly as one naming a bot that does not exist.

Access is confined three ways, by the shape of the operation:

- an operation that **names a bot** is admitted only if a live grant covers it;
- an operation that **lists the owner's bots** is admitted and its results are
  narrowed to the granted ones;
- an operation with **no bot dimension** is admitted only where a grant has
  nothing to say and the answer is already public to any authenticated caller in
  the tenant.

Everything else is refused, and a route added tomorrow is refused unless someone
puts it in one of those groups on purpose.

## Motivation

#937 shipped the record. Nothing reads it. A platform team can be authorized by a
bot's owner, can see the authorization in both directions, and still cannot make
a single operational call, because every route requires an end user. The feature
exists for a server with no human at the keyboard, and that server is exactly the
caller the surface refuses.

The seam this needs already exists and was built for it. `require_user_id`'s
docstring predicts this change almost line for line: *"When an App may act for a
user, this function stops comparing the two ids and asks whether the delegation
was granted — and no handler, schema or path changes, because none of them ever
named the user."* Naming the user in the request was the preparatory work. This
feature is the payoff.

**Trigger:** GitHub issue #950, carrying the second half of #928 (auto-closed by
#937).

## User Stories

- As a platform integrator holding only my application's credential, I want to
  operate an owner's bot the way that owner can, so that my server can drive it
  without a person signing in for every call.
- As a platform integrator, I want to ask which of an owner's bots I may reach
  and get back exactly the granted ones, so that my own view of my scope comes
  from the platform rather than from my records.
- As a platform integrator, I want a listing operation to return only what I was
  granted rather than refusing outright, so that discovery works without the
  owner having to enumerate their bots to me out of band.
- As a bot owner, I want an authorization to reach exactly the bots I named, so
  that authorizing an integration for one bot tells it nothing about the others.
- As a bot owner, I want an application's access to end the moment I delete the
  bot, so that deletion means what it says.
- As a bot owner, I want authorizing and withdrawing to stay mine alone, so that
  an application can never widen its own access or read which other applications
  I have authorized.
- As a security reviewer, I want an operation that has not been placed in an
  admission group to refuse a user-less caller, so that the safe answer is the
  one a route gets by saying nothing.
- As a security reviewer, I want a route that does not put a bot on the wire to
  be refused unless its bot can be resolved before the handler runs, so that
  "we could not check the grant" never resolves to "allow".

## Acceptance Criteria

### Who is admitted

- [ ] A request carrying a verified **application identity and no end user** is
      admitted on the operations placed in an admission group below, and refused
      with `401` everywhere else on the public surface.
- [ ] An operation in no admission group refuses the user-less caller **without
      any code in that operation** — the refusal is a property of the surface.
- [ ] Adding a new route does not admit the user-less caller. A test fails if a
      route becomes admissible without being placed in a group.
- [ ] A request carrying an end user is unaffected everywhere: same parameters,
      same scoping, same refusals, same responses, same published schema.
- [ ] A caller carrying neither an end user nor an application — an access-key or
      bot identity alone — is refused everywhere.

### How the owner is established

- [ ] `user_id` remains a **required** query parameter on every operation that
      requires it today. No operation's schema changes.
- [ ] For a caller naming an end user, `user_id` must still equal that caller
      (`403` otherwise). Unchanged.
- [ ] For an app-only caller, `user_id` names the owner the request acts for and
      is authorized against the grant, never trusted on its own.
- [ ] The application is read from the verified credential and is never a
      parameter, so no request can be authorized against another application's
      grants.
- [ ] The tenant comes from the verified principal and confines the grant lookup;
      nothing the caller sends can widen it.

### Grant-checked operations (the request names a bot)

- [ ] Admitted only if a live authorization exists for the calling application,
      that bot, and the named owner.
- [ ] With no such authorization the call is refused, and the refusal does not
      distinguish "no such bot" from "not authorized for it".
- [ ] A grant for a different bot, or one held by a different application, does
      not admit the call.
- [ ] Once the grant is checked, the operation behaves identically to the same
      call made by the owner.

### Grant-filtered operations (the operation lists the owner's bots)

- [ ] Admitted without naming a bot, and the result contains **only** bots the
      owner granted the calling application.
- [ ] An application with no grants from that owner gets an empty result, not an
      error — it has asked a question with a legitimate empty answer.
- [ ] The same operation called by the owner returns all their bots, unfiltered.
- [ ] Pagination counts describe the filtered result, so a caller cannot infer
      how many bots it was not granted.

### Operations with no bot dimension

- [ ] An operation whose answer concerns the owner's account is admitted only
      while the application holds at least one live grant from that owner.
- [ ] An operation whose answer is identical for every caller in the tenant, and
      names no owner at all, is admitted on authentication alone. This is not a
      new exposure: the same answer is already readable by any authenticated
      caller in that tenant.
- [ ] An operation that writes owner-level configuration with no bot dimension is
      **refused**. A grant is consent to reach a bot, not to reconfigure an
      account.

### Refusals, named because refusing them is the point

- [ ] Creating a bot is refused.
- [ ] Granting, listing per bot, and withdrawing authorizations are refused. Only
      the owner may widen or inspect who can reach their bots.
- [ ] The bot-logs group is refused. Its `user_id` means "whose traces to read"
      rather than "whose call this is", so a grant does not translate to it.
- [ ] A route that names no bot and whose bot cannot be resolved before the
      handler runs is refused rather than admitted unchecked.

### The deletion invariant

- [ ] Deleting a bot withdraws every authorization standing against it, as part
      of the deletion.
- [ ] This holds when the deletion is performed by an application, which thereby
      removes its own access.
- [ ] The withdrawal is recorded in the authorization history.
- [ ] Deletion still succeeds for a bot with no authorizations; a failure to
      withdraw fails the deletion rather than being swallowed.

### Nothing else changes

- [ ] No operation gains, loses, or reshapes a parameter, a response body, or a
      status code for a caller that names an end user.
- [ ] The tenant isolation guard remains the sole enforcement of tenancy.
- [ ] Refusals stay indistinguishable to the caller; which check failed is logged
      for an operator and never returned.

## In Scope

- Making "an application with no end user" verifiable and, per operation,
  admissible — fail-closed by default.
- Authorizing an app-only request against the grant rather than against an
  identity comparison.
- Filtering owner-scoped bot listings to the granted set.
- Resolving the bot for admitted routes that carry a `skill_id` instead of a
  `bot_id`.
- Revoking authorizations when a bot is deleted.
- The gateway route rules, and documenting the admitted set.

## Out of Scope

- **Delegation** — an application acting for a *verified* human (auth design §15,
  issue #911). There the person is authenticated; here the grant stands in for an
  owner who is absent, and `user_id` is authorized rather than verified. The two
  must not be collapsed.
- **Per-operation or per-group grant scopes.** A grant is all-or-nothing for the
  bot it names. Adding a scope column now is speculative.
- **Widening `owner_id` beyond 256 characters** (carried from #937 — widening
  pushes the unique key past InnoDB's 3072-byte cap; a real owner id longer than
  256 needs a hash column, which is its own change).
- **The `COLLATE utf8mb4_bin` drift** between deployed DDL and the checked-in
  `.sql`. This feature assumes byte-exact comparison, which the deployed
  collation gives.
- **Singlebox coverage-manifest registration** — unchanged from #937: singlebox
  has no gateway to mint principals, so these routes land on
  `coverage_baseline.txt` (tracked by #651).
- **WebSocket planes**, which authenticate by a credential in the handshake.

## Decisions Made Without Asking

1. **Creating a bot is refused.** No bot exists yet for a grant to cover, and
   creation spends the owner's quota. The coherent alternative — admit creation
   and auto-grant the new bot to the creating application — invents a consent the
   owner never gave, so it is not taken silently. Easy to overturn; see Open
   Question 1.
2. **The four `skill_id`-only routes are brought into the grant-checked group by
   resolving the skill's bot before the handler.** Refusing them instead would
   leave a group where two operations are admitted and four are not, for a reason
   invisible from the outside. Admitting them unchecked was never an option: the
   owner-scoped service would happily reach a skill on a bot the application was
   never granted.
3. **MCP configuration routes are refused** even though they carry `user_id` and
   no bot. They read and write the owner's account-level configuration, which a
   grant does not speak to.
4. **The four routes that carry no `user_id` at all** — bot name check and the
   three MCP catalogue reads — are admitted on authentication alone, because
   there is no owner on the wire to gate against and their answers are already
   identical for every authenticated caller in the tenant.

## Open Questions

1. Should bot **creation** be admitted, with the new bot auto-granted to the
   creating application? It would let an integration provision end to end, and
   the owner has already consented to that application generally. Against: an
   auto-grant is consent the owner did not give per bot, which is the unit this
   whole feature is built on. Struck for now.
2. Should a grant-filtered listing tell the caller that filtering happened — a
   flag or a distinct count? Against: it leaks the size of what was withheld.
   For: an integrator debugging a missing bot cannot currently tell "not granted"
   from "does not exist". Currently silent.
