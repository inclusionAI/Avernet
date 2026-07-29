use bcs_storage_api::StorageError;
use bcs_storage_baas::error::map_baas_error;

#[test]
fn error_code_map_table() {
    let cases: &[(&str, u16, &str, &str)] = &[
        ("TRANSFER_NOT_FOUND", 404, "m", "NotFound"),
        ("SOURCE_TRANSFER_NOT_FOUND", 404, "m", "NotFound"),
        ("SOURCE_TRANSFER_NOT_READY", 409, "m", "Conflict"),
        ("TRANSFER_STATE_CONFLICT", 409, "m", "Conflict"),
        ("TRANSFER_NOT_TERMINAL", 409, "m", "Conflict"),
        ("OSS_OBJECT_NOT_FOUND", 409, "m", "Conflict"),
        ("INVALID_TRANSITION", 422, "m", "Conflict"),
        ("NOT_IMPLEMENTED", 501, "m", "Unsupported"),
        ("INTERNAL_ERROR", 500, "m", "Backend"),
        ("UNKNOWN_NEW_CODE", 500, "m", "Backend"),
    ];
    for (code, status, msg, expect) in cases {
        let e = map_baas_error(code, *status, msg);
        let got = match e {
            StorageError::NotFound => "NotFound",
            StorageError::Conflict(_) => "Conflict",
            StorageError::Unsupported(_) => "Unsupported",
            StorageError::Backend(_) => "Backend",
            StorageError::InvalidInput(_) => "InvalidInput",
        };
        assert_eq!(got, *expect, "code {code} expected {expect} got {got}");
    }
}