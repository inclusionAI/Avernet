use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthConfig, AuthPluginChain, AuthPrincipal};
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
            initial_session_id: None,
            initial_run: None,
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
    GroupDetail::Collaboration(CollaborationGroupDetail { catalog_labels:Default::default(),
        group_id: "group-with-webhook".into(),
        version: 1,
        name: Some("Webhook group".into()),
        status: GroupStatus::Active,
        visibility: GroupVisibility::Private,
        context: Some("diagnostic".into()),
        opening_message: None,
        originator_actor_id: "human_staff-1".into(),
        participants: vec![Participant {
            actor_id: "driver-bot".into(),
            actor_kind: ActorKind::Bot,
            name: None,
            avatar_url: None,
            role: ParticipantRole::Driver,
            mode: ParticipantMode::Auto,
            tags: Vec::new(),
            message_view_scope: bcs_domain::MessageViewScope::Full,
        }],
        driver_bot_uuid: "driver-bot".into(),
        driver_bot_owner: None,
        driver_bot_owner_name: None,
        collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
            delivery_policy: GroupDeliveryPolicy {
                bot_final_delivery: BotFinalDelivery::SendToDriver,
            },
        }),
        human_mention_notify_mode: bcs_service_api::HumanMentionNotifyMode::All,
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

fn bot_test_router(service: Arc<RecordingGroupService>) -> axum::Router {
    let principal = AuthPrincipal {
        bot_uuid: Some("caller-bot".to_string()),
        ..Default::default()
    };
    let auth_chain = Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]));
    let state = HttpAppState::new(Services::builder().build_for_test())
        .with_group_application(service)
        .with_auth_chain(auth_chain.clone(), AuthConfig::default())
        .with_user_identity(Arc::new(ChainUserIdentityPort::new(auth_chain)))
        .with_strict_container_validation(true);
    build_router(state)
}

fn inline_subscription_body() -> Body {
    Body::from(
        json!({
            "label": "Webhook group",
            "driver_bot": "driver-bot",
            "participants": [{
                "bot_uuid": "driver-bot",
                "role": "driver"
            }],
            "group_strategy": "chat",
            "event_subscriptions": [{
                "name": "group-webhook",
                "event_filters": ["group.*"],
                "sink": {
                    "type": "webhook",
                    "url": "http://127.0.0.1:28082/events"
                }
            }]
        })
        .to_string(),
    )
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
                        "human_mention_notify_mode": "driver_bot_only",
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
    assert_eq!(response_json["human_mention_notify_mode"], "all");

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
            assert_eq!(group.human_mention_notify_mode, bcs_service_api::HumanMentionNotifyMode::DriverBotOnly);
            assert_eq!(group.driver_bot_uuid, "driver-bot");
            assert_eq!(group.participants.len(), 1);
        }
        CreateGroupSpec::DirectMessage(_) => panic!("expected collaboration group"),
    }
}

#[tokio::test]
async fn legacy_bot_create_with_inline_subscriptions_preserves_container_validation() {
    let service = Arc::new(RecordingGroupService::default());
    let response = bot_test_router(service.clone())
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("content-type", "application/json")
                .header("X-BCS-Bot-Token", "valid-bot-token")
                .header("x-agentclaw-bolt-id", "caller-bot")
                .body(inline_subscription_body())
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::OK);
    let (command, subscriptions) = service
        .create
        .lock()
        .expect("create lock")
        .take()
        .expect("create command");
    assert_eq!(
        command.caller.bot.as_ref().map(|bot| bot.bot_uuid.as_str()),
        Some("caller-bot")
    );
    assert!(command.caller.user.is_none());
    assert_eq!(subscriptions.len(), 1);
}

#[tokio::test]
async fn legacy_bot_create_with_inline_subscriptions_rejects_mismatched_container() {
    let service = Arc::new(RecordingGroupService::default());
    let response = bot_test_router(service.clone())
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("content-type", "application/json")
                .header("X-BCS-Bot-Token", "valid-bot-token")
                .header("x-agentclaw-bolt-id", "different-container")
                .body(inline_subscription_body())
                .expect("request"),
        )
        .await
        .expect("response");

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(service.create.lock().expect("create lock").is_none());
}

