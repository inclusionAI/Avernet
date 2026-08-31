"""Which domain error becomes which public response — the tables, and only them.

Split out of ``responses.py``, which had reached the 1000-line cap this
repository enforces on any one module. The line is a real one rather than an
arbitrary cut: **this** module answers "what does this failure mean to a
caller", and ``responses.py`` answers "how is a response built". Almost every
import in this file exists to name an error type in the tables below, which is
why they came with it — 176 of the 198 names were used by nothing else.

Consumers keep importing :data:`ENVELOPE_ERRORS`, :data:`ENVELOPE_ERROR_CODES`
and :class:`SkillCenterMarketplaceUnavailableError` from ``responses``, which
re-exports them, so the split is invisible outside these two files.

The rules the tables follow have not changed and are stated where they always
were: fixed public messages, specific leaves before their base classes, and two
404s that are byte-for-byte identical so existence cannot be probed.
"""

from json import JSONDecodeError
from agentclaw.community.api.bot_startup_script_service import (
    MAX_SCRIPT_BYTES,
    StartupScriptNotEncodableError,
    StartupScriptTooLargeError,
)
from agentclaw.community.api.bot_config_manifest_service import (
    MAX_DOCUMENT_BYTES,
    ManifestNotEncodableError,
    ManifestTooLargeError,
    ManifestValidationError,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    BotAccessRefusedError,
    BotEditLockCheckError,
    BotEditLockRequiredError,
    CallerIdentityConflictError,
    CallerIdentityForbiddenError,
    CallerIdentityInvalidError,
    CallerIdentityOpenApiError,
    DeptLookupError,
    ClusterMismatchError,
    GrantNotResolvableError,
    IamTokenUnavailableError,
    MissingPrincipalError,
    StartupScriptUnsupportedError,
    UnsupportedEngineError,
    UserIdMismatchError,
)
from agentclaw.community.adapters.http.openapi_v1.errors_bot_create import (
    BOT_CREATE_HTTP_ERRORS,
)
from agentclaw.community.adapters.http.openapi_v1.errors_space import (
    SpaceErrorCode,
    SpacePublicErrorMessage,
)
from agentclaw.community.adapters.http.openapi_v1.errors_space_skill import (
    SPACE_SKILL_ERROR_CODES,
    SPACE_SKILL_HTTP_ERRORS,
)
from agentclaw.community.adapters.http.openapi_v1.errors_work_order import (
    WorkOrderErrorCode,
    WorkOrderPublicErrorMessage,
)
from agentclaw.community.core.bot_app_grant.errors import (
    GrantBotNotLiveError,
    GrantIdentityTooLongError,
    GrantNotFoundError,
    GrantOwnerConflictError,
)
from agentclaw.community.core.bot_collaborator.errors import (
    BotNotFoundError as CollaboratorBotNotFoundError,
    BotNotServiceTypeError,
    CannotRemoveSelfError,
    CollaboratorAlreadyExistsError,
    CollaboratorNotFoundError,
    CollaboratorSpaceMembershipError,
    InvalidCollaboratorRoleError,
    PermissionDeniedError as CollaboratorPermissionDeniedError,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotInvalidLifecycleStateError,
    BotLimitExceededError,
    BotNameExistsError,
    BotNameInvalidError,
    BotNotFoundError,
    BotOperationNotAllowedError,
    BotPermissionError,
    BotServiceError,
    DeviceLimitError,
)
from agentclaw.community.core.bot_public.services.bot_public_service import (
    BotNotFoundError as BotPublicBotNotFoundError,
    BotPublicServiceError,
)
from agentclaw.community.core.channel.errors import (
    ChannelEditLockedError,
    ChannelNotFoundError,
    ChannelSyncError,
)
from agentclaw.community.core.bot_management.render_screen.errors import (
    RenderScreenConflictError,
    RenderScreenNotFoundError,
)
from agentclaw.community.core.bot_chat.errors import (
    InvalidBotLogQueryError,
    SessionNotFoundError,
)
from agentclaw.community.core.bot_management.create_flow import (
    AuthStatusUnavailableError,
)
from agentclaw.community.core.bot_inventory.errors import (
    BotInventoryOperationNotAllowedError,
    BotInventoryPermissionError,
    BotInventoryUpstreamError,
)
from agentclaw.community.core.bot_dormant.activate_service import InvalidBotStateError
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.cron.errors import (
    CronApiTimeoutError,
    CronRelayError,
)
from agentclaw.community.core.caller_identity.contracts import (
    CallerCallTypeInvalidError,
    CallerIdentityAmbiguousError,
    CallerIdentityIrreversibleError,
    CallerIdentityNotFoundError,
    CallerIdentityPermissionError,
    CallerIdentityReadOnlyError,
    CallerLockEpochError,
    CallerMcpNotFoundError,
    CallerMcpSyncError,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineBotTypeNotSupportedError,
    EngineHistoryDepthExceededError,
    EngineCapabilityUnsupportedError,
    EngineDeviceNotReadyError,
    EngineResourceNotFoundError,
    EngineRuntimeError,
    EngineStageNotLiveError,
    EngineStageReadOnlyError,
    EngineUpstreamError,
)
from agentclaw.community.core.gateway_principal import PrincipalVerificationError
from agentclaw.community.core.harness.errors import (
    HealthDiagnosisConflictError,
    HealthDiagnosisNotFoundError,
    HealthDiagnosisUnavailableError,
)
from agentclaw.community.core.market_favorites.errors import (
    FavoriteNotFoundError,
    FavoriteTargetInvalidError,
)
from agentclaw.community.core.spaces.errors import (
    PersonalSpaceInvariantError,
    SpaceAccessDeniedError,
    SpaceAlreadyExistsError,
    SpaceCreatorInvariantError,
    SpaceMemberAlreadyExistsError,
    SpaceMemberInvalidError,
    SpaceMemberNotFoundError,
    SpaceNameInvalidError,
    SpaceNotFoundError,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderAlreadyProcessedError,
    WorkOrderCallbackError,
    WorkOrderApplicantAlreadyEditorError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderBotEditorRequestNotAllowedError,
    WorkOrderSkillEditorRequestNotAllowedError,
    WorkOrderSkillApplicantAlreadyEditorError,
    WorkOrderInvalidReasonError,
    WorkOrderInvalidEventError,
    WorkOrderInvalidRemarkError,
    WorkOrderJoinNotAllowedError,
    WorkOrderNoReviewerError,
    WorkOrderNotFoundError,
    WorkOrderNotificationNotFoundError,
)
from agentclaw.community.core.mcp.errors import (
    McpConfigValueError,
    McpHeadersInvalidError,
    McpMarketUnavailableError,
    McpServerNotFoundError,
    McpSyncFailedError,
)
from agentclaw.community.core.resources.service import (
    DuplicateResourceError,
    FileTooLargeError,
    InvalidResourcePathError,
    ResourceNotFoundError,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillDuplicateError,
    LocalSkillActiveError,
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillEditPausedError,
    LocalSkillLayoutRollbackError,
    LocalSkillInvalidPackageError,
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillOwnerAmbiguousError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
    RepositoryCatalogNotFoundError,
    RepositoryCatalogSyncFailedError,
    RepositoryCatalogSyncInProgressError,
    SkillEngineNotSupportedError,
    SkillParameterValidationError,
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneLockUnavailableError,
    SkillSetControlPlaneNotFoundError,
    SkillSetRuntimeReconcileError,
    SkillSetAccessDeniedError,
    McpPermissionDeniedError,
    LocalSkillTooLargeError,
)
from agentclaw.community.adapters.http.openapi_v1 import errors_skill_center
from agentclaw.community.core.services.identity import (
    InvalidIdentityEntityTypeError,
    InvalidIdentityFileTypeError,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipError
from agentclaw.community.plugin_api.passport import PassportError
from agentclaw.community.core.errors import (
    CallbackAuthError,
    CallbackCorrelationError,
)
from agentclaw.community.core.task.domain.errors import (
    GraphAlreadyInitializedError,
    GraphIntegrityError,
    NodeNotFoundError,
    TaskError,
    TaskNotFoundError,
    TaskStateError,
)
from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import (
    LockNotHeldError,
    LockReleaseDeniedError,
)
from agentclaw.community.core.service_bot.errors import (
    ServiceContainerConflictError,
    ServiceContainerNotFoundError,
    ServiceContainerUpstreamError,
    ServicePublicationConflictError,
    ServicePublicationLockedError,
    ServicePublicationNotFoundError,
    ServicePublicationUnsupportedError,
)
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterMarketSearchError,
    SkillCenterPublishStatusError,
    SkillCenterTeamCreateError,
)


