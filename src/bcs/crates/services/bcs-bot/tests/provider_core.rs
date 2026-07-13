use std::sync::Arc;

use bcs_bot::core::{BotCore, ProviderCore};
use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_service_api::{
    ActorStatus, BotDeliveryTarget, BotRegistryCoreService, CoordinationMode,
    ProviderAuthMode, ProviderBotBindingRepoPort, ProviderBotCoreService,
    ProviderCoordinationConfig, ProviderCoreService, ProviderCredentialRepoPort,
    ProviderOrganizationManagementConfig, ProviderRepoPort, RegisterProviderBotParams,
    ServiceError,
};

struct TestContext {
    core: ProviderCore,
    registry: Arc<BotCore>,
    _temp_dir: tempfile::TempDir,
}

fn test_context() -> TestContext {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let core = ProviderCore::new(
        provider_repo,
        provider_credentials,
        provider_bindings,
        registry.clone(),
    );
    TestContext {
        core,
        registry,
        _temp_dir: temp_dir,
    }
}

struct RegisteredProviderFixture {
    provider_id: String,
    admin_token: String,
}

async fn register_provider(
    ctx: &TestContext,
    auth_mode: ProviderAuthMode,
) -> RegisteredProviderFixture {
    let registered = ctx
        .core
        .register_provider(
            "Provider".to_string(),
            "https://provider.example.com/bcs/webhook".to_string(),
            auth_mode,
            "11111111".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider");
    RegisteredProviderFixture {
        provider_id: registered.provider.provider_id,
        admin_token: registered.provider_admin_token,
    }
}

async fn register_provider_with_coordination(
    ctx: &TestContext,
    auth_mode: ProviderAuthMode,
    coordination: ProviderCoordinationConfig,
) -> RegisteredProviderFixture {
    let registered = ctx
        .core
        .register_provider(
            "Provider".to_string(),
            "https://provider.example.com/bcs/webhook".to_string(),
            auth_mode,
            "197262".to_string(),
            None,
            Some(coordination),
        )
        .await
        .expect("register provider");
    RegisteredProviderFixture {
        provider_id: registered.provider.provider_id,
        admin_token: registered.provider_admin_token,
    }
}

#[tokio::test]
async fn register_provider_persists_mcporter_coordination_config() {
    let ctx = test_context();
    let registered = ctx
        .core
        .register_provider(
            "Provider".to_string(),
            "https://provider.example.com/bcs/webhook".to_string(),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            Some(ProviderCoordinationConfig {
                mode: CoordinationMode::McporterMcp,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: Some("mcporter".to_string()),
            }),
        )
        .await
        .expect("register provider");

    let config: serde_json::Value =
        serde_json::from_str(&registered.provider.config).expect("provider config json");
    assert_eq!(config["coordination"]["mode"], "mcporter_mcp");
    assert_eq!(config["coordination"]["mcp_server"], "bcs");
    assert_eq!(config["coordination"]["mcporter_command"], "mcporter");
}

#[tokio::test]
async fn register_provider_rejects_native_tool_with_mcp_fields() {
    let ctx = test_context();
    let err = ctx
        .core
        .register_provider(
            "Provider".to_string(),
            "https://provider.example.com/bcs/webhook".to_string(),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            Some(ProviderCoordinationConfig {
                mode: CoordinationMode::NativeTool,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: None,
            }),
        )
        .await
        .expect_err("native_tool with mcp_server should fail");

    assert!(matches!(err, ServiceError::InvalidOperation { message, .. } if message.contains("native_tool")));
}

#[tokio::test]
async fn provider_bot_resolves_coordination_surface_from_provider_config() {
    let ctx = test_context();
    let provider = register_provider_with_coordination(
        &ctx,
        ProviderAuthMode::StaticBearer,
        ProviderCoordinationConfig {
            mode: CoordinationMode::NativeMcp,
            mcp_server: Some("bcs".to_string()),
            mcporter_command: None,
        },
    )
    .await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["197262".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    let surface = ctx
        .registry
        .resolve_coordination_surface(&binding.bot_uuid)
        .await
        .expect("resolve coordination surface");

    assert_eq!(surface.mode, CoordinationMode::NativeMcp);
    assert_eq!(surface.mcp_server.as_deref(), Some("bcs"));
    assert_eq!(surface.mcporter_command, None);
}

#[tokio::test]
async fn websocket_plugin_bot_resolves_native_tool_surface() {
    let ctx = test_context();
    ctx.registry
        .register(
            "bot-plugin".to_string(),
            bcs_service_api::BotCapabilities {
                name: Some("Plugin Bot".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("register bot");
    ctx.registry
        .add_bot_info("bot-plugin", "client_kind", "plugin".to_string())
        .await;

    let surface = ctx
        .registry
        .resolve_coordination_surface("bot-plugin")
        .await
        .expect("resolve coordination surface");

    assert_eq!(surface.mode, CoordinationMode::NativeTool);
    assert_eq!(surface.mcp_server, None);
    assert_eq!(surface.mcporter_command, None);
}

#[tokio::test]
async fn register_provider_rejects_private_webhook_urls() {
    let ctx = test_context();

    for webhook_url in [
        "http://127.0.0.1:8080/hook",
        "http://10.0.0.1/hook",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/hook",
    ] {
        let err = ctx
            .core
            .register_provider(
                "Provider".to_string(),
                webhook_url.to_string(),
                ProviderAuthMode::StaticBearer,
                "11111111".to_string(),
                None,
                None,
            )
            .await
            .expect_err("private webhook URL should be rejected");

        assert!(matches!(err, ServiceError::InvalidOperation { .. }), "{webhook_url}");
    }
}

#[tokio::test]
async fn update_provider_rejects_private_webhook_url() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;

    let err = ctx
        .core
        .update_provider(
            &provider.provider_id,
            &provider.admin_token,
            "11111111",
            None,
            Some("http://127.0.0.1:8080/hook".to_string()),
            None,
            None,
            None,
        )
        .await
        .expect_err("private webhook URL update should be rejected");

    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
}

#[test]
fn organization_management_config_rejects_malformed_stored_subtree() {
    let error = ProviderOrganizationManagementConfig::from_provider_config(
        r#"{"organization_management":{"authorized_manager_provider_ids":"provider-a"}}"#,
    )
    .expect_err("malformed organization management config must fail closed");

    assert!(error.is_data());
}

#[tokio::test]
async fn update_provider_normalizes_organization_management_and_preserves_other_config() {
    let ctx = test_context();
    let provider_a = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let provider_c = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let provider_b = register_provider_with_coordination(
        &ctx,
        ProviderAuthMode::StaticBearer,
        ProviderCoordinationConfig {
            mode: CoordinationMode::NativeMcp,
            mcp_server: Some("bcs".to_string()),
            mcporter_command: None,
        },
    )
    .await;

    let updated = ctx
        .core
        .update_provider(
            &provider_b.provider_id,
            &provider_b.admin_token,
            "197262",
            None,
            None,
            None,
            None,
            Some(ProviderOrganizationManagementConfig {
                authorized_manager_provider_ids: vec![
                    provider_c.provider_id.clone(),
                    provider_b.provider_id.clone(),
                    provider_a.provider_id.clone(),
                    provider_a.provider_id.clone(),
                ],
            }),
        )
        .await
        .expect("update organization management config");

    let organization_management =
        ProviderOrganizationManagementConfig::from_provider_config(&updated.config)
            .expect("parse config");
    let mut expected_manager_provider_ids = vec![provider_a.provider_id, provider_c.provider_id];
    expected_manager_provider_ids.sort();
    assert_eq!(
        organization_management.authorized_manager_provider_ids,
        expected_manager_provider_ids,
    );
    let config: serde_json::Value = serde_json::from_str(&updated.config).expect("provider config");
    assert_eq!(
        config["downlink"]["webhook_url"],
        "https://provider.example.com/bcs/webhook"
    );
    assert_eq!(config["coordination"]["mode"], "native_mcp");
}

#[tokio::test]
async fn update_provider_rejects_invalid_organization_management_provider_id() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;

    let error = ctx
        .core
        .update_provider(
            &provider.provider_id,
            &provider.admin_token,
            "11111111",
            None,
            None,
            None,
            None,
            Some(ProviderOrganizationManagementConfig {
                authorized_manager_provider_ids: vec!["invalid provider id".to_string()],
            }),
        )
        .await
        .expect_err("invalid manager provider ID must fail");

    assert!(matches!(error, ServiceError::InvalidOperation { message, .. } if message.contains("invalid authorized_manager_provider_id")));
}

#[tokio::test]
async fn update_provider_rejects_unknown_organization_management_provider() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;

    let error = ctx
        .core
        .update_provider(
            &provider.provider_id,
            &provider.admin_token,
            "11111111",
            None,
            None,
            None,
            None,
            Some(ProviderOrganizationManagementConfig {
                authorized_manager_provider_ids: vec!["provider-missing".to_string()],
            }),
        )
        .await
        .expect_err("unknown manager provider must fail");

    assert!(matches!(error, ServiceError::InvalidOperation { message, .. } if message.contains("provider-missing") && message.contains("not found")));
}

#[tokio::test]
async fn static_bearer_provider_bot_registration_returns_runtime_token() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;

    let (binding, bot_runtime_token) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: Some("Reviews code".to_string()),
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    assert!(bot_runtime_token
        .as_deref()
        .is_some_and(|token| uuid::Uuid::parse_str(token).is_ok()));
    assert_eq!(binding.provider_bot_ref, "reviewer-v2");
    assert_eq!(
        ctx.registry
            .get(&binding.bot_uuid)
            .await
            .expect("registered bot")
            .created_by
            .as_deref(),
        Some("11111111")
    );
}

#[tokio::test]
async fn agentpass_provider_bot_registration_omits_runtime_token() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::AgentPass).await;

    let (binding, bot_runtime_token) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    assert!(bot_runtime_token.is_none());
    assert_eq!(binding.provider_bot_ref, "reviewer-v2");
    let session_token = ctx
        .registry
        .load_token(&binding.bot_uuid)
        .await
        .expect("agentpass provider bot should still have a BCS session token");
    assert!(uuid::Uuid::parse_str(&session_token).is_ok());
}

