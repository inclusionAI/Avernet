# Local Kubernetes storage

`Storage` continues to mount the shared PVC's per-storage-ID subdirectory.
`mount_points` now requires an explicit local mapping; unsupported mounts fail
before Kubernetes resource creation rather than silently losing files.

Local plugin environment configuration (no production contract change):

- `LOCAL_K8S_PVC_MOUNT_MAPPINGS`: JSON object mapping absolute logical remote
  prefixes to safe relative subPaths in `LOCAL_K8S_SHARED_PVC_NAME`.
  For example `{"/artifacts":"published"}` maps `/artifacts/user/bot` to
  PVC subPath `published/user/bot`, mounted at the requested `local_dir`.
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
