use std::sync::Arc;

use bcs_bot::core::{BotCore, ProviderCore};
use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_organization::{OrganizationCore, OrganizationManagement};
use bcs_organization_store::MemoryOrganizationRepo;
use bcs_service_api::{
    BotRegistryCoreService, CreateOrganizationCommand, OrganizationAuth,
    OrganizationCandidateQuery, OrganizationCoreService, OrganizationManagementService, ProviderAuthMode,
    ProviderBotBindingRepoPort, ProviderBotCoreService, ProviderCoreService,
    ProviderCredentialRepoPort, ProviderOrganizationManagementConfig, ProviderRepoPort,
    PutOrganizationMemberCommand, RegisterProviderBotParams, ServiceError, Skill,
    UpdateOrganizationCommand,
};

struct ProviderFixture {
    provider_id: String,
    admin_token: String,
}

struct TestContext {
    service: OrganizationManagement,
    core: Arc<OrganizationCore>,
    registry: Arc<BotCore>,
    provider_core: Arc<ProviderCore>,
    _temp_dir: tempfile::TempDir,
}

fn provider_auth(provider: &ProviderFixture) -> OrganizationAuth {
    OrganizationAuth {
        provider_id: provider.provider_id.clone(),
        provider_admin_token: provider.admin_token.clone(),
    }
}

fn bad_provider_auth(provider: &ProviderFixture) -> OrganizationAuth {
    OrganizationAuth {
        provider_id: provider.provider_id.clone(),
        provider_admin_token: "not-a-real-admin-token".to_string(),
    }
}

async fn test_context() -> TestContext {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let providers: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        providers.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let provider_core = Arc::new(ProviderCore::new(
        providers.clone(),
        provider_credentials,
        provider_bindings.clone(),
        registry.clone(),
    ));
    let organization_core = Arc::new(OrganizationCore::new(
        "test".to_string(),
        Arc::new(MemoryOrganizationRepo::new()),
        providers,
        provider_bindings,
        registry.clone(),
    ));
    let service = OrganizationManagement::new(provider_core.clone(), organization_core.clone());
    TestContext {
        service,
        core: organization_core,
        registry,
        provider_core,
        _temp_dir: temp_dir,
    }
}

async fn register_provider(ctx: &TestContext, name: &str) -> ProviderFixture {
    let registered = ctx
        .provider_core
        .register_provider(
            name.to_string(),
            "https://provider.example.com/bcs/webhook".to_string(),
            ProviderAuthMode::StaticBearer,
            "11111111".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider");
    ProviderFixture {
        provider_id: registered.provider.provider_id,
        admin_token: registered.provider_admin_token,
    }
}

async fn grant_manager(
    ctx: &TestContext,
    resource: &ProviderFixture,
    manager: &ProviderFixture,
) {
    ctx.provider_core
        .update_provider(
            &resource.provider_id,
            &resource.admin_token,
            "11111111",
            None,
            None,
            None,
            None,
            Some(ProviderOrganizationManagementConfig {
                authorized_manager_provider_ids: vec![manager.provider_id.clone()],
            }),
        )
        .await
        .expect("grant organization manager");
}

async fn register_bot(ctx: &TestContext, provider: &ProviderFixture, bot_uuid: &str) {
    ctx.provider_core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: format!("{bot_uuid} name"),
                summary: Some(format!("{bot_uuid} summary")),
                owners: vec!["11111111".to_string()],
                provider_bot_ref: format!("{bot_uuid}-ref"),
                domains: vec!["marketing".to_string()],
                skills: vec![Skill::new("planning")],
                scopes: vec!["campaign".to_string()],
                bot_uuid: Some(bot_uuid.to_string()),
            },
        )
        .await
        .expect("register provider bot");
}

async fn create_org(ctx: &TestContext, provider: &ProviderFixture) {
    ctx.service
        .create(CreateOrganizationCommand {
            auth: provider_auth(provider),
            organization_code: "promo-2026".to_string(),
            name: "Promo 2026".to_string(),
            description: Some("campaign org".to_string()),
        })
        .await
        .expect("create organization");
}

