//! Shared native MySQL cases for the isolated store test and full-chain CI gate.
use std::sync::Arc;
use bcs_bot_store::a2a::DbA2aBotStore;
use bcs_chat_run_store::a2a::DbA2aRunStore;
use bcs_db_api::{DbPlugin,DbSqlFlavor,DbStatement};
use bcs_service_api::port::repo::a2a_bot::*;
use bcs_service_api::port::repo::a2a_run::*;
use bcs_test_support::contract::a2a_bot::{a2a_bot_repo_port_contract_tests,a2a_run_repo_port_contract_tests};
use serde_json::json;

fn card()->A2aBotRecord {
    A2aBotRecord {bot_uuid:"a2a-bot".into(),provider_id:"provider".into(),provider_bot_ref:"remote".into(),
        agent_card_url:Some("https://example.org/card".into()),agent_card:json!({"name":"Agent","description":"服务能力","supportedInterfaces":[{"url":"https://example.org/a2a/bots/remote","protocolBinding":"HTTP+JSON","protocolVersion":"1.0"}],
            "skills":[{"id":"refund_lookup","name":"查询","description":"完整说明 100%_exact foo~bar","tags":["invoice-tag"],"unknown":{"keep":true}}]}),revision:1,}
}
fn run()->A2aRunRecord {
    let card=card();
    let interface=select_a2a_interface(&card.agent_card,|_,_|true).unwrap();
    A2aRunRecord {run_id:"a2a-run".into(),scope:A2aContextScope {bot_id:card.bot_uuid,session_id:"session".into(),provider_id:"provider".into(),
        provider_bot_ref:"remote".into(),interface_url:interface.url.clone(),tenant:None,route_fingerprint:None},group_id:"group".into(),interface,
        credential_kind:"downlink_bcs_to_provider".into(),extensions:vec![],message_id:"initial-message".into(),request_digest:"digest".into(),
        remote_task_id:None,context_id:None,task_version:None,state:A2aRunState::Submitting,deadline_ms:121000,budget:None,pending_input:None,updated_at_ms:1000,version:1}
}

/// The caller owns a fully migrated disposable schema and its cleanup.
pub async fn verify(db:Arc<dyn DbPlugin>) {
    let columns=db.query(DbStatement::new("SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='bcs_bots' AND (COLUMN_NAME LIKE 'a2a_%' OR COLUMN_NAME LIKE 'agent_card%') ORDER BY COLUMN_NAME")).await.unwrap();
    let names=columns.iter().map(|row|row.get_string("name").unwrap().unwrap()).collect::<Vec<_>>();
    assert_eq!(names,vec!["agent_card","agent_card_revision","agent_card_url"]);

    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid,name,env,provider_id,provider_bot_ref,connection_mode) VALUES ('a2a-bot','Billing %_exact','a2a-test','provider','remote','gateway')")).await.unwrap();
    let cards=DbA2aBotStore::new(db.clone(),"a2a-test".into(),DbSqlFlavor::Mysql);
    a2a_bot_repo_port_contract_tests(&cards,card()).await;
    for query in ["BILLING", "%_exact", "完整说明","100%_exact","foo~bar","invoice-tag","REFUND_LOOKUP"] {
        assert_eq!(cards.discovery_cards(&["a2a-bot".into()],Some(query),&[]).await.unwrap()["a2a-bot"],Some(card().agent_card),"{query}");
    }
    for query in ["Billing X_exact", "100X_exact","100%Xexact","fooXbar","not-present"] {
        assert_eq!(cards.discovery_cards(&["a2a-bot".into()],Some(query),&[]).await.unwrap()["a2a-bot"],None,"{query}");
    }
    assert_eq!(cards.discovery_cards(&["a2a-bot".into()],None,&["REFUND_LOOKUP".into()]).await.unwrap()["a2a-bot"],Some(card().agent_card));
    assert_eq!(cards.discovery_cards(&["a2a-bot".into()],Some("billing"),&["REFUND_LOOKUP".into()]).await.unwrap()["a2a-bot"],Some(card().agent_card));
    assert_eq!(cards.discovery_cards(&["a2a-bot".into()],Some("billing"),&["legacy-skill".into()]).await.unwrap()["a2a-bot"],None);
    assert!(DbA2aBotStore::new(db.clone(),"foreign".into(),DbSqlFlavor::Mysql).get("a2a-bot").await.unwrap().is_none());
    assert!(cards.availability_many(&["a2a-bot".into()]).await.unwrap()[0].ready);
    db.execute(DbStatement::new("ALTER TABLE bcs_bots ADD CONSTRAINT reject_a2a_card CHECK (agent_card_revision <> 2)")).await.unwrap();
    let mut update=card();update.revision=2;
    assert!(cards.compare_and_set(&update,1).await.is_err());assert_eq!(cards.get("a2a-bot").await.unwrap().unwrap().revision,1);
    db.execute(DbStatement::new("ALTER TABLE bcs_bots DROP CHECK reject_a2a_card")).await.unwrap();
    // Inline updates clear the optional source and leave periodic URL scans.
    let mut inline=card();inline.revision=2;inline.agent_card_url=None;
    assert!(cards.compare_and_set(&inline,1).await.unwrap());
    assert_eq!(cards.get("a2a-bot").await.unwrap(),Some(inline));
    assert!(cards.refreshable(None,16).await.unwrap().is_empty());
    let runs=DbA2aRunStore::new(db.clone(),"a2a-test".into(),DbSqlFlavor::Mysql);
    a2a_run_repo_port_contract_tests(&runs,run()).await;
    let mut first=run();first.run_id="failed-association".into();first.scope.session_id="another-session".into();
    assert!(runs.create(&first).await.unwrap());
    db.execute(DbStatement::new("ALTER TABLE bcs_a2a_contexts ADD CONSTRAINT reject_a2a_context CHECK (context_id <> 'another-context')")).await.unwrap();
    let mut accepted=first.clone();accepted.version=2;accepted.remote_task_id=Some("another-remote-task".into());accepted.context_id=Some("another-context".into());accepted.state=A2aRunState::Running;
    assert!(runs.compare_and_set(&accepted,1).await.is_err());assert_eq!(runs.get(&first.run_id).await.unwrap(),Some(first.clone()));
    assert!(runs.context(&first.scope).await.unwrap().is_none());
    let orphan=db.query(DbStatement::new("SELECT COUNT(*) AS found FROM bcs_a2a_tasks WHERE env='a2a-test' AND remote_task_id='another-remote-task'")).await.unwrap();
    assert_eq!(orphan[0].get_i64("found").unwrap(),Some(0),"failed context association must roll back the Task reservation too");
    db.execute(DbStatement::new("ALTER TABLE bcs_a2a_contexts DROP CHECK reject_a2a_context")).await.unwrap();
    for table in ["bcs_a2a_tasks", "bcs_a2a_run_bindings", "bcs_a2a_contexts"] {
        let rows=db.query(DbStatement::new(format!("SELECT id, CAST(gmt_create AS CHAR) AS created, CAST(gmt_modified AS CHAR) AS modified FROM {table} LIMIT 1"))).await.unwrap();
        assert!(rows[0].get_i64("id").unwrap().unwrap()>0);
        assert!(rows[0].get_string("created").unwrap().is_some());
        assert!(rows[0].get_string("modified").unwrap().is_some());
    }
    bcs_test_support::contract::a2a_bot::a2a_many_run_task_contract_tests(&runs,run()).await;
    println!("A2A MySQL: Card/Run, JSON search, transactional failure and shared Task contracts passed");
}
