//! Provider-declared initial managers use real application/Core/Memory stores.
#![allow(clippy::expect_used, clippy::unwrap_used)]
use std::sync::Arc;
use bcs_bot::{application::ProviderManagement, core::{BotCore, ProviderCore}};
use bcs_bot_store::{MemoryBotRepo, provider::{MemoryBotProviderStore, MemoryProviderStore}};
use bcs_service_api::{
    BotRegistryCoreService, ProviderManagementService, RegisterProviderCommand,
    RegisterProviderBotCommand, ProviderAuthMode, ProviderBotConnectionMode,
    port::repo::BotAuthorityRepoPort,
    types::{bot_authority::{BotAccessRelation, human_actor_id}, bot_profile::AvatarChange},
};
use bcs_relation::RelationCore;

struct Fixture {
    management: ProviderManagement,
    core: Arc<ProviderCore>,
    relation: Arc<RelationCore>,
    repo: Arc<MemoryBotRepo>,
    registry: Arc<dyn BotRegistryCoreService>,
    provider_id: String,
    token: String,
    _dir: tempfile::TempDir,
}
impl Fixture {
    async fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let repo = Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
        let providers = Arc::new(MemoryProviderStore::new());
        let metadata = Arc::new(MemoryBotProviderStore::new(repo.clone(), providers.clone()));
        let registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_provider_repos(
            repo.clone(), providers.clone(), providers.clone(), providers.clone()));
        let core = Arc::new(ProviderCore::new(providers.clone(), providers.clone(), providers,
            registry.clone()).with_bot_provider_repo(metadata));
        let relation = Arc::new(RelationCore::new());
        let management = ProviderManagement::new(core.clone(), core.clone(), registry.clone(), relation.clone());
        let provider = management.register_provider(RegisterProviderCommand { a2a: None,
            name: "Provider".into(), webhook_url: Some("https://provider.example.com/webhook".into()),
            auth_mode: ProviderAuthMode::StaticBearer, created_by: "alice".into(),
            caller_capabilities: None, collaboration_scope: None, slug: None, admin_callback_url: None,
            protocol_version: None, coordination: None,
        }).await.unwrap();
        Self { management, core, relation, repo, registry, provider_id: provider.provider_id,
            token: provider.provider_admin_token, _dir: dir }
    }
    fn command(&self, owners: &[&str]) -> RegisterProviderBotCommand {
        RegisterProviderBotCommand {
            provider_id: self.provider_id.clone(), provider_admin_token: self.token.clone(),
            name: "Managed Bot".into(), owners: owners.iter().map(|id| (*id).into()).collect(),
            provider_bot_ref: "managed-ref".into(), webhook_url: None, summary: None,
            domains: vec![], skills: vec![], scopes: vec![], bot_uuid: None,
            reject_existing_bot_uuid: false, connection_mode: ProviderBotConnectionMode::Gateway,
            avatar: AvatarChange::Keep,
        }
    }
}

#[tokio::test]
async fn provider_initializes_first_owner_and_remaining_managers() {
    let f = Fixture::new().await;
    let result = f.management.register_provider_bot(f.command(&["alice", "bob", "carol"])).await
        .expect("multiple declared users must initialize owner and managers");
    for (user, role) in [("alice", BotAccessRelation::Owner), ("bob", BotAccessRelation::Manager),
        ("carol", BotAccessRelation::Manager)] {
        assert_eq!(f.repo.role(&human_actor_id(user), &result.bot_uuid).await.unwrap(), Some(role));
        assert!(f.registry.get(&human_actor_id(user)).await.is_some());
    }
    assert_eq!(f.registry.get(&result.bot_uuid).await.unwrap().created_by.as_deref(), Some("alice"));
}

