use super::*;

#[tokio::test]
async fn list_bots_filters_paginates_and_maps_dtos() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "a-alpha",
        caps(Some("Alpha"), Some("Named bot"), "public"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "b-default:alice",
        caps(None, Some("Default helper"), "private"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "c-charlie",
        caps(Some("Charlie"), Some("Third bot"), "protected"),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "d-empty",
        caps(None, None, "protected"),
        None,
    )
    .await;
    fixture
        .registry
        .update_actor_status("b-default:alice", ActorStatus::Hidden)
        .await
        .expect("hide default bot");

    let result = service
        .list_bots(BotListCommand {
            caller_actor_id: Some("alice".to_string()),
            offset: 1,
            limit: 1,
            onboarded: Some(true),
        })
        .await
        .expect("list onboarded bots");

    assert_eq!(result.total, 3);
    assert_eq!(result.offset, 1);
    assert_eq!(result.limit, 1);
    assert_eq!(result.bots.len(), 1);
    let entry = &result.bots[0];
    assert_eq!(entry.bot_uuid, "b-default:alice");
    assert_eq!(entry.name, None);
    assert_eq!(entry.summary.as_deref(), Some("Default helper"));
    assert_eq!(entry.status, ActorStatus::Hidden);
    assert_eq!(entry.visibility, "private");
    assert_eq!(entry.owner_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(entry.created_by.as_deref(), Some("alice"));
    assert_eq!(
        entry.capabilities.summary.as_deref(),
        Some("Default helper")
    );

    let unonboarded = service
        .list_bots(BotListCommand {
            caller_actor_id: None,
            offset: 0,
            limit: 10,
            onboarded: Some(false),
        })
        .await
        .expect("list unonboarded bots");

    assert_eq!(unonboarded.total, 2);
    let unonboarded_ids: Vec<&str> = unonboarded
        .bots
        .iter()
        .map(|entry| entry.bot_uuid.as_str())
        .collect();
    assert_eq!(unonboarded_ids, vec!["b-default:alice", "d-empty"]);
}

#[tokio::test]
async fn get_bot_returns_detail_dto_and_not_found_error() {
    let fixture = RegistryFixture::new();
    fixture.seed_owner("detail-bot", "alice");
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "detail-bot",
        caps(Some("Detail"), Some("Detail summary"), "private"),
        Some("alice"),
    )
    .await;
    fixture
        .registry
        .update_actor_status("detail-bot", ActorStatus::Hidden)
        .await
        .expect("hide detail bot");

    let detail = service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "detail-bot".to_string(),
        })
        .await
        .expect("bot detail");

    assert_eq!(detail.bot_uuid, "detail-bot");
    assert_eq!(detail.capabilities.name.as_deref(), Some("Detail"));
    assert_eq!(
        detail.capabilities.summary.as_deref(),
        Some("Detail summary")
    );
    assert_eq!(detail.status, ActorStatus::Hidden);
    assert_eq!(detail.visibility, "private");
    assert_eq!(detail.owner_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(detail.created_by.as_deref(), Some("alice"));
    assert_eq!(detail.actor_kind, ActorKind::Bot);
    assert!(detail.env.is_some());
    assert_eq!(detail.dynamic_status.status, "offline");

    let missing = service
        .get_bot(BotDetailCommand {
            caller_actor_id: None,
            bot_id: "missing-bot".to_string(),
        })
        .await;

    assert!(matches!(
        missing,
        Err(BotUseCaseError::Service(ServiceError::BotNotFound(id)))
            if id == "missing-bot"
    ));
}

#[tokio::test]
async fn get_bot_rejects_private_bot_owned_by_other_user() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "private-bot",
        caps(Some("Private"), Some("Private summary"), "private"),
        Some("alice"),
    )
    .await;

    let result = service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some("human_bob".to_string()),
            bot_id: "private-bot".to_string(),
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Forbidden(message))
            if message == "Not authorized to access bot 'private-bot'"
    ));
}

#[tokio::test]
async fn get_bot_reports_effective_active_status_for_connected_online_bot() {
    let fixture = RegistryFixture::new();
    fixture.seed_owner("active-bot", "alice");
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "active-bot",
        caps(Some("Active"), Some("Connected bot"), "public"),
        Some("alice"),
    )
    .await;
    fixture
        .registry
        .register_streaming_connection("active-bot".to_string())
        .await
        .expect("streaming connection");

    let detail = service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "active-bot".to_string(),
        })
        .await
        .expect("bot detail");

    assert_eq!(detail.dynamic_status.status, "active");
}

#[tokio::test]
async fn provider_http_bot_query_views_are_active_without_ws_connection() {
    let fixture = ProviderRegistryFixture::new();
    let bot_id = fixture
        .register_provider_bot("11111111")
        .await
        .clone();
    fixture.authority.seed_owner(&bot_id, "11111111");
    let service = fixture.service();

    assert!(!fixture.registry.is_connected(&bot_id).await);

    let detail = service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some("human_11111111".to_string()),
            bot_id: bot_id.clone(),
        })
        .await
        .expect("provider bot detail");
    assert_eq!(detail.dynamic_status.status, "active");

    let mine = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "11111111".to_string(),
            offset: 0,
            limit: 10,
            active_only: false,
        })
        .await
        .expect("my provider bots");
    let my_bot = mine
        .items
        .iter()
        .find(|entry| entry.bot_uuid == bot_id)
        .expect("provider bot in my bots");
    assert_eq!(my_bot.dynamic_status.status, "active");

    let active_mine = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "11111111".to_string(),
            offset: 0,
            limit: 10,
            active_only: true,
        })
        .await
        .expect("active my provider bots");
    assert_eq!(active_mine.total, 1);
    assert_eq!(active_mine.items[0].bot_uuid, bot_id);

    let queried = service
        .query_bots_by_ids(BotQueryByIdsCommand {
            bot_ids: vec![bot_id.clone()],
        })
        .await
        .expect("provider bot query");
    assert_eq!(queried.bots.len(), 1);
    assert_eq!(queried.bots[0].dynamic_status.status, "active");
}

