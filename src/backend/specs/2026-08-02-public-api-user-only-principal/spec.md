# Public API — Admit Only Callers That Name an End User

## Summary

The public `/openapi/v1` surface scopes every read and write to an owner, and
only one of the gateway's four identity types names one. Today that constraint
is enforced by accident: a caller whose identity set names no person reaches the
handler, and the request fails only when that handler happens to ask for the
owner. Handlers that never ask do not fail — they serve data.

This makes the rule explicit and structural. Verification refuses an identity set
that carries no `user` principal, so a caller the surface cannot scope is turned
away at the boundary rather than at a lookup each handler has to remember to
perform. `bot`, `app` and `access_key` callers are refused by design.

## Motivation

**The rule is real but unenforced.** `VerifiedCaller.user_id` returns `""` for an
`app` or `access_key` caller, `caller_owner_id` raises on the empty string, and
the request answers 401. That chain works only for handlers that call
`caller_owner_id`. Four in `resources/router.py` — `list_resources`,
`create_resource`, `get_resource`, `update_resource` — take the principal
dependency but never derive an owner, scoping instead on a caller-supplied
`bot_id`. For them the 401 never happens. The surface's own module docstring
claims otherwise ("Owner/identity comes from `caller_owner_id(principal)`
(fail-closed)"), which is how a gap like this stays invisible: the contract is
stated in prose and enforced per call site.

An invariant that every handler must remember is not an invariant. Moving it to
the boundary makes it independent of handler discipline, and makes a future
route safe by construction rather than by review.

**A bot caller is admitted today, and should not be.** `user_id` falls back to a
`bot` principal's `owner_id`, so a bot presenting its own token would be scoped
as its owner across the entire public contract. No `route_security` rule
currently requires or accepts `bot`, so this is unreachable rather than
exploitable — but it is a capability nobody decided to grant, sitting one gateway
config line away from being live.

**The decision has been pending in three places.** The `/openapi/v1` handoff
records that `app` and `access_key` callers 401 and that this "likely needs
settling before any category goes live". The auth design leaves resource
ownership granularity open (§14 Q4) and defers delegation entirely (§15). The
code documents the *symptom* at length — `user_id`'s docstring explains why those
callers yield no owner and says settling it "is a cross-team decision, not
something to paper over here". Nothing records a decision. Narrowing the surface
without writing one down invites the next reader to restore what that docstring
describes.

## User Stories

- As an engineer implementing a public API category, I want a caller who cannot
  be owner-scoped to be refused before my handler runs, so that forgetting an
  owner lookup cannot expose another caller's data.
- As a reviewer, I want the set of identity types this surface admits to be
  stated in one place, so I can tell whether a change widens it without reading
  every router.
- As an operator reading logs, I want a refused caller to say it named no user
  identity, so a misconfigured `route_security` rule is diagnosable as a policy
  refusal rather than a missing field.
- As the engineer who later implements delegation, I want the refusal to name
  what would lift it, so widening the surface is an edit to one guard rather
  than an archaeology exercise.
- As an integrator, I want a refused request to be indistinguishable from any
  other authentication failure, so that probing identity types reveals nothing.

## Acceptance Criteria

- [ ] An identity set carrying no `user` principal fails verification.
- [ ] A `bot`-only, `app`-only, or `access_key`-only identity set is refused.
- [ ] A set carrying a `user` principal alongside any other identity is
      accepted, and the user remains the owner anchor.
- [ ] A refused set yields the same fixed `401` response as every other
      verification failure, with the specific reason logged and never returned.
- [ ] Every route on the public surface requires an authenticated caller
      structurally, not because its handler declares the dependency.
- [ ] A new route added to any public group inherits that requirement without
      its author doing anything.
- [ ] `user_id` is derived only from a `user` principal; no other identity type
      can supply an owner.
- [ ] The internal `/api` surface is unchanged, and its test suite passes
      unmodified.
- [ ] Existing `user`-caller behaviour is byte-identical — same owner, same
      tenant, same responses.

## In Scope

- The identity-set admission rule in gateway principal verification.
- Removal of `bot` as an owner source.
- Applying the caller requirement once at the public router's assembly point.
- Tests covering each refused identity type, the mixed-set acceptance, and the
  structural route requirement.
- Recording the decision in the `/openapi/v1` handoff doc.

## Out of Scope

- **Owner scoping within a tenant.** The four `resources` handlers that scope on
  a caller-supplied `bot_id` rather than the caller's owner are a separate
  defect: this change stops a caller with *no* owner from reaching them, but a
  `user` caller can still name another owner's `bot_id`. Owned by the resources
  category owner; filed separately.
- **Delegation (auth design §15).** A partner acting for a verified end user is
  the designed way to admit third-party callers, and is what should lift this
  restriction. Not built here.
- **The gateway's `route_security` table.** It already requires `user` on both
  rules that exist. This change makes the backend agree with it independently
  rather than depending on it.
- **An architecture gate asserting every public handler derives an owner.** The
  right follow-up to the resources defect, and test-only; kept separate so it
  can land red-or-exempted without blocking this.
- **`app` / `access_key` ownership semantics.** Deliberately left unsettled —
  this change records that they are refused, not what they would own.

## Resolved Questions

1. **Refuse the whole set, or only sets made *entirely* of non-user
   identities?** *Resolved:* require a `user` principal to be present; ignore
   any others. The gateway forwards every identity it resolved, so a route
   declaring `user: required, app: optional` legitimately produces two
   principals. Refusing a set that merely *contains* a non-user identity would
   reject a request the gateway considers valid, and would break that rule shape
   the first time anyone uses it.

2. **Enforce in the verifier or in `caller_owner_id`?** *Resolved:* the
   verifier. `caller_owner_id` runs per handler, which is the property that
   made the rule skippable. The verifier runs once per request, before any
   route, and already hosts the sibling policy that refuses the internal
   tenant off the wire — so the admission rules for an identity set live
   together.

3. **Keep the `bot` owner fallback for a future bot-facing route?** *Resolved:*
   remove it. It is unreachable today, and leaving a silent grant in place for a
   route that does not exist means the grant is never reviewed when that route
   is written. A bot-facing surface should re-add it deliberately, with its own
   decision about what a bot may do as its owner.

4. **Does refusing at the boundary change what a caller sees?** *Resolved:* no.
   The response is the same fixed 401 envelope; only the log line differs. A
   caller cannot tell a refused identity type from a bad signature, which is the
   existing posture for every verification failure.
