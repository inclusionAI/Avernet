use std::sync::{Arc, atomic::{AtomicBool, AtomicUsize, Ordering}};
use async_trait::async_trait;
use bcs_bot::{BotCore, ProviderCore};
use bcs_app_a2a_bot::A2aBotManagement;
use bcs_bot::core::a2a_bot::A2aBotCore;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore, DbProviderStore, a2a::DbA2aBotStore};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{ProviderAuthMode, ProviderCoreService, ProviderRepoPort, ServiceError, ServiceResult};
use bcs_service_api::application::a2a_bot::{A2aBotManagementService, ConfigureA2aBotCommand};
use bcs_service_api::port::{a2a_card::{A2aCardPort, ValidatedA2aCard}, repo::a2a_bot::{A2aBinding, A2aInterface, A2aUpdateMode, A2aBotRepoPort}};
use serde_json::json;

struct Fetcher { calls: AtomicUsize, fail: AtomicBool, change_route: AtomicBool, paused:AtomicBool, entered:tokio::sync::Notify, release:tokio::sync::Notify }

#[tokio::test]
async fn refresh_round_preserves_last_good_snapshot_and_retries_next_round() {
    let f=fixture().await;let configured=f.service.configure(f.command.clone()).await.unwrap();
    assert_eq!(f.service.refresh_due(1000).await.unwrap(),0);
    assert_eq!(f.service.refresh_due(300_999).await.unwrap(),0);
    assert_eq!(f.service.refresh_due(301_000).await.unwrap(),1);
    assert_eq!(f.cards.get("bot").await.unwrap().unwrap().revision,2);
    f.fetcher.fail.store(true,Ordering::SeqCst);
    let previous=f.cards.get("bot").await.unwrap().unwrap();
    assert!(f.service.refresh_due(601_000).await.is_err());
    assert_eq!(f.cards.get("bot").await.unwrap().unwrap(),previous);
    assert_eq!(f.service.refresh_due(601_001).await.unwrap(),0);
    f.fetcher.fail.store(false,Ordering::SeqCst);
    assert_eq!(f.service.refresh_due(901_000).await.unwrap(),1);
    assert_eq!(f.cards.get("bot").await.unwrap().unwrap().revision,configured.revision+2);
}

#[async_trait]
impl A2aCardPort for Fetcher {
    async fn validate(&self,raw:serde_json::Value,_ready:bool)->ServiceResult<ValidatedA2aCard> {
        let interface=bcs_service_api::types::a2a_bot::select_a2a_interface(&raw,|_,_|true).map_err(|_|ServiceError::InternalError("invalid test Card".into()))?;
        Ok(ValidatedA2aCard {raw,interface})
    }

