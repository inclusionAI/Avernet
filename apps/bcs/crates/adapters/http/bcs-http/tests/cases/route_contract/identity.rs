use super::*;

fn actor_directory_use_cases(
    registry: Arc<dyn BotRegistryCoreService>,
    friend: Arc<dyn FriendCoreService>,
) -> Arc<dyn ActorDirectoryService> {
    let worker_profiles = Arc::new(EmptyWorkerProfileCoreService);
    let candidate_search = Arc::new(BotCandidateSearchCore::new(
        registry.clone(),
        friend.clone(),
        worker_profiles.clone(),
        0.0,
    ));
    Arc::new(ActorDirectory::new(
        registry,
        friend,
        noop_relation(),
        worker_profiles,
        candidate_search,
    ))
}

fn human_actor_use_cases(registry: Arc<dyn BotRegistryCoreService>) -> Arc<dyn HumanActorService> {
    Arc::new(HumanActor::new(registry, noop_relation()))
}
#[tokio::test]
async fn me_route_returns_mock_user_identity() {
    let app = build_router(
        HttpAppState::new(Services::noop()).with_user_identity(Arc::new(ChainUserIdentityPort::new(static_auth_chain("alice", "Alice")))),
    );

    let response = app
        .oneshot(Request::builder().uri("/me").body(Body::empty()).unwrap())
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["user_id"], "alice");
    assert_eq!(json["staff_no"], "alice");
    assert_eq!(json["nick_name"], "Alice");
    assert_eq!(json["actor_uuid"], "human_alice");
}

#[tokio::test]
async fn me_repair_info_route_returns_repair_response_for_fallback_human_name() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry.ensure_human_actor("alice", "alice").await.unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .human_actors(human_actor_use_cases(registry.clone()))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/me/repair-info")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["ok"], true);
    assert_eq!(json["actor_uuid"], "human_alice");
    assert_eq!(json["previous_name"], "alice");
    assert_eq!(json["current_name"], "Alice");
    assert_eq!(json["name_repaired"], true);
}

#[tokio::test]
async fn ensure_human_route_creates_current_user_actor() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let services = Services::builder()
        .registry(registry.clone())
        .human_actors(human_actor_use_cases(registry.clone()))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/me/ensure-human")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["actor_uuid"], "human_alice");
    assert_eq!(json["human_created"], true);
    assert_eq!(json["matched_bots"], serde_json::json!([]));
    assert_eq!(json["edges_created"], 0);
    assert_eq!(json["edges_upgraded"], 0);

    let stored = registry.get("human_alice").await.unwrap();
    assert_eq!(stored.actor_kind, bcs_service_api::ActorKind::Human);
    assert_eq!(stored.capabilities.name.as_deref(), Some("Alice"));
}

#[tokio::test]
async fn actor_status_route_allows_actor_self_update() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("actor-bot".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .store_token_mapping("actor-token".to_string(), "actor-bot".to_string())
        .await;
    let friend = Arc::new(StaticFriendCoreService::default());
    let services = Services::builder()
        .registry(registry.clone())
        .friend(friend.clone())
        .actor_directory(actor_directory_use_cases(registry.clone(), friend))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/actors/actor-bot/status")
                .header("authorization", "Bearer actor-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "status": "hidden"
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
    assert_eq!(json["data"]["actor_id"], "actor-bot");
    assert_eq!(json["data"]["status"], "hidden");
    assert_eq!(
        registry.get("actor-bot").await.unwrap().status,
        ActorStatus::Hidden
    );
}
#[tokio::test]
async fn actors_list_route_uses_cooperatable_filter_and_actor_shape() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, name, visibility, skills) in [
        ("current-bot", "Current", "public", vec!["self"]),
        ("public-bot", "Public", "public", vec!["search"]),
        ("friend-bot", "Friend", "private", vec!["ops"]),
        ("stranger-bot", "Stranger", "protected", vec!["hidden"]),
    ] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(name.to_string()),
                    summary: Some(format!("{name} summary")),
                    visibility: visibility.to_string(),
                    skills: skills.into_iter().map(Skill::new).collect(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    registry
        .register_streaming_connection("public-bot".to_string())
        .await
        .unwrap();
    let friend = Arc::new(StaticFriendCoreService::new(vec![(
        "current-bot",
        "friend-bot",
    )]));
    let services = Services::builder()
        .registry(registry.clone())
        .friend(friend.clone())
        .actor_directory(actor_directory_use_cases(registry, friend))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/actors/list?current_bot_uuid=current-bot&cooperatable_only=true&page_size=10&page_no=1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let bots = json["bots"].as_array().unwrap();
    let mut ids: Vec<String> = bots
        .iter()
        .map(|bot| bot["bot_uuid"].as_str().unwrap().to_string())
        .collect();
    ids.sort();
    assert_eq!(ids, vec!["friend-bot", "public-bot"]);
    assert_eq!(json["total"], 2);
    let public_bot = bots
        .iter()
        .find(|bot| bot["bot_uuid"] == "public-bot")
        .unwrap();
    assert_eq!(public_bot["capabilities"]["name"], "Public");
    assert_eq!(public_bot["dynamic_status"]["status"], "active");
    assert_eq!(public_bot["is_friend"], false);
    assert_eq!(public_bot["tags"], serde_json::json!({}));
    let friend_bot = bots
        .iter()
        .find(|bot| bot["bot_uuid"] == "friend-bot")
        .unwrap();
    assert_eq!(friend_bot["visibility"], "private");
    assert_eq!(friend_bot["is_friend"], true);
}

#[tokio::test]
async fn actors_search_route_falls_back_to_registry_when_no_worker_profile_port() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, name, visibility, skill) in [
        ("current-bot", "Current", "public", "self"),
        ("alpha-bot", "Alpha Helper", "public", "review"),
        ("private-alpha", "Alpha Private", "private", "secret"),
    ] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(name.to_string()),
                    summary: Some(format!("{name} summary")),
                    visibility: visibility.to_string(),
                    skills: vec![Skill::new(skill)],
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    let friend = Arc::new(StaticFriendCoreService::default());
    let services = Services::builder()
        .registry(registry.clone())
        .friend(friend.clone())
        .actor_directory(actor_directory_use_cases(registry, friend))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/actors/search?q=Alpha&current_bot_uuid=current-bot&cooperatable_only=false")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let bots = json["bots"].as_array().unwrap();
    assert_eq!(bots.len(), 1);
    assert_eq!(bots[0]["bot_uuid"], "alpha-bot");
    assert_eq!(bots[0]["score"], 0.0);
    assert!(
        bots[0]["short_profile"]
            .as_str()
            .unwrap()
            .contains("review")
    );
    assert_eq!(json["context"]["recommend_response"], Value::Null);
}
