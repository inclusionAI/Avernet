//! Immutable collaboration authoring revisions and their independent read grants.
use serde::{Deserialize, Serialize};

pub const MAX_DRAFT_BYTES: usize = 65_536;
pub const MAX_DRAFT_REVISION: u64 = 9_007_199_254_740_991;

#[derive(Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DraftContent {
    pub definition_yaml: String,
    pub title: Option<String>,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct DraftMetadata {
    pub draft_id: String,
    pub owner_bot_id: String,
    pub head_revision: u64,
    pub created_at: String,
    pub deleted: bool,
}

#[derive(Clone, Serialize, Deserialize)]
pub struct DraftRevision {
    pub draft_id: String,
    pub revision: u64,
    pub definition_yaml: String,
    pub title: Option<String>,
    pub content_hash: String,
    pub size_bytes: usize,
    pub created_by: String,
    pub created_at: String,
}

// Deliberately no Debug: neither raw content nor grant material belongs in logs.
#[derive(Clone, Serialize, Deserialize)]
pub struct StoredDraftRevision {
    pub content: DraftRevision,
    /// Random capability persisted with this exact revision; never a deployment key.
    pub read_token: String,
    pub revoked: bool,
}

#[derive(Clone)]
pub enum DraftAccess {
    Owner(String),
    ReadToken(String),
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DraftReference {
    pub schema_version: u8,
    pub draft_id: String,
    pub revision: u64,
    pub read_token: String,
}

pub struct SavedDraftRevision {
    pub content: DraftRevision,
    pub created: bool,
}