#[tokio::test]
async fn put_member_allows_own_and_granted_provider_bots_and_restores_disabled_member() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    create_org(&ctx, &provider_a).await;

    let own = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect("own bot allowed");
    assert_eq!(own.role.as_deref(), Some("planner"));

    let granted = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("traffic_analyst".to_string()),
        })
        .await
        .expect("granted provider bot allowed");
    assert_eq!(granted.bot_uuid, "bot-b");

    ctx.service
        .delete_member(provider_auth(&provider_a), "promo-2026", "bot-b")
        .await
        .expect("delete existing member");
    let disabled = ctx
        .service
        .get_member(provider_auth(&provider_a), "promo-2026", "bot-b")
        .await
        .expect("read disabled member")
        .expect("disabled member is retained");
    assert!(disabled.disabled);

    let restored = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("traffic_analyst".to_string()),
        })
        .await
        .expect("put restores disabled member");
    assert!(!restored.disabled);

    ctx.service
        .delete_member(provider_auth(&provider_a), "promo-2026", "missing-member")
        .await
        .expect("delete missing member is idempotent");
}

#[tokio::test]
async fn put_member_rejects_ungranted_provider_bot() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_c = register_provider(&ctx, "Provider C").await;
    register_bot(&ctx, &provider_c, "bot-c").await;
    create_org(&ctx, &provider_a).await;

    let err = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-c".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect_err("ungranted provider bot must be rejected");

    assert!(matches!(err, ServiceError::Forbidden(reason) if reason == "organization_manager_not_authorized"));
}

#[tokio::test]
async fn put_member_rejects_disabled_organization_before_bot_checks() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    create_org(&ctx, &provider_a).await;
    ctx.service
        .update(UpdateOrganizationCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            name: None,
            description: None,
            disabled: Some(true),
        })
        .await
        .expect("disable organization");

    let err = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "missing-bot".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect_err("disabled organization should be rejected first");

    assert!(matches!(err, ServiceError::Forbidden(reason) if reason == "organization_disabled"));
}

#[tokio::test]
async fn put_member_rejects_missing_and_deleted_bots() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    create_org(&ctx, &provider_a).await;

    let missing = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "missing-bot".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect_err("missing bot should be rejected");
    assert!(matches!(missing, ServiceError::BotNotFound(bot) if bot == "missing-bot"));

    register_bot(&ctx, &provider_a, "deleted-bot").await;
    assert!(ctx.registry.soft_delete("deleted-bot").await);
    let deleted = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "deleted-bot".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect_err("deleted bot should be rejected");
    assert!(matches!(deleted, ServiceError::BotNotFound(bot) if bot == "deleted-bot"));
}

#[tokio::test]
async fn organization_management_rejects_wrong_manager_and_bad_auth() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    create_org(&ctx, &provider_a).await;

    let wrong_manager = ctx
        .service
        .get(provider_auth(&provider_b), "promo-2026")
        .await
        .expect_err("wrong manager should be rejected");
    assert!(matches!(wrong_manager, ServiceError::Forbidden(reason) if reason == "organization_manager_required"));

    let bad_auth = ctx
        .service
        .list(bad_provider_auth(&provider_a), false)
        .await
        .expect_err("application must authenticate before core access");
    assert!(matches!(bad_auth, ServiceError::Unauthorized(_)));
}

#[tokio::test]
async fn validation_rejects_invalid_code_invalid_role_and_patch_without_fields() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    create_org(&ctx, &provider_a).await;

    let invalid_code = ctx
        .service
        .create(CreateOrganizationCommand {
            auth: provider_auth(&provider_a),
            organization_code: "invalid code".to_string(),
            name: "Invalid".to_string(),
            description: None,
        })
        .await
        .expect_err("invalid code should fail");
    assert!(matches!(invalid_code, ServiceError::InvalidOperation { message, .. } if message.contains("invalid organization_code")));

    let invalid_role = ctx
        .service
        .put_member(PutOrganizationMemberCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: Some("bad role!".to_string()),
        })
        .await
        .expect_err("invalid role should fail");
    assert!(matches!(invalid_role, ServiceError::InvalidOperation { message, .. } if message.contains("invalid role")));

    let patch_without_fields = ctx
        .service
        .update(UpdateOrganizationCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            name: None,
            description: None,
            disabled: None,
        })
        .await
        .expect_err("PATCH with no fields should fail");
    assert!(matches!(patch_without_fields, ServiceError::InvalidOperation { message, .. } if message.contains("no organization fields")));
}

