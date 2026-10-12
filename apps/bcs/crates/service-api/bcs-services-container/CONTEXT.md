# bcs-services-container Context

## Provides

- Production `Services` assembly container.
- Fail-fast `ServicesBuilder` that rejects missing required services.
- Test-only Noop convenience wiring behind the `test-support` feature.
- The plan-Task-13/14 authority facades as bundle slots: `bot_manager` and
  `ownership_transfer` are required (fail-fast like every other slot), while
  `team_manager_sync` is optional by contract — `None` keeps the
  credential-gated team write routes unmounted (never anonymous). The
  `test-support` Noop fill answers fail-closed (Forbidden / fixed-code deny),
  never an empty allowance.

## Consumes

- `bcs-service-api` contract traits and DTOs.
- `bcs-test-support` only when tests or the `test-support` feature request Noop wiring.

## Forbidden dependencies

- `bootstrap/bcs`
- `adapters/*`
- Concrete `services/*`
- Concrete `plugins/*`

## Runtime ownership

This crate owns composition shape only. It must not implement business policy,
read runtime environment variables, or choose concrete infrastructure plugins.
