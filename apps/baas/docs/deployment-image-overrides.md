# Deployment image overrides

`DeployConfig.docker_image` is an override for **the current deployment**, not
an image pin inherited from the previous deployment.

For bot updates (including caller-driven release/rollback), scale operations,
and targeted device updates:

| Request | Image override used by the deployment |
| --- | --- |
| `config` omitted or `null` | Cleared |
| `config.deploy_config` omitted or `null` | Cleared |
| `docker_image` omitted or `null` | Cleared |
| `docker_image` supplied | The supplied image |

Clearing an override means that the existing template/provider default selects
the image. It does **not** mean sending an empty-string image to the provider.
Clients that intentionally pin an image must include it in every deployment
request. Metadata-only bot edits that do not create a deployment are unchanged.

Other deployment fields retain their existing sparse-merge behavior: environment
variables, mounts, TTL, resource settings and hooks are not cleared merely
because the image was omitted. The resolved configuration is captured by the
publish so approval delays do not reintroduce an older Bot image.

Native bot restart has no image input. Before restarting a device, it clears the
persisted device image override while retaining the remaining configuration.
Configuration persistence must succeed before provisioning or its inline retry;
a failed write terminates that device's publish record instead of falling back
to a stale image.

No schema migration, global historical-data cleanup, or rewrite of old publish
records is required. Existing explicit caller image-selection/pinning policies
remain unchanged. Scale-down does not re-provision existing devices.