#[tokio::test]
async fn normalizes_duplicates_and_audits_provider_snapshot() {
    let f = Fixture::new().await;
    let result = f.management.register_provider_bot(f.command(&[" alice ", " bob ", "bob", "alice", "Carol", "carol", "Jose", "José"])).await.unwrap();
    let state = f.repo.authority_state().read().await;
    let audit = state.audit.iter().find(|a| a.bot_id == result.bot_uuid && a.action == "initialize").unwrap();
    assert_eq!(audit.actor_kind, "provider");
    assert_eq!(audit.actor_id, format!("provider_{}", f.provider_id));
    let snapshot: serde_json::Value = serde_json::from_str(&audit.after_state).unwrap();
    assert_eq!(snapshot["managers"], serde_json::json!(["human_bob", "human_Carol", "human_carol", "human_Jose", "human_José"]));
}

#[tokio::test]
async fn invalid_entries_fail_before_bot_creation() {
    for users in [vec![], vec!["", "bob"], vec!["alice", " "], vec!["alice", "human_bob"], vec!["alice", "bo\nb"]] {
        let f = Fixture::new().await;
        let mut command = f.command(&users);
        command.bot_uuid = Some("invalid-bot".into());
        assert!(f.management.register_provider_bot(command).await.is_err());
        assert!(f.registry.get("invalid-bot").await.is_none());
    }
}

#[tokio::test]
async fn gateway_and_plugin_replay_do_not_restore_removed_managers() {
    use bcs_service_api::types::bot_authority::{AuthorityActor, AuthorityMutation, ManagerEdit, ManagerEditAction};
    for mode in [ProviderBotConnectionMode::Gateway, ProviderBotConnectionMode::Plugin] {
        let f = Fixture::new().await;
        let command = || { let mut c = f.command(&["alice", "bob"]); c.connection_mode = mode;
            c.bot_uuid = Some("replay-bot".into()); c };
        let first = f.management.register_provider_bot(command()).await.unwrap();
        f.repo.edit_manager(ManagerEdit { bot_id: first.bot_uuid.clone(), user_id: human_actor_id("bob"),
            action: ManagerEditAction::Revoke, mutation: AuthorityMutation { mutation_id: "revoke-bob".into(),
                actor: AuthorityActor::Human { user_id: "alice".into() } } }).await.unwrap();
        let mut replay = command(); replay.owners.push("carol".into());
        let second = f.management.register_provider_bot(replay).await.unwrap();
        assert_eq!(first.bot_uuid, second.bot_uuid);
        for user in ["bob", "carol"] {
            assert_eq!(f.repo.role(&human_actor_id(user), &first.bot_uuid).await.unwrap(), None);
        }
        let state = f.repo.authority_state().read().await;
        assert_eq!(state.audit.iter().filter(|a| a.bot_id == first.bot_uuid && a.action == "initialize").count(), 1);
    }
}

#[tokio::test]
async fn failed_manager_validation_does_not_commit_owner_or_other_managers() {
    use bcs_service_api::{BotCapabilities, port::repo::BotRepoPort};
    let f = Fixture::new().await;
    // A non-Human occupying the actor ID must not become a Human via initialization.
    f.registry.register_with_owner_and_token("human_bob".into(), BotCapabilities::default(), "other", "other-token").await.unwrap();
    let mut c = f.command(&["alice", "carol", "bob"]); c.bot_uuid = Some("failed-bot".into());
    assert!(f.management.register_provider_bot(c).await.is_err());
    assert!(f.repo.ownership_governance_state("failed-bot").await.unwrap().is_none(),
        "failed multi-user registration must not leave a Bot that mine can initialize owner-only");
    assert!(f.repo.list_uninitialized_bots_by_creator("alice", 100).await.unwrap().is_empty());
    assert!(f.registry.get("human_carol").await.is_none());
    assert!(!f.repo.authority_state().read().await.audit.iter().any(|a| a.bot_id == "failed-bot"));
    let mut retry = f.command(&["alice", "carol"]); retry.bot_uuid = Some("failed-bot".into());
    f.management.register_provider_bot(retry).await.unwrap();
    assert_eq!(f.repo.role("human_carol", "failed-bot").await.unwrap(), Some(BotAccessRelation::Manager));
}

