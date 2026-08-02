# Repository Implementations Move Into Core

## Summary

Repository implementations currently live in the plugin layer even though they
are not plugins: each has exactly one implementation and none varies by profile.
This change relocates every repository implementation to sit beside its existing
contract in the domain it belongs to, makes each contract's members abstract,
and makes each implementation declare that contract as its base. The result is
that a developer can navigate from contract to implementation in an IDE, and a
repository that stops satisfying its contract fails loudly at startup instead of
silently returning nothing.

## Motivation

A component belongs in the plugin layer when different runtime profiles require
different implementations — the plugin layer's own documentation and Rule 20
require every plugin contract to have paired local and production
implementations. Every repository fails that test. There is exactly one
implementation of each, and the only per-profile difference is the database
capability injected into it, which sits one layer below. The current placement
is a fossil: these repositories used to exist as raw-SQL and ORM twins, the
twins were deliberately collapsed into single bodies, and the now-redundant
plugin-layer address was never revisited.

Three concrete costs today:

1. **Contracts and implementations are unlinked.** An implementation does not
   name the contract it satisfies, so the connection exists only in a dependency
   wiring statement and in prose. IDE "go to implementation" cannot follow it.
   This is what triggered the work.

2. **Contract drift is undetectable.** If an implementation stops providing a
   member its contract declares, nothing catches it — there is no static type
   checker in CI, and the runtime-checkable marker on these contracts is never
   exercised by an actual runtime check. The failure would surface as a missing
   attribute at call time, in production.

3. **Three competing conventions.** The architecture constitution's own
   recommended layout places repositories inside the domain, and several
   repository implementations already live there. New code has no single correct
   answer to "where does a repository go?"

The move also forces a latent layering violation into the open: several
repository bodies depend on data models that are filed under the local-profile
plugin package but are in fact shared by both profiles. That dependency is
invisible only because the guard that would reject it does not scan the plugin
layer. It must be resolved for this change to land, so it is part of this
change.

## User Stories

- As a developer reading a domain service, I want to jump from a repository
  contract to the code that implements it in one keystroke, so that I can
  understand persistence behavior without searching the whole tree.

- As a developer changing a repository contract, I want an implementation that
  no longer satisfies it to fail immediately and by name, so that drift cannot
  reach production as a silently missing value.

- As a developer adding a new repository, I want exactly one obvious place to
  put it, so that I do not have to choose between three existing conventions.

- As a reviewer, I want the layering guards to cover repository code, so that a
  repository cannot quietly depend on profile-specific internals.

- As the engineer applying the matching change to the corporate distribution, I
  want a complete list of every module path that moved, so that I can update it
  in lockstep and keep the broken window to a single commit.

## Acceptance Criteria

- [ ] Every repository implementation resides in the domain that owns its
      contract, in the same package as that contract.
- [ ] Every repository contract declares its members abstract, and every
      corresponding implementation declares that contract as a base.
- [ ] Constructing a repository implementation that omits a contract member
      fails at construction time with an error naming the missing member.
- [ ] No code in the domain layer depends on any profile-specific plugin
      implementation.
- [ ] Components in the plugin layer that are not repositories — composition
      fragments, helper modules, and the one transport client — are not treated
      as repositories, and none of them gains a contract.
- [ ] No repository's runtime behavior changes: same queries, same return
      shapes, same error cases.
- [ ] Every existing architecture guard passes, including the ones covering
      layering, module boundaries, vendor-neutral naming in the domain layer,
      and the file-size allowlist.
- [ ] The full backend test suite passes.
- [ ] No reference anywhere in the repository still points at a moved
      component's old location, in code, tests, or documentation.
- [ ] The change lands as a single commit, and ships with a complete
      old-location-to-new-location map covering every moved component.

## In Scope

- Relocating the 35 repository implementations out of the plugin layer.
- Relocating the 7 plugin-layer components that are not repositories but are
  bound to a repository that moves, without giving them contracts.
- Making repository contract members abstract and having implementations
  declare their contract.
- Resolving name collisions where a moved implementation and its contract would
  claim the same filename.
- Relocating the five shared data model classes that repository bodies depend
  on out of the local-profile plugin package.
- Neutralizing vendor product names in the relocated files' documentation, as
  the domain layer forbids them.
- Updating every dependency-wiring site, importer, guard allowlist, and module
  boundary declaration affected by the moves.
- Producing the old-to-new module path map for the corporate distribution.

## Out of Scope

- Authoring any new contract. Every repository already has one.
- Replacing the structural-typing contract style with a nominal one.
- Introducing a static type checker.
- Splitting oversized repository files.
- Converging the six existing contract-file naming conventions beyond the
  minimum the collisions force.
- Relocating the transport client that currently sits among the repositories; it
  stays where it is and gets a follow-up issue instead.
- Closing the conformance-test gap: repository contracts sit outside the suite
  that enforces conformance tests for plugin contracts. Worth fixing, and the
  larger correctness risk of the two, but a separate change.
- Any change to the dependency manifest.

## Open Questions

1. **Does the corporate distribution carry its own implementation of any of
   these 35 repositories?** This could not be verified — the corporate tree is
   not present in this repository. If any repository does have a second
   implementation there, it is a genuine plugin and must be excluded from the
   move. This needs confirmation before planning, because it changes the file
   set.

2. **How are the two repositories sequenced?** The change is atomic within this
   repository, but the corporate side is a separate commit in a separate
   repository. Which lands first, and is there a window where one is broken?

3. **Do the five relocated data model classes have a preferred destination?**
   There is an existing module holding exactly this kind of shared model, which
   is the obvious candidate — but these classes are currently named for the
   local profile, and several of their dependents are tenant-isolation tests, so
   confirmation is worth having before planning.

4. **Is a follow-up issue wanted for the three-conventions cleanup?** This
   change makes the domain layer the single answer for repositories, but the six
   contract-file naming conventions and the leftover implementations already
   sitting in the domain layer under a different spelling remain untouched.
