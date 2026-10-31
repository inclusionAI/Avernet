use std::sync::Arc;
use bcs_bot_store::a2a::DbA2aBotStore;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::a2a_bot::{
    A2aBotRecord, A2aBotRepoPort, };
use serde_json::json;

async fn database() -> Arc<LocalSqliteDbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new("CREATE TABLE bcs_bots (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, provider_id TEXT, provider_bot_ref TEXT, connection_mode TEXT, is_deleted INTEGER NOT NULL DEFAULT 0, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (bot_uuid, env))")).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid,env,provider_id,provider_bot_ref,connection_mode,is_deleted) VALUES ('bot', 'test', 'provider', 'remote', 'gateway', 0), ('bot', 'other', 'provider', 'remote', 'gateway', 0), ('plugin', 'test', 'provider', 'plugin', 'plugin', 0)")).await.unwrap();
    for sql in include_str!("../../../../migrations/sqlite/039_bot_a2a.sql")
        .split(';').map(str::trim).filter(|s| !s.is_empty()) {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    db
}

fn record() -> A2aBotRecord {
    A2aBotRecord {
        bot_uuid:"bot".into(), provider_id:"provider".into(), provider_bot_ref:"remote".into(),
        agent_card_url:Some("https://example.org/a2a/remote/card".into()),
        agent_card:json!({"name":"Agent", "supportedInterfaces":[{"url":"https://example.org/a2a/remote","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}], "skills":[{"id":"lookup", "name":"查询", "description":"完整说明", "futureField":{"nested":true}}]}), revision:1,}
}

#[tokio::test]
async fn migration_preserves_legacy_rows_and_card_round_trip_is_lossless() {
    let db = database().await;
    let store = DbA2aBotStore::sqlite(db.clone(), "test".into());
    assert_eq!(store.get("bot").await.unwrap(), None);
    assert!(store.refreshable(None,16).await.unwrap().is_empty());
    let expected = record();
    assert!(store.compare_and_set(&expected, 0).await.unwrap());
    assert_eq!(store.get("bot").await.unwrap(), Some(expected));
    assert_eq!(DbA2aBotStore::sqlite(db, "other".into()).get("bot").await.unwrap(), None);
}

#[tokio::test]
async fn stale_refresh_cannot_overwrite_card_or_management_metadata() {
    let db = database().await;
    let store = DbA2aBotStore::sqlite(db, "test".into());
    let mut current = record();
    assert!(store.compare_and_set(&current, 0).await.unwrap());
    let stale = current.clone();
    current.revision = 2;
    current.agent_card["name"] = json!("New Agent");
    assert!(store.compare_and_set(&current, 1).await.unwrap());
    assert!(!store.compare_and_set(&stale, 0).await.unwrap());
    assert_eq!(store.get("bot").await.unwrap(), Some(current));
}

#[tokio::test]
async fn public_card_batch_is_lossless_fresh_bounded_and_scoped() {
    let db=database().await;
    let store=DbA2aBotStore::sqlite(db.clone(),"test".into());
    let mut value=record();
    assert!(store.compare_and_set(&value,0).await.unwrap());
    let ids=vec!["bot".into(),"plugin".into(),"absent".into(),"bot".into()];
    let cards=store.cards_for_bots(&ids).await.unwrap();
    assert_eq!(cards.len(),1);assert_eq!(cards["bot"],value.agent_card);
    let unmatched=store.discovery_cards(&ids,Some("absent-description"),&[]).await.unwrap();
    assert_eq!(unmatched.len(),1);assert_eq!(unmatched["bot"],None);
    let matches=store.discovery_cards(&ids,None,&["LOOKUP".into()]).await.unwrap();
    assert_eq!(matches["bot"],Some(value.agent_card.clone()));
    assert!(DbA2aBotStore::sqlite(db.clone(),"other".into()).cards_for_bots(&ids).await.unwrap().is_empty());
    value.revision=2;value.agent_card["skills"][0]["description"]=json!("fresh Card-only description");
    assert!(store.compare_and_set(&value,1).await.unwrap());
    assert_eq!(store.cards_for_bots(&ids).await.unwrap()["bot"],value.agent_card);
    assert!(store.cards_for_bots(&vec!["bot".into();257]).await.is_err());
    db.execute(DbStatement::new("UPDATE bcs_bots SET is_deleted=1 WHERE env='test'")).await.unwrap();
    assert!(store.cards_for_bots(&ids).await.unwrap().is_empty());
}

#[tokio::test]
async fn updates_are_fenced_by_provider_reference_mode_and_deletion() {
    let db = database().await;
    let store = DbA2aBotStore::sqlite(db.clone(), "test".into());
    let mut wrong = record();
    wrong.provider_id = "other-provider".into();
    assert!(!store.compare_and_set(&wrong, 0).await.unwrap());
    wrong = record();
    wrong.provider_bot_ref = "other-remote".into();
    assert!(!store.compare_and_set(&wrong, 0).await.unwrap());
    wrong = record();
    wrong.bot_uuid = "plugin".into();
    wrong.provider_bot_ref = "plugin".into();
    assert!(!store.compare_and_set(&wrong, 0).await.unwrap());
    wrong = record();
    wrong.revision = 9;
    assert!(store.compare_and_set(&wrong, 0).await.is_err());
    assert!(store.compare_and_set(&record(), 0).await.unwrap());
    db.execute(DbStatement::new("UPDATE bcs_bots SET is_deleted=1 WHERE env='test'")).await.unwrap();
    assert_eq!(store.get("bot").await.unwrap(), None);
    assert!(store.refreshable(None,16).await.unwrap().is_empty());
    let mut update = record(); update.revision = 2;
    assert!(!store.compare_and_set(&update, 1).await.unwrap());
}

#[tokio::test]
async fn refresh_scan_is_keyset_bounded_and_excludes_inline_cards() {
    let db=database().await;
    for i in 0..105 {
        db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid,env,provider_id,provider_bot_ref,connection_mode,agent_card,agent_card_url,agent_card_revision) VALUES (?,'test','provider',?,'gateway','{}',?,1)",
            vec![format!("source-{i:03}").into(),format!("ref-{i}").into(),if i==50 {bcs_db_api::DbValue::Null} else {"https://example.org/card".into()}])).await.unwrap();
    }
    let store=DbA2aBotStore::sqlite(db,"test".into());
    let first=store.refreshable(None,u32::MAX).await.unwrap();assert_eq!(first.len(),16);
    let next=store.refreshable(Some(&first[15].bot_uuid),16).await.unwrap();assert_eq!(next[0].bot_uuid,"source-016");
    let mut cursor=None;let mut count=0;
    loop {let page=store.refreshable(cursor.as_deref(),16).await.unwrap();if page.is_empty() {break;}
        assert!(page.iter().all(|c|c.agent_card_url.is_some()));count+=page.len();cursor=Some(page.last().unwrap().bot_uuid.clone());}
    assert_eq!(count,104);
}

#[tokio::test]
async fn malformed_data_and_database_write_errors_are_not_success() {
    let db = database().await;
    let store = DbA2aBotStore::sqlite(db.clone(), "test".into());
    assert!(store.compare_and_set(&record(), 0).await.unwrap());
    db.execute(DbStatement::new("UPDATE bcs_bots SET agent_card='invalid-json' WHERE env='test'")).await.unwrap();
    assert!(store.get("bot").await.is_err());
    db.execute(DbStatement::new("CREATE TRIGGER reject_refresh BEFORE UPDATE ON bcs_bots BEGIN SELECT RAISE(ABORT, 'test failure'); END")).await.unwrap();
    let mut update = record(); update.revision = 2;
    assert!(store.compare_and_set(&update, 1).await.is_err());
    let rows = db.query(DbStatement::new("SELECT agent_card_revision FROM bcs_bots WHERE env='test' AND bot_uuid='bot'")).await.unwrap();
    assert_eq!(rows[0].get_i64("agent_card_revision").unwrap(), Some(1));
}

#[tokio::test]
async fn sqlite_a2a_repository_conformance() {
    use bcs_test_support::contract::a2a_bot::a2a_bot_repo_port_contract_tests;
    let store = DbA2aBotStore::sqlite(database().await, "test".into());
    a2a_bot_repo_port_contract_tests(&store, record()).await;
}

#[tokio::test]
async fn availability_is_bounded_scope_local_and_retains_invalid_a2a_markers() {
    let db = database().await;
    let store = DbA2aBotStore::sqlite(db.clone(), "test".into());
    assert!(store.availability_many(&["bot".into()]).await.unwrap().is_empty());
    assert!(store.compare_and_set(&record(), 0).await.unwrap());
    let states = store.availability_many(&["bot".into(), "plugin".into(), "missing".into()]).await.unwrap();
    assert_eq!(states.len(), 1);
    assert!(states[0].ready);
    assert!(DbA2aBotStore::sqlite(db.clone(), "other".into()).availability_many(&["bot".into()]).await.unwrap().is_empty());
    db.execute(DbStatement::new("UPDATE bcs_bots SET agent_card='{}' WHERE env='test' AND bot_uuid='bot'")).await.unwrap();
    assert!(!store.availability_many(&["bot".into()]).await.unwrap()[0].ready);
    assert!(store.availability_many(&vec!["bot".into(); 257]).await.is_err());
}

struct CountedDb {
    inner:Arc<LocalSqliteDbPlugin>,
    queries:std::sync::atomic::AtomicUsize,
    fail:std::sync::atomic::AtomicBool,
}
#[async_trait::async_trait]
impl DbPlugin for CountedDb {
    async fn query(&self, statement:DbStatement)->bcs_db_api::DbResult<Vec<bcs_db_api::DbRow>> {
        self.queries.fetch_add(1,std::sync::atomic::Ordering::SeqCst);
        if self.fail.load(std::sync::atomic::Ordering::SeqCst) {return Err(bcs_db_api::DbError::Backend("injected read failure".into()));}
        self.inner.query(statement).await
    }
    async fn execute(&self,statement:DbStatement)->bcs_db_api::DbResult<bcs_db_api::DbExecuteResult> {self.inner.execute(statement).await}
    async fn transaction(&self,steps:Vec<bcs_db_api::DbTransactionStep>)->bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> {self.inner.transaction(steps).await}
    async fn health_check(&self)->bcs_db_api::DbResult<bcs_db_api::DbHealth> {self.inner.health_check().await}
}

#[tokio::test]
async fn refresh_and_availability_have_one_bounded_query_even_on_failure() {
    use std::sync::atomic::{AtomicBool,AtomicUsize,Ordering};
    let inner=database().await;
    let setup=DbA2aBotStore::sqlite(inner.clone(),"test".into());
    assert!(setup.compare_and_set(&record(),0).await.unwrap());
    for n in 0..256 {
        inner.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid,env,provider_id,provider_bot_ref,connection_mode,agent_card,agent_card_url,agent_card_revision) VALUES (?,'test','provider',?,'gateway',?,?,1)",
            vec![format!("batch-{n:03}").into(),format!("remote-{n}").into(),record().agent_card.to_string().into(),"https://example.org/card".into()])).await.unwrap();
    }
    let db=Arc::new(CountedDb {inner,queries:AtomicUsize::new(0),fail:AtomicBool::new(false)});
    let store=DbA2aBotStore::sqlite(db.clone(),"test".into());
    assert_eq!(store.refreshable(None,u32::MAX).await.unwrap().len(),16);
    assert_eq!(db.queries.swap(0,Ordering::SeqCst),1);
    let ids=(0..256).map(|n|format!("batch-{n:03}")).collect::<Vec<_>>();
    assert_eq!(store.availability_many(&ids).await.unwrap().len(),256);
    assert_eq!(db.queries.swap(0,Ordering::SeqCst),1);
    assert!(store.availability_many(&vec!["bot".into();257]).await.is_err());
    assert_eq!(db.queries.load(Ordering::SeqCst),0);
    db.fail.store(true,Ordering::SeqCst);
    assert!(store.refreshable(None,16).await.is_err());
    assert_eq!(db.queries.swap(0,Ordering::SeqCst),1);
    assert!(store.availability_many(&ids).await.is_err());
    assert_eq!(db.queries.load(Ordering::SeqCst),1);
}
