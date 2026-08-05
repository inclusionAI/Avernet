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
        ApplicationError::Internal(detail) => {
            tracing::error!(
                request_id = %request_id.0,
                error = %detail,
                "OpenAPI V1 request failed"
            );
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                50_000,
                "internal_error".to_string(),
                "Internal server error".to_string(),
            )
        }
    };
    ErrorResponse {
        status,
        code,
        error_code,
        message,
        request_id: request_id.0.clone(),
    }
}

#[cfg(test)]
mod tests {
    use std::io::{self, Write};
    use std::sync::{Arc, Mutex};

    use super::*;

    #[derive(Clone, Default)]
    struct SharedLogBuffer(Arc<Mutex<Vec<u8>>>);

    struct SharedLogWriter(Arc<Mutex<Vec<u8>>>);

    impl Write for SharedLogWriter {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            self.0
                .lock()
                .expect("log buffer lock")
                .extend_from_slice(buf);
            Ok(buf.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedLogBuffer {
        type Writer = SharedLogWriter;

        fn make_writer(&'a self) -> Self::Writer {
            SharedLogWriter(self.0.clone())
        }
    }

    #[test]
    fn maps_application_errors_to_the_v1_http_contract() {
        let cases = [
            (
                ApplicationError::invalid("invalid_group", "invalid group"),
                StatusCode::BAD_REQUEST,
                40_000,
                "invalid_group",
                "invalid group",
            ),
            (
                ApplicationError::Unauthenticated,
                StatusCode::UNAUTHORIZED,
                40_100,
                "unauthenticated",
                "Authentication is required",
            ),
            (
                ApplicationError::forbidden("access denied"),
                StatusCode::FORBIDDEN,
                40_300,
                "forbidden",
                "access denied",
            ),
            (
                ApplicationError::not_found("group_not_found", "group not found"),
                StatusCode::NOT_FOUND,
                40_400,
                "group_not_found",
                "group not found",
            ),
            (
                ApplicationError::conflict("group_exists", "group exists"),
                StatusCode::CONFLICT,
                40_900,
                "group_exists",
                "group exists",
            ),
            (
                ApplicationError::Gone {
                    code: "group_gone".to_string(),
                    message: "group is gone".to_string(),
                },
                StatusCode::GONE,
                41_000,
                "group_gone",
                "group is gone",
            ),
            (
                ApplicationError::QuotaExceeded {
                    code: "group_quota_exceeded".to_string(),
                    message: "group quota exceeded".to_string(),
                },
                StatusCode::TOO_MANY_REQUESTS,
                42_900,
                "group_quota_exceeded",
                "group quota exceeded",
            ),
            (
                ApplicationError::internal("database credentials leaked"),
                StatusCode::INTERNAL_SERVER_ERROR,
                50_000,
                "internal_error",
                "Internal server error",
            ),
        ];
        let request_id = RequestId("request-123".to_string());

        for (error, status, code, error_code, message) in cases {
            let response = application_error_response(&request_id, error);

            assert_eq!(response.status, status);
            assert_eq!(response.code, code);
            assert_eq!(response.error_code, error_code);
            assert_eq!(response.message, message);
            assert_eq!(response.request_id, request_id.0);
        }
    }

    #[test]
    fn logs_internal_detail_with_request_id_before_redacting_the_response() {
        let buffer = SharedLogBuffer::default();
        let subscriber = tracing_subscriber::fmt()
            .with_ansi(false)
            .with_level(false)
            .with_target(false)
            .with_writer(buffer.clone())
            .finish();
        let request_id = RequestId("request-sensitive-123".to_string());

        let response = tracing::subscriber::with_default(subscriber, || {
            application_error_response(
                &request_id,
                ApplicationError::internal("database connection reset"),
            )
        });
        let logs = String::from_utf8(buffer.0.lock().expect("log buffer lock").clone())
            .expect("UTF-8 logs");

        assert!(logs.contains("database connection reset"));
        assert!(logs.contains("request-sensitive-123"));
        assert_eq!(response.message, "Internal server error");
        assert!(!response.message.contains("database connection reset"));
    }
}
