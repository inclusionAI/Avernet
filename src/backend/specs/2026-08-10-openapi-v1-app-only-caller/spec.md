# Public API — Admit the App-Principal-Only Caller Against a User's Grant

## Summary

A registered application holding only its own machine credential — no human
anywhere on the wire — can act **as a user who delegated their access to it**,
across the public surface, confined to the bots that user granted.

The record #937 shipped says *"this bot's owner authorized this app."* That is
too narrow: on this platform a person routinely works on bots they do not own,
as a member-level collaborator, and an integration onboarded by such a person
could reach nothing. So the record grows one field and its meaning sharpens to
**"application A may act as user U on bot B"** — where B may belong to someone
else entirely, and `owner_id` records who that is.

The consequence is the invariant the whole feature rests on:

> **An application's reach is exactly its granting user's reach, and never more.**

Not a copy of it taken at consent time — the live thing. Every app-only request
is adjudicated against the same collaborator gate the human would face, at the
moment it is made. If U is removed as a collaborator on B, the application stops
reaching B immediately, with no revocation and nothing to clean up. If U never
had access to B, no grant for it can be created in the first place.

The request still names the human: `user_id` stays a required query parameter,
exactly where #692 and #911 put it. What changes is what it is checked
*against*. Today it must equal the verified caller. For an application there is
no such caller to compare with, so the check becomes the grant — and the
parameter is never trusted, because an application naming a user who granted it
nothing is refused exactly as one naming a bot that does not exist.

Access is confined three ways, by the shape of the operation:

- an operation that **names a bot** is admitted only if a live grant covers it;
- an operation that **lists the user's bots** is admitted and its results are
  narrowed to the granted ones;
- an operation with **no bot dimension** is admitted only where a grant has
  nothing to say and the answer is already public to any authenticated caller in
  the tenant.

Everything else is refused, and a route added tomorrow is refused unless someone
puts it in one of those groups on purpose.

## Motivation

#937 shipped the record. Nothing reads it. A platform team can be authorized,
can see the authorization from both ends, and still cannot make a single
operational call, because every route requires an end user. The feature exists
for a server with no human at the keyboard, and that server is exactly the
caller the surface refuses.

The seam this needs already exists and was built for it. `require_user_id`'s
docstring predicts this change almost line for line: *"When an App may act for a
user, this function stops comparing the two ids and asks whether the delegation
was granted — and no handler, schema or path changes, because none of them ever
named the user."* Naming the user in the request was the preparatory work.

**Trigger:** GitHub issue #950, carrying the second half of #928 (auto-closed by
#937).

## User Stories

- As a platform integrator holding only my application's credential, I want to
  operate a user's bot the way that user can, so that my server can drive it
  without a person signing in for every call.
- As a team member who collaborates on my team's bots without owning them, I
  want to authorize an integration for a bot I work on, so that onboarding does
  not depend on tracking down whoever created it.
- As a platform integrator, I want to ask which bots I may reach and get back
  exactly the granted ones, so that my view of my own scope comes from the
  platform rather than from my records.
- As a bot owner, I want to see every application that can reach my bot and who
  delegated it, so that machine access to my bot is never invisible to me.
- As a bot owner, I want to withdraw any authorization on my bot, including one
  a collaborator created, so that I keep final say over my own bot.
- As a bot owner, I want an application's access to end the moment I delete the
  bot, so that deletion means what it says.
- As a team lead, I want an integration's access to follow the delegating
  person's, so that removing someone from a bot removes their integrations too,
  without a separate cleanup step I would forget.
- As a security reviewer, I want an operation that has not been placed in an
  admission group to refuse a user-less caller, so that the safe answer is the
  one a route gets by saying nothing.

## Acceptance Criteria

### Who is admitted

- [ ] A request carrying a verified **application identity and no end user** is
      admitted on the operations placed in an admission group below, and refused
      with `401` everywhere else on the public surface.
- [ ] An operation in no admission group refuses the user-less caller **without
      any code in that operation**.
- [ ] Adding a new route does not admit the user-less caller. A test fails if a
      route becomes admissible without being placed in a group.
- [ ] A request carrying an end user is unaffected everywhere: same parameters,
      same scoping, same refusals, same responses, same request/response schemas.
- [ ] A caller carrying neither an end user nor an application is refused
      everywhere.

### The record

