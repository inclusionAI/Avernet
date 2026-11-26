use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, AuthenticatedCaller, DeleteResult};
use crate::RequestAuthHeaders;

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
    pub request_ids: Vec<String>,
    pub edge_ids: Vec<u64>,
    pub status: FriendConnectionCreateStatus,
    pub auto_accepted: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FriendConnectionRequestView {
    pub request_id: String,
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
    /// Number of friends matching target_type before pagination.
    pub total: u32,
    pub page: u32,
    pub page_size: u32,
}

#[derive(Debug, Clone)]
pub struct CreateFriendConnectionRequest {
    pub caller: AuthenticatedCaller,
    pub from_actor: Option<FriendConnectionActor>,
    pub to_actor: FriendConnectionActor,
    pub message: Option<String>,
    /// Caller credential (Authorization/Cookie) forwarded to the backend
    /// work-order API so its user-scoped auth accepts the notification.
    pub request_auth: Option<RequestAuthHeaders>,
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
    pub request_id: String,
    /// Forwarded gateway principal + trace headers so the grant
    /// friend-auth-sync trigger (Task 11b) can authenticate the backend
    /// work-order call.
    pub request_auth: Option<RequestAuthHeaders>,
}

#[derive(Debug, Clone)]
pub struct RejectFriendConnectionRequest {
    pub caller: AuthenticatedCaller,
    pub request_id: String,
    pub reason: Option<String>,
}

#[derive(Debug, Clone)]
pub struct CancelFriendConnectionRequest {
    pub caller: AuthenticatedCaller,
    pub request_id: String,
}

#[derive(Debug, Clone)]
pub struct ListFriendConnections {
    pub caller: AuthenticatedCaller,
    pub actor: FriendConnectionActor,
    /// Omit to include both Human and Bot peers. Does not change edge permissions.
    pub target_type: Option<FriendConnectionActorType>,
    /// One-based page; page_size must be between 1 and 100.
    pub page: u32,
    pub page_size: u32,
}

#[derive(Debug, Clone)]
pub struct DeleteFriendConnection {
    pub caller: AuthenticatedCaller,
    /// Absent = caller-side self-removal (today's behavior). Present = a
    /// managed-Bot Owner removal where `actor` is the Bot whose friendship
    /// is being dissolved; the transport layer validates both-or-none on
    /// the wire params, and the application service gates it on
    /// `require_owner`.
    pub actor: Option<FriendConnectionActor>,
    pub target_actor: FriendConnectionActor,
    /// Forwarded gateway principal + trace headers so the revoke
    /// friend-auth-sync trigger (Task 11d) can authenticate the backend
    /// work-order call.
    pub request_auth: Option<RequestAuthHeaders>,
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::application::v1::{AuthenticatedCaller, AuthenticatedUserIdentity};

    fn user_caller() -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: None,
            user: Some(AuthenticatedUserIdentity {
                id: "u_1".into(),
                username: "alice".into(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    #[test]
    fn friend_connection_actor_serde_round_trip_is_unchanged() {
        let actor = FriendConnectionActor {
            actor_type: FriendConnectionActorType::Bot,
            id: "bot-1".into(),
        };
        let json = serde_json::to_string(&actor).unwrap();
        assert_eq!(json, r#"{"type":"bot","id":"bot-1"}"#);
        let restored: FriendConnectionActor = serde_json::from_str(&json).unwrap();
        assert_eq!(restored, actor);

        let human = FriendConnectionActor {
            actor_type: FriendConnectionActorType::Human,
            id: "u_1".into(),
        };
        let json = serde_json::to_string(&human).unwrap();
        assert_eq!(json, r#"{"type":"human","id":"u_1"}"#);
    }

    #[test]
    fn delete_friend_connection_without_actor_stays_self_removal() {
        let command = DeleteFriendConnection {
            caller: user_caller(),
            target_actor: FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: "bot-1".into(),
            },
            actor: None,
            request_auth: None,
        };
        assert!(command.actor.is_none());
    }

    #[test]
    fn delete_friend_connection_with_actor_marks_managed_bot_removal() {
        let command = DeleteFriendConnection {
            caller: user_caller(),
            target_actor: FriendConnectionActor {
                actor_type: FriendConnectionActorType::Human,
                id: "u_2".into(),
            },
            actor: Some(FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: "bot-1".into(),
            }),
            request_auth: None,
        };
        let actor = command.actor.expect("actor present");
        assert_eq!(actor.actor_type, FriendConnectionActorType::Bot);
        assert_eq!(actor.id, "bot-1");
    }
}