class SkillCenterMarketplaceUnavailableError(RuntimeError):
    """A public Skill Center marketplace read could not be served."""


# Domain error → (HTTP status, fixed public message). Only the specific leaf
# errors are listed; anything unmapped propagates to the app's existing 500
# handler. Messages are fixed (never ``str(exc)``) so that (a) internal
# identifiers and internal-language text never leak to external callers, and
# (b) the two 404-mapped errors are byte-for-byte identical — a caller cannot
# tell "exists but not yours/other tenant" from "does not exist".
ENVELOPE_ERRORS: dict[type[Exception], tuple[int, str]] = {
    IamTokenUnavailableError: (401, "IAM credential is unavailable"),
    CallerIdentityInvalidError: (400, "Invalid Caller identity request"),
    CallerIdentityForbiddenError: (403, "Forbidden"),
    CallerIdentityConflictError: (409, "Caller identity target is ambiguous"),
    CallerIdentityOpenApiError: (502, "Caller identity operation failed"),
    CallerIdentityPermissionError: (404, "Not found"),
    CallerIdentityNotFoundError: (404, "Not found"),
    CallerIdentityAmbiguousError: (409, "Caller identity target is ambiguous"),
    CallerIdentityIrreversibleError: (409, "Caller identity cannot be reverted"),
    CallerIdentityReadOnlyError: (409, "Caller identity configuration is read-only"),
    CallerLockEpochError: (423, "Edit lock required"),
    CallerMcpNotFoundError: (404, "Not found"),
    CallerMcpSyncError: (502, "Caller identity synchronization failed"),
    CallerCallTypeInvalidError: (500, "Internal error"),
    MissingPrincipalError: (401, "Unauthorized"),
    # Byte-identical to the line above, deliberately. "You sent no principal" and
    # "your principal did not verify" must be indistinguishable, or the response
    # tells a forger whether their signature was the part that failed. The seam
    # in ``dependencies.py`` already funnels both into MissingPrincipalError; this
    # entry covers a handler that calls ``verify_principal_token`` directly, so
    # the error cannot escape the envelope as a 500.
    PrincipalVerificationError: (401, "Unauthorized"),
    # 403, not 401: the caller authenticated fine, it just asked to act for
    # someone it may not act for. Not folded into the 401s above for that
    # reason — a partner debugging an integration needs to tell "my credential
    # is wrong" from "my credential is fine but this user is not mine", and the
    # two have different fixes. The message says nothing about which user was
    # asked for; both ids are on the warning line in ``principal.py``.
    UserIdMismatchError: (403, "Forbidden"),
    SpaceAccessDeniedError: (403, "Forbidden"),
    SpaceNotFoundError: (404, "Not found"),
    SpaceMemberInvalidError: (400, "Invalid space member"),
    SpaceMemberNotFoundError: (404, "Not found"),
    SpaceNameInvalidError: (400, "Invalid space name"),
    FavoriteTargetInvalidError: (400, "Invalid favorite target"),
    FavoriteNotFoundError: (404, "Not found"),
    SpaceAlreadyExistsError: (409, "Space already exists"),
    SpaceMemberAlreadyExistsError: (409, "Space member already exists"),
    **SPACE_SKILL_HTTP_ERRORS,
    SpaceCreatorInvariantError: (409, "Space creator cannot be removed or demoted"),
    PersonalSpaceInvariantError: (409, "Personal space membership is immutable"),
    SkillCenterTeamCreateError: (
        502,
        SpacePublicErrorMessage.SKILL_CENTER_TEAM_CREATE_FAILED,
    ),
    SkillCenterMarketSearchError: (502, "Skill Center marketplace unavailable"),
    SkillCenterMarketplaceUnavailableError: (
        502,
        "Skill Center marketplace unavailable",
    ),
    SkillCenterPublishStatusError: (502, "Skill Center publish status unavailable"),
    # Staff directory infra failure (master-data service unreachable/errored).
    # 502, not 200-null: "directory down" must stay distinct from "no dept" so an
    # operator can tell the two apart; the org/user + org/dept lookups raise this
    # and ``@envelope_errors`` maps it. Fixed message — the cause is logged, never
    # returned (mirrors MissingPrincipalError keeping its reason off the wire).
    DeptLookupError: (502, "Department directory unavailable"),
    WorkOrderAccessDeniedError: (403, WorkOrderPublicErrorMessage.FORBIDDEN),
    WorkOrderNotFoundError: (404, WorkOrderPublicErrorMessage.NOT_FOUND),
    WorkOrderNotificationNotFoundError: (
        404,
        WorkOrderPublicErrorMessage.NOT_FOUND,
    ),
    WorkOrderInvalidEventError: (400, "Invalid work-order event"),
    WorkOrderInvalidReasonError: (
        400,
        WorkOrderPublicErrorMessage.INVALID_REASON,
    ),
    WorkOrderInvalidRemarkError: (
        400,
        WorkOrderPublicErrorMessage.INVALID_REMARK,
    ),
    WorkOrderAlreadyPendingError: (
        409,
        WorkOrderPublicErrorMessage.ALREADY_PENDING,
    ),
    WorkOrderAlreadyProcessedError: (
        409,
        WorkOrderPublicErrorMessage.ALREADY_PROCESSED,
    ),
    WorkOrderCallbackError: (502, WorkOrderPublicErrorMessage.CALLBACK_FAILED),
    WorkOrderApplicantAlreadyMemberError: (
        409,
        WorkOrderPublicErrorMessage.APPLICANT_ALREADY_MEMBER,
    ),
    WorkOrderApplicantAlreadyEditorError: (
        409,
        WorkOrderPublicErrorMessage.APPLICANT_ALREADY_EDITOR,
    ),
    WorkOrderJoinNotAllowedError: (
        409,
        WorkOrderPublicErrorMessage.JOIN_NOT_ALLOWED,
    ),
    WorkOrderBotEditorRequestNotAllowedError: (
        409,
        WorkOrderPublicErrorMessage.BOT_EDITOR_REQUEST_NOT_ALLOWED,
    ),
    WorkOrderSkillEditorRequestNotAllowedError: (
        409,
        WorkOrderPublicErrorMessage.SKILL_EDITOR_REQUEST_NOT_ALLOWED,
    ),
    WorkOrderSkillApplicantAlreadyEditorError: (
        409,
        WorkOrderPublicErrorMessage.SKILL_APPLICANT_ALREADY_EDITOR,
    ),
    WorkOrderNoReviewerError: (
        409,
        WorkOrderPublicErrorMessage.NO_REVIEWER,
    ),
    InvalidBotLogQueryError: (400, "Invalid log query"),
    SessionNotFoundError: (404, "Not found"),
    BotNotFoundError: (404, "Not found"),
    ChannelNotFoundError: (404, "Not found"),
    ChannelSyncError: (502, "Channel synchronization failed"),
    # Byte-identical to the line above, deliberately. An application that could
    # tell "I hold no grant for this bot" from "no such bot" would have an
    # enumeration oracle for every bot id in the tenant, so the refusal must be
    # the *same* refusal — same status, same message, same envelope.
    GrantNotResolvableError: (404, "Not found"),
    # And byte-identical again, for the person rather than the application. The
    # seam refuses a caller below an operation's collaborator level with this,
    # and a caller who could tell "not permitted" from "no such bot" would have
    # the same enumeration oracle. Registering it here is what makes that true:
    # the app-level handler asks this table first, and an unmapped error falls
    # through to the raw ``{"detail": ...}`` shape — a *different* body from the
    # envelope a genuinely absent bot returns, which is the tell.
    BotAccessRefusedError: (404, "Not found"),
    BotEditLockRequiredError: (423, "Edit lock required"),
    BotEditLockCheckError: (500, "Internal error"),
    # Withdrawing an authorization that is not there. Shares the 404 shape with
    # an absent bot, and that is not a collision worth avoiding: an owner
    # reconciling their records needs "there was nothing to remove" to read
    # differently from "removed", which the status already gives them. Which of
    # the two 404s they hit is answerable from the bot's own endpoints.
    GrantNotFoundError: (404, "Not found"),
    # The bot went away between being resolved and the row being written.
    # Byte-identical to an absent bot, which is what it now is.
    GrantBotNotLiveError: (404, "Not found"),
    # 400, not 404: the delegation is not missing, it is unrepresentable. The
    # message names no caller-supplied value.
    GrantIdentityTooLongError: (400, "User id is too long to authorize"),
    # 409: the request is well-formed and the caller is entitled to it, but it
    # conflicts with a live authorization on another owner's same-named bot.
    # Retrying is futile and the remedy is a withdrawal, which is what a
    # conflict says and a 400 would not.
    GrantOwnerConflictError: (
        409,
        "Another authorization for this bot id is already live",
    ),
    CollaboratorBotNotFoundError: (404, "Not found"),
    # The bot-public service's own ``BotNotFoundError`` — a distinct class from
    # the bot-management and collaborator ones above, raised by the BCS
    # publish-to-users flow — is the same outcome (a missing bot addressed on a
    # public route), so it answers the same 404 the surface answers everywhere a
    # bot is addressed that does not exist.
    BotPublicBotNotFoundError: (404, "Not found"),
    # The bot-public service's own ``BotPublicServiceError`` — a server-side
    # failure of the BCS publish-to-users flow (approval-ticket submit rejected
    # by the approval service, e.g. a malformed biz_id/puid, or any other
    # invariant the service guards). Distinct from the not-found case above: the
    # bot was addressed and found, the publish itself failed. Mapped here so it
    # surfaces as a business-coded 5xx through ``@envelope_errors`` rather than
    # escaping as a bare 500; the cause is logged at the raise site.
    BotPublicServiceError: (500, "Publish failed"),
    CollaboratorPermissionDeniedError: (404, "Not found"),
    CollaboratorNotFoundError: (404, "Not found"),
    CollaboratorAlreadyExistsError: (409, "Editor already exists"),
    CannotRemoveSelfError: (409, "Use the leave operation to remove yourself"),
    BotNotServiceTypeError: (409, "Editors are not supported for this bot"),
    InvalidCollaboratorRoleError: (400, "Invalid editor role"),
    CollaboratorSpaceMembershipError: (
        409,
        "Editor must be a member of the Bot Team Space",
    ),
    RenderScreenNotFoundError: (404, "Not found"),
    RenderScreenConflictError: (409, "Render-screen mapping already exists"),
    BotPermissionError: (404, "Not found"),
    ServiceContainerNotFoundError: (404, "Not found"),
    ServiceContainerConflictError: (
        409,
        "Container is not in a valid state for this operation",
    ),
    ServiceContainerUpstreamError: (502, "Container service error"),
    HealthDiagnosisNotFoundError: (404, "Not found"),
    HealthDiagnosisConflictError: (409, "A health diagnosis is already running"),
    HealthDiagnosisUnavailableError: (502, "Health diagnosis service error"),
    ServicePublicationNotFoundError: (404, "Not found"),
    LockNotHeldError: (404, "Not found"),
    LockReleaseDeniedError: (404, "Not found"),
    BotNameExistsError: (409, "Bot name already exists"),
    BotNameInvalidError: (400, "Invalid bot name"),
    BotLimitExceededError: (409, "Bot creation limit reached"),
    DeviceLimitError: (409, "Device limit reached"),
    BotInvalidLifecycleStateError: (
        409,
        "Bot is not in a valid state for this operation",
    ),
    BotOperationNotAllowedError: (409, "Operation not supported for this bot"),
    BotInventoryOperationNotAllowedError: (409, "Operation not supported for this bot"),
    # Dormant activate: a bot that is not RECYCLED cannot be reactivated.
    InvalidBotStateError: (409, "Operation not supported for this bot"),
    BotInventoryPermissionError: (404, "Not found"),
    BotInventoryUpstreamError: (502, "Desktop service error"),
    ServicePublicationConflictError: (
        409,
        "Publication is not in a valid state for this operation",
    ),
    ServicePublicationUnsupportedError: (
        409,
        "Operation not supported for this bot",
    ),
    ServicePublicationLockedError: (423, "Edit lock required"),
    ChannelEditLockedError: (423, "Edit lock required"),
    ClusterMismatchError: (400, "engine and cluster_name do not match"),
    UnsupportedEngineError: (400, "Unsupported engine"),
    **BOT_CREATE_HTTP_ERRORS,
    PassportError: (502, "Authorization service error"),
    AuthRelationshipError: (502, "Authorization relationship service error"),
    # Engine-config failures. None of these is a BotServiceError, so the base
    # mapping below does not cover them and they would otherwise escape the
    # envelope. They are also plain RuntimeError *siblings*, not a hierarchy, so
    # each documented propagation path out of EngineConfigService needs its own
    # entry — mapping one does not cover the others.
    DeviceNotBoundError: (409, "Bot has no active device"),
    # The binding row names a device provider the resolver does not know: bad
    # data on our side, never something the caller can correct.
    UnknownProviderError: (500, "Device binding is misconfigured"),
    # The connection-info build called the underlying device service and it
    # failed — an upstream dependency problem, hence 502 like the other
    # downstream-service mappings.
    ConnInfoBuildError: (502, "Device service error"),
    # The passport service answered with nothing at all — upstream problem, not
    # a caller mistake, and not an unhandled crash.
    AuthStatusUnavailableError: (502, "Authorization service error"),
    JSONDecodeError: (500, "Malformed engine configuration"),
    # Resources domain errors — ValueError subclasses raised by the slim
    # core/resources/service.py. Mapped here so the openapi_v1 resources router
    # lets them propagate to @envelope_errors instead of hand-translating with
    # str(exc), which would leak internal ids/paths to external callers.
    DuplicateResourceError: (409, "Resource already exists"),
    ResourceNotFoundError: (404, "Not found"),
    # The message says the path was rejected but not what the server made of it:
    # echoing the caller's path back, or naming the segment that failed, turns a
    # rejection into a probe for how addresses are resolved.
    InvalidResourcePathError: (400, "Invalid resource path"),
    LocalSkillNotFoundError: (404, "Not found"),
    SkillSetControlPlaneNotFoundError: (404, "Not found"),
    SkillSetAccessDeniedError: (403, "Forbidden"),
    McpPermissionDeniedError: (403, "Forbidden"),
    SkillSetControlPlaneConflictError: (
        409,
        "SkillSet state conflicts with this operation",
    ),
    # 409, not 503. Two of this error's three raise sites are a lease this
    # request held being lost mid-mutation, and the third is the fence refusing
    # to be taken — in every one of them the service is up and answering, and
    # the command simply lost a race for one Bot's fence. A 503 would tell
    # proxies and client retry layers the whole surface is out of rotation over
    # a per-Bot conflict. The internal /api/skillsets mapping in
    # ``adapters.http.app`` answers 409 for the same reason.
    SkillSetControlPlaneLockUnavailableError: (
        409,
        "Another SkillSet mutation holds this Bot's fence",
    ),
    SkillSetRuntimeReconcileError: (502, "Skill runtime synchronization failed"),
    LocalSkillOwnerAmbiguousError: (409, "Ambiguous Local Skill owner"),
    LocalSkillInvalidPackageError: (400, "Invalid Skill package"),
    LocalSkillNotReadyError: (409, "Bot is not ready"),
    LocalSkillActiveError: (409, "Skill is active"),
    LocalSkillDuplicateError: (409, "Local Skill already exists"),
    LocalSkillTooLargeError: (413, "Skill package is too large"),
    LocalSkillStorageError: (502, "Skill storage operation failed"),
    SkillParameterValidationError: (422, "Skill parameters are invalid"),
    LocalSkillRuntimeSyncError: (502, "Skill runtime synchronization failed"),
    LocalSkillEditBusyError: (409, "Another Skill update is in progress"),
    LocalSkillLayoutRollbackError: (409, "Skill layout rollback is in progress"),
    LocalSkillEditLockUnavailableError: (
        503,
        "Skill update service is temporarily unavailable",
    ),
    LocalSkillEditPausedError: (409, "Skill layout is being updated"),
    SkillRuntimeNameConflictError: (409, "Skill runtime name conflicts with an active Skill"),
    SkillEngineNotSupportedError: (409, "Skill is not supported by this bot type and engine"),
    RepositoryCatalogNotFoundError: (404, "Not found"),
    RepositoryCatalogSyncInProgressError: (409, "Repository synchronization is already in progress"),
    RepositoryCatalogSyncFailedError: (502, "Repository synchronization failed"),
    **errors_skill_center.SKILL_CENTER_ENVELOPE_ERRORS,
    FileTooLargeError: (413, "File too large for preview"),
    # Startup script (issue #926): the body is refused at write time so a
    # caller learns the limit instead of hitting it inside a container. The
    # limit is interpolated from the constant rather than typed as a literal so
    # the message cannot drift from what the service actually enforces — and
    # unlike ``str(exc)`` it carries no caller data or internal path, which is
    # what the fixed-message rule above is protecting against.
    # A body JSON accepted but UTF-8 cannot encode — a lone surrogate. The
    # caller's input, so a 400, not the 500 an unmapped encode error would give.
    StartupScriptNotEncodableError: (400, "Startup script is not valid UTF-8"),
    StartupScriptTooLargeError: (
        413,
        f"Startup script exceeds the {MAX_SCRIPT_BYTES}-byte limit",
    ),
    # ... and refused outright for a bot whose container cannot run one,
    # rather than stored where it would silently never execute.
    StartupScriptUnsupportedError: (
        409,
        "Startup script is not supported for this bot",
    ),
    # Config manifest (issue #1469). The 422 is the all-or-nothing refusal, and
    # it is one of only two errors on this surface that carry ``data``: the
    # fixed message says a document was refused, and ``data.violations`` names
    # each offending entry and the rule it broke. Those come from the caller's
    # own document — locations and rule names — never from an internal path or
    # another tenant's data, which is what the fixed-message rule protects.
    ManifestValidationError: (422, "Config manifest is invalid"),
    ManifestTooLargeError: (
        413,
        f"Config manifest exceeds the {MAX_DOCUMENT_BYTES}-byte limit",
    ),
    ManifestNotEncodableError: (400, "Config manifest is not valid UTF-8"),
    # Identity domain errors — ValueError subclasses raised by IdentityService
    # validate_entity_type / validate_file_type.
    InvalidIdentityEntityTypeError: (400, "Invalid entity type"),
    InvalidIdentityFileTypeError: (400, "Invalid file type"),
    # ── Engine-runtime (Track C) ──────────────────────────────────────────
    # Ordering inside this block is load-bearing: ``EngineRuntimeError`` is the
    # base of every ``Engine*`` error below it and is listed AFTER them.
    # ``test_engine_runtime_base_does_not_swallow_its_leaves`` finds the leaves
    # by scanning the errors module and its ``__all__``, so a new one is covered
    # here without editing any list — but it must still be given an entry below.
    # Lookup returns on the first isinstance match in insertion order, so a base
    # placed first would swallow every leaf under it — the trap recorded in the
    # Track B gotchas.
    #
    # The three ``DeviceAdapter*`` errors are *siblings*, not a hierarchy
    # (``TimeoutError`` and two independent ``ValueError`` subclasses —
    # ``plugin_api/device_adapter_transport.py``), so each needs its own entry
    # and their relative order does not matter. Do not assume otherwise: a
    # comment here previously claimed EndpointNotFound subclassed HTTPStatus,
    # which is false and would have justified a wrong "fix" to the ordering.
    #
    # The two 501s are distinct answers to distinct questions and must not be
    # merged: one is "your bot's engine does not offer this", answerable from
    # the capabilities endpoint; the other is "this operation is not offered for
    # your bot's type", which capabilities cannot tell you.
    EngineBotTypeNotSupportedError: (
        501,
        "Not supported for this bot type",
    ),
    EngineCapabilityUnsupportedError: (
        501,
        "Not supported by this bot's engine; see the engine capabilities endpoint",
    ),
    # Retryable: cold, dormant or restarting. Distinct from 404 (the bot IS the
    # caller's) and from 500 (nothing is broken).
    EngineDeviceNotReadyError: (409, "Bot device is not ready"),
    # NOT retryable as-is: the named stage has no live runtime (nothing
    # validating for verify, nothing released for online, or a published stage
    # named on a personal bot). Distinct from the masked 404 deliberately — the
    # operator adjudication has already run, and an operator may fix a stage by
    # publishing — and from "device not ready", which promises a retry will
    # eventually succeed.
    EngineStageNotLiveError: (409, "No live runtime at the requested stage"),
    # A write addressed to a published runtime. 409 like the two above, and a
    # *separate* answer from both: "no live runtime" would send the caller off to
    # publish one and retry, which would not help, and the caller is entitled to
    # the operation — it is the addressed runtime that does not take writes.
    # Deliberately not a 200 with a no-op flag either: automation that checks the
    # status code would record the write as landed.
    EngineStageReadOnlyError: (409, "The requested stage is read-only"),
    # An out-of-range page argument, so it joins the 422 FastAPI already returns
    # for page_size > 100 rather than inventing a status. Needs a mapped entry
    # rather than a bare HTTPException: app-level handlers replace an unmapped
    # message with the bare HTTP reason phrase, and "Unprocessable Entity" would
    # not tell the caller a depth limit is what they hit.
    EngineHistoryDepthExceededError: (
        422,
        "Requested page is deeper than the message history this endpoint serves",
    ),
    # Byte-identical to the other 404s above, so an engine-side missing resource
    # cannot be distinguished from a bot that is not the caller's.
    EngineResourceNotFoundError: (404, "Not found"),
    EngineUpstreamError: (502, "Engine service error"),
    # Base of the Engine* errors above — LAST of its group.
    EngineRuntimeError: (502, "Engine service error"),
    # Cron relay category (routines) — a backstop for engine adapter failures
    # that escape the handler. The delete/other handlers already wrap the explicit
    # error_code-bearing CronRelayError into HTTPException themselves; these
    # entries catch anything the handler does not, so it does not fall through to
    # the app-level 500 with a vague message. Subclass listed before its base.
    CronApiTimeoutError: (504, "Cron relay timed out"),
    CronRelayError: (502, "Cron relay service error"),
    # Transport errors that reach a handler without the relay translating them
    # (e.g. a future caller using the transport directly). The relay already
    # converts the first two; these are the backstop.
    DeviceAdapterTimeoutError: (504, "Engine request timed out"),
    DeviceAdapterEndpointNotFoundError: (404, "Not found"),
    # A sibling of the entry above, not its base — see the block comment. Order
    # between these two is therefore free.
    DeviceAdapterHTTPStatusError: (502, "Engine service error"),
    # MCP category (Track B). These share no base with BotServiceError, so their
    # order among themselves is free — but they must sit above the BotServiceError
    # fallback like everything else. Fixed public messages: the header-validation
    # and value errors carry internal-language text (the validator answers in
    # Chinese), which is exactly what the fixed-message rule keeps from leaking.
    # 404 message is byte-identical to the bots not-found so existence can't be
    # probed. The two upstream failures are 502 (downstream problem), matching how
    # PassportError / ConnInfoBuildError are mapped above.
    McpServerNotFoundError: (404, "Not found"),
    McpHeadersInvalidError: (400, "Invalid MCP headers"),
    McpConfigValueError: (400, "Invalid MCP configuration"),
    McpSyncFailedError: (502, "Device sync failed"),
    McpMarketUnavailableError: (502, "MCP service error"),
    # Base class LAST: the bot mappings above subclass BotServiceError (the
    # resources, identity, MCP, and engine-runtime entries are separate
    # hierarchies that never match a bot error), and
    # the lookup returns on the first isinstance match in insertion order, so the
    # specific mappings still win. Services raise the bare base for device,
    # persistence, and downstream failures — without this the decorator would
    # re-raise and the app's catch-all would answer with {"detail": ...}, which
    # is not an Envelope and breaks the public contract.
    BotServiceError: (500, "Internal error"),
    # Task goal-driven execution framework: the task / callback endpoints raise
    # these domain errors and let ``@envelope_errors`` map them, so the router
    # stays a thin protocol layer (no hand-rolled ``HTTPException`` for domain
    # failures). ``TaskError`` is not a ``DomainError`` — it has no app-level
    # handler — so every task subclass that can reach a handler needs an entry
    # here (concrete leaves first, ``TaskError`` base last as a 500 fallback) or
    # it would escape the envelope as a bare 500. ``CallbackAuthError`` /
    # ``CallbackCorrelationError`` ARE ``DomainError`` (already in the app's
    # ``_DOMAIN_ERROR_STATUS_MAP``) but are mapped here too so the decorator owns
    # them directly; only task code raises them. Discovery's unexpected-failure
    # catch-all raises generic ``InternalError`` (also a ``DomainError``), which
    # is NOT mapped here — it re-raises out of ``@envelope_errors`` to the app's
    # ``DomainError`` handler, keeping this table task-specific.
    TaskNotFoundError: (404, "Not found"),
    NodeNotFoundError: (404, "Not found"),
    GraphAlreadyInitializedError: (409, "Task graph already exists"),
    GraphIntegrityError: (409, "Graph integrity violated"),
    TaskStateError: (409, "Illegal state transition"),
    CallbackAuthError: (401, "Unauthorized"),
    CallbackCorrelationError: (400, "Bad request"),
    TaskError: (500, "Internal error"),
}
# Most public categories retain the ordinary ``xxx000`` business code.  A
# small, explicit override table lets a category expose a stable actionable
# subcode without changing any existing public response.
ENVELOPE_ERROR_CODES: dict[type[Exception], int] = {
    SkillCenterTeamCreateError: SpaceErrorCode.SKILL_CENTER_TEAM_CREATE_FAILED,
    **SPACE_SKILL_ERROR_CODES,
    WorkOrderInvalidEventError: WorkOrderErrorCode.INVALID_REASON,
    WorkOrderInvalidReasonError: WorkOrderErrorCode.INVALID_REASON,
    WorkOrderInvalidRemarkError: WorkOrderErrorCode.INVALID_REMARK,
    WorkOrderAccessDeniedError: WorkOrderErrorCode.ACCESS_DENIED,
    WorkOrderNotFoundError: WorkOrderErrorCode.NOT_FOUND,
    WorkOrderNotificationNotFoundError: WorkOrderErrorCode.NOTIFICATION_NOT_FOUND,
    WorkOrderAlreadyPendingError: WorkOrderErrorCode.ALREADY_PENDING,
    WorkOrderAlreadyProcessedError: WorkOrderErrorCode.ALREADY_PROCESSED,
    WorkOrderCallbackError: WorkOrderErrorCode.CALLBACK_FAILED,
    WorkOrderApplicantAlreadyMemberError: WorkOrderErrorCode.APPLICANT_ALREADY_MEMBER,
    WorkOrderApplicantAlreadyEditorError: WorkOrderErrorCode.APPLICANT_ALREADY_EDITOR,
    WorkOrderNoReviewerError: WorkOrderErrorCode.NO_REVIEWER,
    WorkOrderJoinNotAllowedError: WorkOrderErrorCode.JOIN_NOT_ALLOWED,
    WorkOrderBotEditorRequestNotAllowedError: WorkOrderErrorCode.BOT_EDITOR_REQUEST_NOT_ALLOWED,
    WorkOrderSkillEditorRequestNotAllowedError: WorkOrderErrorCode.SKILL_EDITOR_REQUEST_NOT_ALLOWED,
    WorkOrderSkillApplicantAlreadyEditorError: WorkOrderErrorCode.SKILL_APPLICANT_ALREADY_EDITOR,
    LocalSkillOwnerAmbiguousError: 409104,
    LocalSkillInvalidPackageError: 400101,
    LocalSkillNotReadyError: 409101,
    LocalSkillActiveError: 409102,
    LocalSkillDuplicateError: 409103,
    LocalSkillTooLargeError: 413101,
    LocalSkillStorageError: 502101,
    SkillParameterValidationError: 422101,
    ManifestValidationError: 422109,
    LocalSkillRuntimeSyncError: 502102,
    SkillRuntimeNameConflictError: 409106,
    SkillEngineNotSupportedError: 409107,
    RepositoryCatalogSyncInProgressError: 409108,
    RepositoryCatalogSyncFailedError: 502103,
    **errors_skill_center.SKILL_CENTER_ENVELOPE_ERROR_CODES,
    SkillSetControlPlaneLockUnavailableError: 409209,
    SkillSetAccessDeniedError: 403201,
    McpPermissionDeniedError: 403202,
}

_SKILL_SET_CONFLICT_CODES: dict[str, tuple[int, str]] = {
    "RESOURCE_DIRECT_ACTIVE": (409201, "Resource is directly active"),
    "RESOURCE_MANAGED_BY_SKILL_SET": (409202, "Resource is managed by a SkillSet"),
    "RESOURCE_MANAGED_BY_PLATFORM_POLICY": (
        409210,
        "Resource is managed by the platform Default policy",
    ),
    "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET": (409203, "Resource belongs to another SkillSet"),
    "SYSTEM_DEFAULT_IMMUTABLE": (409204, "System Default SkillSet is immutable"),
    "SKILL_SET_ACTIVE": (409205, "Active SkillSet cannot be deleted"),
    "SKILL_SET_NAME_CONFLICT": (409206, "SkillSet name already exists"),
    "IDEMPOTENCY_KEY_REUSED": (409207, "Idempotency key was reused with a different request"),
    "BOT_MUTATION_BUSY": (409208, "Another SkillSet mutation is in progress"),
}
