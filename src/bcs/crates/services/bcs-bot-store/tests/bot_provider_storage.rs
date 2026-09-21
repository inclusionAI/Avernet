use std::sync::Arc;

use bcs_bot_store::{DbProviderStore, MemoryBotRepo, MemoryProviderStore};
use bcs_bot_store::provider::MemoryBotProviderStore;
use bcs_db_api::{DbPlugin, DbStatement};
#[path = "common/bot_provider.rs"]
mod database;
use database::sqlite;
use bcs_test_support::contract::bot_provider::bot_provider_repo_port_contract_tests;
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
use bcs_service_api::{BotCapabilities, ProviderBotBindingRepoPort};

fn record(id: &str, mode: BotConnectionMode) -> BotProviderRecord {
    BotProviderRecord {
        bot_uuid: id.into(), provider_id: "provider-a".into(), provider_bot_ref: id.into(),
        connection_mode: mode, webhook_url: None, is_deleted: false,
        registered_at: 1000, updated_at: 1000,
    }
}

fn caps() -> BotCapabilities {
    BotCapabilities { name: Some("Provider Bot".into()), visibility: "protected".into(), ..Default::default() }
}

#[tokio::test]
async fn backfill_rejects_missing_providers_and_partial_membership() {
    let db = sqlite().await;
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::new("CREATE TABLE bcs_provider_registrations (env TEXT, provider_id TEXT, provider_bot_ref TEXT, bot_uuid TEXT, record_json TEXT, completed INTEGER)")).await.unwrap();
    db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, env, name) VALUES ('orphan', ?, 'Orphan')", vec![env.as_str().into()])).await.unwrap();
    db.execute(DbStatement::with_params("INSERT INTO bcs_provider_bot_bindings (bot_uuid, env, provider_id, provider_bot_ref) VALUES ('orphan', ?, 'missing-provider', 'ref')", vec![env.as_str().into()])).await.unwrap();
    db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, env, name, provider_bot_ref) VALUES ('partial', ?, 'Partial', 'dangling-ref')", vec![env.as_str().into()])).await.unwrap();
    let store = DbProviderStore::sqlite(db);
    let report = store.backfill_provider_bots(false, false).await.unwrap();
    assert!(report.issues.iter().any(|issue| issue.contains("orphan") && issue.contains("Provider")));
    assert!(report.issues.iter().any(|issue| issue.contains("partial") && issue.contains("metadata")));
    assert!(store.backfill_provider_bots(true, true).await.is_err());
}

#[tokio::test]
async fn backfill_recovers_completed_upstream_journal_without_consuming_it() {
    let db = sqlite().await;
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::new("CREATE TABLE bcs_provider_registrations (env TEXT, provider_id TEXT, provider_bot_ref TEXT, bot_uuid TEXT, record_json TEXT, completed INTEGER)")).await.unwrap();
    db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, env, name, created_by, session_token) VALUES ('old-upstream', ?, 'Old', 'alice', 'test-old-upstream-runtime')", vec![env.as_str().into()])).await.unwrap();
    let payload = serde_json::json!({"provider_id": "provider-a", "provider_bot_ref": "old-ref", "bot_uuid": "old-upstream", "owner": "alice", "bot_token": "test-old-upstream-runtime", "mode": "upstream", "completed": true}).to_string();
    db.execute(DbStatement::with_params("INSERT INTO bcs_provider_registrations (env, provider_id, provider_bot_ref, bot_uuid, record_json, completed) VALUES (?, 'provider-a', 'old-ref', 'old-upstream', ?, 1)", vec![env.as_str().into(), payload.into()])).await.unwrap();
    let store = DbProviderStore::sqlite(db.clone());
    let report = store.backfill_provider_bots(true, true).await.unwrap();
    assert_eq!(report.memberships_to_backfill, 1);
    assert!(report.issues.is_empty());
    let record = store.get_provider_bot("old-upstream").await.unwrap().unwrap();
    assert_eq!(record.connection_mode, BotConnectionMode::Upstream);
    assert_eq!(record.provider_bot_ref, "old-ref");
    assert!(store.get_binding_by_bot_uuid("old-upstream").await.unwrap().is_none());
    assert_eq!(db.query(DbStatement::new("SELECT bot_uuid FROM bcs_provider_registrations")).await.unwrap().len(), 1);
    assert_eq!(store.backfill_provider_bots(false, false).await.unwrap().memberships_to_backfill, 0);
}