- [ ] The grant carries the **delegating user** and the **bot's owner** as two
      separate fields, because they are two different people whenever the bot is
      shared.
- [ ] Uniqueness is per application, bot and **delegating user**: two
      collaborators may each authorize the same application for the same bot,
      and those are two distinct, independently withdrawable delegations.
- [ ] The history records the delegating user too, so "who let this application
      in, and when" is answerable after the live row is gone.
- [ ] The change is applied to both the checked-in `CREATE` and a migration for
      the deployed table, and the two describe the same shape column for column.

### Who may grant

- [ ] A user may authorize an application for a bot they can **operate** —
      their own, or one they collaborate on at member level or above. This is
      the same bar `core/engine_runtime/gate.py` already applies to operating
      the bot, so the rule is: *you may delegate exactly the access you have.*
- [ ] A user who cannot operate the bot cannot authorize an application for it,
      and is answered exactly as if the bot did not exist.
- [ ] Granting still requires both parties on the request — the delegating
      user's identity and the application's own credential — and the
      application is still never a parameter.

### Whose access the application gets

- [ ] An app-only request acts as the delegating user named in `user_id`, with
      the bot's owner taken **from the grant record**, never from the request.
- [ ] The delegating user's access is re-adjudicated on every request, not
      trusted from consent time. Losing collaborator access on a bot ends the
      application's access to it immediately, with no revocation.
- [ ] An application never reaches further than its delegating user. On
      operations where a user can only reach their own bots, so can the
      application; on operations admitting collaborators, so is the application —
      as that user, at that user's level.
- [ ] An app-only caller cannot use the addressed-owner parameter to reach a bot
      outside its grant. A request naming an owner other than the grant's is
      refused before the downstream resolve, not left to fail there by accident.
- [ ] A human caller's use of that parameter is unchanged.

### How the user is established

- [ ] `user_id` remains a **required** query parameter everywhere it is required
      today. No operation's schema changes.
- [ ] For a caller naming an end user, `user_id` must still equal that caller
      (`403` otherwise). Unchanged.
- [ ] For an app-only caller, `user_id` names the delegating user and is
      authorized against the grant, never trusted on its own.
- [ ] The application is read from the verified credential and is never a
      parameter, so no request can be authorized against another application's
      grants.
- [ ] The tenant comes from the verified principal and confines the grant
      lookup; nothing the caller sends can widen it.

### Grant-checked operations (the request names a bot)

- [ ] Admitted only if a live grant exists for the calling application, that bot
      and the named delegating user.
- [ ] With no such grant the call is refused, and the refusal does not
      distinguish "no such bot" from "not authorized for it".
- [ ] A grant for a different bot, one held by a different application, or one
      delegated by a different user does not admit the call.
- [ ] Once the grant is checked and the user's own access adjudicated, the
      operation behaves identically to the same call made by that user.

### Grant-filtered operations (the operation lists bots)

- [ ] Admitted without naming a bot, and the result contains **only** bots the
      delegating user granted the calling application.
- [ ] An application with no grants from that user gets an empty result, not an
      error.
- [ ] The same operation called by the user returns their own bots, unfiltered.
- [ ] Pagination counts describe the filtered result, so a caller cannot infer
      how many bots it was not granted.
- [ ] The application's own view of its grants includes bots the delegating user
      does not own, since those never appear in a list of that user's bots and
      would otherwise be undiscoverable.

### Operations with no bot dimension

- [ ] An operation whose answer concerns the delegating user's account is
      admitted only while the application holds at least one live grant from
      that user.
- [ ] An operation whose answer is identical for every caller in the tenant, and
      names no user at all, is admitted on authentication alone. This is not a
      new exposure: the same answer is already readable by any authenticated
      caller in that tenant.
- [ ] An operation that writes account-level configuration with no bot dimension
      is **refused**. A grant is consent to reach a bot, not to reconfigure an
      account.

### The owner's authority over their own bot

- [ ] The owner of a bot sees **every** live authorization on it, whoever
      delegated it, and each names its delegating user.
- [ ] The owner may withdraw any authorization on their bot, including one a
      collaborator created.
- [ ] A collaborator who is not the owner sees and withdraws only the
      authorizations they themselves delegated.

### Refusals, named because refusing them is the point

