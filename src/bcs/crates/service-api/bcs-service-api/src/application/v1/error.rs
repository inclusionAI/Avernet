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
    NotFound { code: String, message: String },
    #[error("{code}: {message}")]
    Conflict { code: String, message: String },
    #[error("{code}: {message}")]
    Gone { code: String, message: String },
    #[error("{code}: {message}")]
    QuotaExceeded { code: String, message: String },
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

    pub fn internal(message: impl Into<String>) -> Self {
        Self::Internal(message.into())
    }

    pub fn code(&self) -> &str {
        match self {
            Self::InvalidInput { code, .. }
            | Self::NotFound { code, .. }
            | Self::Conflict { code, .. }
            | Self::Gone { code, .. }
            | Self::QuotaExceeded { code, .. } => code,
            Self::Unauthenticated => "unauthenticated",
            Self::Forbidden(_) => "forbidden",
            Self::Internal(_) => "internal_error",
        }
    }
}
