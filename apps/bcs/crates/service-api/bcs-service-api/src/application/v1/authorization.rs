use std::collections::BTreeSet;

use async_trait::async_trait;

use super::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUser, AuthenticatedUserIdentity, Principal,
};

/// Selects which authenticated identity may become the effective Actor for an
/// OpenAPI V1 use case. Resource authorization remains the responsibility of
/// the application service after this selection.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum IdentityPolicy {
    /// Existing V1 routes fail closed as Human-only unless they opt in to a
    /// different policy.
    #[default]
    HumanOnly,
    BotOnly,
    HumanOrOwnedBot,
}

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

fn require_bot(caller: &AuthenticatedCaller) -> Result<Principal, ApplicationError> {
    let bot = caller
        .bot
        .as_ref()
        .ok_or_else(|| ApplicationError::forbidden("This operation requires a Bot caller"))?;
    let tenant = caller
        .tenant
        .as_ref()
        .ok_or_else(|| ApplicationError::forbidden("The Bot caller requires a tenant"))?;
    Ok(Principal::bot(
        bot.bot_uuid.clone(),
        tenant.clone(),
        BTreeSet::new(),
    ))
}

/// Select the effective Actor from Gateway-authenticated identities.
///
/// The User/Bot ownership consistency check is claim-local: Gateway signs the
/// Bot `owner_id`, so this layer never needs a database lookup.
pub fn select_principal(
    caller: &AuthenticatedCaller,
    policy: IdentityPolicy,
) -> Result<Principal, ApplicationError> {
    match policy {
        IdentityPolicy::HumanOnly => require_human(caller),
        IdentityPolicy::BotOnly => require_bot(caller),
        IdentityPolicy::HumanOrOwnedBot => match (&caller.user, &caller.bot) {
            (Some(user), Some(bot)) => {
                if bot.owner_id != user.id {
                    return Err(ApplicationError::forbidden(
                        "The authenticated Bot is not owned by the authenticated User",
                    ));
                }
                require_bot(caller)
            }
            (None, Some(_)) => require_bot(caller),
            (Some(_), None) => require_human(caller),
            (None, None) => Err(ApplicationError::forbidden(
                "This operation requires a Human or Bot caller",
            )),
        },
    }
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