#[tokio::test]
async fn registration_replay_preserves_a_transferred_owner_and_managers_cannot_transfer() {
    use bcs_service_api::types::bot_authority::{AuthorityActor, AuthorityMutation,
        TransferKey, TransferCommand, TransferAction};
    for mode in [ProviderBotConnectionMode::Gateway, ProviderBotConnectionMode::Plugin] {
        let f = Fixture::new().await;
        let command = || { let mut c = f.command(&["alice", "bob", "carol"]);
            c.connection_mode = mode; c.bot_uuid = Some("transfer-bot".into()); c };
        f.management.register_provider_bot(command()).await.unwrap();
        let transfer = |user: &str, action, mutation: &str| TransferCommand {
            key: TransferKey { bot_id: "transfer-bot".into(), target_owner_actor_id: "human_carol".into() },
            action, mutation: AuthorityMutation { mutation_id: mutation.into(),
                actor: AuthorityActor::Human {user_id: user.into()} },
        };
        assert!(f.repo.change_transfer(transfer("bob", TransferAction::RequestOrRefresh, "manager-denied")).await.is_err());
        f.repo.change_transfer(transfer("alice", TransferAction::RequestOrRefresh, "owner-request")).await.unwrap();
        f.repo.change_transfer(transfer("carol", TransferAction::Accept, "owner-accept")).await.unwrap();
        f.management.register_provider_bot(command()).await.unwrap();
        assert_eq!(f.repo.role("human_carol", "transfer-bot").await.unwrap(), Some(BotAccessRelation::Owner));
        assert_ne!(f.repo.role("human_alice", "transfer-bot").await.unwrap(), Some(BotAccessRelation::Owner));
    }
}

#[tokio::test]
async fn mine_between_core_and_application_cannot_steal_initial_manager_snapshot() {
    use bcs_service_api::{ProviderBotCoreService, RegisterProviderBotParams, port::repo::BotRepoPort};
    for mode in [ProviderBotConnectionMode::Gateway, ProviderBotConnectionMode::Plugin] {
        let f = Fixture::new().await;
        let params = RegisterProviderBotParams { bot_name: "Managed Bot".into(),
            owners: vec!["alice".into()], provider_bot_ref: "managed-ref".into(),
            bot_uuid: Some("interleaved-bot".into()), connection_mode: mode,
            webhook_url: None, summary: None, domains: vec![], skills: vec![], scopes: vec![], initial_caller: None };
        f.core.register_provider_bot_with_initial_managers(&f.provider_id, &f.token, params, vec!["bob".into()]).await.unwrap();
        // Force the review's exact pause: Core has returned, the application has
        // not done its governance read. The mine sweep cannot find a v0 candidate.
        assert!(f.repo.list_uninitialized_bots_by_creator("alice", 100).await.unwrap().is_empty());
        assert_eq!(f.repo.role("human_bob", "interleaved-bot").await.unwrap(), Some(BotAccessRelation::Manager));
        let mut replay = f.command(&["alice", "bob"]);
        replay.bot_uuid = Some("interleaved-bot".into()); replay.connection_mode = mode;
        f.management.register_provider_bot(replay).await.unwrap();
        assert_eq!(f.repo.role("human_bob", "interleaved-bot").await.unwrap(), Some(BotAccessRelation::Manager));
    }
}

#[tokio::test]
async fn multi_user_attachment_refuses_uninitialized_existing_bot_without_mutation() {
    use bcs_service_api::{BotCapabilities, port::repo::BotRepoPort};
    let f = Fixture::new().await;
    f.registry.register_with_owner_and_token("existing".into(), BotCapabilities::default(), "alice", "existing-token").await.unwrap();
    let mut command = f.command(&["alice", "bob"]); command.bot_uuid = Some("existing".into());
    assert!(matches!(f.management.register_provider_bot(command).await, Err(bcs_service_api::ServiceError::Conflict(_))));
    assert_eq!(f.repo.ownership_governance_state("existing").await.unwrap().unwrap().ownership_version, 0);
    assert_eq!(f.registry.load_token("existing").await.as_deref(), Some("existing-token"));
    assert!(f.registry.get("human_bob").await.is_none());
}