#[tokio::test]
async fn candidate_bots_include_manager_and_granted_bots_without_leaking_ungranted_provider_ids() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    let provider_c = register_provider(&ctx, "Provider C").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    register_bot(&ctx, &provider_c, "bot-c").await;

    let candidates = ctx
        .service
        .candidate_bots(provider_auth(&provider_a), OrganizationCandidateQuery::default())
        .await
        .expect("candidate bots");
    let mut bot_ids = candidates
        .iter()
        .map(|candidate| candidate.bot_uuid.as_str())
        .collect::<Vec<_>>();
    bot_ids.sort();
    assert_eq!(bot_ids, vec!["bot-a", "bot-b"]);
    assert!(candidates.iter().all(|candidate| {
        candidate.provider_id == provider_a.provider_id || candidate.provider_id == provider_b.provider_id
    }));
    assert!(!candidates
        .iter()
        .any(|candidate| candidate.provider_id == provider_c.provider_id));

    let filtered = ctx
        .service
        .candidate_bots(
            provider_auth(&provider_a),
            OrganizationCandidateQuery {
                q: Some("bot-b name".to_string()),
                ..OrganizationCandidateQuery::default()
            },
        )
        .await
        .expect("filtered candidate bots");
    assert_eq!(filtered.len(), 1);
    assert_eq!(filtered[0].bot_uuid, "bot-b");

    // An explicit provider_id narrows to that one authorized resource provider.
    let narrowed = ctx
        .service
        .candidate_bots(
            provider_auth(&provider_a),
            OrganizationCandidateQuery {
                provider_id: Some(provider_b.provider_id.clone()),
                ..OrganizationCandidateQuery::default()
            },
        )
        .await
        .expect("narrowed candidate bots");
    assert_eq!(narrowed.len(), 1);
    assert_eq!(narrowed[0].bot_uuid, "bot-b");
    assert_eq!(narrowed[0].provider_id, provider_b.provider_id);

    // A provider_id outside the manager's authorized set is rejected (403),
    // not silently returned as an empty list.
    let unauthorized = ctx
        .service
        .candidate_bots(
            provider_auth(&provider_a),
            OrganizationCandidateQuery {
                provider_id: Some(provider_c.provider_id.clone()),
                ..OrganizationCandidateQuery::default()
            },
        )
        .await;
    match unauthorized {
        Err(ServiceError::Forbidden(reason)) => {
            assert_eq!(reason, "organization_manager_not_authorized");
        }
        other => panic!(
            "unauthorized provider_id should be rejected with 403, got {other:?}"
        ),
    }
}


#[tokio::test]
async fn effective_membership_authorizes_pair_and_fails_after_grant_revocation() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    create_org(&ctx, &provider_a).await;
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-a".to_string(),
            role: Some("planner".to_string()),
        })
        .await
        .expect("add sender");
    ctx.service
        .put_member(PutOrganizationMemberCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            bot_uuid: "bot-b".to_string(),
            role: Some("traffic_analyst".to_string()),
        })
        .await
        .expect("add target");

    let pair = ctx
        .core
        .authorize_pair("promo-2026", "bot-a", "bot-b")
        .await
        .expect("authorized pair");
    assert_eq!(pair.organization.code, "promo-2026");
    assert_eq!(pair.sender.bot_uuid, "bot-a");
    assert_eq!(pair.target.bot_uuid, "bot-b");

    ctx.provider_core
        .update_provider(
            &provider_b.provider_id,
            &provider_b.admin_token,
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
        .expect("revoke organization manager");

    assert!(matches!(
        ctx.core.authorize_pair("promo-2026", "bot-a", "bot-b").await,
        Err(ServiceError::Forbidden(message))
            if message == "organization_provider_grant_required"
    ));
}

