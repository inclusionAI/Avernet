use axum::{
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use bcs_service_api::ServiceError;
use strum::AsRefStr;

#[derive(Debug, thiserror::Error, AsRefStr)]
#[strum(serialize_all = "snake_case")]
pub enum HttpAdapterError {
    /// Bad request with structured params payload for frontend i18n sub-classification.
    #[error("{message}")]
    #[strum(serialize = "bad_request")]
    BadRequestStructured {
        message: String,
        params: serde_json::Value,
    },

    #[error(transparent)]
    #[strum(serialize = "_delegated")] // Never used directly — resolved_code() delegates to inner ServiceError
    Service(#[from] ServiceError),

    #[error("Invalid request: {0}")]
    BadRequest(String),            // → "bad_request"

    #[error("{0}")]
    Unauthorized(String),          // → "unauthorized"

    #[error("{0}")]
    Forbidden(String),             // → "forbidden"

    #[error("{0}")]
    Conflict(String),              // → "conflict"

    #[error("{0}")]
    NotFound(String),              // → "not_found"

    #[error("{0}")]
    Gone(String),                  // → "gone" (invite link expiry)
}

impl HttpAdapterError {
    fn status(&self) -> StatusCode {
        match self {
            Self::Service(ServiceError::GroupNotFound(_)) => StatusCode::NOT_FOUND,
            Self::Service(ServiceError::BotNotFound(_)) => StatusCode::NOT_FOUND,
            Self::Service(ServiceError::ParticipantNotFound(_)) => StatusCode::NOT_FOUND,
            Self::Service(ServiceError::Unauthorized(_)) => StatusCode::UNAUTHORIZED,
            Self::Service(ServiceError::Forbidden(_)) => StatusCode::FORBIDDEN,
            Self::Service(ServiceError::NotFriends(_)) => StatusCode::FORBIDDEN,
            Self::Service(ServiceError::PrivateBotCannotCollaborate) => StatusCode::FORBIDDEN,
            Self::Service(ServiceError::BotHidden(_)) => StatusCode::FORBIDDEN,
            Self::Service(ServiceError::Conflict(_)) => StatusCode::CONFLICT,
            Self::Service(ServiceError::BotAlreadyBound { .. }) => StatusCode::CONFLICT,
            Self::Service(ServiceError::ProviderNotReadyForDownlink { .. }) => StatusCode::CONFLICT,
            Self::Service(ServiceError::InvalidOperation { .. }) => StatusCode::BAD_REQUEST,
            Self::Service(ServiceError::CannotAddSelf) => StatusCode::BAD_REQUEST,
            Self::Service(ServiceError::CannotAcceptRejected) => StatusCode::BAD_REQUEST,
            Self::Service(ServiceError::CannotRejectAccepted) => StatusCode::BAD_REQUEST,
            Self::Service(ServiceError::SessionInvalidParams(_)) => StatusCode::BAD_REQUEST,
            Self::Service(ServiceError::MessageLimitReached(_)) => StatusCode::TOO_MANY_REQUESTS,
            Self::Service(ServiceError::ProviderNotFound(_)) => StatusCode::NOT_FOUND,
            Self::Service(ServiceError::ProposalNotFound(_)) => StatusCode::NOT_FOUND,
            Self::Service(ServiceError::FriendRequestNotFound(_)) => StatusCode::NOT_FOUND,
            Self::Service(ServiceError::BotNotRegistered(_)) => StatusCode::NOT_FOUND,
            Self::Service(ServiceError::BotNotConnected(_)) => StatusCode::SERVICE_UNAVAILABLE,
            Self::Service(ServiceError::SessionNotFound(_)) => StatusCode::NOT_FOUND,
            Self::Service(ServiceError::SessionCallbackPending(_)) => StatusCode::CONFLICT,
            Self::Service(ServiceError::PendingRequestExists { .. }) => StatusCode::CONFLICT,
            Self::BadRequestStructured { .. } => StatusCode::BAD_REQUEST,
            Self::Service(_) => StatusCode::INTERNAL_SERVER_ERROR,
            Self::BadRequest(_) => StatusCode::BAD_REQUEST,
            Self::Unauthorized(_) => StatusCode::UNAUTHORIZED,
            Self::Forbidden(_) => StatusCode::FORBIDDEN,
            Self::Conflict(_) => StatusCode::CONFLICT,
            Self::NotFound(_) => StatusCode::NOT_FOUND,
            Self::Gone(_) => StatusCode::GONE,
        }
    }

    /// Returns the effective error code, delegating through to ServiceError
    /// when the variant wraps it, or using the strum-derived code otherwise.
    fn resolved_code(&self) -> &str {
        match self {
            Self::Service(e) => e.as_ref(),
            other => {
                let code = other.as_ref();
                debug_assert!(
                    code != "_delegated",
                    "unexpected _delegated code on non-Service variant"
                );
                code
            }
        }
    }

    /// Returns dynamic parameters for frontend i18n interpolation.
    fn resolved_params(&self) -> serde_json::Value {
        match self {
            Self::Service(e) => e.error_params(),
            Self::BadRequestStructured { params, .. } => params.clone(),
            Self::BadRequest(reason)
            | Self::Unauthorized(reason)
            | Self::Forbidden(reason)
            | Self::Conflict(reason)
            | Self::NotFound(reason)
            | Self::Gone(reason) => {
                serde_json::json!({ "reason": reason })
            }
        }
    }
}

impl IntoResponse for HttpAdapterError {
    fn into_response(self) -> Response {
        let status = self.status();
        let message = self.to_string();
        let body = serde_json::json!({
            "status": status.as_u16(),
            "code": self.resolved_code(),
            "params": self.resolved_params(),
            "message": &message,
            "error": &message,
        });
        (status, Json(body)).into_response()
    }
}