#[tokio::test]
async fn provider_bot_registration_requires_exactly_one_owner() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;

    for owners in [vec![], vec!["11111111".to_string(), "12345678".to_string()]] {
        let err = ctx
            .core
            .register_provider_bot_with_bot_uuid(
                &provider.provider_id,
                &provider.admin_token,
                RegisterProviderBotParams {
                    bot_name: "Code Reviewer".to_string(),
                    owners,
                    provider_bot_ref: "reviewer-v2".to_string(),
                    ..Default::default()
                },
            )
            .await
            .expect_err("owner validation should fail");
        assert!(matches!(err, ServiceError::InvalidOperation { .. }));
    }
}

#[tokio::test]
async fn provider_bot_registration_trims_owner_before_saving_created_by() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;

    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec![" 11111111 ".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    assert_eq!(
        ctx.registry
            .get(&binding.bot_uuid)
            .await
            .expect("registered bot")
            .created_by
            .as_deref(),
        Some("11111111")
    );
}

#[tokio::test]
async fn provider_bot_registration_is_idempotent_for_duplicate_provider_ref_without_orphan_bot() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;

    let (first_binding, first_runtime_token) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");
    let bot_count = ctx.registry.list_all_bots().await.len();

    let (second_binding, second_runtime_token) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Second Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("duplicate provider bot ref should return existing binding");

    assert_eq!(second_binding.bot_uuid, first_binding.bot_uuid);
    assert_eq!(second_binding.provider_id, first_binding.provider_id);
    assert_eq!(second_binding.provider_bot_ref, first_binding.provider_bot_ref);
    assert!(first_runtime_token.is_some());
    assert!(second_runtime_token.is_none());
    assert_eq!(ctx.registry.list_all_bots().await.len(), bot_count);
}

