use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::{ActorKind, ParticipantMode, ParticipantRole};
use bcs_service_api::application::v1::{
    AddGroupParticipant, ApplicationError, BotFinalDelivery, ChatConfiguration,
    CollaborationConfiguration, CollaborationGroupDetail, CreateGroup, DeleteGroup,
    DeleteGroupParticipant, DeleteResult, GetGroup, GroupDeliveryPolicy, GroupDetail, GroupService,
    GroupStatus, GroupVisibility, ListGroups, Page, Participant, UpdateGroup,
    UpdateGroupParticipant,
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tower::ServiceExt;

#[derive(Default)]
struct RecordingGroupService {
    update: Mutex<Option<UpdateGroup>>,
    update_error: Mutex<Option<ApplicationError>>,
}

#[async_trait]
impl GroupService for RecordingGroupService {
    async fn list_groups(
        &self,
        _command: ListGroups,
    ) -> Result<Page<bcs_service_api::application::v1::GroupSummary>, ApplicationError> {
        unreachable!("list is not used by this contract test")
    }

    async fn create(&self, _command: CreateGroup) -> Result<GroupDetail, ApplicationError> {
        unreachable!("create is not used by this contract test")
    }

    async fn get(&self, _query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        unreachable!("get is not used by this contract test")
    }

    async fn update(&self, command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        *self.update.lock().expect("update lock") = Some(command);
        if let Some(error) = self.update_error.lock().expect("update error lock").take() {
            return Err(error);
        }
        Ok(group_detail())
    }

    async fn delete(&self, _command: DeleteGroup) -> Result<DeleteResult, ApplicationError> {
        unreachable!("delete is not used by this contract test")
    }

    async fn add_participant(
        &self,
        _command: AddGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        unreachable!("add_participant is not used by this contract test")
    }

    async fn update_participant(
        &self,
        _command: UpdateGroupParticipant,
    ) -> Result<Participant, ApplicationError> {
        unreachable!("update_participant is not used by this contract test")
    }

    async fn delete_participant(
        &self,
        _command: DeleteGroupParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        unreachable!("delete_participant is not used by this contract test")
    }
}

fn group_detail() -> GroupDetail {
    GroupDetail::Collaboration(CollaborationGroupDetail {
        group_id: "group-1".into(),
        version: 2,
        name: Some("Renamed".into()),
        status: GroupStatus::Active,
        visibility: GroupVisibility::Public,
        context: None,
        originator_actor_id: "driver-bot".into(),
        participants: vec![Participant {
            actor_id: "driver-bot".into(),
            actor_kind: ActorKind::Bot,
            name: Some("Driver".into()),
            role: ParticipantRole::Driver,
            mode: ParticipantMode::Auto,
        }],
        driver_bot_uuid: "driver-bot".into(),
        collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
            delivery_policy: GroupDeliveryPolicy {
                bot_final_delivery: BotFinalDelivery::InjectObservers,
            },
        }),
        created_at: 1,
        updated_at: 2,
    })
}

fn test_router(service: Arc<RecordingGroupService>) -> axum::Router {
    let principal = AuthPrincipal {
        user_id: Some("staff-1".to_string()),
        user_name: Some("Ray".to_string()),
        ..Default::default()
    };
    let auth_chain = Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]));
    let state = HttpAppState::new(Services::builder().build_for_test())
        .with_group_application(service)
        .with_user_identity(Arc::new(ChainUserIdentityPort::new(auth_chain)));
    build_router(state)
}

fn service_returning(error: ApplicationError) -> Arc<RecordingGroupService> {
    Arc::new(RecordingGroupService {
        update: Mutex::new(None),
        update_error: Mutex::new(Some(error)),
    })
}

async fn patch_response(service: Arc<RecordingGroupService>) -> axum::response::Response {
    test_router(service)
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/groups/group-1")
                .header("content-type", "application/json")
                .body(Body::from(json!({"name": "Renamed"}).to_string()))
                .expect("request"),
        )
        .await
        .expect("response")
}

#[tokio::test]
async fn legacy_patch_group_delegates_to_v1_group_application() {
    let service = Arc::new(RecordingGroupService::default());
    let response = test_router(service.clone())
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/groups/group-1")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "name": "Renamed",
                        "visibility": "public",
                        "delivery_policy": {
                            "bot_final_delivery": "inject_observers"
                        }
                    })
                    .to_string(),
                ))
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::OK);
    let response_body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body");
    let response_json: Value = serde_json::from_slice(&response_body).expect("response JSON");
    assert_eq!(response_json["group_id"], "group-1");
    assert_eq!(response_json["name"], "Renamed");

    let command = service
        .update
        .lock()
        .expect("update lock")
        .take()
        .expect("update command");
    assert_eq!(command.group_id, "group-1");
    let user = command.caller.user.expect("human caller");
    assert_eq!(user.id, "staff-1");
    assert_eq!(user.display_name.as_deref(), Some("Ray"));
    assert_eq!(command.patch.name.as_deref(), Some("Renamed"));
    assert_eq!(command.patch.visibility, Some(GroupVisibility::Public));
    assert_eq!(
        command
            .patch
            .delivery_policy
            .expect("delivery policy")
            .bot_final_delivery,
        BotFinalDelivery::InjectObservers
    );
}

