//! Actual Core/application/SQLite query boundary; Card claims never grant BCS visibility.
use std::sync::Arc;
use bcs_bot::{Bot, BotCore, BotControlPlaneCore, core::a2a_bot::A2aBotCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore, PersistentBotRepo, DbProviderStore, a2a::DbA2aBotStore};
use bcs_db_api::{DbPlugin, DbStatement, DbSqlFlavor};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{BotCapabilities, BotRegistryCoreService, BotQueryService, BotDiscoveryService,
    BotDetailCommand, BotDiscoveryCommand, SearchBotsCommand, BotUseCaseError, Skill};
use bcs_service_api::port::repo::a2a_bot::{A2aBotRepoPort,A2aBotRecord};
use serde_json::json;

struct Fixture { app: Bot, db:Arc<LocalSqliteDbPlugin>, cards:Arc<DbA2aBotStore>, record:A2aBotRecord, _dir:tempfile::TempDir }

async fn fixture() -> Fixture {
    let dir=tempfile::tempdir().unwrap();
    let repo=Arc::new(MemoryBotRepo::with_base_dir(dir.path().into()));
    let authority=bcs_test_support::lazy_authority_chain_with_repo(repo.clone());
    let registry=Arc::new(BotCore::with_repo(repo));
    let db=Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new("CREATE TABLE bcs_bots (
        bot_uuid TEXT NOT NULL, env TEXT NOT NULL, name TEXT, bot_info TEXT, session_token TEXT,
        provider_id TEXT, provider_bot_ref TEXT, connection_mode TEXT DEFAULT 'gateway',
        visibility TEXT, user_visibility TEXT DEFAULT 'protected', created_by TEXT,
        status TEXT DEFAULT 'online', actor_kind TEXT DEFAULT 'bot', is_deleted INTEGER DEFAULT 0,
        agent_code TEXT, task_claim_mode INTEGER DEFAULT 0, task_dream_mode INTEGER DEFAULT 0,
        friend_ext JSON, friend_check_in_strategy TEXT DEFAULT 'APPROVAL',
        gmt_create TEXT DEFAULT CURRENT_TIMESTAMP,gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(bot_uuid,env))")).await.unwrap();
    for sql in include_str!("../../../../migrations/sqlite/039_bot_a2a.sql").split(';').map(str::trim).filter(|s|!s.is_empty()) {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    let env=bcs_config::resolve_env_str();
    let cards=Arc::new(DbA2aBotStore::sqlite(db.clone(),env.clone()));
    let raw=json!({"name":"Card payment agent","description":"Payment capabilities","supportedInterfaces":[{"url":"https://example.org/a2a","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}],
        "visibility":"public","owners":["untrusted"],"future":{"opaque":42},
        "skills":[{"id":"refund_lookup","name":"Refund lookup","description":"settlementneedle 100%_exact",
            "tags":["invoice-tag"],"futureSkill":{"opaque":"not-a-search-field"}}]});
    let mut record=A2aBotRecord {bot_uuid:"card-1".into(),provider_id:"provider".into(),provider_bot_ref:"card-1".into(),
        agent_card_url:Some("https://example.org/card".into()),agent_card:raw,revision:1,};
    for (id,visibility) in [("card-1","public"),("card-2","public"),("card-private","private"),("legacy","public")] {
        let caps=BotCapabilities {name:Some(format!("BCS {id}")),summary:Some("legacy-only".into()),visibility:visibility.into(),
            skills:vec![Skill::new("legacy-skill")],..Default::default()};
        registry.register(id.into(),caps.clone()).await.unwrap();
        authority.hook.seed_owner(id,"alice");
        db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid,env,name,bot_info,provider_id,provider_bot_ref,visibility) VALUES(?,?,?,?,?,?,?)",
            vec![id.into(),env.clone().into(),format!("BCS {id}").into(),serde_json::to_string(&caps).unwrap().into(),"provider".into(),id.into(),visibility.into()])).await.unwrap();
        if id!="legacy" {
            record.bot_uuid=id.into();record.provider_bot_ref=id.into();
            assert!(cards.compare_and_set(&record,0).await.unwrap());
        }
    }
    record.bot_uuid="card-1".into();record.provider_bot_ref="card-1".into();
    let providers=Arc::new(MemoryProviderStore::new());
    let control=Arc::new(BotControlPlaneCore::new(Arc::new(PersistentBotRepo::with_sql_flavor(db.clone(),DbSqlFlavor::Sqlite)),providers.clone(),providers));
    let core=Arc::new(A2aBotCore::new(cards.clone(),Arc::new(DbProviderStore::sqlite(db.clone()))));
    let app=Bot::new(registry).with_bot_authority(authority.hook).with_control_plane(control).with_a2a_queries(core);
    Fixture {app,db,cards,record,_dir:dir}
}

