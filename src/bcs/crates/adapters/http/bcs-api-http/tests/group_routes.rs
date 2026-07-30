use std::collections::BTreeSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::body::{Body, to_bytes};
use axum::http::{HeaderMap, Request, StatusCode};
use bcs_api_http::{ApiState, PrincipalVerificationError, PrincipalVerifier, router};
use bcs_service_api::application::v1::{
    AddGroupParticipant, ApplicationError, BotFinalDelivery, ChatConfiguration,
    CollaborationConfiguration, CollaborationGroupDetail, CreateGroup, CreateGroupOutcome,
    DeleteGroup, DeleteGroupParticipant, DeleteResult, GetGroup, GroupDeliveryPolicy, GroupDetail,
    GroupService, GroupStatus, GroupStrategy, GroupVisibility, ListBotGroups, Page, Participant,
    Principal, UpdateGroup, UpdateGroupParticipant,
};
use bcs_service_api::application::v1::{
    AddSessionParticipant, CompleteSession, CreateSession, CreateSessionOutcome,
    DeleteSession, DeleteSessionParticipant, GetSession, ListSessionMessages, ListSessions,
    SessionCompletionResult, SessionDetail, SessionMessage, SessionMessageService,
    SessionParticipant, SessionService, SessionSummary, UpdateSession,
    UpdateSessionParticipant,
};
use bcs_service_api::application::v1::{
    AcceptFriendRequest, AcceptInvitation, CreateFriendRequest, CreateGroupInvitation,
    CreateSessionInvitation, Friendship, FriendshipService, FriendRequest, Invitation,
    InvitationAcceptResult, InvitationService, ListFriendRequests, ListFriendships,
    RejectFriendRequest, RemoveFriendship,
};
use bcs_service_api::{ActorKind, ParticipantMode, ParticipantRole};
use serde_json::{Value, json};
use tower::ServiceExt;

struct HeaderVerifier {
    principal: Principal,
}

#[async_trait]
impl PrincipalVerifier for HeaderVerifier {
    async fn verify(&self, headers: &HeaderMap) -> Result<Principal, PrincipalVerificationError> {
        if headers
            .get("x-test-auth")
            .and_then(|value| value.to_str().ok())
            == Some("yes")
        {
            Ok(self.principal.clone())
        } else {
            Err(PrincipalVerificationError::Missing)
        }
    }
}

#[derive(Default)]
struct FakeGroupService {
    list: Mutex<Option<ListBotGroups>>,
    created: Mutex<Option<CreateGroup>>,
    reuse_dm: AtomicBool,
    get: Mutex<Option<GetGroup>>,
    updated: Mutex<Option<UpdateGroup>>,
    deleted: Mutex<Option<DeleteGroup>>,
    added_participant: Mutex<Option<AddGroupParticipant>>,
    updated_participant: Mutex<Option<UpdateGroupParticipant>>,
    removed_participant: Mutex<Option<DeleteGroupParticipant>>,
}

#[async_trait]
impl GroupService for FakeGroupService {
    async fn list_bot_groups(
        &self,
        command: ListBotGroups,
    ) -> Result<Page<bcs_service_api::application::v1::GroupSummary>, ApplicationError> {
        *self.list.lock().expect("list lock") = Some(command);
        Ok(Page::empty(0, 20))
    }

    async fn create(&self, command: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        *self.created.lock().expect("create lock") = Some(command);
        Ok(group_detail())
    }

    async fn create_with_outcome(
        &self,
        command: CreateGroup,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        let group = self.create(command).await?;
        Ok(CreateGroupOutcome {
            group,
            created: !self.reuse_dm.load(Ordering::Relaxed),
        })
    }

    async fn get(&self, query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        *self.get.lock().expect("get lock") = Some(query);
        Ok(group_detail())
    }

    async fn update(&self, command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        *self.updated.lock().expect("update lock") = Some(command);
        Ok(group_detail())
    }

    async fn delete(&self, command: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        *self.deleted.lock().expect("delete lock") = Some(command);
        Ok(DeleteResult {
            deleted: true,
        })
    }

    async fn add_participant(
        &self,
        command: AddGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        *self.added_participant.lock().expect("add participant lock") = Some(command.clone());
        Ok(Participant {
            actor_id: command.actor_id,
            actor_kind: ActorKind::Bot,
            name: None,
            role: command.role,
            mode: ParticipantMode::Auto,
        })
    }

    async fn update_participant(
        &self,
        command: UpdateGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        *self
            .updated_participant
            .lock()
            .expect("update participant lock") = Some(command.clone());
        Ok(Participant {
            actor_id: command.actor_id,
            actor_kind: ActorKind::Bot,
            name: None,
            role: ParticipantRole::Consultant,
            mode: command.mode,
        })
    }

