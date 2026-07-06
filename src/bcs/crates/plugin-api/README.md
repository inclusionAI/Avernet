# BCS Plugin API

`crates/plugin-api/*` contains infrastructure plugin contracts. These crates
define the interfaces BCS services call when they need external infrastructure
capabilities such as cache or database access.

## Dependency Rule

- Services may depend on `bcs-cache-api` and `bcs-db-api`.
- Services must not depend on concrete plugin implementation crates such as
  `bcs-cache-local` or `bcs-db-local`.
- Concrete implementation selection belongs to the composition root in
  `crates/bootstrap/bcs`.
- Plugin API crates must not depend on internal SDKs, private infrastructure
  providers, service implementations, delivery adapters, or bootstrap.

## Contract Shape

- `bcs-cache-api` owns cache primitives: byte values, hash fields, TTL, and
  conditional writes. Business cache keys and invalidation policy belong to the
  service store layer.
- `bcs-db-api` is a driver-level SQL execution contract. It abstracts driver,
  connection, transaction, health, and row conversion concerns, but does not
  promise SQL dialect portability.
- `bcs-user-directory-api` owns user-directory lookup primitives such as
  `staff_no -> nick_name`; business fallbacks and actor writes belong to the
  consuming service.

SQL dialect portability, query builders, and ORM-style mapping should live above
`DbPlugin`, usually inside service-owned repositories/stores. `DbPlugin` should
remain a small infrastructure port, not a business persistence model.

## Verification

Every plugin contract must have reusable contract tests in
`crates/test-support/bcs-test-support`. Every concrete implementation should
mount those tests in its own crate.
