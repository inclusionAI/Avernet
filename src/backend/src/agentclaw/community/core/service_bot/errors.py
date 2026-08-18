"""Stable public-facing errors raised by the service-Bot domain."""


class ServicePublicationError(Exception):
    """Base error for service-publication orchestration."""


class ServicePublicationNotFoundError(ServicePublicationError):
    """The Bot/publication pair is absent or hidden from the caller."""


class ServicePublicationConflictError(ServicePublicationError):
    """The requested action conflicts with the publication's current state."""


class ServicePublicationLockedError(ServicePublicationError):
    """A collaborative draft write was attempted without holding its lock."""


class ServicePublicationUnsupportedError(ServicePublicationError):
    """The Bot cannot participate in the service publication lifecycle."""


class ServiceContainerNotFoundError(ServicePublicationError):
    """The addressed service-Bot container is absent or hidden."""


class ServiceContainerConflictError(ServicePublicationError):
    """The container operation conflicts with its current runtime state."""


class ServiceContainerUpstreamError(ServicePublicationError):
    """The runtime provider failed while serving a container operation."""
