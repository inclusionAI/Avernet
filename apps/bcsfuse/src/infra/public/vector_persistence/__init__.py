"""Public vector persistence providers for open-core.

This package provides OSS-compatible vector persistence implementations:
- Local file system-based vector persistence for development and testing

Internal production implementations (ZDAS-backed) will be provided
in bcsfuse-internal.
"""

from .local_vector_persistence_provider import LocalVectorPersistenceProvider

__all__ = [
    "LocalVectorPersistenceProvider",
]