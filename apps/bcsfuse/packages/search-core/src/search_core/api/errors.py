"""Transport-independent failures; provider details are not public messages."""


class UnsupportedSearchOption(ValueError):
    """A syntactically valid option is not supported by this application."""


class SearchUnavailable(RuntimeError):
    """No valid retrieval path is available."""


class SearchOverloaded(RuntimeError):
    """Application execution capacity is occupied; do not enqueue more work."""
