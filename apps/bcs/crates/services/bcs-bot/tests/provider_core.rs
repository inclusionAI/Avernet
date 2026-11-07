use std::sync::Arc;

use bcs_bot::core::{BotCore, ProviderCore};
use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_service_api::{
    ActorStatus, BotDeliveryTarget, BotRegistryCoreService, CoordinationMode,
    ProviderAuthMode, ProviderBotBindingRepoPort, ProviderBotConnectionMode,
    ProviderBotCoreService, ProviderCoordinationConfig, ProviderCoreService,
    ProviderCredentialRepoPort, ProviderOrganizationManagementConfig, ProviderRepoPort,
    RegisterProviderBotParams, ServiceError, is_mock_token,
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
            Some("https://provider.example.com/bcs/webhook".to_string()),
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
            Some("https://provider.example.com/bcs/webhook".to_string()),
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
            Some("https://provider.example.com/bcs/webhook".to_string()),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            Some(ProviderCoordinationConfig {
                mode: CoordinationMode::McporterMcp,
                worker_send_task_message_enabled: true,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: Some("mcporter".to_string()),
                tool_name_mapping: Default::default(),
            }),
        )
        .await
        .expect("register provider");

    let config: serde_json::Value =
        serde_json::from_str(&registered.provider.config).expect("provider config json");
    assert_eq!(config["coordination"]["mode"], "mcporter_mcp");
    assert_eq!(config["coordination"]["mcp_server"], "bcs");
    assert_eq!(config["coordination"]["mcporter_command"], "mcporter");
    assert!(config["coordination"]
        .get("worker_send_task_message_enabled")
        .is_none());
}

#[test]
fn provider_coordination_defaults_worker_send_task_message_to_enabled() {
    let config: ProviderCoordinationConfig = serde_json::from_value(serde_json::json!({
        "mode": "native_tool"
    }))
    .expect("coordination config should deserialize without the optional field");
    assert!(config.worker_send_task_message_enabled);

    let mut disabled = config;
    disabled.worker_send_task_message_enabled = false;
    let serialized = serde_json::to_value(disabled).expect("coordination config should serialize");
    assert_eq!(serialized["worker_send_task_message_enabled"], false);
}

#[tokio::test]
async fn register_provider_persists_native_mcp_tool_name_mapping() {
    let ctx = test_context();
    let assign_tool = "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_assign_task";
    let send_message_tool = "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_send_task_message";
    let complete_tool = "mcp_mcp.ant.agentclawscs.bcs_mcp_bcs_task_complete";
    let registered = ctx
        .core
        .register_provider(
            "Provider".to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            Some(ProviderCoordinationConfig {
                mode: CoordinationMode::NativeMcp,
                worker_send_task_message_enabled: true,
                mcp_server: Some("mcp.ant.agentclawscs.bcs".to_string()),
                mcporter_command: None,
                tool_name_mapping: [
                    (assign_tool.to_string(), "bcs_assign_task".to_string()),
                    (
                        send_message_tool.to_string(),
                        "bcs_send_task_message".to_string(),
                    ),
                    (
                        complete_tool.to_string(),
                        "bcs_task_complete".to_string(),
                    ),
                ]
                .into_iter()
                .collect(),
            }),
        )
        .await
        .expect("register provider");

    let config: serde_json::Value =
        serde_json::from_str(&registered.provider.config).expect("provider config json");
    assert_eq!(config["coordination"]["mode"], "native_mcp");
    assert_eq!(
        config["coordination"]["tool_name_mapping"][assign_tool],
        "bcs_assign_task"
    );
    assert_eq!(
        config["coordination"]["tool_name_mapping"][send_message_tool],
        "bcs_send_task_message"
    );
    assert_eq!(
        config["coordination"]["tool_name_mapping"][complete_tool],
        "bcs_task_complete"
    );
}

#[tokio::test]
async fn register_provider_rejects_non_native_mcp_tool_name_mapping() {
    let ctx = test_context();
    let err = ctx
        .core
        .register_provider(
            "Provider".to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            Some(ProviderCoordinationConfig {
                mode: CoordinationMode::McporterMcp,
                worker_send_task_message_enabled: true,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: Some("mcporter".to_string()),
                tool_name_mapping: [(
                    "provider-specific-tool".to_string(),
                    "bcs_assign_task".to_string(),
                )]
                .into_iter()
                .collect(),
            }),
        )
        .await
        .expect_err("non-native MCP mapping should fail");

    assert!(matches!(err, ServiceError::InvalidOperation { message, .. } if message.contains("mcporter_mcp") && message.contains("tool_name_mapping")));
}