    async fn delete_participant(
        &self,
        command: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        *self
            .removed_participant
            .lock()
            .expect("remove participant lock") = Some(command);
        Ok(DeleteResult { deleted: true })
    }
}

struct NoopSessionService;

#[async_trait]
impl SessionService for NoopSessionService {
    async fn create(
        &self,
        _command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn list(&self, _command: ListSessions) -> Result<Page<SessionSummary>, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn get(&self, _query: GetSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn update(&self, _command: UpdateSession) -> Result<SessionDetail, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn delete(&self, _command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn complete(
        &self,
        _command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn add_participant(
        &self,
        _command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn update_participant(
        &self,
        _command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }

    async fn delete_participant(
        &self,
        _command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("session not configured"))
    }
}

struct NoopSessionMessageService;

#[async_trait]
impl SessionMessageService for NoopSessionMessageService {
    async fn list(
        &self,
        _query: ListSessionMessages,
    ) -> Result<Page<SessionMessage>, ApplicationError> {
        Err(ApplicationError::internal("session messages not configured"))
    }
}

struct NoopInvitationService;

#[async_trait]
impl InvitationService for NoopInvitationService {
    async fn create_group_invitation(
        &self,
        _command: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }

    async fn create_session_invitation(
        &self,
        _command: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }

    async fn accept_invitation(
        &self,
        _command: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        Err(ApplicationError::internal("invitation not configured"))
    }
}

struct NoopFriendshipService;

#[async_trait]
impl FriendshipService for NoopFriendshipService {
    async fn list_friendships(
        &self,
        _command: ListFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn remove_friendship(
        &self,
        _command: RemoveFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn create_friend_request(
        &self,
        _command: CreateFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn list_friend_requests(
        &self,
        _command: ListFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn accept_friend_request(
        &self,
        _command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }

    async fn reject_friend_request(
        &self,
        _command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        Err(ApplicationError::internal("friendship not configured"))
    }
}

fn principal() -> Principal {
    Principal::bot("bot-1", "tenant-a", BTreeSet::new())
}

fn group_detail() -> GroupDetail {
    GroupDetail::Collaboration(CollaborationGroupDetail {
        group_id: "group-1".into(),
        version: 1,
        name: Some("Planning".into()),
        status: GroupStatus::Active,
        visibility: GroupVisibility::Private,
        context: None,
        originator_actor_id: "bot-1".into(),
        participants: vec![Participant {
            actor_id: "bot-1".into(),
            actor_kind: ActorKind::Bot,
            name: Some("Bot 1".into()),
            role: ParticipantRole::Driver,
            mode: ParticipantMode::Auto,
        }],
        driver_bot_uuid: "bot-1".into(),
        collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
            delivery_policy: GroupDeliveryPolicy {
                bot_final_delivery: BotFinalDelivery::SendToDriver,
            },
        }),
        created_at: 1,
        updated_at: 2,
    })
}

fn test_router(service: Arc<FakeGroupService>) -> axum::Router {
    router(ApiState::new(
        service,
        Arc::new(NoopSessionService),
        Arc::new(NoopSessionMessageService),
        Arc::new(NoopInvitationService),
        Arc::new(NoopFriendshipService),
        Arc::new(HeaderVerifier {
            principal: principal(),
        }),
    ))
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

fn authenticated_request(method: &str, uri: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method(method)
        .uri(uri)
        .header("content-type", "application/json")
        .header("x-test-auth", "yes")
        .header("x-request-id", "request-123")
        .body(Body::from(body.to_string()))
        .expect("request")
}

#[tokio::test]
async fn all_five_group_routes_forward_the_verified_principal() {
    let service = Arc::new(FakeGroupService::default());
    let app = test_router(service.clone());

    let list_response = app
        .clone()
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/bots/collaboration/bot-1/groups?offset=5&limit=10&membership=session_only&kind=all&strategy=state_machine",
            Value::Null,
        ))
        .await
        .expect("list response");
    assert_eq!(list_response.status(), StatusCode::OK);
    let list_body = response_json(list_response).await;
    assert_eq!(list_body["code"], 20_000);
    assert_eq!(list_body["message"], "OK");
    assert_eq!(list_body["request_id"], "request-123");
    {
        let list = service.list.lock().expect("list lock");
        let list = list.as_ref().expect("list command");
        assert_eq!(list.principal.actor_id(), "bot-1");
        assert_eq!(list.offset, 5);
        assert_eq!(list.limit, 10);
        assert_eq!(list.strategy, Some(GroupStrategy::StateMachine));
    }

    let create_response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups",
            json!({
                "group_kind": "normal",
                "name": "Planning",
                "driver_bot_uuid": "bot-1",
                "participants": [
                    {"actor_id": "bot-1", "role": "driver"}
                ],
                "collaboration": {
                    "strategy": "chat",
                    "delivery_policy": {"bot_final_delivery": "send_to_driver"}
                }
            }),
        ))
        .await
        .expect("create response");
    assert_eq!(create_response.status(), StatusCode::CREATED);
    let create_body = response_json(create_response).await;
    assert_eq!(create_body["code"], 20_100);
    assert_eq!(create_body["message"], "Created");
    assert_eq!(
        service
            .created
            .lock()
            .expect("create lock")
            .as_ref()
            .expect("create command")
            .principal
            .actor_id(),
        "bot-1"
    );

    let get_response = app
        .clone()
        .oneshot(authenticated_request(
            "GET",
            "/openapi/v1/groups/group-1",
            Value::Null,
        ))
        .await
        .expect("get response");
    assert_eq!(get_response.status(), StatusCode::OK);

    let patch_response = app
        .clone()
        .oneshot(authenticated_request(
            "PATCH",
            "/openapi/v1/groups/group-1",
            json!({
                "name": "Renamed",
                "delivery_policy": {"bot_final_delivery": "inject_observers"}
            }),
        ))
        .await
        .expect("patch response");
    assert_eq!(patch_response.status(), StatusCode::OK);

    let delete_response = app
        .clone()
        .oneshot(authenticated_request(
            "DELETE",
            "/openapi/v1/groups/group-1",
            Value::Null,
        ))
        .await
        .expect("delete response");
    assert_eq!(delete_response.status(), StatusCode::OK);
    let delete_body = response_json(delete_response).await;
    assert_eq!(delete_body["data"]["deleted"], true);

    let add_participant_response = app
        .clone()
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups/group-1/participants",
            json!({ "actor_id": "bot-2", "role": "consultant" }),
        ))
        .await
        .expect("add participant forwarding response");
    assert_eq!(add_participant_response.status(), StatusCode::OK);
    {
        let added = service
            .added_participant
            .lock()
            .expect("add participant lock");
        let added = added.as_ref().expect("add participant command");
        assert_eq!(added.principal.actor_id(), "bot-1");
        assert_eq!(added.group_id, "group-1");
        assert_eq!(added.actor_id, "bot-2");
        assert_eq!(added.role, ParticipantRole::Consultant);
    }

    let update_participant_response = app
        .clone()
        .oneshot(authenticated_request(
            "PATCH",
            "/openapi/v1/groups/group-1/participants/bot-2",
            json!({ "mode": "muted" }),
        ))
        .await
        .expect("update participant forwarding response");
    assert_eq!(update_participant_response.status(), StatusCode::OK);
    {
        let updated = service
            .updated_participant
            .lock()
            .expect("update participant lock");
        let updated = updated.as_ref().expect("update participant command");
        assert_eq!(updated.principal.actor_id(), "bot-1");
        assert_eq!(updated.group_id, "group-1");
        assert_eq!(updated.actor_id, "bot-2");
        assert_eq!(updated.mode, ParticipantMode::Muted);
    }

    let remove_participant_response = app
        .oneshot(authenticated_request(
            "DELETE",
            "/openapi/v1/groups/group-1/participants/bot-2",
            Value::Null,
        ))
        .await
        .expect("remove participant forwarding response");
    assert_eq!(remove_participant_response.status(), StatusCode::OK);
    {
        let removed = service
            .removed_participant
            .lock()
            .expect("remove participant lock");
        let removed = removed.as_ref().expect("remove participant command");
        assert_eq!(removed.principal.actor_id(), "bot-1");
        assert_eq!(removed.group_id, "group-1");
        assert_eq!(removed.actor_id, "bot-2");
    }
}

#[tokio::test]
async fn missing_principal_and_unknown_request_fields_use_the_common_error_envelope() {
    let service = Arc::new(FakeGroupService::default());
    let app = test_router(service);

    let missing = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/openapi/v1/groups/group-1")
                .body(Body::empty())
                .expect("request"),
        )
        .await
        .expect("missing auth response");
    assert_eq!(missing.status(), StatusCode::UNAUTHORIZED);
    let missing_body = response_json(missing).await;
    assert_eq!(missing_body["data"]["error_code"], "unauthenticated");

