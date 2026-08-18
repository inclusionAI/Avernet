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
