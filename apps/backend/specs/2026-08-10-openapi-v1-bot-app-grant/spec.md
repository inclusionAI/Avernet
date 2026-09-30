# Public API — Owner-Granted Bot Authorization for Applications

## Summary

A bot's owner can authorize one named application to reach one named bot of
theirs, see which applications are authorized, and withdraw an authorization.
An application, calling alongside that owner, can see which of the owner's bots
it has been authorized for. The authorization is a durable record: it survives
the application rotating its credential, and it is the thing a later,
machine-only call path will be checked against.

This feature ships the record and four operations over it — three the owner
drives, one the application reads. It grants no one any new ability to *operate*
a bot, and it admits no new kind of caller: every operation here still requires
an end user, exactly as every operational route does today.

## Motivation

A platform team wants to drive bots from their own server holding only a machine
credential: no human at the keyboard, no owner login state on the wire, calls
triggered by their platform rather than by a person clicking in our web app.

Nothing on the public API lets a bot's owner say *"this application may reach
this bot of mine."* Because no such record exists, there is nothing a machine-only
call could ever be checked against — the missing authorization record is the
blocker, not the missing call path. This feature creates it.

The two halves are separable and are being kept separate deliberately. This one
is safe on its own: it adds a record and four operations over it, every one of
which still requires an end user, and changes nothing about who may call what. The half that admits application-only
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
- As a platform integrator, I want to see which of an owner's bots I have been
  authorized for, so that I can reconcile my own records against the truth and
  notice an authorization that has been withdrawn.
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

- [x] Granting requires **both** the bot owner's identity and the application's
      own credential on the same request. A request carrying only one of them is
      refused before it reaches the operation.
- [x] The request names **only the bot**. The application is read from the
      credential presented, never from a parameter — so a request cannot point a
      grant at any application other than the one making the call.
- [x] Granting a bot that is already granted to that same application succeeds
      and leaves one authorization, not two.
- [x] A grant that would cross a tenant boundary — the application and the bot
      belong to different tenants — is refused.

### Withdrawing

- [x] Withdrawing requires only the bot owner. It does not require the
      application's credential, so an owner can withdraw after that credential
      is lost, rotated, or the relationship ends.
- [x] Withdrawing an authorization that does not exist is distinguishable from
      withdrawing one that does.

### The two views

The record is read from both ends, and the two reads answer different questions
for different readers.

- [x] **The owner's view — "which applications can reach my bot?"** — requires
      only the bot owner, so it never depends on holding any one application's
      credential. It is scoped to one named bot.
- [x] **The application's view — "which of this owner's bots may I reach?"** —
      requires **both** the owner's identity and the application's own
      credential, the same both-parties posture as granting. It spans the
      owner's bots rather than naming one.
- [x] The application's view is scoped to the **calling** application, read from
      its credential. There is no parameter naming an application, so it cannot
      be used to ask what some *other* application may reach.
- [x] Both views return only live authorizations; withdrawn ones do not appear.

### Authority

Two separate questions, and conflating them is what this section exists to
prevent: *whose data is in scope* is not the same as *which identities must be
on the call*.

- [x] **Whose data:** every operation is scoped to the bot **owner**, who must
      be the verified caller. A collaborator who may operate the bot may not
      manage its authorizations, and no operation here ever reaches another
      person's bots.
- [x] **Which identities:** granting and the application's view need both
      parties present; the owner's view and withdrawing need only the owner.
      Requiring the application on a call is never what authorizes it — the
      owner's identity is.
- [x] A caller who is not the owner receives the same answer as a caller naming
      a bot that does not exist — the surface must not confirm a bot exists to
      someone who may not manage it.

### The record

- [x] An authorization covers exactly one bot. Authorizing one bot conveys
      nothing about any other bot the owner holds.
- [x] An authorization survives the application rotating its credential.
- [x] A withdrawn authorization leaves a trace, so "this application could reach
      this bot between T1 and T2" remains answerable rather than vanishing.
- [x] The record states whose bot it is and which tenant it belongs to, resolved
      when it is written rather than read from a later request.

### Everything else unchanged

- [x] No existing operation changes who may call it. A caller presenting only an
      application credential still reaches nothing — including the application's
      own view, which requires the owner alongside it.
- [x] The published API description documents the four new operations and
      nothing else moves in it.

## In Scope

- The authorization record, and four operations over it on the public API: the
  owner's grant / list / withdraw, and the application's read of which of that
  owner's bots it may reach.
- The requirement that granting and the application's view carry both parties,
  enforced before the operation runs.
- Tenant isolation and owner-scoped authority for all four operations.

## Out of Scope

- **Admitting application-only callers — to any route, including the ones this
  feature adds.** The application's view still requires the owner alongside it,
  so an application acting alone on its own schedule reaches nothing. A view an
  application could read *without* the owner present is the natural companion to
  that admission work and belongs with it — it is the remaining half of issue
  #928, and a separate piece of work.
- Any change to who may call an existing endpoint.
- Per-operation scopes inside an authorization — an authorization is
  all-or-nothing for its bot.
- Expiring authorizations.
- Rate-limit or quota changes, including attributing usage per authorization.
- The separate BaaS chat surface's own allowed-bots list, which stays as it is
  and governs only that surface.

## Resolved Questions

Both questions this spec opened are now settled. Kept rather than deleted, so a
later reader sees what was weighed.

- **How recognizable is a listed authorization?** — *Snapshot the name.* An
  authorization records the application's human-facing name as it stood when the
  owner consented, alongside its identifier. A bare identifier does not serve the
  "an authorization I no longer recognize is visible to me" story, and the name
  is already on the credential presented at grant time, so recording it costs
  nothing extra to obtain.

  It is a snapshot, not a live lookup, and that is the point: it records what the
  owner actually consented to. If the application is later renamed, the
  authorization still reads as what was agreed.

- **Should re-granting refresh the creation time?** — *No.* Re-granting a live
  authorization is idempotent and leaves the original time intact; the record
  must keep answering "could reach this bot from T1" honestly, and refreshing T1
  on a duplicate call would make it lie.

  Re-granting after a withdrawal is a *new* authorization period rather than a
  revival of the old one, so the withdrawn record keeps its closed interval and
  the two periods stay distinguishable.
