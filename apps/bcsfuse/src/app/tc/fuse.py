"""Small compatibility adapters from legacy Fuse consumers to current TC data."""

from app.application.services.participant_availability_checker import (
    ParticipantAvailability,
)
from app.domain.models.worker_runtime_state import WorkerRuntimeState
from app.domain.services.worker_profile_retrieval_service import (
    RetrievalResponse,
    RetrievalResult,
)
from app.infra.config.feature_flags import FeatureFlags


class TcProfileRetrieval:
    """G5 consumes the shared recommendation result without another scorer or scan."""

    def __init__(self, search, source):
        self.search, self.source = search, source

    def retrieve(
        self,
        question,
        mode,
        top_k=None,
        profile_keys=None,
        skill_filter=None,
        min_score=0.0,
        strict_participants=None,
    ):
        response = self.search.recommend(
            question,
            mode=mode,
            participants=profile_keys,
            max_candidates=100 if skill_filter else top_k or 5,
            strict_participants=bool(strict_participants),
        )
        results = []
        for candidate in response.recommendations:
            row = self.search.catalog.get(candidate.worker_id)
            if row is None:
                continue
            if skill_filter and not set(skill_filter).intersection(
                skill["name"] for skill in row.card.source_card.get("skills", [])
            ):
                continue
            profile = self.source.get_profile(
                candidate.worker_id,
                row.card.source_card.get("profile_id", "default"),
            )
            if profile is not None:
                results.append(
                    RetrievalResult(
                        profile=profile,
                        total_score=candidate.score,
                        rank=len(results) + 1,
                    )
                )
        if top_k is not None:
            results = results[:top_k]
        return RetrievalResponse(results=results, question=question, mode=mode)


class TcAvailabilityChecker:
    """Resolve full Worker IDs and legacy keys through the current Card catalog."""

    def __init__(self, catalog, registry_store):
        self.catalog = catalog
        self._registry_store = registry_store

    def check_availability(self, participant_id):
        if not FeatureFlags.is_explicit_participant_availability_warning_enabled():
            return ParticipantAvailability(
                participant_id=participant_id, is_available=True
            )
        worker_id = self.catalog.resolve(participant_id)
        row = self.catalog.get(worker_id) if worker_id is not None else None
        if row is None:
            return ParticipantAvailability(
                participant_id=participant_id,
                is_available=False,
                unavailability_reason="unregistered",
            )
        state = WorkerRuntimeState(row.card.source_card.get("runtime_state", "offline"))
        online = state == WorkerRuntimeState.ONLINE
        return ParticipantAvailability(
            participant_id=participant_id,
            worker_id=worker_id,
            runtime_state=state,
            is_registered=True,
            is_available=online,
            unavailability_reason=None if online else f"runtime_state={state.value}",
        )

    def check_batch(self, participant_ids):
        return {key: self.check_availability(key) for key in participant_ids}

    def get_unavailable_participants(self, participant_ids):
        return [
            value
            for value in self.check_batch(participant_ids).values()
            if not value.is_available
        ]


class TcProfileFilter:
    """Preserve the old filter entry point using only supplied current identities."""

    def __init__(self, checker, enabled):
        self.checker, self.enabled = checker, enabled

    def is_profile_allowed(self, profile_key):
        return (
            not self.enabled
            or self.checker.check_availability(profile_key).is_available
        )

    def get_allowed_profile_keys(self, all_profile_keys=None):
        return {key for key in all_profile_keys or [] if self.is_profile_allowed(key)}

    def filter_profiles(self, profiles):
        return [
            profile
            for profile in profiles
            if self.is_profile_allowed(profile.profile_key)
        ]
