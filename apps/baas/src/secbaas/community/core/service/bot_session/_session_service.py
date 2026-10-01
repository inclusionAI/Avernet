"""DefaultSessionService — concrete implementation of SessionService Protocol.

Provides session lifecycle management:
- create_session: Create PENDING session on request arrival
- mark_running: Transition to RUNNING when execution starts
- mark_completed: Store result and mark COMPLETED
- mark_failed: Store error and mark FAILED
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from secbaas.community.core.repository.bot_session import (
    BotSessionRecord,
    BotSessionRepository,
)
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

from ._enums import SessionStatus
from ._models import PaginatedResult
from ._protocols import SessionService

logger = get_logger("core-service")


class DefaultSessionService(SessionService):
    """Service for managing bot execution sessions."""

    def __init__(self, repository: BotSessionRepository) -> None:
        self._bot_session_repository = repository

    def create_session(
        self,
        *,
        bot_uuid: str,
        invoker: str,
        req: dict[str, Any],
        device_uuid: str,
        tenant: str,
        trace_id: str | None = None,
    ) -> str:
        """Create a new session record with PENDING status.

        Called when request arrives (before execution starts).
          - Sync calls: immediately followed by mark_running
          - Async calls: remains PENDING until background task starts

        Args:
            bot_uuid: Bot UUID
            invoker: Invoker identifier
            req: Request data (business data only: command, parameters)
            device_uuid: Selected device UUID
            tenant: Tenant name for multi-tenant isolation
            trace_id: Optional trace ID for distributed tracing

        Returns:
            Generated session_id (format: SESSION-{uuid})
        """
        session_id = f"SESSION-{uuid4().hex}"

        context = {}
        if trace_id:
            context["trace_id"] = trace_id

        logger.info(
            f"[create_session] bot_uuid={bot_uuid}, device_uuid={device_uuid}, "
            f"session_id={session_id}, tenant={tenant}, trace_id={trace_id}"
        )

        repo = self._bot_session_repository
        record_id = repo.insert_session(
            bot_uuid=bot_uuid,
            invoker=invoker,
            session_id=session_id,
            req=req,
            device_uuid=device_uuid,
            context=context if context else None,
            status=SessionStatus.PENDING.value,
            tenant=tenant,
        )

        logger.info(f"[create_session] Created session with record_id={record_id}")
        return session_id

    def mark_running(
        self,
        session_id: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Mark session as RUNNING.

        Called when command execution actually starts on the device:
          - Sync: immediately after create_session
          - Async: when background task begins execution

        Args:
            session_id: Session ID
            context: Optional context updates to apply during transition
        """
        logger.info(f"[mark_running] session_id={session_id}")

        repo = self._bot_session_repository
        repo.update_status(
            session_id=session_id,
            status=SessionStatus.RUNNING.value,
        )

        # Update context if provided
        if context:
            repo.update_context(
                session_id=session_id,
                context=context,
            )

        logger.info(f"[mark_running] Session {session_id} marked as RUNNING")

    def mark_completed(
        self,
        session_id: str,
        result: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        err_msg: str | None = None,
    ) -> None:
        """Mark session as COMPLETED with flexible field updates.

        Called when command execution finishes.

        Args:
            session_id: Session ID
            result: Optional business result data (device output)
            context: Optional incremental context updates
            err_msg: Optional error message (for partial failure scenarios)
        """
        logger.info(f"[mark_completed] session_id={session_id}")

        repo = self._bot_session_repository

        # Use update_context for atomic update of all fields
        repo.update_context(
            session_id=session_id,
            result=result,
            context=context,
            err_msg=err_msg,
        )

        # Update status
        repo.update_status(
            session_id=session_id,
            status=SessionStatus.COMPLETED.value,
        )

        logger.info(f"[mark_completed] Session {session_id} marked as COMPLETED")

    def mark_failed(
        self,
        session_id: str,
        err_msg: str | None = None,
        result: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Mark session as FAILED with flexible field updates.

        Called when command execution fails or times out.

        Args:
            session_id: Session ID
            err_msg: Optional error description
            result: Optional partial result data (for partial success scenarios)
            context: Optional incremental context updates
        """
        log_msg = f"[mark_failed] session_id={session_id}"
        if err_msg:
            log_msg += f", err_msg={err_msg[:100]}..."
        logger.info(log_msg)

        repo = self._bot_session_repository

        # Use update_context for atomic update of all fields
        repo.update_context(
            session_id=session_id,
            result=result,
            context=context,
            err_msg=err_msg,
        )

        # Update status
        repo.update_status(
            session_id=session_id,
            status=SessionStatus.FAILED.value,
        )

        logger.info(f"[mark_failed] Session {session_id} marked as FAILED")

    def get_by_session_id(self, session_id: str) -> BotSessionRecord | None:
        """Get session by session_id.

        Primary lookup for result retrieval (async invocation pattern).

        Args:
            session_id: Session ID (format: SESSION-{uuid})

        Returns:
            Session record or None if not found
        """
        logger.info(f"[get_by_session_id] session_id={session_id}")

        repo = self._bot_session_repository
        record = repo.get_by_session_id(session_id)

        if record:
            logger.info(
                f"[get_by_session_id] Found: status={record.status}, "
                f"bot_uuid={record.bot_uuid}"
            )
        else:
            logger.info(f"[get_by_session_id] Not found: {session_id}")

        return record

    def list_by_bot(
        self,
        bot_uuid: str,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResult:
        """List execution history for a bot with pagination.

        Default: time descending (newest first), 50 records per page.

        Args:
            bot_uuid: Bot UUID
            page: Page number (1-based, default: 1)
            page_size: Records per page (default: 50)

        Returns:
            PaginatedResult with total count and page items
        """
        env = get_current_env()
        logger.info(
            f"[list_by_bot] bot_uuid={bot_uuid}, env={env}, page={page}, page_size={page_size}"
        )

        repo = self._bot_session_repository
        total, items = repo.list_by_bot_uuid(
            bot_uuid=bot_uuid,
            page=page,
            page_size=page_size,
        )

        logger.info(f"[list_by_bot] total={total}, returned {len(items)} items")

        return PaginatedResult(
            total=total,
            page=page,
            page_size=page_size,
            items=items,
        )

    def list_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        bot_uuid: str | None = None,
    ) -> list[BotSessionRecord]:
        """List sessions within a time range (for audit trails).

        Args:
            start_time: Start of time range (inclusive)
            end_time: End of time range (inclusive)
            bot_uuid: Optional bot UUID filter

        Returns:
            List of session records ordered by time descending
        """
        logger.info(
            f"[list_by_time_range] start={start_time}, end={end_time}, "
            f"bot_uuid={bot_uuid}"
        )

        repo = self._bot_session_repository
        items = repo.list_by_time_range(
            start_time=start_time,
            end_time=end_time,
            bot_uuid=bot_uuid,
        )

        logger.info(f"[list_by_time_range] returned {len(items)} items")
        return items

    def list_by_bot_device_invoker(
        self,
        bot_uuid: str,
        invoker: str,
        start_time: datetime,
        end_time: datetime,
        device_uuid: str | None = None,
    ) -> list[BotSessionRecord]:
        """List sessions by bot, device, invoker with time range.

        Uses idx_bot_dev_ivk_time composite index for efficient lookup.
        This is the most common query pattern for session history.

        Args:
            bot_uuid: Bot UUID
            invoker: Invoker identifier
            start_time: Start of time range (inclusive)
            end_time: End of time range (inclusive)
            device_uuid: Device UUID (optional, None means all devices)

        Returns:
            List of session records ordered by time descending
        """
        logger.info(
            f"[list_by_bot_device_invoker] bot_uuid={bot_uuid}, "
            f"invoker={invoker}, device_uuid={device_uuid}, "
            f"start={start_time}, end={end_time}"
        )

        repo = self._bot_session_repository
        items = repo.list_by_bot_device_invoker(
            bot_uuid=bot_uuid,
            device_uuid=device_uuid,
            invoker=invoker,
            start_time=start_time,
            end_time=end_time,
        )

        logger.info(f"[list_by_bot_device_invoker] returned {len(items)} items")
        return items

    def update_context(
        self,
        session_id: str,
        context: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        err_msg: str | None = None,
    ) -> None:
        """Update session fields without status transition.

        Supports incremental updates during long-running operations.
        All parameters are optional — caller specifies what to update.

        Args:
            session_id: Session ID
            context: Optional context updates (merged with existing)
            result: Optional result data update
            err_msg: Optional error message update
        """
        logger.info(
            f"[update_context] session_id={session_id}, "
            f"has_context={context is not None}, "
            f"has_result={result is not None}, "
            f"has_err_msg={err_msg is not None}"
        )

        if not any([context, result, err_msg]):
            logger.info(f"[update_context] Nothing to update for session {session_id}")
            return

        repo = self._bot_session_repository
        repo.update_context(
            session_id=session_id,
            context=context,
            result=result,
            err_msg=err_msg,
        )

        logger.info(f"[update_context] Session {session_id} updated")
