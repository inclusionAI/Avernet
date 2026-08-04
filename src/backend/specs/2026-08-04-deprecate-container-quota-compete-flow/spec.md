# Retire the Container-Quota "抢名额" Admission Flow

## Summary

Access to the platform is decided by a three-tier gate: an operator-managed
whitelist, then a list of users who previously won a container slot, then a live
competition for one of a limited number of daily slots. The second and third
tiers are dormant — the record of slot winners has not been written to in about
three months — and they are the only reason a per-user admission table exists.

This retires tiers two and three. Admission becomes whitelist-only: an operator
puts a person on the list, or that person is not admitted. The operator screens
for managing slot winners, and the endpoint reporting the daily slot budget, go
with them.

## Motivation

**The flow is dormant, and dormant admission logic is worse than none.** Tier
three writes a record every time it admits somebody, and it runs on the *read*
path — every access check by a non-whitelisted user is a potential write. That
the record has not been written to in three months is therefore strong evidence
that no user reaches tier three at all: not that the feature is quiet, but that
it is unreachable. Logic nobody exercises is logic nobody is checking, and it
currently decides who gets into the product.

**It is the last thing forcing a per-user admission table to exist.** Multi-tenancy
work now underway (raised while scoping #556 / #625) needs every table carrying
per-user state to grow a tenant axis, or to justify not having one. Giving this
table a tenant axis means designing tenant-aware slot accounting — how a daily
budget divides across tenants, whether one tenant can exhaust another's capacity
— for a feature no tenant uses. Removing the flow removes that entire design
problem instead of solving it.

**The slot-budget report is worse than unused — it is unauthenticated.** The
endpoint reporting the daily budget, total capacity, and current occupancy takes
no identity and is not gated by any authentication dependency; the request-level
auth middleware deliberately does not block unauthenticated requests, leaving
enforcement to each route, and this route enforces nothing. It is the only route
in the affected set that is not operator-gated. No caller for it exists anywhere
in this repository — not in the frontend, not in the gateway. So today it
publishes live capacity figures, pooled across all tenants, to anyone who asks,
in service of a feature nobody uses.

**The behavior change is small but real, and needs a decision.** Once tiers two
and three are gone, a user who is not on the whitelist is denied outright. Today
such a user gets a chance at a daily slot. If any user is currently being
admitted that way, this takes their access away. The evidence says none are —
that is exactly what the dormant table means — but the confirmation is an
operational check that has to happen before the change ships, not an assumption
the change can be built on.

## User Stories

- As an operator, I want admission to be decided solely by the whitelist I
  manage, so that who has access is something I can read off one list rather
  than infer from a daily budget race I cannot observe.
- As an operator, I want the screens for managing slot winners removed once the
  competition is gone, so that I am not offered controls that no longer affect
  anything.
- As a platform maintainer, I want no per-user admission state outside the
  whitelist, so that the multi-tenancy work has one fewer table to reason about
  and no tenant-scoped capacity accounting to design.
- As a security reviewer, I want no unauthenticated endpoint publishing platform
  capacity figures, so that occupancy across tenants is not readable by anyone
  who can reach the service.

## Acceptance Criteria

- [ ] A user on the whitelist is admitted. This is unchanged.
- [ ] A user explicitly denied on the whitelist is refused. This is unchanged.
- [ ] A user who appears on no whitelist entry is refused. **This is the
      behavior change**: such a user is currently offered a slot.
- [ ] An admission check performs no writes. Checking whether a user is admitted
      never changes stored state, no matter how many times it runs or who runs
      it.
- [ ] An operator can still add a user to, and remove a user from, the whitelist,
      and the effect on that user's admission is immediate.
- [ ] Per-user settings stored alongside a whitelist entry (such as a user's bot
      limit) survive whitelisting and un-whitelisting that same user. Adding
      someone to the whitelist does not reset their other settings.
- [ ] The operator endpoints for listing, reading, and creating slot-winner
      records no longer exist. A request to any of them is answered as a route
      that is not there, not as an empty or error-shaped success.
- [ ] No stored per-user admission state remains outside the whitelist.
- [ ] Every automated test suite that currently depends on the removed surface
      passes without it — including the shared test seed helper used across
      roughly fifty test files, the database-plugin conformance test, and the
      test framework's own documented worked example, all three of which are
      built on the surface being removed.
- [ ] Documentation describing admission states that the whitelist is the only
      input, and no document still describes the slot competition as live.

## In Scope

- The two dormant admission tiers: the check against previous slot winners, and
  the live competition for a daily slot.
- The stored record of slot winners, and every read and write of it.
- The operator endpoints for listing, reading, and creating slot-winner records.
- The endpoint reporting the daily slot budget and current occupancy — see Open
  Questions, its fate is not yet decided.
- The configuration values defining the daily slot budget, the total capacity
  ceiling, and the daily reset time, which have no other consumer.
- The active-device counter, which exists only to compute the slot budget and to
  populate the budget report.
- Test fixtures, flows, baselines, and documentation built on any of the above.

## Out of Scope

- **The whitelist itself.** It stays, unchanged in behavior. Its tenant axis is
  tracked separately; this work neither adds nor blocks it.
- **Per-user bot limits**, which are stored alongside whitelist entries. They are
  untouched, and must keep working through the change.
- **Backfilling or migrating existing slot-winner records.** If any user is being
  admitted solely by a slot record today, this work does not automatically move
  them to the whitelist — see Open Questions.
- **Dropping the underlying table in deployed environments.** The application
  stops reading and writing it; physically removing it is an operational step
  sequenced separately.
- **The unrelated beta-quota feature**, which shares vocabulary but no code path
  and is not affected.

## Open Questions

1. **Is denying unknown users the intended end state?** Once the competition is
   gone, a user not on the whitelist is refused outright rather than offered a
   slot. This is the only user-visible behavior change in the work. It needs an
   explicit yes before implementation, or an alternative unknown-user rule to
   build instead.

2. **Has the slot-winner record been confirmed dormant in every environment, not
   just the one checked?** The check is per-environment, on the most recent
   modification timestamp. Because admission writes on the read path, a live
   caller anywhere would still be inserting today. This cannot be answered from
   the repository and gates the whole change.

3. **If any user is currently admitted only by a slot record, what happens to
   them?** Options: migrate them onto the whitelist before the change ships, or
   accept that they lose access. Moot if question 2 confirms the record is empty
   everywhere; it must be answered if it is not.

4. **Does the slot-budget report survive?** Recommendation: remove it. It has no
   caller in this repository, it is unauthenticated, and the occupancy figure it
   reports is pooled across tenants — so keeping it means both adding
   authentication and giving it a tenant axis, for a number that describes a
   retired feature. Keeping it requires naming the consumer that justifies the
   work.

5. **Do the operator slot-winner endpoints have consumers outside this
   repository** — internal tooling, scripts, dashboards? Nothing in this
   repository calls them, but they are a public HTTP surface and this repository
   cannot see everything that reaches it.

6. **Is immediate removal acceptable, or is a deprecation window required?**
   Immediate removal means the endpoints stop existing in the next release.
   The alternative is a release in which they answer "gone" before being deleted.
   This depends on the answer to question 5.
