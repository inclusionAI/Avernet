//! Real SQLite transaction rollback/replay for Provider authority snapshots.
#![allow(clippy::expect_used, clippy::unwrap_used)]
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_service_api::{port::repo::{BotRepoPort, BotAuthorityRepoPort}, types::bot_authority::{
    AuthorityActor, AuthorityMutation, OwnershipInitialization, BotAccessRelation,
}};
use bcs_test_support::contract::bot_registration_chain::{registration_chain, RegistrationChain};

fn initialization(managers: &[&str]) -> OwnershipInitialization {
    OwnershipInitialization { owner_user_id: "alice".into(),
        initial_manager_user_ids: managers.iter().map(|s| (*s).into()).collect(),
        mutation: AuthorityMutation { mutation_id: "provider-init".into(),
            actor: AuthorityActor::Provider { provider_id: "provider-a".into() } } }
}
async fn fixture() -> RegistrationChain {
    let chain = registration_chain().await;
    for (id, kind) in [("bot", "bot"), ("human_alice", "human")] {
        chain.db.execute(DbStatement::with_params(
            "INSERT INTO bcs_bots (bot_uuid, env, actor_kind, created_by) VALUES (?, ?, ?, 'alice')",
            vec![id.into(), bcs_config::resolve_env_str().into(), kind.into()])).await.unwrap();
    }
    chain
}
async fn count(chain: &RegistrationChain, sql: &str) -> i64 {
    chain.db.query(DbStatement::new(sql)).await.unwrap()[0].get_i64("n").unwrap().unwrap()
}

#[tokio::test]
async fn sqlite_initializes_managers_and_preserves_revocations_on_replay() {
    let c = fixture().await;
    c.bot_repo.initialize_provider_authority("bot", initialization(&["bob", "carol"])).await.unwrap();
    for (id, role) in [("human_alice", BotAccessRelation::Owner), ("human_bob", BotAccessRelation::Manager), ("human_carol", BotAccessRelation::Manager)] {
        assert_eq!(c.authority_store.role(id, "bot").await.unwrap(), Some(role));
    }
    c.db.execute(DbStatement::new("UPDATE edge_grants SET status='revoked' WHERE from_id='human_bob'")).await.unwrap();
    c.bot_repo.initialize_provider_authority("bot", initialization(&["bob", "dave"])).await.unwrap();
    assert_eq!(c.authority_store.role("human_bob", "bot").await.unwrap(), None);
    assert_eq!(count(&c, "SELECT COUNT(*) AS n FROM bcs_bots WHERE bot_uuid='human_dave'").await, 0);
    assert_eq!(count(&c, "SELECT COUNT(*) AS n FROM bot_authority_changes").await, 1);
    let row = c.db.query(DbStatement::new("SELECT actor_kind, after_state FROM bot_authority_changes")).await.unwrap();
    assert_eq!(row[0].get_string("actor_kind").unwrap().as_deref(), Some("provider"));
    let snapshot: serde_json::Value = serde_json::from_str(&row[0].get_string("after_state").unwrap().unwrap()).unwrap();
    assert_eq!(snapshot["managers"], serde_json::json!(["human_bob", "human_carol"]));
}

#[tokio::test]
async fn sqlite_mid_transaction_failures_roll_back_owner_version_managers_and_audit() {
    for trigger in [
        "CREATE TRIGGER reject_write BEFORE INSERT ON bcs_bots WHEN NEW.bot_uuid='human_bob' BEGIN SELECT RAISE(ABORT, 'human failure'); END",
        "CREATE TRIGGER reject_write BEFORE INSERT ON edge_grants WHEN NEW.grant_kind='manager' BEGIN SELECT RAISE(ABORT, 'manager failure'); END",
        "CREATE TRIGGER reject_write BEFORE INSERT ON bot_authority_changes BEGIN SELECT RAISE(ABORT, 'audit failure'); END",
    ] {
        let c = fixture().await;
        c.db.execute(DbStatement::new(trigger)).await.unwrap();
        assert!(c.bot_repo.initialize_provider_authority("bot", initialization(&["carol", "bob"])).await.is_err());
        assert_eq!(count(&c, "SELECT ownership_version AS n FROM bcs_bots WHERE bot_uuid='bot'").await, 0);
        for sql in ["SELECT COUNT(*) AS n FROM edge_grants", "SELECT COUNT(*) AS n FROM bot_authority_changes",
            "SELECT COUNT(*) AS n FROM bcs_bots WHERE bot_uuid IN ('human_bob','human_carol')"] {
            assert_eq!(count(&c, sql).await, 0);
        }
        c.db.execute(DbStatement::new("DROP TRIGGER reject_write")).await.unwrap();
        c.bot_repo.initialize_provider_authority("bot", initialization(&["carol", "bob"])).await.unwrap();
        assert_eq!(c.authority_store.role("human_bob", "bot").await.unwrap(), Some(BotAccessRelation::Manager));
    }
}

#[tokio::test]
async fn sqlite_refuses_tombstoned_manager_without_resurrection() {
    let c = fixture().await;
    c.db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid,env,actor_kind,is_deleted) VALUES ('human_bob',?,'human',1)",
        vec![bcs_config::resolve_env_str().into()])).await.unwrap();
    assert!(c.bot_repo.initialize_provider_authority("bot", initialization(&["carol", "bob"])).await.is_err());
    assert_eq!(count(&c, "SELECT COUNT(*) AS n FROM edge_grants").await, 0);
    assert_eq!(count(&c, "SELECT COUNT(*) AS n FROM bcs_bots WHERE bot_uuid='human_carol'").await, 0);
}

#[tokio::test]
async fn sqlite_accepts_maximum_snapshot_but_refuses_non_provider_principal() {
    let c = fixture().await;
    let mut init = initialization(&[]);
    init.initial_manager_user_ids = (0..100).map(|i| format!("manager-{i}")).collect();
    c.bot_repo.initialize_provider_authority("bot", init).await.unwrap();
    assert_eq!(count(&c, "SELECT COUNT(*) AS n FROM edge_grants WHERE grant_kind='manager'").await, 100);
    let c = fixture().await;
    let mut init = initialization(&["bob"]);
    init.mutation.actor = AuthorityActor::Human { user_id: "alice".into() };
    assert!(c.bot_repo.initialize_provider_authority("bot", init).await.is_err());
    assert_eq!(count(&c, "SELECT COUNT(*) AS n FROM edge_grants").await, 0);
}

#[tokio::test]
async fn sqlite_case_and_accent_distinct_identities_match_mysql_and_memory() {
    let c = fixture().await;
    c.db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid,env,actor_kind,name) VALUES ('human_bob',?,'human','Original Bob')",
        vec![bcs_config::resolve_env_str().into()])).await.unwrap();
    c.bot_repo.initialize_provider_authority("bot", initialization(&["Carol", "carol", "Bob", "Jose", "José"])).await.unwrap();
    for user in ["Carol", "carol", "Bob", "Jose", "José"] {
        assert_eq!(c.authority_store.role(&format!("human_{user}"), "bot").await.unwrap(), Some(BotAccessRelation::Manager));
    }
    assert_eq!(c.authority_store.role("human_bob", "bot").await.unwrap(), None);
}
