# Public API — Owner-Granted Bot Authorization for Applications

## Summary

A bot's owner can authorize one named application to reach one named bot of
theirs, see which applications are authorized, and withdraw an authorization.
The authorization is a durable record: it survives the application rotating its
credential, and it is the thing a later, machine-only call path will be checked
against.

This feature ships the record and the owner's three operations over it. It
grants no one any new ability to *operate* a bot — every operational route keeps
requiring an end user exactly as it does today.

## Motivation

A platform team wants to drive bots from their own server holding only a machine
credential: no human at the keyboard, no owner login state on the wire, calls
triggered by their platform rather than by a person clicking in our web app.

Nothing on the public API lets a bot's owner say *"this application may reach
this bot of mine."* Because no such record exists, there is nothing a machine-only
call could ever be checked against — the missing authorization record is the
blocker, not the missing call path. This feature creates it.

The two halves are separable and are being kept separate deliberately. This one
is safe on its own: it adds a record and three owner-facing operations, and
changes nothing about who may call what. The half that admits application-only
callers to operational routes is later work, and it will *read* what this
feature writes. Shipping the record first means that work starts against a
populated, reviewed permission model rather than inventing one under pressure.

**Trigger:** GitHub issue #928, narrowed to its first half by explicit decision.

## User Stories

- As a bot owner, I want to authorize a named application to reach one specific
  bot of mine, so that a platform integration can be driven without me being
  present for every call.
- As a bot owner, I want to see which applications can reach my bot, so that an
  authorization I no longer recognize is visible to me.
- As a bot owner, I want to withdraw an application's authorization on my own,
  so that a lost or rotated application credential does not leave a permanent
  hole.
- As a bot owner, I want an authorization to cover exactly one bot, so that
  authorizing one integration does not expose the rest of my bots.
- As a platform integrator, I want an authorization to survive rotating my
  application credential, so that routine key hygiene does not break a live
  integration.
- As a security reviewer, I want an authorization to be impossible to point at
  an application other than the one making the call, and impossible to cross a
  tenant boundary, so that the record cannot become a way to borrow someone
  else's access.
- As the engineer building the machine-caller half later, I want the record to
  answer "whose bot, in which tenant" on its own, so that path never has to
  trust anything the caller sends.

## Acceptance Criteria

### Granting

- [ ] Granting requires **both** the bot owner's identity and the application's
      own credential on the same request. A request carrying only one of them is
      refused before it reaches the operation.
- [ ] The request names **only the bot**. The application is read from the
      credential presented, never from a parameter — so a request cannot point a
      grant at any application other than the one making the call.
- [ ] Granting a bot that is already granted to that same application succeeds
      and leaves one authorization, not two.
- [ ] A grant that would cross a tenant boundary — the application and the bot
      belong to different tenants — is refused.

### Withdrawing and listing

- [ ] Withdrawing requires only the bot owner. It does not require the
      application's credential, so an owner can withdraw after that credential
      is lost, rotated, or the relationship ends.
- [ ] Listing requires only the bot owner, so answering "which applications can
      reach my bot?" never depends on holding any one application's credential.
- [ ] Listing returns only live authorizations; withdrawn ones do not appear.
- [ ] Withdrawing an authorization that does not exist is distinguishable from
      withdrawing one that does.

### Authority

- [ ] Only the bot's **owner** may grant, list or withdraw. A collaborator who
      may operate the bot may not manage its authorizations.
- [ ] A caller who is not the owner receives the same answer as a caller naming
      a bot that does not exist — the surface must not confirm a bot exists to
      someone who may not manage it.

### The record

- [ ] An authorization covers exactly one bot. Authorizing one bot conveys
      nothing about any other bot the owner holds.
- [ ] An authorization survives the application rotating its credential.
- [ ] A withdrawn authorization leaves a trace, so "this application could reach
      this bot between T1 and T2" remains answerable rather than vanishing.
- [ ] The record states whose bot it is and which tenant it belongs to, resolved
      when it is written rather than read from a later request.

### Everything else unchanged

- [ ] No existing operation changes who may call it. A caller presenting only an
      application credential still reaches no operational route.
- [ ] The published API description documents the three new operations and
      nothing else moves in it.

## In Scope

- The authorization record, and the owner's grant / list / withdraw operations
  over it on the public API.
- The requirement that granting carries both parties, enforced before the
  operation runs.
- Tenant isolation and owner-only authority for all three operations.

## Out of Scope

- **Admitting application-only callers to any operational route.** That is the
  remaining half of issue #928 and a separate piece of work.
- Any change to who may call an existing endpoint.
- Per-operation scopes inside an authorization — an authorization is
  all-or-nothing for its bot.
- Expiring authorizations.
- Rate-limit or quota changes, including attributing usage per authorization.
- The separate BaaS chat surface's own allowed-bots list, which stays as it is
  and governs only that surface.

## Open Questions

- **How recognizable is a listed authorization?** The user story asks that an
  authorization the owner no longer recognizes be *visible* to them, but the
  authorization identifies the application by its identifier, and the
  application's human-facing name lives in a registry this surface does not
  read. Options: list identifiers only (smallest change, weakest
  recognizability); or snapshot the application's name onto the record when it
  is written, which costs one field and no extra lookup because the name is
  already on the credential presented at grant time. Non-blocking — the field is
  additive, so the plan proceeds with identifiers and this can be folded in
  either now or later.
- **Should re-granting an existing authorization refresh its creation time, or
  leave the original?** Affects only what an audit reads back. Non-blocking; the
  plan will pick the one that keeps "could reach it from T1" honest.
