use super::*;

#[derive(Default)]
struct RecordingVisibilitySyncPort {
    requests: Mutex<Vec<VisibilitySyncRequest>>,
    notify: Notify,
}

#[async_trait::async_trait]
impl VisibilitySyncPort for RecordingVisibilitySyncPort {
    async fn sync_visibility(&self, request: VisibilitySyncRequest) {
        self.requests.lock().await.push(request);
        self.notify.notify_waiters();
    }
}

struct SlowVisibilitySyncPort {
    requests: Mutex<Vec<VisibilitySyncRequest>>,
    notify: Notify,
    delay: std::time::Duration,
}

impl SlowVisibilitySyncPort {
    fn new(delay: std::time::Duration) -> Self {
        Self {
            requests: Mutex::new(Vec::new()),
            notify: Notify::new(),
            delay,
        }
    }
}

#[async_trait::async_trait]
impl VisibilitySyncPort for SlowVisibilitySyncPort {
    async fn sync_visibility(&self, request: VisibilitySyncRequest) {
        self.requests.lock().await.push(request);
        self.notify.notify_waiters();
        tokio::time::sleep(self.delay).await;
    }
}
fn bot_onboarding_use_cases(
    registry: Arc<dyn BotRegistryCoreService>,
) -> Arc<dyn BotOnboardingService> {
    Arc::new(BotOnboarding::new(registry, noop_relation(), true, None))
}
#[tokio::test]
async fn bots_route_lists_onboarded_bots_and_downgrades_skills() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                name: Some("Alpha".to_string()),
                summary: Some("Alpha bot".to_string()),
                skills: vec![
                    Skill::with_description("review", "Review code"),
                    Skill::new("ops"),
                ],
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register("bot-unonboarded".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let items = json.as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["bot_uuid"], "bot-alpha");
    assert_eq!(
        items[0]["capabilities"]["skills"],
        serde_json::json!(["review", "ops"])
    );
}

#[tokio::test]
async fn bots_paged_route_filters_by_user_suffix() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for bot_id in ["agent:alice", "agent:bob"] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    summary: Some("Test bot".to_string()),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/paged?user_id=alice&offset=0&limit=5")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["total"], 1);
    assert_eq!(json["items"].as_array().unwrap().len(), 1);
    assert_eq!(json["items"][0]["bot_uuid"], "agent:alice");
}

