# bcs-session Context

## Provides

- The physical-session application facades owned by the session domain:
  the durable `SessionManagementServiceImpl` and the authority-hooked
  `SessionLaunchApplication` (Human launch authorization answers through
  the centralized `BotAuthorityHook`, never a `created_by` fallback).
- A Noop pair for builder defaults and tests: `NoopSessionManagementService`
  and `NoopSessionLaunchService`.

## Consumes

- `bcs-service-api` session/launch application contracts and the
  `BotOperationContext` audit identity the write lanes require.

## Forbidden dependencies

- Concrete stores (session stores live in `bcs-session-store`).
- `bootstrap/bcs`, `adapters/*`, business SQL of any kind.

## Runtime ownership

This crate owns session use-case orchestration only; persistence semantics
(including the same-transaction session audit writes) belong to the session
store crates.

### Noop failure posture

The Noops evolve EXPLICITLY with the `SessionManagementService` trait —
no inherited trait default may stand in for a required Noop method. Write,
claim and CAS lanes answer fail-closed errors (`Err`); the only `Ok` answers
are reads over the Noop store, which cannot hold rows (`get` = `None`,
list-cursors and collected-state maps = empty vectors, `delete` = `false`).
Any future trait method must update this file's Noop by hand, keeping the
fail-closed posture instead of an inherited empty-`Vec` default.