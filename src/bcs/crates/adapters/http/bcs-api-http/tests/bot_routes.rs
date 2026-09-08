#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_config_api::ManifestConfig;
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::*;
use bcs_test_support::{NoopChannelService, NoopCollaborationRuntimeService};
use serde_json::{Value, json};
use tower::ServiceExt;

struct HeaderVerifier;

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(AuthenticatedCaller {
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
            })
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

#[derive(Default)]
struct NoopAuthService;

#[async_trait]
impl AuthService for NoopAuthService {
    async fn login_urls(
        &self,
        _request: BuildLoginUrls,
    ) -> Result<AuthProviderUrlList, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }

    async fn complete_login(
        &self,
        _request: CompleteOAuthLogin,
    ) -> Result<AuthRedirect, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }

    async fn current_user(
        &self,
        _request: ReadCurrentUser,
    ) -> Result<AuthUserInfo, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }

    async fn refresh_session(
        &self,
        _request: RefreshSession,
    ) -> Result<SessionRenewal, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }

    async fn logout(&self, _request: LogoutSession) -> Result<LogoutResult, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

#[derive(Default)]
struct NoopCollaborationTemplateService;

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
struct FakeBotService {
    candidates: Mutex<Option<ListBotCandidates>>,
    eligible_candidates: Mutex<Option<ListBotCandidates>>,
    candidate_searches: Mutex<Vec<SearchBotCandidates>>,
    query: Mutex<Option<QueryBots>>,
    get: Mutex<Option<GetBot>>,
    update: Mutex<Option<UpdateBot>>,
    mine: Mutex<Option<ListMyBots>>,
}

#[derive(Default)]
struct FakeInviteCodeService {
    allow_access: bool,
    ensure_access_calls: Mutex<Vec<AuthenticatedCaller>>,
    init_invite_codes_calls: Mutex<Vec<InitInviteCodes>>,
    claim_public_invite_code_calls: Mutex<Vec<ClaimPublicInviteCode>>,
    bind_invite_code_calls: Mutex<Vec<BindInviteCode>>,
    get_my_invite_code_binding_calls: Mutex<Vec<GetMyInviteCodeBinding>>,
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
                is_friend: false,
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
        Ok(Page {
            items: vec![Bot::Human(human_bot())],
            total: 1,
            offset: 2,
            limit: 3,
        })
    }
}

struct NoopGroupService;

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

struct NoopSessionService;

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

struct NoopMessageService;

#[async_trait]
impl SessionMessageService for NoopMessageService {
    async fn list(
        &self,
        _: ListSessionMessages,
    ) -> Result<Vec<bcs_service_api::GroupMessage>, ApplicationError> {
        Err(ApplicationError::internal("not configured"))
    }
}

struct NoopInvitationService;

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

struct NoopFriendshipService;

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

struct NoopRegisterService;

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

fn test_router(service: Arc<FakeBotService>) -> axum::Router {
    invite_code_test_router(service, Arc::new(FakeInviteCodeService::default()), false)
}

fn invite_code_test_router(
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

#[test]
fn api_state_builder_methods_attach_optional_services() {
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier),
    )
    .with_auth_service(Arc::new(NoopAuthService), "http://127.0.0.1/openapi/v1/auth".to_string())
    .with_channel_service(Arc::new(NoopChannelService))
    .with_collaboration_runtime_service(Arc::new(NoopCollaborationRuntimeService))
    .with_collaboration_template_service(Arc::new(NoopCollaborationTemplateService))
    .with_manifest_config("test".to_string(), ManifestConfig::default());

    assert!(state.auth_service.is_some());
    assert_eq!(state.auth_public_base_url, "http://127.0.0.1/openapi/v1/auth");
    assert!(state.channel_service.is_some());
    assert!(state.collaboration_runtime_service.is_some());
    assert!(state.collaboration_template_service.is_some());
    assert_eq!(state.manifest_env, "test");
    assert_eq!(state.manifest, ManifestConfig::default());
}

