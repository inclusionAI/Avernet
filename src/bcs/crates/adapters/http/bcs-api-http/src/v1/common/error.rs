use axum::Json;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use bcs_service_api::application::v1::ApplicationError;

use super::{Envelope, ErrorData, RequestId};

pub struct ErrorResponse {
    status: StatusCode,
    code: u32,
    error_code: String,
    message: String,
    request_id: String,
}

impl ErrorResponse {
    pub fn unauthenticated(request_id: impl Into<String>) -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            code: 40_100,
            error_code: "unauthenticated".to_string(),
            message: "Authentication is required".to_string(),
            request_id: request_id.into(),
        }
    }
}

impl IntoResponse for ErrorResponse {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(Envelope {
                code: self.code,
                message: self.message,
                data: ErrorData {
                    error_code: self.error_code,
                },
                request_id: self.request_id,
            }),
        )
            .into_response()
    }
}

pub fn invalid_request(request_id: &RequestId, message: impl Into<String>) -> ErrorResponse {
    ErrorResponse {
        status: StatusCode::BAD_REQUEST,
        code: 40_000,
        error_code: "invalid_request".to_string(),
        message: message.into(),
        request_id: request_id.0.clone(),
    }
}

pub fn application_error_response(
    request_id: &RequestId,
    error: ApplicationError,
) -> ErrorResponse {
    let (status, code, error_code, message) = match error {
        ApplicationError::InvalidInput { code, message } => {
            (StatusCode::BAD_REQUEST, 40_000, code, message)
        }
        ApplicationError::Unauthenticated => (
            StatusCode::UNAUTHORIZED,
            40_100,
            "unauthenticated".to_string(),
            "Authentication is required".to_string(),
        ),
        ApplicationError::Forbidden(message) => (
            StatusCode::FORBIDDEN,
            40_300,
            "forbidden".to_string(),
            message,
        ),
        ApplicationError::NotFound { code, message } => {
            (StatusCode::NOT_FOUND, 40_400, code, message)
        }
        ApplicationError::Conflict { code, message } => {
            (StatusCode::CONFLICT, 40_900, code, message)
        }
        ApplicationError::Gone { code, message } => (StatusCode::GONE, 41_000, code, message),
        ApplicationError::QuotaExceeded { code, message } => {
            (StatusCode::TOO_MANY_REQUESTS, 42_900, code, message)
        }
        ApplicationError::Internal(_) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            50_000,
            "internal_error".to_string(),
            "Internal server error".to_string(),
        ),
    };
    ErrorResponse {
        status,
        code,
        error_code,
        message,
        request_id: request_id.0.clone(),
    }
}
