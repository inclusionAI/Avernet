use super::*;

fn services_builder_with_group_use_cases(
    group: Arc<GroupStore>,
    registry: Arc<BotCore>,
    friend: Arc<dyn FriendCoreService>,
) -> ServicesBuilder {
    let group_service: Arc<dyn GroupCoreService> = group;
    let registry_service: Arc<dyn BotRegistryCoreService> = registry;
    let group_use_cases = Arc::new(
        GroupManagement::with_defaults(
            group_service.clone(),
            registry_service.clone(),
            friend.clone(),
        )
        // The CURRENT control legs consult the
        // authority hook (bot-authority plan
        // Task 6.3); the lazy chain carries the
        // owner edges registered per test
        // (`shared_route_authority`).
        .with_bot_authority(shared_route_authority()),
    );
    Services::builder()
        .registry(registry_service)
        .friend(friend)
        .group(group_service)
        .group_management(group_use_cases.clone())
        .group_query(group_use_cases)
}
// Legacy friend-route contract tests (Strategy A/B ownership model, enriched
// `{success,data}` wire shape) were retired by the edge-permission reform
// (Task 3). The `/friends/*` + `/bots/{id}/friends` handlers now back onto
// the Noop-wired `ConnectService`; the new wire contract and real-service
// behavior are covered by Installment 3 tests.
#[tokio::test]
async fn create_group_route_persists_normal_group_and_includes_driver() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["driver-bot", "worker-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    registry
        .save_created_by("driver-bot", "alice", true)
        .await
        .unwrap();
    shared_route_authority().seed_owner("driver-bot", "alice");
    shared_route_authority().seed_owner("driver-bot", "alice");
    let group = Arc::new(GroupStore::new());
    let services = Services::builder()
        .registry(registry.clone())
        .group(group.clone())
        .group_management(Arc::new(GroupManagement::with_defaults(
            group.clone(),
            registry,
            Arc::new(NoopFriendCoreService),
        )))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "id": "group-1",
                        "label": "Launch",
                        "driver_bot": "driver-bot",
                        "participants": [
                            { "bot_uuid": "worker-bot", "role": "consultant" }
                        ],
                        "context": "Ship it"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "group-1");
    assert_eq!(json["driver_bot"], "driver-bot");
    assert_eq!(json["group_kind"], "normal");
    assert_eq!(json["created"], true);
    let participants = json["participants"].as_array().unwrap();
    assert!(participants.iter().any(|id| id == "driver-bot"));
    assert!(participants.iter().any(|id| id == "worker-bot"));

    let stored = group.get("group-1").await.unwrap();
    assert_eq!(stored.label.as_deref(), Some("Launch"));
    assert_eq!(stored.context.as_deref(), Some("Ship it"));
    assert!(
        stored
            .participants
            .iter()
            .any(|p| p.bot_uuid == "driver-bot")
    );
}

#[tokio::test]
async fn list_and_get_group_routes_return_group_details() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let group = Arc::new(GroupStore::new());
    let mut stored = Group::new(
        "group-1",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("worker-bot", ParticipantRole::Consultant),
        ],
    );
    stored.label = Some("Launch".to_string());
    stored.updated_at = 10;
    group.upsert(stored).await.unwrap();
    let mut newer = Group::new(
        "group-2",
        "driver-bot",
        vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
    );
    newer.updated_at = 20;
    group.upsert(newer).await.unwrap();
    let services =
        services_builder_with_group_use_cases(group, registry, Arc::new(NoopFriendCoreService))
            .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let list_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/groups?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(list_response.status(), StatusCode::OK);
    let body = to_bytes(list_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 2);
    assert_eq!(json["items"][0]["id"], "group-2");
    assert_eq!(json["items"][1]["id"], "group-1");
    assert_eq!(json["items"][0]["group_kind"], "normal");

    let get_response = app
        .oneshot(
            Request::builder()
                .uri("/groups/group-1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(get_response.status(), StatusCode::OK);
    let body = to_bytes(get_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["id"], "group-1");
    assert_eq!(json["label"], "Launch");
    assert_eq!(json["participants"].as_array().unwrap().len(), 2);
}

#[tokio::test]
async fn delete_group_route_removes_group_when_driver_matches() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let group = Arc::new(GroupStore::new());
    group
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .group(group.clone())
        .group_management(Arc::new(GroupManagement::with_defaults(
            group.clone(),
            registry,
            Arc::new(NoopFriendCoreService),
        )))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/groups/group-1?bot_id=driver-bot")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["deleted"], true);
    assert_eq!(json["id"], "group-1");
    assert!(group.get("group-1").await.is_none());
}

