# Deployment image overrides for update publishes

For bot updates (including caller-driven release/rollback through UPDATE) and
targeted device updates (UPDATE_DEVICE), `DeployConfig.docker_image` comes from
**the current update request**, not the previous deployment:

| Update request | Image override used by the deployment |
| --- | --- |
| `config` omitted or `null` when an update is otherwise requested | Cleared |
| `config.deploy_config` omitted or `null` | Cleared |
| `docker_image` omitted or `null` | Cleared |
| `docker_image` supplied | The supplied image |

Clearing an override lets the existing template/provider default select the
image; it does **not** send an empty-string image to the provider. Clients that
intentionally pin an image must supply it on every update request. Metadata-only
bot edits that do not create a publish are unchanged.

Other deployment fields keep their existing sparse-merge behavior: environment
variables, mounts, TTL, resource settings and hooks are retained. The publish's
resolved deployment snapshot takes precedence over a stale or subsequently
changed Bot row. The direct publish API applies the same update rule. New update
publishes explicitly save the deployment snapshot, including a null snapshot
when no deployment config exists; legacy publishes that lack the snapshot key
retain their previous fallback behavior. Configuration persistence must succeed before a record becomes
PROCESSING or provisioning/retry can use the new configuration. A failed write
terminates the update record instead of retrying with an older image.

## Deliberately deferred behavior

- Scale keeps its existing call conditions: the merge helper is called only when
  both `config` and `config.deploy_config` are supplied. In that case omission or
  null of `docker_image` clears the old override. **Missing/null outer scale
  config or deploy_config can still inherit the previous image.** Fixing that
  missing-configuration path is separate follow-up work; this PR does not change
  OCB request parameters. Scale-down does not re-provision existing devices.
- Native RESTART is unchanged and retains the persisted device image and its
  existing retry behavior. It is not an UPDATE release/rollback.
- No schema migration, global historical-data cleanup or old-publish rewrite.

The original service layout is retained. The existing oversized service and legacy test files
are documented in the [scoped file-size waiver](../../../docs/arch/waivers/2026-10-27-baas-update-image-scope.md),
which requires maintainer approval; no CI thresholds or tests are weakened.