#[tokio::test]
async fn get_bot_route_returns_registration_and_effective_status() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                name: Some("Alpha".to_string()),
                summary: Some("Alpha bot".to_string()),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register_streaming_connection("bot-alpha".to_string())
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    // A Human caller reading a Bot that grants them NO
    // authority is denied (bot-authority plan carry
    // B): the legacy `created_by: None → allow` read
    // fallback no longer silently authorizes. (The
    // unauthenticated read keeps its public
    // projection elsewhere.)
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/bots/bot-alpha")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // The CURRENT owner reads the same route.
    shared_route_authority().seed_owner("bot-alpha", "alice");
    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/bot-alpha")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["bot_uuid"], "bot-alpha");
    assert_eq!(json["capabilities"]["name"], "Alpha");
    assert_eq!(json["actor_kind"], "bot");
    assert_eq!(json["status"], "online");
    assert_eq!(json["dynamic_status"]["status"], "active");
}
#[tokio::test]
async fn leave_bot_route_soft_deletes_owner_bot() {
    let temp_dir = TempDir::new().unwrap();
    // ONE shared authority store: the hook's seeded edges are
    // the same rows the store-level retirement re-verifies
    // inside its atomic span (store 事务内复验).
    let bot_repo = Arc::new(bcs_bot_store::MemoryBotRepo::with_base_dir(
        temp_dir.path().to_path_buf(),
    ));
    let authority = bcs_test_support::lazy_authority_chain_with_repo(bot_repo.clone());
    let registry = Arc::new(BotCore::with_repo(bot_repo));
    registry
        .register("bot-leave".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .save_created_by("bot-leave", "alice", true)
        .await
        .unwrap();
    authority.hook.seed_owner("bot-leave", "alice");
    // Same use-case wiring as the builder but against THIS
    // test's shared authority store.
    let registry_service: Arc<dyn BotRegistryCoreService> = registry.clone();
    let bot_use_cases = Arc::new(
        Bot::new_with_friend(
            registry_service.clone(),
            Arc::new(NoopFriendCoreService),
        )
        .with_bot_authority(authority.hook.clone()),
    );
    let services = Services::builder()
        .registry(registry_service)
        .friend(Arc::new(NoopFriendCoreService))
        .bot_query(bot_use_cases.clone())
        .bot_management(bot_use_cases.clone())
        .bot_discovery(bot_use_cases)
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("DELETE")
                .uri("/bots/bot-leave")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["left"], true);
    assert_eq!(json["bot_uuid"], "bot-leave");
    assert!(registry.get("bot-leave").await.is_none());
}
#[tokio::test]
async fn get_visibility_route_returns_normalized_visibility() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-visible".to_string(),
            BotCapabilities {
                visibility: "protected".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("visible-token".to_string(), "bot-visible".to_string())
        .await;
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/bot-visible/visibility")
                .header("authorization", "Bearer visible-token")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["success"], true);
    assert_eq!(json["data"]["bot_uuid"], "bot-visible");
    assert_eq!(json["data"]["visibility"], "protected");
}
#[tokio::test]
async fn set_visibility_route_updates_registry_and_triggers_sync_port() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-visible".to_string(),
            BotCapabilities {
                name: Some("Visible".to_string()),
                summary: Some("Visibility test".to_string()),
                domains: vec!["ops".to_string()],
                skills: vec![Skill::new("monitor")],
                visibility: "private".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("visible-token".to_string(), "bot-visible".to_string())
        .await;
    let sync = Arc::new(RecordingVisibilitySyncPort::default());
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services).with_visibility_sync(sync.clone()));

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/bots/bot-visible/visibility")
                .header("authorization", "Bearer visible-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "visibility": "protected"
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
    assert_eq!(json["data"]["bot_uuid"], "bot-visible");
    assert_eq!(json["data"]["visibility"], "protected");

    let stored = registry.get("bot-visible").await.unwrap();
    assert_eq!(stored.capabilities.visibility, "protected");

    let sync_request = tokio::time::timeout(std::time::Duration::from_secs(1), async {
        loop {
            let notified = sync.notify.notified();
            {
                let requests = sync.requests.lock().await;
                if let Some(request) = requests.first().cloned() {
                    return request;
                }
            }
            notified.await;
        }
    })
    .await
    .unwrap();
    assert_eq!(sync_request.bot_uuid, "bot-visible");
    assert_eq!(sync_request.visibility, "protected");
    assert_eq!(sync_request.capabilities.name.as_deref(), Some("Visible"));
    assert_eq!(
        sync_request.capabilities.summary.as_deref(),
        Some("Visibility test")
    );
    assert_eq!(sync_request.capabilities.domains, vec!["ops".to_string()]);
    assert_eq!(sync_request.capabilities.skills[0].name, "monitor");
}

#[tokio::test]
async fn set_visibility_route_returns_before_visibility_sync_finishes() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-visible".to_string(),
            BotCapabilities {
                visibility: "private".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("visible-token".to_string(), "bot-visible".to_string())
        .await;
    let sync = Arc::new(SlowVisibilitySyncPort::new(
        std::time::Duration::from_millis(250),
    ));
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let app = build_router(HttpAppState::new(services).with_visibility_sync(sync.clone()));

    let response = tokio::time::timeout(
        std::time::Duration::from_millis(100),
        app.oneshot(
            Request::builder()
                .method("PUT")
                .uri("/bots/bot-visible/visibility")
                .header("authorization", "Bearer visible-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "visibility": "protected"
                    })
                    .to_string(),
                ))
                .unwrap(),
        ),
    )
    .await
    .expect("visibility route should not wait for visibility sync")
    .unwrap();

    assert_eq!(response.status(), StatusCode::OK);

    let sync_request = tokio::time::timeout(std::time::Duration::from_secs(1), async {
        loop {
            let notified = sync.notify.notified();
            {
                let requests = sync.requests.lock().await;
                if let Some(request) = requests.first().cloned() {
                    return request;
                }
            }
            notified.await;
        }
    })
    .await
    .unwrap();
    assert_eq!(sync_request.bot_uuid, "bot-visible");
    assert_eq!(sync_request.visibility, "protected");
}

