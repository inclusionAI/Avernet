"""Two independent synchronization intervals; invalid DRM retains the last policy."""

from threading import Lock

from pydantic import BaseModel, ConfigDict, Field, model_validator

from search_core.core.observation import events


class IndexSyncPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    incremental_interval_ms: int = Field(strict=True, ge=1, le=300000)
    reconcile_interval_ms: int = Field(strict=True, ge=1, le=86400000)

    @model_validator(mode="after")
    def check_order(self):
        if self.reconcile_interval_ms < self.incremental_interval_ms:
            raise ValueError(
                "reconciliation must not run more frequently than incremental sync"
            )
        return self


class IndexSyncControl:
    def __init__(self, *, incremental_interval_ms=5000):
        self.current = IndexSyncPolicy(
            incremental_interval_ms=incremental_interval_ms,
            reconcile_interval_ms=max(60000, incremental_interval_ms),
        )
        self._lock = Lock()

    def update(self, raw):
        try:
            policy = (
                IndexSyncPolicy.model_validate_json(raw)
                if isinstance(raw, str)
                else IndexSyncPolicy.model_validate(raw)
            )
            # Sub-second intervals are useful in local tests, not operational pushes.
            if policy.incremental_interval_ms < 1000:
                raise ValueError("DRM sync interval must be at least one second")
            with self._lock:
                if policy != self.current:
                    self.current = policy
                    events.record(
                        "search_index_sync_config",
                        "config",
                        "applied",
                        **policy.model_dump(),
                    )
            return True
        except (ValueError, TypeError):
            events.record(
                "search_index_sync_config",
                "config",
                "rejected",
                **self.current.model_dump(),
            )
            return False
