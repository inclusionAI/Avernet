"""Sessions group — ``/openapi/v1/bots/{bot_id}/sessions``.

Wraps the engine's ``/api/sessions`` surface. An **operator console**: served
to the addressed bot's owner and its member-level collaborators, for the
stage the request names (``?stage=``, draft by default), and device-wide —
see ``engine_runtime/gating.py`` and ``core/engine_runtime/gate.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import StreamingResponse

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas import (
    MessagePage,
    Session,
    SessionCreate,
    SessionFavorite,
    SessionFile,
    SessionFileList,
    SessionFileUploadCompleteRequest,
    SessionFileUploadGrant,
    SessionFileUploadIntentRequest,
    SessionFileUploadIntentResult,
    SessionPage,
    SessionUpdate,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.schemas_helpers import (
    _as_list,
    _history_page,
    _history_window,
    _map_message,
    _map_session,
    _page,
    _require_within_depth,
    _session_file_headers,
    _session_file_not_found,
    _session_file_resource,
    _window,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.converter_creation import reconcile_created_session
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.dependencies_session_files import (
    OpenApiSessionFileAdapter,
    UploadActorNameDep,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.file_access import (
    authorize_file_list,
    authorize_file_operation,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    DeviceUuidQuery,
    OwnerIdDep,
    StageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.friend_chat import FriendUserIdQuery
from .backend import resolve_session_backend as _resolve_session_backend
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    deleted,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.expert_chat_service import ExpertChatServiceProtocol
from agentclaw.community.api.human_bot_friendship_service import HumanBotFriendshipServiceProtocol
from agentclaw.community.core.engine_runtime.errors import (
    EngineCallerRuntimePreparingError,
    EngineDeviceNotReadyError,
    EngineResourceNotFoundError,
    EngineUpstreamError,
)
from agentclaw.community.core.engine_runtime.session_key import SessionKeyCodecRegistry
from agentclaw.community.core.resources.service import FileTooLargeError
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest
from .listing import list_sessions, _raise_expert_error
from .operator_reads import list_operator_messages, list_operator_sessions
from agentclaw.community.di import Injected
from agentclaw.community.utils.runtime_list_timing import mark
router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/sessions",
    tags=["sessions"],
    route_class=PublicAPIRoute,
)
router.add_api_route("", list_sessions, methods=["GET"], response_model=Envelope[SessionPage])

#: The path parameter naming the session an operation addresses.
SessionIdPath = Annotated[
    str,
    Path(
        description="The session's id, exactly as returned in a session "
        "listing's session_id — use it verbatim, do not re-encode it."
    ),
]

#: One extra item is requested beyond the page, purely to learn whether more
#: exist. Neither engine route reports a total, and ``Page.total`` is required —
#: so for this group the total is derived from the window rather than invented,
#: and both paged routes answer with :class:`BoundedPage` to say so.
_LOOKAHEAD = 1

#: How far back message history is served, in messages. The history fetch is
#: tail-limited and its cost is the whole window, not the page — see
#: :func:`_history_window` — so without a ceiling the page number alone
#: multiplies into an arbitrarily large upstream request. A page reaching past
#: this depth is refused rather than served short; see
#: :func:`_require_within_depth` for why an empty page could not be used.
#: Generous for a conversation; bounded enough that a page number cannot be
#: turned into device load.
_MAX_HISTORY_DEPTH = 5000

