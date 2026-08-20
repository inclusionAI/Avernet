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
    CollaborationConfiguration, CollaborationGroupDetail, CreateGroup, CreateGroupOutcome,
    CreateGroupSpec, DeleteGroup, DeleteGroupParticipant, DeleteResult, EventSinkInput, GetGroup,
    GroupDeliveryPolicy, GroupDetail, GroupService, GroupStatus, GroupVisibility,
    InlineGroupEventSubscriptionRequest, ListGroups, Page, Participant, UpdateGroup,
    UpdateGroupParticipant,
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tower::ServiceExt;

#[derive(Default)]
struct RecordingGroupService {
    create: Mutex<Option<(CreateGroup, Vec<InlineGroupEventSubscriptionRequest>)>>,
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
        unreachable!("create without subscriptions is not used by this contract test")
    }

    async fn create_with_event_subscriptions(
        &self,
        command: CreateGroup,
        subscriptions: Vec<InlineGroupEventSubscriptionRequest>,
    ) -> Result<CreateGroupOutcome, ApplicationError> {
        *self.create.lock().expect("create lock") = Some((command, subscriptions));
        Ok(CreateGroupOutcome {
            group: group_detail(),
            created: true,
            event_subscriptions: Vec::new(),
        })
    }

    async fn get(&self, _query: GetGroup) -> Result<GroupDetail, ApplicationError> {
        unreachable!("get is not used by this contract test")
    }

    async fn update(&self, _command: UpdateGroup) -> Result<GroupDetail, ApplicationError> {
        unreachable!("update is not used by this contract test")
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
        group_id: "group-with-webhook".into(),
        version: 1,
        name: Some("Webhook group".into()),
        status: GroupStatus::Active,
        visibility: GroupVisibility::Private,
        context: Some("diagnostic".into()),
        originator_actor_id: "human_staff-1".into(),
        participants: vec![Participant {
            actor_id: "driver-bot".into(),
            actor_kind: ActorKind::Bot,
            name: None,
            role: ParticipantRole::Driver,
            mode: ParticipantMode::Auto,
        }],
        driver_bot_uuid: "driver-bot".into(),
        collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
            delivery_policy: GroupDeliveryPolicy {
                bot_final_delivery: BotFinalDelivery::SendToDriver,
            },
        }),
        created_at: 1,
        updated_at: 1,
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
async fn legacy_create_group_delegates_inline_subscriptions_to_v1_application() {
    let service = Arc::new(RecordingGroupService::default());
    let response = test_router(service.clone())
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("content-type", "application/json")
                .body(Body::from(
                    json!({
                        "label": "Webhook group",
                        "context": "diagnostic",
                        "driver_bot": "driver-bot",
                        "participants": [{
                            "bot_uuid": "driver-bot",
                            "role": "driver"
                        }],
                        "group_strategy": "chat",
                        "event_subscriptions": [{
                            "name": "group-webhook",
                            "event_filters": ["group.*", "session.*"],
                            "payload": {"mode": "metadata_only"},
                            "sink": {
                                "type": "webhook",
                                "url": "http://127.0.0.1:28082/events",
                                "request_timeout_ms": 2000
                            }
                        }]
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
    assert_eq!(response_json["id"], "group-with-webhook");

    let (command, subscriptions) = service
        .create
        .lock()
        .expect("create lock")
        .take()
        .expect("create command");
    let user = command.caller.user.expect("human caller");
    assert_eq!(user.id, "staff-1");
    assert_eq!(subscriptions.len(), 1);
    assert_eq!(subscriptions[0].name, "group-webhook");
    match &subscriptions[0].sink {
        EventSinkInput::Webhook {
            url,
            request_timeout_ms,
        } => {
            assert_eq!(url, "http://127.0.0.1:28082/events");
            assert_eq!(*request_timeout_ms, Some(2000));
        }
    }
    match command.group {
        CreateGroupSpec::Collaboration(group) => {
            assert_eq!(group.driver_bot_uuid, "driver-bot");
            assert_eq!(group.participants.len(), 1);
        }
        CreateGroupSpec::DirectMessage(_) => panic!("expected collaboration group"),
    }
}
