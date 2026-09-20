"""Bounded CAS retries prevent lost updates across backend workers."""

import hashlib
from typing import Literal
from injector import inject
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from agentclaw.community.core.service_bot.repository.build_ignore import (
    BuildIgnoreModel,
)
from agentclaw.community.core.repository.protocols.build_ignore import (
    BuildIgnoreRepositoryProtocol,
)
from agentclaw.community.kernel.build_ignore import (
    BuildIgnoreConfig,
    BuildIgnoreError,
    MAX_RULE_BYTES,
    normalize_build_ignore_path,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.log import get_logger

logger = get_logger()


def _key(env: str, entity_id: str, bot_id: str, engine_type: str) -> str:
    raw = "".join(f"{len(p)}:{p}" for p in (env, entity_id, bot_id, engine_type))
    return hashlib.sha256(raw.encode()).hexdigest()


class BuildIgnoreRepository(BuildIgnoreRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin):
        self._db = db

    def get(
        self, *, env: str, entity_id: str, bot_id: str, engine_type: str
    ) -> BuildIgnoreConfig | None:
        with self._db.orm_session() as db:
            row = (
                db.query(BuildIgnoreModel)
                .filter_by(config_key=_key(env, entity_id, bot_id, engine_type))
                .one_or_none()
            )
            return BuildIgnoreConfig(tuple(row.paths), row.revision) if row else None

    def change(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        engine_type: str,
        operation: Literal["add", "remove"],
        path: str,
        modifier: str,
    ) -> tuple[BuildIgnoreConfig, bool]:
        path = normalize_build_ignore_path(path)
        if operation not in {"add", "remove"}:
            raise BuildIgnoreError("invalid_operation")
        key = _key(env, entity_id, bot_id, engine_type)
        for attempt in range(16):
            try:
                with self._db.orm_session() as db:
                    query = db.query(BuildIgnoreModel).filter_by(config_key=key)
                    row = query.one_or_none()
                    old = (
                        BuildIgnoreConfig(tuple(row.paths), row.revision)
                        if row
                        else BuildIgnoreConfig()
                    )
                    paths = list(old.paths)
                    if operation == "add" and path not in paths:
                        paths.append(path)
                    elif operation == "remove" and path in paths:
                        paths.remove(path)
                    else:
                        return old, False
                    if sum(len(p.encode("utf-8")) + 1 for p in paths) > MAX_RULE_BYTES:
                        raise BuildIgnoreError("ignore_rules_too_large")
                    new = BuildIgnoreConfig(tuple(paths), old.revision + 1)
                    if row is None:
                        db.add(
                            BuildIgnoreModel(
                                config_key=key,
                                env=env,
                                entity_id=entity_id,
                                bot_id=bot_id,
                                engine_type=engine_type,
                                paths=paths,
                                revision=new.revision,
                                modifier=modifier,
                            )
                        )
                        db.flush()
                    elif (
                        query.filter_by(revision=old.revision).update(
                            {
                                "paths": paths,
                                "revision": new.revision,
                                "modifier": modifier,
                                "gmt_modified": func.now(),
                            },
                            synchronize_session=False,
                        )
                        != 1
                    ):
                        continue
                return new, True
            except IntegrityError:
                logger.info(
                    "build_ignore.cas_retry bot_id=%s engine_type=%s attempt=%s",
                    bot_id,
                    engine_type,
                    attempt + 1,
                )
        raise BuildIgnoreError("concurrent_update")