#[tokio::test]
async fn resolve_delivery_target_returns_http_provider_for_enabled_binding() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    let target = ctx
        .registry
        .resolve_delivery_target(&binding.bot_uuid)
        .await
        .expect("resolve delivery target");

    match target {
        BotDeliveryTarget::HttpProvider {
            provider_id,
            provider_bot_ref,
            webhook_url,
            ..
        } => {
            assert_eq!(provider_id, provider.provider_id);
            assert_eq!(provider_bot_ref, "reviewer-v2");
            assert_eq!(webhook_url, "https://provider.example.com/bcs/webhook");
        }
        BotDeliveryTarget::WebSocket { .. } => panic!("expected http provider target"),
    }
}

#[tokio::test]
async fn provider_http_bot_is_effectively_online_without_ws_connection() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    assert!(!ctx.registry.is_connected(&binding.bot_uuid).await);
    assert!(ctx.registry.is_effectively_online(&binding.bot_uuid).await);

    ctx.registry
        .update_actor_status(&binding.bot_uuid, ActorStatus::Hidden)
        .await
        .expect("hide provider bot");
    assert!(!ctx.registry.is_effectively_online(&binding.bot_uuid).await);
}

#[tokio::test]
async fn disabled_provider_is_not_routable() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");
    ctx.core
        .set_provider_disabled(&provider.provider_id, &provider.admin_token, "11111111", true)
        .await
        .expect("disable provider");

    let err = ctx
        .registry
        .resolve_delivery_target(&binding.bot_uuid)
        .await
        .expect_err("disabled provider should not be routable");
    assert!(matches!(err, ServiceError::InvalidOperation { .. }));
}

