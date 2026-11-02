//! Provider retirement end-to-end (Task 4.1/4.3/4.4,
//! bot-authority spec §7.1) through the REAL
//! ProviderCore/Application over the REAL memory
//! projection: the three branches, the audit actor,
//! the cleanup-pending retry (idempotent, tombstone
//! lookup-back), the version0 runtime path and the
//! transfer-after-retire barrier.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_bot::application::ProviderManagement;
use bcs_bot::core::{BotCore, ProviderCore};
use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::provider::{MemoryBotProviderStore, MemoryProviderStore};
use bcs_service_api::core::RetireProviderBotParams;
use bcs_service_api::port::repo::{BotAuthorityRepoPort, bot_provider::BotProviderRepoPort};
use bcs_service_api::types::bot_authority::{
    AuthorityActor, BotAccessRelation, TransferAction, TransferCommand, TransferKey,
    TransferStatus, human_actor_id,
};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotCatalogCleanupPort,
    BotRegistryCoreService, ChannelBindingCleanupPort,
    DeleteProviderBotCommand, ProviderAuthMode, ProviderBotBindingRepoPort,
    ProviderBotConnectionMode, ProviderBotCoreService,
    ProviderCredentialRepoPort, ProviderManagementService, ProviderRepoPort,
    RegisterProviderBotCommand, RegisterProviderCommand, ServiceError,
    ServiceResult, types::bot_authority::AuthorityMutation,
};
use bcs_test_support::NoopRelationCoreService;
use tokio::sync::Mutex;

fn test_now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_millis() as u64)
        .unwrap_or(0)
}

struct RecordingCleanup {
    deleted_bot_ids: Mutex<Vec<String>>,
    fail: Mutex<bool>,
}

impl Default for RecordingCleanup {
    fn default() -> Self {
        Self {
            deleted_bot_ids: Mutex::new(Vec::new()),
            fail: Mutex::new(false),
        }
    }
}

