"""ORM repository for ``baas_bot_run_interaction``."""

from __future__ import annotations

import json
from dataclasses import replace

from sqlalchemy.exc import IntegrityError

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session

from ._orm_model import BotRunInteractionModel
from ._record import (
    BotRunInteractionCreateResult,
    BotRunInteractionPayload,
    BotRunInteractionPayloadPatch,
    BotRunInteractionRecord,
    InteractionState,
)


def _dump(payload: BotRunInteractionPayload) -> str:
    return json.dumps(payload.to_dict(), ensure_ascii=False, separators=(",", ":"))


class OrmBotRunInteractionRepository(OrmConnectionMixin):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def create_requested(
        self,
        *,
        session_key: str,
        interaction_id: str,
        payload: BotRunInteractionPayload,
    ) -> BotRunInteractionCreateResult:
        # Engine may redeliver an event. The unique key owns idempotency and an
        # existing row must never be rolled back to requested.
        row = self._get_row(session_key=session_key, interaction_id=interaction_id)
        if row is not None:
            return BotRunInteractionCreateResult(record=row.to_record(), created=False)

        row = BotRunInteractionModel(
            session_key=session_key,
            interaction_id=interaction_id,
            state="requested",
            payload=_dump(payload),
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            row = self._get_row(session_key=session_key, interaction_id=interaction_id)
            if row is None:
                raise
            return BotRunInteractionCreateResult(record=row.to_record(), created=False)
        return BotRunInteractionCreateResult(record=row.to_record(), created=True)

    @with_orm_session
    def get(
        self, *, session_key: str, interaction_id: str
    ) -> BotRunInteractionRecord | None:
        row = self._get_row(session_key=session_key, interaction_id=interaction_id)
        return row.to_record() if row is not None else None

    @with_orm_session
    def transition(
        self,
        *,
        session_key: str,
        interaction_id: str,
        from_states: frozenset[InteractionState],
        to_state: InteractionState,
        patch: BotRunInteractionPayloadPatch,
    ) -> BotRunInteractionRecord | None:
        return self._atomic_update(
            session_key=session_key,
            interaction_id=interaction_id,
            allowed_states=from_states,
            to_state=to_state,
            patch=patch,
        )

    @with_orm_session
    def merge_payload(
        self,
        *,
        session_key: str,
        interaction_id: str,
        allowed_states: frozenset[InteractionState],
        patch: BotRunInteractionPayloadPatch,
    ) -> BotRunInteractionRecord | None:
        return self._atomic_update(
            session_key=session_key,
            interaction_id=interaction_id,
            allowed_states=allowed_states,
            to_state=None,
            patch=patch,
        )

    def _atomic_update(
        self,
        *,
        session_key: str,
        interaction_id: str,
        allowed_states: frozenset[InteractionState],
        to_state: InteractionState | None,
        patch: BotRunInteractionPayloadPatch,
    ) -> BotRunInteractionRecord | None:
        """Lock, validate state, and merge the JSON payload in one transaction.

        The row lock serializes payload read/merge/write on production MySQL.
        The UPDATE still carries the allowed-state predicate, so state-machine
        races are rejected without a separate version column. SQLAlchemy omits
        ``FOR UPDATE`` on SQLite, while the conditional UPDATE preserves the
        unit-test/runtime fallback behavior.
        """
        row = self._get_row(
            session_key=session_key,
            interaction_id=interaction_id,
            for_update=True,
        )
        if row is None or row.state not in allowed_states:
            return None

        current = row.to_record()
        next_payload = current.payload.merge(patch)
        values = {"payload": _dump(next_payload)}
        if to_state is not None:
            values["state"] = to_state

        count = (
            self._session.query(BotRunInteractionModel)
            .filter(
                BotRunInteractionModel.session_key == session_key,
                BotRunInteractionModel.interaction_id == interaction_id,
                BotRunInteractionModel.state.in_(allowed_states),
            )
            .update(values, synchronize_session=False)
        )
        if count != 1:
            return None
        return replace(
            current,
            state=to_state or current.state,
            payload=next_payload,
        )

    def _get_row(
        self,
        *,
        session_key: str,
        interaction_id: str,
        for_update: bool = False,
    ) -> BotRunInteractionModel | None:
        query = self._session.query(BotRunInteractionModel).filter(
            BotRunInteractionModel.session_key == session_key,
            BotRunInteractionModel.interaction_id == interaction_id,
        )
        if for_update:
            query = query.with_for_update()
        return query.one_or_none()
