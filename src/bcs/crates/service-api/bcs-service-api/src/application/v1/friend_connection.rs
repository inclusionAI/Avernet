use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, AuthenticatedCaller, DeleteResult};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FriendConnectionActorType {
    Human,
    Bot,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FriendConnectionActor {
    #[serde(rename = "type")]
    pub actor_type: FriendConnectionActorType,
    pub id: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum FriendConnectionRequestStatus {
    Pending,
    Approved,
    Rejected,
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FriendConnectionRequestDirection {
    Received,
    Sent,
    All,
}

impl Default for FriendConnectionRequestDirection {
    fn default() -> Self {
        Self::Received
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FriendConnectionCreateStatus {
    Pending,
    Approved,
    PublicNoEdge,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FriendConnectionCreateResult {
    pub request_ids: Vec<u64>,
    pub edge_ids: Vec<u64>,
    pub status: FriendConnectionCreateStatus,
    pub auto_accepted: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FriendConnectionRequestView {
    pub request_id: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub edge_id: Option<u64>,
    pub from_actor: FriendConnectionActor,
    pub to_actor: FriendConnectionActor,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    pub status: FriendConnectionRequestStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decision_reason: Option<String>,
    pub created_by: FriendConnectionActor,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decided_by: Option<FriendConnectionActor>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decided_at: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FriendConnectionRequestPage {
    pub items: Vec<FriendConnectionRequestView>,
    pub total: u32,
    pub page: u32,
    pub page_size: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FriendConnectionView {
    pub actor: FriendConnectionActor,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
    pub is_online: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FriendConnectionPage {
    pub items: Vec<FriendConnectionView>,
    pub total: u32,
}

#[derive(Debug, Clone)]
pub struct CreateFriendConnectionRequest {
    pub caller: AuthenticatedCaller,
    pub from_actor: Option<FriendConnectionActor>,
    pub to_actor: FriendConnectionActor,
    pub message: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ListFriendConnectionRequests {
    pub caller: AuthenticatedCaller,
    pub actor: Option<FriendConnectionActor>,
    pub direction: FriendConnectionRequestDirection,
    pub status: Option<FriendConnectionRequestStatus>,
    pub page: u32,
    pub page_size: u32,
}

#[derive(Debug, Clone)]
pub struct AcceptFriendConnectionRequest {
    pub caller: AuthenticatedCaller,
    pub request_id: u64,
}

#[derive(Debug, Clone)]
pub struct RejectFriendConnectionRequest {
    pub caller: AuthenticatedCaller,
    pub request_id: u64,
    pub reason: Option<String>,
}

#[derive(Debug, Clone)]
pub struct CancelFriendConnectionRequest {
    pub caller: AuthenticatedCaller,
    pub request_id: u64,
}

#[derive(Debug, Clone)]
pub struct ListFriendConnections {
    pub caller: AuthenticatedCaller,
    pub actor: FriendConnectionActor,
}

#[derive(Debug, Clone)]
pub struct DeleteFriendConnection {
    pub caller: AuthenticatedCaller,
    pub target_actor: FriendConnectionActor,
}

#[async_trait]
pub trait FriendConnectionService: Send + Sync {
    async fn create_friend_connection_request(
        &self,
        command: CreateFriendConnectionRequest,
    ) -> Result<FriendConnectionCreateResult, ApplicationError>;

    async fn list_friend_connection_requests(
        &self,
        command: ListFriendConnectionRequests,
    ) -> Result<FriendConnectionRequestPage, ApplicationError>;

    async fn accept_friend_connection_request(
        &self,
        command: AcceptFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError>;

    async fn reject_friend_connection_request(
        &self,
        command: RejectFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError>;

    async fn cancel_friend_connection_request(
        &self,
        command: CancelFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError>;

    async fn list_friend_connections(
        &self,
        command: ListFriendConnections,
    ) -> Result<FriendConnectionPage, ApplicationError>;

    async fn delete_friend_connection(
        &self,
        command: DeleteFriendConnection,
    ) -> Result<DeleteResult, ApplicationError>;
}
