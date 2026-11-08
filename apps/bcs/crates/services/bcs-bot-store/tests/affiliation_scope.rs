use bcs_bot_store::provider::MemoryBotProviderStore;
use bcs_bot_store::{DbProviderStore, MemoryBotRepo, MemoryProviderStore};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_group_store::{MemoryGroupRepo, MySqlGroupStore};
use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
use bcs_service_api::port::repo::{GroupRepoPort, bot_provider::BotProviderRepoPort};
use bcs_service_api::{
    BotCapabilities, BotRepoPort, Group, Participant, ParticipantRole, ProviderBotBindingRepoPort,
    ProviderRecord, ProviderRepoPort, ServiceError,
};
use std::sync::Arc;
#[path = "common/bot_provider.rs"]
mod database;

fn record(mode: BotConnectionMode) -> BotProviderRecord {
    BotProviderRecord {
        bot_uuid: "existing".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "existing-ref".into(),
        connection_mode: mode,
        webhook_url: None,
        is_deleted: false,
    }
}

#[tokio::test]
async fn sql_public_membership_rejects_attachment_and_rolls_back_both_modes() {
    for mode in [BotConnectionMode::Plugin, BotConnectionMode::Gateway] {
        let db = database::sqlite().await;
        let env = bcs_config::resolve_env_str();
        db.execute(DbStatement::with_params(
            "UPDATE bcs_providers SET config = ?",
            vec![r#"{"collaboration_scope":"same_owner"}"#.into()],
        ))
        .await
        .unwrap();
        db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, env, name, created_by, session_token, connection_mode) VALUES ('existing', ?, 'Original', 'alice', 'fixture-token', 'plugin')", vec![env.clone().into()])).await.unwrap();
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_groups VALUES ('public', ?, 'public', 'active')",
            vec![env.clone().into()],
        ))
        .await
        .unwrap();
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_group_participants VALUES ('public', ?, 'existing')",
            vec![env.clone().into()],
        ))
        .await
        .unwrap();
        let store = DbProviderStore::sqlite(db.clone());
        assert!(matches!(
            store
                .validate_provider_affiliation("provider-a", "existing")
                .await,
            Err(ServiceError::Conflict(_))
        ));
        assert!(matches!(
            store.attach_provider_bot(record(mode)).await,
            Err(ServiceError::Conflict(_))
        ));
        assert!(store.get_provider_bot("existing").await.unwrap().is_none());
        assert!(
            store
                .get_binding_by_bot_uuid("existing")
                .await
                .unwrap()
                .is_none()
        );
        let rows = db
            .query(DbStatement::new(
                "SELECT name, created_by, session_token FROM bcs_bots WHERE bot_uuid = 'existing'",
            ))
            .await
            .unwrap();
        assert_eq!(
            rows[0].get_string("name").unwrap().as_deref(),
            Some("Original")
        );
        assert_eq!(
            rows[0].get_string("created_by").unwrap().as_deref(),
            Some("alice")
        );
        assert_eq!(
            rows[0].get_string("session_token").unwrap().as_deref(),
            Some("fixture-token")
        );
        db.execute(DbStatement::new(
            "UPDATE bcs_groups SET visibility = 'private'",
        ))
        .await
        .unwrap();
        store.attach_provider_bot(record(mode)).await.unwrap();
        assert_eq!(
            store.get_provider_bot("existing").await.unwrap(),
            Some(record(mode))
        );
        assert_eq!(
            store
                .get_binding_by_bot_uuid("existing")
                .await
                .unwrap()
                .is_some(),
            mode == BotConnectionMode::Gateway
        );
    }
}

#[tokio::test]
async fn standard_deleted_groups_and_other_environments_preserve_attachment() {
    for (scope, status, group_env) in [
        ("standard", "active", bcs_config::resolve_env_str()),
        ("same_owner", "deleted", bcs_config::resolve_env_str()),
        ("same_owner", "active", "other-env".into()),
    ] {
        let db = database::sqlite().await;
        let env = bcs_config::resolve_env_str();
        db.execute(DbStatement::with_params(
            "UPDATE bcs_providers SET config = ?",
            vec![format!("{{\"collaboration_scope\":\"{scope}\"}}").into()],
        ))
        .await
        .unwrap();
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_bots (bot_uuid, env, name) VALUES ('existing', ?, 'Original')",
            vec![env.into()],
        ))
        .await
        .unwrap();
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_groups VALUES ('public', ?, 'public', ?)",
            vec![group_env.clone().into(), status.into()],
        ))
        .await
        .unwrap();
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_group_participants VALUES ('public', ?, 'existing')",
            vec![group_env.into()],
        ))
        .await
        .unwrap();
        DbProviderStore::sqlite(db)
            .attach_provider_bot(record(BotConnectionMode::Plugin))
            .await
            .unwrap();
    }
}

