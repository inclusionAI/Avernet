use strum::AsRefStr;

/// Service error type.
#[derive(Debug, thiserror::Error, AsRefStr)]
#[strum(serialize_all = "snake_case")]
pub enum ServiceError {
    /// Bot not found.
    #[error("Bot '{0}' not found")]
    BotNotFound(String),

    /// Bot not registered.
    #[error("Bot '{0}' is not registered")]
    BotNotRegistered(String),

    /// Bot is registered but currently has no active runtime connection.
    #[error("Bot '{0}' is not connected")]
    BotNotConnected(String),

    /// Bot is hidden and not accepting communication.
    #[error("Bot '{0}' is not collaborative")]
    BotHidden(String),

    /// Group not found.
    #[error("Group '{0}' not found")]
    GroupNotFound(String),

    /// Proposal not found.
    #[error("Proposal '{0}' not found or expired")]
    ProposalNotFound(String),

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

    /// Session reactivation blocked because callback is still pending.
    #[error("Session '{0}' callback still pending")]
    SessionCallbackPending(String),

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
            Self::BotNotFound(id)
            | Self::BotNotRegistered(id)
            | Self::BotNotConnected(id)
            | Self::BotHidden(id) => {
                serde_json::json!({ "bot_id": id })
            }
            Self::GroupNotFound(id) => serde_json::json!({ "group_id": id }),
            Self::ProposalNotFound(id) => serde_json::json!({ "proposal_id": id }),
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
            Self::SessionCallbackPending(id) => serde_json::json!({ "session_id": id }),
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
            | Self::PrivateBotCannotCollaborate => serde_json::Value::Null,
        }
    }
}
