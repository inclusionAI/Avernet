//! Contract tests for Bot::switch_delivery_to_provider.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot::{Bot, BotCore, ProviderCore};
use bcs_bot_store::provider::MemoryProviderStore;
use bcs_bot_store::MemoryBotRepo;
use bcs_user_directory_api::{UserDirectoryPlugin, UserDirectoryProfile};
use bcs_service_api::{
    EnsureOwnerEdgesResult,
    BotCapabilities, BotConnectionControlPort, BotManagementService, BotRegistryCoreService,
    BotUseCaseError, KickReason, ProviderAuthMode, ProviderBotBindingRepoPort,
    ProviderBotBinding, ProviderCoreService, ProviderCredentialRepoPort, ProviderRepoPort,
    RelationCoreService, RelationEdge, ServiceResult, SwitchDeliveryToProviderCommand,
};
use tempfile::TempDir;
use tokio::sync::Mutex;

#[derive(Default)]
struct RecordingKickPort {
    calls: Mutex<Vec<(String, KickReason)>>,
    return_value: bool,
}

impl RecordingKickPort {
    fn new(return_value: bool) -> Arc<Self> {
        Arc::new(Self {
            calls: Mutex::new(Vec::new()),
            return_value,
        })
    }
}

#[async_trait]
impl BotConnectionControlPort for RecordingKickPort {
    async fn kick(&self, bot_id: &str, reason: KickReason) -> bool {
        self.calls.lock().await.push((bot_id.to_string(), reason));
        self.return_value
    }
}

struct Fixture {
    core: Arc<BotCore>,
    provider: ProviderCore,
    provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
    relation: Arc<RecordingRelationCoreService>,
    kick: Arc<RecordingKickPort>,
    bot: Bot,
    _data_dir: TempDir,
}

#[derive(Default)]
struct RecordingRelationCoreService {
    owner_edges: Mutex<Vec<(String, String, String)>>,
}

#[derive(Default)]
struct RecordingUserDirectoryPlugin {
    nick_name: String,
    lookups: Mutex<Vec<String>>,
}

#[async_trait]
impl UserDirectoryPlugin for RecordingUserDirectoryPlugin {
    async fn lookup_by_staff_no(
        &self,
        staff_no: &str,
    ) -> Result<Option<UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
        self.lookups.lock().await.push(staff_no.to_string());
        Ok(Some(UserDirectoryProfile {
            staff_no: staff_no.to_string(),
            nick_name: Some(self.nick_name.clone()),
        }))
    }
}

#[async_trait]
impl RelationCoreService for RecordingRelationCoreService {
    async fn upsert_edge(&self, _edge: RelationEdge) -> ServiceResult<()> {
        Ok(())
    }

