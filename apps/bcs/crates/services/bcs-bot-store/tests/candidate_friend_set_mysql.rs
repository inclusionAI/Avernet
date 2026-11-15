//! Candidate contract on a real migrated MySQL schema containing unrelated Bots.
#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;
#[path = "support/candidate_friend_set_contract.rs"]
mod candidates;

use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::DbSqlFlavor;
use bcs_service_api::{BotCapabilities, BotRepoPort};

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing at an empty disposable database"]
async fn candidate_friend_set_with_unrelated_rows_on_full_mysql_chain() {
    let database = mysql_chain::MysqlConformanceDb::open_empty().await;
    database.apply_full_migration_chain().await;
    let repo = PersistentBotRepo::with_sql_flavor(database.plugin.clone(), DbSqlFlavor::Mysql);
    repo.create_registration_if_absent(
        "other-contract-public".into(),
        BotCapabilities {
            name: Some("Other contract public Bot".into()),
            visibility: "public".into(),
            ..Default::default()
        },
        "other-owner",
        "other-contract-public-token",
    ).await.expect("seed unrelated public Bot");
    candidates::verify(&repo, &repo, &bcs_config::resolve_env_str(), "mysql-candidates", 256).await;
    database.close().await;
}
