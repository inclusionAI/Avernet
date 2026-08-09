import os
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from injector import inject

from agentclaw.community.core.bot_chat.errors import LangfuseAPIError, SessionNotFoundError
from agentclaw.community.core.repository.implementations.chat.db import BotChatDbRepository
from agentclaw.community.core.bot_chat.query_support import QueryScope
from agentclaw.community.core.bot_chat.schemas import (
    ConversationDetail,
    ConversationObservation,
    ConversationSession,
    HealthCheckData,
    SessionListResponse,
    SessionMetadata,
)
from agentclaw.community.di.config import BotChatConfig
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.log import get_logger

logger = get_logger()

# Langfuse credentials/endpoint are deployment config (``BotChatConfig``, from
# the ``bot_chat`` yaml block), injected into BotChatService. When any is empty
# (community build), the trace-query methods degrade to empty / unconfigured.

# 对外列表接口默认返回条数；调用方不传 limit 时使用。
_DEFAULT_PAGE_SIZE = 20
# 对外列表接口允许的最大 limit；service 层兜底限制，避免单次返回过大。
_MAX_PAGE_SIZE = 100
# 对外列表接口默认回看时间窗口；调用方不传 from_date/to_date 时查最近 72 小时。
_DEFAULT_TIME_RANGE_HOURS = 72
# Fuzzy searches are bounded even when callers provide an explicit range.
_MAX_FUZZY_TIME_RANGE_DAYS = 90
# metadata 精确查询走多页扫描时的内部批大小；与对外返回 limit 解耦。
_SCAN_PAGE_SIZE = 100
# metadata 精确查询走多页扫描时默认最多扫描页数；默认最多扫描 100 * 10 条 trace。
_DEFAULT_MAX_SCAN_PAGES = 10


def _get_max_scan_pages() -> int:
    """Get max scan pages from env."""
    env_val = os.environ.get("BOT_CHAT_EXACT_QUERY_MAX_PAGES")
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    return _DEFAULT_MAX_SCAN_PAGES


def _build_auth_header(public_key: str, secret_key: str) -> str:
    """Build Basic auth header from Langfuse credentials."""
    import base64

    credentials = f"{public_key}:{secret_key}"
    return f"Basic {base64.b64encode(credentials.encode()).decode()}"


