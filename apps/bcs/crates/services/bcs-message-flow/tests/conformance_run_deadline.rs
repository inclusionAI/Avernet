use std::sync::Arc;
use bcs_db_api::{DbPlugin,DbStatement};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::{MemoryMessageRepo,MySqlMessageStore};
use bcs_test_support::contract::application::message_delivery::managed_run_deadline_contract;
#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

#[tokio::test]
async fn memory_managed_deadline_keeps_scope_attempt_and_slot() {
    let service=ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new()));
    managed_run_deadline_contract(&service).await.unwrap();
}
#[tokio::test]
async fn sqlite_managed_deadline_keeps_scope_attempt_and_slot() {
    let db=Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id,group_id,env,participants) VALUES ('deadline-session','deadline-group','deadline-test','[]')")).await.unwrap();
    let service=ManagedMessageDelivery::new(Arc::new(MySqlMessageStore::sqlite(db,"deadline-test".into())));
    managed_run_deadline_contract(&service).await.unwrap();
}


#[tokio::test]
async fn sqlite_deadline_write_failure_propagates_without_releasing_the_slot() {
    use bcs_service_api::{ManagedMessageDeliveryService,ManagedDeliveryError};
    use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoError;
    let db=Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id,group_id,env,participants) VALUES ('deadline-session','deadline-group','deadline-test','[]')")).await.unwrap();
    db.execute(DbStatement::new("CREATE TRIGGER reject_reschedule BEFORE UPDATE OF run_deadline_at_ms ON bcs_message_deliveries WHEN OLD.run_deadline_at_ms IS NOT NULL AND NEW.run_deadline_at_ms <> OLD.run_deadline_at_ms BEGIN SELECT RAISE(ABORT,'test deadline persistence failure'); END")).await.unwrap();
    let service=ManagedMessageDelivery::new(Arc::new(MySqlMessageStore::sqlite(db,"deadline-test".into())));
    let error=managed_run_deadline_contract(&service).await.unwrap_err();
    assert!(matches!(error,ManagedDeliveryError::Repository(MessageDeliveryRepoError::Storage(_))));
    let row=service.snapshot(Some("deadline-session")).await.unwrap().remove(0);
    assert_eq!(row.run_deadline_at_ms,Some(10_000));
    assert_eq!(row.state.status,bcs_domain::message_delivery::MessageDeliveryStatus::Dispatching);
    assert_eq!(service.active_count("deadline-bot").await.unwrap(),1);
}

use std::sync::atomic::{AtomicBool,AtomicUsize,Ordering};
use async_trait::async_trait;
use bcs_service_api::*;
use bcs_service_api::types::run_budget::{RunBudgetTarget,RunBudgetUpdate};
use bcs_service_api::port::repo::message_delivery::{MessageDeliveryRepoPort,AdmitMessageDeliveries,DeliveryAdmissionTarget};
use bcs_message_flow::{MemoryBotRunContextStore,run_budget::RunBudgetCore,run_budget_management::RunBudgetManagement};
use bcs_domain::{NewMessage,SenderType,DeliveryType,MessageVisibilityDomain,message_delivery::DeliveryFlowKind};

