use std::collections::BTreeSet;

use async_trait::async_trait;

use super::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUser, AuthenticatedUserIdentity, Principal,
};

/// Require the User identity admitted by the current Human-facing V1 APIs.
///
/// Gateway authentication may establish several identities at once. These
/// APIs deliberately select only `caller.user`; Bot/App/AccessKey identities
/// never act as a fallback.
pub fn require_authenticated_user(
    caller: &AuthenticatedCaller,
) -> Result<&AuthenticatedUserIdentity, ApplicationError> {
    caller
        .user
        .as_ref()
        .ok_or_else(|| ApplicationError::forbidden("This operation requires a Human caller"))
}

/// Project the authenticated User into BCS's existing Human Actor model.
pub fn require_human(caller: &AuthenticatedCaller) -> Result<Principal, ApplicationError> {
    let user = require_authenticated_user(caller)?;
    Ok(Principal::human(
        AuthenticatedUser {
            id: user.id.clone(),
            username: user.username.clone(),
            display_name: user.display_name.clone(),
            full_name: user.full_name.clone(),
        },
        caller.tenant.clone(),
        BTreeSet::new(),
    ))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    ListGroups,
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
