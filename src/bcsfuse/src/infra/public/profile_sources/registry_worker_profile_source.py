"""
Registry Worker Profile Source - OSS Wrapper

Wraps existing registry-based profile source for OSS compatibility.
"""
from src.infra.worker_profiles.sources.registry_worker_profile_source import RegistryWorkerProfileSource as _RegistryWorkerProfileSource


class RegistryWorkerProfileSource(_RegistryWorkerProfileSource):
    """
    Registry Worker Profile Source for OSS.

    This is a thin wrapper around the existing registry-based profile source
    to maintain consistent naming and future extensibility.

    Loads worker profiles from the worker registry store.
    Suitable for runtime and production deployments.
    """

    pass