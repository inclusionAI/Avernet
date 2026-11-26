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
from search_core.application.retrieval_config import EmergencyConfig, RetrievalConfig
from search_core.core.observation import events


class RetrievalSwitches(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    keyword_enabled: StrictBool
    vector_enabled: StrictBool
    rerank_enabled: StrictBool
    empty_result_fallback_enabled: StrictBool = True

    def pipeline_switches(self) -> dict[str, bool]:
        return {
            "keyword_enabled": self.keyword_enabled,
            "vector_enabled": self.vector_enabled,
            "rerank_enabled": self.rerank_enabled,
        }

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
    keyword_timeout_ms: float = Field(
        default=500, strict=True, gt=0, le=4000, allow_inf_nan=False
    )
    embedding_singleflight_enabled: StrictBool = True
    card_metadata_timeout_ms: float = Field(
        default=500, strict=True, gt=0, le=4000, allow_inf_nan=False
    )
    reranker_timeout_ms: float = Field(strict=True, gt=0, le=4000, allow_inf_nan=False)
    cache: SearchCachePolicy = Field(default_factory=SearchCachePolicy)


@dataclass(frozen=True)
class EmergencySnapshot:
    policy: EmergencyPolicy
    version: str
    retrieval: RetrievalService
    request_defaults: RetrievalSwitches | None = None
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
        request_defaults: RetrievalSwitches | None = None,
    ):
        if request_defaults is not None:
            defaults = defaults.model_copy(
                update={
                    "retrieval": defaults.retrieval.model_copy(
                        update={
                            "empty_result_fallback_enabled": (
                                request_defaults.empty_result_fallback_enabled
                            )
                        }
                    )
                }
            )
        self._startup_policy = defaults
        self._startup_request_defaults = request_defaults
        self._default_keyword_timeout_ms = defaults.keyword_timeout_ms
        self._default_card_metadata_timeout_ms = defaults.card_metadata_timeout_ms
        self._build = build
        self._build_variants = build_variants
        self._lock = Lock()  # SDK callbacks may arrive outside the event loop.
        self._bound: ContextVar[EmergencySnapshot | None] = ContextVar(
            "search_emergency", default=None
        )
        self._independent_cache = False
        self.current = self._compile(defaults)

    def _compile(self, policy, request_defaults=None):
        if request_defaults is None:
            request_defaults = (
                self.current.request_defaults
                if hasattr(self, "current")
                else self._startup_request_defaults
            )
        values = policy.model_dump()
        if request_defaults is not None:
            values["request_defaults"] = request_defaults.model_dump()
        encoded = json.dumps(values, sort_keys=True).encode()
        variants = self._build_variants(policy) if self._build_variants else {}
        key = tuple(policy.retrieval.pipeline_switches().values())
        retrieval = variants[key] if variants else self._build(policy)
        return EmergencySnapshot(
            policy,
            hashlib.sha256(encoded).hexdigest()[:12],
            retrieval,
            request_defaults,
            variants,
        )

    def for_request(self, overrides):
        snapshot = self._bound.get() or self.current
        configured = snapshot.policy.retrieval.pipeline_switches()
        defaults = (
            snapshot.request_defaults.pipeline_switches()
            if snapshot.request_defaults is not None
            else dict.fromkeys(configured, True)
        )
        switches = {
            name: enabled and overrides.get(name, defaults[name])
            for name, enabled in configured.items()
        }
        # No recall means default TopK; there is nothing to rerank.
        if not switches["keyword_enabled"] and not switches["vector_enabled"]:
            switches["rerank_enabled"] = False
        if switches == configured:
            return snapshot
        retrieval = snapshot.variants.get(tuple(switches.values()))
        if retrieval is None:
            raise UnsupportedSearchOption("request retrieval switches unavailable")
        policy = snapshot.policy.model_copy(
            update={"retrieval": snapshot.policy.retrieval.model_copy(update=switches)}
        )
        return replace(snapshot, policy=policy, retrieval=retrieval)

    def update(self, raw):
        if self._startup_request_defaults is not None:
            return self._update_emergency_switches(raw)
        try:
            policy = (
                EmergencyPolicy.model_validate_json(raw)
                if isinstance(raw, str)
                else EmergencyPolicy.model_validate(raw)
            )
            # Legacy DRM payloads use the host's startup keyword deadline.
            if "keyword_timeout_ms" not in policy.model_fields_set:
                policy = policy.model_copy(
                    update={"keyword_timeout_ms": self._default_keyword_timeout_ms}
                )
            if "card_metadata_timeout_ms" not in policy.model_fields_set:
                policy = policy.model_copy(
                    update={
                        "card_metadata_timeout_ms": self._default_card_metadata_timeout_ms
                    }
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

    def _update_emergency_switches(self, raw):
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(values, dict) and "retrieval" in values:
                # Old hosts published limits and deadlines with permissions.
                # Ignore those known fields, without losing emergency denials.
                values = {
                    key: value
                    for key, value in values.items()
                    if key == "retrieval" or key not in EmergencyPolicy.model_fields
                }
                if isinstance(values["retrieval"], dict):
                    values["retrieval"] = {
                        key: value
                        for key, value in values["retrieval"].items()
                        if key != "empty_result_fallback_enabled"
                    }
            config = EmergencyConfig.model_validate(values)
            switches = config.retrieval.model_dump()
            if not switches["keyword_enabled"] and not switches["vector_enabled"]:
                switches["rerank_enabled"] = False
            with self._lock:
                policy = self.current.policy.model_copy(
                    update={
                        "retrieval": self.current.policy.retrieval.model_copy(
                            update=switches
                        )
                    }
                )
                if policy != self.current.policy:
                    self.current = self._compile(policy)
                events.record(
                    "search_emergency_config",
                    "config",
                    "applied",
                    version=self.current.version,
                    **config.model_dump(),
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

    def update_retrieval(self, raw):
        """Replace normal settings; omitted keys reset to host startup defaults."""
        if self._startup_request_defaults is None:
            raise UnsupportedSearchOption(
                "separate retrieval configuration unavailable"
            )
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(values, dict):
                raise TypeError("retrieval configuration must be an object")
            defaults = self._startup_policy.model_dump(exclude={"cache", "retrieval"})
            defaults.update(self._startup_request_defaults.model_dump())
            config = RetrievalConfig.model_validate({**defaults, **values})
            fields = config.model_dump()
            switch_fields = {
                name: fields.pop(name)
                for name in defaults
                if name in RetrievalSwitches.model_fields
            }
            # Normal defaults can request reranking without recall; selection
            # turns it off unless a caller explicitly enables a recall branch.
            request_defaults = self._startup_request_defaults.model_copy(
                update=switch_fields
            )
            with self._lock:
                fields["retrieval"] = self.current.policy.retrieval.model_copy(
                    update={
                        "empty_result_fallback_enabled": config.empty_result_fallback_enabled
                    }
                )
                policy = self.current.policy.model_copy(update=fields)
                if (
                    policy != self.current.policy
                    or request_defaults != self.current.request_defaults
                ):
                    self.current = self._compile(policy, request_defaults)
                events.record(
                    "search_retrieval_config",
                    "config",
                    "applied",
                    version=self.current.version,
                    **config.model_dump(),
                )
            return True
        except (ValueError, TypeError):
            events.record(
                "search_retrieval_config",
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
        return self._bound.get() or self.for_request({})

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
