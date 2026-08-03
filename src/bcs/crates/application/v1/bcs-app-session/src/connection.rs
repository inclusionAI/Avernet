use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    GetSession, GroupSessionConnectionBinding, GroupSessionConnectionError,
    GroupSessionConnectionService, IssueGroupSessionConnectionToken,
    IssuedGroupSessionConnectionToken, SessionService, VerifyGroupSessionConnectionToken,
    GROUP_SESSION_WS_TOKEN_TTL_SECONDS, require_authenticated_user,
};
use bcs_service_api::port::{
    GroupSessionTokenError, GroupSessionTokenPort, GroupSessionTokenScope,
};

/// Transport-agnostic use cases for issuing and verifying session-scoped
/// Workbench WebSocket connection tokens.
pub struct GroupSessionConnectionServiceImpl {
    sessions: Arc<dyn SessionService>,
    tokens: Arc<dyn GroupSessionTokenPort>,
}

impl GroupSessionConnectionServiceImpl {
    pub fn new(
        sessions: Arc<dyn SessionService>,
        tokens: Arc<dyn GroupSessionTokenPort>,
    ) -> Self {
        Self { sessions, tokens }
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
