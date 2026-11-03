"""DWS rollout identity policy v1, selected only at the composition root.

The default preserves opaque account IDs exactly. Internal deployments may
override comparison keys for trusted staff IDs. Keys are only for rollout
membership: never rewrite authenticated actors, signatures or Bot ownership.
Implementations must be deterministic, side-effect free and reject invalid IDs
with an empty key. The caller must never grant membership on an empty key.
"""


class DwsRolloutIdentity:
    def key(self, value: str) -> str:
        return value