#[tokio::test]
async fn set_provider_disabled_requires_matching_admin_token() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    let other_registered = ctx
        .core
        .register_provider(
            "Provider 2".to_string(),
            "https://provider2.example.com/bcs/webhook".to_string(),
            ProviderAuthMode::StaticBearer,
            "11111111".to_string(),
            None,
            None,
        )
        .await
        .expect("register provider 2");

    let err = ctx
        .core
        .set_provider_disabled(
            &provider.provider_id,
            &other_registered.provider_admin_token,
            "11111111",
            true,
        )
        .await
        .expect_err("admin token for another provider must fail");

    assert!(matches!(err, ServiceError::Forbidden(message) if message == "provider_id_mismatch"));
}

#[tokio::test]
async fn provider_admin_provider_bot_registration_returns_runtime_token() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;

    let (binding, bot_runtime_token) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    assert!(
        bot_runtime_token.is_some(),
        "provider_admin mode still issues a bot_runtime_token"
    );
    assert_eq!(binding.provider_bot_ref, "reviewer-v2");
}

#[tokio::test]
async fn authenticate_provider_admin_event_returns_identity() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    let identity = ctx
        .core
        .authenticate_provider_admin_event(
            &provider.provider_id,
            &provider.admin_token,
            "reviewer-v2",
        )
        .await
        .expect("authenticate provider_admin event");

    assert_eq!(identity.bot_uuid, binding.bot_uuid);
    assert_eq!(identity.provider_id, provider.provider_id);
}

#[tokio::test]
async fn authenticate_provider_admin_event_rejects_static_bearer_provider() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::StaticBearer).await;
    ctx.core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    let err = ctx
        .core
        .authenticate_provider_admin_event(
            &provider.provider_id,
            &provider.admin_token,
            "reviewer-v2",
        )
        .await
        .expect_err("auth_mode_mismatch should fail");
    assert!(matches!(err, ServiceError::Unauthorized(msg) if msg == "auth_mode_mismatch"));
}

#[tokio::test]
async fn authenticate_provider_admin_event_rejects_other_provider_id() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;
    let other = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;
    ctx.core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");

    let err = ctx
        .core
        .authenticate_provider_admin_event(
            &other.provider_id,
            &provider.admin_token,
            "reviewer-v2",
        )
        .await
        .expect_err("admin token from another provider must fail");
    assert!(matches!(err, ServiceError::Forbidden(msg) if msg == "provider_id_mismatch"));
}

#[tokio::test]
async fn authenticate_provider_admin_event_rejects_unknown_bot_ref() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;

    let err = ctx
        .core
        .authenticate_provider_admin_event(
            &provider.provider_id,
            &provider.admin_token,
            "no-such-ref",
        )
        .await
        .expect_err("unknown bot ref should fail");
    assert!(matches!(err, ServiceError::BotNotFound(_)));
}

#[tokio::test]
async fn authenticate_provider_admin_event_rejects_disabled_provider() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;
    ctx.core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");
    ctx.core
        .set_provider_disabled(&provider.provider_id, &provider.admin_token, "11111111", true)
        .await
        .expect("disable provider");

    let err = ctx
        .core
        .authenticate_provider_admin_event(
            &provider.provider_id,
            &provider.admin_token,
            "reviewer-v2",
        )
        .await
        .expect_err("disabled provider should fail");
    assert!(matches!(err, ServiceError::InvalidOperation { message, .. } if message.contains("is disabled")));
}

#[tokio::test]
async fn authenticate_provider_admin_event_rejects_disabled_binding() {
    let ctx = test_context();
    let provider = register_provider(&ctx, ProviderAuthMode::ProviderAdmin).await;
    let (binding, _) = ctx
        .core
        .register_provider_bot_with_bot_uuid(
            &provider.provider_id,
            &provider.admin_token,
            RegisterProviderBotParams {
                bot_name: "Code Reviewer".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "reviewer-v2".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register provider bot");
    ctx.core
        .set_provider_bot_disabled(
            &provider.provider_id,
            &binding.bot_uuid,
            &provider.admin_token,
            true,
        )
        .await
        .expect("disable provider bot");

    let err = ctx
        .core
        .authenticate_provider_admin_event(
            &provider.provider_id,
            &provider.admin_token,
            "reviewer-v2",
        )
        .await
        .expect_err("disabled binding should fail");
    assert!(matches!(err, ServiceError::InvalidOperation { message, .. } if message.contains("is disabled")));
}