#[tokio::test]
async fn fresh_bot_cannot_collide_with_its_initial_human_identities() {
    for id in ["human_alice", "human_bob"] {
        let f = Fixture::new().await;
        let mut command = f.command(&["alice", "bob"]); command.bot_uuid = Some(id.into());
        assert!(f.management.register_provider_bot(command).await.is_err(), "physical Bot must not alias an initial Human");
        assert!(f.registry.get(id).await.is_none());
        assert!(!f.repo.authority_state().read().await.audit.iter().any(|a| a.bot_id == id));
    }
}

#[tokio::test]
async fn atomic_authority_registration_does_not_write_retired_creator_relations() {
    use bcs_service_api::RelationCoreService;
    let f = Fixture::new().await;
    let bot = f.management.register_provider_bot(f.command(&["alice", "bob"])).await.unwrap();
    assert!(f.relation.get_edge("human_alice", &bot.bot_uuid, &bcs_config::resolve_env_str()).await.unwrap().is_none(),
        "atomic registration uses authority edges, like the trusted Human/A2A composites");
    assert_eq!(f.repo.role("human_alice", &bot.bot_uuid).await.unwrap(), Some(BotAccessRelation::Owner));
}

#[tokio::test]
async fn provider_reactivation_replaces_snapshot_and_rotates_runtime_token() {
    use bcs_bot_store::{MemoryAuthorityEdgeKind, MemoryTransferRow};
    use bcs_service_api::{
        BotRepoPort, DeleteProviderBotCommand, ProviderBotCoreService,
        types::bot_authority::TransferStatus,
    };

    let f = Fixture::new().await;
    let mut old = f.command(&["old-owner", "old-manager"]);
    old.name = "Old Bot".into();
    old.summary = Some("old summary".into());
    old.domains = vec!["old-domain".into()];
    old.skills = vec![bcs_service_api::Skill::new("old-skill")];
    old.scopes = vec!["old-scope".into()];
    old.bot_uuid = Some("stable-provider-bot".into());
    old.webhook_url = Some("https://example.org/old".into());
    let registered = f.management.register_provider_bot(old).await.unwrap();
    let old_token = registered.bot_runtime_token.clone().unwrap();
    assert_eq!(registered.bot_uuid, "stable-provider-bot");

    let env = bcs_config::resolve_env_str();
    f.repo.authority_state().write().await.transfers.insert(
        (env, registered.bot_uuid.clone(), "human_transfer-target".into()),
        MemoryTransferRow {
            bot_id: registered.bot_uuid.clone(),
            target_owner_actor_id: "human_transfer-target".into(),
            from_owner_actor_id: "human_old-owner".into(),
            expected_ownership_version: 1,
            status: TransferStatus::Pending,
            requested_at: 1,
            expires_at: u64::MAX,
            updated_at: 1,
            decided_at: None,
            decided_by_kind: None,
            decided_by_id: None,
            reason: None,
            result_ownership_version: None,
            bot_name_snapshot: None,
        },
    );
    f.management.delete_provider_bot(DeleteProviderBotCommand {
        provider_id: f.provider_id.clone(),
        provider_admin_token: f.token.clone(),
        provider_bot_ref: "managed-ref".into(),
        allow_unbound_owner_suffixed_bot: false,
    }).await.unwrap();
    assert!(f.repo.find_bot_by_token(&old_token).await.is_none());

    let mut restored = f.command(&["new-owner", "new-manager"]);
    restored.name = "New Bot".into();
    restored.summary = None;
    restored.domains = vec![];
    restored.skills = vec![];
    restored.scopes = vec![];
    restored.bot_uuid = Some("caller-supplied-id-is-ignored".into());
    restored.webhook_url = Some("https://example.org/new".into());
    let restored = f.management.register_provider_bot(restored).await.unwrap();
    assert!(restored.created);
    assert_eq!(restored.bot_uuid, "stable-provider-bot");
    let new_token = restored.bot_runtime_token.clone().unwrap();
    assert_ne!(new_token, old_token);
    assert!(f.repo.find_bot_by_token(&old_token).await.is_none());
    assert_eq!(f.repo.find_bot_by_token(&new_token).await.as_deref(), Some("stable-provider-bot"));

    let bot = f.registry.get("stable-provider-bot").await.unwrap();
    assert_eq!(bot.capabilities.name.as_deref(), Some("New Bot"));
    assert_eq!(bot.capabilities.summary, None);
    assert!(bot.capabilities.domains.is_empty());
    assert!(bot.capabilities.skills.is_empty());
    assert!(bot.capabilities.scopes.is_empty());
    assert!(bot.capabilities.binding_channels.is_none());
    assert_eq!(bot.created_by.as_deref(), Some("new-owner"));
    let binding = f.core.get_provider_bot_binding_by_ref(&f.provider_id, "managed-ref").await.unwrap().unwrap();
    assert!(!binding.disabled);
    assert_eq!(binding.webhook_url.as_deref(), Some("https://example.org/new"));

    for (user, expected) in [
        ("old-owner", None),
        ("old-manager", None),
        ("new-owner", Some(BotAccessRelation::Owner)),
        ("new-manager", Some(BotAccessRelation::Manager)),
    ] {
        assert_eq!(f.repo.role(&human_actor_id(user), "stable-provider-bot").await.unwrap(), expected);
    }
    let authority = f.repo.authority_state().read().await;
    assert_eq!(authority.ownership_versions.get(&(bcs_config::resolve_env_str(), "stable-provider-bot".into())), Some(&1));
    assert!(!authority.transfers.keys().any(|key| key.1 == "stable-provider-bot"));
    assert!(authority.edges.keys().any(|key| {
        key.1 == "stable-provider-bot" && key.2 == "human_new-owner" && key.3 == MemoryAuthorityEdgeKind::Owner
    }));
}

