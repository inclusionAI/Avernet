use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot::application::ProviderManagement;
use bcs_bot::core::{BotCore, ProviderCore};
use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_service_api::{
    BotCapabilities, BotCatalogCleanupPort, BotRegistryCoreService, ChannelBindingCleanupPort,
    DeleteProviderBotCommand,
    ProviderAuthMode, ProviderBotBindingRepoPort, ProviderBotCoreService,
    ProviderCredentialRepoPort, ProviderManagementService, ProviderRepoPort,
    RegisterProviderBotCommand, RegisterProviderCommand, ServiceError, ServiceResult,
};
use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotControlPlaneRepoPort};
use bcs_service_api::types::{
    AuditActor, CreateOwnershipTransfer, OwnershipInitialization, TerminalReason, TransferStatus,
};
use bcs_test_support::NoopRelationCoreService;
use tokio::sync::Mutex;

struct TestContext {
    management: ProviderManagement,
    registry: Arc<dyn BotRegistryCoreService>,
    repo: Arc<MemoryBotRepo>,
    cleanup: Arc<RecordingCleanup>,
    catalog_cleanup: Arc<RecordingCatalogCleanup>,
    _temp_dir: tempfile::TempDir,
}

#[derive(Default)]
struct RecordingCatalogCleanup {
    deleted_bot_ids: Mutex<Vec<String>>,
    fail: Mutex<bool>,
}

#[async_trait]
impl BotCatalogCleanupPort for RecordingCatalogCleanup {
    async fn delete_bot(&self, bot_id: &str) -> ServiceResult<()> {
        if *self.fail.lock().await {
            return Err(ServiceError::InternalError(
                "catalog cleanup failure injected by test".to_string(),
            ));
        }
        self.deleted_bot_ids.lock().await.push(bot_id.to_string());
        Ok(())
    }
}

#[derive(Default)]
struct RecordingCleanup {
    deleted_bot_ids: Mutex<Vec<String>>,
    fail: Mutex<bool>,
}

#[async_trait]
impl ChannelBindingCleanupPort for RecordingCleanup {
    async fn delete_bindings_for_group(&self, _group_id: &str) -> ServiceResult<u64> {
        Ok(0)
    }

    async fn delete_bindings_for_bot(&self, bot_id: &str) -> ServiceResult<u64> {
        if *self.fail.lock().await {
            return Err(ServiceError::InternalError(
                "cleanup failure injected by test".to_string(),
            ));
        }
        self.deleted_bot_ids.lock().await.push(bot_id.to_string());
        Ok(1)
    }
}