#[async_trait]
impl ChannelBindingCleanupPort for RecordingCleanup {
    async fn delete_bindings_for_group(
        &self,
        _group_id: &str,
    ) -> ServiceResult<u64> {
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

#[derive(Default)]
struct RecordingCatalogCleanup {
    deleted_bot_ids: Mutex<Vec<String>>,
}

#[async_trait]
impl BotCatalogCleanupPort for RecordingCatalogCleanup {
    async fn delete_bot(&self, bot_id: &str) -> ServiceResult<()> {
        self.deleted_bot_ids.lock().await.push(bot_id.to_string());
        Ok(())
    }
}

struct TestContext {
    management: ProviderManagement,
    registry: Arc<dyn BotRegistryCoreService>,
    provider_core: Arc<ProviderCore>,
    cleanup: Arc<RecordingCleanup>,
    bot_repo: Arc<MemoryBotRepo>,
    bot_providers: Arc<MemoryBotProviderStore>,
    _temp_dir: tempfile::TempDir,
}

fn test_context() -> TestContext {
    let temp_dir = tempfile::tempdir().expect("temp dir");
    let provider_store = Arc::new(MemoryProviderStore::new());
    let provider_repo: Arc<dyn ProviderRepoPort> = provider_store.clone();
    let provider_credentials: Arc<dyn ProviderCredentialRepoPort> =
        provider_store.clone();
    let provider_bindings: Arc<dyn ProviderBotBindingRepoPort> =
        provider_store.clone();
    let bot_repo =
        Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    // The production composition always wires the Bot
    // Provider metadata over the SAME shared MemoryBotState.
    let bot_providers = Arc::new(MemoryBotProviderStore::new(
        bot_repo.clone(),
        provider_store.clone(),
    ));
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(
        BotCore::with_provider_repos(
            bot_repo.clone(),
            provider_repo.clone(),
            provider_credentials.clone(),
            provider_bindings.clone(),
        ),
    );
    let provider_core = Arc::new(
        ProviderCore::new(
            provider_repo,
            provider_credentials,
            provider_bindings,
            registry.clone(),
        )
        .with_bot_provider_repo(bot_providers.clone()),
    );
    let cleanup = Arc::new(RecordingCleanup::default());
    let management =
        ProviderManagement::new(
            provider_core.clone(),
            provider_core.clone(),
            registry.clone(),
            Arc::new(NoopRelationCoreService),
        )
        .with_channel_binding_cleanup(cleanup.clone())
        .with_bot_catalog_cleanup(Arc::new(RecordingCatalogCleanup::default()));
    TestContext {
        management,
        registry,
        provider_core,
        cleanup,
        bot_repo,
        bot_providers,
        _temp_dir: temp_dir,
    }
}

async fn register_provider(ctx: &TestContext) -> (String, String) {
    let registered = ctx
        .management
        .register_provider(RegisterProviderCommand {
            caller_capabilities: None,
            collaboration_scope: None,
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

/// The owner-suffixed legacy Bot (unbound; the
/// `allow_unbound_owner_suffixed_bot` flag only relaxes the
/// LOOKUP — the admission shape is the flag AND this id form).
async fn register_legacy_bot(ctx: &TestContext, bot_id: &str) {
    ctx.registry
        .register_with_owner_and_token(
            bot_id.to_string(),
            BotCapabilities::default(),
            "11111111",
            "legacy-runtime-token",
        )
        .await
        .expect("register legacy bot");
}

fn delete_command(
    provider_id: &str,
    admin_token: &str,
    provider_bot_ref: &str,
    allow_unbound: bool,
) -> DeleteProviderBotCommand {
    DeleteProviderBotCommand {
        provider_id: provider_id.to_string(),
        provider_admin_token: admin_token.to_string(),
        provider_bot_ref: provider_bot_ref.to_string(),
        allow_unbound_owner_suffixed_bot: allow_unbound,
    }
}

/// The audit rows of the memory projection ordered by
/// append. `(action, actor_kind, actor_id, subject_id)`.
async fn audit_rows(bot_repo: &Arc<MemoryBotRepo>, bot_id: &str) -> Vec<(String, String, String, String)> {
    bot_repo
        .authority_state()
        .read()
        .await
        .audit
        .iter()
        .filter(|row| row.bot_id == bot_id)
        .map(|row| {
            (
                row.action.clone(),
                row.actor_kind.clone(),
                row.actor_id.clone(),
                row.subject_id.clone(),
            )
        })
        .collect()
}

/// The Provider token from `register_provider` is a
/// credential — it must NEVER appear in any audit identity.
async fn assert_no_token_in_audit(bot_repo: &Arc<MemoryBotRepo>, token: &str) {
    let rows = bot_repo.authority_state().read().await;
    for row in &rows.audit {
        assert_ne!(
            row.actor_id, token,
            "a credential leaked into an audit identity"
        );
        assert_ne!(
            row.subject_id, token,
            "a credential leaked into an audit subject"
        );
    }
}

#[tokio::test]
async fn retirement_commits_one_atomic_retire_audit_per_branch() {
    // Metadata branch (plugin bot: record accepted) and
    // BindingFallback branch (gateway bot: binding routes)
    // each write exactly ONE `retire` audit with the
    // REAL Provider actor and no credential anywhere.
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;

    // Plugin (metadata) branch.
    let plugin = ctx
        .management
        .register_provider_bot(RegisterProviderBotCommand {
            webhook_url: None,
            provider_id: provider_id.clone(),
            provider_admin_token: admin_token.clone(),
            name: "Plugin Bot".to_string(),
            summary: None,
            owners: vec!["11111111".to_string()],
            provider_bot_ref: "plugin-ref".to_string(),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            bot_uuid: None,
            reject_existing_bot_uuid: false,
            connection_mode: ProviderBotConnectionMode::Plugin,
        })
        .await
        .expect("register plugin bot")
        .bot_uuid;
    let plugin_outcome = ctx
        .management
        .delete_provider_bot(delete_command(
            &provider_id,
            &admin_token,
            "plugin-ref",
            false,
        ))
        .await
        .expect("retire on the metadata branch");
    assert!(plugin_outcome.deleted);
    assert_eq!(plugin_outcome.bot_uuid, plugin);
    assert!(ctx.registry.try_get(&plugin).await.unwrap().is_none(),
        "the tombstone committed and the cache is invalidated");
    let actions = audit_rows(&ctx.bot_repo, &plugin).await;
    assert!(
        actions.iter().any(|(action, kind, id, _)| action == "retire"
            && kind == "provider"
            && id == &format!("provider_{provider_id}")),
        "the real Provider actor is the retire initiator: {actions:?}"
    );
    assert_no_token_in_audit(&ctx.bot_repo, &admin_token).await;

    // Gateway (binding fallback) branch.
    let (_gateway_provider, _gateway_user) = (provider_id.clone(), admin_token.clone());
    let gateway = ctx
        .management
        .register_provider_bot(RegisterProviderBotCommand {
            webhook_url: None,
            provider_id: provider_id.clone(),
            provider_admin_token: admin_token.clone(),
            name: "Gateway Bot".to_string(),
            summary: None,
            owners: vec!["11111111".to_string()],
            provider_bot_ref: "gateway-ref".to_string(),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            bot_uuid: None,
            reject_existing_bot_uuid: false,
            connection_mode: ProviderBotConnectionMode::Gateway,
        })
        .await
        .expect("register gateway bot")
        .bot_uuid;
    let gateway_outcome = ctx
        .management
        .delete_provider_bot(delete_command(
            &provider_id,
            &admin_token,
            "gateway-ref",
            false,
        ))
        .await
        .expect("retire on the binding fallback branch");
    assert!(gateway_outcome.deleted);
    assert_eq!(gateway_outcome.bot_uuid, gateway);
    let actions = audit_rows(&ctx.bot_repo, &gateway).await;
    assert!(
        actions.iter().any(|(action, kind, id, _)| action == "retire"
            && kind == "provider"
            && id == &format!("provider_{provider_id}")),
        "the binding fallback branch keeps the same audit shape: {actions:?}"
    );
}

#[tokio::test]
async fn retirement_cleanup_pending_is_retriable_and_idempotent() {
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    let bot_uuid = "legacy:11111111";
    register_legacy_bot(&ctx, bot_uuid).await;

    *ctx.cleanup.fail.lock().await = true;
    let failed = ctx
        .management
        .delete_provider_bot(delete_command(
            &provider_id, &admin_token, bot_uuid, true,
        ))
        .await;
    let error = failed.expect_err("the cleanup failure surfaces");
    assert!(
        format!("{error:?}").contains("retirement_cleanup_pending"),
        "the stable cleanup-pending code must surface: {error:?}"
    );
    // The tombstone is DURABLE: no rollback, no full
    // success, no resurrection from stale caches.
    assert!(ctx.registry.get(bot_uuid).await.is_none());

    *ctx.cleanup.fail.lock().await = false;
    let retried = ctx
        .management
        .delete_provider_bot(delete_command(
            &provider_id, &admin_token, bot_uuid, true,
        ))
        .await
        .expect("the retry re-derives from the tombstone");
    assert!(!retried.deleted, "idempotent: changed = false");
    assert_eq!(
        ctx.cleanup.deleted_bot_ids.lock().await.as_slice(),
        &[bot_uuid.to_string()],
        "the cleanup ran exactly once in total"
    );
    assert_eq!(
        audit_rows(&ctx.bot_repo, bot_uuid)
            .await
            .iter()
            .filter(|(action, ..)| action == "retire")
            .count(),
        1,
        "no duplicate retire audit on the retry"
    );
    assert_no_token_in_audit(&ctx.bot_repo, &admin_token).await;
}

#[tokio::test]
async fn retirement_never_reactivates_a_tombstoned_identity() {
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    let bot_uuid = "legacy2:11111111";
    register_legacy_bot(&ctx, bot_uuid).await;
    ctx.management
        .delete_provider_bot(delete_command(
            &provider_id, &admin_token, bot_uuid, true,
        ))
        .await
        .expect("retired");
    // Re-registration, onboarding-style ensures and token
    // mutations never resurrect a deleted ID (§3.2.8).
    assert!(
        !ctx
            .registry
            .create_registration_if_absent(
                bot_uuid.to_string(),
                BotCapabilities::default(),
                "11111111",
                "fresh-token",
            )
            .await
            .expect("atomic create"),
        "a deleted ID never resurrects through atomic re-creation"
    );
    assert!(ctx.registry.get(bot_uuid).await.is_none());
}

#[tokio::test]
async fn runtime_version0_bot_retire_stays_human_not_provider() {
    // The ordinary retirement path (Registry Core) keeps its
    // OWN contract: version0 runtime bots retire through the
    // BotCore retire with the real Human actor and get the
    // `retire` audit — provider-managed ones never chain in.
    let ctx = test_context();
    ctx.registry
        .register_with_owner_and_token(
            "runtime-v0".to_string(),
            BotCapabilities::default(),
            "22222222",
            "v0-token",
        )
        .await
        .expect("version0 runtime registration");
    let mutation = AuthorityMutation {
        mutation_id: "retire-runtime-v0".to_string(),
        actor: AuthorityActor::Human {
            user_id: "22222222".to_string(),
        },
    };
    let changed = ctx
        .registry
        .retire_with_authority("runtime-v0", mutation)
        .await
        .expect("ordinary retire");
    assert!(changed);
    let rows = audit_rows(&ctx.bot_repo, "runtime-v0").await;
    assert!(
        rows.iter().any(|(action, kind, id, subject)| action == "retire"
            && kind == "human"
            && id == "human_22222222"
            && subject == "runtime-v0"),
        "the real Human is the retire initiator: {rows:?}"
    );
    let again = ctx
        .registry
        .retire_with_authority(
            "runtime-v0",
            AuthorityMutation {
                mutation_id: "retire-runtime-v0-2".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "22222222".to_string(),
                },
            },
        )
        .await
        .expect("idempotent replay");
    assert!(!again);
}

#[tokio::test]
async fn retire_closes_pending_transfer_and_blocks_further_transfers() {
    let ctx = test_context();
    ctx.registry
        .register_with_owner_and_token(
            "runtime-v1".to_string(),
            BotCapabilities::default(),
            "11111111",
            "v1-token",
        )
        .await
        .expect("registered");
    // Seed a valid pending transfer directly on the shared
    // authority state (the fixture surface, spec §7). The
    // transfer target's live Human ACTOR row is ensured on
    // the shared registry first (the guards read it).
    {
        use bcs_service_api::port::repo::BotRepoPort;
        ctx.bot_repo
            .ensure_human_actor("22222222", "22222222")
            .await
            .expect("ensure fixture Human actor row");
    }
    {
        let mut state = ctx.bot_repo.authority_state().write().await;
        let env = bcs_config::resolve_env_str();
        state.ownership_versions.insert((env.clone(), "runtime-v1".into()), 1);
        state.edges.insert(
            (env.clone(), "runtime-v1".into(), "human_11111111".into(),
             bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner),
            (bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Approved, 0),
        );
        state
            .transfers
            .entry((env.clone(), "runtime-v1".into(), "human_22222222".into()))
            .or_insert_with(|| bcs_bot_store::memory_authority::MemoryTransferRow {
                bot_id: "runtime-v1".into(),
                target_owner_actor_id: "human_22222222".into(),
                from_owner_actor_id: "human_11111111".into(),
                expected_ownership_version: 1,
                status: TransferStatus::Pending,
                requested_at: test_now_ms() - 1_000,
                expires_at: test_now_ms() + 7 * 24 * 3600 * 1_000,
                updated_at: test_now_ms(),
                decided_at: None,
                decided_by_kind: None,
                decided_by_id: None,
                reason: None,
                result_ownership_version: None,
                bot_name_snapshot: Some("runtime-v1".into()),
            });
    }
    let mutation = AuthorityMutation {
        mutation_id: "retire-v1".to_string(),
        actor: AuthorityActor::Human {
            user_id: "11111111".to_string(),
        },
    };
    ctx.registry
        .retire_with_authority("runtime-v1", mutation)
        .await
        .expect("retired with a pending in flight");
    // The pending closed with the fixed system marker and the
    // retirement reason; the retire audit keeps the real Human.
    let rows = audit_rows(&ctx.bot_repo, "runtime-v1").await;
    assert!(
        rows.iter().any(|(action, kind, id, _)| action == "transfer_invalidate"
            && kind == "system"
            && id == "system_transfer_close"),
        "the pending closed with the system marker: {rows:?}"
    );
    {
        let state = ctx.bot_repo.authority_state().read().await;
        let env = bcs_config::resolve_env_str();
        let row = state
            .transfers
            .get(&(env, "runtime-v1".into(), "human_22222222".into()))
            .expect("row remains");
        assert_eq!(
            row.status,
            TransferStatus::Invalidated
        );
        assert_eq!(
            row.reason.as_deref(),
            Some("bot_retired"),
            "the fixed system reason"
        );
        let edge = state
            .edges
            .get(&(
                bcs_config::resolve_env_str(),
                "runtime-v1".into(),
                "human_11111111".into(),
                bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner,
            ))
            .expect("edge row remains");
        assert_eq!(
            edge.0,
            bcs_bot_store::memory_authority::MemoryAuthorityEdgeStatus::Revoked,
            "the owner edge revoked in the same commit"
        );
    }
    // Barrier: a transfer decision AFTER the retire cannot
    // resurrect a pending on the tombstoned Bot.
    let after = ctx
        .bot_repo
        .change_transfer(TransferCommand {
            key: TransferKey {
                bot_id: "runtime-v1".to_string(),
                target_owner_actor_id: human_actor_id("33333333"),
            },
            action: TransferAction::RequestOrRefresh,
            mutation: AuthorityMutation {
                mutation_id: "after-retire".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "11111111".to_string(),
                },
            },
        })
        .await;
    assert!(after.is_err(), "transfers after the retire are blocked");
}

#[tokio::test]
async fn ordinary_retire_refuses_provider_managed_and_unknown_bots() {
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    ctx.management
        .register_provider_bot(RegisterProviderBotCommand {
            webhook_url: None,
            provider_id: provider_id.clone(),
            provider_admin_token: admin_token.clone(),
            name: "Managed".to_string(),
            summary: None,
            owners: vec!["11111111".to_string()],
            provider_bot_ref: "managed-ref".to_string(),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            bot_uuid: None,
            reject_existing_bot_uuid: false,
            connection_mode: ProviderBotConnectionMode::Gateway,
        })
        .await
        .expect("managed bot");
    let bot_uuid = ctx
        .provider_core
        .get_provider_bot_binding_by_ref(&provider_id, "managed-ref")
        .await
        .expect("binding read")
        .expect("binding")
        .bot_uuid;
    // Attach the Provider metadata (the store's durable
    // provider-membership marker, like the SQL
    // `provider_id` column).
    ctx.bot_providers
        .attach_provider_bot(bcs_service_api::bot_provider::BotProviderRecord {
            bot_uuid: bot_uuid.clone(),
            provider_id: provider_id.clone(),
            provider_bot_ref: "managed-ref".to_string(),
            connection_mode: bcs_service_api::bot_provider::BotConnectionMode::Gateway,
            webhook_url: None,
            is_deleted: false,
        })
        .await
        .expect("attached metadata");
    // Ordinary retire REFUSES provider-managed bots: two
    // commands, two contracts, never chained.
    let refused = ctx
        .registry
        .retire_with_authority(
            &bot_uuid,
            AuthorityMutation {
                mutation_id: "ordinary-1".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "11111111".to_string(),
                },
            },
        )
        .await;
    assert!(
        refused.is_err(),
        "provider-managed bots retire through the Provider command"
    );
    // Unknown Bot: 404-shaped error, nothing written.
    let unknown = ctx
        .registry
        .retire_with_authority(
            "no-such-bot",
            AuthorityMutation {
                mutation_id: "ordinary-2".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "11111111".to_string(),
                },
            },
        )
        .await;
    assert!(matches!(unknown, Err(ServiceError::BotNotFound(_))));
}

#[tokio::test]
async fn provider_only_actor_needs_its_token_never_in_mutation() {
    // The Core command takes the token for VERIFICATION only:
    // a wrong token never retires and nothing is written.
    let ctx = test_context();
    let (provider_id, _admin_token) =
        register_provider(&ctx).await;
    let bot_uuid = "legacy3:11111111";
    register_legacy_bot(&ctx, bot_uuid).await;
    let params = RetireProviderBotParams {
        provider_id: provider_id.clone(),
        provider_admin_token: "bogus".to_string(),
        provider_bot_ref: bot_uuid.to_string(),
        allow_unbound_owner_suffixed_bot: true,
    };
    let refused = ctx.provider_core.retire_registered_bot(params).await;
    assert!(refused.is_err(), "verification-only token gate");
    assert!(ctx.registry.get(bot_uuid).await.is_some());
}

// ---------------------------------------------------------------------------
// Admin registration ownership minting (spec §10.3 trusted delegation):
// a freshly created admin Bot initializes its owner edge in the same
// registration call; the audit initiator is `provider_{id}` — the
// Provider credential vouches for the declared identity and never
// impersonates the claimed Human in the audit identity.
// ---------------------------------------------------------------------------

#[tokio::test]
async fn admin_registration_mints_ownership_for_the_declared_owner() {
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    let owner = "33333333";

    let outcome = ctx
        .management
        .register_provider_bot(RegisterProviderBotCommand {
            webhook_url: None,
            provider_id: provider_id.clone(),
            provider_admin_token: admin_token.clone(),
            name: "Owned Bot".to_string(),
            summary: None,
            owners: vec![owner.to_string()],
            provider_bot_ref: "owned-ref".to_string(),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            bot_uuid: None,
            reject_existing_bot_uuid: false,
            connection_mode: ProviderBotConnectionMode::Gateway,
        })
        .await
        .expect("fresh admin registration");
    assert!(outcome.created);

    // The owner edge is live for the strict authority read ...
    let relation = ctx
        .bot_repo
        .role(&human_actor_id(owner), &outcome.bot_uuid)
        .await
        .expect("role read");
    assert_eq!(relation, Some(BotAccessRelation::Owner));

    // ... backed by the necessary Human actor row (no user directory
    // wired in this harness → the staff_no fallback name, matching the
    // trusted-register behavior).
    let human = ctx
        .registry
        .get(&human_actor_id(owner))
        .await
        .expect("human actor row ensured");
    assert_eq!(human.actor_kind, ActorKind::Human);
    assert_eq!(human.capabilities.name.as_deref(), Some(owner));

    // The `initialize` audit records the PROVIDER principal; the
    // claimed Human never appears as its own initiator anywhere.
    let rows = audit_rows(&ctx.bot_repo, &outcome.bot_uuid).await;
    assert!(
        rows.iter().any(|(action, kind, id, _)| action == "initialize"
            && kind == "provider"
            && id == &format!("provider_{provider_id}")),
        "admin mint audits the Provider identity: {rows:?}"
    );
    assert_no_token_in_audit(&ctx.bot_repo, &admin_token).await;
}

#[tokio::test]
async fn admin_reregistration_under_a_divergent_owner_is_refused() {
    // The Provider-registration legacy guard refuses re-registering an
    // existing Bot under a different claimed owner ("Bot owner does not
    // match registration owner"): the committed owner is protected
    // BEFORE the authority composite is ever consulted.
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    register_legacy_bot(&ctx, "legacy-owned:11111111").await;
    {
        use bcs_service_api::port::repo::BotRepoPort;
        ctx.bot_repo
            .ensure_human_actor("11111111", "11111111")
            .await
            .expect("ensure the original owner's actor row");
    }
    ctx.registry
        .initialize_ownership(
            "legacy-owned:11111111",
            "11111111",
            AuthorityMutation {
                mutation_id: "seed-owner".to_string(),
                actor: AuthorityActor::Human {
                    user_id: "11111111".to_string(),
                },
            },
        )
        .await
        .expect("seed the original owner");

    let refused = ctx
        .management
        .register_provider_bot(RegisterProviderBotCommand {
            webhook_url: None,
            provider_id: provider_id.clone(),
            provider_admin_token: admin_token,
            name: "Reclaimed".to_string(),
            summary: None,
            owners: vec!["22222222".to_string()],
            provider_bot_ref: "legacy-owned:11111111".to_string(),
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            // Re-register the SAME bot id (the route derives this from
            // provider_bot_ref for allow-listed providers); the legacy
            // guard compares the claimed owner with the committed
            // created record and refuses the divergence.
            bot_uuid: Some("legacy-owned:11111111".to_string()),
            reject_existing_bot_uuid: false,
            connection_mode: ProviderBotConnectionMode::Gateway,
        })
        .await;
    assert!(
        matches!(refused, Err(ServiceError::Forbidden(_))),
        "divergent-owner re-registration must be refused: {refused:?}"
    );

    assert_eq!(
        ctx.bot_repo
            .role(&human_actor_id("11111111"), "legacy-owned:11111111")
            .await
            .expect("role read"),
        Some(BotAccessRelation::Owner),
    );
    assert_eq!(
        ctx.bot_repo
            .role(&human_actor_id("22222222"), "legacy-owned:11111111")
            .await
            .expect("role read"),
        None,
        "the divergent claim leaves no edge"
    );
    let rows = audit_rows(&ctx.bot_repo, "legacy-owned:11111111").await;
    assert!(
        !rows
            .iter()
            .any(|(action, kind, _, _)| action == "initialize" && kind == "provider"),
        "no provider initialize audit fired on the refused attempt: {rows:?}"
    );
}

#[tokio::test]
async fn admin_duplicate_registration_is_an_idempotent_replay() {
    // Same provider + same ref → the duplicate short-circuit: the
    // existing Bot is returned and NO second owner initialization
    // fires (a backend retry must never double-mint).
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    let owner = "33333333";
    let command = |token: &str| RegisterProviderBotCommand {
        webhook_url: None,
        provider_id: provider_id.clone(),
        provider_admin_token: token.to_string(),
        name: "Owned Bot".to_string(),
        summary: None,
        owners: vec![owner.to_string()],
        provider_bot_ref: "owned-ref".to_string(),
        domains: Vec::new(),
        skills: Vec::new(),
        scopes: Vec::new(),
        bot_uuid: None,
        reject_existing_bot_uuid: false,
        connection_mode: ProviderBotConnectionMode::Gateway,
    };

    let outcome = ctx
        .management
        .register_provider_bot(command(&admin_token))
        .await
        .expect("fresh admin registration");
    assert!(outcome.created);

    let replay = ctx
        .management
        .register_provider_bot(command(&admin_token))
        .await
        .expect("duplicate re-registration succeeds as a replay");
    assert!(!replay.created);
    assert_eq!(replay.bot_uuid, outcome.bot_uuid);

    let rows = audit_rows(&ctx.bot_repo, &outcome.bot_uuid).await;
    assert_eq!(
        rows.iter()
            .filter(|(action, _, _, _)| action == "initialize")
            .count(),
        1,
        "exactly one initialize audit across the replay: {rows:?}"
    );
}

/// A relation port that fails `ensure_owner_edges` exactly
/// once, simulating a transient store failure between the
/// binding commit and the ownership ensures (the window the
/// F2 regression lives in).
struct FailOnceRelation(std::sync::atomic::AtomicBool);

#[async_trait]
impl bcs_service_api::RelationCoreService for FailOnceRelation {
    async fn upsert_edge(&self, _: bcs_service_api::RelationEdge) -> ServiceResult<()> {
        Ok(())
    }
    async fn delete_edge(&self, _: &str, _: &str, _: &str) -> ServiceResult<()> {
        Ok(())
    }
    async fn get_edge(
        &self,
        _: &str,
        _: &str,
        _: &str,
    ) -> ServiceResult<Option<bcs_service_api::RelationEdge>> {
        Ok(None)
    }
    async fn ensure_owner_edges(&self, _: &str, _: &str, _: &str) -> ServiceResult<()> {
        if self
            .0
            .swap(false, std::sync::atomic::Ordering::SeqCst)
        {
            Err(ServiceError::InternalError(
                "injected transient relation failure".to_string(),
            ))
        } else {
            Ok(())
        }
    }
    async fn ensure_owner_edges_counted(
        &self,
        _: &str,
        _: &str,
        _: &str,
    ) -> ServiceResult<bcs_service_api::EnsureOwnerEdgesResult> {
        Ok(bcs_service_api::EnsureOwnerEdgesResult { created: 0, upgraded: 0 })
    }
    async fn add_friend_edges(&self, _: &str, _: &str, _: &str) -> ServiceResult<()> {
        Ok(())
    }
    async fn remove_friend_edges(&self, _: &str, _: &str, _: &str) -> ServiceResult<()> {
        Ok(())
    }
    async fn remove_all_friend_edges(&self, _: &str, _: &str) -> ServiceResult<()> {
        Ok(())
    }
    async fn add_relation_edge(&self, _: &str, _: &str, _: &str) -> ServiceResult<()> {
        Ok(())
    }
    async fn list_friends_via_relation(&self, _: &str, _: &str) -> ServiceResult<Vec<String>> {
        Ok(Vec::new())
    }
}

/// A registration that dies AFTER the Bot + binding commit
/// but BEFORE the ownership ensures still succeeds on retry —
/// and the retry must COMPLETE the missing authority
/// initialization instead of replaying success around
/// `ownership_version = 0`.
#[tokio::test]
async fn provider_admin_retry_completes_authority_after_transient_failure() {
    let ctx = test_context();
    let (provider_id, admin_token) = register_provider(&ctx).await;
    let owner = "33333333";
    let management = ProviderManagement::new(
        ctx.provider_core.clone(),
        ctx.provider_core.clone(),
        ctx.registry.clone(),
        Arc::new(FailOnceRelation(std::sync::atomic::AtomicBool::new(true))),
    );
    let command = |token: &str| RegisterProviderBotCommand {
        webhook_url: None,
        provider_id: provider_id.clone(),
        provider_admin_token: token.to_string(),
        name: "Retry Bot".to_string(),
        summary: None,
        owners: vec![owner.to_string()],
        provider_bot_ref: "retry-ref".to_string(),
        domains: Vec::new(),
        skills: Vec::new(),
        scopes: Vec::new(),
        bot_uuid: None,
        reject_existing_bot_uuid: false,
        connection_mode: ProviderBotConnectionMode::Gateway,
    };

    management
        .register_provider_bot(command(&admin_token))
        .await
        .expect_err("injected relation failure aborts the first attempt");

    let retried = management
        .register_provider_bot(command(&admin_token))
        .await
        .expect("retry succeeds once the store heals");
    assert!(!retried.created, "the binding row already exists");

    let state = ctx
        .registry
        .ownership_governance_state(&retried.bot_uuid)
        .await
        .expect("governance")
        .expect("governance row exists after a successful registration");
    assert_eq!(
        state.ownership_version, 1,
        "the retry must initialize ownership: {state:?}"
    );
    let rows = audit_rows(&ctx.bot_repo, &retried.bot_uuid).await;
    assert_eq!(
        rows.iter()
            .filter(|(action, _, _, _)| action == "initialize")
            .count(),
        1,
        "exactly one initialize audit across first-attempt failure + retry: {rows:?}"
    );
}
