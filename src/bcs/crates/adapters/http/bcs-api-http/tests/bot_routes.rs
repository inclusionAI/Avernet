#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::BTreeSet;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::*;
use serde_json::{Value, json};
use tower::ServiceExt;

struct HeaderVerifier;

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(&self, headers: &HeaderMap) -> Result<Principal, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(Principal::human(
                AuthenticatedUser {
                    id: "staff-1".to_string(),
                    username: "staff-1".to_string(),
                    display_name: None,
                    full_name: None,
                },
                "tenant-1",
                BTreeSet::new(),
            ))
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

#[derive(Default)]
struct FakeBotService {
    candidates: Mutex<Option<ListBotCandidates>>,
    query: Mutex<Option<QueryBots>>,
    get: Mutex<Option<GetBot>>,
    update: Mutex<Option<UpdateBot>>,
    mine: Mutex<Option<ListMyBots>>,
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
    async fn list_bot_groups(
        &self,
        _: ListBotGroups,
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
    async fn list(&self, _: ListSessionMessages) -> Result<SessionMessagePage, ApplicationError> {
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

fn test_router(service: Arc<FakeBotService>) -> axum::Router {
    router(
        ApiState::new(
            Arc::new(NoopGroupService),
            Arc::new(NoopSessionService),
            Arc::new(NoopMessageService),
            Arc::new(NoopInvitationService),
            Arc::new(NoopFriendshipService),
            Arc::new(HeaderVerifier),
        )
        .with_bot_service(service),
    )
}

#[tokio::test]
async fn all_five_bot_routes_forward_verified_human_and_contract_inputs() {
    let service = Arc::new(FakeBotService::default());
    let app = test_router(service.clone());

    let candidates = app
        .clone()
        .oneshot(request(
            "GET",
            "/openapi/v1/bots/acting/candidates?purpose=collaboration&name=planner&offset=5&limit=10",
            Value::Null,
        ))
        .await
        .expect("candidates response");
    assert_eq!(candidates.status(), StatusCode::OK);
    assert_eq!(
        response_json(candidates).await["data"]["items"][0]["bot"]["kind"],
        "bot"
    );

    let queried = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/bots/query",
            json!({"bot_ids": ["bot-2", "bot-1", "bot-2"]}),
        ))
        .await
        .expect("query response");
    assert_eq!(queried.status(), StatusCode::OK);

    let got = app
        .clone()
        .oneshot(request("GET", "/openapi/v1/bots/bot-1", Value::Null))
        .await
        .expect("get response");
    assert_eq!(got.status(), StatusCode::OK);

    let updated = app
        .clone()
        .oneshot(request(
            "PATCH",
            "/openapi/v1/bots/bot-1",
            json!({
                "name": "Renamed",
                "visibility": "protected",
                "status": "hidden",
                "descriptor": {"domains": [], "skills": [{"name": "plan"}]}
            }),
        ))
        .await
        .expect("update response");
    assert_eq!(updated.status(), StatusCode::OK);

    let mine = app
        .oneshot(request(
            "GET",
            "/openapi/v1/bots/mine?kind=human&name=vin&status=online&reachability=unreachable&offset=2&limit=3",
            Value::Null,
        ))
        .await
        .expect("mine response");
    assert_eq!(mine.status(), StatusCode::OK);
    assert_eq!(
        response_json(mine).await["data"]["items"][0]["kind"],
        "human"
    );

    let candidates = service.candidates.lock().expect("candidates lock");
    let candidates = candidates.as_ref().expect("candidates command");
    assert_eq!(
        candidates
            .principal
            .authenticated_user()
            .map(|user| user.id.as_str()),
        Some("staff-1")
    );
    assert_eq!(candidates.bot_id, "acting");
    assert_eq!(candidates.purpose, BotCandidatePurpose::Collaboration);
    assert_eq!(candidates.name.as_deref(), Some("planner"));
    assert_eq!((candidates.offset, candidates.limit), (5, 10));

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
async fn bot_routes_reject_unknown_request_fields_and_missing_principal() {
    let app = test_router(Arc::new(FakeBotService::default()));
    let unknown = app
        .clone()
        .oneshot(request(
            "PATCH",
            "/openapi/v1/bots/bot-1",
            json!({"name": "Bot", "created_by": "forged"}),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(unknown.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        response_json(unknown).await["data"]["error_code"],
        "invalid_request"
    );

    let missing = app
        .oneshot(
            Request::builder()
                .uri("/openapi/v1/bots/bot-1")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("missing principal response");
    assert_eq!(missing.status(), StatusCode::UNAUTHORIZED);
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

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}
