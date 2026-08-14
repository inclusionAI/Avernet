//! Backend-handle serde types persisted inside `UploadHandle.backend_handle`.
//! Pending: the durable locator (`transfer_id`, `type`, `expires_at`).
//! Ready: slimmed to `transfer_id` only after complete.

use serde::{Deserialize, Serialize};

/// backend_handle persisted while Pending: only the durable locator.
/// type is DIAG-only (baas doesn't need it on subsequent calls).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BaasPendingHandle {
    pub transfer_id: String,
    #[serde(rename = "type")]
    pub transfer_type: String, // "SINGLE" | "MULTIPART"
    pub expires_at: u64,
}

/// backend_handle after complete (Ready): slimmed to transfer_id only.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BaasReadyHandle {
    pub transfer_id: String,
}