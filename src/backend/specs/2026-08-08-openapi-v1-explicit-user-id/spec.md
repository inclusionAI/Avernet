# Public API — Name the End User in the Request

## Summary

Every `/openapi/v1` operation that acts on a user's data currently works out
*which* user from the caller's credential. This makes the user an explicit part
of the request instead: the operation says whose bots, resources, skills or
sessions it is for, in the request itself.

Nothing about who may call what changes. A request without a verified user
principal is refused exactly as it is today, and a caller can still only reach
its own data. This is a contract change made ahead of the capability that needs
it, not the capability itself.

## Motivation

**The user is about to stop being implicit.** Today an end user reaches the
public API through the gateway, which resolves their identity and forwards it as
a signed principal. Every operation therefore has exactly one person behind it,
and the backend can infer them without being told. The next step for this surface
is letting a registered **App call on behalf of a user**: the App presents its
own credential, and the person it is acting for is not inside that credential.

At that point 52 operations have a problem that is not an implementation
problem — it is a *contract* problem. Their published contract never mentions a
user, so there is nowhere for a caller to put one. Adding the parameter then
means changing the contract of the whole surface at the same moment the new
caller type arrives, which is two risky changes landing together on the same
release.

**Doing it now costs nothing and is fully reversible in review.** While the two
values must still agree, the change is observable only as a stricter request
shape: the same callers, the same data, the same failures. Every operation gets
its parameter, the surface gets one place where "which user is this for?" is
answered, and the delegation work later changes that one place rather than 52
handlers, 11 request schemas and their tests.

**The surface has already drifted on exactly this question.** `POST
…/bots/routines` takes its `bot_id` as a required body field; `PATCH
…/bots/routines/{routine_id}` takes the same `bot_id` as a query parameter, and
`/bots/resources` takes it as a query parameter on all nine of its operations
including two that carry a JSON body. `/bots/logs/traces` already names its user
explicitly — as a query parameter. Nobody decided that; it accumulated. Settling
where a scoping parameter goes, and applying the answer to `bot_id` at the same
time, is what stops the next category from inventing a third convention.

## User Stories

- As a partner integrating the public API, I want each operation to state which
  user it acts for, so that the request I build does not depend on which kind of
  credential happened to authenticate it.
- As an engineer adding a public endpoint, I want one stated rule for where a
  scoping parameter goes, so that I do not have to guess from the neighbouring
  category — and get it wrong, as this surface already has.
- As a reviewer, I want the "which user?" question answered in one place, so
  that widening it later to App-on-behalf-of is a reviewable change to that
  place rather than a sweep across every router.
- As an operator, I want a request that names a user its caller may not act for
  to be refused distinguishably from a bad credential, so that a partner's
  misconfiguration is not diagnosed as an auth outage.

## Acceptance Criteria

**The parameter exists, everywhere it should**

- [ ] Every public operation that scopes to a user requires the user id as part
      of its request, and the published API description says so.
- [ ] The parameter is required, not optional — an operation cannot be called
      without naming its user.
- [ ] An operation that has no user dimension at all does **not** take it. The
      exempt set is small, listed with a reason each, and pinned by a test, so a
      later addition to it is a deliberate edit rather than a drift.
- [ ] The five Bot Logs operations are unchanged: they never derived a user from
      the credential, and the one that is user-scoped already names its user.

**Placement is one stated rule, not a per-category habit**

- [ ] A single rule decides where the user id goes, it is written down where an
      engineer adding an endpoint will find it, and a test fails if a new
      operation breaks it.
- [ ] The user id is a query parameter on every operation that takes it —
      whatever the method, whatever the body. There is no second placement to
      learn and no exception table.
- [ ] The bot id is untouched: it stays in the path where it addresses a bot and
      in the query string where it is a parameter.

**Behaviour is unchanged**

- [ ] A request with no verified caller is refused with the same status, body
      and message as before this change.
- [ ] A request naming the caller itself succeeds and reaches the same data as
      before, for every operation.
- [ ] A request naming *another* user is refused, and the refusal is
      distinguishable from an authentication failure.
- [ ] The refusal reveals nothing about the user that was named or whether it
      exists; two different rejected ids produce identical responses.
- [ ] Which user was asked for, and which caller asked, are recoverable from the
      logs.
- [ ] An operation that scopes to a user takes the user it acts on from the
      request, not from the credential, so that permitting the two to differ is
      a change to one rule and not to any operation.
- [ ] The internal `/api/...` surface is untouched.

## In Scope

- The 52 public operations that derive a user id from the verified principal.
- The 4 operations that declare a caller principal but never derive an owner —
  `list_resources`, `create_resource`, `get_resource`, `update_resource`. They
  take the parameter too. Not for uniformity: they are user-scoped in principle
  and merely fail to enforce it today (the gap the 2026-08-02 spec records), so
  closing that gap later should not have to add a required parameter to four
  public operations.
- The published API description, and the developer-facing docs for this surface.

**56 operations take the user id.**

## Out of Scope

- **The 4 operations with no user dimension.** `check_bot_name` answers a
  tenant-wide uniqueness question; `list_mcp_servers`, `list_mcp_tenants` and
  `get_mcp_server` read a marketplace catalogue that is identical for every
  caller in the tenant. None of them has a user-shaped answer to give, now or
  later, so none of them asks for one. An authenticated caller is still
  required — that is the principal's job, not this parameter's.
- **The bot id.** It stays exactly where it is on every operation. Where it
  addresses a bot it is part of the address; where it is a parameter it is
  already a query parameter, which is where this change puts the user id too.
- **Admitting App-on-behalf-of callers.** Which credentials the surface accepts
  is unchanged: a request with no verified user principal still fails. This
  change makes the contract ready for that work; it does not do it.
- **Relaxing the agreement between the named user and the caller.** Naming
  another user is refused. Deciding when an App *may* name another user is the
  delegation workstream (auth design §15).
- **Re-addressing any endpoint.** Paths do not change.
- The internal `/api/...` surface, the gateway's own configuration, and the
  Bot Logs group.

## Decisions

Settled in review rather than left open:

1. **The user id is a query parameter everywhere, never a body field.** A body
   describes the resource being acted on; the user id describes who the call is
   for. It is the same value on every operation and identical in meaning on a
   read and a write, so splitting it by method would put one concept in two
   places — and in a `PUT …/bots/{bot_id}` payload, beside `bot_name`, it would
   read as a property being set on the bot. Placing it by method also buys
   nothing on log hygiene, since the majority of operations are reads that carry
   it in the query string regardless.
2. **The user id is never a path segment.** A path names the resource an
   operation acts on; `/bots/{bot_id}/users/{user_id}` would claim to address a
   user beneath a bot, which inverts the ownership and does not describe what
   the endpoint returns. A user-first address (`/users/{user_id}/bots/…`) is a
   coherent design in the abstract but closed here: the first segment after
   `/openapi/v1` is the gateway's domain selector.
3. **A mismatch is refused rather than resolved.** Preferring the request's value
   would let any verified user read another's data; preferring the credential's
   would answer a request the caller did not make. Refusing is the only option
   that leaves today's behaviour exactly as it is.
