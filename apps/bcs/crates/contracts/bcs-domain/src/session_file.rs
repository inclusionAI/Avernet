//! Session workspace file domain types.

use serde::{Deserialize, Serialize};

use crate::actor::ActorRef;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "PascalCase")]
pub enum FileStatus {
    #[default]
    Pending,
    Ready,
    Deleting,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionFile {
    pub file_id: String,
    pub session_id: String,
    pub file_name: String,
    pub mime_type: String,
    pub size: u64,
    /// v1 always None (integrity not verified); backends may populate later.
    pub sha256: Option<String>,
    pub owner: ActorRef,
    pub storage_backend: String,
    /// Serialized `UploadHandle` (Pending) or `StorageHandle` (Ready).
    /// Opaque to clients; never returned over the wire.
    pub object_handle: String,
    pub status: FileStatus,
    pub created_at: u64,
    pub updated_at: u64,
}

/// Allocate a globally-unique file id (ULID, 26-char Crockford base32).
pub fn new_file_id() -> String {
    ulid::Ulid::new().to_string()
}