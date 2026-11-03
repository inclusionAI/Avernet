use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ActorKind, ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity,
    AuthorizeGroupSessionConnection, AuthorizedGroupSessionConnection,
    ConnectionView,
    GROUP_SESSION_WS_TOKEN_TTL_SECONDS, GetSession, GroupSessionConnectionBinding,
    GroupSessionConnectionError, GroupSessionConnectionService, IssueGroupSessionConnectionToken,
    IssuedGroupSessionConnectionToken, ParticipantMode, SessionParticipant, SessionService,
    VerifyGroupSessionConnectionToken, require_authenticated_user,
};
use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::port::{
    GroupSessionTokenError, GroupSessionTokenPort, GroupSessionTokenScope,
};

/// Transport-agnostic use cases for issuing and verifying session-scoped
/// Workbench WebSocket connection tokens.
pub struct GroupSessionConnectionServiceImpl {
    sessions: Arc<dyn SessionService>,
    tokens: Arc<dyn GroupSessionTokenPort>,
    /// Strict Session authorization-fact snapshot read
    /// (bot-authority plan Task 7.3, spec §8.3): the Bot view
    /// authorizes against the snapshot, never a display read.
    session_snapshots: Arc<dyn SessionAuthorizationCoreService>,
    /// Live Human→Bot management edge, consulted only when a
    /// Bot view is explicitly selected. A missing wiring fails
    /// closed.
    bot_authority: Option<Arc<dyn BotAuthorityPort>>,
}

impl GroupSessionConnectionServiceImpl {
    pub fn new(
        sessions: Arc<dyn SessionService>,
        tokens: Arc<dyn GroupSessionTokenPort>,
        session_snapshots: Arc<dyn SessionAuthorizationCoreService>,
        bot_authority: Option<Arc<dyn BotAuthorityPort>>,
    ) -> Self {
        Self {
            sessions,
            tokens,
            session_snapshots,
            bot_authority,
        }
    }
}

#[async_trait]
impl GroupSessionConnectionService for GroupSessionConnectionServiceImpl {
    async fn issue_token(
        &self,
        command: IssueGroupSessionConnectionToken,
    ) -> Result<IssuedGroupSessionConnectionToken, GroupSessionConnectionError> {
        let user_id = require_authenticated_user(&command.caller)?.id.clone();
        let tenant = command.caller.tenant.clone();
        let session_id = command.session_id.clone();
        let session = self
            .sessions
            .get(GetSession {
                caller: command.caller,
                session_id: session_id.clone(),
            })
            .await?;

        let issued = self
            .tokens
            .issue(
                GroupSessionTokenScope {
                    tenant,
                    user_id,
                    group_id: session.group_id,
                    session_id,
                },
                GROUP_SESSION_WS_TOKEN_TTL_SECONDS,
            )
            .map_err(map_issue_error)?;

        Ok(IssuedGroupSessionConnectionToken {
            token: issued.token,
            expires_at: issued.expires_at,
        })
    }

    async fn verify_token(
        &self,
        command: VerifyGroupSessionConnectionToken,
    ) -> Result<GroupSessionConnectionBinding, GroupSessionConnectionError> {
        let claims = self
            .tokens
            .verify(&command.token)
            .map_err(map_verify_error)?;

        Ok(GroupSessionConnectionBinding {
            tenant: claims.scope.tenant,
            user_id: claims.scope.user_id,
            group_id: claims.scope.group_id,
            session_id: claims.scope.session_id,
        })
    }

    async fn authorize_connect(
        &self,
        command: AuthorizeGroupSessionConnection,
    ) -> Result<AuthorizedGroupSessionConnection, GroupSessionConnectionError> {
        let binding = command.binding;
        match command.view {
            ConnectionView::HumanSelf => self
                .authorize_human_self_view(&binding)
                .await
                .map(|participants| AuthorizedGroupSessionConnection {
                    participants,
                    view: ConnectionView::HumanSelf,
                }),
            ConnectionView::Bot { bot_id } => self
                .authorize_bot_view(&binding, &bot_id)
                .await
                .map(|participants| AuthorizedGroupSessionConnection {
                    participants,
                    view: ConnectionView::Bot { bot_id },
                }),
        }
    }
}