#[tokio::test]
async fn legacy_group_label_policy_errors_are_bad_requests_before_any_create_or_subscription() {
    use bcs_group::{GroupManagement, GroupStore};
    use bcs_service_api::GroupCoreService;
    use bcs_service_api::types::catalog_labels::{CatalogLabelPolicy, CatalogLabelRule};
    let groups=Arc::new(GroupStore::new());
    let service=Arc::new(RecordingGroupService::default());
    let management=GroupManagement::with_defaults(groups.clone(),
        Arc::new(bcs_test_support::NoopBotRegistryCoreService),
        Arc::new(bcs_test_support::NoopFriendCoreService))
        .with_catalog_label_policy(Arc::new(CatalogLabelPolicy::new([("stage".into(),CatalogLabelRule {
            allowed_values:vec!["trial".into(),"running".into()],allow_multiple:false,
        })].into()).unwrap()));
    let app=build_router(HttpAppState::new(Services::builder().group(groups.clone())
        .group_management(Arc::new(management)).build_for_test()).with_group_application(service.clone()));
    for subscriptions in [false,true] {
        for (labels,field,reason) in [
            (json!({"unknown":null}),"catalog_labels.unknown","UNKNOWN_KEY"),
            (json!({"unknown":[]}),"catalog_labels.unknown","UNKNOWN_KEY"),
            (json!({"stage":["unknown"]}),"catalog_labels.stage[0]","UNKNOWN_VALUE"),
            (json!({"stage":["trial","running"]}),"catalog_labels.stage","MULTIPLE_VALUES_NOT_ALLOWED"),
        ] {
            let mut request=json!({"driver_bot":"driver-bot","group_strategy":"manager_worker",
                "participants":[{"bot_uuid":"driver-bot","role":"driver"}],"catalog_labels":labels});
            if subscriptions {request["event_subscriptions"]=json!([{
                "name":"group-webhook","event_filters":["group.*"],
                "sink":{"type":"webhook","url":"http://127.0.0.1:28082/events"},
            }]);}
            let response=app.clone().oneshot(Request::builder().method("POST").uri("/groups")
                .header("content-type","application/json").body(Body::from(request.to_string())).unwrap()).await.unwrap();
            let status=response.status();let body:Value=serde_json::from_slice(&to_bytes(response.into_body(),8192).await.unwrap()).unwrap();
            assert_eq!(status,StatusCode::BAD_REQUEST,"inline={subscriptions}: {body}");
            assert_eq!(body["params"]["field"],field);assert_eq!(body["params"]["reason"],reason);
            assert!(groups.list().await.is_empty(),"invalid labels must not create a Group");
            assert!(service.create.lock().unwrap().is_none(),"invalid labels must not provision subscriptions");
        }
    }
}


#[tokio::test]
async fn legacy_group_label_type_errors_are_structured_before_any_write() {
    use bcs_group::GroupStore;
    use bcs_service_api::GroupCoreService;
    let groups=Arc::new(GroupStore::new());
    let service=Arc::new(RecordingGroupService::default());
    let app=build_router(HttpAppState::new(Services::builder().group(groups.clone()).build_for_test())
        .with_group_application(service.clone()));
    for subscriptions in [false,true] {
        for (labels,field) in [
            (json!(null),"catalog_labels"),
            (json!([]),"catalog_labels"),
            (json!(1),"catalog_labels"),
            (json!({"stage":1}),"catalog_labels.stage"),
            (json!({"stage":{}}),"catalog_labels.stage"),
            (json!({"stage":[1]}),"catalog_labels.stage[0]"),
            (json!({"stage":["trial",false]}),"catalog_labels.stage[1]"),
        ] {
            let mut body=json!({"driver_bot":"driver-bot","group_strategy":"manager_worker",
                "participants":[{"bot_uuid":"driver-bot","role":"driver"}],"catalog_labels":labels});
            if subscriptions {body["event_subscriptions"]=json!([{
                "name":"group-webhook","event_filters":["group.*"],
                "sink":{"type":"webhook","url":"http://127.0.0.1:28082/events"},
            }]);}
            let response=app.clone().oneshot(Request::builder().method("POST").uri("/groups")
                .header("content-type","application/json").body(Body::from(body.to_string())).unwrap()).await.unwrap();
            assert_eq!(response.status(),StatusCode::BAD_REQUEST,"inline={subscriptions}, labels={labels}");
            assert!(response.headers()["content-type"].to_str().unwrap().starts_with("application/json"));
            let body:Value=serde_json::from_slice(&to_bytes(response.into_body(),8192).await.unwrap()).unwrap();
            assert_eq!(body["code"],"bad_request");
            assert_eq!(body["params"]["code"],"invalid_request");
            assert_eq!(body["params"]["field"],field);
            assert_eq!(body["params"]["reason"],"INVALID_TYPE");
            assert!(groups.list().await.is_empty());
            assert!(service.create.lock().unwrap().is_none());
        }
    }
}

#[tokio::test]
async fn legacy_group_non_label_json_rejections_keep_their_statuses() {
    let service=Arc::new(RecordingGroupService::default());
    let app=bot_test_router(service.clone());
    for (content_type,body,status) in [
        ("application/json",r#"{"driver_bot":"driver-bot","participants":1}"#.to_owned(),StatusCode::UNPROCESSABLE_ENTITY),
        ("application/json","{".to_owned(),StatusCode::BAD_REQUEST),
        ("text/plain","{}".to_owned(),StatusCode::UNSUPPORTED_MEDIA_TYPE),
        ("application/json"," ".repeat(3*1024*1024),StatusCode::PAYLOAD_TOO_LARGE),
    ] {
        let response=app.clone().oneshot(Request::builder().method("POST").uri("/groups")
            .header("content-type",content_type).body(Body::from(body)).unwrap()).await.unwrap();
        assert_eq!(response.status(),status);
        assert!(service.create.lock().unwrap().is_none());
    }
}
