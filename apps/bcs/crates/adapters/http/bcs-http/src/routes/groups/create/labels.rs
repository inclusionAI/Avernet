//! Preserve legacy parsing errors except for field-specific catalog-label type errors.
use super::*;
use std::error::Error;

pub(super) fn rejection_response(rejection: JsonRejection) -> Response {
    if let JsonRejection::JsonDataError(error) = &rejection {
        let mut source: &(dyn Error + 'static) = error;
        loop {
            if let Some(error) = source.downcast_ref::<serde_path_to_error::Error<serde_json::Error>>() {
                if matches!(error.path().iter().next(),
                    Some(serde_path_to_error::Segment::Map { key }) if key == "catalog_labels") {
                    return HttpAdapterError::BadRequestStructured {
                        message: "invalid catalog_labels type".into(),
                        params: serde_json::json!({
                            "code": "invalid_request",
                            "field": error.path().to_string(),
                            "reason": "INVALID_TYPE",
                        }),
                    }.into_response();
                }
                break;
            }
            match source.source() {
                Some(next) => source = next,
                None => break,
            }
        }
    }
    // Keep syntax, size, content-type and unrelated DTO error behavior unchanged.
    rejection.into_response()
}
