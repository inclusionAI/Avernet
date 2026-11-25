//! Native MySQL, full migration chain; use only an empty disposable database.
#![allow(clippy::expect_used, clippy::unwrap_used)]
#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;
#[path = "provider_initial_managers_mysql/registration.rs"]
mod registration;
use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement};
use bcs_service_api::{port::repo::{BotRepoPort, BotAuthorityRepoPort}, types::bot_authority::{
    AuthorityActor, AuthorityMutation, OwnershipInitialization, BotAccessRelation,
}};

fn init(managers: Vec<String>, mutation: &str) -> OwnershipInitialization {
    OwnershipInitialization { owner_user_id: "alice".into(), initial_manager_user_ids: managers,
        mutation: AuthorityMutation { mutation_id: mutation.into(),
            actor: AuthorityActor::Provider { provider_id: "provider-a".into() } } }
}

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing at an empty disposable database"]
async fn provider_initial_managers_mysql_full_chain() {
    let database = mysql_chain::MysqlConformanceDb::open_empty().await;
    database.apply_full_migration_chain().await;
    let db = database.plugin.clone();
    let env = bcs_config::resolve_env_str();
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Mysql);
    let authority = bcs_edge_permission_store::DbBotAuthorityStore::mysql(db.clone(), env.clone());
    for (id, kind) in [("batch-bot", "bot"), ("fail-bot", "bot"), ("human_alice", "human")] {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, created_by) VALUES (?, ?, ?, ?, 'alice')",
            vec![id.into(), id.into(), env.clone().into(), kind.into()])).await.unwrap();
    }
    // Native multirow upserts at maximum size, exact role reads and no replay grants.
    let managers = (0..100).map(|i| format!("manager-{i}")).collect::<Vec<_>>();
    repo.initialize_provider_authority("batch-bot", init(managers.clone(), "batch-init")).await.unwrap();
    assert_eq!(authority.role("human_alice", "batch-bot").await.unwrap(), Some(BotAccessRelation::Owner));
    for id in &managers {
        assert_eq!(authority.role(&format!("human_{id}"), "batch-bot").await.unwrap(), Some(BotAccessRelation::Manager));
    }
    db.execute(DbStatement::new("UPDATE edge_grants SET status='revoked' WHERE from_id='human_manager-0'")).await.unwrap();
    repo.initialize_provider_authority("batch-bot", init(managers, "replay")).await.unwrap();
    assert_eq!(authority.role("human_manager-0", "batch-bot").await.unwrap(), None);

    // Native constraint failure after owner/version/audit writes must roll back all.
    db.execute(DbStatement::new("ALTER TABLE edge_grants ADD CONSTRAINT reject_initial_manager CHECK (NOT (to_id='fail-bot' AND grant_kind='manager'))")).await.unwrap();
    assert!(repo.initialize_provider_authority("fail-bot", init(vec!["rollback-manager".into()], "failure-init")).await.is_err());
    let rows = db.query(DbStatement::new("SELECT ownership_version FROM bcs_bots WHERE bot_uuid='fail-bot'")).await.unwrap();
    assert_eq!(rows[0].get_i64("ownership_version").unwrap(), Some(0));
    for sql in ["SELECT COUNT(*) AS n FROM bcs_bots WHERE bot_uuid='human_rollback-manager'",
        "SELECT COUNT(*) AS n FROM edge_grants WHERE to_id='fail-bot'",
        "SELECT COUNT(*) AS n FROM bot_authority_changes WHERE bot_id='fail-bot'"] {
        assert_eq!(db.query(DbStatement::new(sql)).await.unwrap()[0].get_i64("n").unwrap(), Some(0));
    }
    db.execute(DbStatement::new("ALTER TABLE edge_grants DROP CHECK reject_initial_manager")).await.unwrap();
    repo.initialize_provider_authority("fail-bot", init(vec!["rollback-manager".into()], "failure-init")).await.unwrap();
    assert_eq!(authority.role("human_rollback-manager", "fail-bot").await.unwrap(), Some(BotAccessRelation::Manager));
    registration::retained_upgrade(db.as_ref()).await;
    assert_eq!(authority.role("human_alice", "batch-bot").await.unwrap(), Some(BotAccessRelation::Owner));
    assert_eq!(authority.role("human_manager-1", "batch-bot").await.unwrap(), Some(BotAccessRelation::Manager));
    assert_eq!(authority.role("human_manager-0", "batch-bot").await.unwrap(), None);
    // The identity contract is byte-exact, including under default MySQL collation.
    for (index, managers) in [vec!["Carol", "carol"], vec!["Bob"], vec!["Jose", "José"]].into_iter().enumerate() {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_bots (bot_uuid,name,env,actor_kind,created_by) VALUES (?, ?, ?, 'bot', 'alice')",
            vec![format!("case-bot-{index}").into(), format!("Case Bot {index}").into(), env.clone().into()])).await.unwrap();
        if index == 1 {
            db.execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid,name,env,actor_kind) VALUES ('human_bob','Original Bob',?,'human')",
                vec![env.clone().into()])).await.unwrap();
        }
        let bot = format!("case-bot-{index}");
        repo.initialize_provider_authority(&bot, init(managers.iter().map(|id| (*id).into()).collect(), &format!("case-init-{index}")))
            .await.expect("case/accent-distinct managers must not collide");
        for user in managers {
            assert_eq!(authority.role(&format!("human_{user}"), &bot).await.unwrap(), Some(BotAccessRelation::Manager));
        }
    }
    assert_eq!(authority.role("human_bob", "case-bot-1").await.unwrap(), None);
    registration::verify(db.clone(), &env).await;
    registration::distinct_runtime_profiles_and_memberships(db, &env).await;
    database.close().await;
}
