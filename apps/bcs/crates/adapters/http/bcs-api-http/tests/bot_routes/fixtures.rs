//! Shared fixtures for the bot / invite-code route tests.
//! Split from the former 1_292-line top-level target (2026-11 V1 API auth
//! plan, Task 7): fixtures here, behavioral read/write suites beside.

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request};
use bcs_api_http::{ApiState, CredentialKind, PrincipalVerificationError, PrincipalVerifier, VerifiedRequestIdentity, router};
use bcs_service_api::application::v1::*;
use serde_json::{Value, json};

pub(crate) struct HeaderVerifier;

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(VerifiedRequestIdentity::pin(AuthenticatedCaller {
                tenant: Some("tenant-1".into()),
                user: Some(AuthenticatedUserIdentity {
                    id: "staff-1".to_string(),
                    username: "staff-1".to_string(),
                    display_name: None,
                    full_name: None,
                }),
                bot: None,
                app: None,
                access_key: None,
            }, "test-double", CredentialKind::GatewayPrincipalHeader))
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

#[derive(Default)]
pub(crate) struct NoopAuthService;

#[async_trait]
impl AuthService for NoopAuthService {
    async fn login_urls(
        &self,
        _request: BuildLoginUrls,
    ) -> AuthFlowReply<AuthProviderUrlList> {
        AuthFlowReply {
            result: Err(ApplicationError::not_found(
                "auth_not_configured",
                "OAuth is not configured",
            )),
            cookie_changes: Vec::new(),
        }
    }

    async fn complete_login(
        &self,
        _request: CompleteOAuthLogin,
    ) -> AuthFlowReply<AuthRedirectLocation> {
        AuthFlowReply {
            result: Err(ApplicationError::not_found(
                "auth_not_configured",
                "OAuth is not configured",
            )),
            cookie_changes: Vec::new(),
        }
    }

    async fn current_user(
        &self,
        _request: AuthenticatedUserQuery,
    ) -> Result<AuthUserInfo, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }

    async fn refresh_session(
        &self,
        _session: PresentedSession,
    ) -> AuthFlowReply<SessionRenewalData> {
        AuthFlowReply {
            result: Err(ApplicationError::Unauthenticated),
            cookie_changes: Vec::new(),
        }
    }

    async fn logout(
        &self,
        _session: Option<PresentedSession>,
    ) -> AuthFlowReply<LogoutData> {
        AuthFlowReply {
            result: Err(ApplicationError::internal("not configured")),
            cookie_changes: Vec::new(),
        }
    }
}

#[derive(Default)]
pub(crate) struct NoopCollaborationTemplateService;

#[async_trait]
impl CollaborationTemplateService for NoopCollaborationTemplateService {
    async fn list_templates(
        &self,
        _command: ListCollaborationTemplates,
    ) -> Result<CollaborationTemplateListResponse, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }

    async fn get_template(
        &self,
        _query: GetCollaborationTemplate,
    ) -> Result<CollaborationTemplateDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

#[derive(Default)]
pub(crate) struct FakeBotService {
    pub(crate) candidates: Mutex<Option<ListBotCandidates>>,
    pub(crate) eligible_candidates: Mutex<Option<ListBotCandidates>>,
    pub(crate) candidate_searches: Mutex<Vec<SearchBotCandidates>>,
    pub(crate) query: Mutex<Option<QueryBots>>,
    pub(crate) get: Mutex<Option<GetBot>>,
    pub(crate) update: Mutex<Option<UpdateBot>>,
    pub(crate) mine: Mutex<Option<ListMyBots>>,
    pub(crate) mine_items: Mutex<Option<Vec<Bot>>>,
}

#[derive(Default)]
pub(crate) struct FakeInviteCodeService {
    pub(crate) allow_access: bool,
    pub(crate) ensure_access_calls: Mutex<Vec<AuthenticatedCaller>>,
    pub(crate) init_invite_codes_calls: Mutex<Vec<InitInviteCodes>>,
    pub(crate) claim_public_invite_code_calls: Mutex<Vec<ClaimPublicInviteCode>>,
    pub(crate) claim_limit_reached: bool,
    pub(crate) bind_invite_code_calls: Mutex<Vec<BindInviteCode>>,
    pub(crate) get_my_invite_code_binding_calls: Mutex<Vec<GetMyInviteCodeBinding>>,
}

#[async_trait]
impl InviteCodeService for FakeInviteCodeService {
    async fn init_invite_codes(
        &self,
        command: InitInviteCodes,
    ) -> Result<InitInviteCodesResult, ApplicationError> {
        self.init_invite_codes_calls
            .lock()
            .expect("init invite codes lock")
            .push(command);
        Ok(InitInviteCodesResult { codes: vec!["ABC123".to_string()] })
    }