fn test_context() -> TestContext {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_provider_repos(
        bot_repo.clone(),
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let provider_core = Arc::new(ProviderCore::new(
        provider_repo,
        provider_credentials,
        provider_bindings,
        registry.clone(),
    ));
    let provider_bot_core: Arc<dyn ProviderBotCoreService> = provider_core.clone();
    let cleanup = Arc::new(RecordingCleanup::default());
    let catalog_cleanup = Arc::new(RecordingCatalogCleanup::default());
    let management = ProviderManagement::new(
        provider_core.clone(),
        provider_bot_core,
        registry.clone(),
        Arc::new(NoopRelationCoreService),
    )
    .with_channel_binding_cleanup(cleanup.clone())
    .with_bot_catalog_cleanup(catalog_cleanup.clone());
    TestContext {
        management,
        registry,
        repo: bot_repo.clone(),
        cleanup,
        catalog_cleanup,
        _temp_dir: temp_dir,
    }
}

#[tokio::test]
async fn unbound_legacy_delete_retries_catalog_cleanup_after_transient_failure() {
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    let bot_uuid = "legacy:11111111";
    ctx.registry
        .register_with_owner_and_token(
            bot_uuid.to_string(),
            BotCapabilities::default(),
            "11111111",
            "test-runtime-token",
        )
        .await
        .expect("register legacy bot without provider binding");
    *ctx.catalog_cleanup.fail.lock().await = true;
    let command = DeleteProviderBotCommand {
        allow_unbound_owner_suffixed_bot: true,
        ..delete_command(&provider_id, &admin_token, bot_uuid)
    };

    let first = ctx.management.delete_provider_bot(command.clone()).await;
    assert!(first.is_err());
    assert!(ctx.registry.get(bot_uuid).await.is_some());

    *ctx.catalog_cleanup.fail.lock().await = false;
    let retried = ctx
        .management
        .delete_provider_bot(command)
        .await
        .expect("retry must reach catalog cleanup for the tombstoned legacy bot");

    assert_eq!(retried.bot_uuid, bot_uuid);
    assert_eq!(
        ctx.catalog_cleanup.deleted_bot_ids.lock().await.as_slice(),
        &[bot_uuid.to_string()]
    );
}

fn named_caps(name: &str) -> BotCapabilities {
    BotCapabilities {
        name: Some(name.to_string()),
        ..BotCapabilities::default()
    }
}

#[tokio::test]
async fn unbound_legacy_delete_retires_role_edges_and_pending_transfers() {
    // Reviewer scenario: an INITIALIZED legacy (owner-suffixed, unbound)
    // Bot carries real approved role edges and a PENDING transfer; the
    // legacy Provider delete lane must retire through the governed
    // single-transaction deletion boundary — a plain soft delete would
    // tombstone the Bot but leave its approved edges behind, and the
    // strict controllable union then fails the WHOLE mine read
    // (CorruptAuthority dangling edge) while the pending slot survives.
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    let legacy_id = "legacy:11111111";
    ctx.registry
        .register_with_owner_and_token(
            legacy_id.to_string(),
            named_caps("legacy"),
            "11111111",
            "token-legacy",
        )
        .await
        .expect("register legacy bot without provider binding");
    let init = || OwnershipInitialization {
        owner_user_id: "11111111".to_string(),
        actor: AuditActor::Human {
            user_id: "11111111".to_string(),
        },
        operation_id: uuid::Uuid::new_v4().to_string(),
    };
    ctx.registry
        .initialize_existing_ownership(legacy_id, init())
        .await
        .expect("initialize the legacy bot's ownership");
    // A second live Bot of the same Human proves the WHOLE mine union
    // stays readable after the legacy tombstone.
    ctx.registry
        .register_with_owner_and_token(
            "bot-live".to_string(),
            named_caps("live"),
            "11111111",
            "token-live",
        )
        .await
        .expect("register a second live bot");
    ctx.registry
        .initialize_existing_ownership("bot-live", init())
        .await
        .expect("initialize the live bot's ownership");
    ctx.registry
        .ensure_human_actor("22222222", "user-two")
        .await
        .expect("materialize the transfer recipient");
    let transfer = ctx
        .repo
        .create_transfer(CreateOwnershipTransfer {
            actor_user_id: "11111111".to_string(),
            bot_id: legacy_id.to_string(),
            to_user_id: "22222222".to_string(),
            expected_owner_version: 1,
            client_request_id: uuid::Uuid::new_v4().to_string(),
        })
        .await
        .expect("seed a pending ownership transfer")
        .receipt;

    let outcome = ctx
        .management
        .delete_provider_bot(DeleteProviderBotCommand {
            allow_unbound_owner_suffixed_bot: true,
            ..delete_command(&provider_id, &admin_token, legacy_id)
        })
        .await
        .expect("legacy provider delete");

    assert!(outcome.deleted, "the legacy bot must be deleted");
    // No dangling edges: the strict mine read still succeeds and shows
    // the other owned Bot — the whole union is not poisoned.
    let mine = ctx
        .repo
        .list_controllable(bcs_service_api::types::BotControllableQuery {
            user_id: "11111111".to_string(),
            env: bcs_config::resolve_env_str(),
            kind: None,
            name: None,
            status: None,
        })
        .await
        .expect("the owner's mine must stay readable after the delete");
    let ids: Vec<&str> = mine.iter().map(|row| row.record.bot_id.as_str()).collect();
    assert!(ids.contains(&"bot-live"), "the live Bot must stay listed: {ids:?}");
    assert!(
        !ids.contains(&legacy_id),
        "the retired Bot must not appear anymore"
    );
    // The pending transfer terminated under the fixed system marker,
    // never under the retiring identity — the same shape the governed
    // retirement boundary commits.
    let receipt = ctx
        .repo
        .get_transfer("11111111", &transfer.transfer_id)
        .await
        .expect("terminal receipt re-read");
    assert_eq!(receipt.status, TransferStatus::Invalidated);
    assert_eq!(receipt.terminal_reason, Some(TerminalReason::BotDeleted));
    assert_eq!(
        receipt.decision_actor,
        Some(AuditActor::System {
            name: "ownership-deletion".to_string(),
        })
    );
}

async fn register_provider(ctx: &TestContext) -> (String, String) {
    let registered = ctx
        .management
        .register_provider(RegisterProviderCommand {
            slug: None,
            name: "Provider".to_string(),
            webhook_url: Some("https://provider.example.com/bcs/webhook".to_string()),
            admin_callback_url: None,
            auth_mode: ProviderAuthMode::StaticBearer,
            created_by: "11111111".to_string(),
            protocol_version: None,
            coordination: None,
        })
        .await
        .expect("register provider");
    (registered.provider_id, registered.provider_admin_token)
}

async fn register_provider_bot(
    ctx: &TestContext,
    provider_id: &str,
    admin_token: &str,
    provider_bot_ref: &str,
) -> String {
    let outcome = ctx
        .management
        .register_provider_bot(RegisterProviderBotCommand {
            webhook_url: None,
            provider_id: provider_id.to_string(),
            provider_admin_token: admin_token.to_string(),
            name: "Bot".to_string(),
            summary: None,
            owners: vec!["11111111".to_string()],
            provider_bot_ref: provider_bot_ref.to_string(),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            bot_uuid: None,
            reject_existing_bot_uuid: false,
            connection_mode: bcs_service_api::ProviderBotConnectionMode::Gateway,
        })
        .await
        .expect("register provider bot");
    outcome.bot_uuid
}

fn delete_command(provider_id: &str, admin_token: &str, provider_bot_ref: &str) -> DeleteProviderBotCommand {
    DeleteProviderBotCommand {
        provider_id: provider_id.to_string(),
        provider_admin_token: admin_token.to_string(),
        provider_bot_ref: provider_bot_ref.to_string(),
        allow_unbound_owner_suffixed_bot: false,
    }
}

#[tokio::test]
async fn delete_provider_bot_cleans_channel_bindings_for_deleted_bot() {
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    let bot_uuid = register_provider_bot(&ctx, &provider_id, &admin_token, "bot-ref-1").await;

    let outcome = ctx
        .management
        .delete_provider_bot(delete_command(&provider_id, &admin_token, "bot-ref-1"))
        .await
        .expect("delete provider bot");

    assert!(outcome.deleted);
    assert_eq!(outcome.bot_uuid, bot_uuid);
    assert_eq!(
        ctx.cleanup.deleted_bot_ids.lock().await.as_slice(),
        &[bot_uuid.clone()]
    );
    assert_eq!(
        ctx.catalog_cleanup.deleted_bot_ids.lock().await.as_slice(),
        &[bot_uuid]
    );
}

#[tokio::test]
async fn delete_provider_bot_returns_error_when_catalog_cleanup_fails() {
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    register_provider_bot(&ctx, &provider_id, &admin_token, "bot-ref-1").await;
    *ctx.catalog_cleanup.fail.lock().await = true;

    let result = ctx
        .management
        .delete_provider_bot(delete_command(&provider_id, &admin_token, "bot-ref-1"))
        .await;

    assert!(result.is_err());
}

#[tokio::test]
async fn delete_provider_bot_returns_error_when_channel_binding_cleanup_fails() {
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    register_provider_bot(&ctx, &provider_id, &admin_token, "bot-ref-1").await;
    *ctx.cleanup.fail.lock().await = true;

    let result = ctx
        .management
        .delete_provider_bot(delete_command(&provider_id, &admin_token, "bot-ref-1"))
        .await;

    assert!(result.is_err());
}

#[tokio::test]
async fn delete_provider_bot_keeps_bindings_for_other_bots() {
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    let bot_uuid_1 = register_provider_bot(&ctx, &provider_id, &admin_token, "bot-ref-1").await;
    let bot_uuid_2 = register_provider_bot(&ctx, &provider_id, &admin_token, "bot-ref-2").await;
    assert_ne!(bot_uuid_1, bot_uuid_2);

    ctx.management
        .delete_provider_bot(delete_command(&provider_id, &admin_token, "bot-ref-1"))
        .await
        .expect("delete provider bot");

    assert_eq!(
        ctx.cleanup.deleted_bot_ids.lock().await.as_slice(),
        &[bot_uuid_1]
    );
}