struct BudgetContexts {
    inner:MemoryBotRunContextStore,calls:AtomicUsize,reject:AtomicBool,fail:AtomicBool,
}
#[async_trait]
impl BotRunContextPort for BudgetContexts {
    async fn put_context(&self,context:BotRunContext) {self.inner.put_context(context).await;}
    async fn get_context(&self,run:&str)->Option<BotRunContext> {self.inner.get_context(run).await}
    async fn try_begin_terminal(&self,run:&str)->bool {self.inner.try_begin_terminal(run).await}
    async fn mark_terminal(&self,run:&str)->bool {self.inner.mark_terminal(run).await}
    async fn release_terminal(&self,run:&str) {self.inner.release_terminal(run).await;}
    async fn begin_provider_transport(&self,run:&str,deadline:u64)->bool {self.inner.begin_provider_transport(run,deadline).await}
    async fn bind_provider_transport(&self,run:&str,transport:ProviderRunTransport)->bool {self.inner.bind_provider_transport(run,transport).await}
    async fn get_provider_transport(&self,run:&str)->Option<ProviderRunTransport> {self.inner.get_provider_transport(run).await}
    async fn mark_provider_transport_terminal(&self,run:&str) {self.inner.mark_provider_transport_terminal(run).await;}
    async fn clear_provider_transport(&self,run:&str) {self.inner.clear_provider_transport(run).await;}
    async fn reschedule_deadline(&self,scope:&BotRunScope,run:&str,expected:u64,deadline:u64)->ServiceResult<bool> {
        self.calls.fetch_add(1,Ordering::SeqCst);
        if self.fail.load(Ordering::SeqCst) {return Err(ServiceError::InternalError("fixture cache write failed".into()));}
        if self.reject.load(Ordering::SeqCst) {return Ok(false);}
        self.inner.reschedule_deadline(scope,run,expected,deadline).await
    }
}
struct BudgetFixture {
    service:RunBudgetManagement,deliveries:Arc<ManagedMessageDelivery>,contexts:Arc<BudgetContexts>,target:RunBudgetTarget,now:u64,
}
async fn budget_fixture(repo:Arc<dyn MessageDeliveryRepoPort>)->BudgetFixture {
    let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() as u64;
    let deliveries=Arc::new(ManagedMessageDelivery::new(repo));
    let admitted=deliveries.admit(AdmitMessageDeliveries {display_message:None,message_id:"budget-message".into(),flow_kind:DeliveryFlowKind::Group,
        now_ms:now as i64,expire_at_ms:None,event:None,targets:vec![DeliveryAdmissionTarget {rejection:None,target_bot_id:"budget-bot".into(),
            kind:DeliveryType::Send,max_queued:10,semantic_projection_json:serde_json::json!({"version":1}),run_id:None}],
        message:NewMessage {visibility_domain:MessageVisibilityDomain::Chat,audience:None,group_id:"budget-group".into(),session_id:"budget-session".into(),
            sender_id:"human".into(),sender_type:SenderType::Human,message_type:"chat".into(),content:serde_json::json!({"text":"test"}),
            client_msg_id:None,owner_bot_id:None,created_at:now,run_id:String::new()}}).await.unwrap();
    let row=&admitted.deliveries[0];let run=row.run_id.clone().unwrap();let deadline=now+120_000;
    deliveries.transition(DeliveryTransitionCommand {delivery_id:row.delivery_id.clone(),expected_state_version:row.state.state_version,
        event:DeliveryLifecycleEvent::StartSend,now_ms:now as i64,request_id:None,actor_id:None,reply:None,
        transport_context_json:Some(serde_json::json!({"version":1})),deadline_at_ms:Some(deadline as i64), caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None}).await.unwrap();
    let contexts=Arc::new(BudgetContexts {inner:MemoryBotRunContextStore::new(),calls:AtomicUsize::new(0),reject:AtomicBool::new(false),fail:AtomicBool::new(false)});
    let scope=BotRunScope {bot_id:"budget-bot".into(),group_id:"budget-group".into(),session_id:"budget-session".into()};
    contexts.inner.put_context(BotRunContext {run_id:run.clone(),bot_id:scope.bot_id.clone(),group_id:scope.group_id.clone(),bcs_session_id:Some(scope.session_id.clone()),deadline_ms:deadline,terminal:false}).await;
    contexts.inner.register_active_run(ActiveBotRunContext {canonical_run_id:run.clone(),downstream_run_id:run.clone(),downstream_session_key:Some(scope.session_id.clone()),
        scope:scope.clone(),transport_owner:BotRunTransportOwner::A2a {provider_id:"provider".into(),provider_bot_ref:"bot".into()},provider_bypass_headers:vec![],deadline_ms:deadline}).await.unwrap();
    assert!(contexts.inner.begin_provider_transport(&run,deadline).await);
    assert!(contexts.inner.bind_provider_transport(&run,ProviderRunTransport::A2a {binding:bcs_domain::a2a::A2aBinding::HttpJson,update_mode:bcs_domain::a2a::A2aUpdateMode::Stream}).await);
    let service=RunBudgetManagement::new(Arc::new(RunBudgetCore),deliveries.clone(),contexts.clone());
    BudgetFixture {service,deliveries,contexts,target:RunBudgetTarget {scope,run_id:run,expected_deadline_ms:deadline},now}
}
async fn sqlite_budget_fixture()->(BudgetFixture,Arc<bcs_db_local::LocalSqliteDbPlugin>) {
    let db=Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id,group_id,env,participants) VALUES ('budget-session','budget-group','budget-test','[]')")).await.unwrap();
    let f=budget_fixture(Arc::new(MySqlMessageStore::sqlite(db.clone(),"budget-test".into()))).await;(f,db)
}