#[tokio::test]
async fn register_provider_rejects_too_many_tool_name_mappings() {
    let ctx = test_context();
    let tool_name_mapping = (0..33)
        .map(|index| (format!("provider-tool-{index}"), "bcs_assign_task".to_string()))
        .collect();
    let err = ctx
        .core
        .register_provider(
            "Provider".to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            Some(ProviderCoordinationConfig {
                mode: CoordinationMode::NativeMcp,
                worker_send_task_message_enabled: true,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: None,
                tool_name_mapping,
            }),
        )
        .await
        .expect_err("more than 32 mappings should fail");

    assert!(matches!(err, ServiceError::InvalidOperation { message, .. } if message.contains("must not exceed 32")));
}

#[tokio::test]
async fn register_provider_rejects_invalid_provider_tool_name() {
    let ctx = test_context();
    let err = ctx
        .core
        .register_provider(
            "Provider".to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            Some(ProviderCoordinationConfig {
                mode: CoordinationMode::NativeMcp,
                worker_send_task_message_enabled: true,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: None,
                tool_name_mapping: [(" provider-tool".to_string(), "bcs_assign_task".to_string())]
                    .into_iter()
                    .collect(),
            }),
        )
        .await
        .expect_err("tool names with surrounding whitespace should fail");

    assert!(matches!(err, ServiceError::InvalidOperation { message, .. } if message.contains("invalid provider tool name")));
}

#[tokio::test]
async fn register_provider_rejects_unsupported_canonical_tool_name() {
    let ctx = test_context();
    let err = ctx
        .core
        .register_provider(
            "Provider".to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            Some(ProviderCoordinationConfig {
                mode: CoordinationMode::NativeMcp,
                worker_send_task_message_enabled: true,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: None,
                tool_name_mapping: [("provider-tool".to_string(), "unknown-tool".to_string())]
                    .into_iter()
                    .collect(),
            }),
        )
        .await
        .expect_err("unknown canonical tool names should fail");

    assert!(matches!(err, ServiceError::InvalidOperation { message, .. } if message.contains("unsupported canonical tool 'unknown-tool'")));
}

#[tokio::test]
async fn register_provider_rejects_native_tool_with_mcp_fields() {
    let ctx = test_context();
    let err = ctx
        .core
        .register_provider(
            "Provider".to_string(),
            Some("https://provider.example.com/bcs/webhook".to_string()),
            ProviderAuthMode::StaticBearer,
            "197262".to_string(),
            None,
            Some(ProviderCoordinationConfig {
                mode: CoordinationMode::NativeTool,
                worker_send_task_message_enabled: true,
                mcp_server: Some("bcs".to_string()),
                mcporter_command: None,
                tool_name_mapping: Default::default(),
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
            worker_send_task_message_enabled: false,
            mcp_server: Some("bcs".to_string()),
            mcporter_command: None,
            tool_name_mapping: [(
                "provider_assign_task".to_string(),
                "bcs_assign_task".to_string(),
            )]
            .into_iter()
            .collect(),
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
    assert!(!surface.worker_send_task_message_enabled);
    assert_eq!(surface.mcp_server.as_deref(), Some("bcs"));
    assert_eq!(surface.mcporter_command, None);
    assert_eq!(
        surface.tool_name_mapping.get("provider_assign_task").map(String::as_str),
        Some("bcs_assign_task")
    );
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
    assert!(surface.worker_send_task_message_enabled);
    assert_eq!(surface.mcp_server, None);
    assert_eq!(surface.mcporter_command, None);
    assert!(surface.tool_name_mapping.is_empty());
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
                Some(webhook_url.to_string()),
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
            worker_send_task_message_enabled: true,
            mcp_server: Some("bcs".to_string()),
            mcporter_command: None,
            tool_name_mapping: Default::default(),
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

#[path = "provider_core_parts/delivery_and_auth.rs"]
mod delivery_and_auth;