#[tokio::test]
async fn all_seven_bot_routes_forward_verified_human_and_contract_inputs() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());

    let candidates = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/human_staff-1/candidates?purpose=collaboration&name=planner&offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("candidates response");
    let candidates_status = candidates.status();
    if candidates_status != StatusCode::OK {
        let body = to_bytes(candidates.into_body(), usize::MAX)
            .await
            .expect("candidates body");
        panic!(
            "candidates route returned {candidates_status}: {}",
            String::from_utf8_lossy(&body)
        );
    }
    assert_eq!(candidates_status, StatusCode::OK);
    assert_eq!(
        response_json(candidates).await["data"]["items"][0]["bot"]["kind"],
        "bot"
    );

    let eligible_candidates = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/human_staff-1/eligible-candidates?purpose=collaboration&name=planner&offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("eligible candidates response");
    assert_eq!(eligible_candidates.status(), StatusCode::OK);

    let searched = app
        .clone()
        .oneshot(request(
            "GET",
            "/api/v1/collaboration/bots/human_staff-1/candidates/search?q=planning%20help&purpose=collaboration",
            Value::Null,
        ))
        .await
        .expect("candidate search response");
    assert_eq!(searched.status(), StatusCode::OK);
    let searched_body = response_json(searched).await;
    assert_eq!(
        searched_body["data"],
        json!({
            "items": [{
                "bot": physical_bot(),
                "is_friend": true,
                "tags": {"specialty": "planning"},
                "score": 0.0,
                "short_profile": "Planning specialist"
            }],
            "search_mode": "semantic"
        })
    );
    assert!(searched_body["data"].get("context").is_none());
    assert!(!searched_body.to_string().contains("recommend_response"));

    let queried = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/bots/query",
            json!({"bot_ids": ["bot-2", "bot-1", "bot-2"]}),
        ))
        .await
        .expect("query response");
    assert_eq!(queried.status(), StatusCode::OK);

    let got = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-1",
            Value::Null,
        ))
        .await
        .expect("get response");
    assert_eq!(got.status(), StatusCode::OK);

    let updated = app
        .clone()
        .oneshot(request(
            "PATCH",
            "/openapi/v1/collaboration/bots/bot-1",
            json!({
                "name": "Renamed",
                "visibility": "protected",
                "status": "hidden",
                "descriptor": {"domains": [], "skills": [{"name": "plan"}]},
                "user_visibility": "private",
                "friend_ext": {"no_check_scope_friend_deps": ["dep-1"]},
                "friend_check_in_strategy": "DEPT_FREE"
            }),
        ))
        .await
        .expect("update response");
    assert_eq!(updated.status(), StatusCode::OK);

    let mine = app
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/mine?kind=human&name=vin&status=online&reachability=unreachable&offset=2&limit=3",
            Value::Null,
        ))
        .await
        .expect("mine response");
    assert_eq!(mine.status(), StatusCode::OK);
    let mine_body = response_json(mine).await;
    assert_eq!(mine_body["data"]["items"][0]["kind"], "human");
    assert_eq!(mine_body["data"]["items"][0]["user_visibility"], "protected");
    assert_eq!(mine_body["data"]["items"][0]["friend_ext"], json!({}));
    assert_eq!(
        mine_body["data"]["items"][0]["friend_check_in_strategy"],
        "APPROVAL"
    );

    let candidates = service.candidates.lock().expect("candidates lock");
    let candidates = candidates.as_ref().expect("candidates command");
    assert_eq!(
        candidates.caller.user.as_ref().map(|user| user.id.as_str()),
        Some("staff-1")
    );
    assert_eq!(candidates.bot_id, "human_staff-1");
    assert_eq!(candidates.purpose, BotCandidatePurpose::Collaboration);
    assert_eq!(candidates.name.as_deref(), Some("planner"));
    assert_eq!((candidates.offset, candidates.limit), (5, 10));
    let eligible_candidates = service
        .eligible_candidates
        .lock()
        .expect("eligible candidates lock");
    let eligible_candidates = eligible_candidates
        .as_ref()
        .expect("eligible candidates command");
    assert_eq!(
        eligible_candidates.caller.user.as_ref().map(|user| user.id.as_str()),
        Some("staff-1")
    );
    assert_eq!(eligible_candidates.bot_id, "human_staff-1");
    assert_eq!(eligible_candidates.purpose, BotCandidatePurpose::Collaboration);
    assert_eq!(eligible_candidates.name.as_deref(), Some("planner"));
    assert_eq!((eligible_candidates.offset, eligible_candidates.limit), (5, 10));
    let searches = service
        .candidate_searches
        .lock()
        .expect("candidate searches lock");
    let search = searches.first().expect("candidate search command");
    assert_eq!(
        search.caller.user.as_ref().map(|user| user.id.as_str()),
        Some("staff-1")
    );
    assert_eq!(search.bot_id, "human_staff-1");
    assert_eq!(search.purpose, BotCandidatePurpose::Collaboration);
    assert_eq!(search.query.as_deref(), Some("planning help"));

    assert_eq!(
        service
            .query
            .lock()
            .expect("query lock")
            .as_ref()
            .expect("query command")
            .bot_ids,
        vec!["bot-2", "bot-1", "bot-2"]
    );
    let update = service.update.lock().expect("update lock");
    let update = update.as_ref().expect("update command");
    assert_eq!(update.patch.visibility, Some(BotVisibility::Protected));
    assert_eq!(update.patch.status, Some(BotStatus::Hidden));
    assert_eq!(update.patch.user_visibility, Some(UserVisibility::Private));
    assert_eq!(
        update.patch.friend_check_in_strategy,
        Some(FriendCheckInStrategy::DeptFree)
    );
    assert_eq!(
        update.patch.friend_ext.as_ref().expect("friend ext")["no_check_scope_friend_deps"],
        json!(["dep-1"])
    );
    assert_eq!(
        update
            .patch
            .descriptor
            .as_ref()
            .and_then(|d| d.domains.as_ref()),
        Some(&vec![])
    );
    let mine = service.mine.lock().expect("mine lock");
    let mine = mine.as_ref().expect("mine command");
    assert_eq!(mine.kind, Some(BotKind::Human));
    assert_eq!(mine.reachability, Some(BotReachability::Unreachable));
}

