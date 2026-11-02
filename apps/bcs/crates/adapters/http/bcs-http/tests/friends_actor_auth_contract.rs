//! Legacy friend routes distinguish Human self access from live Bot control.

#![allow(clippy::unwrap_used, reason = "contract test assertions fail fast")]

mod support;

use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::{Method, Request, StatusCode},
    response::Response,
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotCapabilities, BotDetailResult, BotUseCaseError,
    CreateFriendRequestCommand, FriendRequest, FriendRequestDecisionCommand,
    FriendRequestDirection, FriendRequestStatus, FriendService, FriendUseCaseError,
    ListFriendRequestsCommand, ListFriendsCommand, ServiceError,
    application::friends::FriendListEntry, port::bot_authority::BotAuthorityPort,
};
use bcs_services_container::Services;
use serde_json::Value;
use support::bot_use_cases::RecordingBotQueryService;
use tokio::sync::Mutex;
use tower::ServiceExt;

const HUMAN: &str = "human_327325";
const STAFF: &str = "327325";

struct RecordingFriends {
    receiver: String,
    calls: Mutex<Vec<(&'static str, String)>>,
    list_commands: Mutex<Vec<ListFriendRequestsCommand>>,
}

impl RecordingFriends {
    async fn record(&self, operation: &'static str, caller: String) {
        self.calls.lock().await.push((operation, caller));
    }
}

#[async_trait]
impl FriendService for RecordingFriends {
    async fn create_friend_request(
        &self,
        command: CreateFriendRequestCommand,
    ) -> Result<FriendRequest, FriendUseCaseError> {
        self.record("create", command.caller_actor_id.clone()).await;
        Ok(FriendRequest {
            id: "request-1".to_string(),
            from_bot: command.caller_actor_id,
            to_bot: command.to_bot,
            status: FriendRequestStatus::Pending,
            created_at: 1,
            updated_at: 1,
        })
    }

    async fn list_friend_requests(
        &self,
        command: ListFriendRequestsCommand,
    ) -> Result<Vec<FriendRequest>, FriendUseCaseError> {
        self.record("requests", command.caller_actor_id.clone())
            .await;
        self.list_commands.lock().await.push(command);
        Ok(Vec::new())
    }

    async fn accept_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        assert_eq!(
            command.request_to_bot.as_deref(),
            Some(self.receiver.as_str())
        );
        self.record("accept", command.caller_actor_id).await;
        Ok(())
    }

    async fn reject_friend_request(
        &self,
        command: FriendRequestDecisionCommand,
    ) -> Result<(), FriendUseCaseError> {
        assert_eq!(
            command.request_to_bot.as_deref(),
            Some(self.receiver.as_str())
        );
        self.record("reject", command.caller_actor_id).await;
        Ok(())
    }

    async fn friend_request_receiver(
        &self,
        _request_id: &str,
    ) -> Result<String, FriendUseCaseError> {
        Ok(self.receiver.clone())
    }

    async fn list_friends(
        &self,
        command: ListFriendsCommand,
    ) -> Result<Vec<FriendListEntry>, FriendUseCaseError> {
        assert_eq!(command.target_actor_id, self.receiver);
        self.record("friends", command.caller_actor_id).await;
        Ok(Vec::new())
    }
}

struct Fixture {
    app: axum::Router,
    friends: Arc<RecordingFriends>,
}

fn fixture(
    actor_id: &str,
    kind: ActorKind,
    staff_no: Option<&str>,
    authority: Arc<dyn BotAuthorityPort>,
) -> Fixture {
    let query = RecordingBotQueryService {
        detail_result: Ok(BotDetailResult {
            agent_card: None,
            bot_uuid: actor_id.to_string(),
            capabilities: BotCapabilities::default(),
            status: ActorStatus::Online,
            visibility: "protected".to_string(),
            owner_actor_id: None,
            created_by: None,
            actor_kind: kind,
            env: None,
            dynamic_status: Default::default(),
        }),
        ..Default::default()
    };
    fixture_with_query(actor_id, staff_no, authority, query)
}