def _as_utc(value: datetime) -> datetime:
    """Treat timezone-free API values as UTC and normalize aware values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _extract_user_input(trace_input: Any) -> str | None:
    """Extract the last user message from trace input."""
    if trace_input is None:
        return None
    if isinstance(trace_input, str):
        return trace_input
    if isinstance(trace_input, list):
        for msg in reversed(trace_input):
            if (
                isinstance(msg, dict)
                and msg.get("role") == "user"
            ):
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    for part in content:
                        if (
                            isinstance(part, dict)
                            and part.get("type") == "text"
                        ):
                            return part.get("text", "")
                    first_text = next(
                        (
                            p
                            for p in content
                            if isinstance(p, str)
                        ),
                        None,
                    )
                    if first_text:
                        return first_text
        first = trace_input[0] if trace_input else None
        if isinstance(first, dict):
            return first.get("content", str(first))
        return str(first) if first else None
    if isinstance(trace_input, dict):
        return trace_input.get("content", str(trace_input))
    return str(trace_input)


def _trace_owner_id(trace: dict[str, Any], attributes: dict[str, Any]) -> Any:
    return trace.get("userId") or attributes.get("identity.owner_id") or attributes.get("user.id")


def _map_trace_to_session(trace: dict[str, Any]) -> ConversationSession:
    """Map a Langfuse trace dict to a ConversationSession."""
    metadata_raw = trace.get("metadata") or {}
    attributes = (metadata_raw.get("attributes") or {}) if isinstance(metadata_raw, dict) else {}
    usage = trace.get("usage") or {}

    return ConversationSession(
        id=trace.get("id", ""),
        biz_task_id=(
            trace.get("biz_task_id")
            or attributes.get("agentic.biz_task_id")
            or attributes.get("identity.biz_task_id")
        ),
        biz_scene=(
            trace.get("biz_scene")
            or attributes.get("agentic.biz_scene")
            or attributes.get("identity.biz_scene")
        ),
        session_id=attributes.get("gen_ai.session.id"),
        session_key=(
            attributes.get("session_id")
            or attributes.get("gen_ai.conversation.id")
        ),
        bot_id=attributes.get("identity.bot_id"),
        name=trace.get("name") or "未命名会话",
        input=_extract_user_input(trace.get("input")),
        output_preview=(
            str(trace.get("output"))[:500]
            if trace.get("output") is not None
            else None
        ),
        search_output=(
            str(trace.get("output"))
            if trace.get("output") is not None
            else None
        ),
        status="FAILED" if trace.get("success") is False else "SUCCESS",
        timestamp=trace.get("timestamp", ""),
        user_id=_trace_owner_id(trace, attributes),
        metadata=SessionMetadata(attributes=attributes),
        total_cost=float(trace.get("totalCost") or 0),
        latency_ms=float(trace.get("latency") or 0),
        total_tokens=int(usage.get("totalTokens") or 0),
    )


def _map_observation(obs: dict[str, Any]) -> ConversationObservation:
    """Map a Langfuse observation dict to a ConversationObservation."""
    metadata_raw = obs.get("metadata") or {}
    attributes = (metadata_raw.get("attributes") or {}) if isinstance(metadata_raw, dict) else {}
    usage = obs.get("usage") or {}

    return ConversationObservation(
        id=obs.get("id", ""),
        biz_task_id=(
            obs.get("biz_task_id")
            or attributes.get("agentic.biz_task_id")
            or attributes.get("identity.biz_task_id")
        ),
        biz_scene=(
            obs.get("biz_scene")
            or attributes.get("agentic.biz_scene")
            or attributes.get("identity.biz_scene")
        ),
        name=obs.get("name", ""),
        type=obs.get("type", "SPAN"),
        latency_ms=float(obs.get("latency") or 0),
        total_cost=float(obs.get("totalCost") or 0),
        total_tokens=int(usage.get("totalTokens") or 0),
        input=obs.get("input"),
        output=obs.get("output"),
        metadata=metadata_raw if isinstance(metadata_raw, dict) else None,
        model_name=attributes.get("gen_ai.request.model"),
        parent_observation_id=obs.get("parentObservationId"),
        children=[],
    )


def _build_observation_tree(observations: list[dict[str, Any]]) -> list[ConversationObservation]:
    """Build a nested observation tree from a flat list using parentObservationId."""
    mapped = [
        _map_observation(obs)
        for obs in observations
    ]
    by_id: dict[str, ConversationObservation] = {obs.id: obs for obs in mapped}
    roots: list[ConversationObservation] = []

    for obs in mapped:
        parent_id = obs.parent_observation_id
        if (
            parent_id
            and parent_id in by_id
        ):
            by_id[parent_id].children.append(obs)
        else:
            roots.append(obs)

    return roots


def _matches_session_key(attributes: dict[str, Any], session_key: str) -> bool:
    """Check if attributes match session_key in either legacy or GenAI field."""
    return (
        attributes.get("session_id") == session_key
        or attributes.get("gen_ai.conversation.id") == session_key
    )


def _apply_client_side_filters(
    sessions: list[ConversationSession],
    bot_id: str | None,
    trace_id: str | None,
    session_id: str | None,
    session_key: str | None,
    query: str | None = None,
    biz_scene: str | None = None,
    biz_task_id: str | None = None,
    match_mode: str = "exact",
    include_output_match: bool = False,
) -> list[ConversationSession]:
    """Apply client-side filters that Langfuse API doesn't support natively."""
    result = sessions
    def matches(actual: str | None, expected: str) -> bool:
        if actual is None:
            return False
        return expected in actual if match_mode == "contains" else actual == expected

    if trace_id:
        result = [
            s
            for s in result
            if matches(s.id, trace_id)
        ]
    if bot_id:
        result = [
            s
            for s in result
            if (
                s.metadata
                and s.metadata.attributes.get("identity.bot_id") == bot_id
            )
        ]
    if session_key:
        result = [
            s
            for s in result
            if (
                s.metadata
                and (
                    (
                        session_key
                        in str(s.metadata.attributes.get("session_id") or "")
                        or session_key
                        in str(s.metadata.attributes.get("gen_ai.conversation.id") or "")
                    )
                    if match_mode == "contains"
                    else _matches_session_key(s.metadata.attributes, session_key)
                )
            )
        ]
    if session_id:
        result = [
            s
            for s in result
            if (
                s.metadata
                and matches(
                    s.metadata.attributes.get("gen_ai.session.id"), session_id
                )
            )
        ]
    if biz_scene:
        result = [s for s in result if matches(s.biz_scene, biz_scene)]
    if biz_task_id:
        result = [s for s in result if matches(s.biz_task_id, biz_task_id)]
    if query:
        q = query.lower()
        result = [
            s
            for s in result
            if (
                (
                    s.name is not None
                    and q in s.name.lower()
                )
                or (
                    s.input is not None
                    and q in s.input.lower()
                )
                or (
                    include_output_match
                    and s.search_output is not None
                    and q in s.search_output.lower()
                )
            )
        ]
    return result