#[tokio::test]
async fn legacy_patch_group_rejects_unknown_fields_before_application() {
    let service = Arc::new(RecordingGroupService::default());
    let response = test_router(service.clone())
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/groups/group-1")
                .header("content-type", "application/json")
                .body(Body::from(json!({"name": "Renamed", "owner": "other"}).to_string()))
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert!(service.update.lock().expect("update lock").is_none());
}

#[tokio::test]
async fn legacy_patch_group_maps_application_errors_to_legacy_responses() {
    let cases = [
        (
            ApplicationError::invalid("invalid_group", "invalid group"),
            StatusCode::BAD_REQUEST,
            "invalid_group",
            "invalid group",
        ),
        (
            ApplicationError::Unauthenticated,
            StatusCode::UNAUTHORIZED,
            "unauthenticated",
            "authentication is required",
        ),
        (
            ApplicationError::forbidden("access denied"),
            StatusCode::FORBIDDEN,
            "forbidden",
            "access denied",
        ),
        (
            ApplicationError::forbidden_code("owner_required", "owner required"),
            StatusCode::FORBIDDEN,
            "owner_required",
            "owner required",
        ),
        (
            ApplicationError::not_found("group_not_found", "group not found"),
            StatusCode::NOT_FOUND,
            "group_not_found",
            "group not found",
        ),
        (
            ApplicationError::conflict("version_conflict", "version conflict"),
            StatusCode::CONFLICT,
            "version_conflict",
            "version conflict",
        ),
        (
            ApplicationError::Gone {
                code: "group_gone".into(),
                message: "group is gone".into(),
            },
            StatusCode::GONE,
            "group_gone",
            "group is gone",
        ),
        (
            ApplicationError::QuotaExceeded {
                code: "group_quota_exceeded".into(),
                message: "group quota exceeded".into(),
            },
            StatusCode::TOO_MANY_REQUESTS,
            "group_quota_exceeded",
            "group quota exceeded",
        ),
        (
            ApplicationError::payload_too_large("payload_too_large", "payload too large"),
            StatusCode::PAYLOAD_TOO_LARGE,
            "payload_too_large",
            "payload too large",
        ),
        (
            ApplicationError::unprocessable("invalid_state", "invalid state"),
            StatusCode::UNPROCESSABLE_ENTITY,
            "invalid_state",
            "invalid state",
        ),
        (
            ApplicationError::bad_gateway("storage_unavailable", "storage unavailable"),
            StatusCode::BAD_GATEWAY,
            "storage_unavailable",
            "storage unavailable",
        ),
        (
            ApplicationError::internal("database credentials leaked"),
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal_error",
            "internal server error",
        ),
    ];

    for (error, expected_status, expected_code, expected_message) in cases {
        let response = patch_response(service_returning(error)).await;
        assert_eq!(response.status(), expected_status);
        let response_body = to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("response body");
        let response_json: Value = serde_json::from_slice(&response_body).expect("response JSON");
        assert_eq!(response_json["status"], expected_status.as_u16());
        assert_eq!(response_json["code"], expected_code);
        assert_eq!(response_json["message"], expected_message);
        assert_eq!(response_json["error"], expected_message);
        assert!(!response_json.to_string().contains("database credentials"));
    }
}

#[tokio::test]
async fn legacy_patch_group_reports_unavailable_application_service() {
    let principal = AuthPrincipal {
        user_id: Some("staff-1".to_string()),
        ..Default::default()
    };
    let auth_chain = Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]));
    let state = HttpAppState::new(Services::builder().build_for_test())
        .with_user_identity(Arc::new(ChainUserIdentityPort::new(auth_chain)));
    let response = build_router(state)
        .oneshot(
            Request::builder()
                .method("PATCH")
                .uri("/groups/group-1")
                .header("content-type", "application/json")
                .body(Body::from(json!({"name": "Renamed"}).to_string()))
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    let response_body = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("response body");
    let response_json: Value = serde_json::from_slice(&response_body).expect("response JSON");
    assert_eq!(response_json["code"], "internal_error");
}
