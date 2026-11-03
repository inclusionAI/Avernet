use std::collections::BTreeSet;

use async_trait::async_trait;

use super::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUser, AuthenticatedUserIdentity,
    Principal,
};
use crate::port::bot_authority::BotAuthorityPort;

/// Selects which authenticated identity may become the effective Actor for an
/// OpenAPI V1 use case. Resource authorization remains the responsibility of
/// the application service after this selection.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IdentityPolicy {
    /// Existing V1 routes fail closed as Human-only unless they opt in to a
    /// different policy.
    HumanOnly,
    BotOnly,
    HumanOrOwnedBot,
    /// Human-Or-AuthorizedBot (bot-authority plan Task 6, spec §3.3):
    /// the authenticated Human acts AS the current manager of
    /// `bot_id`, and the Bot becomes the business principal when
    /// (and only when) the Human's management edge passes
    /// live `can_manage`. A pure Bot credential keeps its own
    /// authentication and never consults the authority hook.
    HumanOrAuthorizedBot {
        /// The Bot (byte-exact Bot id) this resolution is for.
        bot_id: String,
    },
}

impl IdentityPolicy {
    /// The Bot id a [`IdentityPolicy::HumanOrAuthorizedBot`]
    /// resolution is for; `None` for every other policy.
    pub fn authorized_bot_id(&self) -> Option<&str> {
        match self {
            IdentityPolicy::HumanOrAuthorizedBot { bot_id } => Some(bot_id.as_str()),
            _ => None,
        }
    }
}

impl Default for IdentityPolicy {
    fn default() -> Self {
        IdentityPolicy::HumanOnly
    }
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
        // This claim-local selector cannot verify the live
        // management edge `HumanOrAuthorizedBot` requires;
        // use `resolve_authorized_principal` for that policy.
        IdentityPolicy::HumanOrAuthorizedBot { .. } => Err(
            ApplicationError::forbidden(
                "this route does not implement the \
                 HumanOrAuthorizedBot identity policy",
            ),
        ),
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

/// Resolve the effective business principal for the caller under
/// `policy`, consulting the authority hook ONLY where the policy
/// needs live management facts (bot-authority plan Task 6.3,
/// spec §3.3 授权来源与撤权边界).
///
/// - `HumanOrAuthorizedBot` with hybrid User+Bot credentials: the
///   Human must pass a live `can_manage(bot_id)` FIRST, and only
///   then does the (credential-matching) Bot become the business
///   principal. A revoked or denied edge is a 403 here.
/// - A pure Bot credential keeps its pure-Bot contract unchanged:
///   the authority hook is NOT consulted for that path.
/// - Human-only credentials keep the existing Human self/view
///   rules (`require_human`); the resolver only selects the
///   principal — it never fabricates resource roles.
///
/// Authority read failures are errors, never denials-as-`Ok(false)`
/// masquerade, and never a silent `Ok`.
pub async fn resolve_authorized_principal(
    caller: &AuthenticatedCaller,
    policy: IdentityPolicy,
    authority: &dyn BotAuthorityPort,
) -> Result<Principal, ApplicationError> {
    match policy {
        // Policies that carry no authority fact consult nobody: the
        // hook cannot be reached and cannot turn identity selection
        // into a deny (or an unexpected success).
        policy @ (IdentityPolicy::HumanOnly
        | IdentityPolicy::BotOnly
        | IdentityPolicy::HumanOrOwnedBot) => select_principal(caller, policy),
        IdentityPolicy::HumanOrAuthorizedBot { bot_id } => {
            match (&caller.user, &caller.bot) {
                // Hybrid credential: the Human's live management edge
                // authorizes the BOT to act.
                (Some(user), Some(bot)) => {
                    if bot.bot_uuid != bot_id {
                        return Err(ApplicationError::forbidden(format!(
                            "the authenticated bot '{}' does not match the \
                             requested bot '{bot_id}'",
                            bot.bot_uuid
                        )));
                    }
                    match authority.can_manage(&user.id, &bot_id).await {
                        Ok(true) => require_bot(caller),
                        Ok(false) => Err(ApplicationError::forbidden(format!(
                            "the caller is not the owner or a manager of \
                             bot '{bot_id}'"
                        ))),
                        // A failing authority read must never be treated
                        // as "unauthorized" or as "authorized" (spec §8:
                        // authorization reads return `Result`).
                        Err(error) => Err(ApplicationError::internal(format!(
                            "bot authority lookup failed: {error}"
                        ))),
                    }
                }
                // Pure Bot: its own authentication decides; the
                // authority hook is not consulted (spec §3.3).
                (None, Some(_)) => require_bot(caller),
                // Human-only keeps the existing Human self rules.
                (Some(_), None) => require_human(caller),
                (None, None) => Err(ApplicationError::forbidden(
                    "This operation requires a Human or Bot caller",
                )),
            }
        }
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