#[tokio::test]
async fn sqlite_bot_provider_contract() {
    let store = DbProviderStore::sqlite(sqlite().await);
    bot_provider_repo_port_contract_tests(&store, &store).await;
}

#[tokio::test]
async fn memory_bot_provider_contract() {
    let temp = tempfile::tempdir().unwrap();
    let bots = Arc::new(MemoryBotRepo::with_base_dir(temp.path().into()));
    let bindings = Arc::new(MemoryProviderStore::new());
    let store = MemoryBotProviderStore::new(bots, bindings.clone());
    bot_provider_repo_port_contract_tests(&store, bindings.as_ref()).await;
}

#[tokio::test]
async fn gateway_binding_conflict_rolls_back_new_bot() {
    let db = sqlite().await;
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::with_params("INSERT INTO bcs_provider_bot_bindings (bot_uuid, env, provider_id, provider_bot_ref) VALUES ('already-bound', ?, 'provider-a', 'conflict')", vec![env.into()])).await.unwrap();
    let store = DbProviderStore::sqlite(db.clone());
    assert!(store.create_provider_bot(record("conflict", BotConnectionMode::Gateway), caps(), "owner-a", "test-rollback-runtime").await.is_err());
    assert!(db.query(DbStatement::new("SELECT bot_uuid FROM bcs_bots WHERE bot_uuid = 'conflict'")).await.unwrap().is_empty());
}

#[tokio::test]
async fn attach_existing_bot_preserves_credential_and_rejects_provider_takeover() {
    let db = sqlite().await;
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, env, name, session_token) VALUES ('existing', ?, 'Existing', 'test-original-runtime')", vec![env.into()])).await.unwrap();
    let store = DbProviderStore::sqlite(db.clone());
    let upstream = record("existing", BotConnectionMode::Upstream);
    store.attach_provider_bot(upstream.clone()).await.unwrap();
    assert_eq!(store.get_provider_bot_by_ref("provider-a", "existing").await.unwrap(), Some(upstream));
    assert!(store.get_binding_by_bot_uuid("existing").await.unwrap().is_none());
    store.attach_provider_bot(record("existing", BotConnectionMode::Gateway)).await.unwrap();
    assert!(store.get_binding_by_bot_uuid("existing").await.unwrap().is_some());
    let mut takeover = record("existing", BotConnectionMode::Gateway);
    takeover.provider_id = "provider-b".into();
    assert!(store.attach_provider_bot(takeover).await.is_err());
    assert!(store.attach_provider_bot(record("existing", BotConnectionMode::Upstream)).await.is_err());
    let rows = db.query(DbStatement::new("SELECT session_token FROM bcs_bots WHERE bot_uuid = 'existing'")).await.unwrap();
    assert_eq!(rows[0].get_string("session_token").unwrap().as_deref(), Some("test-original-runtime"));
    assert_eq!(store.list_provider_bot_metadata(Some("provider-a")).await.unwrap().len(), 1);
}

#[tokio::test]
async fn webhook_missing_projection_rolls_back_bot_and_foreign_provider_cannot_delete() {
    let db = sqlite().await;
    let store = DbProviderStore::sqlite(db.clone());
    let original = record("gateway", BotConnectionMode::Gateway);
    store.create_provider_bot(original.clone(), caps(), "owner-a", "test-update-runtime").await.unwrap();
    assert!(store.delete_provider_bot("provider-b", "gateway", 2000).await.is_err());
    db.execute(DbStatement::new("DELETE FROM bcs_provider_bot_bindings WHERE bot_uuid = 'gateway'")).await.unwrap();
    assert!(store.update_provider_webhook("provider-a", "gateway", Some("https://example.org/new".into()), 2000).await.is_err());
    assert_eq!(store.get_provider_bot("gateway").await.unwrap(), Some(original));
}