    async fn delete_edge(&self, _from_id: &str, _to_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_edge(
        &self,
        _from_id: &str,
        _to_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<RelationEdge>> {
        Ok(None)
    }

    async fn ensure_owner_edges(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<()> {
        self.owner_edges.lock().await.push((
            human_id.to_string(),
            bot_id.to_string(),
            env.to_string(),
        ));
        Ok(())
    }

    async fn ensure_owner_edges_counted(
        &self,
        human_id: &str,
        bot_id: &str,
        env: &str,
    ) -> ServiceResult<EnsureOwnerEdgesResult> {
        self.ensure_owner_edges(human_id, bot_id, env).await?;
        Ok(EnsureOwnerEdgesResult {
            created: 2,
            upgraded: 0,
        })
    }

    async fn add_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_friend_edges(&self, _a: &str, _b: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friend_edges(&self, _actor_id: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn add_relation_edge(&self, _caller: &str, _target: &str, _env: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn list_friends_via_relation(
        &self,
        _actor_id: &str,
        _env: &str,
    ) -> ServiceResult<Vec<String>> {
        Ok(Vec::new())
    }
}

impl Fixture {
    async fn new() -> Self {
        Self::new_with_kick_return(true).await
    }

    async fn new_with_kick_return(kicked: bool) -> Self {
        let data_dir = tempfile::tempdir().expect("temp data dir");
        let provider_store = Arc::new(MemoryProviderStore::new());
        let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
        let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
        let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(data_dir.path().to_path_buf()));
        let core = Arc::new(BotCore::with_provider_repos(
            bot_repo,
            provider_repo.clone(),
            provider_credentials.clone(),
            provider_bindings.clone(),
        ));
        let provider = ProviderCore::new(
            provider_repo,
            provider_credentials,
            provider_bindings.clone(),
            core.clone(),
        );
        let kick = RecordingKickPort::new(kicked);
        let relation = Arc::new(RecordingRelationCoreService::default());
        let bot = Bot::new(core.clone() as Arc<dyn BotRegistryCoreService>)
            .with_bot_core(core.clone())
            .with_relation(relation.clone())
            .with_connection_control(kick.clone() as Arc<dyn BotConnectionControlPort>);
        Self {
            core,
            provider,
            provider_bindings,
            relation,
            kick,
            bot,
            _data_dir: data_dir,
        }
    }

    async fn register_bot(&self, staff_no: &str) -> String {
        let bot_id = format!("ws-bot-{}", staff_no);
        self.core
            .register_with_owner_and_token(
                bot_id.clone(),
                BotCapabilities::default(),
                staff_no,
                "token-irrelevant",
            )
            .await
            .expect("register bot");
        bot_id
    }

    async fn register_ready_provider(&self, owner: &str) -> String {
        let outcome = self
            .provider
            .register_provider(
                "TestProvider".to_string(),
                "https://provider.example.com/webhook".to_string(),
                ProviderAuthMode::StaticBearer,
                owner.to_string(),
                None,
                None,
            )
            .await
            .expect("register provider");
        outcome.provider.provider_id
    }
}

#[tokio::test]
async fn switch_happy_path_creates_binding_and_kicks() {
    let f = Fixture::new().await;
    let bot_id = f.register_bot("alice").await;
    let provider_id = f.register_ready_provider("alice").await;

    let result = f
        .bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: bot_id.clone(),
            provider_id: provider_id.clone(),
            provider_bot_ref: "ext-ref-1:alice".to_string(),
            name: None,
            summary: None,
        })
        .await
        .expect("switch should succeed");

    assert_eq!(result.bot_id, bot_id);
    assert_eq!(result.provider_id, provider_id);
    assert_eq!(result.provider_bot_ref, "ext-ref-1:alice");
    assert!(!result.idempotent_replay);
    assert!(result.websocket_kicked);
    assert!(result.binding_created_at > 0);

    let kicks = f.kick.calls.lock().await;
    assert_eq!(kicks.len(), 1);
    assert_eq!(kicks[0].0, bot_id);
    assert_eq!(kicks[0].1, KickReason::DeliverySwitchedToProvider);
}

#[tokio::test]
async fn switch_is_idempotent_when_binding_matches() {
    let f = Fixture::new().await;
    let bot_id = f.register_bot("alice").await;
    let provider_id = f.register_ready_provider("alice").await;

    let cmd = SwitchDeliveryToProviderCommand {
        bot_id: bot_id.clone(),
        provider_id: provider_id.clone(),
        provider_bot_ref: "ext-ref-1:alice".to_string(),
        name: None,
        summary: None,
    };
    let _first = f.bot.switch_delivery_to_provider(cmd.clone()).await.unwrap();
    let second = f.bot.switch_delivery_to_provider(cmd).await.unwrap();

    assert!(second.idempotent_replay);
    let kicks = f.kick.calls.lock().await;
    assert_eq!(kicks.len(), 2);
}

#[tokio::test]
async fn switch_idempotent_same_binding_auto_onboards_missing_bot() {
    let f = Fixture::new().await;
    let bot_id = "teamclaw-bot:alice".to_string();
    let provider_id = f.register_ready_provider("alice").await;

    f.provider_bindings
        .insert_binding(ProviderBotBinding {
            bot_uuid: bot_id.clone(),
            provider_id: provider_id.clone(),
            provider_bot_ref: bot_id.clone(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .expect("insert existing binding");
    assert!(!f.core.has_been_onboarded(&bot_id).await);

    let result = f
        .bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: bot_id.clone(),
            provider_id,
            provider_bot_ref: bot_id.clone(),
            name: Some("Teamclaw Bot".to_string()),
            summary: Some("Handles Teamclaw tasks".to_string()),
        })
        .await
        .expect("same binding should repair missing bot row");

    assert!(result.idempotent_replay);
    let bot = f
        .core
        .get(&bot_id)
        .await
        .expect("missing bot row should be auto onboarded");
    assert_eq!(bot.capabilities.name.as_deref(), Some("Teamclaw Bot"));
    assert_eq!(
        bot.capabilities.summary.as_deref(),
        Some("Handles Teamclaw tasks")
    );
    assert_eq!(bot.created_by.as_deref(), Some("alice"));
}

#[tokio::test]
async fn switch_conflicts_when_binding_disagrees() {
    let f = Fixture::new().await;
    let bot_id = f.register_bot("alice").await;
    let provider_id = f.register_ready_provider("alice").await;

    f.bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: bot_id.clone(),
            provider_id: provider_id.clone(),
            provider_bot_ref: "ext-ref-1:alice".to_string(),
            name: None,
            summary: None,
        })
        .await
        .unwrap();

    let err = f
        .bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: bot_id.clone(),
            provider_id: provider_id.clone(),
            provider_bot_ref: "ext-ref-DIFFERENT:alice".to_string(),
            name: None,
            summary: None,
        })
        .await
        .expect_err("must conflict");