#[tokio::test]
async fn query_bots_route_filters_to_onboarded_and_returns_status_fields() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                name: Some("Alpha".to_string()),
                summary: Some("Alpha bot".to_string()),
                visibility: "private".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .register("bot-unonboarded".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    registry
        .register_streaming_connection("bot-alpha".to_string())
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases(registry).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/query")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuids": ["bot-alpha", "bot-unonboarded", "missing-bot"]
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
    let items = json.as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["bot_uuid"], "bot-alpha");
    assert_eq!(items[0]["visibility"], "private");
    assert_eq!(items[0]["actor_kind"], "bot");
    assert_eq!(items[0]["status"], "online");
    assert_eq!(items[0]["dynamic_status"]["status"], "active");
}

#[tokio::test]
async fn connect_bot_route_registers_http_connection() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/connect")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_id": "bot-alpha"
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
    assert_eq!(json["is_new"], true);
    assert_eq!(json["bot_uuid"], "bot-alpha");
    let token = json["token"].as_str().unwrap();
    assert_eq!(
        registry.find_bot_by_token(token).await.as_deref(),
        Some("bot-alpha")
    );
}

#[tokio::test]
async fn onboard_url_route_uses_adapter_config_and_encodes_query() {
    let app = build_router(HttpAppState::new(Services::noop()).with_onboard_url_config(
        Some("https://botchat.example.com/".to_string()),
        "/bcn/register".to_string(),
    ));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/onboard/url?token=t%201&name=Alpha%20Bot&summary=Hello%20World&skills=code,ops")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(
        json["onboard_url"],
        "https://botchat.example.com/bcn/register?token=t%201&name=Alpha%20Bot&summary=Hello%20World&skills=code%2Cops"
    );
}
#[tokio::test]
async fn bot_status_route_accepts_payload_without_retaining_it() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                name: Some("Alpha".to_string()),
                summary: Some("Alpha bot".to_string()),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "bot-alpha".to_string())
        .await;
    let services = services_builder_with_bot_use_cases(registry.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/status")
                .header("authorization", "Bearer token-1")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_uuid": "bot-alpha",
                        "status": {
                            "status": "busy",
                            "dynamic_summary": "running task",
                            "load": 0.75,
                            "updated_at": 42
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
    assert_eq!(json["updated"], true);
    assert_eq!(json["bot_uuid"], "bot-alpha");
    assert_eq!(json["status"]["status"], "busy");
    assert_eq!(json["status"]["dynamic_summary"], "running task");
    assert_eq!(json["status"]["load"], 0.75);
    assert_eq!(json["status"]["updated_at"], 42);

    let stored = registry.get("bot-alpha").await.unwrap();
    assert!(serde_json::to_value(stored).unwrap().get("dynamic_status").is_none());
}
#[tokio::test]
async fn discover_bots_route_filters_private_and_marks_collaboration_friends() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, name, visibility) in [
        ("caller-bot", "Caller", "public"),
        ("public-bot", "Public", "public"),
        ("friend-bot", "Friend", "protected"),
        ("stranger-bot", "Stranger", "protected"),
        ("private-bot", "Private", "private"),
    ] {
        registry
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(name.to_string()),
                    summary: Some(format!("{name} summary")),
                    visibility: visibility.to_string(),
                    ..BotCapabilities::default()
                },
            )
            .await
            .unwrap();
    }
    let friend = Arc::new(StaticFriendCoreService::new(vec![(
        "caller-bot",
        "friend-bot",
    )]));
    let services =
        services_builder_with_bot_use_cases_and_friend(registry, friend).build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/discover?collaborate_bot=caller-bot")
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
    assert_eq!(ids, vec!["caller-bot", "friend-bot", "public-bot"]);
    let friend = bots
        .iter()
        .find(|bot| bot["bot_uuid"] == "friend-bot")
        .unwrap();
    assert_eq!(friend["is_friend"], true);
    assert_eq!(json["count"], 3);
}
#[tokio::test]
async fn onboard_route_persists_capabilities_from_token_and_user_identity() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "bot-alpha".to_string())
        .await;
    let services = Services::builder()
        .registry(registry.clone())
        .bot_onboarding(bot_onboarding_use_cases(registry.clone()))
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/onboard")
                .header("authorization", "Bearer token-1")
                .header("x-agentclaw-agent-code", "agent-code-1")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "name": "Alpha",
                        "summary": "Alpha helper",
                        "domains": ["engineering"],
                        "skills": ["review"],
                        "scopes": ["repo"]
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
    assert_eq!(json["bot_uuid"], "bot-alpha");
    assert_eq!(json["onboarded"], true);
    assert_eq!(json["name"], "Alpha");
    assert_eq!(json["binding_results"], serde_json::json!({}));
    assert_eq!(json["unbound"], serde_json::json!([]));

    let stored = registry.get("bot-alpha").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("Alpha"));
    assert_eq!(stored.capabilities.summary.as_deref(), Some("Alpha helper"));
    assert_eq!(stored.capabilities.visibility, "public");
    assert_eq!(stored.created_by.as_deref(), Some("alice"));

    let credentials = registry.get_agent_credentials("bot-alpha").await.unwrap();
    assert_eq!(
        credentials.agent_code.as_deref(),
        Some("agent-code-1")
    );
    assert_eq!(
        credentials.agent_token.as_deref(),
        Some("Bearer token-1")
    );
}