#[tokio::test]
async fn invite_code_routes_forward_through_gate_and_validate_payloads() {
    let bot_service = Arc::new(FakeBotService::default());
    let invite_code_service = Arc::new(FakeInviteCodeService { allow_access: true, ..Default::default() });
    let app = invite_code_test_router(bot_service.clone(), invite_code_service.clone(), true);

    let claim = app
        .clone()
        .oneshot(anonymous_request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/claim",
            Value::Null,
        ))
        .await
        .expect("public claim response");
    assert_eq!(claim.status(), StatusCode::CREATED);
    assert_eq!(claim.headers()["cache-control"], "no-store");
    assert_eq!(response_json(claim).await["data"], json!({"invite_code": "ABC123"}));

    let bind = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/bind",
            json!({"code": "ABC123"}),
        ))
        .await
        .expect("bind response");
    assert_eq!(bind.status(), StatusCode::OK);
    assert_eq!(response_json(bind).await["data"], json!({"bound": true, "bound_at": 123}));

    let me = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/invite-codes/me",
            Value::Null,
        ))
        .await
        .expect("my binding response");
    assert_eq!(me.status(), StatusCode::OK);
    assert_eq!(response_json(me).await["data"], json!({"bound": true, "bound_at": 456}));

    let init = app
        .clone()
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/invite-codes/init",
            json!({"count": 3}),
        ))
        .await
        .expect("init response");
    assert_eq!(init.status(), StatusCode::OK);
    assert_eq!(response_json(init).await["data"], json!({"codes": ["ABC123"]}));

    let gated_candidates = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/human_staff-1/candidates?purpose=collaboration&name=planner&offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("gated candidates response");
    assert_eq!(gated_candidates.status(), StatusCode::OK);
    assert_eq!(response_json(gated_candidates).await["data"]["items"][0]["bot"]["bot_id"], "bot-1");

    let empty_bind = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/bind",
            json!({"code": "   "}),
        ))
        .await
        .expect("empty bind response");
    assert_eq!(empty_bind.status(), StatusCode::BAD_REQUEST);
    assert_eq!(response_json(empty_bind).await["data"]["error_code"], "invalid_request");

    let zero_init = app
        .clone()
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/invite-codes/init",
            json!({"count": 0}),
        ))
        .await
        .expect("zero init response");
    assert_eq!(zero_init.status(), StatusCode::BAD_REQUEST);
    assert_eq!(response_json(zero_init).await["data"]["error_code"], "invalid_request");

    assert_eq!(
        invite_code_service
            .ensure_access_calls
            .lock()
            .expect("ensure access lock")
            .len(),
        1,
        "protected routes should run the invite-code gate exactly once"
    );
    assert_eq!(
        invite_code_service
            .claim_public_invite_code_calls
            .lock()
            .expect("claim public invite code lock")
            .len(),
        1
    );
    assert_eq!(
        invite_code_service
            .bind_invite_code_calls
            .lock()
            .expect("bind invite code lock")
            .len(),
        1
    );
    assert_eq!(
        invite_code_service
            .get_my_invite_code_binding_calls
            .lock()
            .expect("get my invite code binding lock")
            .len(),
        1
    );
    assert_eq!(
        invite_code_service
            .init_invite_codes_calls
            .lock()
            .expect("init invite codes lock")
            .len(),
        1
    );
    assert!(
        bot_service
            .candidates
            .lock()
            .expect("candidates lock")
            .is_some(),
        "protected routes should flow through the invite-code gate when access is granted"
    );
}