    match err {
        BotUseCaseError::BotAlreadyBound {
            bot_id: b,
            existing_provider_id,
            existing_provider_bot_ref,
        } => {
            assert_eq!(b, bot_id);
            assert_eq!(existing_provider_id, provider_id);
            assert_eq!(existing_provider_bot_ref, "ext-ref-1:alice");
        }
        other => panic!("expected BotAlreadyBound, got {other:?}"),
    }
}

#[tokio::test]
async fn switch_conflicts_when_provider_ref_belongs_to_another_bot_without_auto_onboarding() {
    let f = Fixture::new().await;
    let bot_id = f.register_bot("alice").await;
    let provider_id = f.register_ready_provider("alice").await;

    f.bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: bot_id.clone(),
            provider_id: provider_id.clone(),
            provider_bot_ref: "shared-ref:alice".to_string(),
            name: None,
            summary: None,
        })
        .await
        .expect("first switch should succeed");

    let err = f
        .bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: "teamclaw-bot:bob".to_string(),
            provider_id: provider_id.clone(),
            provider_bot_ref: "shared-ref:alice".to_string(),
            name: Some("Bob Bot".to_string()),
            summary: Some("Bob summary".to_string()),
        })
        .await
        .expect_err("provider ref must not be rebound to another bot");

    match err {
        BotUseCaseError::BotAlreadyBound {
            bot_id,
            existing_provider_id,
            existing_provider_bot_ref,
        } => {
            assert_eq!(bot_id, "teamclaw-bot:bob");
            assert_eq!(existing_provider_id, provider_id);
            assert_eq!(existing_provider_bot_ref, "shared-ref:alice");
        }
        other => panic!("expected BotAlreadyBound, got {other:?}"),
    }
    assert!(!f.core.has_been_onboarded("teamclaw-bot:bob").await);
}