- [ ] Creating a bot is refused.
- [ ] Granting, listing per bot, and withdrawing authorizations are refused for
      an app-only caller. Delegation is a human act.
- [ ] The bot-logs group is refused. Its `user_id` means "whose traces to read"
      rather than "whose call this is", so a grant does not translate to it.
- [ ] A route that names no bot and whose bot cannot be resolved before the
      handler runs is refused rather than admitted unchecked.

### The deletion invariant

- [ ] Deleting a bot withdraws every authorization standing against it, whoever
      delegated it, as part of the deletion.
- [ ] This holds when the deletion is performed by an application.
- [ ] The withdrawals are recorded in the history.
- [ ] Deletion still succeeds for a bot with no authorizations; a failure to
      withdraw fails the deletion rather than being swallowed.

### Nothing else changes

- [ ] No operation gains, loses, or reshapes a request parameter or a status
      code for a caller that names an end user. The one response change is
      additive: the owner's authorization listing gains the delegating user.
- [ ] The tenant isolation guard remains the sole enforcement of tenancy.
- [ ] Refusals stay indistinguishable to the caller.

## In Scope

- Adding the delegating user to the grant record and its history, with the
  matching migration.
- Letting a member-level collaborator delegate, and giving the bot's owner
  visibility and override.
- Making "an application with no end user" verifiable and, per operation,
  admissible — fail-closed by default.
- Authorizing an app-only request against the grant, and adjudicating the
  delegating user's access per request.
- Filtering bot listings to the granted set.
- Resolving the bot for admitted routes that carry a `skill_id` instead.
- Revoking authorizations when a bot is deleted.
- The gateway route rules and the published description.

## Out of Scope

- **Delegation of a *verified* human** (auth design §15, issue #911). There the
  person is authenticated on the request; here the grant stands in for an absent
  one and `user_id` is authorized rather than verified.
- **Per-operation or per-group grant scopes.** A grant is all-or-nothing for the
  bot it names, bounded by the delegating user's own level.
- **Widening `owner_id` or `user_id` beyond 256 characters** — the unique key is
  already at 2392 of InnoDB's 3072 bytes.
- **The `COLLATE utf8mb4_bin` drift** on the existing columns, which stays a
  separate follow-up. The new column declares its collation explicitly so the
  field this feature resolves on is not added to the drift.
- **Singlebox coverage-manifest registration** — unchanged from #937; these
  routes land on `coverage_baseline.txt` (tracked by #651).
- **WebSocket planes**, which authenticate by a credential in the handshake.

## Decisions Made Without Asking

1. **Creating a bot is refused.** No bot exists yet for a grant to cover, and
   creation spends the user's quota. The coherent alternative — admit creation
   and auto-grant the new bot — invents consent. See Open Question 1.
2. **The four `skill_id`-only routes are brought into the grant-checked group by
   resolving the skill's bot before the handler.** Refusing them would leave a
   group split two-admitted / four-refused for a reason invisible from outside.
   Admitting them unchecked was never an option: the underlying service scopes
   by user only, so an application would reach a skill on an ungranted bot.
3. **MCP configuration routes are refused** even though they carry `user_id` and
   no bot: they read and write account-level configuration a grant does not
   speak to.
4. **The four routes carrying no `user_id` at all** — the bot name check and the
   three MCP catalogue reads — are admitted on authentication alone, because
   there is no user on the wire to gate against and their answers are already
   identical for every authenticated caller in the tenant.
5. **The delegating user replaces the bot owner in the unique key** rather than
   joining it. Adding a 256-character column to a 2392-byte key gives 3416,
   past InnoDB's 3072-byte cap — the same wall #937 hit. Uniqueness per
   `(app, bot, delegating user)` is also the correct semantics.

## Open Questions

1. Should bot **creation** be admitted, with the new bot auto-granted to the
   creating application? It would let an integration provision end to end.
   Against: an auto-grant is consent nobody gave per bot. Struck for now.
2. Should a grant-filtered listing tell the caller that filtering happened?
   Against: it leaks the size of what was withheld. For: an integrator cannot
   currently tell "not granted" from "does not exist". Currently silent.
3. When a bot owner withdraws a collaborator's delegation, should the
   collaborator be told? Nothing notifies them today, and their integration
   simply starts getting `404`s. Out of scope here, but it is the kind of gap
   that turns into a support ticket rather than a bug report.