#[tokio::test]
async fn read_source_is_explicit_and_does_not_change_gateway_writes() {
    use bcs_service_api::bot_provider::DownlinkDetectionSource;
    use bcs_bot_store::provider::ProviderBindingProjection;
    let db = sqlite().await;
    let store = Arc::new(DbProviderStore::sqlite(db.clone()));
    let legacy = ProviderBindingProjection::new(store.clone(), store.clone(), DownlinkDetectionSource::Binding);
    let bots = ProviderBindingProjection::new(store.clone(), store.clone(), DownlinkDetectionSource::BotConnectionMode);
    store.create_provider_bot(record("upstream", BotConnectionMode::Upstream), caps(), "owner-a", "test-source-upstream").await.unwrap();
    store.create_provider_bot(record("gateway", BotConnectionMode::Gateway), caps(), "owner-a", "test-source-gateway").await.unwrap();
    for reader in [&legacy, &bots] {
        assert!(reader.get_binding_by_bot_uuid("upstream").await.unwrap().is_none());
        assert!(reader.get_binding_by_bot_uuid("gateway").await.unwrap().is_some());
        reader.update_binding_webhook_url("provider-a", "gateway", Some("https://example.org/override"), 2000).await.unwrap();
    }
    assert_eq!(store.get_provider_bot("gateway").await.unwrap().unwrap().webhook_url, store.get_binding_by_bot_uuid("gateway").await.unwrap().unwrap().webhook_url);
    // A missing Bot migration state is not silently interpreted as upstream.
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, env, name) VALUES ('unmigrated', ?, 'Old')", vec![env.into()])).await.unwrap();
    assert!(legacy.get_binding_by_bot_uuid("unmigrated").await.unwrap().is_none());
    assert!(bots.get_binding_by_bot_uuid("unmigrated").await.is_err());
}

#[tokio::test]
async fn audited_backfill_is_explicit_repeatable_and_blocks_state_mismatches() {
    let db = sqlite().await;
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::new("CREATE TABLE bcs_provider_registrations (env TEXT, provider_id TEXT, provider_bot_ref TEXT, bot_uuid TEXT, record_json TEXT, completed INTEGER)")).await.unwrap();
    for id in ["old-gateway", "ordinary"] {
        db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, env, name, session_token) VALUES (?, ?, ?, ?)", vec![id.into(), env.as_str().into(), id.into(), format!("test-{id}-runtime").into()])).await.unwrap();
    }
    db.execute(DbStatement::with_params("INSERT INTO bcs_provider_bot_bindings (bot_uuid, env, provider_id, provider_bot_ref, disabled) VALUES ('old-gateway', ?, 'provider-a', 'old-gateway', 1)", vec![env.as_str().into()])).await.unwrap();
    let store = DbProviderStore::sqlite(db.clone());
    let report = store.backfill_provider_bots(false, false).await.unwrap();
    assert!(!report.issues.is_empty());
    assert!(store.backfill_provider_bots(true, true).await.is_err());
    db.execute(DbStatement::new("UPDATE bcs_provider_bot_bindings SET disabled = 0")).await.unwrap();
    let report = store.backfill_provider_bots(false, false).await.unwrap();
    assert!(report.issues.is_empty());
    assert_eq!(report.memberships_to_backfill, 1);
    assert_eq!(report.ordinary_modes_to_backfill, 1);
    assert!(store.get_provider_bot("old-gateway").await.unwrap().is_none());
    assert!(store.backfill_provider_bots(true, false).await.is_err());
    store.backfill_provider_bots(true, true).await.unwrap();
    assert_eq!(store.get_provider_bot("old-gateway").await.unwrap().unwrap().connection_mode, BotConnectionMode::Gateway);
    assert_eq!(store.get_connection_mode("ordinary").await.unwrap(), Some(BotConnectionMode::Upstream));
    let report = store.backfill_provider_bots(false, false).await.unwrap();
    assert!(report.issues.is_empty());
    assert_eq!(report.memberships_to_backfill + report.ordinary_modes_to_backfill, 0);
    let rows = db.query(DbStatement::new("SELECT session_token FROM bcs_bots WHERE bot_uuid = 'old-gateway'")).await.unwrap();
    assert_eq!(rows[0].get_string("session_token").unwrap().as_deref(), Some("test-old-gateway-runtime"));
}