#[tokio::test]
async fn switch_existing_bot_without_owner_backfills_human_actor_and_owner_edges_from_provider_ref() {
    let f = Fixture::new().await;
    let bot_id = "ws-bot-without-owner".to_string();
    f.core
        .register(
            bot_id.clone(),
            BotCapabilities {
                name: Some("Existing Bot".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("register ownerless bot");
    assert_eq!(f.core.get(&bot_id).await.unwrap().created_by, None);
    let provider_id = f.register_ready_provider("provider-owner").await;

    f.bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: bot_id.clone(),
            provider_id,
            provider_bot_ref: "trusted-ref:alice".to_string(),
            name: None,
            summary: None,
        })
        .await
        .expect("switch should backfill owner binding");

    let bot = f.core.get(&bot_id).await.expect("bot should remain registered");
    assert_eq!(bot.created_by.as_deref(), Some("alice"));
    let human = f
        .core
        .get("human_alice")
        .await
        .expect("human actor should be ensured");
    assert_eq!(human.created_by.as_deref(), Some("alice"));
    let owner_edges = f.relation.owner_edges.lock().await;
    assert!(
        owner_edges
            .iter()
            .any(|(human_id, edge_bot_id, _)| human_id == "human_alice" && edge_bot_id == &bot_id),
        "expected owner edge for human_alice -> {bot_id}, got {owner_edges:?}",
    );
}

#[tokio::test]
async fn switch_existing_bot_overwrites_created_by_from_provider_ref() {
    let f = Fixture::new().await;
    let bot_id = f.register_bot("bob").await;
    assert_eq!(
        f.core
            .get(&bot_id)
            .await
            .expect("bot should be registered")
            .created_by
            .as_deref(),
        Some("bob"),
    );
    let provider_id = f.register_ready_provider("provider-owner").await;

    f.bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: bot_id.clone(),
            provider_id,
            provider_bot_ref: "trusted-ref:alice".to_string(),
            name: None,
            summary: None,
        })
        .await
        .expect("switch should trust provider_bot_ref owner");

    let bot = f.core.get(&bot_id).await.expect("bot should remain registered");
    assert_eq!(bot.created_by.as_deref(), Some("alice"));
    let human = f
        .core
        .get("human_alice")
        .await
        .expect("human actor should be ensured");
    assert_eq!(human.created_by.as_deref(), Some("alice"));
    let owner_edges = f.relation.owner_edges.lock().await;
    assert!(
        owner_edges
            .iter()
            .any(|(human_id, edge_bot_id, _)| human_id == "human_alice" && edge_bot_id == &bot_id),
        "expected owner edge for human_alice -> {bot_id}, got {owner_edges:?}",
    );
}

#[tokio::test]
async fn switch_ensures_human_actor_with_nick_name_from_user_directory() {
    let f = Fixture::new().await;
    let user_directory = Arc::new(RecordingUserDirectoryPlugin {
        nick_name: "Alice Hua".to_string(),
        ..Default::default()
    });
    let bot = f.bot.clone().with_user_directory(user_directory.clone());
    let bot_id = f.register_bot("bob").await;
    let provider_id = f.register_ready_provider("provider-owner").await;

    bot.switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
        bot_id: bot_id.clone(),
        provider_id,
        provider_bot_ref: "trusted-ref:alice".to_string(),
        name: None,
        summary: None,
    })
    .await
    .expect("switch should ensure human actor with resolved nick name");

    let human = f
        .core
        .get("human_alice")
        .await
        .expect("human actor should be ensured");
    assert_eq!(human.capabilities.name.as_deref(), Some("Alice Hua"));
    assert_eq!(user_directory.lookups.lock().await.as_slice(), ["alice"]);
}

#[tokio::test]
async fn switch_auto_onboards_missing_bot_with_request_metadata_and_generated_token() {
    let f = Fixture::new().await;
    let provider_id = f.register_ready_provider("alice").await;

    let result = f
        .bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: "teamclaw-bot:alice".to_string(),
            provider_id: provider_id.clone(),
            provider_bot_ref: "teamclaw-bot:alice".to_string(),
            name: Some("Teamclaw Bot".to_string()),
            summary: Some("Handles Teamclaw tasks".to_string()),
        })
        .await
        .expect("switch should auto onboard missing bot");

    assert_eq!(result.bot_id, "teamclaw-bot:alice");
    assert_eq!(result.provider_id, provider_id);
    assert_eq!(result.provider_bot_ref, "teamclaw-bot:alice");
    assert!(!result.idempotent_replay);
    assert!(result.binding_created_at > 0);

    assert!(f.core.has_been_onboarded("teamclaw-bot:alice").await);
    let token = f.core.load_token("teamclaw-bot:alice").await;
    assert!(token.as_deref().is_some_and(|token| !token.is_empty()));

    let caps = f
        .core
        .load_from_storage("teamclaw-bot:alice")
        .await
        .expect("auto onboarded capabilities should be persisted");
    assert_eq!(caps.name.as_deref(), Some("Teamclaw Bot"));
    assert_eq!(caps.summary.as_deref(), Some("Handles Teamclaw tasks"));

    let bot = f
        .core
        .get("teamclaw-bot:alice")
        .await
        .expect("auto onboarded bot should be in registry memory");
    assert_eq!(bot.created_by.as_deref(), Some("alice"));
}

