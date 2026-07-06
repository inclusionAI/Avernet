use serde::{Deserialize, Serialize};

/// Request body for creating a friend request.
#[derive(Debug, Serialize, Deserialize)]
pub struct CreateFriendRequestBody {
    /// Caller bot UUID (fallback when no Bearer token is provided).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub from_bot: Option<String>,
    /// Target bot UUID to send the friend request to.
    pub to_bot: String,
}

/// Query parameters for listing friend requests.
#[derive(Debug, Serialize, Deserialize)]
pub struct ListFriendRequestsQuery {
    /// Caller bot UUID (fallback when no Bearer token is provided).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bot_uuid: Option<String>,
    /// Direction filter: "received" (default), "sent", or "all".
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub direction: Option<String>,
    /// Status filter: "pending", "accepted", or "rejected". None returns all.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
}

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
#[derive(Debug, Deserialize)]
pub struct FriendApiResponse {
    pub success: bool,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub message: Option<String>,
    #[serde(default)]
    pub data: Option<serde_json::Value>,
}
