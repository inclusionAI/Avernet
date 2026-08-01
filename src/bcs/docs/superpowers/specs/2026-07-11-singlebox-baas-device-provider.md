# Singlebox BaaS Device Provider

## Problem

`DeployProfile.SINGLEBOX` currently shares `TestDevicesModule` with unit tests.
That module builds a BaaS-backed `LocalDeviceService`, registers it under the
`local` provider key, and persists `device_provider=local`.

The persisted provider is a domain fact, not a deployment-location flag.
Singlebox device, bot, and process lifecycles are owned by the local BaaS
service (`LocalPaasService` performs the host process spawn), so the binding
must persist `device_provider=baas` while the independent runtime axes remain:

- `DEPLOY_PROFILE=singlebox`
- `SERVER_ENV=dev`
- `device_provider=baas`

## Contract

1. `test` keeps `TestDevicesModule` and its local/in-memory doubles.
2. `singlebox` installs a dedicated `SingleboxDevicesModule`.
3. The singlebox `DeviceServiceRouter` registers only
   `baas -> BaasDeviceService`, with `baas` as its default provider.
4. Singlebox create-time routing always selects `baas`.
5. `DeviceAccessor` resolves to `BaasDeviceAccessor` so filesystem, sync, and
   connection consumers agree with the persisted provider.
6. Local BaaS configuration still decides where processes run; no caller
   derives provider identity from `DeployProfile` or `SERVER_ENV`.
7. Unit-test-only local bindings must not leak into the singlebox runtime.

## Compatibility

Existing singlebox databases are recreated by the startup script, so no
`local -> baas` data migration is required. Production profiles and their
ARCA/BaaS routing are unchanged.

PR #62 will stack on this change and verify the live BaaS lifecycle instead of
asserting that a singlebox binding is rejected as a non-BaaS provider.