fn fixture_with_query(
    actor_id: &str,
    staff_no: Option<&str>,
    authority: Arc<dyn BotAuthorityPort>,
    query: RecordingBotQueryService,
) -> Fixture {
    let friends = Arc::new(RecordingFriends {
        receiver: actor_id.to_string(),
        calls: Mutex::new(Vec::new()),
        list_commands: Mutex::new(Vec::new()),
    });
    let mut services = Services::noop();
    services.bot_query = Arc::new(query);
    services.friend_use_cases = friends.clone();
    services.bot_authority_hook = authority;
    let principal = AuthPrincipal {
        user_id: staff_no.map(str::to_string),
        ..Default::default()
    };
    let chain = Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]));
    Fixture {
        app: build_router(
            HttpAppState::new(services)
                .with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))),
        ),
        friends,
    }
}

fn routes(actor_id: &str) -> [(Method, String, StatusCode, &'static str); 5] {
    [
        (
            Method::GET,
            format!("/friends/requests?bot_uuid={actor_id}&direction=received"),
            StatusCode::OK,
            "requests",
        ),
        (
            Method::POST,
            "/friends/request".to_string(),
            StatusCode::CREATED,
            "create",
        ),
        (
            Method::POST,
            "/friends/requests/request-1/accept".to_string(),
            StatusCode::OK,
            "accept",
        ),
        (
            Method::POST,
            "/friends/requests/request-1/reject".to_string(),
            StatusCode::OK,
            "reject",
        ),
        (
            Method::GET,
            format!("/bots/{actor_id}/friends"),
            StatusCode::OK,
            "friends",
        ),
    ]
}

async fn send(app: &axum::Router, method: Method, uri: &str, actor_id: &str) -> Response {
    app.clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "from_bot": actor_id,
                        "to_bot": "target-bot",
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap()
}

async fn inbox(fixture: &Fixture, actor_id: &str) -> Response {
    send(
        &fixture.app,
        Method::GET,
        &format!("/friends/requests?bot_uuid={actor_id}&direction=received"),
        actor_id,
    )
    .await
}

#[tokio::test]
async fn human_self_can_list_received_requests_without_bot_authority() {
    let fixture = fixture(
        HUMAN,
        ActorKind::Human,
        Some(STAFF),
        bcs_test_support::lazy_authority_chain().hook,
    );
    let response = inbox(&fixture, HUMAN).await;
    assert_eq!(response.status(), StatusCode::OK);
    let body: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap()).unwrap();
    assert_eq!(body, serde_json::json!({"success": true, "data": []}));
    let commands = fixture.friends.list_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id, HUMAN);
    assert!(matches!(
        commands[0].direction,
        FriendRequestDirection::Received
    ));
    assert!(commands[0].status_filter.is_none());
}

#[tokio::test]
async fn all_legacy_friend_routes_allow_human_self_without_reading_bot_authority() {
    let fixture = fixture(
        HUMAN,
        ActorKind::Human,
        Some(STAFF),
        bcs_test_support::failing_authority_hook(),
    );
    for (method, uri, expected, operation) in routes(HUMAN) {
        let response = send(&fixture.app, method, &uri, HUMAN).await;
        assert_eq!(response.status(), expected, "{uri}");
        assert_eq!(
            fixture.friends.calls.lock().await.last(),
            Some(&(operation, HUMAN.to_string()))
        );
    }
    assert_eq!(fixture.friends.calls.lock().await.len(), 5);
}

#[tokio::test]
async fn all_legacy_friend_routes_reject_other_humans_without_reading_bot_authority() {
    let fixture = fixture(
        HUMAN,
        ActorKind::Human,
        Some("other-user"),
        bcs_test_support::failing_authority_hook(),
    );
    for (method, uri, _, _) in routes(HUMAN) {
        let response = send(&fixture.app, method, &uri, HUMAN).await;
        assert_eq!(response.status(), StatusCode::FORBIDDEN, "{uri}");
    }
    assert!(fixture.friends.calls.lock().await.is_empty());
}

