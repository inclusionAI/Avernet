use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot::application::ProviderManagement;
use bcs_bot::core::{BotCore, ProviderCore};
use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_service_api::{
    BotRegistryCoreService, ChannelBindingCleanupPort, DeleteProviderBotCommand,
    ProviderAuthMode, ProviderBotBindingRepoPort, ProviderBotCoreService,
    ProviderCredentialRepoPort, ProviderManagementService, ProviderRepoPort,
    RegisterProviderBotCommand, RegisterProviderCommand, ServiceError, ServiceResult,
};
use bcs_test_support::NoopRelationCoreService;
use tokio::sync::Mutex;

struct TestContext {
    management: ProviderManagement,
    provider_core: Arc<ProviderCore>,
    cleanup: Arc<RecordingCleanup>,
    _temp_dir: tempfile::TempDir,
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
        bot_repo,
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
    let management = ProviderManagement::new(
        provider_core.clone(),
        provider_bot_core,
        registry,
        Arc::new(NoopRelationCoreService),
    )
    .with_channel_binding_cleanup(cleanup.clone());
    TestContext {
        management,
        provider_core,
        cleanup,
        _temp_dir: temp_dir,
    }
}

async fn register_provider(ctx: &TestContext) -> (String, String) {
    let registered = ctx
        .management
        .register_provider(RegisterProviderCommand {
            name: "Provider".to_string(),
            webhook_url: "https://provider.example.com/bcs/webhook".to_string(),
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
        &[bot_uuid]
    );
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
