"""OSS request-context dependency composition divorced from the large facade."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)



def get_profile_source_from_request(request):
    """
    Create CompositeWorkerProfileSource using stores from OSS provider registry.

    Phase B3 Fix: This function ensures Fusion profile lookup uses the SAME profile_content_store
    instance as Profile CRUD/Activate.

    Why this is needed:
    - APIProfileSource defaults to creating its own SQLiteWorkerProfileContentStore
    - Profile CRUD/Activate uses request.app.state.context.registry worker_profile_content_store
    - This caused instance mismatch: Profile written to Instance A, Fusion reads from Instance B
    - Result: Fusion active_profiles_loaded_count = 0

    Args:
        request: FastAPI Request object with app.state.context.registry

    Returns:
        CompositeWorkerProfileSource with request-context stores

    Raises:
        RuntimeError: If critical stores are missing (no silent fallback)
    """
    try:
        from app.infra.worker_profiles.sources.composite_worker_profile_source import CompositeWorkerProfileSource
        from app.infra.worker_profiles.sources.registry_worker_profile_source import RegistryWorkerProfileSource
        from app.infra.worker_profiles.sources.api_profile_source import APIProfileSource

        # Get stores from OSS provider registry
        registry = request.app.state.context.registry

        # Worker registry store for RegistryWorkerProfileSource
        worker_registry_store = registry.get('worker_registry_store')
        worker_runtime_state_store = registry.get('worker_runtime_state_store')

        # Profile content store for APIProfileSource (CRITICAL: same instance as Profile CRUD)
        worker_profile_content_store = registry.get('worker_profile_content_store')
        if worker_profile_content_store is None:
            raise RuntimeError(
                "OSS registry missing 'worker_profile_content_store'. "
                "Profile CRUD/Activate and Fusion MUST use the same store instance. "
                "Check opensource.py _build_*_providers() registration."
            )

        # Create Registry source (highest priority)
        registry_source = None
        if worker_registry_store and worker_runtime_state_store:
            registry_source = RegistryWorkerProfileSource(
                registry_store=worker_registry_store,
                runtime_state_store=worker_runtime_state_store,
                include_offline=False,  # Only include online workers
            )
            logger.info(
                "[OSS-Safe] RegistryWorkerProfileSource created from request context: "
                "registry_store_id=%d, runtime_state_store_id=%d",
                id(worker_registry_store),
                id(worker_runtime_state_store)
            )

        # Create API source with injected content_store (Phase B3 fix)
        api_source = APIProfileSource(content_store=worker_profile_content_store)
        logger.info(
            "[OSS-Safe] APIProfileSource created with injected content_store: "
            "content_store_id=%d, content_store_type=%s",
            id(worker_profile_content_store),
            type(worker_profile_content_store).__name__
        )

        # Create composite source
        composite = CompositeWorkerProfileSource(
            registry_source=registry_source,
            api_source=api_source,
            file_source=None,  # FILE source disabled
        )

        logger.info(
            "[OSS-Safe] CompositeWorkerProfileSource created from request context: "
            "registry_source=%s, api_source=%s",
            "✓" if registry_source else "✗",
            "✓" if api_source else "✗"
        )

        return composite

    except Exception as e:
        logger.error(
            "[OSS-Safe] Failed to create CompositeWorkerProfileSource from request context: %s",
            e,
            exc_info=True
        )
        raise RuntimeError(
            f"Failed to create CompositeWorkerProfileSource from OSS registry: {e}. "
            f"This is a CRITICAL error - do not fall back to global singletons."
        ) from e


def get_availability_checker_from_request(request):
    """
    Create ParticipantAvailabilityChecker using stores from OSS provider registry.

    P1 Fix: This function ensures Fusion uses the SAME store instances as OSS Worker CRUD.

    Why this is needed:
    - OSS Worker CRUD uses stores from request.app.state.context.registry
    - Old implementation used global singletons from worker_dependencies.py
    - This caused instance mismatch: Worker created in MySQL, Fusion checked in SQLite
    - Result: Fusion couldn't find workers, perspectives = 0

    Phase B3 Fix: Now also uses worker_profile_binding_store from registry.

    Args:
        request: FastAPI Request object with app.state.context.registry

    Returns:
        ParticipantAvailabilityChecker or None (if dependencies unavailable)

    Raises:
        RuntimeError: If critical stores are missing (no silent fallback)
    """
    try:
        from app.application.services.participant_availability_checker import ParticipantAvailabilityChecker

        # Get stores from OSS provider registry (CRITICAL: same as Worker CRUD)
        registry = request.app.state.context.registry

        # Worker registry store (CRITICAL: same instance as Worker CRUD)
        worker_registry_store = registry.get('worker_registry_store')
        if worker_registry_store is None:
            raise RuntimeError(
                "OSS registry missing 'worker_registry_store'. "
                "Worker CRUD and Fusion MUST use the same store instance. "
                "Check opensource.py _build_*_providers() registration."
            )

        # Worker runtime state store (CRITICAL: same instance as Worker CRUD)
        worker_runtime_state_store = registry.get('worker_runtime_state_store')
        if worker_runtime_state_store is None:
            raise RuntimeError(
                "OSS registry missing 'worker_runtime_state_store'. "
                "Worker CRUD and Fusion MUST use the same store instance. "
                "Check opensource.py _build_*_providers() registration."
            )

        # Phase B3 Fix: Worker profile binding store from registry (no more global singleton)
        worker_profile_binding_store = registry.get('worker_profile_binding_store')
        if worker_profile_binding_store is None:
            raise RuntimeError(
                "OSS registry missing 'worker_profile_binding_store'. "
                "Profile CRUD/Activate and Fusion MUST use the same store instance. "
                "Check opensource.py _build_*_providers() registration (Phase B1)."
            )

        logger.info(
            "[OSS-Safe] Created ParticipantAvailabilityChecker with stores from request context: "
            "registry_store_id=%d, runtime_state_store_id=%d, profile_binding_store_id=%d",
            id(worker_registry_store),
            id(worker_runtime_state_store),
            id(worker_profile_binding_store)
        )

        return ParticipantAvailabilityChecker(
            registry_store=worker_registry_store,
            runtime_state_store=worker_runtime_state_store,
            profile_binding_store=worker_profile_binding_store,
        )

    except Exception as e:
        logger.error(
            "[OSS-Safe] Failed to create ParticipantAvailabilityChecker from request context: %s",
            e,
            exc_info=True
        )
        raise RuntimeError(
            f"Failed to create ParticipantAvailabilityChecker from OSS registry: {e}. "
            f"This is a CRITICAL error - do not fall back to global singletons."
        ) from e
