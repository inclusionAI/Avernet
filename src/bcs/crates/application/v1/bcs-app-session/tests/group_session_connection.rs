use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_session::GroupSessionConnectionServiceImpl;
use bcs_service_api::application::v1::{
    ActorKind, AddSessionParticipant, ApplicationError, AuthenticatedAccessKeyIdentity,
    AuthenticatedAppIdentity, AuthenticatedBotIdentity, AuthenticatedCaller,
    AuthenticatedUserIdentity, AuthorizeGroupSessionConnection, CompleteSession, CreateSession,
    CreateSessionOutcome, DeleteResult, DeleteSession, DeleteSessionParticipant, GetSession,
    GroupSessionConnectionBinding, GroupSessionConnectionError, GroupSessionConnectionService,
    IssueGroupSessionConnectionToken, IssuedGroupSessionConnectionToken, ListSessions, Page,
    ParticipantMode, ParticipantRole, SessionCompletionResult, SessionDetail, SessionParticipant,
    SessionService, SessionStatus, SessionSummary, UpdateSession, UpdateSessionParticipant,
    VerifyGroupSessionConnectionToken, GROUP_SESSION_WS_TOKEN_TTL_SECONDS,
};
use bcs_service_api::port::{
    GroupSessionTokenClaims, GroupSessionTokenError, GroupSessionTokenPort,
    GroupSessionTokenScope, IssuedGroupSessionToken,
};
use time::OffsetDateTime;

#[derive(Clone, Copy)]
enum SessionMode {
    Success,
    Forbidden,
    NotFound,
    OtherGroup,
    HumanAbsent,
    OwnedBotOnly,
}

struct FakeSessionService {
    mode: SessionMode,
    get_calls: Mutex<Vec<GetSession>>,
}

impl FakeSessionService {
    fn new(mode: SessionMode) -> Self {
        Self {
            mode,
            get_calls: Mutex::new(Vec::new()),
        }
    }

    fn get_call_count(&self) -> usize {
        match self.get_calls.lock() {
            Ok(calls) => calls.len(),
            Err(_) => panic!("test mutex must not be poisoned"),
        }
    }

    fn get_calls(&self) -> Vec<GetSession> {
        match self.get_calls.lock() {
            Ok(calls) => calls.clone(),
            Err(_) => panic!("test mutex must not be poisoned"),
        }
    }
}

#[async_trait]
impl SessionService for FakeSessionService {
    async fn create(
        &self,
        _command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError> {
        panic!("create is not used by connection-token tests")
    }

    async fn list(&self, _command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        panic!("list is not used by connection-token tests")
    }

    async fn get(&self, query: GetSession) -> Result<SessionDetail, ApplicationError> {
        match self.get_calls.lock() {
            Ok(mut calls) => calls.push(query.clone()),
            Err(_) => panic!("test mutex must not be poisoned"),
        }
        match self.mode {
            SessionMode::Success => Ok(session_detail(&query.session_id, "server-owned-group")),
            SessionMode::Forbidden => Err(ApplicationError::forbidden("session access denied")),
            SessionMode::NotFound => Err(ApplicationError::not_found(
                "session_not_found",
                "session not found",
            )),
            SessionMode::OtherGroup => Ok(session_detail(&query.session_id, "moved-group")),
            SessionMode::HumanAbsent => {
                let mut session = session_detail(&query.session_id, "server-owned-group");
                session.participants[0].mode = ParticipantMode::Absent;
                Ok(session)
            }
            SessionMode::OwnedBotOnly => {
                let mut session = session_detail(&query.session_id, "server-owned-group");
                session.participants[0] = SessionParticipant {
                    actor_id: "owned-bot".into(),
                    actor_kind: ActorKind::Bot,
                    name: None,
                    role: ParticipantRole::Driver,
                    mode: ParticipantMode::Auto,
                    joined_at: None,
                };
                Ok(session)
            }
        }
    }

    async fn update(&self, _command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        panic!("update is not used by connection-token tests")
    }

    async fn delete(&self, _command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        panic!("delete is not used by connection-token tests")
    }

    async fn complete(
        &self,
        _command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        panic!("complete is not used by connection-token tests")
    }

    async fn add_participant(
        &self,
        _command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        panic!("add_participant is not used by connection-token tests")
    }

    async fn update_participant(
        &self,
        _command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        panic!("update_participant is not used by connection-token tests")
    }

    async fn delete_participant(
        &self,
        _command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        panic!("delete_participant is not used by connection-token tests")
    }
}

fn session_binding() -> GroupSessionConnectionBinding {
    GroupSessionConnectionBinding {
        tenant: Some("tenant-a".into()),
        user_id: "user-a".into(),
        group_id: "server-owned-group".into(),
        session_id: "session-a".into(),
    }
}

#[derive(Clone, Copy)]
enum TokenMode {
    Success,
    Invalid,
    Unavailable,
    Internal,
}

struct FakeTokenPort {
    mode: TokenMode,
    issue_calls: Mutex<Vec<(GroupSessionTokenScope, u64)>>,
}

impl FakeTokenPort {
    fn new(mode: TokenMode) -> Self {
        Self {
            mode,
            issue_calls: Mutex::new(Vec::new()),
        }
    }

