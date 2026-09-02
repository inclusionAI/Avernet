# Public API — User-Level Authorization for Applications

## Summary

A user can authorize one named application to act as them at the **account
level** — on the operations of the public API that concern the user rather than
any one bot — see which applications hold that authorization, and withdraw it.
An application acting alone (its own credential, no human on the wire) is
admitted to those operations only while it holds such an authorization from the
user it names.

This is the user-level counterpart of the bot-level record
(`specs/2026-08-10-openapi-v1-bot-app-grant/`). That record answers *"may this
application reach this bot as this user?"*. This one answers *"may this
application act as this user at all, where no bot is addressed?"*. The two are
independent: neither implies the other.

## Motivation

The public API admits an application acting alone in six modes
(`adapters/http/openapi_v1/admission.py`). Four of them are bot-shaped — a bot
is named, so a **bot grant** is the thing checked. Two are not:

- `USER_GATED` — an operation that names no bot but is about the named user's
  account: their bot-creation ceiling, their routines across every bot, their
  Spaces, their work orders and notifications, their local devices and the
  files on them, the governed skill repository, the Skill Center sync.
- `OPEN` — tenant-identical answers, which need no user gate at all.

`USER_GATED` had no record of its own. It borrowed the bot record: an
application was admitted to a user-level operation if it held **at least one
live bot grant** from the named user, whichever bot that was. That proxy has
three defects, and this feature exists to remove them:

1. **It is the wrong consent.** A user who authorized an application to reach
   *one bot* also, without being told, authorized it to list their Spaces, read
   their work orders, enumerate their local devices and browse those devices'
   file trees. Consent on a bot is not consent on the account.
2. **It cannot be withdrawn on its own.** The only way to stop an application
   reading account-level data was to withdraw every bot grant it held.
3. **It was enforced by hand.** The check was a private helper copied into
   five routers and written inline in two more, with no dependency for the
   route inventory test to hold each `USER_GATED` operation to — and several
   `USER_GATED` operations (the four skill-repository reads, the Skill Center
   sync, the skill README) carried no check at all. An application naming any
   user reached them on authentication alone.

## User Stories

- As a user, I want to authorize a named application to act as me on the
  account-level operations of the public API, so that an integration can read
  my Spaces, work orders and devices on its own schedule.
- As a user, I want that authorization to be separate from any bot I have
  authorized, so that letting an application drive one bot does not expose the
  rest of my account.
- As a user, I want to see which applications hold an account-level
  authorization from me, and withdraw any of them without the application's
  cooperation.
- As a platform integrator, I want an account-level authorization to survive
  rotating my application credential, and to be idempotent to grant.
- As a security reviewer, I want every user-level operation to prove the
  authorization through one declared dependency that a structural test holds it
  to, so that an operation cannot silently skip the check.
- As a security reviewer, I want an application holding no authorization to be
  answered exactly as if the user did not exist.

## Acceptance Criteria

### The record

- [x] A live authorization is one row meaning *"app A may act as user U at the
      account level"*, unique per `(tenant, app, user, env)`.
- [x] Granting is idempotent: repeating a live authorization returns it
      unchanged, and does not move its start time.
- [x] Withdrawing hard-deletes the live row and leaves an append-only history
      event, so *"this application could act as this user between T1 and T2"*
      stays answerable.
- [x] The record snapshots the application's display name at consent time.
- [x] The record is confined to the request's tenant by the tenant guard; a
      cross-tenant grant is refused rather than written.
- [x] A user id too long for the record to store is refused at consent time
      rather than truncated into a row no lookup can match.

### Operations (`/openapi/v1/org/user/authorized-apps`)

- [x] **Granting** requires **both** the user's identity and the application's
      own credential on the same request. The application is read from the
      credential, never from a parameter.
- [x] **Listing** and **withdrawing** require only the user.
- [x] Withdrawing an authorization that does not exist answers 404, distinct
      from a successful withdrawal.
- [x] All three operations are `REFUSED` to an application acting alone:
      delegation is a human act.
- [x] `user_id` is a required query parameter naming the caller, as on every
      user-scoped operation; naming anyone else is a 403.

### Admission

- [x] `USER_GATED` now means: an application acting alone is admitted iff it
      holds a live **user-level** authorization from the named user. A bot
      grant no longer admits it to a user-level operation.
- [x] Every `USER_GATED` operation declares one shared dependency,
      `require_granted_user`, and `test_admission_inventory.py` holds the set
      of operations declaring it equal to the table's `USER_GATED` entries, in
      both directions.
- [x] An application with no user-level authorization is refused with the same
      masked `404` a nonexistent bot receives.
- [x] A human caller is unaffected: the dependency is a no-op for a caller
      naming an end user.
- [x] Bot-addressed operations (`GRANT_CHECKED_*`) and grant-narrowed listings
      (`GRANT_FILTERED`) are unchanged; a user-level authorization confers no
      bot access.

## In Scope

- The record, its repository and service, and three operations over it.
- The `USER_GATED` dependency and the migration of every `USER_GATED` route
  onto it, retiring the bot-grant proxy and the per-router copies of it.
- Gateway `route_security` for the new operations and the test that pins it.

## Out of Scope

- An application-side view ("which users have authorized me at the account
  level"). The application learns the answer by calling any user-level
  operation; a listing can follow if integrators ask for it.
- Per-operation scopes inside an authorization — it is all-or-nothing for the
  account-level surface.
- Expiring authorizations.
- Any change to which operations are `USER_GATED`, or to the gateway's
  human-only rules on Spaces and work orders (those remain human-only at the
  edge regardless of the backend's admission mode).
- A frontend consent screen.

## Resolved Questions

- **Should a bot grant keep admitting an application to user-level
  operations, for compatibility?** — *No.* Keeping it would make the new record
  meaningless on exactly the operations it exists for. An integration that
  relied on the proxy re-consents once, through the new grant operation; the
  failure it sees until then is the same masked 404 it would see for any
  missing authorization.
- **Should the record live under `/openapi/v1/bots/…` like the bot-level
  one?** — *No.* The gateway routes `/openapi/v1/org/**` to the same backend,
  and the org group is already "the caller's own identity". An account-level
  authorization is a property of the user, so it sits beneath
  `/openapi/v1/org/user`, and the reserved-component list under `/bots` does
  not grow.
- **Should a user-level grant imply access to the user's bots?** — *No.* The
  two records answer different questions, and collapsing them would recreate
  the over-broad consent this feature removes, in the other direction.