# ---------------------------------------------------------------------------
# BotChatService
# ---------------------------------------------------------------------------

class BotChatService:
    """Service for querying bot conversation sessions."""

    @inject
    def __init__(self, db: DatabasePlugin, config: BotChatConfig) -> None:
        """Initialize service with DatabasePlugin + BotChatConfig (Langfuse)."""
        self._db = db
        self._langfuse_base_url = config.langfuse_base_url
        self._langfuse_public_key = config.langfuse_public_key
        self._langfuse_secret_key = config.langfuse_secret_key
        self._db_repo = BotChatDbRepository(db)

    def _get_log_source(self, log_source: str | None) -> str:
        """Determine effective log source.

        Rules:
        - Only explicit log_source="langfuse" uses Langfuse
        - All other cases (None, "db", "dual", etc.) default to "db"
        - Langfuse requires credentials. When they are empty (the deploy did not
          provision LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY), fall back to "db"
          rather than calling Langfuse with an empty Basic-auth header (which
          would 401). This keeps the feature dormant-by-default, matching KB/BCN.
        """
        if log_source == "langfuse":
            if self._langfuse_public_key and self._langfuse_secret_key:
                return "langfuse"
            logger.warning(
                "[bot_chat] log_source=langfuse requested but Langfuse credentials "
                "are not configured; falling back to db"
            )
        return "db"

    def _check_bot_access(self, user_id: str, bot_id: str) -> bool:
        """Check access: owner (ac_bots) or collaborator (ac_bot_collaborator)."""
        return self._db_repo.has_bot_access(user_id, bot_id)

    @staticmethod
    def _is_legacy_default_bot_id(bot_id: str | None, owner_id: str) -> bool:
        """True iff ``bot_id`` is a legacy per-owner default alias (pre-retirement).

        These ids (`"default"` and ``f"{owner_id}_default"``) were unique only per
        owner, so :func:`has_bot_access` cannot disambiguate them across owners
        via ``get_by_id`` — multiple owners each have a `"default"` bot. Routes
        over historical traces keep the legacy short-circuit (owner match by
        user_id / scoped list) instead of routing through ``has_bot_access``.
        New bots are globally unique and never hit this branch.

        Kept as a service-layer helper so the alias set + rationale live in one
        place rather than scattered across list/get-session paths.
        """
        if not bot_id:
            return False
        return bot_id == "default" or bot_id == f"{owner_id}_default"

    async def list_sessions(
        self,
        owner_id: str,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        page: int = 1,
        limit: int = _DEFAULT_PAGE_SIZE,
        bot_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        session_key: str | None = None,
        query: str | None = None,
        biz_scene: str | None = None,
        biz_task_id: str | None = None,
        group_id: str | None = None,
        match_mode: str = "exact",
        include_output_match: bool = False,
        time_scope: str = "default",
        log_source: str | None = None,
    ) -> SessionListResponse:
        """List conversation sessions for a given owner."""
        limit = min(max(1, limit), _MAX_PAGE_SIZE)
        page = max(1, page)

        if match_mode not in {"exact", "contains"}:
            raise ValueError("match_mode must be 'exact' or 'contains'")
        if time_scope not in {"default", "all"}:
            raise ValueError("time_scope must be 'default' or 'all'")

        exact_identifiers = (
            trace_id,
            session_id,
            session_key,
            biz_task_id,
            group_id,
        )
        if time_scope == "all" and (
            match_mode != "exact" or not any(exact_identifiers)
        ):
            raise ValueError(
                "time_scope=all requires match_mode=exact and an exact identifier"
            )

        now = datetime.now(timezone.utc)
        if from_date is not None:
            from_date = _as_utc(from_date)
        if to_date is not None:
            to_date = _as_utc(to_date)
        if time_scope == "all":
            from_date = from_date or datetime(1970, 1, 1, tzinfo=timezone.utc)
            to_date = to_date or now
        else:
            if from_date is None:
                from_date = now - timedelta(hours=_DEFAULT_TIME_RANGE_HOURS)
            if to_date is None:
                to_date = now
        if from_date > to_date:
            raise ValueError("from_date must not be later than to_date")
        if (
            match_mode == "contains"
            and to_date - from_date > timedelta(days=_MAX_FUZZY_TIME_RANGE_DAYS)
        ):
            raise ValueError("contains queries support a maximum time range of 90 days")

        effective_source = self._get_log_source(log_source)
        # Task relations and group-session mappings are stored in the DB.
        if group_id or biz_scene or biz_task_id:
            effective_source = "db"

        if effective_source == "langfuse":
            return await self._list_sessions_langfuse(
                owner_id=owner_id,
                from_date=from_date,
                to_date=to_date,
                page=page,
                limit=limit,
                bot_id=bot_id,
                trace_id=trace_id,
                session_id=session_id,
                session_key=session_key,
                query=query,
                match_mode=match_mode,
                include_output_match=include_output_match,
            )

        # Default DB mode: for non-(legacy-default) bot, check access (owner or collaborator).
        # Legacy default aliases short-circuit (see _is_legacy_default_bot_id): the
        # subsequent DB list is already scoped by owner_id, so access is implicit.
        if bot_id and not self._is_legacy_default_bot_id(bot_id, owner_id):
            has_access = self._check_bot_access(owner_id, bot_id)
            if not has_access:
                return SessionListResponse(
                    sessions=[],
                    total=0,
                    page=page,
                    limit=limit,
                    has_more=False,
                )

        # Default: DB mode (no fallback to Langfuse)
        return await self._list_sessions_db(
            owner_id=owner_id,
            from_date=from_date,
            to_date=to_date,
            page=page,
            limit=limit,
            bot_id=bot_id,
            trace_id=trace_id,
            session_id=session_id,
            session_key=session_key,
            query=query,
            biz_scene=biz_scene,
            biz_task_id=biz_task_id,
            group_id=group_id,
            match_mode=match_mode,
            include_output_match=include_output_match,
        )

    async def _list_sessions_db(
        self,
        owner_id: str | None,
        from_date: datetime,
        to_date: datetime,
        page: int,
        limit: int,
        bot_id: str | None,
        trace_id: str | None,
        session_id: str | None,
        session_key: str | None,
        query: str | None,
        biz_scene: str | None,
        biz_task_id: str | None,
        group_id: str | None,
        match_mode: str,
        include_output_match: bool,
        query_scope: QueryScope = QueryScope.OWNER,
    ) -> SessionListResponse:
        """List sessions using one DB source per request.

        During dual-write, the same trace may exist in both AC OTEL and
        Langfuse backup tables. Mixing sources within one paginated response
        can duplicate traces and corrupt totals, so legacy fallback only
        happens when the new table has no matching rows at all.
        """
        # Convert datetime to milliseconds timestamp
        from_ms = int(from_date.timestamp() * 1000)
        to_ms = int(to_date.timestamp() * 1000)

        sessions, total = self._db_repo.list_ocb_traces(
            owner_id=owner_id,
            from_ms=from_ms,
            to_ms=to_ms,
            page=page,
            limit=limit,
            bot_id=bot_id,
            trace_id=trace_id,
            session_id=session_id,
            session_key=session_key,
            query=query,
            biz_scene=biz_scene,
            biz_task_id=biz_task_id,
            group_id=group_id,
            match_mode=match_mode,
            include_output_match=include_output_match,
            query_scope=query_scope,
        )

        if total == 0:
            sessions, total = self._db_repo.list_traces(
                owner_id=owner_id,
                from_ms=from_ms,
                to_ms=to_ms,
                page=page,
                limit=limit,
                bot_id=bot_id,
                trace_id=trace_id,
                session_id=session_id,
                session_key=session_key,
                query=query,
                biz_scene=biz_scene,
                biz_task_id=biz_task_id,
                group_id=group_id,
                match_mode=match_mode,
                include_output_match=include_output_match,
                query_scope=query_scope,
            )

        has_more = page * limit < total

        return SessionListResponse(
            sessions=sessions,
            total=total,
            page=page,
            limit=limit,
            has_more=has_more,
        )

    async def _list_sessions_langfuse(
        self,
        owner_id: str,
        from_date: datetime,
        to_date: datetime,
        page: int,
        limit: int,
        bot_id: str | None,
        trace_id: str | None,
        session_id: str | None,
        session_key: str | None,
        query: str | None,
        match_mode: str,
        include_output_match: bool,
    ) -> SessionListResponse:
        """List sessions using Langfuse source with legacy scan logic."""
        requires_exhaustive_scan = bool(
            bot_id
            or trace_id
            or session_id
            or session_key
            or query
            or match_mode == "contains"
        )

        if requires_exhaustive_scan:
            return await self._list_sessions_with_exhaustive_scan(
                owner_id=owner_id,
                from_date=from_date,
                to_date=to_date,
                page=page,
                limit=limit,
                bot_id=bot_id,
                trace_id=trace_id,
                session_id=session_id,
                session_key=session_key,
                query=query,
                match_mode=match_mode,
                include_output_match=include_output_match,
            )

        traces, total = await self._fetch_traces_from_langfuse(
            owner_id=owner_id,
            from_date=from_date,
            to_date=to_date,
            page=page,
            page_limit=limit,
        )

        sessions = [
            _map_trace_to_session(t)
            for t in traces
        ]
        sessions = _apply_client_side_filters(
            sessions,
            bot_id,
            trace_id,
            session_id,
            session_key,
            query,
            match_mode=match_mode,
            include_output_match=include_output_match,
        )
        self._db_repo.enrich_labels(sessions)

        filtered_total = len(sessions)
        has_more = (page * limit) < total

        return SessionListResponse(
            sessions=sessions,
            total=(
                filtered_total
                if (
                    bot_id
                    or trace_id
                    or session_key
                    or session_id
                    or query
                )
                else total
            ),
            page=page,
            limit=limit,
            has_more=has_more,
        )

    async def _fetch_traces_from_langfuse(
        self,
        owner_id: str,
        from_date: datetime,
        to_date: datetime,
        page: int,
        page_limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch traces from Langfuse Public API."""
        auth = _build_auth_header(
            self._langfuse_public_key, self._langfuse_secret_key
        )
        params = {
            "userId": owner_id,
            "fromTimestamp": from_date.isoformat(),
            "toTimestamp": to_date.isoformat(),
            "page": str(page),
            "limit": str(page_limit),
        }

        url = f"{self._langfuse_base_url}/api/public/traces"
        headers = {
            "Authorization": auth,
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10), ssl=False
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error(
                            "Langfuse list traces failed: status=%s, url=%s, params=%s, body=%s",
                            resp.status, url, params, text[:500],
                        )
                        raise LangfuseAPIError(
                            f"Langfuse API returned {resp.status}", status_code=resp.status
                        )
                    data = await resp.json()
        except LangfuseAPIError:
            raise
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.exception(
                "Langfuse list traces request failed: error_type=%s, url=%s, params=%s",
                type(e).__name__, url, params,
            )
            raise LangfuseAPIError(
                f"Langfuse API request failed: {type(e).__name__}: {e}"
            ) from e
        except Exception as e:
            logger.exception(
                "Unexpected error while requesting Langfuse list traces: error_type=%s, url=%s, params=%s",
                type(e).__name__, url, params,
            )
            raise

        traces = data.get("data") or []
        total = data.get("meta", {}).get("totalItems", 0)
        return traces, total

    async def _list_sessions_with_exhaustive_scan(
        self,
        owner_id: str,
        from_date: datetime,
        to_date: datetime,
        page: int,
        limit: int,
        bot_id: str | None,
        trace_id: str | None,
        session_id: str | None,
        session_key: str | None,
        query: str | None,
        match_mode: str = "exact",
        include_output_match: bool = False,
    ) -> SessionListResponse:
        """List sessions with exhaustive multi-page scan for exact metadata matching.

        This is used for exact metadata filters to find all matching traces within
        the scan window, not just matches on the current Langfuse page.
        """
        max_scan_pages = _get_max_scan_pages()
        matched_sessions: list[ConversationSession] = []
        scanned_count = 0
        total_items = 0

        for scan_page in range(1, max_scan_pages + 1):
            traces, total = await self._fetch_traces_from_langfuse(
                owner_id=owner_id,
                from_date=from_date,
                to_date=to_date,
                page=scan_page,
                page_limit=_SCAN_PAGE_SIZE,
            )
            total_items = total

            sessions = [
                _map_trace_to_session(t)
                for t in traces
            ]
            filtered = _apply_client_side_filters(
                sessions,
                bot_id,
                trace_id,
                session_id,
                session_key,
                query,
                match_mode=match_mode,
                include_output_match=include_output_match,
            )
            matched_sessions.extend(filtered)
            scanned_count += len(sessions)

            if scanned_count >= total_items:
                break

        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_sessions = matched_sessions[start_idx:end_idx]
        self._db_repo.enrich_labels(paginated_sessions)

        has_more = (page * limit < len(matched_sessions)) or (scanned_count < total_items)

        return SessionListResponse(
            sessions=paginated_sessions,
            total=len(matched_sessions),
            page=page,
            limit=limit,
            has_more=has_more,
        )

    async def get_session(self, trace_id: str, owner_id: str | None = None, log_source: str | None = None) -> ConversationDetail:
        """Get a single conversation session detail with observations."""
        effective_source = self._get_log_source(log_source)

        if effective_source == "langfuse":
            return await self._get_session_langfuse(trace_id, owner_id)

        # Default: DB mode
        return await self._get_session_db(trace_id, owner_id)

    async def _get_session_db(self, trace_id: str, owner_id: str | None = None) -> ConversationDetail:
        """Get session detail from DB using one source per trace.

        If the AC OTEL trace exists, observations also come from AC OTEL.
        Legacy tables are used only when the trace itself is absent from the
        new table, avoiding mixed OCB/Langfuse details for dual-written traces.
        """
        row = self._db_repo.get_ocb_trace(trace_id)
        is_ocb_row = row is not None
        if row is None:
            row = self._db_repo.get_trace(trace_id)

        if row is None:
            raise SessionNotFoundError(f"Trace {trace_id} not found or not accessible")

        # Bot access check based on row's bot_id.
        # - legacy default alias (or null bot_id): require trace's user_id to match
        #   caller — see _is_legacy_default_bot_id; has_bot_access can't disambiguate
        #   a literal "default" across owners.
        # - non-legacy bot: require caller to be owner or collaborator.
        if owner_id:
            if (
                row.bot_id is None
                or self._is_legacy_default_bot_id(row.bot_id, owner_id)
            ):
                if row.user_id != owner_id:
                    raise SessionNotFoundError(f"Trace {trace_id} not found or not accessible")
            else:
                if not self._db_repo.has_bot_access(owner_id, row.bot_id):
                    raise SessionNotFoundError(f"Trace {trace_id} not found or not accessible")

        if is_ocb_row:
            observations = self._db_repo.list_ocb_observations(trace_id)
        else:
            observations = self._db_repo.list_legacy_observations(trace_id)

        return self._db_repo._row_to_detail(row, observations)

    async def _get_session_langfuse(self, trace_id: str, owner_id: str | None = None) -> ConversationDetail:
        """Get session detail from Langfuse API."""
        auth = _build_auth_header(
            self._langfuse_public_key, self._langfuse_secret_key
        )
        headers = {
            "Authorization": auth,
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            trace_url = f"{self._langfuse_base_url}/api/public/traces/{trace_id}"
            async with session.get(
                trace_url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10), ssl=False
            ) as resp:
                if resp.status == 404:
                    raise SessionNotFoundError(f"Trace {trace_id} not found or not accessible")
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Langfuse get trace failed: status={resp.status}, body={text[:500]}")
                    raise LangfuseAPIError(f"Langfuse API returned {resp.status}", status_code=resp.status)
                trace_data = await resp.json()

            # Access check for Langfuse traces.
            # - default bot (or missing bot_id): require trace's userId to match caller.
            # - non-default bot: require caller to be owner or collaborator.
            if owner_id:
                metadata_raw = trace_data.get("metadata") or {}
                attributes = (metadata_raw.get("attributes") or {}) if isinstance(metadata_raw, dict) else {}
                trace_bot_id = attributes.get("identity.bot_id")
                trace_owner_id = _trace_owner_id(trace_data, attributes)

                if (
                    trace_bot_id is None
                    or self._is_legacy_default_bot_id(trace_bot_id, owner_id)
                ):
                    if trace_owner_id != owner_id:
                        raise SessionNotFoundError(f"Trace {trace_id} not found or not accessible")
                else:
                    if not self._db_repo.has_bot_access(owner_id, trace_bot_id):
                        raise SessionNotFoundError(f"Trace {trace_id} not found or not accessible")

        observations = await self._fetch_observations_from_langfuse(trace_id)

        metadata_raw = trace_data.get("metadata") or {}
        attributes = (metadata_raw.get("attributes") or {}) if isinstance(metadata_raw, dict) else {}
        usage = trace_data.get("usage") or {}
        session_id = attributes.get("gen_ai.session.id")
        session_key = (
            attributes.get("session_id")
            or attributes.get("gen_ai.conversation.id")
        )
        bot_id = attributes.get("identity.bot_id")
        detail = ConversationDetail(
            id=trace_data.get("id", ""),
            biz_task_id=(
                trace_data.get("biz_task_id")
                or attributes.get("agentic.biz_task_id")
                or attributes.get("identity.biz_task_id")
            ),
            biz_scene=(
                trace_data.get("biz_scene")
                or attributes.get("agentic.biz_scene")
                or attributes.get("identity.biz_scene")
            ),
            session_id=session_id,
            session_key=session_key,
            bot_id=bot_id,
            bot_name=None,
            group_id=None,
            session_kind=None,
            name=trace_data.get("name") or "未命名会话",
            input=trace_data.get("input"),
            output=trace_data.get("output"),
            status="FAILED" if trace_data.get("success") is False else "SUCCESS",
            timestamp=trace_data.get("timestamp", ""),
            user_id=_trace_owner_id(trace_data, attributes),
            metadata=SessionMetadata(attributes=attributes),
            total_cost=float(trace_data.get("totalCost") or 0),
            latency_ms=float(trace_data.get("latency") or 0),
            total_tokens=int(usage.get("totalTokens") or 0),
            observations=observations,
        )
        self._db_repo.enrich_labels([detail])
        return detail

    async def _fetch_observations_from_langfuse(
        self, trace_id: str
    ) -> list[ConversationObservation]:
        """Fetch observation tree from Langfuse API."""
        auth = _build_auth_header(
            self._langfuse_public_key, self._langfuse_secret_key
        )
        headers = {
            "Authorization": auth,
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                obs_url = f"{self._langfuse_base_url}/api/public/observations"
                obs_params = {"traceId": trace_id, "limit": "50"}
                async with session.get(
                    obs_url, params=obs_params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10), ssl=False,
                ) as resp:
                    if resp.status == 200:
                        obs_data = await resp.json()
                        observations_raw = obs_data.get("data") or []
                        return _build_observation_tree(observations_raw)
        except Exception as e:
            logger.warning(f"Failed to fetch observations from Langfuse: {e}")

        return []

    async def health_check(self) -> HealthCheckData:
        """Check Langfuse API connectivity."""
        """Check Langfuse API connectivity."""
        if (
            not self._langfuse_public_key
            or not self._langfuse_secret_key
            or not self._langfuse_base_url
        ):
            return HealthCheckData(
                status="unhealthy",
                langfuse_url=None,
                error="Langfuse credentials not configured",
            )

        auth = _build_auth_header(
            self._langfuse_public_key, self._langfuse_secret_key
        )
        headers = {
            "Authorization": auth,
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self._langfuse_base_url}/api/public/traces"
                params = {"limit": "1", "page": "1"}
                async with session.get(
                    url, params=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        return HealthCheckData(
                            status="healthy",
                            langfuse_url=self._langfuse_base_url,
                        )
                    text = await resp.text()
                    return HealthCheckData(
                        status="unhealthy",
                        langfuse_url=self._langfuse_base_url,
                        error=f"Langfuse API returned {resp.status}: {text[:200]}",
                    )
        except Exception as e:
            return HealthCheckData(
                status="unhealthy",
                langfuse_url=self._langfuse_base_url,
                error=str(e),
            )
