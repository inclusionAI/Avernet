"""No-op storage policy for composer tests that never exercise storage."""


class NoopStoragePolicy:
    """Passthrough: context and storage go through unchanged."""

    def resolve_deploy_context(self, ctx):
        return ctx

    def apply_to_storage(self, storage, ctx):
        return storage