#[tokio::test]
async fn onboard_route_returns_before_visibility_sync_finishes() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register(
            "bot-alpha".to_string(),
            BotCapabilities {
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
    registry
        .store_token_mapping("token-1".to_string(), "bot-alpha".to_string())
        .await;
    let services = Services::builder()
        .registry(registry.clone())
        .bot_onboarding(bot_onboarding_use_cases(registry))
        .build_for_test();
    let sync = Arc::new(SlowVisibilitySyncPort::new(
        std::time::Duration::from_millis(250),
    ));
    let app = build_router(HttpAppState::new(services).with_visibility_sync(sync.clone()));

    let response = tokio::time::timeout(
        std::time::Duration::from_millis(100),
        app.oneshot(
            Request::builder()
                .method("POST")
                .uri("/bots/onboard")
                .header("authorization", "Bearer token-1")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "name": "Alpha",
                        "summary": "Alpha helper"
                    })
                    .to_string(),
                ))
                .unwrap(),
        ),
    )
    .await
    .expect("onboard route should not wait for visibility sync")
    .unwrap();

    assert_eq!(response.status(), StatusCode::OK);

    let sync_request = tokio::time::timeout(std::time::Duration::from_secs(1), async {
        loop {
            let notified = sync.notify.notified();
            {
                let requests = sync.requests.lock().await;
                if let Some(request) = requests.first().cloned() {
                    return request;
                }
            }
            notified.await;
        }
    })
    .await
    .unwrap();
    assert_eq!(sync_request.bot_uuid, "bot-alpha");
}

#[tokio::test]
async fn admin_onboard_route_persists_existing_bot_without_token() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    registry
        .register("bot-admin".to_string(), BotCapabilities::default())
        .await
        .unwrap();
    let services = Services::builder()
        .registry(registry.clone())
        .bot_onboarding(bot_onboarding_use_cases(registry.clone()))
        .build_for_test();
    let chain = static_auth_chain("admin", "Admin");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/admin/bots/onboard")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_id": "bot-admin",
                        "name": "Admin Bot",
                        "summary": "Admin onboarded",
                        "domains": ["ops"],
                        "skills": ["deploy"],
                        "scopes": ["prod"]
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
    assert_eq!(json["bot_uuid"], "bot-admin");
    assert_eq!(json["onboarded"], true);
    assert_eq!(json["name"], "Admin Bot");

    let stored = registry.get("bot-admin").await.unwrap();
    assert_eq!(stored.capabilities.name.as_deref(), Some("Admin Bot"));
    assert_eq!(
        stored.capabilities.summary.as_deref(),
        Some("Admin onboarded")
    );
    assert_eq!(stored.created_by.as_deref(), Some("admin"));
}

#[tokio::test]
async fn admin_onboard_route_returns_onboarded_false_for_missing_bot() {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    let services = Services::builder()
        .registry(registry.clone())
        .bot_onboarding(bot_onboarding_use_cases(registry))
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/admin/bots/onboard")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "bot_id": "missing-bot",
                        "name": "Missing",
                        "summary": "Missing bot"
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
    assert_eq!(json["bot_uuid"], "missing-bot");
    assert_eq!(json["onboarded"], false);
}
