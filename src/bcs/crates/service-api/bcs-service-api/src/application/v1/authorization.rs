use async_trait::async_trait;

use super::{ApplicationError, Principal};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    ListBotGroups,
    CreateGroup,
    ReadGroup,
    UpdateGroup,
    DeleteGroup,
    AddGroupParticipant,
    UpdateGroupParticipant,
    RemoveGroupParticipant,
    CreateSession,
    ReadSession,
    UpdateSession,
    DeleteSession,
    CompleteSession,
    ListSessionMessages,
    AddSessionParticipant,
    UpdateSessionParticipant,
    RemoveSessionParticipant,
    CreateGroupInvitation,
    CreateSessionInvitation,
    AcceptInvitation,
    ListBotFriendships,
    DeleteBotFriendship,
    CreateBotFriendRequest,
    ListBotFriendRequests,
    AcceptFriendRequest,
    RejectFriendRequest,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResourceRef<'a> {
    Bot(&'a str),
    Group(&'a str),
    NewGroup,
}

#[async_trait]
pub trait AuthorizationService: Send + Sync {
    async fn authorize(
        &self,
        principal: &Principal,
        action: Action,
        resource: ResourceRef<'_>,
    ) -> Result<(), ApplicationError>;
}