#[tokio::test]
async fn switch_auto_onboard_persists_runtime_registry_token_when_present() {
    let f = Fixture::new().await;
    let provider_id = f.register_ready_provider("alice").await;
    let bot_id = "teamclaw-bot:alice".to_string();
    let runtime_token = f
        .core
        .register_streaming_connection(bot_id.clone())
        .await
        .expect("pre-register streaming bot");

    f.bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: bot_id.clone(),
            provider_id,
            provider_bot_ref: bot_id.clone(),
            name: Some("Teamclaw Bot".to_string()),
            summary: Some("Handles Teamclaw tasks".to_string()),
        })
        .await
        .expect("switch should auto onboard missing bot");

    assert_eq!(f.core.load_token(&bot_id).await.as_deref(), Some(runtime_token.as_str()));
    assert_eq!(f.core.find_bot_by_token(&runtime_token).await.as_deref(), Some(bot_id.as_str()));
}

#[tokio::test]
async fn switch_auto_onboard_falls_back_to_bot_id_for_blank_name_and_summary() {
    let f = Fixture::new().await;
    let provider_id = f.register_ready_provider("alice").await;

    f.bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: "teamclaw-bot:alice".to_string(),
            provider_id,
            provider_bot_ref: "teamclaw-bot:alice".to_string(),
            name: Some("   ".to_string()),
            summary: None,
        })
        .await
        .expect("switch should auto onboard with fallback metadata");

    let caps = f
        .core
        .load_from_storage("teamclaw-bot:alice")
        .await
        .expect("auto onboarded capabilities should be persisted");
    assert_eq!(caps.name.as_deref(), Some("teamclaw-bot:alice"));
    assert_eq!(caps.summary.as_deref(), Some("teamclaw-bot:alice"));
}

#[tokio::test]
async fn switch_rejects_unknown_provider() {
    let f = Fixture::new().await;
    let bot_id = f.register_bot("alice").await;

    let err = f
        .bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id,
            provider_id: "nonexistent".to_string(),
            provider_bot_ref: "ext-ref-1:alice".to_string(),
            name: None,
            summary: None,
        })
        .await
        .expect_err("must fail");

    assert!(
        matches!(err, BotUseCaseError::ProviderNotFound(_)),
        "expected ProviderNotFound, got {err:?}",
    );
}

#[tokio::test]
async fn switch_passes_through_when_kick_port_absent() {
    let data_dir = tempfile::tempdir().expect("temp data dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> = provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> = provider_store.clone();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(data_dir.path().to_path_buf()));
    let core = Arc::new(BotCore::with_provider_repos(
        bot_repo,
        provider_repo.clone(),
        provider_credentials.clone(),
        provider_bindings.clone(),
    ));
    let provider = ProviderCore::new(
        provider_repo,
        provider_credentials,
        provider_bindings,
        core.clone(),
    );
    let bot = Bot::new(core.clone() as Arc<dyn BotRegistryCoreService>)
        .with_bot_core(core.clone())
        .with_relation(Arc::new(RecordingRelationCoreService::default()));

    core.register_with_owner_and_token(
        "ws-bot-bob".to_string(),
        BotCapabilities::default(),
        "bob",
        "tok",
    )
    .await
    .unwrap();
    let prov = provider
        .register_provider(
            "P".to_string(),
            "https://example.com/webhook".to_string(),
            ProviderAuthMode::StaticBearer,
            "bob".to_string(),
            None,
            None,
        )
        .await
        .unwrap();

    let result = bot
        .switch_delivery_to_provider(SwitchDeliveryToProviderCommand {
            bot_id: "ws-bot-bob".to_string(),
            provider_id: prov.provider.provider_id,
            provider_bot_ref: "r:bob".to_string(),
            name: None,
            summary: None,
        })
        .await
        .expect("switch ok");
    assert!(!result.websocket_kicked);
}