#[tokio::test]
async fn human_self_identity_must_match_the_entire_canonical_actor_id() {
    for actor_id in ["human_327325-extra", "human_0327325", "human_human_327325"] {
        let fixture = fixture(
            actor_id,
            ActorKind::Human,
            Some(STAFF),
            bcs_test_support::failing_authority_hook(),
        );
        assert_eq!(
            inbox(&fixture, actor_id).await.status(),
            StatusCode::FORBIDDEN
        );
        assert!(fixture.friends.calls.lock().await.is_empty());
    }
}

#[tokio::test]
async fn human_self_still_requires_nonempty_authenticated_identity() {
    for staff_no in [None, Some("")] {
        let fixture = fixture(
            HUMAN,
            ActorKind::Human,
            staff_no,
            bcs_test_support::failing_authority_hook(),
        );
        for (method, uri, _, _) in routes(HUMAN) {
            let response = send(&fixture.app, method, &uri, HUMAN).await;
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED, "{uri}");
        }
        assert!(fixture.friends.calls.lock().await.is_empty());
    }
}

#[tokio::test]
async fn human_self_still_requires_an_existing_actor() {
    let query = RecordingBotQueryService {
        detail_result: Err(BotUseCaseError::Service(ServiceError::BotNotFound(
            HUMAN.to_string(),
        ))),
        ..Default::default()
    };
    let fixture = fixture_with_query(
        HUMAN,
        Some(STAFF),
        bcs_test_support::failing_authority_hook(),
        query,
    );
    assert_eq!(inbox(&fixture, HUMAN).await.status(), StatusCode::NOT_FOUND);
    assert!(fixture.friends.calls.lock().await.is_empty());
}

#[tokio::test]
async fn bot_with_human_like_id_cannot_bypass_live_authority() {
    let fixture = fixture(
        HUMAN,
        ActorKind::Bot,
        Some(STAFF),
        bcs_test_support::lazy_authority_chain().hook,
    );
    assert_eq!(inbox(&fixture, HUMAN).await.status(), StatusCode::FORBIDDEN);
    assert!(fixture.friends.calls.lock().await.is_empty());
}

#[tokio::test]
async fn received_bot_requests_follow_live_owner_and_manager_authority() {
    let authority = bcs_test_support::lazy_authority_chain().hook;
    authority.seed_owner("owned-bot", STAFF);
    let fixture = fixture("owned-bot", ActorKind::Bot, Some(STAFF), authority.clone());
    assert_eq!(inbox(&fixture, "owned-bot").await.status(), StatusCode::OK);

    authority.seed_owner("owned-bot", "new-owner");
    assert_eq!(
        inbox(&fixture, "owned-bot").await.status(),
        StatusCode::FORBIDDEN
    );

    authority.seed_manager("owned-bot", STAFF);
    assert_eq!(inbox(&fixture, "owned-bot").await.status(), StatusCode::OK);

    authority.revoke_manager("owned-bot", STAFF);
    assert_eq!(
        inbox(&fixture, "owned-bot").await.status(),
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        fixture.friends.calls.lock().await.as_slice(),
        &[
            ("requests", "owned-bot".to_string()),
            ("requests", "owned-bot".to_string()),
        ]
    );
}

#[tokio::test]
async fn bot_authority_lookup_failure_remains_an_internal_error() {
    let fixture = fixture(
        "owned-bot",
        ActorKind::Bot,
        Some(STAFF),
        bcs_test_support::failing_authority_hook(),
    );
    assert_eq!(
        inbox(&fixture, "owned-bot").await.status(),
        StatusCode::INTERNAL_SERVER_ERROR
    );
    assert!(fixture.friends.calls.lock().await.is_empty());
}
