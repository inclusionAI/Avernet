//! baas v1.1 error code -> `StorageError`. baas error body:
//! `{"detail":{"error":"<CODE>","message":"...", ...optional}}`.

use bcs_storage_api::StorageError;

/// Map a baas error code (from response `detail.error`) + the HTTP status
/// to a `StorageError`. See design-baas-plugin §「错误映射」.
pub fn map_baas_error(code: &str, status: u16, detail_msg: &str) -> StorageError {
    match code {
        "TRANSFER_NOT_FOUND" | "SOURCE_TRANSFER_NOT_FOUND" => StorageError::NotFound,
        "SOURCE_TRANSFER_NOT_READY"
        | "TRANSFER_STATE_CONFLICT"
        | "TRANSFER_NOT_TERMINAL"
        | "OSS_OBJECT_NOT_FOUND"
        | "INVALID_TRANSITION" => StorageError::Conflict(format!("{code}: {detail_msg}")),
        "NOT_IMPLEMENTED" => StorageError::Unsupported("baas"),
        _ => StorageError::Backend(anyhow::anyhow!(
            "baas error {status} {code}: {detail_msg}"
        )),
    }
}

/// Delete (DELETE .../transfers/{transfer_id}) idempotent success: a 409
/// TRANSFER_NOT_TERMINAL is a real conflict; a 404 / already-DELETED is Ok.
pub fn is_delete_idempotent_ok(code: Option<&str>) -> bool {
    matches!(code, Some("TRANSFER_NOT_FOUND")) // transfer gone already
}