"""Version inspection request errors, mapped by HTTP delivery only."""


class EngineVersionStageUnsupportedError(ValueError):
    """Only personal/workspace service drafts are in scope."""


class EngineVersionInstanceSelectionError(ValueError):
    """The requested instance is absent or the binding is ambiguous."""