#[tokio::test]
async fn missing_or_invalid_policy_and_failed_group_reads_cannot_fall_back_to_standard() {
    let db = database::sqlite().await;
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, env, name) VALUES ('existing', ?, 'Original')",
        vec![env.into()],
    ))
    .await
    .unwrap();
    for config in [
        "[]",
        "{\"collaboration_scope\":null}",
        "{\"collaboration_scope\":\"invalid\"}",
        "{\"collaboration_scope\":\"STANDARD\"}",
        "{\"collaboration_scope\":\"SAME_OWNER\"}",
    ] {
        db.execute(DbStatement::with_params(
            "UPDATE bcs_providers SET config = ?",
            vec![config.into()],
        ))
        .await
        .unwrap();
        let store = DbProviderStore::sqlite(db.clone());
        assert!(
            store
                .validate_provider_affiliation("provider-a", "existing")
                .await
                .is_err()
        );
        assert!(
            store
                .attach_provider_bot(record(BotConnectionMode::Plugin))
                .await
                .is_err()
        );
        assert!(store.get_provider_bot("existing").await.unwrap().is_none());
    }
    db.execute(DbStatement::new(
        "UPDATE bcs_providers SET config = '{\"collaboration_scope\":\"same_owner\"}'",
    ))
    .await
    .unwrap();
    db.execute(DbStatement::new("DROP TABLE bcs_groups"))
        .await
        .unwrap();
    assert!(
        DbProviderStore::sqlite(db.clone())
            .attach_provider_bot(record(BotConnectionMode::Plugin))
            .await
            .is_err()
    );
    db.execute(DbStatement::new("DELETE FROM bcs_providers"))
        .await
        .unwrap();
    assert!(
        DbProviderStore::sqlite(db)
            .validate_provider_affiliation("provider-a", "existing")
            .await
            .is_err()
    );
}

#[tokio::test]
async fn memory_attachment_uses_the_same_group_gate_and_current_membership() {
    let temp = tempfile::tempdir().unwrap();
    let bots = Arc::new(MemoryBotRepo::with_base_dir(temp.path().into()));
    bots.register_with_owner_and_token(
        "existing".into(),
        BotCapabilities::default(),
        "alice",
        "fixture-token",
    )
    .await
    .unwrap();
    let providers = Arc::new(MemoryProviderStore::new());
    providers
        .insert_provider(ProviderRecord {
            provider_id: "provider-a".into(),
            name: "Local".into(),
            slug: None,
            config: r#"{"collaboration_scope":"same_owner"}"#.into(),
            created_by: "alice".into(),
            owners: "[]".into(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        })
        .await
        .unwrap();
    let metadata: Arc<dyn BotProviderRepoPort> =
        Arc::new(MemoryBotProviderStore::new(bots.clone(), providers.clone()));
    assert!(
        metadata
            .attach_provider_bot(record(BotConnectionMode::Plugin))
            .await
            .is_err(),
        "missing group projection fails closed"
    );
    let groups = Arc::new(MemoryGroupRepo::new());
    providers.bind_group_scope_source(groups.clone(), Arc::downgrade(&metadata));
    let mut group = Group::new(
        "public",
        "existing",
        vec![Participant::bot("existing", ParticipantRole::Driver)],
    );
    group.visibility = "public".into();
    groups.upsert(group).await.unwrap();
    assert!(matches!(
        metadata
            .attach_provider_bot(record(BotConnectionMode::Plugin))
            .await,
        Err(ServiceError::Conflict(_))
    ));
    assert!(
        metadata
            .get_provider_bot("existing")
            .await
            .unwrap()
            .is_none()
    );
    groups.update_visibility("public", "private").await.unwrap();
    let gate = providers.policy_gate();
    let reader = gate.read().await;
    let writer = metadata.clone();
    let task = tokio::spawn(async move {
        writer
            .attach_provider_bot(record(BotConnectionMode::Plugin))
            .await
    });
    tokio::task::yield_now().await;
    assert!(!task.is_finished());
    drop(reader);
    task.await.unwrap().unwrap();
    assert_eq!(
        metadata.get_provider_bot("existing").await.unwrap(),
        Some(record(BotConnectionMode::Plugin))
    );
    assert_eq!(
        bots.load_token("existing").await.as_deref(),
        Some("fixture-token")
    );
}

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn independent_sqlite_connections_cannot_commit_public_group_and_restricted_affiliation() {
    use bcs_db_local::LocalSqliteDbPlugin;
    for _ in 0..4 {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("race.sqlite");
        let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        bootstrap_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        let env = bcs_config::resolve_env_str();
        db.execute(DbStatement::with_params("INSERT INTO bcs_providers (provider_id, env, name, config, created_by, owners) VALUES ('provider-a', ?, 'Local', '{\"collaboration_scope\":\"same_owner\"}', 'alice', '[]')", vec![env.clone().into()])).await.unwrap();
        db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, env, name, created_by) VALUES ('existing', ?, 'Original', 'alice')", vec![env.clone().into()])).await.unwrap();
        let other: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        let barrier = Arc::new(tokio::sync::Barrier::new(2));
        let writer = DbProviderStore::sqlite(db.clone());
        let groups = MySqlGroupStore::sqlite(other, env.clone()).with_provider_scope_guard();
        let ready = barrier.clone();
        let attach = tokio::spawn(async move {
            ready.wait().await;
            writer
                .attach_provider_bot(record(BotConnectionMode::Plugin))
                .await
        });
        let public = tokio::spawn(async move {
            barrier.wait().await;
            let mut group = Group::new(
                "raced",
                "existing",
                vec![Participant::bot("existing", ParticipantRole::Driver)],
            );
            group.visibility = "public".into();
            groups.upsert(group).await
        });
        let (attached, created) = (attach.await.unwrap(), public.await.unwrap());
        assert!(!(attached.is_ok() && created.is_ok()));
        let rows = db.query(DbStatement::with_params("SELECT COUNT(*) AS exposed FROM bcs_bots b JOIN bcs_group_participants gp ON gp.bot_uuid = b.bot_uuid AND gp.env = b.env JOIN bcs_groups g ON g.group_id = gp.group_id AND g.env = gp.env WHERE b.env = ? AND b.provider_id = 'provider-a' AND g.visibility = 'public'", vec![env.into()])).await.unwrap();
        assert_eq!(
            bcs_db_api::db_get_column::<i64>(&rows[0], "exposed").unwrap(),
            0
        );
    }
}
