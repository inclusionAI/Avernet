# Local Kubernetes storage

`Storage` continues to mount the shared PVC's per-storage-ID subdirectory.
`mount_points` now requires an explicit local mapping; unsupported mounts fail
before Kubernetes resource creation rather than silently losing files.

Local plugin environment configuration (no production contract change):

- `LOCAL_K8S_PVC_MOUNT_MAPPINGS`: JSON object mapping absolute logical remote
  prefixes to safe relative subPaths in `LOCAL_K8S_SHARED_PVC_NAME`.
  For example `{"/artifacts":"published"}` maps `/artifacts/user/bot` to
  PVC subPath `published/user/bot`, mounted at the requested `local_dir`.
- `LOCAL_K8S_DEFAULT_MOUNTS`: optional JSON list (default `[]`) of platform
  mount declarations, each with exactly `remote_dir`, `local_dir`, `permission`.
  They use the existing `MountPoint` type and are combined with request mounts
  before the same mapping, canonical-path, overlap and permission validation.
  Invalid configuration or a request conflicting with a default fails before
  Kubernetes writes; a request cannot replace a platform read-only default.
  Resolved PVC subPaths of platform read-only defaults are also protected from
  writable request aliases and Bot storage IDs (equal, parent or descendant),
  including different logical prefixes mapping to the same physical subtree.
  These source restrictions apply only when read-only platform defaults exist.
- `LOCAL_K8S_IMAGE_OWNED_MOUNTS`: JSON list of exact `remote_dir`, `local_dir`,
  `permission` declarations already supplied by the runtime image. Matching
  entries are delegated to that image, not shadowed by a second PVC mount.
- `permission=ro/rw` is preserved by the generated Kubernetes volumeMount.
  Unknown paths, traversal, overlapping prefixes and duplicate destinations fail.

The writer (e.g. a simulator Backend) must mount the same PVC subtree at its
artifact output root. Existing container-local artifacts must be preserved and
verified before replacing that mount. These volumes do not emulate object
storage, do not copy files into an image, and do not change runtime startup order.

Change impact: local sandbox creation and simulator deployment configuration.
The production SDK adapter, API models and artifact publication policy are
unchanged. Existing calls without mount_points keep their storage behavior.

Platform defaults are local infrastructure configuration: no engine paths or
production addresses are assumed by the plugin. For example, a configured
`/content` → `shared-content` prefix and the declaration
`{"remote_dir":"/content/repo","local_dir":"/runtime/repo","permission":"ro"}`
mount PVC subPath `shared-content/repo` read-only at `/runtime/repo`. The
configured writer must populate that exact PVC subtree. Existing sandboxes do
not acquire new mounts until recreated through the existing lifecycle.

Bot storage may be the parent of a read-only platform mount (for example a
persistent home with a read-only content subtree), but cannot equal or nest
inside one and shadow it with a writable volume. Defaults remain empty for
deployments that do not opt in.

The runtime FUSE device mount is reserved: default or request mounts cannot
overlap its destination. Mount safety failures occur before PVC/ConfigMap or
Deployment creation.
