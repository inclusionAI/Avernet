# bcs-services-container Context

## Provides

- Production `Services` assembly container.
- Fail-fast `ServicesBuilder` that rejects missing required services.
- Test-only Noop convenience wiring behind the `test-support` feature.

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
