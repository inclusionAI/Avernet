from typing import Protocol


class StartupProvider(Protocol):
    """Public startup provider contract.

    Implementations may be OSS defaults (no-op) or internal plugins (Sofapy).
    Public code must depend on this contract, not internal startup SDKs.
    """

    async def initialize(self) -> None:
        """Initialize the application startup.

        This method is called during application startup to initialize
        any necessary infrastructure, services, or resources.

        For OSS implementations, this may be a no-op.
        For internal implementations, this may initialize Sofapy, MOSN, etc.
        """
        ...

    async def shutdown(self) -> None:
        """Shutdown the application gracefully.

        This method is called during application shutdown to clean up
        any resources, connections, or services.

        For OSS implementations, this may be a no-op.
        For internal implementations, this may shutdown Sofapy, MOSN, etc.
        """
        ...