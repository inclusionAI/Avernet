use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::group::{DeleteResult, Page};
use super::{ApplicationError, AuthenticatedCaller};

/// Reuse the domain friend-request status vocabulary.
///
/// The domain enum already serializes as the V1 wire form (`pending`,
/// `accepted`, `rejected`), so the V1 contract re-exports it directly instead
/// of defining a narrower copy.
pub use bcs_domain::FriendRequestStatus;

/// V1-narrowed friend request direction filter.
///
/// The domain `FriendRequestDirection` includes an `All` variant for internal
/// use; V1 only exposes `Sent` and `Received` to clients, mirroring
/// `FriendRequestDirection` in the V1 domain model.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FriendRequestDirection {
    Sent,
    Received,
}

impl Default for FriendRequestDirection {
    fn default() -> Self {
        Self::Received
    }
}

/// V1 projection of a friendship edge.
///
/// `bot_uuid` is the bot whose friendship list was queried; `friend_bot_uuid`
/// is the peer. This is a symmetric relationship.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Friendship {
    pub bot_uuid: String,
    pub friend_bot_uuid: String,
    pub created_at: u64,
}

/// V1 projection of a friend request.
///
/// Unlike the domain `FriendRequest`, the V1 projection renames fields to the
/// wire form (`request_id`, `from_bot_uuid`, `to_bot_uuid`) and exposes an
/// optional `message` carried from the original request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FriendRequest {
    pub request_id: String,
    pub from_bot_uuid: String,
    pub to_bot_uuid: String,
    pub status: FriendRequestStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    pub created_at: u64,
    pub updated_at: u64,
}

/// List a bot's friendships, ordered by `created_at` descending.
#[derive(Debug, Clone)]
pub struct ListBotFriendships {
    pub caller: AuthenticatedCaller,
    pub bot_uuid: String,
    pub offset: u64,
    pub limit: u64,
}

/// Remove a friendship symmetrically and idempotently.
#[derive(Debug, Clone)]
pub struct DeleteBotFriendship {
    pub caller: AuthenticatedCaller,
    pub bot_uuid: String,
    pub friend_bot_uuid: String,
}

/// Send a friend request from `bot_uuid` (the path target whose identity is
/// used) to `to_bot_uuid` (the receiver).
#[derive(Debug, Clone)]
pub struct CreateBotFriendRequest {
    pub caller: AuthenticatedCaller,
    pub bot_uuid: String,
    pub to_bot_uuid: String,
}

/// List friend requests sent by or received by `bot_uuid`.
#[derive(Debug, Clone)]
pub struct ListBotFriendRequests {
    pub caller: AuthenticatedCaller,
    pub bot_uuid: String,
    pub direction: FriendRequestDirection,
    pub status: Option<FriendRequestStatus>,
    pub offset: u64,
    pub limit: u64,
}

/// Accept a friend request as the receiver; idempotent after acceptance.
#[derive(Debug, Clone)]
pub struct AcceptFriendRequest {
    pub caller: AuthenticatedCaller,
    pub request_id: String,
}

/// Reject a friend request as the receiver; idempotent after rejection.
#[derive(Debug, Clone)]
pub struct RejectFriendRequest {
    pub caller: AuthenticatedCaller,
    pub request_id: String,
}

/// Transport-independent friendship use cases for BCN OpenAPI v1.
///
/// Delivery adapters translate HTTP requests into these commands. The trait is
/// object-safe so an `Arc<dyn FriendshipService>` can be shared across routes.
#[async_trait]
pub trait FriendshipService: Send + Sync {
    async fn list_bot_friendships(
        &self,
        command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError>;

    async fn delete_bot_friendship(
        &self,
        command: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError>;

    async fn create_bot_friend_request(
        &self,
        command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError>;

    async fn list_bot_friend_requests(
        &self,
        command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError>;

    async fn accept_friend_request(
        &self,
        command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError>;

    async fn reject_friend_request(
        &self,
        command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError>;
}