    fn issued_scopes(&self) -> Vec<(GroupSessionTokenScope, u64)> {
        match self.issue_calls.lock() {
            Ok(calls) => calls.clone(),
            Err(_) => panic!("test mutex must not be poisoned"),
        }
    }
}

impl GroupSessionTokenPort for FakeTokenPort {
    fn issue(
        &self,
        scope: GroupSessionTokenScope,
        ttl_seconds: u64,
    ) -> Result<IssuedGroupSessionToken, GroupSessionTokenError> {
        match self.issue_calls.lock() {
            Ok(mut calls) => calls.push((scope, ttl_seconds)),
            Err(_) => panic!("test mutex must not be poisoned"),
        }
        match self.mode {
            TokenMode::Success => Ok(IssuedGroupSessionToken {
                token: "session-token".into(),
                expires_at: OffsetDateTime::UNIX_EPOCH,
            }),
            TokenMode::Invalid => Err(GroupSessionTokenError::Invalid),
            TokenMode::Unavailable => Err(GroupSessionTokenError::Unavailable("offline".into())),
            TokenMode::Internal => Err(GroupSessionTokenError::Internal("sign failed".into())),
        }
    }

    fn verify(&self, _token: &str) -> Result<GroupSessionTokenClaims, GroupSessionTokenError> {
        match self.mode {
            TokenMode::Success => Ok(GroupSessionTokenClaims {
                scope: GroupSessionTokenScope {
                    tenant: Some("tenant-a".into()),
                    user_id: "user-a".into(),
                    group_id: "group-a".into(),
                    session_id: "session-a".into(),
                },
                issued_at: 100,
                expires_at: 400,
            }),
            TokenMode::Invalid => Err(GroupSessionTokenError::Invalid),
            TokenMode::Unavailable => Err(GroupSessionTokenError::Unavailable("offline".into())),
            TokenMode::Internal => Err(GroupSessionTokenError::Internal("verify failed".into())),
        }
    }
}

fn human_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: "user-a".into(),
            username: "alice".into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn session_detail(session_id: &str, group_id: &str) -> SessionDetail {
    SessionDetail {
        session_id: session_id.into(),
        version: 1,
        group_id: group_id.into(),
        status: SessionStatus::Running,
        title: None,
        input: None,
        participants: vec![SessionParticipant {
            actor_id: "human_user-a".into(),
            actor_kind: ActorKind::Human,
            name: None,
            role: ParticipantRole::Consultant,
            mode: ParticipantMode::Present,
            joined_at: None,
        }],
        created_at: 1,
        updated_at: 1,
    }
}

#[tokio::test]
async fn authorize_connect_reloads_the_exact_bound_session() {
    let sessions = Arc::new(FakeSessionService::new(SessionMode::Success));
    let service = GroupSessionConnectionServiceImpl::new(
        sessions.clone(),
        Arc::new(FakeTokenPort::new(TokenMode::Success)),
    );

    let authorized = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: session_binding(),
        })
        .await
        .expect("current V1 session access should authorize connect");

    assert_eq!(authorized.participants.len(), 1);
    assert_eq!(authorized.participants[0].actor_id, "human_user-a");
    let calls = sessions.get_calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].session_id, "session-a");
    assert_eq!(calls[0].caller.tenant.as_deref(), Some("tenant-a"));
    assert_eq!(
        calls[0].caller.user.as_ref().map(|user| user.id.as_str()),
        Some("user-a")
    );
}

#[tokio::test]
async fn authorize_connect_rejects_a_deleted_session() {
    let service = GroupSessionConnectionServiceImpl::new(
        Arc::new(FakeSessionService::new(SessionMode::NotFound)),
        Arc::new(FakeTokenPort::new(TokenMode::Success)),
    );

    let result = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: session_binding(),
        })
        .await;

    assert!(matches!(
        result,
        Err(GroupSessionConnectionError::Application(ApplicationError::NotFound { .. }))
    ));
}

