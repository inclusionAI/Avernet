#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

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
    async fn verify(
        &self,
        headers: &HeaderMap,
    ) -> Result<AuthenticatedCaller, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(caller())
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

fn caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-1".into()),
        user: Some(AuthenticatedUserIdentity {
            id: "staff-1".into(),
            username: "staff-1".into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn caller_id(caller: &AuthenticatedCaller) -> &str {
    caller.user.as_ref().expect("human caller").id.as_str()
}

struct NoopGroupService;

#[async_trait]
impl GroupService for NoopGroupService {
    async fn list_groups(&self, _: ListGroups) -> Result<Page<GroupSummary>, ApplicationError> {
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

#[derive(Default)]
struct FakeEventSubscriptionService {
    calls: Mutex<Vec<String>>,
    patch_revision: Mutex<Option<u64>>,
    list_limit: Mutex<Option<u32>>,
}

impl FakeEventSubscriptionService {
    fn called(&self, name: &str, caller: &AuthenticatedCaller) {
        assert_eq!(caller_id(caller), "staff-1");
        self.calls.lock().expect("calls lock").push(name.into());
    }
}

#[async_trait]
impl EventSubscriptionService for FakeEventSubscriptionService {
    async fn create(
        &self,
        command: CreateEventSubscription,
    ) -> Result<EventSubscription, ApplicationError> {
        self.called("create", &command.caller);
        assert_eq!(command.request.name, "observer");
        Ok(subscription())
    }

    async fn list(
        &self,
        query: ListEventSubscriptions,
    ) -> Result<CursorPage<EventSubscription>, ApplicationError> {
        self.called("list", &query.caller);
        assert_eq!(
            query.scope,
            Some(EventSubscriptionScope {
                scope_type: EventSubscriptionScopeType::Group,
                id: "group-1".into(),
            })
        );
        *self.list_limit.lock().expect("list limit lock") = Some(query.limit);
        Ok(CursorPage {
            items: vec![subscription()],
            next_cursor: Some("sub-next".into()),
        })
    }

    async fn get(
        &self,
        query: GetEventSubscription,
    ) -> Result<EventSubscription, ApplicationError> {
        self.called("get", &query.caller);
        assert_eq!(query.subscription_id, "sub-1");
        Ok(subscription())
    }

    async fn patch(
        &self,
        command: PatchEventSubscription,
    ) -> Result<EventSubscription, ApplicationError> {
        self.called("patch", &command.caller);
        *self.patch_revision.lock().expect("patch revision lock") = Some(command.expected_revision);
        assert_eq!(command.patch.name.as_deref(), Some("renamed"));
        Ok(subscription())
    }

    async fn delete(
        &self,
        command: DeleteEventSubscription,
    ) -> Result<EventSubscription, ApplicationError> {
        self.called("delete", &command.caller);
        assert_eq!(command.expected_revision, 7);
        let mut result = subscription();
        result.status = EventSubscriptionStatus::Deleted;
        Ok(result)
    }

    async fn test(
        &self,
        command: TestEventSubscription,
    ) -> Result<EventSubscriptionTestResult, ApplicationError> {
        self.called("test", &command.caller);
        assert_eq!(command.subscription_id, "sub-1");
        Ok(EventSubscriptionTestResult {
            request_id: "test-1".into(),
            delivered: true,
            http_status: Some(204),
            error_category: None,
            completed_at: "2026-08-19T00:00:00Z".into(),
        })
    }

    async fn list_deliveries(
        &self,
        query: ListEventDeliveries,
    ) -> Result<CursorPage<EventDeliverySummary>, ApplicationError> {
        self.called("list_deliveries", &query.caller);
        assert_eq!(query.status, Some(EventDeliveryStatus::DeadLettered));
        Ok(CursorPage {
            items: vec![delivery(EventDeliveryStatus::DeadLettered)],
            next_cursor: None,
        })
    }

    async fn get_delivery(
        &self,
        query: GetEventDelivery,
    ) -> Result<EventDeliveryDetail, ApplicationError> {
        self.called("get_delivery", &query.caller);
        Ok(EventDeliveryDetail {
            delivery: delivery(EventDeliveryStatus::DeadLettered),
            attempts: Vec::new(),
            replay_of_delivery_id: None,
            resolved_by_delivery_id: None,
        })
    }

    async fn replay_delivery(
        &self,
        command: ReplayEventDelivery,
    ) -> Result<ReplayEventDeliveryResult, ApplicationError> {
        self.called("replay", &command.caller);
        assert_eq!(command.delivery_id, "del-1");
        assert_eq!(command.replay_request_id, "retry-1");
        assert_eq!(command.expected_subscription_revision, 7);
        Ok(ReplayEventDeliveryResult {
            original_delivery_id: command.delivery_id,
            replacement: delivery(EventDeliveryStatus::Pending),
        })
    }

    async fn skip_delivery(
        &self,
        command: SkipEventDelivery,
    ) -> Result<SkipEventDeliveryResult, ApplicationError> {
        self.called("skip", &command.caller);
        assert_eq!(command.delivery_id, "del-1");
        assert_eq!(command.reason, "acknowledged loss");
        Ok(SkipEventDeliveryResult {
            delivery_id: command.delivery_id,
            status: EventDeliveryStatus::Skipped,
            skipped_at: "2026-08-19T00:00:01Z".into(),
        })
    }
}

fn subscription() -> EventSubscription {
    EventSubscription {
        subscription_id: "sub-1".into(),
        name: "observer".into(),
        scope: EventSubscriptionScope {
            scope_type: EventSubscriptionScopeType::Group,
            id: "group-1".into(),
        },
        include_descendants: true,
        event_filters: vec!["group.*".into()],
        payload: EventPayload::default(),
        ordering: EventOrdering::default(),
        sink: EventSinkView::Webhook {
            endpoint: EventWebhookEndpointView {
                scheme: "https".into(),
                host: "example.com".into(),
                path_hash: "hash".into(),
            },
            request_timeout_ms: 5_000,
        },
        status: EventSubscriptionStatus::Active,
        revision: 7,
        created_at: "2026-08-19T00:00:00Z".into(),
        updated_at: "2026-08-19T00:00:00Z".into(),
    }
}

fn delivery(status: EventDeliveryStatus) -> EventDeliverySummary {
    EventDeliverySummary {
        delivery_id: "del-1".into(),
        event_id: "evt-1".into(),
        event_type: "group.created".into(),
        subscription_id: "sub-1".into(),
        subscription_revision: 7,
        stream_key_hash: "stream-hash".into(),
        sequence: 1,
        status,
        attempt_count: 1,
        last_http_status: None,
        last_error_category: None,
        created_at: "2026-08-19T00:00:00Z".into(),
    }
}

fn test_router(service: Arc<FakeEventSubscriptionService>) -> axum::Router {
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
        .with_event_subscription_service(service),
    )
}

fn request(method: &str, uri: &str, body: Value) -> Request<Body> {
    let mut builder = Request::builder()
        .method(method)
        .uri(uri)
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-1");
    if !body.is_null() {
        builder = builder.header("content-type", "application/json");
    }
    builder
        .body(if body.is_null() {
            Body::empty()
        } else {
            Body::from(body.to_string())
        })
        .expect("request")
}

async fn json_body(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

fn create_body() -> Value {
    json!({
        "name": "observer",
        "scope": {"type": "group", "id": "group-1"},
        "event_filters": ["group.*"],
        "sink": {
            "type": "webhook",
            "url": "https://example.com/events?token=private"
        }
    })
}

#[tokio::test]
async fn all_ten_routes_forward_verified_callers_and_return_contract_statuses() {
    let service = Arc::new(FakeEventSubscriptionService::default());
    let app = test_router(service.clone());
    let cases = [
        (
            "POST",
            "/openapi/v1/collaboration/event-subscriptions",
            create_body(),
            StatusCode::CREATED,
        ),
        (
            "GET",
            "/openapi/v1/collaboration/event-subscriptions?scope_type=group&scope_id=group-1&limit=17",
            Value::Null,
            StatusCode::OK,
        ),
        (
            "GET",
            "/openapi/v1/collaboration/event-subscriptions/sub-1",
            Value::Null,
            StatusCode::OK,
        ),
        (
            "PATCH",
            "/openapi/v1/collaboration/event-subscriptions/sub-1",
            json!({"revision": 7, "name": "renamed"}),
            StatusCode::OK,
        ),
        (
            "DELETE",
            "/openapi/v1/collaboration/event-subscriptions/sub-1?revision=7",
            Value::Null,
            StatusCode::OK,
        ),
        (
            "POST",
            "/openapi/v1/collaboration/event-subscriptions/sub-1:test",
            Value::Null,
            StatusCode::OK,
        ),
        (
            "GET",
            "/openapi/v1/collaboration/event-subscriptions/sub-1/deliveries?status=dead_lettered",
            Value::Null,
            StatusCode::OK,
        ),
        (
            "GET",
            "/openapi/v1/collaboration/event-deliveries/del-1",
            Value::Null,
            StatusCode::OK,
        ),
        (
            "POST",
            "/openapi/v1/collaboration/event-deliveries/del-1:replay",
            json!({"replay_request_id": "retry-1", "expected_subscription_revision": 7}),
            StatusCode::ACCEPTED,
        ),
        (
            "POST",
            "/openapi/v1/collaboration/event-deliveries/del-1:skip",
            json!({"reason": "acknowledged loss"}),
            StatusCode::OK,
        ),
    ];

    for (method, uri, body, expected) in cases {
        let response = app
            .clone()
            .oneshot(request(method, uri, body))
            .await
            .expect("route response");
        assert_eq!(response.status(), expected, "{method} {uri}");
        let body = json_body(response).await;
        assert_eq!(body["request_id"], "request-1");
    }

    assert_eq!(
        *service.list_limit.lock().expect("list limit lock"),
        Some(17)
    );
    assert_eq!(
        service.calls.lock().expect("calls lock").as_slice(),
        [
            "create",
            "list",
            "get",
            "patch",
            "delete",
            "test",
            "list_deliveries",
            "get_delivery",
            "replay",
            "skip",
        ]
    );
}

#[tokio::test]
async fn create_response_is_redacted_and_unknown_fields_are_rejected() {
    let service = Arc::new(FakeEventSubscriptionService::default());
    let app = test_router(service.clone());
    let response = app
        .clone()
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/event-subscriptions",
            create_body(),
        ))
        .await
        .expect("create response");
    let body = json_body(response).await;
    let serialized = body.to_string();
    assert!(!serialized.contains("0123456789abcdef"));
    assert!(!serialized.contains("token=private"));
    assert_eq!(body["data"]["sink"]["endpoint"]["host"], "example.com");

    let response = app
        .oneshot(request(
            "POST",
            "/openapi/v1/collaboration/event-subscriptions",
            json!({"name": "bad", "unknown": true}),
        ))
        .await
        .expect("invalid response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        json_body(response).await["data"]["error_code"],
        "invalid_request"
    );
    assert_eq!(
        service.calls.lock().expect("calls lock").as_slice(),
        ["create"]
    );
}

#[tokio::test]
async fn patch_accepts_strong_if_match_and_rejects_missing_or_conflicting_revision() {
    let service = Arc::new(FakeEventSubscriptionService::default());
    let app = test_router(service.clone());

    let mut with_header = request(
        "PATCH",
        "/openapi/v1/collaboration/event-subscriptions/sub-1",
        json!({"name": "renamed"}),
    );
    with_header
        .headers_mut()
        .insert("if-match", "\"7\"".parse().expect("If-Match header value"));
    let response = app
        .clone()
        .oneshot(with_header)
        .await
        .expect("patch response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        *service.patch_revision.lock().expect("patch revision lock"),
        Some(7)
    );

    let missing = app
        .clone()
        .oneshot(request(
            "PATCH",
            "/openapi/v1/collaboration/event-subscriptions/sub-1",
            json!({"name": "renamed"}),
        ))
        .await
        .expect("missing revision response");
    assert_eq!(missing.status(), StatusCode::BAD_REQUEST);

    let mut conflicting = request(
        "PATCH",
        "/openapi/v1/collaboration/event-subscriptions/sub-1",
        json!({"revision": 8, "name": "renamed"}),
    );
    conflicting
        .headers_mut()
        .insert("if-match", "\"7\"".parse().expect("If-Match header value"));
    let response = app
        .oneshot(conflicting)
        .await
        .expect("conflicting revision response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn routes_require_a_verified_principal_before_calling_the_service() {
    let service = Arc::new(FakeEventSubscriptionService::default());
    let app = test_router(service.clone());
    let response = app
        .oneshot(
            Request::builder()
                .uri("/openapi/v1/collaboration/event-subscriptions")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(service.calls.lock().expect("calls lock").is_empty());
}