#[tokio::test]
async fn public_invite_code_claim_returns_not_found_when_disabled() {
    let invite_code_service = Arc::new(FakeInviteCodeService::default());
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier),
    )
    .with_invite_code_service(invite_code_service.clone());
    let response = router(state)
        .oneshot(anonymous_request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/claim",
            Value::Null,
        ))
        .await
        .expect("disabled public claim response");

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert!(invite_code_service
        .claim_public_invite_code_calls
        .lock()
        .expect("claim public invite code lock")
        .is_empty());
}

#[tokio::test]
async fn invite_code_routes_return_internal_when_the_service_is_missing() {
    let state = ApiState::new(
        Arc::new(NoopGroupService),
        Arc::new(NoopSessionService),
        Arc::new(NoopMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopRegisterService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier),
    )
    .with_invite_code_gate_enabled(true);

    let app = router(state);

    let bind = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/invite-codes/bind",
            json!({"code": "ABC123"}),
        ))
        .await
        .expect("bind without invite-code service");
    assert_eq!(bind.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(bind).await["data"]["error_code"], "internal_error");

    let me = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/invite-codes/me",
            Value::Null,
        ))
        .await
        .expect("get binding without invite-code service");
    assert_eq!(me.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(me).await["data"]["error_code"], "internal_error");

    let init = app
        .oneshot(request(
            "POST",
            "/api/v1/collaboration/invite-codes/init",
            json!({"count": 3}),
        ))
        .await
        .expect("init without invite-code service");
    assert_eq!(init.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(init).await["data"]["error_code"], "internal_error");
}

#[tokio::test]
async fn invite_code_gate_rejects_protected_routes_when_access_is_not_granted() {
    let bot_service = Arc::new(FakeBotService::default());
    let invite_code_service = Arc::new(FakeInviteCodeService { allow_access: false, ..Default::default() });
    let app = invite_code_test_router(bot_service.clone(), invite_code_service.clone(), true);

    let response = app
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/human_staff-1/candidates?purpose=collaboration&name=planner&offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("protected route response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(response_json(response).await["data"]["error_code"], "invite_code_required");
    assert_eq!(
        invite_code_service
            .ensure_access_calls
            .lock()
            .expect("ensure access lock")
            .len(),
        1
    );
    assert!(
        bot_service
            .candidates
            .lock()
            .expect("candidates lock")
            .is_none(),
        "gate rejection must short-circuit the bot service"
    );
}

#[tokio::test]
async fn candidate_search_accepts_omitted_empty_and_whitespace_queries() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());

    for uri in [
        "/api/v1/collaboration/bots/human_staff-1/candidates/search",
        "/api/v1/collaboration/bots/human_staff-1/candidates/search?q=",
        "/api/v1/collaboration/bots/human_staff-1/candidates/search?q=%20%20%20",
    ] {
        let response = app
            .clone()
            .oneshot(request("GET", uri, Value::Null))
            .await
            .expect("empty candidate search response");
        assert_eq!(response.status(), StatusCode::OK, "{uri}");
        assert_eq!(
            response_json(response).await["data"],
            json!({"items": [], "search_mode": "empty_query"}),
            "{uri}"
        );
    }

    let searches = service
        .candidate_searches
        .lock()
        .expect("candidate searches lock");
    assert_eq!(searches.len(), 3);
    assert_eq!(searches[0].query, None);
    assert_eq!(searches[1].query.as_deref(), Some(""));
    assert_eq!(searches[2].query.as_deref(), Some("   "));
}

#[tokio::test]
async fn candidate_search_serializes_name_fallback_without_a_score() {
    let service = Arc::new(FakeBotService::default());
    let response = test_router(service)
        .oneshot(request(
            "GET",
            "/api/v1/collaboration/bots/human_staff-1/candidates/search?q=fallback",
            Value::Null,
        ))
        .await
        .expect("fallback candidate search response");

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response_json(response).await["data"],
        json!({
            "items": [{
                "bot": physical_bot(),
                "is_friend": true,
                "tags": {"specialty": "planning"}
            }],
            "search_mode": "name_fallback"
        })
    );
}

