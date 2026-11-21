"""Durable BCN final-event callback state machine."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from secbaas.community.api.bcn import ChatEvent, EventMessage, EventSpeaker, EventUsage
from secbaas.community.core.repository.bot_run import BotRunRepository
from secbaas.community.logger import get_logger

from ._protocol import UplinkClient

if TYPE_CHECKING:
    from secbaas.community.core.repository.bot_run import BotRunRecord

logger = get_logger("core-bot-run")


class BcnCallbackOutcome(StrEnum):
    SENT = "SENT"
    RETRYABLE_PENDING = "RETRYABLE_PENDING"
    TERMINAL_FAILED = "TERMINAL_FAILED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    METADATA_WRITE_FAILED = "METADATA_WRITE_FAILED"


def _build_chat_event(record: BotRunRecord) -> ChatEvent:
    if record.status in ("FAILED", "TIME_OUT"):
        state = "error"
    elif record.status == "ABORTED":
        state = "aborted"
    else:
        state = "final"

    message: EventMessage | None = None
    content = record.result_content_long if state == "final" else record.error
    if content:
        speaker: EventSpeaker | None = None
        if record.metadata and isinstance(record.metadata, dict):
            from_ref = record.metadata.get("from_ref")
            if isinstance(from_ref, dict):
                speaker = EventSpeaker(
                    kind=from_ref.get("kind", "bot"),
                    id=from_ref.get("id"),
                    name=from_ref.get("name"),
                )
        message = EventMessage(text=content, speaker=speaker)

    usage: EventUsage | None = None
    if record.result_extra and isinstance(record.result_extra, dict):
        usage_data = record.result_extra.get("usage")
        if isinstance(usage_data, dict):
            usage = EventUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                model=usage_data.get("model"),
                latency_ms=usage_data.get("latency_ms"),
            )

    return ChatEvent(
        run_id=record.run_id,
        seq=0,
        state=state,
        message=message,
        usage=usage,
    )


class BcnUplinkCallback:
    """Claim, deliver and durably finish one BCN final callback.

    The callback never changes ``baas_bot_run.status`` or queue state.  Its only
    writes are fenced delivery metadata transitions on the result row.
    """

    def __init__(
        self,
        uplink_client: UplinkClient,
        run_repository: BotRunRepository,
        *,
        max_attempts: int | None = 5,
        lease_seconds: int | None = 45,
        retry_backoff_seconds: int = 60,
    ) -> None:
        # Keep minimal/legacy overlays compatible when DI resolves newly added
        # callback keys to None.
        max_attempts = 5 if max_attempts is None else int(max_attempts)
        lease_seconds = 45 if lease_seconds is None else int(lease_seconds)
        if max_attempts <= 0 or lease_seconds <= 0 or retry_backoff_seconds < 0:
            raise ValueError("callback retry/lease configuration is invalid")
        self._uplink_client = uplink_client
        self._run = run_repository
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds
        self._retry_backoff_seconds = retry_backoff_seconds

    async def __call__(self, run_id: str) -> BcnCallbackOutcome:
        run = self._run.get_by_run_id(run_id)
        if run is None or not run.bcn_callback_eligible:
            return BcnCallbackOutcome.NOT_ELIGIBLE
        if run.bcn_callback_function != "bcn_uplink":
            return BcnCallbackOutcome.NOT_ELIGIBLE
        if run.status not in {"COMPLETED", "FAILED", "TIME_OUT", "ABORTED"}:
            return BcnCallbackOutcome.NOT_ELIGIBLE

        try:
            claim = self._run.claim_bcn_callback_attempt(
                run_id,
                now=datetime.now(),
                lease_seconds=self._lease_seconds,
                max_attempts=self._max_attempts,
            )
        except Exception:
            logger.warning(
                "[BcnUplinkCallback] metadata claim failed run_id=%s", run_id
            )
            return BcnCallbackOutcome.METADATA_WRITE_FAILED
        if claim is None:
            return BcnCallbackOutcome.NOT_ELIGIBLE

        run = claim.record
        event = _build_chat_event(run)
        try:
            result = await self._uplink_client.send_event(
                event, bot_id=run.bot_id, event_id=run_id
            )
        except TimeoutError:
            return await self._persist_retryable(
                claim, status="timeout", error_kind="timeout"
            )
        except Exception:
            # Do not log exception text: it may contain a URL, response body or
            # provider credential.  The durable state stores only a category.
            logger.warning(
                "[BcnUplinkCallback] uplink transport failure run_id=%s",
                run_id,
            )
            return await self._persist_retryable(
                claim, status="transport", error_kind="transport"
            )

        if getattr(result, "ok", False):
            return await self._persist_success(
                claim,
                status=(
                    "deduplicated"
                    if getattr(result, "deduplicated", False)
                    else str(getattr(result, "status_code", None) or "2xx")
                ),
            )
        if getattr(result, "retryable", False):
            return await self._persist_retryable(
                claim,
                status=str(getattr(result, "status_code", None) or "retryable"),
                error_kind=str(getattr(result, "category", "retryable_http")),
            )
        return await self._persist_terminal(
            claim,
            status=str(getattr(result, "status_code", None) or "terminal"),
            error_kind=str(getattr(result, "category", "terminal_http")),
        )

    async def _persist_success(self, claim, *, status: str) -> BcnCallbackOutcome:
        try:
            updated = self._run.finish_bcn_callback_success(
                claim.record.run_id,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                status=status,
            )
        except Exception:
            logger.warning(
                "[BcnUplinkCallback] success metadata write failed run_id=%s",
                claim.record.run_id,
            )
            return BcnCallbackOutcome.METADATA_WRITE_FAILED
        return (
            BcnCallbackOutcome.SENT
            if updated
            else BcnCallbackOutcome.METADATA_WRITE_FAILED
        )

    async def _persist_retryable(
        self,
        claim,
        *,
        status: str,
        error_kind: str,
    ) -> BcnCallbackOutcome:
        retry_delay = min(
            self._retry_backoff_seconds
            * (2 ** max(0, claim.record.bcn_callback_attempts - 1)),
            3600,
        )
        next_retry_at = datetime.now() + timedelta(seconds=retry_delay)
        try:
            updated = self._run.finish_bcn_callback_retryable_failure(
                claim.record.run_id,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                next_retry_at=next_retry_at,
                status=status,
                error_kind=error_kind,
                max_attempts=self._max_attempts,
            )
        except Exception:
            logger.warning(
                "[BcnUplinkCallback] retry metadata write failed run_id=%s",
                claim.record.run_id,
            )
            return BcnCallbackOutcome.METADATA_WRITE_FAILED
        if not updated:
            return BcnCallbackOutcome.METADATA_WRITE_FAILED
        if claim.record.bcn_callback_attempts >= self._max_attempts:
            return BcnCallbackOutcome.TERMINAL_FAILED
        return BcnCallbackOutcome.RETRYABLE_PENDING

    async def _persist_terminal(
        self,
        claim,
        *,
        status: str,
        error_kind: str,
    ) -> BcnCallbackOutcome:
        try:
            updated = self._run.finish_bcn_callback_terminal_failure(
                claim.record.run_id,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                status=status,
                error_kind=error_kind,
            )
        except Exception:
            logger.warning(
                "[BcnUplinkCallback] terminal metadata write failed run_id=%s",
                claim.record.run_id,
            )
            return BcnCallbackOutcome.METADATA_WRITE_FAILED
        return (
            BcnCallbackOutcome.TERMINAL_FAILED
            if updated
            else BcnCallbackOutcome.METADATA_WRITE_FAILED
        )
