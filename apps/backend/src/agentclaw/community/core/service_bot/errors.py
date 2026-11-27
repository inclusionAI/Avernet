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


class ServicePublishWorkflowNotFoundError(ServicePublicationError):
    """The addressed BaaS publish workflow is absent or hidden from the caller.

    Distinct from :class:`ServicePublicationNotFoundError`: that type reports
    a local Bot/publication pair that does not exist, while this one relays the
    upstream BaaS provider's own "publish workflow not found" answer.
    """


class ServicePublishUpstreamError(ServicePublicationError):
    """The BaaS provider failed while serving a publish-progress query."""


class ServicePublicationGateBlockedError(ServicePublicationError):
    """The release is blocked by the unified service-Bot evaluation gate."""

    def __init__(self, gate_result: dict[str, object]):
        self.gate_result = dict(gate_result)
        super().__init__(
            str(self.gate_result.get("display_message") or "评测未通过，无法推进发布")
        )


class ServicePublicationGatePendingError(ServicePublicationError):
    """The unified evaluation gate has a decision still in progress."""

    def __init__(self, gate_result: dict[str, object]):
        self.gate_result = dict(gate_result)
        super().__init__(
            str(self.gate_result.get("display_message") or "评测认证处理中，请稍后再试")
        )
