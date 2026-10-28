use super::*;

#[tokio::test]
async fn candidates_require_a_human_owner_and_allow_the_current_human_actor() {
    let fixture = Fixture::new();
    fixture
        .add_bot("acting", "staff-1", "private", ActorStatus::Online)
        .await;
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");
    fixture
        .repo
        .ensure_human_actor("staff-2", "Other Human")
        .await
        .expect("ensure other human");

    let error = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-2"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect_err("non-owner must fail");
    assert_eq!(error.code(), "forbidden");

    let page = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("current human actor may select candidates");
    assert_eq!(page.total, 0);

    let error = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-2".to_string(),
            purpose: BotCandidatePurpose::Discovery,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect_err("another human actor must fail");
    assert_eq!(error.code(), "forbidden");
}
#[tokio::test]
async fn human_actor_collaboration_candidates_include_only_private_friends() {
    let fixture = Fixture::new();
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");
    fixture
        .add_bot("private-friend", "staff-2", "private", ActorStatus::Hidden)
        .await;
    fixture
        .add_bot(
            "private-stranger",
            "staff-3",
            "private",
            ActorStatus::Online,
        )
        .await;
    fixture
        .friends
        .add_friendship("human_staff-1", "private-friend")
        .await
        .expect("add human friendship");

    let page = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            purpose: BotCandidatePurpose::Collaboration,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("list human candidates");

    assert_eq!(page.total, 1);
    assert_eq!(page.items[0].bot.bot_id, "private-friend");
    assert!(page.items[0].is_friend);
}
#[tokio::test]
async fn collaboration_candidates_include_private_friends_without_status_filtering() {
    let fixture = Fixture::new();
    fixture
        .add_bot("acting", "staff-1", "private", ActorStatus::Online)
        .await;
    fixture
        .add_bot("private-friend", "staff-2", "private", ActorStatus::Hidden)
        .await;
    fixture
        .friends
        .add_friendship("acting", "private-friend")
        .await
        .expect("add friendship");

    let page = fixture
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Collaboration,
            name: Some(" FRIEND ".to_string()),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("list candidates");
    assert_eq!(page.total, 1);
    assert_eq!(page.items[0].bot.bot_id, "private-friend");
    assert_eq!(page.items[0].bot.status, BotStatus::Hidden);
    assert_eq!(page.items[0].bot.reachability, BotReachability::Unreachable);
    assert!(page.items[0].is_friend);
}
#[tokio::test]
async fn eligible_candidates_use_edge_permission_but_keep_the_same_projection() {
    let env = bcs_config::resolve_env_str();
    let record = BotControlPlaneRecord {
        bot_id: "acting".to_string(),
        kind: ActorKind::Bot,
        name: "Acting".to_string(),
        visibility: "private".to_string(),
        status: ActorStatus::Online,
        env: env.clone(),
        created_by: Some("staff-1".to_string()),
        descriptor: BotControlPlaneDescriptor {
            summary: String::new(),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
        },
        agent_code: None,
        task_claim_mode: false,
        task_dream_mode: false,
        created_at: 1,
        updated_at: 1,
        user_visibility: Default::default(),
        friend_ext: Default::default(),
        friend_check_in_strategy: Default::default(),
    };
    let control_plane = Arc::new(RecordingBotControlPlane::new(record));
    let connect = Arc::new(RecordingConnectService::new(vec![
        FriendListEntry {
            actor_id: "bot-friend".to_string(),
            name: Some("Bot Friend".to_string()),
            summary: Some("bot summary".to_string()),
            is_online: true,
            kind: ActorKind::Bot,
        },
        FriendListEntry {
            actor_id: "human-friend".to_string(),
            name: Some("Human Friend".to_string()),
            summary: Some("human summary".to_string()),
            is_online: true,
            kind: ActorKind::Human,
        },
    ]));
        let eligible_authority_repo = Arc::new(MemoryBotRepo::new());
    bcs_test_support::grant_owner_edge(&eligible_authority_repo, "acting", "staff-1").await;
let service = BotServiceImpl::new(
        control_plane.clone(),
        Arc::new(NoopBotRegistryCoreService),
        Arc::new(NoopFriendCoreService),
        connect,
        Arc::new(RecordingCandidateSearch::empty()),
        BotServiceConfig { env: env.clone() },
    )
    // Row 1: the acting Bot's CURRENT owner edge
    // lives in the authority store; staff-1 holds
    // it, staff-2 does not.
    .with_bot_authority_hook(bcs_test_support::authority_hook_for(
        eligible_authority_repo.clone(),
    ));

    let page = service
        .list_eligible_candidates(ListBotCandidates {
            caller: human_caller("staff-1"),
            bot_id: "acting".to_string(),
            purpose: BotCandidatePurpose::Collaboration,
            name: Some(" FRIEND ".to_string()),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("eligible candidates");

    assert_eq!(page.total, 0);
    let queries = control_plane.queries.lock().expect("queries lock");
    let query = queries.first().expect("recorded query");
    assert!(query.friend_ids.contains("bot-friend"));
    assert!(!query.friend_ids.contains("human-friend"));
    assert_eq!(query.name.as_deref(), Some("FRIEND"));
}
#[tokio::test]
async fn query_preserves_first_occurrence_and_projects_both_kinds_provider_and_reachability() {
    let fixture = Fixture::new();
    fixture
        .add_bot("physical", "staff-1", "private", ActorStatus::Online)
        .await;
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");
    fixture
        .repo
        .register_streaming_connection("physical".to_string())
        .await
        .expect("connect physical bot");
    fixture
        .providers
        .insert_provider(ProviderRecord {
            provider_id: "provider-1".to_string(),
            slug: None,
            name: "Provider One".to_string(),
            config: "{}".to_string(),
            created_by: "staff-1".to_string(),
            owners: "[]".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("insert provider");
    fixture
        .providers
        .insert_binding(ProviderBotBinding {
            webhook_url: None,
            bot_uuid: "physical".to_string(),
            provider_id: "provider-1".to_string(),
            provider_bot_ref: "secret-internal-ref".to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("insert binding");

    let bots = fixture
        .service
        .query(QueryBots {
            caller: human_caller("staff-2"),
            bot_ids: vec![
                "human_staff-1".to_string(),
                "missing".to_string(),
                "physical".to_string(),
                "human_staff-1".to_string(),
            ],
        })
        .await
        .expect("query bots");
    assert_eq!(
        bots.iter().map(Bot::bot_id).collect::<Vec<_>>(),
        vec!["human_staff-1", "physical"]
    );
    let Bot::Human(human) = &bots[0] else {
        panic!("expected human bot");
    };
    assert_eq!(human.user_visibility, UserVisibility::Protected);
    assert_eq!(human.friend_ext, serde_json::Map::new());
    assert_eq!(human.friend_check_in_strategy, FriendCheckInStrategy::Approval);
    let Bot::Physical(physical) = &bots[1] else {
        panic!("expected physical bot");
    };
    assert_eq!(physical.reachability, BotReachability::Reachable);
    assert_eq!(physical.user_visibility, UserVisibility::Protected);
    assert_eq!(physical.friend_ext, serde_json::Map::new());
    assert_eq!(physical.friend_check_in_strategy, FriendCheckInStrategy::Approval);
    assert_eq!(
        physical.provider.as_ref().map(|p| p.name.as_str()),
        Some("Provider One")
    );
    assert_eq!(physical.agent_code.as_deref(), Some("agent-physical"));
}
#[tokio::test]
async fn update_requires_created_by_and_rejects_descriptor_for_human() {
    let fixture = Fixture::new();
    fixture
        .add_bot("owned", "staff-1", "public", ActorStatus::Online)
        .await;
    fixture
        .repo
        .ensure_human_actor("staff-1", "Human")
        .await
        .expect("ensure human");

    let error = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-2"),
            bot_id: "owned".to_string(),
            patch: BotPatch {
                name: Some("Nope".to_string()),
                ..Default::default()
            },
        })
        .await
        .expect_err("non-owner update");
    assert_eq!(error.code(), "forbidden");

    let error = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            patch: BotPatch {
                descriptor: Some(BotDescriptorPatch {
                    summary: Some("not allowed".to_string()),
                    ..Default::default()
                }),
                ..Default::default()
            },
        })
        .await
        .expect_err("human descriptor update");
    assert_eq!(error.code(), "invalid_bot_kind");

    let error = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-1"),
            bot_id: "human_staff-1".to_string(),
            patch: BotPatch {
                task_claim_mode: Some(true),
                ..Default::default()
            },
        })
        .await
        .expect_err("human task-mode update");
    assert_eq!(error.code(), "invalid_bot_kind");

    let updated = fixture
        .service
        .update(UpdateBot {
            caller: human_caller("staff-1"),
            bot_id: "owned".to_string(),
            patch: BotPatch {
                name: Some(" Renamed ".to_string()),
                visibility: Some(BotVisibility::Protected),
                status: Some(BotStatus::Hidden),
                descriptor: Some(BotDescriptorPatch {
                    domains: Some(vec![]),
                    scopes: Some(vec!["new-scope".to_string()]),
                    ..Default::default()
                }),
                task_claim_mode: None,
                task_dream_mode: None,
                user_visibility: Some(UserVisibility::Private),
                friend_ext: Some(serde_json::Map::from_iter([(
                    "no_check_scope_friend_deps".to_string(),
                    serde_json::json!(["dep-1"]),
                )])),
                friend_check_in_strategy: Some(FriendCheckInStrategy::DeptFree),
            },
        })
        .await
        .expect("owner update");
    let Bot::Physical(updated) = updated else {
        panic!("expected physical bot");
    };
    assert_eq!(updated.name, "Renamed");
    assert_eq!(updated.visibility, BotVisibility::Protected);
    assert_eq!(updated.status, BotStatus::Hidden);
    assert_eq!(updated.user_visibility, UserVisibility::Private);
    assert_eq!(updated.friend_check_in_strategy, FriendCheckInStrategy::DeptFree);
    assert_eq!(
        updated.friend_ext["no_check_scope_friend_deps"],
        serde_json::json!(["dep-1"])
    );
    assert!(updated.descriptor.domains.is_empty());
    assert_eq!(updated.descriptor.scopes, vec!["new-scope"]);
}
