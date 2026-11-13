use super::*;

#[tokio::test]
async fn organization_scoped_discovery_filters_effective_members_and_attaches_metadata() {
    let fixture = RegistryFixture::new();
    let registry: Arc<dyn BotRegistryCoreService> = fixture.registry.clone();
    let organization = Arc::new(StaticOrganizationCoreService::with_members(vec![
        org_member("bot-a", "traffic_analyst"),
        org_member("bot-b", "traffic_analyst"),
        org_member("bot-c", "traffic_analyst"),
        org_member("bot-d", "traffic_analyst"),
        org_member("bot-e", "traffic_analyst"),
        org_member("bot-f", "traffic_analyst"),
    ]));
    let service = Bot::new_with_friend(
        registry,
        Arc::new(StaticFriendCoreService::new(vec![("bot-a", "bot-d")])),
    )
    .with_bot_core(fixture.registry.clone())
    .with_organization(organization);

    register_bot(&fixture.registry, "bot-a", caps(Some("Requester"), Some("traffic"), "public"), None).await;
    register_bot(
        &fixture.registry,
        "bot-b",
        caps_with_skills(
            Some("Traffic Public"),
            Some("traffic"),
            "protected",
            &["routing", "sql"],
        ),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "bot-c",
        caps_with_skills(
            Some("Traffic Private"),
            Some("traffic"),
            "private",
            &["routing", "sql"],
        ),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "bot-d",
        caps_with_skills(
            Some("Traffic Friend"),
            Some("traffic"),
            "private",
            &["routing", "sql"],
        ),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "bot-e",
        caps_with_skills(
            Some("Traffic Missing Skill"),
            Some("traffic"),
            "public",
            &["routing"],
        ),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "bot-f",
        caps_with_skills(
            Some("Traffic Partial Skill"),
            Some("traffic"),
            "public",
            &["routing", "sql_extended"],
        ),
        None,
    )
    .await;
    register_bot(
        &fixture.registry,
        "bot-x",
        caps_with_skills(
            Some("Traffic Global"),
            Some("traffic"),
            "public",
            &["routing", "sql"],
        ),
        None,
    )
    .await;

    let result = service
        .discover_bots(BotDiscoveryCommand {
            q: Some("traffic".to_string()),
            skills: vec!["routing".to_string(), "sql".to_string()],
            requester_bot_id: Some("bot-a".to_string()),
            organization_code: Some("promo-2026".to_string()),
            role: Some("traffic_analyst".to_string()),
            ..Default::default()
        })
        .await
        .expect("scoped discovery");

    let ids = result
        .bots
        .iter()
        .map(|entry| entry.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(ids, vec!["bot-b", "bot-d"]);
    assert!(result.bots.iter().all(|entry| entry.organization_member.as_ref().is_some_and(|member| {
        member.organization_code == "promo-2026" && member.role.as_deref() == Some("traffic_analyst")
    })));
    assert_eq!(result.bots[0].is_friend, Some(false));
    assert_eq!(result.bots[1].is_friend, Some(true));
}

#[tokio::test]
async fn organization_scoped_discovery_rejects_role_without_org_and_nonmember_requester() {
    let fixture = RegistryFixture::new();
    let registry: Arc<dyn BotRegistryCoreService> = fixture.registry.clone();
    let service = Bot::new(registry).with_organization(Arc::new(StaticOrganizationCoreService::rejecting_requester()));

    let role_without_org = service
        .discover_bots(BotDiscoveryCommand {
            role: Some("traffic_analyst".to_string()),
            ..Default::default()
        })
        .await
        .expect_err("role without organization should fail");
    assert!(matches!(role_without_org, BotUseCaseError::Service(ServiceError::InvalidOperation { message, .. }) if message == "role_requires_organization_code"));

    let nonmember = service
        .discover_bots(BotDiscoveryCommand {
            requester_bot_id: Some("bot-a".to_string()),
            organization_code: Some("promo-2026".to_string()),
            ..Default::default()
        })
        .await
        .expect_err("nonmember requester should fail");
    assert!(matches!(nonmember, BotUseCaseError::Service(ServiceError::Forbidden(reason)) if reason == "organization_member_required"));
}

#[tokio::test]
async fn organization_scoped_discovery_keeps_members_after_provider_state_changes() {
    let data_dir = tempfile::tempdir().expect("temp data dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let providers: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let candidate_reads: Arc<dyn bcs_service_api::OrganizationCandidateReadPort> = provider_store.clone();
    let registry = Arc::new(BotCore::with_provider_repos(
        Arc::new(MemoryBotRepo::with_base_dir(data_dir.path().to_path_buf())),
        providers.clone(),
        credentials.clone(),
        bindings.clone(),
    ));
    let provider_core = Arc::new(ProviderCore::new(
        providers.clone(),
        credentials,
        bindings.clone(),
        registry.clone(),
    ));
    let organization_core = Arc::new(OrganizationCore::new(
        "test".to_string(),
        Arc::new(MemoryOrganizationRepo::new()),
        providers,
        bindings,
        candidate_reads,
        registry.clone(),
    ));
    let organization_management = OrganizationManagement::new(
        provider_core.clone(),
        organization_core.clone(),
    );
    let service = Bot::new_with_friend(
        registry.clone(),
        Arc::new(StaticFriendCoreService::default()),
    )
    .with_bot_core(registry.clone())
    .with_organization(organization_core);

    let provider_a = register_organization_provider(&provider_core, "Provider A").await;
    let provider_b = register_organization_provider(&provider_core, "Provider B").await;
    let provider_c = register_organization_provider(&provider_core, "Provider C").await;
    for (resource, managers) in [
        (&provider_b, vec![provider_a.provider_id.clone()]),
        (&provider_c, vec![provider_a.provider_id.clone()]),
    ] {
        provider_core
            .update_provider(
                &resource.provider_id,
                &resource.admin_token,
                "11111111",
                None,
                None,
                None,
                None,
                Some(ProviderOrganizationManagementConfig {
                    authorized_manager_provider_ids: managers,
                }),
            )
            .await
            .expect("grant manager");
    }
    register_organization_bot(&provider_core, &provider_a, "bot-a").await;
    register_organization_bot(&provider_core, &provider_b, "bot-b").await;
    register_organization_bot(&provider_core, &provider_c, "bot-c").await;
    organization_management
        .create(CreateOrganizationCommand {
            auth: organization_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            name: "Promo 2026".to_string(),
            description: None,
        })
        .await
        .expect("create organization");
    for (bot_uuid, role) in [
        ("bot-a", "planner"),
        ("bot-b", "traffic_analyst"),
        ("bot-c", "traffic_analyst"),
    ] {
        organization_management
            .put_member(PutOrganizationMemberCommand {
                auth: organization_member_auth(&provider_a),
                organization_code: "promo-2026".to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: Some(role.to_string()),
            })
            .await
            .expect("add organization member");
    }

    provider_core
        .update_provider(
            &provider_c.provider_id,
            &provider_c.admin_token,
            "11111111",
            None,
            None,
            None,
            None,
            Some(ProviderOrganizationManagementConfig {
                authorized_manager_provider_ids: Vec::new(),
            }),
        )
        .await
        .expect("revoke manager grant");
    provider_core
        .set_provider_bot_disabled(
            &provider_b.provider_id,
            "bot-b",
            &provider_b.admin_token,
            true,
        )
        .await
        .expect("disable provider binding");

    let result = service
        .discover_bots(BotDiscoveryCommand {
            requester_bot_id: Some("bot-a".to_string()),
            organization_code: Some("promo-2026".to_string()),
            role: Some("traffic_analyst".to_string()),
            ..Default::default()
        })
        .await
        .expect("discover organization members");

    let ids = result
        .bots
        .iter()
        .map(|bot| bot.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(ids, vec!["bot-b", "bot-c"]);
}