#[tokio::test]
async fn bot_groups_route_lists_groups_for_participant() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "driver-bot".to_string(),
            BotCapabilities {
                name: Some("Driver Bot".to_string()),
                summary: Some("Driver".to_string()),
                skills: vec![Skill::new("manage")],
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register(
            "worker-bot".to_string(),
            BotCapabilities {
                name: Some("Worker Bot".to_string()),
                summary: Some("Worker".to_string()),
                skills: vec![Skill::new("work")],
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    let group = Arc::new(GroupStore::new());
    let mut normal = Group::new(
        "group-1",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("worker-bot", ParticipantRole::Consultant),
        ],
    );
    normal.label = Some("Normal".to_string());
    normal.updated_at = 10;
    group.upsert(normal).await.unwrap();
    let mut newer = Group::new(
        "group-2",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Manager),
            Participant::bot("worker-bot", ParticipantRole::Worker),
        ],
    );
    newer.group_strategy = GroupStrategy::ManagerWorker;
    newer.updated_at = 20;
    group.upsert(newer).await.unwrap();
    let mut dm = Group::new(
        "dm-1",
        "driver-bot",
        vec![Participant::bot("worker-bot", ParticipantRole::Consultant)],
    );
    dm.group_kind = GroupKind::Dm;
    group.upsert(dm).await.unwrap();
    let services =
        services_builder_with_group_use_cases(group, registry, Arc::new(NoopFriendCoreService))
            .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/worker-bot/groups?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["bot_uuid"], "worker-bot");
    assert_eq!(json["total"], 2);
    assert_eq!(json["items"][0]["group_id"], "group-2");
    assert_eq!(json["items"][1]["group_id"], "group-1");
    assert_eq!(json["items"][0]["group_kind"], "normal");
    assert_eq!(json["items"][0]["group_strategy"], "manager_worker");
    assert_eq!(json["items"][0]["participants"][0]["bot_name"], "Driver Bot");
    assert_eq!(json["items"][0]["participants"][1]["bot_name"], "Worker Bot");
}

#[tokio::test]
async fn bot_groups_route_filters_absent_human_participant_groups() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let group = Arc::new(GroupStore::new());
    let mut present = Group::new(
        "present-group",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::human("human_100005", ParticipantRole::Observer),
        ],
    );
    present
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "human_100005")
        .expect("human participant")
        .mode = Some(bcs_service_api::ParticipantMode::Present);
    group.upsert(present).await.unwrap();
    group
        .upsert(Group::new(
            "absent-group",
            "driver-bot",
            vec![
                Participant::bot("driver-bot", ParticipantRole::Driver),
                Participant::human("human_100005", ParticipantRole::Observer),
            ],
        ))
        .await
        .unwrap();
    let services =
        services_builder_with_group_use_cases(group, registry, Arc::new(NoopFriendCoreService))
            .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/human_100005/groups?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["bot_uuid"], "human_100005");
    assert_eq!(json["total"], 1);
    assert_eq!(json["items"].as_array().unwrap().len(), 1);
    assert_eq!(json["items"][0]["group_id"], "present-group");
}