    async fn claim_public_invite_code(
        &self,
        command: ClaimPublicInviteCode,
    ) -> Result<ClaimPublicInviteCodeResult, ApplicationError> {
        self.claim_public_invite_code_calls
            .lock()
            .expect("claim public invite code lock")
            .push(command);
        if self.claim_limit_reached {
            return Err(ApplicationError::invite_code_claim_limit_reached(
                "maximum public invite-code claim count has been reached",
            ));
        }
        Ok(ClaimPublicInviteCodeResult {
            invite_code: "ABC123".to_string(),
        })
    }

    async fn bind_invite_code(
        &self,
        command: BindInviteCode,
    ) -> Result<BindInviteCodeResult, ApplicationError> {
        self.bind_invite_code_calls
            .lock()
            .expect("bind invite code lock")
            .push(command);
        Ok(BindInviteCodeResult { bound: true, bound_at: 123 })
    }

    async fn get_my_invite_code_binding(
        &self,
        command: GetMyInviteCodeBinding,
    ) -> Result<InviteCodeBindingView, ApplicationError> {
        self.get_my_invite_code_binding_calls
            .lock()
            .expect("get my invite code binding lock")
            .push(command);
        Ok(InviteCodeBindingView { bound: true, bound_at: Some(456) })
    }

    async fn ensure_invite_code_access(
        &self,
        caller: &AuthenticatedCaller,
    ) -> Result<(), ApplicationError> {
        self.ensure_access_calls
            .lock()
            .expect("ensure access lock")
            .push(caller.clone());
        if self.allow_access {
            Ok(())
        } else {
            Err(ApplicationError::invite_code_required("invite code required"))
        }
    }
}

#[async_trait]
impl BotService for FakeBotService {
    async fn list_candidates(
        &self,
        command: ListBotCandidates,
    ) -> Result<Page<BotCandidate>, ApplicationError> {
        *self.candidates.lock().expect("candidates lock") = Some(command);
        Ok(Page {
            items: vec![BotCandidate {
                bot: physical_bot(),
                is_friend: true,
            }],
            total: 1,
            offset: 5,
            limit: 10,
        })
    }

    async fn list_eligible_candidates(
        &self,
        command: ListBotCandidates,
    ) -> Result<Page<BotCandidate>, ApplicationError> {
        *self
            .eligible_candidates
            .lock()
            .expect("eligible candidates lock") = Some(command);
        Ok(Page {
            items: vec![BotCandidate {
                bot: physical_bot(),
                is_friend: true,
            }],
            total: 1,
            offset: 5,
            limit: 10,
        })
    }

    async fn search_candidates(
        &self,
        command: SearchBotCandidates,
    ) -> Result<BotCandidateSearchResult, ApplicationError> {
        let query = command.query.clone();
        self.candidate_searches
            .lock()
            .expect("candidate searches lock")
            .push(command);
        if query.as_deref().is_none_or(|query| query.trim().is_empty()) {
            return Ok(BotCandidateSearchResult {
                items: Vec::new(),
                search_mode: BotCandidateSearchMode::EmptyQuery,
            });
        }
        let is_fallback = query.as_deref() == Some("fallback");
        Ok(BotCandidateSearchResult {
            items: vec![BotCandidateSearchItem {
                bot: physical_bot(),
                is_friend: true,
                tags: std::collections::BTreeMap::from([(
                    "specialty".to_string(),
                    json!("planning"),
                )]),
                score: if is_fallback { None } else { Some(0.0) },
                short_profile: if is_fallback {
                    None
                } else {
                    Some("Planning specialist".to_string())
                },
            }],
            search_mode: if is_fallback {
                BotCandidateSearchMode::NameFallback
            } else {
                BotCandidateSearchMode::Semantic
            },
        })
    }

    async fn query(&self, command: QueryBots) -> Result<Vec<Bot>, ApplicationError> {
        *self.query.lock().expect("query lock") = Some(command);
        Ok(vec![Bot::Physical(physical_bot())])
    }

    async fn get(&self, query: GetBot) -> Result<Bot, ApplicationError> {
        *self.get.lock().expect("get lock") = Some(query);
        Ok(Bot::Physical(physical_bot()))
    }

    async fn update(&self, command: UpdateBot) -> Result<Bot, ApplicationError> {
        *self.update.lock().expect("update lock") = Some(command);
        Ok(Bot::Physical(physical_bot()))
    }

    async fn list_mine(&self, command: ListMyBots) -> Result<Page<Bot>, ApplicationError> {
        *self.mine.lock().expect("mine lock") = Some(command);
        let items = self
            .mine_items
            .lock()
            .expect("mine items lock")
            .clone()
            .unwrap_or_else(|| vec![Bot::Human(human_bot())]);
        Ok(Page {
            total: items.len() as u64,
            items,
            offset: 2,
            limit: 3,
        })
    }
}

