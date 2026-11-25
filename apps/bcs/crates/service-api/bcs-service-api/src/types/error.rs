use strum::AsRefStr;

/// Service error type.
#[derive(Debug, thiserror::Error, AsRefStr)]
#[strum(serialize_all = "snake_case")]
pub enum ServiceError {
    #[error(transparent)]
    A2aManagement(#[from] bcs_domain::a2a_management::ManagementError),
    /// Transport proves the request was never submitted and no sender remains.
    /// Only the transport may classify this; timeouts are not such proof.
    #[error("delivery not sent: {code}")]
    DeliveryNotSent { code: &'static str, retryable: bool },
    /// Bot not found.
    #[error("Bot '{0}' not found")]
    BotNotFound(String),

    /// Bot not registered.
    #[error("Bot '{0}' is not registered")]
    BotNotRegistered(String),

    /// Bot is registered but currently has no active runtime connection.
    #[error("Bot '{0}' is not connected")]
    BotNotConnected(String),

    /// The connected Bot runtime does not implement a requested Plugin API method.
    #[error("Bot '{bot_id}' does not support method '{method}'")]
    BotMethodUnsupported { bot_id: String, method: String },

    /// Bot is hidden and not accepting communication.
    #[error("Bot '{0}' is not collaborative")]
    BotHidden(String),

    /// Group not found.
    #[error("Group '{0}' not found")]
    GroupNotFound(String),


    /// Invalid operation on a specific request.
    #[error("Invalid operation on request {request_id}: {message}", request_id = request_id.as_deref().unwrap_or("unknown"))]
    InvalidOperation {
        message: String,
        request_id: Option<String>,
    },

    /// Provider record not found.
    #[error("Provider '{0}' not found")]
    ProviderNotFound(String),

    /// Provider exists but its downlink is not ready (disabled / missing
    /// downlink config / missing or disabled downlink credential).
    #[error("Provider '{provider_id}' downlink not ready: {reason}")]
    ProviderNotReadyForDownlink {
        provider_id: String,
        reason: String,
    },

    /// Bot is already bound to a provider with a different `(provider_id,
    /// provider_bot_ref)` pair.
    #[error(
        "Bot '{bot_id}' already bound to provider '{existing_provider_id}' \
         (ref '{existing_provider_bot_ref}')"
    )]
    BotAlreadyBound {
        bot_id: String,
        existing_provider_id: String,
        existing_provider_bot_ref: String,
    },

    /// Registration committed but a post-commit initialization failed
    /// (P1.3): the Bot/binding stay durable, nothing is auto-retried,
    /// deleted or credential-replayed by BCS; the caller reconciles via
    /// the authenticated management queries and may resubmit the same
    /// command. Disabling the caller configuration can never unblock an
    /// incomplete registration.
    #[error("registration committed; caller reconciliation required")]
    RegistrationReconciliationRequired,
    /// Request conflicts with an existing immutable or concurrently updated resource.
    #[error("Conflict: {0}")]
    Conflict(String),

    /// Unauthorized operation.
    #[error("Unauthorized: {0}")]
    Unauthorized(String),

    /// Forbidden by policy (e.g. AI security gateway block, content moderation).
    /// Distinct from Unauthorized: 403 Forbidden semantics rather than 401.
    /// Caller authentication is fine; the action itself is denied.
    #[error("Forbidden: {0}")]
    Forbidden(String),

    /// Message limit reached for a group.
    #[error("Message limit reached: {0}")]
    MessageLimitReached(String),

    /// Internal error.
    #[error("Internal error: {0}")]
    InternalError(String),

    /// Cannot add yourself as a friend.
    #[error("Cannot add yourself as friend")]
    CannotAddSelf,

    /// A pending friend request already exists.
    #[error("Pending request already exists: {request_id} (from={from_bot:?}, to={to_bot:?})")]
    PendingRequestExists {
        request_id: String,
        from_bot: Option<String>,
        to_bot: Option<String>,
    },

    /// Cannot accept a rejected friend request (AC-21).
    #[error("Cannot accept a rejected request")]
    CannotAcceptRejected,

    /// Cannot reject an accepted friend request (AC-21).
    #[error("Cannot reject an accepted request")]
    CannotRejectAccepted,

