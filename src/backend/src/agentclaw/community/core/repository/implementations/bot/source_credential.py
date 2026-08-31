"""Source credential Repository (prod OceanBase + local SQLite).

One ORM implementation behind ``SourceCredentialRepositoryProtocol``, the
same single-body-two-runtimes shape as every bot-domain repository.
"""
from __future__ import annotations

import json
from typing import Optional

from injector import inject
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func

from agentclaw.community.core.bot_config_manifest.credentials.models import (
    SourceCredentialModel,
    SourceCredentialRow,
)
from agentclaw.community.core.repository.protocols.bot.source_credential import (
    SourceCredentialRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


def _encode_prefixes(prefixes: list[str]) -> str:
    # Deterministic separators: the row is the storage form of the
    # validated list, and a re-PUT of the same list must produce the
    # same bytes (a noisy serialization would break unchanged-detection).
    return json.dumps(prefixes, ensure_ascii=False, separators=(",", ":"))


class SourceCredentialRepository(
    SourceCredentialRepositoryProtocol,
):
    """租户级源凭证仓储实现。"""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db
        self._Credential = SourceCredentialModel

    def get(self, *, name: str) -> Optional[SourceCredentialRow]:
        with self._db.orm_session() as db:
            row = (
                db.query(self._Credential)
                .filter(self._Credential.name == name)
                .one_or_none()
            )
            return row.to_row() if row is not None else None

    def list(self) -> list[SourceCredentialRow]:
        with self._db.orm_session() as db:
            return [
                row.to_row()
                for row in db.query(self._Credential)
                .order_by(self._Credential.name)
                .all()
            ]

    def upsert(
        self,
        *,
        name: str,
        credential_type: str,
        header_name: str,
        allowed_prefixes: list[str],
        secret_ciphertext: str,
        modifier: str,
    ) -> SourceCredentialRow:
        try:
            return self._upsert_once(
                name=name,
                credential_type=credential_type,
                header_name=header_name,
                allowed_prefixes=allowed_prefixes,
                secret_ciphertext=secret_ciphertext,
                modifier=modifier,
            )
        except IntegrityError:
            # Two first-PUTs for one name raced; the loser's retry takes
            # the replace branch — a perfectly valid rotation.
            logger.info(
                "[source_credential.upsert] insert lost a race, retrying as "
                "a replace: name=%s",
                name,
            )
            return self._upsert_once(
                name=name,
                credential_type=credential_type,
                header_name=header_name,
                allowed_prefixes=allowed_prefixes,
                secret_ciphertext=secret_ciphertext,
                modifier=modifier,
            )

    def _upsert_once(
        self,
        *,
        name: str,
        credential_type: str,
        header_name: str,
        allowed_prefixes: list[str],
        secret_ciphertext: str,
        modifier: str,
    ) -> SourceCredentialRow:
        with self._db.orm_session() as db:
            row = (
                db.query(self._Credential)
                .filter(self._Credential.name == name)
                .one_or_none()
            )
            if row is None:
                row = self._Credential(
                    name=name,
                    credential_type=credential_type,
                    header_name=header_name,
                    allowed_prefixes=_encode_prefixes(allowed_prefixes),
                    secret_ciphertext=secret_ciphertext,
                    modifier=modifier,
                )
                db.add(row)
            else:
                row.credential_type = credential_type
                row.header_name = header_name
                row.allowed_prefixes = _encode_prefixes(allowed_prefixes)
                row.secret_ciphertext = secret_ciphertext
                row.modifier = modifier
                # Stamped explicitly (never left to onupdate): a same-value
                # re-PUT is still a write, and the audit contract records
                # every one. ``func.now()``: one clock for both gmt_ columns.
                row.gmt_modified = func.now()
            db.flush()
            db.refresh(row)
            # The secret value never reaches the log — the name is the
            # identifier callers and operators discuss.
            logger.info(
                "[source_credential.upsert] stored name=%s modifier=%s",
                name,
                modifier,
            )
            return row.to_row()

    def delete(self, *, name: str) -> bool:
        with self._db.orm_session() as db:
            deleted = (
                db.query(self._Credential)
                .filter(self._Credential.name == name)
                .delete(synchronize_session=False)
            )
            logger.info(
                "[source_credential.delete] name=%s deleted=%s", name, deleted
            )
            return deleted > 0