    let unknown_field = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups",
            json!({
                "group_kind": "dm",
                "target_actor_id": "bot-2",
                "originator": "attacker"
            }),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(unknown_field.status(), StatusCode::BAD_REQUEST);
    let unknown_body = response_json(unknown_field).await;
    assert_eq!(unknown_body["data"]["error_code"], "invalid_request");
}

#[tokio::test]
async fn patch_rejects_explicit_null_for_every_mutable_field() {
    let service = Arc::new(FakeGroupService::default());
    let app = test_router(service.clone());

    for body in [
        json!({"name": null}),
        json!({"context": null}),
        json!({"visibility": null}),
        json!({"delivery_policy": null}),
        json!({"name": "Renamed", "context": null}),
    ] {
        let response = app
            .clone()
            .oneshot(authenticated_request(
                "PATCH",
                "/openapi/v1/groups/group-1",
                body,
            ))
            .await
            .expect("null patch response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "invalid_request");
    }

    assert!(service.updated.lock().expect("update lock").is_none());
}

#[tokio::test]
async fn malformed_percent_encoded_paths_use_the_common_error_envelope() {
    let service = Arc::new(FakeGroupService::default());
    let app = test_router(service);

    for (method, uri, body) in [
        (
            "GET",
            "/openapi/v1/bots/collaboration/%FF/groups",
            Value::Null,
        ),
        ("GET", "/openapi/v1/groups/%FF", Value::Null),
        (
            "PATCH",
            "/openapi/v1/groups/%FF",
            json!({"name": "Renamed"}),
        ),
        ("DELETE", "/openapi/v1/groups/%FF", Value::Null),
    ] {
        let response = app
            .clone()
            .oneshot(authenticated_request(method, uri, body))
            .await
            .expect("malformed path response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        let body = response_json(response).await;
        assert_eq!(body["data"]["error_code"], "invalid_request");
        assert_eq!(body["request_id"], "request-123");
    }
}

#[tokio::test]
async fn state_machine_definition_version_must_be_positive_at_the_http_boundary() {
    let service = Arc::new(FakeGroupService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups",
            json!({
                "group_kind": "normal",
                "driver_bot_uuid": "bot-1",
                "participants": [
                    {"actor_id": "bot-1", "role": "driver"}
                ],
                "collaboration": {
                    "strategy": "state_machine",
                    "definition": {
                        "definition_id": "review",
                        "version": 0
                    },
                    "participant_bindings": []
                }
            }),
        ))
        .await
        .expect("invalid version response");

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
    assert!(service.created.lock().expect("create lock").is_none());
}

#[tokio::test]
async fn state_machine_binding_actor_ids_must_not_be_empty_at_the_http_boundary() {
    let service = Arc::new(FakeGroupService::default());
    let app = test_router(service.clone());

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups",
            json!({
                "group_kind": "normal",
                "driver_bot_uuid": "bot-1",
                "participants": [
                    {"actor_id": "bot-1", "role": "driver"}
                ],
                "collaboration": {
                    "strategy": "state_machine",
                    "definition": {
                        "definition_id": "review",
                        "version": 1
                    },
                    "participant_bindings": [{
                        "binding": "reviewer",
                        "actor_ids": []
                    }]
                }
            }),
        ))
        .await
        .expect("empty binding response");

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
    assert!(service.created.lock().expect("create lock").is_none());
}

