use super::*;

    #[test]
    fn internal_attribute_application_errors_keep_their_http_status() {
        let cases = vec![
            (
                ApplicationError::invalid("invalid", "invalid"),
                StatusCode::BAD_REQUEST,
            ),
            (ApplicationError::Unauthenticated, StatusCode::UNAUTHORIZED),
            (
                ApplicationError::forbidden("forbidden"),
                StatusCode::FORBIDDEN,
            ),
            (
                ApplicationError::not_found("missing", "missing"),
                StatusCode::NOT_FOUND,
            ),
            (
                ApplicationError::conflict("conflict", "conflict"),
                StatusCode::CONFLICT,
            ),
            (
                ApplicationError::Gone {
                    code: "gone".into(),
                    message: "gone".into(),
                },
                StatusCode::GONE,
            ),
            (
                ApplicationError::QuotaExceeded {
                    code: "quota".into(),
                    message: "quota".into(),
                },
                StatusCode::TOO_MANY_REQUESTS,
            ),
            (
                ApplicationError::payload_too_large("large", "large"),
                StatusCode::PAYLOAD_TOO_LARGE,
            ),
            (
                ApplicationError::unprocessable("unprocessable", "unprocessable"),
                StatusCode::UNPROCESSABLE_ENTITY,
            ),
            (
                ApplicationError::bad_gateway("upstream", "upstream"),
                StatusCode::BAD_GATEWAY,
            ),
            (
                ApplicationError::internal("internal"),
                StatusCode::INTERNAL_SERVER_ERROR,
            ),
        ];

        for (error, expected_status) in cases {
            assert_eq!(internal_attributes_error(error).status, expected_status);
        }
    }
