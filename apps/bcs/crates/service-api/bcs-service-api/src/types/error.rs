use strum::AsRefStr;

/// Fixed spec §11.2 error codes of the typed ownership-transfer
/// conflict branches (plan Task 14). Declared next to
/// [`TransferConflict`] so the domain error and the application's
/// fixed-code mapping can never drift apart.
pub const CODE_OWNERSHIP_TRANSFER_PENDING: &str = "ownership_transfer_pending";
/// See [`CODE_OWNERSHIP_TRANSFER_PENDING`]; the owner/version-moved code
/// (spec §11.2 `ownership_changed`).
pub const CODE_OWNERSHIP_CHANGED: &str = "ownership_changed";
/// See [`CODE_OWNERSHIP_TRANSFER_PENDING`]; the same-key-different-payload
/// idempotency code (spec §11.2 `idempotency_conflict`).
pub const CODE_IDEMPOTENCY_CONFLICT: &str = "idempotency_conflict";
/// See [`CODE_OWNERSHIP_TRANSFER_PENDING`]; the incompatible-terminal
/// code (spec §11.2 `ownership_transfer_not_pending`).
pub const CODE_OWNERSHIP_TRANSFER_NOT_PENDING: &str = "ownership_transfer_not_pending";
/// See [`CODE_OWNERSHIP_TRANSFER_PENDING`]; the lapsed-deadline code
/// (spec §11.2 `ownership_transfer_expired`).
pub const CODE_OWNERSHIP_TRANSFER_EXPIRED: &str = "ownership_transfer_expired";
/// See [`CODE_OWNERSHIP_TRANSFER_PENDING`]; the unresolvable-recipient
/// code, only produced for an already-authorized initiator
/// (spec §11.2 `invalid_transfer_recipient`).
pub const CODE_INVALID_TRANSFER_RECIPIENT: &str = "invalid_transfer_recipient";

/// Service error type.
#[derive(Debug, thiserror::Error, AsRefStr)]
#[strum(serialize_all = "snake_case")]
pub enum ServiceError {
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

    /// Strongly-typed bot authority business failure
    /// (spec §11.2, plan Task 1). Real infrastructure failures keep
    /// using the storage/internal error branches — only the enumerated
    /// business branches below are carried here, and the application
    /// layer maps them to the spec's fixed error codes.
    #[error(transparent)]
    Authority(#[from] AuthorityError),
}

/// Strongly-typed bot authority business branches (spec §5.1/§5.4/§11.2).
///
/// These are business outcomes, not infrastructure failures: decode
/// failures/DB errors must stay on `ServiceError`'s internal/storage
/// branches so they fail closed as 500, never as a mapped business
/// code. The application layer maps each branch to the spec's fixed
/// code; consumers must never match on error message strings.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum AuthorityError {
    /// The Bot's ownership has not been initialized
    /// (`ownership_version = 0`); manager/ownership APIs return
    /// 409/`ownership_not_initialized` and never auto-claim.
    #[error("bot ownership is not initialized: bot '{bot_id}' (env {env})")]
    OwnershipNotInitialized { bot_id: String, env: String },
    /// Corrupt authority data (e.g. version > 0 but no/multiple owner
    /// edge, unknown role source encoding); reported as a consistency
    /// error and denied — never treated as an ordinary permission
    /// result (spec §12.4).
    #[error("corrupt authority data: bot '{bot_id}' (env {env}): {detail}")]
    CorruptAuthority {
        bot_id: String,
        env: String,
        detail: String,
    },
    /// The authenticated caller is not allowed to perform this action
    /// on the visible resource (403/`forbidden`).
    #[error("forbidden: {0}")]
    Forbidden(String),
    /// A subject of the operation is not a resolvable legal actor for
    /// it (400; e.g. unresolvable transfer recipient for the authorized
    /// initiator, Human treated as a transfer Bot).
    #[error("invalid subject: {0}")]
    InvalidSubject(String),
    /// Business-level conflict with persisted authority state
    /// (409; e.g. pending transfer slot, idempotency conflict,
    /// already-final mutation state).
    #[error("conflict: {0}")]
    Conflict(String),
    /// The ownership transfer record does not exist, or the authenticated
    /// viewer/actor is not one of its two recorded parties — ONE branch by
    /// design (404/`ownership_transfer_not_found`, spec §11.2), so an
    /// unauthorized caller cannot learn whether a transfer id exists
    /// (anti-enumerment). Different 403 semantics exist for callers that
    /// ARE a recorded party but whose role forbids the requested action.
    #[error("ownership transfer not found: '{transfer_id}'")]
    OwnershipTransferNotFound { transfer_id: String },
    /// Typed ownership-transfer conflict (spec §11.2, plan Task 14): the
    /// store's transfer lanes return this instead of machine-worded
    /// `Conflict(String)` payloads, so consumers branch on TYPES and
    /// never parse error message strings for dispatch.
    #[error(transparent)]
    TransferConflict(TransferConflict),
}