    async fn fetch(&self, _url: &str, ready: bool) -> ServiceResult<ValidatedA2aCard> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        assert!(ready);
        if self.paused.load(Ordering::SeqCst) {self.entered.notify_one();self.release.notified().await;}
        if self.fail.load(Ordering::SeqCst) { return Err(ServiceError::InternalError("test fetch failure".into())); }
        let url=if self.change_route.load(Ordering::SeqCst) {"https://other.example.org"} else {"https://example.org/a2a/remote"};
        Ok(ValidatedA2aCard { raw: json!({"name":"Card Agent","description":"Full description","supportedInterfaces":[{"url":url,"protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}],"skills":[{"description":"full description", "future":42}]}),
            interface: A2aInterface { url: if self.change_route.load(Ordering::SeqCst) { "https://other.example.org" } else { "https://example.org/a2a/remote" }.into(),
                tenant: None, protocol_version: "1.0".into(), binding: A2aBinding::HttpJson, update_mode: A2aUpdateMode::Stream } })
    }
}

struct Fixture {
    service: A2aBotManagement, core: Arc<A2aBotCore>, cards: Arc<DbA2aBotStore>, fetcher: Arc<Fetcher>,
    providers: Arc<MemoryProviderStore>, command: ConfigureA2aBotCommand,
    db:Arc<LocalSqliteDbPlugin>,
    _dir: tempfile::TempDir,
}

async fn fixture() -> Fixture {
    let dir = tempfile::tempdir().unwrap();
    let providers = Arc::new(MemoryProviderStore::new());
    let registry = Arc::new(BotCore::with_provider_repos(Arc::new(MemoryBotRepo::with_base_dir(dir.path().into())), providers.clone(), providers.clone(), providers.clone()));
    let provider_core = Arc::new(ProviderCore::new(providers.clone(), providers.clone(), providers.clone(), registry));
    let registered = provider_core.register_provider("Provider".into(), Some("https://example.org/provider".into()), ProviderAuthMode::StaticBearer, "alice".into(), None, None).await.unwrap();
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new("CREATE TABLE bcs_bots (bot_uuid TEXT NOT NULL, env TEXT NOT NULL, provider_id TEXT, provider_bot_ref TEXT, connection_mode TEXT, webhook_url TEXT, is_deleted INTEGER NOT NULL DEFAULT 0, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (bot_uuid, env))")).await.unwrap();
    let env = bcs_config::resolve_env_str();
    db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid, env, provider_id, provider_bot_ref, connection_mode) VALUES ('bot', ?, ?, 'remote', 'gateway')", vec![env.clone().into(), registered.provider.provider_id.clone().into()])).await.unwrap();
    for sql in bcs_db_api::split_sql_statements(include_str!("../../../../migrations/sqlite/039_bot_a2a.sql")).into_iter() {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    let cards = Arc::new(DbA2aBotStore::sqlite(db.clone(), env));
    let core = Arc::new(A2aBotCore::new(cards.clone(), Arc::new(DbProviderStore::sqlite(db.clone()))));
    let fetcher = Arc::new(Fetcher { calls: AtomicUsize::new(0), fail: AtomicBool::new(false), change_route: AtomicBool::new(false),paused:AtomicBool::new(false),entered:Default::default(),release:Default::default() });
    let command = ConfigureA2aBotCommand { provider_id: registered.provider.provider_id,
        provider_admin_token: registered.provider_admin_token, bot_uuid: "bot".into(),
        source: "https://example.org/card".into(), expected_revision: 0, now_ms: 1000 };
    Fixture { service: A2aBotManagement::new(provider_core, core.clone(), fetcher.clone()), core, cards, fetcher, providers, command, db, _dir: dir }
}

#[tokio::test]
async fn authorized_configuration_preserves_card_without_persisting_route_or_credentials() {
    let f = fixture().await;
    let configured = f.service.configure(f.command.clone()).await.unwrap();
    assert_eq!(configured.revision, 1);
    assert_eq!(configured.agent_card["skills"][0]["description"], "full description");
    assert_eq!(f.cards.get("bot").await.unwrap(), Some(configured));
    assert_eq!(f.fetcher.calls.load(Ordering::SeqCst), 1);
    let read = f.service.get(&f.command.provider_id, &f.command.provider_admin_token, "bot").await.unwrap().unwrap();
    assert_eq!(read.agent_card["skills"][0]["description"], "full description");
    assert!(f.service.get(&f.command.provider_id, "wrong-admin", "bot").await.is_err());
    assert_eq!(f.fetcher.calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn unauthorized_or_disabled_provider_never_fetches_or_writes() {
    let f = fixture().await;
    let mut bad = f.command.clone(); bad.provider_admin_token = "bad".into();
    assert!(f.service.configure(bad).await.is_err());
    f.providers.update_provider_disabled(&f.command.provider_id, true, 2000).await.unwrap();
    assert!(f.service.configure(f.command.clone()).await.is_err());
    assert_eq!(f.fetcher.calls.load(Ordering::SeqCst), 0);
    assert!(f.cards.get("bot").await.unwrap().is_none());
}

#[tokio::test]
async fn unknown_or_foreign_bot_is_rejected_before_network() {
    let f = fixture().await;
    let mut bad = f.command.clone(); bad.bot_uuid = "missing".into();
    assert!(f.service.configure(bad).await.is_err());
    assert_eq!(f.fetcher.calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn failed_refresh_preserves_snapshot_and_new_card_routes_are_allowed_for_new_runs() {
    let f=fixture().await;let old=f.service.configure(f.command.clone()).await.unwrap();
    f.fetcher.fail.store(true,Ordering::SeqCst);
    assert!(f.service.refresh(&f.command.provider_id,&f.command.provider_admin_token,"bot",2000).await.is_err());
    assert_eq!(f.cards.get("bot").await.unwrap(),Some(old));
    f.fetcher.fail.store(false,Ordering::SeqCst);f.fetcher.change_route.store(true,Ordering::SeqCst);
    let fresh=f.service.refresh(&f.command.provider_id,&f.command.provider_admin_token,"bot",2000).await.unwrap();
    assert_eq!(fresh.agent_card["supportedInterfaces"][0]["url"],"https://other.example.org");
    assert_eq!(fresh.revision,2);
    assert!(f.service.configure(f.command.clone()).await.is_err());
    assert_eq!(f.cards.get("bot").await.unwrap(),Some(fresh));
}

#[tokio::test]
async fn a2a_management_conformance() {
    use bcs_test_support::contract::a2a_bot::a2a_bot_management_service_contract_tests;
    let f = fixture().await;
    a2a_bot_management_service_contract_tests(&f.service, f.command.clone()).await;
    assert_eq!(f.fetcher.calls.load(Ordering::SeqCst), 3);
}

#[tokio::test]
async fn a2a_core_conformance() {
    use bcs_test_support::contract::a2a_bot::a2a_bot_core_service_contract_tests;
    let f = fixture().await;
    let card = f.fetcher.fetch("https://example.org/card", true).await.unwrap();
    let record = bcs_service_api::port::repo::a2a_bot::A2aBotRecord {
        bot_uuid:"bot".into(), provider_id:f.command.provider_id.clone(), provider_bot_ref:"remote".into(),
        agent_card_url:Some("https://example.org/card".into()), agent_card:card.raw, revision:1,};
    a2a_bot_core_service_contract_tests(f.core.as_ref(), record).await;
}

#[tokio::test]
async fn inline_configuration_has_no_source_and_never_refreshes_over_network() {
    let f=fixture().await;let raw=json!({"name":"Inline","supportedInterfaces":[{"url":"https://example.org/a2a","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}],"unknown":{"keep":true}});
    let mut command=f.command.clone();command.source=bcs_service_api::types::a2a_bot::A2aCardSource::Inline(raw.clone());
    let saved=f.service.configure(command).await.unwrap();assert_eq!(saved.agent_card,raw);assert!(saved.agent_card_url.is_none());
    assert!(f.service.refresh(&f.command.provider_id,&f.command.provider_admin_token,"bot",1000).await.is_err());
    f.service.refresh_due(1000).await.unwrap();assert_eq!(f.service.refresh_due(301000).await.unwrap(),0);
    assert_eq!(f.fetcher.calls.load(Ordering::SeqCst),0);
}

#[tokio::test]
async fn late_url_refresh_cannot_overwrite_a_new_inline_card() {
    let f=fixture().await;f.service.configure(f.command.clone()).await.unwrap();
    let service=Arc::new(f.service);f.fetcher.paused.store(true,Ordering::SeqCst);
    let (provider,token)=(f.command.provider_id.clone(),f.command.provider_admin_token.clone());
    let background=service.clone();
    let old=tokio::spawn(async move {background.refresh(&provider,&token,"bot",2000).await});
    tokio::time::timeout(std::time::Duration::from_secs(3),f.fetcher.entered.notified()).await.unwrap();
    let mut command=f.command.clone();command.expected_revision=1;
    command.source=bcs_service_api::types::a2a_bot::A2aCardSource::Inline(json!({"name":"New inline","supportedInterfaces":[{"url":"https://example.org/new","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}]}));
    let committed=service.configure(command).await.unwrap();
    f.fetcher.release.notify_one();assert!(matches!(old.await.unwrap(),Err(ServiceError::Conflict(_))));
    assert_eq!(f.cards.get("bot").await.unwrap(),Some(committed));
}

#[tokio::test]
async fn failing_refresh_pages_are_bounded_and_advance_without_starving_later_bots() {
    let f=fixture().await;
    f.service.configure(f.command.clone()).await.unwrap();
    for n in 0..17 {
        f.db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid,env,provider_id,provider_bot_ref,connection_mode,agent_card,agent_card_url,agent_card_revision) SELECT ?,env,provider_id,?,'gateway',agent_card,agent_card_url,agent_card_revision FROM bcs_bots WHERE bot_uuid='bot'",
            vec![format!("page-{n:03}").into(),format!("remote-{n}").into()])).await.unwrap();
    }
    f.fetcher.calls.store(0,Ordering::SeqCst);f.fetcher.fail.store(true,Ordering::SeqCst);
    assert_eq!(f.service.refresh_due(1000).await.unwrap(),0);
    assert!(f.service.refresh_due(301000).await.is_err());
    assert_eq!(f.fetcher.calls.load(Ordering::SeqCst),16);
    assert!(f.service.refresh_due(311000).await.is_err());
    assert_eq!(f.fetcher.calls.load(Ordering::SeqCst),18);
    assert_eq!(f.service.refresh_due(321000).await.unwrap(),0);
    assert_eq!(f.fetcher.calls.load(Ordering::SeqCst),18);
    assert_eq!(f.cards.get("bot").await.unwrap().unwrap().revision,1);
}

#[tokio::test]
async fn scheduled_card_write_failure_is_returned_and_snapshot_remains_unchanged() {
    let f=fixture().await;let before=f.service.configure(f.command.clone()).await.unwrap();
    f.db.execute(DbStatement::new("CREATE TRIGGER reject_card_update BEFORE UPDATE ON bcs_bots BEGIN SELECT RAISE(ABORT,'test write failure'); END")).await.unwrap();
    assert_eq!(f.service.refresh_due(1000).await.unwrap(),0);
    assert!(f.service.refresh_due(301000).await.is_err());
    assert_eq!(f.cards.get("bot").await.unwrap(),Some(before));
}
