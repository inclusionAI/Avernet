"""One immutable emergency snapshot per request; no remote I/O on search."""

import hashlib
import json
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from search_core.api.errors import UnsupportedSearchOption
from search_core.api.services import RetrievalService
from search_core.core.observation import events


class RetrievalSwitches(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    keyword_enabled: StrictBool
    vector_enabled: StrictBool
    rerank_enabled: StrictBool

    @model_validator(mode="after")
    def has_recall(self):
        if not self.keyword_enabled and not self.vector_enabled and self.rerank_enabled:
            raise ValueError("reranking requires a recall branch")
        return self


class CachePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ttl_ms: int = Field(strict=True, ge=0, le=3600000)
    # Omitted capacities retain this application's startup limits.
    max_entries: int | None = Field(default=None, strict=True, ge=1)
    max_size_mb: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_enabled(cls, value):
        if not isinstance(value, dict) or "enabled" not in value:
            return value
        value = dict(value)
        enabled = value.pop("enabled")
        if type(enabled) is not bool:
            raise ValueError("cache enabled must be a boolean")
        ttl = value.get("ttl_ms")
        # Invalid TTLs still go through strict field validation, even if disabled.
        if not enabled and type(ttl) is int and 0 <= ttl <= 3600000:
            value["ttl_ms"] = 0
        return value

    @property
    def enabled(self):
        return self.ttl_ms > 0


class SearchCachePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: StrictBool = True
    result: CachePolicy = CachePolicy(ttl_ms=0)
    query_embedding: CachePolicy = CachePolicy(ttl_ms=0)
    card_metadata: CachePolicy = CachePolicy(ttl_ms=60000)
    default_candidates: CachePolicy = CachePolicy(ttl_ms=60000)


class EmergencyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_inflight_requests: int = Field(strict=True, ge=0, le=4096)
    max_results: int = Field(default=20, strict=True, ge=1, le=100)
    retrieval: RetrievalSwitches
    embedding_timeout_ms: float = Field(strict=True, gt=0, le=4000, allow_inf_nan=False)
    embedding_singleflight_enabled: StrictBool = True
    reranker_timeout_ms: float = Field(strict=True, gt=0, le=4000, allow_inf_nan=False)
    cache: SearchCachePolicy = Field(default_factory=SearchCachePolicy)


@dataclass(frozen=True)
class EmergencySnapshot:
    policy: EmergencyPolicy
    version: str
    retrieval: RetrievalService
    variants: dict[tuple[bool, bool, bool], RetrievalService] = field(
        default_factory=dict
    )


class EmergencyControl:
    """Compile only on updates; existing requests retain their original service."""

    def __init__(
        self,
        defaults: EmergencyPolicy,
        build: Callable,
        *,
        build_variants: Callable | None = None,
    ):
        self._build = build
        self._build_variants = build_variants
        self._lock = Lock()  # SDK callbacks may arrive outside the event loop.
        self._bound: ContextVar[EmergencySnapshot | None] = ContextVar(
            "search_emergency", default=None
        )
        self._independent_cache = False
        self.current = self._compile(defaults)

    def _compile(self, policy):
        encoded = json.dumps(policy.model_dump(), sort_keys=True).encode()
        variants = self._build_variants(policy) if self._build_variants else {}
        key = tuple(policy.retrieval.model_dump().values())
        retrieval = variants[key] if variants else self._build(policy)
        return EmergencySnapshot(
            policy, hashlib.sha256(encoded).hexdigest()[:12], retrieval, variants
        )

    def for_request(self, overrides):
        snapshot = self.snapshot()
        switches = {
            name: enabled and overrides.get(name, True)
            for name, enabled in snapshot.policy.retrieval.model_dump().items()
        }
        # No recall means default TopK; there is nothing to rerank.
        if not switches["keyword_enabled"] and not switches["vector_enabled"]:
            switches["rerank_enabled"] = False
        if switches == snapshot.policy.retrieval.model_dump():
            return snapshot
        retrieval = snapshot.variants.get(tuple(switches.values()))
        if retrieval is None:
            raise UnsupportedSearchOption("request retrieval switches unavailable")
        policy = snapshot.policy.model_copy(
            update={"retrieval": RetrievalSwitches.model_validate(switches)}
        )
        return replace(snapshot, policy=policy, retrieval=retrieval)

    def update(self, raw):
        try:
            policy = (
                EmergencyPolicy.model_validate_json(raw)
                if isinstance(raw, str)
                else EmergencyPolicy.model_validate(raw)
            )
            # Zero remains a local startup option, never a DRM bypass.
            if policy.max_inflight_requests == 0:
                raise ValueError("DRM cannot disable admission")
            with self._lock:
                # Legacy emergencyConfig.cache is only a migration fallback.
                if self._independent_cache:
                    policy = policy.model_copy(
                        update={"cache": self.current.policy.cache}
                    )
                if policy == self.current.policy:
                    return True
                replacement = self._compile(policy)
                self.current = replacement
                events.record(
                    "search_emergency_config",
                    "config",
                    "applied",
                    version=replacement.version,
                    **policy.model_dump(),
                )
            return True
        except (ValueError, TypeError):
            events.record(
                "search_emergency_config",
                "config",
                "rejected",
                retained_version=self.current.version,
            )
            return False

    def update_cache(self, raw):
        """Independent cacheConfig wins over legacy nested cache settings."""
        try:
            cache = (
                SearchCachePolicy.model_validate_json(raw)
                if isinstance(raw, str)
                else SearchCachePolicy.model_validate(raw)
            )
            with self._lock:
                policy = self.current.policy.model_copy(update={"cache": cache})
                replacement = (
                    self.current
                    if policy == self.current.policy
                    else self._compile(policy)
                )
                first = not self._independent_cache
                changed = replacement is not self.current
                self.current = replacement
                self._independent_cache = True
                if first or changed:
                    events.record(
                        "search_cache_config",
                        "config",
                        "applied",
                        version=replacement.version,
                        **cache.model_dump(),
                    )
            return True
        except (ValueError, TypeError):
            events.record(
                "search_cache_config",
                "config",
                "rejected",
                retained_version=self.current.version,
            )
            return False

    @contextmanager
    def bind(self, snapshot):
        token = self._bound.set(snapshot)
        try:
            yield
        finally:
            self._bound.reset(token)

    def snapshot(self):
        return self._bound.get() or self.current

    async def retrieve(self, request):
        snapshot = self.snapshot()
        result = await snapshot.retrieval.retrieve(request)
        if not snapshot.policy.retrieval.rerank_enabled:
            result = replace(
                result,
                status="degraded",
                reasons=tuple(dict.fromkeys((*result.reasons, "reranker_disabled"))),
            )
        return result