#[tokio::test]
async fn plugin_reactivation_does_not_restore_old_gateway_binding() {
    use bcs_service_api::{BotRepoPort, DeleteProviderBotCommand, ProviderBotCoreService};

    let f = Fixture::new().await;
    let mut old = f.command(&["old-owner"]);
    old.bot_uuid = Some("stable-plugin-bot".into());
    old.webhook_url = Some("https://example.org/old".into());
    let registered = f.management.register_provider_bot(old).await.unwrap();
    let old_token = registered.bot_runtime_token.unwrap();
    f.management.delete_provider_bot(DeleteProviderBotCommand {
        provider_id: f.provider_id.clone(),
        provider_admin_token: f.token.clone(),
        provider_bot_ref: "managed-ref".into(),
        allow_unbound_owner_suffixed_bot: false,
    }).await.unwrap();

    let mut restored = f.command(&["new-owner"]);
    restored.bot_uuid = Some("ignored-new-id".into());
    restored.connection_mode = ProviderBotConnectionMode::Plugin;
    let restored = f.management.register_provider_bot(restored).await.unwrap();
    assert!(restored.created);
    assert_eq!(restored.bot_uuid, "stable-plugin-bot");
    assert!(restored.bot_runtime_token.is_none());
    assert!(f.core.get_provider_bot_binding_by_ref(&f.provider_id, "managed-ref").await.unwrap().is_none());
    assert!(f.repo.find_bot_by_token(&old_token).await.is_none());
    assert!(f.registry.try_get("stable-plugin-bot").await.unwrap().is_some());
    assert!(f.registry.try_get("ignored-new-id").await.unwrap().is_none());
}