#[tokio::test]
async fn memory_run_budget_service_conformance() {
    let f=budget_fixture(Arc::new(MemoryMessageRepo::new())).await;
    bcs_test_support::contract::application::run_budget::run_budget_service_contract_tests(&f.service,f.deliveries.as_ref(),f.contexts.as_ref(),f.target,f.now).await;
    assert_eq!(f.contexts.calls.load(Ordering::SeqCst),3);
}
#[tokio::test]
async fn sqlite_run_budget_service_conformance() {
    let (f,_db)=sqlite_budget_fixture().await;
    bcs_test_support::contract::application::run_budget::run_budget_service_contract_tests(&f.service,f.deliveries.as_ref(),f.contexts.as_ref(),f.target,f.now).await;
    assert_eq!(f.contexts.calls.load(Ordering::SeqCst),3);
}
#[tokio::test]
async fn initial_total_cap_updates_both_deadlines_without_releasing_slot() {
    let f=budget_fixture(Arc::new(MemoryMessageRepo::new())).await;
    let started=f.service.initialize(f.now,f.target.expected_deadline_ms,RunBudgetLimits {input_wait_ms:100,total_run_ms:1000}).unwrap();
    let applied=f.service.update_deadline(f.target.clone(),RunBudgetUpdate::InitialLimit(started),f.now+10).await.unwrap();
    assert_eq!(applied.deadline_ms,f.now+1000);
    assert_eq!(f.contexts.get_context(&f.target.run_id).await.unwrap().deadline_ms,applied.deadline_ms);
    assert_eq!(f.deliveries.snapshot(Some("budget-session")).await.unwrap()[0].run_deadline_at_ms,Some(applied.deadline_ms as i64));
    assert_eq!(f.deliveries.active_count("budget-bot").await.unwrap(),1);
}
#[tokio::test]
async fn managed_write_failure_prevents_cache_write() {
    let (f,db)=sqlite_budget_fixture().await;
    db.execute(DbStatement::new("CREATE TRIGGER reject_budget BEFORE UPDATE OF run_deadline_at_ms ON bcs_message_deliveries BEGIN SELECT RAISE(ABORT,'fixture write failure'); END")).await.unwrap();
    let started=f.service.initialize(f.now,f.target.expected_deadline_ms,RunBudgetLimits {input_wait_ms:1000,total_run_ms:600_000}).unwrap();
    assert!(f.service.update_deadline(f.target.clone(),RunBudgetUpdate::Pause {state:started.state,input_expires_at_ms:f.now+30_000},f.now+50).await.is_err());
    assert_eq!(f.contexts.calls.load(Ordering::SeqCst),0);
    assert_eq!(f.contexts.get_context(&f.target.run_id).await.unwrap().deadline_ms,f.target.expected_deadline_ms);
    assert_eq!(f.deliveries.snapshot(Some("budget-session")).await.unwrap()[0].run_deadline_at_ms,Some(f.target.expected_deadline_ms as i64));
    assert_eq!(f.deliveries.active_count("budget-bot").await.unwrap(),1);
}
#[tokio::test]
async fn cache_rejection_and_failure_propagate_after_managed_write_without_terminalizing() {
    for fail in [false,true] {
        let f=budget_fixture(Arc::new(MemoryMessageRepo::new())).await;
        f.contexts.reject.store(!fail,Ordering::SeqCst);f.contexts.fail.store(fail,Ordering::SeqCst);
        let started=f.service.initialize(f.now,f.target.expected_deadline_ms,RunBudgetLimits {input_wait_ms:1000,total_run_ms:600_000}).unwrap();
        assert!(f.service.update_deadline(f.target.clone(),RunBudgetUpdate::Pause {state:started.state,input_expires_at_ms:f.now+30_000},f.now+50).await.is_err());
        assert_eq!(f.contexts.calls.load(Ordering::SeqCst),1);
        assert_eq!(f.contexts.get_context(&f.target.run_id).await.unwrap().deadline_ms,f.target.expected_deadline_ms);
        let row=f.deliveries.snapshot(Some("budget-session")).await.unwrap().remove(0);
        assert_eq!(row.run_deadline_at_ms,Some((f.now+1050) as i64));
        assert!(!row.state.status.is_terminal());assert_eq!(f.deliveries.active_count("budget-bot").await.unwrap(),1);
    }
}