#[tokio::test]
async fn extended_query_methods_page_creator_and_query_by_ids() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "agent:alice",
        caps(Some("Alice Agent"), Some("Owned"), "public"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "agent:bob",
        caps(Some("Bob Agent"), Some("Owned"), "public"),
        Some("bob"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "draft:alice",
        caps(None, Some("Draft"), "public"),
        Some("alice"),
    )
    .await;
    fixture
        .registry
        .register_streaming_connection("agent:alice".to_string())
        .await
        .expect("connect alice agent");

    let paged = service
        .list_bots_paged(BotPagedListCommand {
            user_id: Some("alice".to_string()),
            offset: 0,
            limit: 10,
        })
        .await
        .unwrap();
    assert_eq!(paged.total, 2);
    let alice_agent = paged
        .items
        .iter()
        .find(|entry| entry.bot_uuid == "agent:alice")
        .expect("alice agent in page");
    assert_eq!(alice_agent.dynamic_status.status, "active");

    let mine = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "bob".to_string(),
            offset: 0,
            limit: 10,
            active_only: false,
        })
        .await
        .unwrap();
    assert_eq!(mine.total, 1);
    assert_eq!(mine.items[0].bot_uuid, "agent:bob");

    let queried = service
        .query_bots_by_ids(BotQueryByIdsCommand {
            bot_ids: vec![
                "draft:alice".to_string(),
                "agent:bob".to_string(),
                "missing".to_string(),
            ],
        })
        .await
        .unwrap();
    assert_eq!(queried.bots.len(), 1);
    assert_eq!(queried.bots[0].bot_uuid, "agent:bob");
}

#[tokio::test]
async fn my_bots_active_only_filters_runtime_active_and_ignores_hidden() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    register_bot(
        &fixture.registry,
        "connected-hidden",
        caps(Some("Connected Hidden"), Some("Owned"), "public"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "disconnected",
        caps(Some("Disconnected"), Some("Owned"), "public"),
        Some("alice"),
    )
    .await;
    fixture
        .registry
        .register_streaming_connection("connected-hidden".to_string())
        .await
        .expect("connect hidden bot");
    fixture
        .registry
        .update_actor_status("connected-hidden", ActorStatus::Hidden)
        .await
        .expect("hide connected bot");

    let all = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "alice".to_string(),
            offset: 0,
            limit: 10,
            active_only: false,
        })
        .await
        .expect("all my bots");
    assert_eq!(all.total, 2);
    assert_eq!(all.items[0].bot_uuid, "connected-hidden");
    assert_eq!(all.items[0].dynamic_status.status, "active");
    assert_eq!(all.items[0].status, ActorStatus::Hidden);
    assert_eq!(all.items[1].bot_uuid, "disconnected");
    assert_eq!(all.items[1].dynamic_status.status, "offline");

    let active = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "alice".to_string(),
            offset: 0,
            limit: 10,
            active_only: true,
        })
        .await
        .expect("active my bots");
    assert_eq!(active.total, 1);
    assert_eq!(active.items[0].bot_uuid, "connected-hidden");
    assert_eq!(active.items[0].dynamic_status.status, "active");
}

#[tokio::test]
async fn legacy_my_bots_expose_the_sampled_access_relation() {
    // The legacy mine page (control-plane-wired Bot)
    // labels every union entry with its sampled
    // authority relation (spec §8 mine row: 逐项
    // `access_relation=owner|manager`; the Human
    // self entry keeps `self`).
    let data_dir = tempfile::tempdir().expect("temp data dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(
        data_dir.path().to_path_buf(),
    ));
    let core = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane =
        Arc::new(bcs_bot::BotControlPlaneCore::new(
            repo.clone(),
            providers.clone(),
            providers,
        ));
    let service =
        Bot::new(core.clone() as Arc<dyn bcs_service_api::BotRegistryCoreService>)
            .with_control_plane(
                control_plane
                    as Arc<dyn bcs_service_api::BotControlPlaneCoreService>,
            );

    core
        .register(
            "bot-owned".to_string(),
            caps(Some("Owned"), Some("owned bot"), "public"),
        )
        .await
        .expect("register owned bot");
    core
        .register(
            "bot-managed".to_string(),
            caps(Some("Managed"), Some("managed bot"), "public"),
        )
        .await
        .expect("register managed bot");
    core
        .ensure_human_actor("alice", "alice")
        .await
        .expect("self row");
    bcs_test_support::grant_owner_edge(&repo, "bot-owned", "alice").await;
    bcs_test_support::grant_manager_edge(&repo, "bot-managed", "alice").await;

    let mine = service
        .list_my_bots(bcs_service_api::MyBotsCommand {
            staff_no: "alice".to_string(),
            offset: 0,
            limit: 10,
            active_only: false,
        })
        .await
        .expect("my bots");
    let relation_of = |bot_uuid: &str| {
        mine.items
            .iter()
            .find(|entry| entry.bot_uuid == bot_uuid)
            .and_then(|entry| entry.access_relation.clone())
            .unwrap_or_else(|| {
                panic!("'{bot_uuid}' missing from the mine page")
            })
    };
    assert_eq!(relation_of("bot-owned"), "owner");
    assert_eq!(relation_of("bot-managed"), "manager");
    assert_eq!(relation_of("human_alice"), "self");
}
