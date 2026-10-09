"""ORM model for baas_bot_run_interaction."""

from __future__ import annotations

import json
from typing import cast

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Column,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)

from secbaas.community.spi.database import Base

from ._record import (
    BotRunInteractionPayload,
    BotRunInteractionRecord,
    InteractionState,
)


class BotRunInteractionModel(Base):
    """Minimal interaction state table.

    ``payload`` stores the complete protocol snapshot as JSON text for maximum
    DB compatibility; the migration uses JSON where available.
    """

    __tablename__ = "baas_bot_run_interaction"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    baas_interaction_id = Column(String(160), nullable=False)
    session_key = Column(String(512), nullable=False)
    interaction_id = Column(String(160), nullable=False)
    state = Column(String(32), nullable=False)
    payload = Column(Text, nullable=False)
    gmt_create = Column(TIMESTAMP, nullable=False, server_default=func.now())
    gmt_modified = Column(
        TIMESTAMP, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("baas_interaction_id", name="uk_baas_interaction_id"),
        UniqueConstraint(
            "session_key", "interaction_id", name="uk_session_interaction"
        ),
        Index("idx_session_state", "session_key", "state"),
    )

    def to_record(self) -> BotRunInteractionRecord:
        try:
            decoded = json.loads(self.payload)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("invalid interaction payload JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("interaction payload must be a JSON object")
        if self.state not in {
            "requested",
            "queued",
            "dispatching",
            "resolved",
            "expired",
            "failed",
        }:
            raise ValueError(f"invalid interaction state: {self.state}")
        return BotRunInteractionRecord(
            id=self.id,
            baas_interaction_id=self.baas_interaction_id,
            session_key=self.session_key,
            interaction_id=self.interaction_id,
            state=cast(InteractionState, self.state),
            payload=BotRunInteractionPayload.from_dict(decoded),
            created_at=self.gmt_create,
            updated_at=self.gmt_modified,
        )