#[tokio::test]
async fn authorize_connect_rejects_revoked_v1_session_access() {
    let service = GroupSessionConnectionServiceImpl::new(
        Arc::new(FakeSessionService::new(SessionMode::Forbidden)),
        Arc::new(FakeTokenPort::new(TokenMode::Success)),
    );

    let result = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: session_binding(),
        })
        .await;

    assert!(matches!(
        result,
        Err(GroupSessionConnectionError::Application(ApplicationError::Forbidden(_)))
    ));
}

#[tokio::test]
async fn authorize_connect_rejects_a_session_moved_to_another_group() {
    let service = GroupSessionConnectionServiceImpl::new(
        Arc::new(FakeSessionService::new(SessionMode::OtherGroup)),
        Arc::new(FakeTokenPort::new(TokenMode::Success)),
    );

    let result = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: session_binding(),
        })
        .await;

    assert!(matches!(
        result,
        Err(GroupSessionConnectionError::Application(ApplicationError::Forbidden(_)))
    ));
}

#[tokio::test]
async fn authorize_connect_rejects_an_absent_bound_human() {
    let service = GroupSessionConnectionServiceImpl::new(
        Arc::new(FakeSessionService::new(SessionMode::HumanAbsent)),
        Arc::new(FakeTokenPort::new(TokenMode::Success)),
    );

    let result = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: session_binding(),
        })
        .await;

    assert!(matches!(
        result,
        Err(GroupSessionConnectionError::Application(ApplicationError::Forbidden(_)))
    ));
}

#[tokio::test]
async fn authorize_connect_accepts_v1_access_through_an_owned_bot() {
    let service = GroupSessionConnectionServiceImpl::new(
        Arc::new(FakeSessionService::new(SessionMode::OwnedBotOnly)),
        Arc::new(FakeTokenPort::new(TokenMode::Success)),
    );

    let authorized = service
        .authorize_connect(AuthorizeGroupSessionConnection {
            binding: session_binding(),
        })
        .await
        .expect("the V1 Session service already authorized the owned Bot");

    assert_eq!(authorized.participants.len(), 1);
    assert_eq!(authorized.participants[0].actor_id, "owned-bot");
    assert_eq!(authorized.participants[0].actor_kind, ActorKind::Bot);
}

#[tokio::test]
async fn issues_scope_only_from_human_and_authorized_session() {
    let sessions = Arc::new(FakeSessionService::new(SessionMode::Success));
    let tokens = Arc::new(FakeTokenPort::new(TokenMode::Success));
    let service = GroupSessionConnectionServiceImpl::new(sessions.clone(), tokens.clone());

    let result = service
        .issue_token(IssueGroupSessionConnectionToken {
            caller: human_caller(),
            session_id: "session-from-path".into(),
        })
        .await;

    assert!(matches!(
        result,
        Ok(IssuedGroupSessionConnectionToken { ref token, .. }) if token == "session-token"
    ));
    assert_eq!(sessions.get_call_count(), 1);
    assert_eq!(
        tokens.issued_scopes(),
        vec![(
            GroupSessionTokenScope {
                tenant: Some("tenant-a".into()),
                user_id: "user-a".into(),
                group_id: "server-owned-group".into(),
                session_id: "session-from-path".into(),
            },
            GROUP_SESSION_WS_TOKEN_TTL_SECONDS,
        )]
    );
}

#[tokio::test]
async fn issues_a_tenantless_scope_for_a_tenantless_human() {
    let sessions = Arc::new(FakeSessionService::new(SessionMode::Success));
    let tokens = Arc::new(FakeTokenPort::new(TokenMode::Success));
    let service = GroupSessionConnectionServiceImpl::new(sessions, tokens.clone());
    let mut caller = human_caller();
    caller.tenant = None;

    let result = service
        .issue_token(IssueGroupSessionConnectionToken {
            caller,
            session_id: "session-from-path".into(),
        })
        .await;

    assert!(result.is_ok());
    assert_eq!(tokens.issued_scopes()[0].0.tenant, None);
}