    /// One or more bots are not friends.
    /// Contains the list of non-friend bot UUIDs.
    #[error("Not friends: {0:?}")]
    NotFriends(Vec<String>),

    /// Friend request not found.
    #[error("Friend request '{0}' not found")]
    FriendRequestNotFound(String),

    /// Bot is in private mode and cannot participate in collaboration (AC-33).
    #[error("Bot is in private mode and cannot initiate collaboration")]
    PrivateBotCannotCollaborate,

    /// Participant not found in group.
    #[error("Participant '{0}' not found")]
    ParticipantNotFound(String),

    /// Session not found.
    #[error("Session '{0}' not found")]
    SessionNotFound(String),

    /// Invalid session parameters.
    #[error("Invalid session params: {0}")]
    SessionInvalidParams(String),

    /// Group contains non-public bots, preventing visibility change to public.
    /// Each tuple is (bot_uuid, bot_name).
    #[error("Group contains non-public bots preventing visibility change")]
    ExistNonPublicBots {
        bots: Vec<(String, Option<String>)>, // (bot_uuid, bot_name)
    },

    /// IO error.
    #[error("IO error: {0}")]
    #[strum(serialize = "internal_error")]
    IoError(#[from] std::io::Error),

    /// JSON error.
    #[error("JSON error: {0}")]
    #[strum(serialize = "internal_error")]
    JsonError(#[from] serde_json::Error),
}

/// Result type for service operations.
pub type ServiceResult<T> = Result<T, ServiceError>;

impl ServiceError {
    /// Returns dynamic parameters for this error, keyed by entity name.
    /// These are consumed by the frontend for i18n interpolation.
    /// Returns `null` for errors with no dynamic parameters or where
    /// internal details must not be exposed (IoError, JsonError).
    pub fn error_params(&self) -> serde_json::Value {
        match self {
            Self::A2aManagement(error)=>serde_json::json!({"code":error.code,"field":error.field,"reason":error.reason}),
            Self::DeliveryNotSent { code, retryable } => serde_json::json!({"code": code, "retryable": retryable}),
            Self::BotNotFound(id)
            | Self::BotNotRegistered(id)
            | Self::BotNotConnected(id)
            | Self::BotHidden(id) => {
                serde_json::json!({ "bot_id": id })
            }
            Self::BotMethodUnsupported { bot_id, method } => {
                serde_json::json!({ "bot_id": bot_id, "method": method })
            }
            Self::GroupNotFound(id) => serde_json::json!({ "group_id": id }),
            Self::InvalidOperation { message, request_id } => {
                serde_json::json!({ "message": message, "request_id": request_id })
            }
            Self::ProviderNotFound(id) => serde_json::json!({ "provider_id": id }),
            Self::ProviderNotReadyForDownlink { provider_id, reason } => {
                serde_json::json!({ "provider_id": provider_id, "reason": reason })
            }
            Self::BotAlreadyBound { bot_id, existing_provider_id, existing_provider_bot_ref } => {
                serde_json::json!({
                    "bot_id": bot_id,
                    "existing_provider_id": existing_provider_id,
                    "existing_provider_bot_ref": existing_provider_bot_ref,
                })
            }
            Self::PendingRequestExists { request_id, from_bot, to_bot } => {
                serde_json::json!({ "request_id": request_id, "from_bot": from_bot, "to_bot": to_bot })
            }
            Self::NotFriends(ids) => serde_json::json!({ "bot_ids": ids }),
            Self::FriendRequestNotFound(id) => serde_json::json!({ "request_id": id }),
            Self::ParticipantNotFound(id) => serde_json::json!({ "participant_id": id }),
            Self::SessionNotFound(id) => serde_json::json!({ "session_id": id }),
            Self::SessionInvalidParams(reason) => serde_json::json!({ "reason": reason }),
            Self::Conflict(reason)
            | Self::Unauthorized(reason)
            | Self::Forbidden(reason)
            | Self::MessageLimitReached(reason) => {
                serde_json::json!({ "reason": reason })
            }
            Self::InternalError(reason) => {
                // Business-layer InternalError carries a safe, controlled message
                serde_json::json!({ "reason": reason })
            }
            // IoError/JsonError may contain file paths, line numbers, or other
            // internal details - never expose to clients
            Self::IoError(_) | Self::JsonError(_) => serde_json::Value::Null,
            Self::ExistNonPublicBots { bots } => {
                let bot_list: Vec<serde_json::Value> = bots
                    .iter()
                    .map(|(uuid, name)| serde_json::json!({
                        "bot_uuid": uuid,
                        "bot_name": name.as_deref().unwrap_or(uuid),
                    }))
                    .collect();
                serde_json::json!({
                    "code": "exist_none_public_bots",
                    "bots": bot_list,
                })
            }
            Self::CannotAddSelf
            | Self::CannotAcceptRejected
            | Self::CannotRejectAccepted
            | Self::PrivateBotCannotCollaborate
            | Self::RegistrationReconciliationRequired => serde_json::Value::Null,
        }
    }
}

pub const ERROR_EVENT_SUBSCRIPTION_NOT_FOUND: &str = "event_subscription_not_found";
pub const ERROR_EVENT_DELIVERY_NOT_FOUND: &str = "event_delivery_not_found";
pub const ERROR_INVALID_EVENT_FILTER: &str = "invalid_event_filter";
pub const ERROR_INVALID_EVENT_SCOPE: &str = "invalid_event_scope";
pub const ERROR_INVALID_WEBHOOK_URL: &str = "invalid_webhook_url";
pub const ERROR_EVENT_SUBSCRIPTION_LIMIT_REACHED: &str = "event_subscription_limit_reached";
pub const ERROR_EVENT_SUBSCRIPTION_REVISION_CONFLICT: &str = "event_subscription_revision_conflict";
pub const ERROR_EVENT_SUBSCRIPTION_FORBIDDEN: &str = "event_subscription_forbidden";
pub const ERROR_EVENT_DELIVERY_NOT_REPLAYABLE: &str = "event_delivery_not_replayable";
pub const ERROR_EVENT_DELIVERY_LANE_BLOCKED: &str = "event_delivery_lane_blocked";

pub const ERROR_INVITE_CODE_REQUIRED: &str = "invite_code_required";
pub const ERROR_INVITE_CODE_ALREADY_BOUND: &str = "invite_code_already_bound";
pub const ERROR_INVITE_CODE_UNAVAILABLE: &str = "invite_code_unavailable";
pub const ERROR_INVITE_CODE_NOT_APPLICABLE: &str = "invite_code_not_applicable";
pub const ERROR_INVITE_CODE_CLAIM_LIMIT_REACHED: &str = "invite_code_claim_limit_reached";

/// Transport-independent error vocabulary for OpenAPI v1 use cases.
///
/// Lives in `types` (the shared low-level module every contract layer may
/// reference without reverse dependencies) so the outbound ports — e.g.
/// [`crate::port::oauth`] — can type their results with the same
/// vocabulary the application-layer use cases translate into.
#[derive(Debug, thiserror::Error)]
pub enum ApplicationError {
    #[error("{code}: {message}")]
    InvalidInput { code: String, message: String },
    #[error("authentication is required")]
    Unauthenticated,
    #[error("forbidden: {0}")]
    Forbidden(String),
    #[error("{code}: {message}")]
    ForbiddenCode { code: String, message: String },
    #[error("{code}: {message}")]
    NotFound { code: String, message: String },
    #[error("{code}: {message}")]
    Conflict { code: String, message: String },
    #[error("{code}: {message}")]
    Gone { code: String, message: String },
    #[error("{code}: {message}")]
    QuotaExceeded { code: String, message: String },
    #[error("{code}: {message}")]
    PayloadTooLarge { code: String, message: String },
    #[error("{code}: {message}")]
    Unprocessable { code: String, message: String },
    #[error("{code}: {message}")]
    BadGateway { code: String, message: String },
    /// An auth-flow backing dependency (pending-login store, session
    /// identity store, verifier) is unreachable. Carries a stable
    /// sanitized `code`/`message` pair only — never underlying storage,
    /// endpoint, or credential text. Distinct from `BadGateway` (kept for
    /// the existing Bot identity store 502 family); the HTTP envelope
    /// maps this to 503 / 50300 / `auth_dependency_unavailable`.
    #[error("{code}: {message}")]
    Unavailable { code: String, message: String },
    #[error("internal error: {0}")]
    Internal(String),
}

impl ApplicationError {
    pub fn invalid(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::InvalidInput {
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn forbidden(message: impl Into<String>) -> Self {
        Self::Forbidden(message.into())
    }

    pub fn forbidden_code(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::ForbiddenCode {
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn not_found(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::NotFound {
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn conflict(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::Conflict {
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn payload_too_large(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::PayloadTooLarge {
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn unprocessable(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::Unprocessable {
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn bad_gateway(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::BadGateway {
            code: code.into(),
            message: message.into(),
        }
    }

    /// Dependency-unreachable failure with a stable sanitized code.
    pub fn unavailable(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::Unavailable {
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::Internal(message.into())
    }

    pub fn event_subscription_not_found(message: impl Into<String>) -> Self {
        Self::not_found(ERROR_EVENT_SUBSCRIPTION_NOT_FOUND, message)
    }

    pub fn event_delivery_not_found(message: impl Into<String>) -> Self {
        Self::not_found(ERROR_EVENT_DELIVERY_NOT_FOUND, message)
    }

    pub fn invalid_event_filter(message: impl Into<String>) -> Self {
        Self::invalid(ERROR_INVALID_EVENT_FILTER, message)
    }

    pub fn invalid_event_scope(message: impl Into<String>) -> Self {
        Self::invalid(ERROR_INVALID_EVENT_SCOPE, message)
    }

    pub fn invalid_webhook_url(message: impl Into<String>) -> Self {
        Self::invalid(ERROR_INVALID_WEBHOOK_URL, message)
    }

    pub fn event_subscription_limit_reached(message: impl Into<String>) -> Self {
        Self::conflict(ERROR_EVENT_SUBSCRIPTION_LIMIT_REACHED, message)
    }

    pub fn event_subscription_revision_conflict(message: impl Into<String>) -> Self {
        Self::conflict(ERROR_EVENT_SUBSCRIPTION_REVISION_CONFLICT, message)
    }

    pub fn event_subscription_forbidden(message: impl Into<String>) -> Self {
        Self::forbidden_code(ERROR_EVENT_SUBSCRIPTION_FORBIDDEN, message)
    }

    pub fn event_delivery_not_replayable(message: impl Into<String>) -> Self {
        Self::conflict(ERROR_EVENT_DELIVERY_NOT_REPLAYABLE, message)
    }

    pub fn event_delivery_lane_blocked(message: impl Into<String>) -> Self {
        Self::conflict(ERROR_EVENT_DELIVERY_LANE_BLOCKED, message)
    }

    pub fn invite_code_required(message: impl Into<String>) -> Self {
        Self::forbidden_code(ERROR_INVITE_CODE_REQUIRED, message)
    }

    pub fn invite_code_already_bound(message: impl Into<String>) -> Self {
        Self::conflict(ERROR_INVITE_CODE_ALREADY_BOUND, message)
    }

    pub fn invite_code_unavailable(message: impl Into<String>) -> Self {
        Self::conflict(ERROR_INVITE_CODE_UNAVAILABLE, message)
    }

    pub fn invite_code_not_applicable(message: impl Into<String>) -> Self {
        Self::forbidden_code(ERROR_INVITE_CODE_NOT_APPLICABLE, message)
    }

    pub fn invite_code_claim_limit_reached(message: impl Into<String>) -> Self {
        Self::QuotaExceeded {
            code: ERROR_INVITE_CODE_CLAIM_LIMIT_REACHED.to_string(),
            message: message.into(),
        }
    }

    pub fn code(&self) -> &str {
        match self {
            Self::InvalidInput { code, .. }
            | Self::NotFound { code, .. }
            | Self::Conflict { code, .. }
            | Self::Gone { code, .. }
            | Self::QuotaExceeded { code, .. }
            | Self::PayloadTooLarge { code, .. }
            | Self::Unprocessable { code, .. }
            | Self::BadGateway { code, .. } => code,
            Self::Unavailable { code, .. } => code,
            Self::Unauthenticated => "unauthenticated",
            Self::Forbidden(_) => "forbidden",
            Self::ForbiddenCode { code, .. } => code,
            Self::Internal(_) => "internal_error",
        }
    }
}