#[tokio::test]
async fn bot_routes_reject_unknown_request_fields_and_missing_principal() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());
    let unknown = app
        .clone()
        .oneshot(request(
            "PATCH",
            "/openapi/v1/collaboration/bots/bot-1",
            json!({"name": "Bot", "created_by": "forged"}),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(unknown.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        response_json(unknown).await["data"]["error_code"],
        "invalid_request"
    );

    let unknown_search_query = app
        .clone()
        .oneshot(request(
            "GET",
            "/api/v1/collaboration/bots/bot-1/candidates/search?q=planning&limit=10",
            Value::Null,
        ))
        .await
        .expect("unknown search query response");
    assert_eq!(unknown_search_query.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        response_json(unknown_search_query).await["data"]["error_code"],
        "invalid_request"
    );
    assert!(
        service
            .candidate_searches
            .lock()
            .expect("candidate searches lock")
            .is_empty()
    );

    let missing = app
        .oneshot(
            Request::builder()
                .uri("/openapi/v1/collaboration/bots/bot-1")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("missing principal response");
    assert_eq!(missing.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn bot_routes_reject_invalid_queries_and_missing_service() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());

    for uri in [
        "/openapi/v1/collaboration/bots/human_staff-1/candidates?purpose=collaboration&name=planner&offset=5&limit=10&unexpected=1",
        "/openapi/v1/collaboration/bots/human_staff-1/eligible-candidates?purpose=collaboration&name=planner&offset=5&limit=10&unexpected=1",
        "/openapi/v1/collaboration/bots/mine?kind=human&name=vin&status=online&reachability=unreachable&offset=2&limit=3&unexpected=1",
    ] {
        let response = app
            .clone()
            .oneshot(request("GET", uri, Value::Null))
            .await
            .expect("invalid query response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{uri}");
        assert_eq!(response_json(response).await["data"]["error_code"], "invalid_request", "{uri}");
    }

    assert!(service.candidates.lock().expect("candidates lock").is_none());
    assert!(service.eligible_candidates.lock().expect("eligible candidates lock").is_none());
    assert!(service.mine.lock().expect("mine lock").is_none());

    let missing_service_app = router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopRegisterService),
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier),
        )
        .with_invite_code_gate_enabled(false),
    );

    let missing_service = missing_service_app
        .oneshot(request(
            "GET",
            "/openapi/v1/collaboration/bots/bot-1",
            Value::Null,
        ))
        .await
        .expect("missing bot service response");
    assert_eq!(missing_service.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(response_json(missing_service).await["data"]["error_code"], "internal_error");
}

#[tokio::test]
async fn previous_bcn_path_families_are_not_mounted() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());

    for uri in [
        "/openapi/v1/bots/bot-1",
        "/openapi/v1/bots/mine",
        "/openapi/v1/bots/collaboration/bot-1",
        "/openapi/v1/groups",
        "/openapi/v1/group-sessions/session-1",
        "/openapi/v1/friend-requests/request-1/accept",
        "/openapi/v1/invitations/token-1/accept",
        "/api/v1/collaboration/bots/bot-1/candidates",
    ] {
        let response = app
            .clone()
            .oneshot(request("GET", uri, Value::Null))
            .await
            .expect("legacy path response");
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{uri}");
    }

    assert!(service.get.lock().expect("get lock").is_none());
    assert!(service.mine.lock().expect("mine lock").is_none());
}

fn physical_bot() -> PhysicalBot {
    PhysicalBot {
        bot_id: "bot-1".to_string(),
        kind: BotKind::Bot,
        name: "Bot One".to_string(),
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
    }
}

fn human_bot() -> HumanBot {
    HumanBot {
        bot_id: "human_staff-1".to_string(),
        kind: BotKind::Human,
        name: "Human".to_string(),
        visibility: BotVisibility::Protected,
        status: BotStatus::Online,
        env: "dev".to_string(),
        created_by: Some("staff-1".to_string()),
        user_visibility: UserVisibility::Protected,
        friend_ext: Default::default(),
        friend_check_in_strategy: FriendCheckInStrategy::Approval,
        created_at: 1,
        updated_at: 2,
    }
}

fn request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-bot")
        .body(Body::from(body.to_string()))
        .expect("request")
}

fn anonymous_request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-request-id", "request-anonymous")
        .body(Body::from(body.to_string()))
        .expect("anonymous request")
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}