#[tokio::test]
async fn rejects_every_non_human_caller_before_reading_session_or_signing() {
    let sessions = Arc::new(FakeSessionService::new(SessionMode::Success));
    let tokens = Arc::new(FakeTokenPort::new(TokenMode::Success));
    let service = GroupSessionConnectionServiceImpl::new(sessions.clone(), tokens.clone());
    let callers = [
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: Some(AuthenticatedBotIdentity {
                bot_uuid: "bot-a".into(),
                owner_id: "owner-a".into(),
                app_id: 1,
                agent_code: "agent-a".into(),
            }),
            app: None,
            access_key: None,
        },
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: None,
            app: Some(AuthenticatedAppIdentity {
                app_id: 1,
                app_name: "app-a".into(),
                owners: "owner-a".into(),
                app_type: "service".into(),
            }),
            access_key: None,
        },
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: None,
            app: None,
            access_key: Some(AuthenticatedAccessKeyIdentity {
                access_key: "test-access-key".into(),
                expire_at: OffsetDateTime::UNIX_EPOCH,
            }),
        },
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: None,
            app: None,
            access_key: None,
        },
    ];

    for caller in callers {
        let result = service
            .issue_token(IssueGroupSessionConnectionToken {
                caller,
                session_id: "session-a".into(),
            })
            .await;

        assert!(matches!(
            result,
            Err(GroupSessionConnectionError::Application(ApplicationError::Forbidden(_)))
        ));
    }
    assert_eq!(sessions.get_call_count(), 0);
    assert!(tokens.issued_scopes().is_empty());
}

#[tokio::test]
async fn does_not_sign_when_session_access_is_forbidden() {
    let sessions = Arc::new(FakeSessionService::new(SessionMode::Forbidden));
    let tokens = Arc::new(FakeTokenPort::new(TokenMode::Success));
    let service = GroupSessionConnectionServiceImpl::new(sessions, tokens.clone());

    let result = service
        .issue_token(IssueGroupSessionConnectionToken {
            caller: human_caller(),
            session_id: "session-a".into(),
        })
        .await;

    assert!(matches!(
        result,
        Err(GroupSessionConnectionError::Application(ApplicationError::Forbidden(_)))
    ));
    assert!(tokens.issued_scopes().is_empty());
}

#[tokio::test]
async fn does_not_sign_when_session_is_missing() {
    let sessions = Arc::new(FakeSessionService::new(SessionMode::NotFound));
    let tokens = Arc::new(FakeTokenPort::new(TokenMode::Success));
    let service = GroupSessionConnectionServiceImpl::new(sessions, tokens.clone());

    let result = service
        .issue_token(IssueGroupSessionConnectionToken {
            caller: human_caller(),
            session_id: "missing-session".into(),
        })
        .await;

    assert!(matches!(
        result,
        Err(GroupSessionConnectionError::Application(ApplicationError::NotFound { .. }))
    ));
    assert!(tokens.issued_scopes().is_empty());
}

#[tokio::test]
async fn maps_token_failures_to_closed_application_errors() {
    let sessions = Arc::new(FakeSessionService::new(SessionMode::Success));
    let unavailable = GroupSessionConnectionServiceImpl::new(
        sessions.clone(),
        Arc::new(FakeTokenPort::new(TokenMode::Unavailable)),
    );
    let internal = GroupSessionConnectionServiceImpl::new(
        sessions,
        Arc::new(FakeTokenPort::new(TokenMode::Internal)),
    );

    let command = || IssueGroupSessionConnectionToken {
        caller: human_caller(),
        session_id: "session-a".into(),
    };
    assert!(matches!(
        unavailable.issue_token(command()).await,
        Err(GroupSessionConnectionError::TokenServiceUnavailable)
    ));
    assert!(matches!(
        internal.issue_token(command()).await,
        Err(GroupSessionConnectionError::Internal(_))
    ));
}

#[tokio::test]
async fn verifies_binding_and_maps_invalid_connection_tokens() {
    let sessions = Arc::new(FakeSessionService::new(SessionMode::Success));
    let valid = GroupSessionConnectionServiceImpl::new(
        sessions.clone(),
        Arc::new(FakeTokenPort::new(TokenMode::Success)),
    );
    let invalid = GroupSessionConnectionServiceImpl::new(
        sessions,
        Arc::new(FakeTokenPort::new(TokenMode::Invalid)),
    );

    let binding = valid
        .verify_token(VerifyGroupSessionConnectionToken {
            token: "valid".into(),
        })
        .await;
    assert!(matches!(
        binding,
        Ok(value)
            if value.tenant.as_deref() == Some("tenant-a")
                && value.user_id == "user-a"
                && value.group_id == "group-a"
                && value.session_id == "session-a"
    ));
    assert!(matches!(
        invalid
            .verify_token(VerifyGroupSessionConnectionToken {
                token: "invalid".into(),
            })
            .await,
        Err(GroupSessionConnectionError::InvalidConnectionToken)
    ));
}