#[tokio::test]
async fn effective_membership_rejects_disabled_org_disabled_member_and_nonmember_sender() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_a, "bot-b").await;
    create_org(&ctx, &provider_a).await;
    for bot_uuid in ["bot-a", "bot-b"] {
        ctx.service
            .put_member(PutOrganizationMemberCommand {
                auth: provider_auth(&provider_a),
                organization_code: "promo-2026".to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: None,
            })
            .await
            .expect("add member");
    }

    ctx.service
        .delete_member(provider_auth(&provider_a), "promo-2026", "bot-b")
        .await
        .expect("disable member");
    let disabled_member = ctx
        .core
        .authorize_pair("promo-2026", "bot-a", "bot-b")
        .await
        .expect_err("disabled target member must be rejected");
    assert!(matches!(disabled_member, ServiceError::Forbidden(reason) if reason == "organization_member_disabled"));

    let nonmember = ctx
        .core
        .authorize_pair("promo-2026", "missing-member", "bot-a")
        .await
        .expect_err("nonmember sender must be rejected");
    assert!(matches!(nonmember, ServiceError::Forbidden(reason) if reason == "organization_member_required"));

    ctx.service
        .update(UpdateOrganizationCommand {
            auth: provider_auth(&provider_a),
            organization_code: "promo-2026".to_string(),
            name: None,
            description: None,
            disabled: Some(true),
        })
        .await
        .expect("disable organization");
    let disabled_org = ctx
        .core
        .require_effective_member("promo-2026", "bot-a")
        .await
        .expect_err("disabled organization must be rejected");
    assert!(matches!(disabled_org, ServiceError::Forbidden(reason) if reason == "organization_disabled"));
}

#[tokio::test]
async fn effective_member_list_filters_by_role_and_omits_revoked_members() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    let provider_c = register_provider(&ctx, "Provider C").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    register_bot(&ctx, &provider_c, "bot-c").await;
    create_org(&ctx, &provider_a).await;
    for (bot_uuid, role) in [
        ("bot-a", "planner"),
        ("bot-b", "traffic_analyst"),
        ("bot-c", "traffic_analyst"),
    ] {
        if bot_uuid == "bot-c" {
            grant_manager(&ctx, &provider_c, &provider_a).await;
        }
        ctx.service
            .put_member(PutOrganizationMemberCommand {
                auth: provider_auth(&provider_a),
                organization_code: "promo-2026".to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: Some(role.to_string()),
            })
            .await
            .expect("add member");
    }
    ctx.provider_core
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
        .expect("revoke provider c");

    let members = ctx
        .core
        .list_effective_members("promo-2026", Some("traffic_analyst"))
        .await
        .expect("list effective members");
    let bot_ids = members
        .iter()
        .map(|member| member.bot_uuid.as_str())
        .collect::<Vec<_>>();
    assert_eq!(bot_ids, vec!["bot-b"]);
}

#[tokio::test]
async fn effective_membership_rejects_disabled_resource_provider_and_missing_org() {
    let ctx = test_context().await;
    let provider_a = register_provider(&ctx, "Provider A").await;
    let provider_b = register_provider(&ctx, "Provider B").await;
    grant_manager(&ctx, &provider_b, &provider_a).await;
    register_bot(&ctx, &provider_a, "bot-a").await;
    register_bot(&ctx, &provider_b, "bot-b").await;
    create_org(&ctx, &provider_a).await;
    for bot_uuid in ["bot-a", "bot-b"] {
        ctx.service
            .put_member(PutOrganizationMemberCommand {
                auth: provider_auth(&provider_a),
                organization_code: "promo-2026".to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: None,
            })
            .await
            .expect("add member");
    }

    let missing_org = ctx
        .core
        .require_effective_member("missing-org", "bot-a")
        .await
        .expect_err("missing org must be rejected");
    assert!(matches!(missing_org, ServiceError::InvalidOperation { message, .. } if message.contains("organization 'missing-org' not found")));

    ctx.provider_core
        .set_provider_disabled(&provider_b.provider_id, &provider_b.admin_token, "11111111", true)
        .await
        .expect("disable provider b");
    let disabled_provider = ctx
        .core
        .authorize_pair("promo-2026", "bot-a", "bot-b")
        .await
        .expect_err("disabled resource provider must be rejected");
    assert!(matches!(disabled_provider, ServiceError::Forbidden(reason) if reason == "organization_provider_grant_required"));
}
