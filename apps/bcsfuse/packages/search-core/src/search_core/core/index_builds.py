"""Durable isolated generations; source conversion and storage are injected."""

import asyncio
import json
import logging
import math
from time import monotonic
from uuid import uuid4

from search_core.api.index_builds import IndexBuildConflict
from search_core.config.index_builds import IndexBuildConfig
from search_core.plugin_api.index_builds import IndexBuildRepository, IndexBuildSource

logger = logging.getLogger(__name__)


class _BuildOwnershipLost(Exception):
    pass


class IndexBuildService:
    def __init__(
        self,
        source: IndexBuildSource,
        builds: IndexBuildRepository,
        embedding_factory,
        *,
        filter_fields,
        conversion_version: str,
        lease_seconds=60.0,
    ):
        if not math.isfinite(lease_seconds) or lease_seconds < 0.1:
            raise ValueError("build lease must be at least 0.1 seconds")
        self.source, self.builds = source, builds
        self.embedding_factory = embedding_factory
        self.filter_fields = filter_fields
        self.lease_seconds = lease_seconds
        self.conversion_version = conversion_version

    def create(self, build_id, model, dimension, *, filter_fields=None):
        # Retrying an existing version must not pick up changed deployment defaults.
        if filter_fields is None:
            previous = self.builds.get(build_id)
            filter_fields = (
                previous.config.filter_fields if previous else self.filter_fields
            )
        return self.builds.create(
            IndexBuildConfig(
                build_id=build_id,
                embedding_model=model,
                embedding_dimension=dimension,
                conversion_version=self.conversion_version,
                filter_fields=tuple(filter_fields),
            )
        )

    def status(self, build_id):
        row = self.builds.get(build_id)
        if row is None:
            raise KeyError(build_id)
        return row

    def select(self, build_id):
        row = self.builds.get(build_id)
        if row is None or row.status != "completed":
            raise ValueError("selected index build does not exist or is incomplete")
        self._check_converter(row.config)
        return row.config

    def _check_converter(self, config):
        if config.conversion_version != self.conversion_version:
            raise ValueError("selected build requires a different source converter")

    def default(self, model, dimension):
        """Freeze default configuration without enqueueing or retrying a build.

        Normal source sync fills missing/stale artifacts independently of startup
        restoration. Existing configuration wins over changed deployment defaults.
        """
        row = self.builds.get("default")
        if row is None:
            row = self.builds.create(
                IndexBuildConfig(
                    build_id="default",
                    embedding_model=model,
                    embedding_dimension=dimension,
                    conversion_version=self.conversion_version,
                    filter_fields=tuple(self.filter_fields),
                ),
                enqueue=False,
            )
        self._check_converter(row.config)
        return row.config

    def _sources(self, check=lambda: None):
        after = ""
        while True:
            check()
            rows = self.source.page(after, 100)
            for row in rows:
                # Older hosts supplied only ID/revision and counted every row.
                yield (*row, False) if len(row) == 2 else row
            if len(rows) < 100:
                return
            after = rows[-1][0]

    def run_once(self, stop=None, *, build_id=""):
        jobs = [self.status(build_id)] if build_id else self.builds.candidates()
        for job in jobs:
            cfg = job.config
            if cfg.conversion_version != self.conversion_version:
                continue
            token = uuid4().hex
            if not self.builds.claim(cfg.build_id, token, self.lease_seconds):
                continue
            self._run_job(cfg, token, stop)
            return  # One job pass per poll; normal source sync remains independent.

    def _run_job(self, cfg, token, stop):
        done, total = 0, 0
        next_renewal = 0.0

        def renew():
            nonlocal next_renewal
            if monotonic() < next_renewal:
                return
            if not self.builds.progress(
                cfg.build_id, token, done, total, self.lease_seconds
            ):
                raise _BuildOwnershipLost
            next_renewal = monotonic() + self.lease_seconds / 3

        try:
            sources = list(self._sources(renew))
            total = sum(not deleted for _, _, deleted in sources)
            with self.embedding_factory(
                cfg.embedding_model, cfg.embedding_dimension
            ) as model:
                if model.dimension != cfg.embedding_dimension:
                    raise ValueError("build model dimension mismatch")
                for key, revision, deleted in sources:
                    if stop is not None and stop.is_set():
                        self.builds.finish(
                            cfg.build_id, token, "pending", done, total, ""
                        )
                        return
                    if not self.builds.progress(
                        cfg.build_id, token, done, total, self.lease_seconds
                    ):
                        return  # Another process owns the continuation now.
                    existing = self.source.prepared_revision(cfg.index_key, key)
                    if existing != revision:
                        try:
                            self.source.rebuild(key, cfg, model, self.lease_seconds)
                        except IndexBuildConflict:
                            continue  # Updated source or another producer; next pass retries.
                    done += int(not deleted)
            # Recount against source revisions, not just work attempted.
            done, total = 0, 0
            all_current = True
            for key, revision, deleted in self._sources(renew):
                renew()
                current = self.source.prepared_revision(cfg.index_key, key) == revision
                all_current = all_current and current
                if not deleted:
                    total += 1
                    done += int(current)
            # Deletion work must finish even though it is excluded from counters.
            status = "completed" if all_current else "pending"
            if not self.builds.finish(cfg.build_id, token, status, done, total, ""):
                return
            logger.info(
                json.dumps(
                    {
                        "event": "search_index_build",
                        "build_id": cfg.build_id,
                        "outcome": status,
                        "completed": done,
                        "total": total,
                    }
                )
            )
        except _BuildOwnershipLost:
            return
        except Exception as error:  # noqa: BLE001 -- persist sanitized failure; never report success
            if not self.builds.finish(
                cfg.build_id, token, "failed", done, total, "BUILD_FAILED"
            ):
                return
            logger.warning(
                json.dumps(
                    {
                        "event": "search_index_build",
                        "build_id": cfg.build_id,
                        "outcome": "failed",
                        "error_code": "BUILD_FAILED",
                        "error_type": type(error).__name__,
                    }
                )
            )

    async def run(self, stop, interval):
        unavailable = False
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.run_once, stop)
                unavailable = False
            except Exception:  # noqa: BLE001 -- optional build polling must not stop normal search
                if not unavailable:
                    logger.warning("event=search_index_build outcome=store_unavailable")
                unavailable = True
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except TimeoutError:
                pass
