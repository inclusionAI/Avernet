//! Real MySQL + complete frozen migration chain, on a disposable empty schema.
#[path="../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;
#[path="support/a2a_mysql_cases.rs"]
mod cases;

#[tokio::test]
#[ignore="requires an isolated empty BCS_TEST_MYSQL_URL schema"]
async fn full_mysql_chain_and_a2a_card_context_conformance() {
    let database=mysql_chain::MysqlConformanceDb::open_empty().await;
    let applied=database.apply_full_migration_chain().await;
    assert!(applied.contains(&39),"complete chain includes A2A audit schema");
    cases::verify(database.plugin.clone()).await;
    println!("A2A MySQL: complete frozen migrations={}",applied.len());
    database.close().await;
}