impl GroupSessionConnectionServiceImpl {
    /// Human self view: the token proves the current Session read
    /// qualification and the Human must still be a present
    /// participant. No Bot authority is consulted (§3.3
    /// independent Human right).
    async fn authorize_human_self_view(
        &self,
        binding: &GroupSessionConnectionBinding,
    ) -> Result<Vec<SessionParticipant>, GroupSessionConnectionError> {
        let user_id = binding.user_id.clone();
        let caller = AuthenticatedCaller {
            tenant: binding.tenant.clone(),
            user: Some(AuthenticatedUserIdentity {
                id: user_id.clone(),
                username: user_id.clone(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        };
        let session = self
            .sessions
            .get(GetSession {
                caller,
                session_id: binding.session_id.clone(),
            })
            .await?;
        if session.group_id != binding.group_id {
            return Err(ApplicationError::forbidden(
                "Session no longer belongs to the token-bound Group",
            )
            .into());
        }
        let actor_id = format!("human_{user_id}");
        if session.participants.iter().any(|participant| {
            participant.actor_kind == ActorKind::Human
                && participant.actor_id == actor_id
                && participant.mode == ParticipantMode::Absent
        }) {
            return Err(ApplicationError::forbidden(
                "Human participant is absent from the token-bound Session",
            )
            .into());
        }
        Ok(session.participants)
    }

    /// Explicit Bot view (spec §8.3): the Bot view is a selection
    /// parameter, not a credential. Verify the token range
    /// (Session still belongs to the token-bound Group), the
    /// CURRENT Human→Bot management edge and the Bot's resource
    /// qualification (a live Session participant).
    async fn authorize_bot_view(
        &self,
        binding: &GroupSessionConnectionBinding,
        bot_id: &str,
    ) -> Result<Vec<SessionParticipant>, GroupSessionConnectionError> {
        let snapshot = self
            .session_snapshots
            .read_authorization_snapshot(&binding.session_id)
            .await
            .map_err(|_| {
                GroupSessionConnectionError::Internal(
                    "session authorization snapshot read failed".into(),
                )
            })?;
        let Some(snapshot) = snapshot else {
            return Err(ApplicationError::forbidden(
                "Session no longer exists",
            )
            .into());
        };
        if snapshot.group_id != binding.group_id {
            return Err(ApplicationError::forbidden(
                "Session no longer belongs to the token-bound Group",
            )
            .into());
        }
        let qualified = snapshot.participants.iter().any(|participant| {
            participant.actor_kind == ActorKind::Bot
                && participant.actor_id == bot_id
        });
        if !qualified {
            return Err(ApplicationError::forbidden(
                "Selected Bot view is not a participant of the token-bound Session",
            )
            .into());
        }
        match self.bot_authority.as_ref() {
            Some(hook) => match hook.can_manage(&binding.user_id, bot_id).await {
                Ok(true) => {}
                Ok(false) => {
                    return Err(ApplicationError::forbidden(
                        "The authenticated Human no longer controls the selected Bot view",
                    )
                    .into());
                }
                Err(_) => {
                    return Err(GroupSessionConnectionError::Internal(
                        "bot authority read failed".into(),
                    ));
                }
            },
            None => {
                return Err(ApplicationError::forbidden(
                    "Bot view authorization is not available",
                )
                .into());
            }
        }
        // The connect-time participant projection comes from the
        // same strict snapshot.
        Ok(snapshot
            .participants
            .iter()
            .map(|participant| SessionParticipant {
                actor_id: participant.actor_id.clone(),
                actor_kind: participant.actor_kind,
                name: None,
                role: participant.role,
                tags: Vec::new(),
                mode: participant.effective_mode,
                message_view_scope: participant.message_view_scope,
                joined_at: None,
            })
            .collect())
    }
}

fn map_issue_error(error: GroupSessionTokenError) -> GroupSessionConnectionError {
    match error {
        GroupSessionTokenError::Unavailable(_) => {
            GroupSessionConnectionError::TokenServiceUnavailable
        }
        GroupSessionTokenError::Invalid
        | GroupSessionTokenError::Expired
        | GroupSessionTokenError::Internal(_) => GroupSessionConnectionError::Internal(
            "group-session connection token issuance failed".into(),
        ),
    }
}

fn map_verify_error(error: GroupSessionTokenError) -> GroupSessionConnectionError {
    match error {
        GroupSessionTokenError::Invalid | GroupSessionTokenError::Expired => {
            GroupSessionConnectionError::InvalidConnectionToken
        }
        GroupSessionTokenError::Unavailable(_) => {
            GroupSessionConnectionError::TokenServiceUnavailable
        }
        GroupSessionTokenError::Internal(_) => GroupSessionConnectionError::Internal(
            "group-session connection token verification failed".into(),
        ),
    }
}
