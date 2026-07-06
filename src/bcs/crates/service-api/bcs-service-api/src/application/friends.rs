//! Friend use-case contracts shared by delivery adapters and services.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::core::{
    DynamicStatusResponse, FriendRequest, FriendRequestDirection, FriendRequestStatus, ServiceError,
};

/// Create a friend request from the resolved caller to `to_bot`.
#[derive(Debug, Clone)]
pub struct CreateFriendRequestCommand {
    pub caller_actor_id: String,
    pub to_bot: String,
}

/// List friend requests for the resolved caller.
#[derive(Debug, Clone)]
pub struct ListFriendRequestsCommand {
    pub caller_actor_id: String,
    pub direction: FriendRequestDirection,
    pub status_filter: Option<FriendRequestStatus>,
}

/// Accept or reject a pending friend request as the request receiver.
#[derive(Debug, Clone)]
pub struct FriendRequestDecisionCommand {
    pub caller_actor_id: String,
    pub request_id: String,
    pub request_to_bot: Option<String>,
}

/// List friends of a target actor after access checks.
#[derive(Debug, Clone)]
pub struct ListFriendsCommand {
    pub caller_actor_id: String,
    pub target_actor_id: String,
}

/// Enriched friend list entry returned by `/bots/{id}/friends`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FriendListEntry {
    pub bot_uuid: String,
    pub name: Option<String>,
    pub summary: Option<String>,
    pub is_online: bool,
    pub dynamic_status: DynamicStatusResponse,
}

/// Use-case level error with enough detail for delivery adapters to map status.
#[derive(Debug)]
pub enum FriendUseCaseError {
    Forbidden(String),
    Service(ServiceError),
}

impl FriendUseCaseError {
    pub fn service(error: ServiceError) -> Self {
        Self::Service(error)
    }
}

#[async_trait]
pub trait FriendService: Send + Sync {
    async fn create_friend_request(
        &self,
        command: CreateFriendRequestCommand,
    ) -> Result<FriendRequest, FriendUseCaseError>;

    async fn list_friend_requests(
        &self,
        command: ListFriendRequestsCommand,
    ) -> Result<Vec<FriendRequest>, FriendUseCaseError>;

    async fn accept_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError>;

    async fn reject_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError>;

    async fn friend_request_receiver(&self, request_id: &str)
    -> Result<String, FriendUseCaseError>;

    async fn list_friends(
        &self,
        command: ListFriendsCommand,
    ) -> Result<Vec<FriendListEntry>, FriendUseCaseError>;
}
