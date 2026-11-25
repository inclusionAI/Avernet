//! Real registry writes, not manual revision changes, fence real invocation.
#[path = "support/agent_invoke_sql_driver.rs"]
mod lifecycle;
#[path = "support/agent_invoke_registry.rs"]
mod registry;

#[tokio::test]
async fn registry_identity_changes_never_revive_an_old_approval() {
    use bcs_db_api::DbSqlFlavor;
    let db = std::sync::Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    registry::run(db, DbSqlFlavor::Sqlite).await;
}

#[tokio::test]
async fn exhausted_revision_rejects_identity_changes_without_partial_state() {
    use bcs_bot_store::{DbProviderStore, PersistentBotRepo};
    use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement, db_get_column};
    use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
    use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
    use bcs_service_api::{ActorStatus, BotCapabilities, BotRepoPort, ProviderBotBindingRepoPort};
    use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
    let db = std::sync::Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    let d = lifecycle::Driver::new_on(
        db.clone(),
        DbSqlFlavor::Sqlite,
        &bcs_config::resolve_env_str(),
    )
    .await;
    d.register("exhausted").await;
    d.sql("UPDATE bcs_bots SET agent_invoke_identity_revision=9223372036854775807 WHERE bot_uuid='exhausted'",vec![]).await;
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Sqlite);
    repo.update_actor_status("exhausted", ActorStatus::Online)
        .await
        .unwrap();
    assert!(
        repo.update_actor_status("exhausted", ActorStatus::Hidden)
            .await
            .is_err()
    );
    assert!(
        repo.register(
            "exhausted".into(),
            BotCapabilities {
                name: Some("must-not-stick".into()),
                agent_code: Some("new-code".into()),
                visibility: "protected".into(),
                ..Default::default()
            }
        )
        .await
        .is_err()
    );
    d.sql("INSERT INTO bcs_providers (provider_id,env,name,config,owners,created_by) VALUES ('exhausted-provider',?,'fixture','{}','[]','fixture')",vec![d.scope.env.as_str().into()]).await;
    let providers = DbProviderStore::sqlite(db.clone());
    assert!(
        providers
            .attach_provider_bot(BotProviderRecord {
                bot_uuid: "exhausted".into(),
                provider_id: "exhausted-provider".into(),
                provider_bot_ref: "ref".into(),
                connection_mode: BotConnectionMode::Gateway,
                webhook_url: None,
                is_deleted: false
            })
            .await
            .is_err()
    );
    assert!(
        providers
            .insert_binding(bcs_service_api::ProviderBotBinding {
                bot_uuid: "exhausted".into(),
                provider_id: "exhausted-provider".into(),
                provider_bot_ref: "ref".into(),
                webhook_url: None,
                disabled: false,
                created_at: 0,
                updated_at: 0,
            })
            .await
            .is_err(),
        "a failed fence must roll back the preceding binding INSERT"
    );
    assert!(!repo.soft_delete("exhausted").await);
    let rows=db.query(DbStatement::new("SELECT name,status,is_deleted,agent_code,provider_id,agent_invoke_identity_revision AS n FROM bcs_bots WHERE bot_uuid='exhausted'")).await.unwrap();
    assert_eq!(
        db_get_column::<u64>(&rows[0], "n").unwrap(),
        i64::MAX as u64
    );
    assert_eq!(
        rows[0].get_string("name").unwrap().as_deref(),
        Some("exhausted")
    );
    assert_eq!(
        rows[0].get_string("status").unwrap().as_deref(),
        Some("online")
    );
    assert_eq!(db_get_column::<u64>(&rows[0], "is_deleted").unwrap(), 0);
    assert!(rows[0].get_string("agent_code").unwrap().is_none());
    assert!(rows[0].get_string("provider_id").unwrap().is_none());
    assert!(
        db.query(DbStatement::new("SELECT * FROM bcs_provider_bot_bindings"))
            .await
            .unwrap()
            .is_empty()
    );
}