#[tokio::test]
async fn reused_dm_returns_ok_instead_of_created() {
    let service = Arc::new(FakeGroupService::default());
    service.reuse_dm.store(true, Ordering::Relaxed);
    let app = test_router(service);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups",
            json!({
                "group_kind": "dm",
                "target_actor_id": "bot-2"
            }),
        ))
        .await
        .expect("reused DM response");

    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["message"], "OK");
}

#[tokio::test]
async fn add_group_participant_returns_participant() {
    let service = Arc::new(FakeGroupService::default());
    let app = test_router(service);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups/group-1/participants",
            json!({ "actor_id": "bot-2", "role": "consultant" }),
        ))
        .await
        .expect("add participant response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["actor_id"], "bot-2");
    assert_eq!(body["data"]["role"], "consultant");
}

#[tokio::test]
async fn update_group_participant_returns_updated_mode() {
    let service = Arc::new(FakeGroupService::default());
    let app = test_router(service);

    let response = app
        .oneshot(authenticated_request(
            "PATCH",
            "/openapi/v1/groups/group-1/participants/bot-2",
            json!({ "mode": "muted" }),
        ))
        .await
        .expect("update participant response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["mode"], "muted");
}

#[tokio::test]
async fn remove_group_participant_returns_deleted() {
    let service = Arc::new(FakeGroupService::default());
    let app = test_router(service);

    let response = app
        .oneshot(authenticated_request(
            "DELETE",
            "/openapi/v1/groups/group-1/participants/bot-2",
            Value::Null,
        ))
        .await
        .expect("remove participant response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["deleted"], true);
}

#[tokio::test]
async fn add_group_participant_rejects_unknown_field() {
    let service = Arc::new(FakeGroupService::default());
    let app = test_router(service);

    let response = app
        .oneshot(authenticated_request(
            "POST",
            "/openapi/v1/groups/group-1/participants",
            json!({ "actor_id": "bot-2", "role": "consultant", "extra": 1 }),
        ))
        .await
        .expect("unknown field response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = response_json(response).await;
    assert_eq!(body["data"]["error_code"], "invalid_request");
}
