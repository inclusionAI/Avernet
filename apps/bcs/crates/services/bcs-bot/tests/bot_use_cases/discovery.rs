use super::*;

#[tokio::test]
async fn discover_bots_applies_visibility_and_friend_matrix() {
    let fixture = RegistryFixture::new();
    let service = fixture.service_with_friends(vec![("driver", "protected-friend")]);

    register_bot(
        &fixture.registry,
        "driver",
        caps(Some("Driver"), Some("planner"), "public"),
        Some("alice"),
    )
    .await;
    register_bot(
        &fixture.registry,
        "protected-friend",
        caps(Some("Planner Friend"), Some("planner"), "protected"),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "protected-stranger",
        caps(Some("Planner Stranger"), Some("planner"), "protected"),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "private-planner",
        caps(Some("Private Planner"), Some("planner"), "private"),
        None,
    )
    .await;

    let result = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("planner".to_string()),
            collaborate_bot: Some("driver".to_string()),
            requester_bot_id: Some("driver".to_string()),
            ..Default::default()
        })
        .await
        .unwrap();

    let ids = result
        .bots
        .iter()
        .map(|entry| (entry.bot_uuid.as_str(), entry.is_friend))
        .collect::<Vec<_>>();
    assert!(!ids.iter().any(|(id, _)| *id == "driver"));
    assert!(ids.contains(&("protected-friend", Some(true))));
    assert!(!ids.iter().any(|(id, _)| *id == "protected-stranger"));
    assert!(!ids.iter().any(|(id, _)| *id == "private-planner"));

    let protected = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("planner".to_string()),
            visibility: Some("protected".to_string()),
            collaborate_bot: Some("driver".to_string()),
            ..Default::default()
        })
        .await
        .unwrap();
    assert!(
        protected
            .bots
            .iter()
            .any(|entry| entry.bot_uuid == "protected-stranger" && entry.is_friend == Some(false))
    );
}

#[tokio::test]
async fn discover_bots_matches_one_skill_exactly_ignoring_case() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    for (bot_id, skill) in [
        ("exact-lower", "code_review"),
        ("exact-mixed", "Code_Review"),
        ("partial", "code_review_extended"),
        ("unrelated", "deployment"),
    ] {
        register_bot(
            &fixture.registry,
            bot_id,
            caps_with_skill(Some(bot_id), Some("review helper"), "public", skill),
            None,
        )
        .await;
    }

    let result = service
        .discover_bots(BotDiscoveryCommand {
            skills: vec!["code_review".to_string()],
            ..Default::default()
        })
        .await
        .expect("discover by exact skill");

    let bot_ids = result
        .bots
        .iter()
        .map(|entry| entry.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(bot_ids, vec!["exact-lower", "exact-mixed"]);
}

#[tokio::test]
async fn discover_bots_requires_all_exact_skills() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    for (bot_id, skills) in [
        ("all-exact", vec!["code_review", "SQL"]),
        ("missing-sql", vec!["code_review"]),
        ("partial-sql", vec!["code_review", "sql_extended"]),
    ] {
        register_bot(
            &fixture.registry,
            bot_id,
            caps_with_skills(
                Some(bot_id),
                Some("review helper"),
                "public",
                skills.as_slice(),
            ),
            None,
        )
        .await;
    }

    let result = service
        .discover_bots(BotDiscoveryCommand {
            skills: vec!["code_review".to_string(), "sql".to_string()],
            ..Default::default()
        })
        .await
        .expect("discover by all exact skills");

    let bot_ids = result
        .bots
        .iter()
        .map(|entry| entry.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(bot_ids, vec!["all-exact"]);
}

#[tokio::test]
async fn discover_bots_combines_q_and_all_skills() {
    let fixture = RegistryFixture::new();
    let service = fixture.service();

    for (bot_id, summary, skills) in [
        (
            "query-and-skills",
            "deployment helper",
            vec!["code_review", "sql"],
        ),
        ("query-one-skill", "deployment helper", vec!["code_review"]),
        (
            "skills-only",
            "documentation helper",
            vec!["code_review", "sql"],
        ),
    ] {
        register_bot(
            &fixture.registry,
            bot_id,
            caps_with_skills(Some(bot_id), Some(summary), "public", skills.as_slice()),
            None,
        )
        .await;
    }

    let result = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("deployment".to_string()),
            skills: vec!["code_review".to_string(), "sql".to_string()],
            ..Default::default()
        })
        .await
        .expect("discover by query and all exact skills");

    let bot_ids = result
        .bots
        .iter()
        .map(|entry| entry.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(bot_ids, vec!["query-and-skills"]);
}

