//! Wire types for internal friend connection routes (edge-permission model).
//!
//! New-model DTOs (edge-permission reform) live here. The legacy
//! `FriendApiResponse`/`FriendEntry` types are retained for the existing
//! `bcs-cli` client contract; they are retired in Phase 5.
use serde::{Deserialize, Serialize};

/// Request body for creating a friend (connect) request.
/// Old `/friends/*` path uses `from_bot`; `/collaboration/friend-connections/*` uses
/// `from_actor` + `actor_kind` as an authenticated act-as target. Both fields
/// coexist for backward compatibility with old clients.
#[derive(Debug, Clone, Deserialize)]
pub struct CreateFriendRequestBody {
    /// Old path: caller bot UUID (fallback when no Bearer). Used by bcs-cli.
    #[serde(default)]
    pub from_bot: Option<String>,
    /// friend-connections path: act-as actor id (raw staff_no or bot_uuid).
    #[serde(default)]
    pub from_actor: Option<String>,
    /// friend-connections path: actor kind: "human" → prefix `human_`, "bot" → use as-is.
    #[serde(default)]
    pub actor_kind: Option<String>,
    pub to_bot: String,
    #[serde(default)]
    pub message: Option<String>,
}

/// `POST /friends/request` response.
#[derive(Debug, Clone, Serialize)]
pub struct CreateFriendRequestResponse {
    pub request_ids: Vec<String>,
    /// "pending" | "approved" | "public_no_edge"
    pub status: String,
    pub edge_ids: Vec<u64>,
    pub auto_accepted: bool,
}

/// `POST /friends/requests/{id}/accept` response.
#[derive(Debug, Clone, Serialize)]
pub struct AcceptFriendRequestResponse {
    pub edge_ids: Vec<u64>,
}

/// Body for reject/cancel/revoke decision endpoints.
#[derive(Debug, Clone, Deserialize)]
pub struct DecisionBody {
    #[serde(default)]
    pub reason: Option<String>,
}

/// `POST .../reject` / `.../cancel` response.
#[derive(Debug, Clone, Serialize)]
pub struct StatusResponse {
    /// "rejected" | "cancelled"
    pub status: String,
}

/// Query parameters for `GET /friends/requests`.
#[derive(Debug, Clone, Deserialize)]
pub struct ListRequestsQuery {
    #[serde(default = "default_direction")]
    pub direction: String,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default = "default_page")]
    pub page: u32,
    #[serde(default = "default_page_size")]
    pub page_size: u32,
    /// `bcs-cli` echoes the caller's `bot_uuid` on this endpoint; we ignore
    /// it (caller identity comes from auth headers) but accept the param so
    /// the query deserializes without error.
    #[serde(default)]
    pub bot_uuid: Option<String>,
}
fn default_direction() -> String {
    "received".into()
}
fn default_page() -> u32 {
    1
}
fn default_page_size() -> u32 {
    20
}

/// `POST /friends/{actor}/revoke` response.
#[derive(Debug, Clone, Serialize)]
pub struct RevokeFriendResponse {
    pub revoked_edges: Vec<u64>,
}

/// `GET /bots/{id}/friends` response.
#[derive(Debug, Clone, Serialize)]
pub struct FriendListResponse {
    pub items: Vec<bcs_domain::edge_permission::FriendListEntry>,
    pub total: u32,
}

/// `GET /friends?actor=` query (friend list for any actor — human or bot).
#[derive(Debug, Clone, Deserialize)]
pub struct FriendListByActorQuery {
    pub actor: String,
    /// Actor kind: "human" → prefix `human_`, "bot" → use as-is. Optional.
    #[serde(default)]
    pub actor_kind: Option<String>,
}

// ---------------------------------------------------------------------------
// Legacy wire types — retained for the existing `bcs-cli` client contract.
// Retired in Phase 5 alongside the old friend graph.
// ---------------------------------------------------------------------------

/// A friend entry in the friend list response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FriendEntry {
    /// Friend's bot UUID.
    pub bot_uuid: String,
    /// Friend's display name.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// Friend's summary.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
    /// Whether the friend is currently connected via streaming transport.
    pub is_online: bool,
    /// Dynamic online status matching `actors/list` semantics.
    pub dynamic_status: super::bots::DynamicStatusResponse,
}

/// Response from friend-related API calls.
#[derive(Debug, Serialize, Deserialize)]
pub struct FriendApiResponse {
    pub success: bool,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub data: Option<serde_json::Value>,
}

/// Wrap a flat DTO in the legacy `{success, data}` envelope that `bcs-cli`
/// still deserializes (`crates/tools/bcs-cli/src/client.rs` reads
/// `FriendApiResponse`). Kept until Phase 5 retires the old client contract.
pub fn envelope<T: Serialize>(payload: &T) -> FriendApiResponse {
    FriendApiResponse {
        success: true,
        error: None,
        message: None,
        data: Some(serde_json::to_value(payload).unwrap_or_default()),
    }
}
