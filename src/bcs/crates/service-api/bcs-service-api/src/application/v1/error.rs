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

/// Transport-independent error vocabulary for OpenAPI v1 use cases.
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
            Self::Unauthenticated => "unauthenticated",
            Self::Forbidden(_) => "forbidden",
            Self::ForbiddenCode { code, .. } => code,
            Self::Internal(_) => "internal_error",
        }
    }
}