/// Typed transfer-lane conflict branches (spec §11.2, plan Tasks 8/14).
///
/// Each variant carries its own fixed spec code and replaces one
/// documented machine wording of the former `Conflict(String)` contract:
/// `ownership_transfer_pending`, `ownership_changed`,
/// `idempotency_conflict`, `not_pending` — plus the bounded
/// optimistic-retry exhaustion outcomes, which stay 409 conflicts of the
/// `ownership_changed` family (the validated window kept being lost
/// because ownership/transfer facts kept moving under the request).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum TransferConflict {
    /// The Bot's single pending-transfer slot already holds a valid
    /// pending (409 [`CODE_OWNERSHIP_TRANSFER_PENDING`]).
    #[error("bot '{bot_id}' already has a valid pending ownership transfer")]
    PendingSlot { bot_id: String },
    /// Only the `expected_owner_version` snapshot is stale: both parties
    /// are legal and no valid pending exists, but the stored
    /// ownership/version no longer matches the request. Nothing was
    /// persisted (409 [`CODE_OWNERSHIP_CHANGED`]; the client re-reads
    /// ownership and retries with the fresh version).
    #[error(
        "ownership changed on bot '{bot_id}': expected version \
         {expected_owner_version}, current version {current_owner_version}"
    )]
    VersionSnapshotStale {
        bot_id: String,
        expected_owner_version: u64,
        current_owner_version: u64,
    },
    /// The idempotency key was already committed with a different
    /// payload (409 [`CODE_IDEMPOTENCY_CONFLICT`]).
    #[error(
        "idempotency key '{client_request_id}' on bot '{bot_id}' was \
         already used with a different payload"
    )]
    IdempotencyBody {
        bot_id: String,
        client_request_id: String,
    },
    /// The transfer row is no longer pending: a terminal row cannot be
    /// re-decided with the requested action (inclusive-action replays
    /// answer with the historical receipt BEFORE this branch; 409
    /// [`CODE_OWNERSHIP_TRANSFER_NOT_PENDING`]). The same type doubles as
    /// the store-INTERNAL drift marker when a decide attempt found the
    /// pending row decided while waiting for its window — that marker
    /// never escapes to the boundary.
    #[error("transfer '{transfer_id}' is no longer pending (status '{status}')")]
    NotPending {
        transfer_id: String,
        status: String,
    },
    /// Bounded optimistic-retry exhaustion under genuine concurrent
    /// decisions on one resource: each attempt re-validated and lost its
    /// window to a committed racer. Surface as a 409 of the
    /// [`CODE_OWNERSHIP_CHANGED`] family — the request's validated
    /// ownership facts kept being replaced before they could commit.
    #[error(
        "concurrent ownership transfer contention on '{resource}': \
         the validated window was lost repeatedly ({detail})"
    )]
    Contended { resource: String, detail: String },
}

impl AuthorityError {
    /// The fixed spec error code this branch maps to in the
    /// application layer (spec §11.2).
    pub fn fixed_code(&self) -> &'static str {
        match self {
            Self::OwnershipNotInitialized { .. } => "ownership_not_initialized",
            Self::CorruptAuthority { .. } => "corrupt_authority",
            Self::Forbidden(_) => "forbidden",
            Self::InvalidSubject(_) => "invalid_subject",
            Self::Conflict(_) => "conflict",
            Self::OwnershipTransferNotFound { .. } => "ownership_transfer_not_found",
            Self::TransferConflict(conflict) => match conflict {
                TransferConflict::PendingSlot { .. } => CODE_OWNERSHIP_TRANSFER_PENDING,
                TransferConflict::VersionSnapshotStale { .. }
                | TransferConflict::Contended { .. } => CODE_OWNERSHIP_CHANGED,
                TransferConflict::IdempotencyBody { .. } => CODE_IDEMPOTENCY_CONFLICT,
                TransferConflict::NotPending { .. } => CODE_OWNERSHIP_TRANSFER_NOT_PENDING,
            },
        }
    }
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
            Self::Authority(err) => {
                match err {
                    AuthorityError::OwnershipNotInitialized {
                        bot_id,
                        env,
                    }
                    | AuthorityError::CorruptAuthority {
                        bot_id,
                        env,
                        ..
                    } => serde_json::json!({
                        "bot_id": bot_id,
                        "env": env,
                    }),
                    AuthorityError::Forbidden(reason)
                    | AuthorityError::InvalidSubject(reason)
                    | AuthorityError::Conflict(reason) => {
                        serde_json::json!({ "reason": reason })
                    }
                    AuthorityError::OwnershipTransferNotFound { transfer_id } => {
                        serde_json::json!({ "transfer_id": transfer_id })
                    }
                    AuthorityError::TransferConflict(conflict) => match conflict {
                        TransferConflict::PendingSlot { bot_id }
                        | TransferConflict::Contended { resource: bot_id, .. } => {
                            serde_json::json!({ "bot_id": bot_id })
                        }
                        TransferConflict::VersionSnapshotStale {
                            bot_id,
                            expected_owner_version,
                            current_owner_version,
                        } => serde_json::json!({
                            "bot_id": bot_id,
                            "expected_owner_version": expected_owner_version,
                            "current_owner_version": current_owner_version,
                        }),
                        TransferConflict::IdempotencyBody {
                            bot_id,
                            client_request_id,
                        } => serde_json::json!({
                            "bot_id": bot_id,
                            "client_request_id": client_request_id,
                        }),
                        TransferConflict::NotPending {
                            transfer_id, ..
                        } => serde_json::json!({ "transfer_id": transfer_id }),
                    },
                }
            }
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