#[tokio::test]
async fn bot_groups_route_can_exclude_session_only_groups() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let group = Arc::new(GroupStore::new());
    group
        .upsert(Group::new(
            "session-only-group",
            "driver-bot",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();

    let session_repo = Arc::new(MemorySessionRepo::new());
    session_repo
        .create(
            "session-only-group",
            bcs_service_api::NewSessionParams {
                participants: vec![Participant::bot(
                    "worker-bot",
                    ParticipantRole::Consultant,
                )],
                ..Default::default()
            },
        )
        .await
        .unwrap();
    let session_management = Arc::new(SessionManagementServiceImpl::new(
        session_repo,
        Arc::new(MemoryGroupRepo::new()),
    ));
    let services = services_builder_with_group_use_cases(
        group,
        registry,
        Arc::new(NoopFriendCoreService),
    )
    .session_management(session_management)
    .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let default_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/bots/worker-bot/groups?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(default_response.status(), StatusCode::OK);
    let body = to_bytes(default_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 1);
    assert_eq!(json["items"][0]["group_id"], "session-only-group");

    let formal_only_response = app
        .oneshot(
            Request::builder()
                .uri(
                    "/bots/worker-bot/groups?offset=0&limit=10&include_session_groups=false",
                )
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(formal_only_response.status(), StatusCode::OK);
    let body = to_bytes(formal_only_response.into_body(), usize::MAX)
        .await
        .unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 0);
    assert!(json["items"].as_array().unwrap().is_empty());
}

#[tokio::test]
async fn service_group_routes_are_not_registered() {
    let app = build_router(HttpAppState::new(Services::noop()));
    let cases = [
        ("GET", "/service-groups"),
        ("POST", "/service-groups"),
        ("GET", "/service-groups/sg-1"),
        ("POST", "/service-groups/sg-1/instances"),
    ];

    for (method, uri) in cases {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method(method)
                    .uri(uri)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{method} {uri}");
    }
}

#[tokio::test]
async fn group_secondary_routes_preserve_metadata_and_reject_removed_workspace() {
    // Grant the Human→Bot control edge in OUR OWN setup: this
    // test's driver control legs consult the live authority
    // chain, and the shared lazy chain may not yet carry an
    // owner edge for ("driver-bot", "alice") when this test
    // runs before the sibling seeding tests under CI's
    // parallel order.
    shared_route_authority().seed_owner("driver-bot", "alice");
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["driver-bot", "member-bot", "new-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    let groups = Arc::new(GroupStore::new());
    let mut group = Group::new(
        "group-secondary",
        "driver-bot",
        vec![
            Participant::bot("driver-bot", ParticipantRole::Driver),
            Participant::bot("member-bot", ParticipantRole::Consultant),
        ],
    );
    group.originator = Some("driver-bot".to_string());
    groups.upsert(group).await.unwrap();
    let services = services_builder_with_group_use_cases(
        groups.clone(),
        registry,
        Arc::new(NoopFriendCoreService),
    )
    .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let add_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-secondary/members")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "new-bot",
                        "role": "consultant"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(add_response.status(), StatusCode::OK);
    assert_eq!(
        groups
            .get("group-secondary")
            .await
            .unwrap()
            .participants
            .len(),
        3
    );

    let status_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-secondary/status")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "status": "completed",
                        "reason": "done"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(status_response.status(), StatusCode::OK);
    assert_eq!(
        groups.get("group-secondary").await.unwrap().status,
        GroupStatus::Completed
    );

    let label_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-secondary/label")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "label": "Updated Group"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(label_response.status(), StatusCode::OK);
    assert_eq!(
        groups
            .get("group-secondary")
            .await
            .unwrap()
            .label
            .as_deref(),
        Some("Updated Group")
    );

    for method in ["GET", "PUT"] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method(method)
                    .uri("/groups/group-secondary/workspace")
                    .header("content-type", "application/json")
                    .body(Body::from(r#"{"decisions":[],"tasks":[],"notes":[],"audit_log":[]}"#))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{method} workspace");
    }

    let detail_response = app
        .clone()
        .oneshot(Request::builder().uri("/groups/group-secondary").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(detail_response.status(), StatusCode::OK);
    let body = to_bytes(detail_response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["label"], "Updated Group");
    assert!(json.get("workspace").is_none());

    let terminate_response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-secondary/terminate")
                .header("authorization", "Bearer driver-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(terminate_response.status(), StatusCode::OK);
    assert_eq!(
        groups.get("group-secondary").await.unwrap().status,
        GroupStatus::Completed
    );
}

#[tokio::test]
async fn add_group_member_route_rejects_non_owner_human_identity() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["driver-bot", "new-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .save_created_by("driver-bot", "alice", true)
        .await
        .unwrap();
    shared_route_authority().seed_owner("driver-bot", "alice");
    shared_route_authority().seed_owner("driver-bot", "alice");
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    let groups = Arc::new(GroupStore::new());
    groups
        .upsert(Group::new(
            "group-owner",
            "driver-bot",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .group(groups.clone())
        .group_management(Arc::new(
            GroupManagement::with_defaults(
                groups.clone(),
                registry,
                Arc::new(NoopFriendCoreService),
            )
            .with_bot_authority(shared_route_authority()),
        ))
        .build_for_test();
    let chain = static_auth_chain("bob", "Bob");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-owner/members")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "new-bot",
                        "role": "consultant"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let stored = groups.get("group-owner").await.unwrap();
    assert_eq!(stored.participants.len(), 1);
}

#[tokio::test]
async fn add_group_member_route_allows_private_friend_target() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, visibility) in [("driver-bot", "public"), ("private-bot", "private")] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: visibility.to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .save_created_by("driver-bot", "alice", true)
        .await
        .unwrap();
    shared_route_authority().seed_owner("driver-bot", "alice");
    shared_route_authority().seed_owner("driver-bot", "alice");
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    let groups = Arc::new(GroupStore::new());
    groups
        .upsert(Group::new(
            "group-private-target",
            "driver-bot",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();
    let friend = Arc::new(StaticFriendCoreService::new(vec![(
        "driver-bot",
        "private-bot",
    )]));
    let services = Services::builder()
        .registry(registry.clone())
        .friend(friend.clone())
        .group(groups.clone())
        .group_management(Arc::new(
        GroupManagement::with_defaults(groups.clone(), registry, friend)
            .with_bot_authority(shared_route_authority()),
    ))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/group-private-target/members")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "private-bot",
                        "role": "consultant"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let stored = groups.get("group-private-target").await.unwrap();
    assert_eq!(stored.participants.len(), 2);
    assert!(stored
        .participants
        .iter()
        .any(|participant| participant.bot_uuid == "private-bot"));
}

#[tokio::test]
async fn group_routing_policy_route_merges_and_persists_policy() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["driver-bot", "member-bot"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    let groups = Arc::new(GroupStore::new());
    groups
        .upsert(Group::new(
            "group-routing",
            "driver-bot",
            vec![
                Participant::bot("driver-bot", ParticipantRole::Driver),
                Participant::bot("member-bot", ParticipantRole::Consultant),
            ],
        ))
        .await
        .unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .group(groups.clone())
        .group_management(Arc::new(
            GroupManagement::with_defaults(
                groups.clone(),
                registry,
                Arc::new(NoopFriendCoreService),
            )
            .with_bot_authority(shared_route_authority()),
        ))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-routing/routing-policy")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "mode": "structured",
                        "default_bot_final_delivery": "inject_observers",
                        "sender_routes": {
                            "driver-bot": ["member-bot"]
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["ok"], true);
    assert_eq!(json["routing_policy"]["mode"], "structured");
    assert_eq!(
        json["routing_policy"]["default_bot_final_delivery"],
        "inject_observers"
    );
    assert_eq!(
        json["routing_policy"]["sender_routes"]["driver-bot"],
        serde_json::json!(["member-bot"])
    );

    let stored = groups.get("group-routing").await.unwrap();
    let policy = stored.routing_policy.unwrap();
    assert_eq!(
        policy.sender_routes.get("driver-bot").unwrap(),
        &vec!["member-bot".to_string()]
    );
}

#[tokio::test]
async fn participant_mode_route_allows_actor_self_update() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "member-bot".to_string(),
            BotCapabilities {
                name: Some("member-bot".to_string()),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("member-token".to_string(), "member-bot".to_string())
        .await;
    let groups = Arc::new(GroupStore::new());
    groups
        .upsert(Group::new(
            "group-mode",
            "member-bot",
            vec![Participant::bot("member-bot", ParticipantRole::Driver)],
        ))
        .await
        .unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .group(groups.clone())
        .group_management(Arc::new(
            GroupManagement::with_defaults(
                groups.clone(),
                registry,
                Arc::new(NoopFriendCoreService),
            )
            .with_bot_authority(shared_route_authority()),
        ))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/groups/group-mode/participants/member-bot/mode")
                .header("authorization", "Bearer member-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "mode": "muted"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["group_id"], "group-mode");
    assert_eq!(json["data"]["actor_id"], "member-bot");
    assert_eq!(json["data"]["mode"], "muted");

    let stored = groups.get("group-mode").await.unwrap();
    let participant = stored
        .participants
        .iter()
        .find(|participant| participant.bot_uuid == "member-bot")
        .unwrap();
    assert_eq!(
        participant.mode,
        Some(bcs_service_api::ParticipantMode::Muted)
    );
}