#[tokio::test]
async fn card_discovery_matches_skill_descriptions_tags_ids_and_preserves_raw_fields() {
    let f=fixture().await;
    for query in ["settlementneedle","invoice-tag","refund_lookup","100%_exact"] {
        let result=f.app.discover_bots(BotDiscoveryCommand {q:Some(query.into()),..Default::default()}).await.unwrap();
        assert_eq!(result.count,2,"{query}");
        for bot in result.bots {assert_eq!(bot.agent_card,Some(f.record.agent_card.clone()));assert_ne!(bot.bot_uuid,"card-private");}
    }
    let result=f.app.discover_bots(BotDiscoveryCommand {skills:vec!["REFUND_LOOKUP".into()],..Default::default()}).await.unwrap();
    assert_eq!(result.count,2);
    assert!(f.app.discover_bots(BotDiscoveryCommand {q:Some("not-a-search-field".into()),..Default::default()}).await.unwrap().bots.is_empty());
    let legacy=f.app.discover_bots(BotDiscoveryCommand {skills:vec!["legacy-skill".into()],..Default::default()}).await.unwrap();
    assert_eq!(legacy.bots.iter().map(|bot|bot.bot_uuid.as_str()).collect::<Vec<_>>(),vec!["legacy"]);
}

#[tokio::test]
async fn search_counts_and_pages_use_card_text_before_pagination() {
    let f=fixture().await;
    let first=f.app.search_bots(SearchBotsCommand {q:Some("settlementneedle".into()),limit:1,..Default::default()}).await.unwrap();
    assert_eq!(first.total,2);assert_eq!(first.items.len(),1);assert_eq!(first.items[0].bot_uuid,"card-1");
    assert_eq!(first.items[0].agent_card,Some(f.record.agent_card.clone()));
    let second=f.app.search_bots(SearchBotsCommand {q:Some("settlementneedle".into()),limit:1,offset:1,..Default::default()}).await.unwrap();
    assert_eq!(second.total,2);assert_eq!(second.items[0].bot_uuid,"card-2");
    let legacy=f.app.search_bots(SearchBotsCommand {q:Some("legacy-only".into()),..Default::default()}).await.unwrap();
    assert_eq!(legacy.total,1);assert_eq!(legacy.items[0].bot_uuid,"legacy");
}

#[tokio::test]
async fn details_keep_authority_and_refresh_without_copying_card_into_legacy_fields() {
    let mut f=fixture().await;
    let read=||BotDetailCommand {caller_actor_id:Some("human_alice".into()),bot_id:"card-private".into()};
    let detail=f.app.get_bot(read()).await.unwrap();
    assert_eq!(detail.visibility,"private");assert_eq!(detail.agent_card,Some(f.record.agent_card.clone()));
    assert!(matches!(f.app.get_bot(BotDetailCommand {caller_actor_id:Some("human_untrusted".into()),..read()}).await,Err(BotUseCaseError::Forbidden(_))));
    assert!(f.app.get_bot(BotDetailCommand {caller_actor_id:Some("reader-bot".into()),..read()}).await.is_err());
    f.record.revision=2;f.record.agent_card["skills"][0]["description"]=json!("new-description");
    assert!(f.cards.compare_and_set(&f.record,1).await.unwrap());
    let detail=f.app.get_bot(BotDetailCommand {caller_actor_id:Some("human_alice".into()),bot_id:"card-1".into()}).await.unwrap();
    assert_eq!(detail.agent_card.as_ref().unwrap()["skills"][0]["description"],"new-description");
    assert_eq!(detail.capabilities.summary.as_deref(),Some("legacy-only"));
    assert_eq!(f.app.discover_bots(BotDiscoveryCommand {q:Some("new-description".into()),..Default::default()}).await.unwrap().count,1);
}

#[tokio::test]
async fn malformed_stored_card_is_an_error_instead_of_legacy_fallback() {
    let f=fixture().await;
    f.db.execute(DbStatement::new("UPDATE bcs_bots SET agent_card='invalid-json' WHERE bot_uuid='card-1'")).await.unwrap();
    assert!(f.app.get_bot(BotDetailCommand {caller_actor_id:Some("human_alice".into()),bot_id:"card-1".into()}).await.is_err());
    assert!(f.app.search_bots(SearchBotsCommand {q:Some("settlementneedle".into()),..Default::default()}).await.is_err());
}
