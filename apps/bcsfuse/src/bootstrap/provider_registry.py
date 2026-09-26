"""
Provider Registry

Central registry for all providers in the OSS provider graph.
"""
from typing import Any, Dict, List, Optional


class ProviderRegistry:
    """
    Provider Registry for managing providers.

    Provides a central registry for all providers in the system.
    Supports registration, retrieval, and listing of providers.
    """

    def __init__(self):
        """Initialize provider registry."""
        self._providers: Dict[str, Any] = {}

    def register(self, name: str, provider: Any) -> None:
        """Register a provider.

        Args:
            name: Provider name/key.
            provider: Provider instance.
        """
        self._providers[name] = provider

    def get(self, name: str) -> Optional[Any]:
        """Get a provider by name.

        Args:
            name: Provider name/key.

        Returns:
            Provider instance if exists, None otherwise.
        """
        return self._providers.get(name)

    def has(self, name: str) -> bool:
        """Check if a provider exists.

        Args:
            name: Provider name/key.

        Returns:
            True if provider exists, False otherwise.
        """
        return name in self._providers

    def keys(self) -> List[str]:
        """Get all registered provider keys.

        Returns:
            List of provider keys.
        """
        return list(self._providers.keys())

    def unregister(self, name: str) -> bool:
        """Unregister a provider.

        Args:
            name: Provider name/key.

        Returns:
            True if provider was removed, False if not found.
        """
        if name in self._providers:
            del self._providers[name]
            return True
        return False

    def clear(self) -> None:
        """Clear all providers."""
        self._providers.clear()

    def get_all(self) -> Dict[str, Any]:
        """Get all providers.

        Returns:
            Dict of all providers.
        """
        return self._providers.copy()

    def __repr__(self) -> str:
        """String representation."""
        return f"ProviderRegistry(providers={list(self._providers.keys())})"