#[tokio::test]
async fn discover_provider_bots_returns_provider_metadata_and_agent_code() {
    let fixture = ProviderRegistryFixture::new();
    let service = fixture.service();
    let provider = fixture
        .provider
        .register_provider(
            "Provider Directory".to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            ProviderAuthMode::AgentPass,
            "alice".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider");
    let (binding, _) = fixture
        .provider
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Provider Searcher".to_string(),
                summary: Some("Finds provider bots".to_string()),
                owners: vec!["alice".to_string()],
                provider_bot_ref: "agent-code-1".to_string(),
                skills: vec![
                    bcs_service_api::Skill::new("search"),
                    bcs_service_api::Skill::new("sql"),
                ],
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");
    fixture
        .provider
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Provider Searcher Missing Skill".to_string(),
                summary: Some("Finds provider bots".to_string()),
                owners: vec!["alice".to_string()],
                provider_bot_ref: "agent-code-2".to_string(),
                skills: vec![bcs_service_api::Skill::new("search")],
                ..Default::default()
            },
        )
        .await
        .expect("register provider query-only bot");
    fixture
        .provider
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Provider Searcher Partial Skill".to_string(),
                summary: Some("Finds provider bots".to_string()),
                owners: vec!["alice".to_string()],
                provider_bot_ref: "agent-code-3".to_string(),
                skills: vec![
                    bcs_service_api::Skill::new("search"),
                    bcs_service_api::Skill::new("sql_extended"),
                ],
                ..Default::default()
            },
        )
        .await
        .expect("register provider partial-skill bot");
    fixture
        .provider
        .register_provider_bot_with_bot_uuid(
            &provider.provider.provider_id,
            &provider.provider_admin_token,
            RegisterProviderBotParams {
                bot_name: "Provider Writer".to_string(),
                summary: Some("Writes documentation".to_string()),
                owners: vec!["alice".to_string()],
                provider_bot_ref: "agent-code-4".to_string(),
                skills: vec![
                    bcs_service_api::Skill::new("search"),
                    bcs_service_api::Skill::new("sql"),
                ],
                ..Default::default()
            },
        )
        .await
        .expect("register provider skills-only bot");

    let result = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("searcher".to_string()),
            skills: vec!["search".to_string(), "sql".to_string()],
            ..Default::default()
        })
        .await
        .expect("discover provider bot");

    assert_eq!(result.count, 1);
    let entry = &result.bots[0];
    assert_eq!(entry.bot_uuid, binding.bot_uuid);
    assert_eq!(entry.agent_code.as_deref(), Some("agent-code-1"));
    let provider_info = entry.provider_info.as_ref().expect("provider info");
    assert_eq!(provider_info.provider_id, provider.provider.provider_id);
    assert_eq!(provider_info.provider_name, "Provider Directory");
}

#[tokio::test]
async fn discovery_ignores_heartbeat_summary_and_keeps_static_metadata() {
    let fixture = RegistryFixture::new();
    register_bot(&fixture.registry, "summary-bot",
        caps(Some("Database Helper"), Some("Static SQL expertise"), "public"), None).await;
    fixture.registry.update_status("summary-bot").await;
    let service = fixture.service();
    let ignored = service.discover_bots(BotDiscoveryCommand {
        q: Some("ephemeral-only-marker".into()), ..Default::default()
    }).await.unwrap();
    assert_eq!(ignored.count, 0);
    let matched = service.discover_bots(BotDiscoveryCommand {
        q: Some("Static SQL".into()), ..Default::default()
    }).await.unwrap();
    assert_eq!(matched.count, 1);
    assert_eq!(matched.bots[0].bot_uuid, "summary-bot");
}