pub(crate) struct NoopGroupService;

#[async_trait]
impl GroupService for NoopGroupService {
    async fn list_groups(
        &self,
        _: ListGroups,
    ) -> Result<Page<GroupSummary>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn create(&self, _: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn get(&self, _: GetGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update(&self, _: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete(&self, _: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn add_participant(
        &self,
        _: AddGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update_participant(
        &self,
        _: UpdateGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete_participant(
        &self,
        _: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

pub(crate) struct NoopSessionService;

#[async_trait]
impl SessionService for NoopSessionService {
    async fn create(&self, _: CreateSession) -> Result<CreateSessionOutcome, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn list(&self, _: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn get(&self, _: GetSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update(&self, _: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete(&self, _: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn complete(
        &self,
        _: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn collect(
        &self,
        _: CollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn uncollect(
        &self,
        _: UncollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn add_participant(
        &self,
        _: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn update_participant(
        &self,
        _: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete_participant(
        &self,
        _: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

pub(crate) struct NoopMessageService;

#[async_trait]
impl SessionMessageService for NoopMessageService {
    async fn list(
        &self,
        _: ListSessionMessages,
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

pub(crate) struct NoopInvitationService;

#[async_trait]
impl InvitationService for NoopInvitationService {
    async fn create_group_invitation(
        &self,
        _: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn create_session_invitation(
        &self,
        _: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn accept_invitation(
        &self,
        _: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

pub(crate) struct NoopFriendshipService;

#[async_trait]
impl FriendshipService for NoopFriendshipService {
    async fn list_bot_friendships(
        &self,
        _: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn delete_bot_friendship(
        &self,
        _: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn create_bot_friend_request(
        &self,
        _: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn list_bot_friend_requests(
        &self,
        _: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn accept_friend_request(
        &self,
        _: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
    async fn reject_friend_request(
        &self,
        _: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

pub(crate) struct NoopRegisterService;

#[async_trait]
impl RegisterService for NoopRegisterService {
    async fn issue_register_token(
        &self,
        _command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        Err(ApplicationError::internal("register service is a noop in this test"))
    }

    async fn register_bot(
        &self,
        _command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        Err(ApplicationError::internal("register service is a noop in this test"))
    }
}

pub(crate) fn test_router(service: Arc<FakeBotService>) -> axum::Router {
    invite_code_test_router(service, Arc::new(FakeInviteCodeService::default()), false)
}

pub(crate) fn invite_code_test_router(
    service: Arc<FakeBotService>,
    invite_code_service: Arc<FakeInviteCodeService>,
    gate_enabled: bool,
) -> axum::Router {
    router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopRegisterService),
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier),
        )
        .with_invite_code_service(invite_code_service)
        .with_invite_code_gate_enabled(gate_enabled)
        .with_public_invite_code_claim_enabled(true)
        .with_bot_service(service),
    )
}

pub(crate) fn physical_bot() -> PhysicalBot {
    PhysicalBot {
        bot_id: "bot-1".to_string(),
        kind: BotKind::Bot,
        name: "Bot One".to_string(),
        avatar_url: Some("https://example.com/bot.png".into()),
        visibility: BotVisibility::Public,
        status: BotStatus::Online,
        env: "dev".to_string(),
        created_by: Some("staff-1".to_string()),
        descriptor: BotDescriptor {
            summary: "summary".to_string(),
            domains: vec![],
            skills: vec![],
            scopes: vec![],
        },
        reachability: BotReachability::Reachable,
        provider: None,
        agent_code: Some("agent-code".to_string()),
        task_claim_mode: false,
        task_dream_mode: false,
        user_visibility: UserVisibility::Protected,
        friend_ext: Default::default(),
        friend_check_in_strategy: FriendCheckInStrategy::Approval,
        created_at: 1,
        updated_at: 2,
        access_relation: None,
    }
}

pub(crate) fn human_bot() -> HumanBot {
    HumanBot {
        bot_id: "human_staff-1".to_string(),
        kind: BotKind::Human,
        name: "Human".to_string(),
        avatar_url: None,
        visibility: BotVisibility::Protected,
        status: BotStatus::Online,
        env: "dev".to_string(),
        created_by: Some("staff-1".to_string()),
        user_visibility: UserVisibility::Protected,
        friend_ext: Default::default(),
        friend_check_in_strategy: FriendCheckInStrategy::Approval,
        created_at: 1,
        updated_at: 2,
        access_relation: None,
    }
}

pub(crate) fn request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-bot")
        .body(Body::from(body.to_string()))
        .expect("request")
}

pub(crate) fn anonymous_request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-request-id", "request-anonymous")
        .body(Body::from(body.to_string()))
        .expect("anonymous request")
}

pub(crate) async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}
