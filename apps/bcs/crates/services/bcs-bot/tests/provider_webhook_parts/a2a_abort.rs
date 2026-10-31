use super::*;
use bcs_db_api::{DbPlugin,DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_bot_store::a2a::DbA2aBotStore;
use bcs_service_api::{ProviderCredentialRepoPort,ProviderRepoPort};
use bcs_service_api::port::repo::a2a_bot::*;

#[tokio::test]
async fn retained_card_remains_usable_and_abort_preserves_provider_authority_checks() {
    let f=fixture().await;let bound=bot(&f,"stale-card").await;
    let db=Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new("CREATE TABLE bcs_bots (bot_uuid TEXT,provider_id TEXT,provider_bot_ref TEXT,env TEXT,connection_mode TEXT,is_deleted INTEGER DEFAULT 0, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP)")).await.unwrap();
    for sql in include_str!("../../../../../migrations/sqlite/039_bot_a2a.sql").split(';').map(str::trim).filter(|s|!s.is_empty()) {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid,provider_id,provider_bot_ref,env,connection_mode) VALUES (?,?,?,?,?)",
        vec![bound.bot_uuid.clone().into(),f.provider_id.clone().into(),bound.provider_bot_ref.clone().into(),"test".into(),"gateway".into()])).await.unwrap();
    let cards=Arc::new(DbA2aBotStore::sqlite(db.clone(),"test".into()));
    let card=A2aBotRecord {bot_uuid:bound.bot_uuid.clone(),provider_id:f.provider_id.clone(),provider_bot_ref:bound.provider_bot_ref.clone(),
        agent_card_url:Some("https://example.org/card".into()),agent_card:json!({"name":"original Card","capabilities":{"extensions":[]},"supportedInterfaces":[{"url":"https://example.org/agent","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}]}),revision:1,};
    assert!(cards.compare_and_set(&card,0).await.unwrap());
    let registry=BotCore::with_provider_repos(f.bots.clone(),f.store.clone(),f.store.clone(),f.store.clone()).with_a2a_cards(cards.clone());
    assert!(matches!(registry.resolve_delivery_target(&bound.bot_uuid).await.unwrap(),BotDeliveryTarget::A2a {..}));
    assert!(matches!(registry.resolve_delivery_target(&bound.bot_uuid).await.unwrap(),BotDeliveryTarget::A2a {..}));
    let abort=registry.resolve_abort_target(&bound.bot_uuid).await.unwrap();
    assert!(matches!(abort,BotDeliveryTarget::A2a {interface,provider_id,..} if interface.url=="https://example.org/agent" && provider_id==f.provider_id));
    f.store.update_binding_disabled(&bound.bot_uuid,true,3).await.unwrap();
    assert!(registry.resolve_abort_target(&bound.bot_uuid).await.is_err());
    f.store.update_binding_disabled(&bound.bot_uuid,false,4).await.unwrap();
    f.store.update_provider_disabled(&f.provider_id,true,5).await.unwrap();
    assert!(registry.resolve_abort_target(&bound.bot_uuid).await.is_err());
    f.store.update_provider_disabled(&f.provider_id,false,6).await.unwrap();
    f.store.update_credential_disabled(&f.provider_id,"downlink_bcs_to_provider",true,7).await.unwrap();
    assert!(registry.resolve_abort_target(&bound.bot_uuid).await.is_err());
    f.store.update_credential_disabled(&f.provider_id,"downlink_bcs_to_provider",false,8).await.unwrap();
    db.execute(DbStatement::new("UPDATE bcs_bots SET provider_bot_ref='another-bot'")).await.unwrap();
    assert!(registry.resolve_abort_target(&bound.bot_uuid).await.is_err(),"stale Card does not relax Bot affiliation");
}

#[tokio::test]
async fn interfaces_are_derived_from_card_and_current_grpc_policy_without_saved_route_columns() {
    let f=fixture().await;let bound=bot(&f,"derived-route").await;
    let db=Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new("CREATE TABLE bcs_bots (bot_uuid TEXT,provider_id TEXT,provider_bot_ref TEXT,env TEXT,connection_mode TEXT,is_deleted INTEGER DEFAULT 0,gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP)")).await.unwrap();
    for sql in include_str!("../../../../../migrations/sqlite/039_bot_a2a.sql").split(';').map(str::trim).filter(|s|!s.is_empty()) {db.execute(DbStatement::new(sql)).await.unwrap();}
    db.execute(DbStatement::with_params("INSERT INTO bcs_bots (bot_uuid,provider_id,provider_bot_ref,env,connection_mode) VALUES (?,?,?,?,?)",
        vec![bound.bot_uuid.clone().into(),f.provider_id.clone().into(),bound.provider_bot_ref.clone().into(),"test".into(),"gateway".into()])).await.unwrap();
    let cards=Arc::new(DbA2aBotStore::sqlite(db,"test".into()));
    let raw=json!({"supportedInterfaces":[{"url":"https://example.org/grpc","protocolBinding":"GRPC","protocolVersion":"1.0"},
        {"url":"https://example.org/http","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}]});
    cards.compare_and_set(&A2aBotRecord {bot_uuid:bound.bot_uuid.clone(),provider_id:f.provider_id.clone(),provider_bot_ref:bound.provider_bot_ref.clone(),agent_card_url:None,agent_card:raw,revision:1},0).await.unwrap();
    let base=BotCore::with_provider_repos(f.bots.clone(),f.store.clone(),f.store.clone(),f.store.clone()).with_a2a_cards(cards);
    assert!(matches!(base.resolve_delivery_target(&bound.bot_uuid).await.unwrap(),BotDeliveryTarget::A2a {interface,credential_kind,..}
        if interface.binding==A2aBinding::HttpJson && credential_kind=="downlink_bcs_to_provider"));
    let grpc=base.with_a2a_route_policy(bcs_route_security::OutboundUrlGuard::strict(),vec!["https://example.org/grpc".into()]);
    assert!(matches!(grpc.resolve_delivery_target(&bound.bot_uuid).await.unwrap(),BotDeliveryTarget::A2a {interface,..} if interface.binding==A2aBinding::Grpc));
}
