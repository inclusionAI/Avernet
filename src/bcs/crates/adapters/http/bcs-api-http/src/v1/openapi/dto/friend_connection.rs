use bcs_service_api::application::v1::{
    AuthenticatedCaller, CancelFriendConnectionRequest, CreateFriendConnectionRequest,
    DeleteFriendConnection, FriendConnectionActor, FriendConnectionRequestDirection,
    FriendConnectionRequestStatus, ListFriendConnectionRequests, ListFriendConnections,
    RejectFriendConnectionRequest,
};
use serde::Deserialize;

fn default_page() -> u32 { 1 }
fn default_page_size() -> u32 { 20 }

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateFriendConnectionRequestBody {
    #[serde(default)]
    pub from_actor: Option<FriendConnectionActor>,
    pub to_actor: FriendConnectionActor,
    #[serde(default)]
    pub message: Option<String>,
}

impl CreateFriendConnectionRequestBody {
    pub fn into_command(self, caller: AuthenticatedCaller) -> CreateFriendConnectionRequest {
        CreateFriendConnectionRequest {
            caller,
            from_actor: self.from_actor,
            to_actor: self.to_actor,
            message: self.message,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListFriendConnectionRequestsQuery {
    #[serde(default)]
    pub actor_type: Option<bcs_service_api::application::v1::FriendConnectionActorType>,
    #[serde(default)]
    pub actor_id: Option<String>,
    #[serde(default)]
    pub direction: FriendConnectionRequestDirection,
    #[serde(default)]
    pub status: Option<FriendConnectionRequestStatus>,
    #[serde(default = "default_page")]
    pub page: u32,
    #[serde(default = "default_page_size")]
    pub page_size: u32,
}

impl ListFriendConnectionRequestsQuery {
    pub fn into_command(self, caller: AuthenticatedCaller) -> ListFriendConnectionRequests {
        let actor = match (self.actor_type, self.actor_id) {
            (Some(actor_type), Some(id)) => Some(FriendConnectionActor { actor_type, id }),
            _ => None,
        };
        ListFriendConnectionRequests {
            caller,
            actor,
            direction: self.direction,
            status: self.status,
            page: self.page,
            page_size: self.page_size,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RejectFriendConnectionRequestBody {
    #[serde(default)]
    pub reason: Option<String>,
}

impl RejectFriendConnectionRequestBody {
    pub fn into_command(
        self,
        caller: AuthenticatedCaller,
        request_id: u64,
    ) -> RejectFriendConnectionRequest {
        RejectFriendConnectionRequest {
            caller,
            request_id,
            reason: self.reason,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListFriendConnectionsQuery {
    pub actor_type: bcs_service_api::application::v1::FriendConnectionActorType,
    pub actor_id: String,
}

impl ListFriendConnectionsQuery {
    pub fn into_command(self, caller: AuthenticatedCaller) -> ListFriendConnections {
        ListFriendConnections {
            caller,
            actor: FriendConnectionActor {
                actor_type: self.actor_type,
                id: self.actor_id,
            },
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeleteFriendConnectionQuery {
    pub target_actor_type: bcs_service_api::application::v1::FriendConnectionActorType,
    pub target_actor_id: String,
}

impl DeleteFriendConnectionQuery {
    pub fn into_command(self, caller: AuthenticatedCaller) -> DeleteFriendConnection {
        DeleteFriendConnection {
            caller,
            target_actor: FriendConnectionActor {
                actor_type: self.target_actor_type,
                id: self.target_actor_id,
            },
        }
    }
}

pub fn cancel_command(
    caller: AuthenticatedCaller,
    request_id: u64,
) -> CancelFriendConnectionRequest {
    CancelFriendConnectionRequest { caller, request_id }
}
