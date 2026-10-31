//! Listening HTTP + real interaction/managed-delivery/context implementations.
use std::{convert::Infallible,sync::{Arc,Mutex,atomic::{AtomicBool,AtomicUsize,Ordering}},time::{Duration,SystemTime,UNIX_EPOCH}};
use async_trait::async_trait;
use axum::{Router,Json,extract::{State,Path},http::HeaderMap,response::{IntoResponse,Response,sse::{Sse,Event}},routing::{get,post}};
use futures::{stream::{self,BoxStream},StreamExt};
use serde_json::{json,Value};
use bcs_a2a_http::{delivery::{A2aHttpTransport,InputRuntime},structured_exec::{EXTENSION,PROFILE}};
use bcs_bot_store::MemoryProviderStore;
use bcs_chat_run_store::a2a::DbA2aRunStore;
use bcs_db_api::{DbPlugin,DbSqlFlavor,DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{DeliveryType,NewMessage,SenderType,MessageVisibilityDomain,message_delivery::DeliveryFlowKind};
use bcs_interaction::{InteractionManagement,MemoryInteractionStore};
use bcs_message_flow::{MemoryBotRunContextStore,managed_delivery::ManagedMessageDelivery,run_budget::RunBudgetCore,run_budget_management::RunBudgetManagement};
use bcs_message_store::MemoryMessageRepo;
use bcs_protocol::{BcsFrame,RequestFrame};
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::*;
use bcs_service_api::types::{a2a_bot::{A2aBinding,A2aInterface,A2aUpdateMode,A2aExtensionDeclaration},run_budget::RunBudgetLimits};
use bcs_service_api::DeliveryLifecycleEvent as Lifecycle;
use bcs_service_api::port::repo::{a2a_run::{A2aRunRepoPort,A2aRunState},message_delivery::{AdmitMessageDeliveries,DeliveryAdmissionTarget}};

fn now()->u64 {SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64}
struct Agent {
    task:Mutex<Value>,requests:Mutex<Vec<Value>>,subscriptions:AtomicUsize,queries:AtomicUsize,cancels:AtomicUsize,
    initial_closed:AtomicBool,confirm:bool,batch:usize,subscribe_failures:AtomicUsize,cancel_rejected:AtomicBool,
}
struct Closed(Arc<Agent>);
impl Drop for Closed {fn drop(&mut self) {self.0.initial_closed.store(true,Ordering::SeqCst);}}
fn events(values:Vec<Value>)->BoxStream<'static,Result<Event,Infallible>> {
    stream::iter(values.into_iter().map(|v|Ok(Event::default().data(v.to_string())))).boxed()
}
fn initial_task(batch:usize)->Value {
    let mut message:Value=serde_json::from_str(include_str!("fixtures/java-structured-exec-input.json")).unwrap();
    message["parts"][0]["data"]["x-bcs-exec"]["expiresAtMs"]=json!(now()+60_000);
    for n in 2..=batch {
        let tool=message["parts"][0]["data"]["x-bcs-exec"]["tools"]["tool-1"].clone();
        let choice=message["parts"][0]["data"]["properties"]["decisions"]["properties"]["tool-1"].clone();
        message["parts"][0]["data"]["x-bcs-exec"]["tools"][format!("tool-{n}")]=tool;
        message["parts"][0]["data"]["properties"]["decisions"]["properties"][format!("tool-{n}")]=choice;
    }
    json!({"id":"fixture-task","contextId":"fixture-context","status":{"state":"TASK_STATE_INPUT_REQUIRED","message":message},
        "artifacts":[{"artifactId":"answer","parts":[{"text":"开始"}]}]})
}
async fn send(State(agent):State<Arc<Agent>>,headers:HeaderMap,Json(body):Json<Value>)->Sse<BoxStream<'static,Result<Event,Infallible>>> {
    assert_eq!(headers["a2a-extensions"],EXTENSION);
    agent.requests.lock().unwrap().push(body);
    let task=agent.task.lock().unwrap().clone();
    let closed=Closed(agent.clone());
    Sse::new(events(vec![json!({"task":task})]).chain(stream::once(async move {
        let _closed=closed;std::future::pending::<Result<Event,Infallible>>().await
    })).boxed())
}
async fn query(State(agent):State<Arc<Agent>>,Path(id):Path<String>)->Json<Value> {
    assert_eq!(id,"fixture-task");agent.queries.fetch_add(1,Ordering::SeqCst);
    Json(agent.task.lock().unwrap().clone())
}
async fn continuation(State(agent):State<Arc<Agent>>,headers:HeaderMap,Json(body):Json<Value>)->Json<Value> {
    assert_eq!(headers["a2a-extensions"],EXTENSION);
    assert_eq!(body["message"]["taskId"],"fixture-task");assert_eq!(body["message"]["contextId"],"fixture-context");
    assert_eq!(body["configuration"]["returnImmediately"],true);
    assert_eq!(body["configuration"]["acceptedOutputModes"],json!(["text/plain"]));
    assert_eq!(body["message"]["parts"][0]["metadata"][EXTENSION],json!({"type":"structured_input","id":"fixture-request"}));
    agent.requests.lock().unwrap().push(body.clone());
    let mut task=agent.task.lock().unwrap();
    if agent.confirm {
        let patch=body["message"]["parts"][0]["data"]["decisions"].as_object().unwrap();
        for (tool,decision) in patch {
            task["metadata"][PROFILE]["version"]=json!(1.0);
            task["metadata"][PROFILE]["requests"]["fixture-request"]["accepted"][tool]=decision.clone();
            let schema=&mut task["status"]["message"]["parts"][0]["data"];
            schema["x-bcs-exec"]["accepted"][tool]=decision.clone();
            let choice=&mut schema["properties"]["decisions"]["properties"][tool];
            choice.as_object_mut().unwrap().remove("enum");choice["const"]=decision.clone();
        }
        if task["metadata"][PROFILE]["requests"]["fixture-request"]["accepted"].as_object().unwrap().len()==agent.batch {
            task["status"]=json!({"state":"TASK_STATE_WORKING"});
        }
    }
    Json(json!({"task":task.clone()}))
}
async fn action(State(agent):State<Arc<Agent>>,Path(id):Path<String>)->Response {
    if id=="fixture-task:cancel" {
        agent.cancels.fetch_add(1,Ordering::SeqCst);
        if agent.cancel_rejected.load(Ordering::SeqCst) {return axum::http::StatusCode::SERVICE_UNAVAILABLE.into_response();}
        let mut task=agent.task.lock().unwrap();task["status"]=json!({"state":"TASK_STATE_CANCELED"});
        return Json(task.clone()).into_response();
    }
    assert_eq!(id,"fixture-task:subscribe");agent.subscriptions.fetch_add(1,Ordering::SeqCst);
    if agent.subscribe_failures.swap(0,Ordering::SeqCst)>0 {return axum::http::StatusCode::SERVICE_UNAVAILABLE.into_response();}
    let mut task=agent.task.lock().unwrap();task["status"]=json!({"state":"TASK_STATE_COMPLETED"});
    task["artifacts"][0]["parts"][0]["text"]=json!("开始完成");
    Sse::new(events(vec![json!({"task":task.clone()})])).into_response()
}

#[derive(Default)]
struct Frontend {events:Mutex<Vec<InteractionFrontendEvent>>}
#[async_trait]
impl InteractionFrontendPort for Frontend {
    async fn publish_interaction(&self,event:InteractionFrontendEvent)->ServiceResult<()> {self.events.lock().unwrap().push(event);Ok(())}
}
struct Auth;
#[async_trait]
impl CanResolveInteraction for Auth {
    async fn can_resolve(&self,cmd:CanResolveInteractionCommand)->ServiceResult<bool> {Ok(cmd.actor_id=="owner")}
}
#[derive(Default)]
struct Ingest {text:Mutex<String>,terminal:AtomicBool}
#[async_trait]
impl ProviderEventIngestService for Ingest {
    async fn ingest_provider_event(&self,cmd:ProviderEventIngestCommand)->ServiceResult<BotEventOutcome> {
        if let Some(delta)=cmd.event.event_payload.get("delta_text").and_then(Value::as_str) {self.text.lock().unwrap().push_str(delta);}
        if matches!(cmd.event.state,ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted) {self.terminal.store(true,Ordering::SeqCst);}
        Ok(BotEventOutcome {bot_deliveries:vec![],frontend_deliveries:vec![],unregistered_run_ids:vec![],mentions:vec![],delivered_count:0,failed_count:0,delivery_results:vec![]})
    }
}
struct Fixture {
    transport:Arc<A2aHttpTransport>,target:BotDeliveryTarget,interactions:Arc<dyn InteractionService>,frontend:Arc<Frontend>,contexts:Arc<MemoryBotRunContextStore>,
    runs:Arc<DbA2aRunStore>,managed:Arc<ManagedMessageDelivery>,agent:Arc<Agent>,ingest:Arc<Ingest>,run:String,deadline:u64,server:tokio::task::JoinHandle<()>,
}
impl Drop for Fixture {fn drop(&mut self) {self.server.abort();}}
async fn wait_until(test:impl Fn()->bool) {
    tokio::time::timeout(Duration::from_secs(4),async {while !test() {tokio::time::sleep(Duration::from_millis(10)).await;}}).await.unwrap();
}
impl Fixture {
    fn requested(&self)->Vec<InteractionFrontendEvent> {self.frontend.events.lock().unwrap().iter().filter(|e|e.payload["phase"]=="requested").cloned().collect()}
    fn resolution(&self,event:&InteractionFrontendEvent,decision:&str)->ResolveInteractionCommand {
        ResolveInteractionCommand {bcs_run_id:self.run.clone(),interaction_id:event.payload["interactionId"].as_str().unwrap().into(),
            idempotency_key:format!("{}-{decision}",event.payload["interactionId"]),resolver_actor_id:"owner".into(),
            expected_bcs_session_id:Some("session".into()),expected_group_id:Some("group".into()),resolution:json!({"decision":decision})}
    }
}
async fn fixture(batch:usize,confirm:bool,wait_ms:u64)->Fixture {
    fixture_with_identity_failure(batch,confirm,wait_ms,false).await
}
async fn fixture_with_identity_failure(batch:usize,confirm:bool,wait_ms:u64,fail_identity:bool)->Fixture {
    let agent=Arc::new(Agent {task:Mutex::new(initial_task(batch)),requests:Mutex::new(vec![]),subscriptions:AtomicUsize::new(0),queries:AtomicUsize::new(0),
        cancels:AtomicUsize::new(0),initial_closed:AtomicBool::new(false),confirm,batch,subscribe_failures:AtomicUsize::new(0),cancel_rejected:AtomicBool::new(false)});
    let listener=tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();let base=format!("http://{}/a2a/bots/demo",listener.local_addr().unwrap());
    let router=Router::new().route("/a2a/bots/demo/message:stream",post(send)).route("/a2a/bots/demo/message:send",post(continuation))
        .route("/a2a/bots/demo/tasks/{id}",get(query).post(action)).with_state(agent.clone());
    let server=tokio::spawn(async move {axum::serve(listener,router).await.unwrap();});
    let db=Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new("CREATE TABLE bcs_bots (bot_uuid TEXT,env TEXT,is_deleted INTEGER DEFAULT 0, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP)")).await.unwrap();
    for sql in bcs_db_api::split_sql_statements(include_str!("../../../../migrations/sqlite/039_bot_a2a.sql")).into_iter() {db.execute(DbStatement::new(sql)).await.unwrap();}
    if fail_identity {
        db.execute(DbStatement::new("CREATE TRIGGER reject_identity BEFORE INSERT ON bcs_a2a_contexts BEGIN SELECT RAISE(ABORT,'fixture identity write failure'); END")).await.unwrap();
    }
    let runs=Arc::new(DbA2aRunStore::new(db,"test".into(),DbSqlFlavor::Sqlite));
    let credentials=Arc::new(MemoryProviderStore::new());
    credentials.insert_credential(ProviderCredential {provider_id:"provider".into(),credential_kind:"downlink_bcs_to_provider".into(),secret_value:"test-exec-only".into(),disabled:false,created_at:1,updated_at:1}).await.unwrap();
    let transport=Arc::new(A2aHttpTransport::new(OutboundUrlGuard::new(true,true),runs.clone(),credentials));
    let contexts=Arc::new(MemoryBotRunContextStore::new());let ingest=Arc::new(Ingest::default());transport.set_ingest(ingest.clone(),contexts.clone()).unwrap();
    let frontend=Arc::new(Frontend::default());
    let interactions:Arc<dyn InteractionService>=Arc::new(InteractionManagement::new(Arc::new(MemoryInteractionStore::new()),Arc::new(Auth),transport.clone(),frontend.clone(),60_000));
    let managed=Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let budgets=Arc::new(RunBudgetManagement::new(Arc::new(RunBudgetCore),managed.clone(),contexts.clone()));
    transport.set_input_runtime(InputRuntime::new(interactions.clone(),managed.clone(),budgets,RunBudgetLimits {input_wait_ms:wait_ms,total_run_ms:120_000})).unwrap();
    let deadline=now()+30_000;
    let admitted=managed.admit(AdmitMessageDeliveries {display_message:None,message_id:"exec-message".into(),flow_kind:DeliveryFlowKind::Group,now_ms:now() as i64,
        expire_at_ms:None,event:None,targets:vec![DeliveryAdmissionTarget {rejection:None,target_bot_id:"bot".into(),kind:DeliveryType::Send,max_queued:10,
            semantic_projection_json:json!({"version":1}),run_id:None}],message:NewMessage {visibility_domain:MessageVisibilityDomain::Chat,audience:None,group_id:"group".into(),
            session_id:"session".into(),sender_id:"owner".into(),sender_type:SenderType::Human,message_type:"chat".into(),content:json!({"text":"test"}),
            client_msg_id:None,owner_bot_id:None,created_at:now(),run_id:String::new()}}).await.unwrap();
    let row=&admitted.deliveries[0];let run=row.run_id.clone().unwrap();
    managed.transition(DeliveryTransitionCommand {delivery_id:row.delivery_id.clone(),expected_state_version:row.state.state_version,event:Lifecycle::StartSend,
        now_ms:now() as i64,request_id:None,actor_id:None,reply:None,transport_context_json:Some(json!({"version":1})),deadline_at_ms:Some(deadline as i64), caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None}).await.unwrap();
    let target=BotDeliveryTarget::A2a {bot_id:"bot".into(),provider_id:"provider".into(),provider_bot_ref:"demo".into(),credential_kind:"downlink_bcs_to_provider".into(),bearer:RedactedToken::new("test-exec-only"),card_revision:1,
        interface:A2aInterface {url:base,tenant:None,protocol_version:"1.0".into(),binding:A2aBinding::HttpJson,update_mode:A2aUpdateMode::Stream},
        extensions:vec![A2aExtensionDeclaration {uri:EXTENSION.into(),required:Some(false),params:json!({"jsonschema_draft":"2020-12","bcs_exec_profile":PROFILE})}]};
    contexts.put_context(BotRunContext {run_id:run.clone(),bot_id:"bot".into(),group_id:"group".into(),bcs_session_id:Some("session".into()),deadline_ms:deadline,terminal:false}).await;
    contexts.register_active_run(ActiveBotRunContext {canonical_run_id:run.clone(),downstream_run_id:run.clone(),downstream_session_key:Some("session".into()),
        scope:BotRunScope {bot_id:"bot".into(),group_id:"group".into(),session_id:"session".into()},transport_owner:BotRunTransportOwner::from_target(&target),provider_bypass_headers:vec![],deadline_ms:deadline}).await.unwrap();
    let delivered=transport.deliver(BotDeliveryCommand {target:target.clone(),run_id:run.clone(),frame:BcsFrame::Request(RequestFrame::new("attempt","chat.send",Some(json!({"bcs_session_id":"session",
        "message":{"role":"user","content":[{"type":"text","text":"查询订单"}],"timestamp":1}})))),delivery_kind:BotDeliveryKind::Send,provider_transport:Default::default(),provider_bypass_headers:vec![]}).await;
    if fail_identity {assert!(delivered.is_err());} else {delivered.unwrap();}
    Fixture {transport,target,interactions,frontend,contexts,runs,managed,agent,ingest,run,deadline,server}
}

#[tokio::test]
async fn first_identity_write_failure_still_cancels_the_observed_task_without_claiming_persistence() {
    let f=fixture_with_identity_failure(1,true,60_000,true).await;
    assert_eq!(f.agent.cancels.load(Ordering::SeqCst),1);
    assert_eq!(f.agent.task.lock().unwrap()["status"]["state"],"TASK_STATE_CANCELED");
    let durable=f.runs.get(&f.run).await.unwrap().unwrap();
    assert!(durable.remote_task_id.is_none());assert!(!durable.state.is_terminal());
    assert!(!f.ingest.terminal.load(Ordering::SeqCst));assert!(f.requested().is_empty());
    assert_eq!(f.managed.active_count("bot").await.unwrap(),1);
}

#[tokio::test]
async fn approved_and_denied_decisions_resume_original_task_and_preserve_text() {
    for decision in ["allow_once","deny"] {
        let f=fixture(1,true,60_000).await;
        wait_until(||f.agent.initial_closed.load(Ordering::SeqCst)).await;
        let event=f.requested().remove(0);
        let paused=f.runs.get(&f.run).await.unwrap().unwrap();assert_eq!(paused.state,A2aRunState::InputRequired);
        assert!(paused.deadline_ms>f.deadline);assert_eq!(f.contexts.get_context(&f.run).await.unwrap().deadline_ms,paused.deadline_ms);
        assert_eq!(f.managed.active_count("bot").await.unwrap(),1);
        tokio::time::sleep(Duration::from_millis(40)).await;
        assert_eq!(f.agent.queries.load(Ordering::SeqCst),0);assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),0);
        let result=f.interactions.resolve(f.resolution(&event,decision)).await.unwrap();assert!(result.accepted);
        wait_until(||f.ingest.terminal.load(Ordering::SeqCst)).await;
        assert_eq!(f.runs.get(&f.run).await.unwrap().unwrap().state,A2aRunState::Completed);
        assert_eq!(*f.ingest.text.lock().unwrap(),"开始完成");assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),1);
        assert!(f.interactions.list_pending("session").await.unwrap().is_empty());
        assert_eq!(f.agent.requests.lock().unwrap().len(),2);
    }
}

#[tokio::test]
async fn partial_batch_keeps_original_wait_and_authorization_precedes_transport() {
    let f=fixture(2,true,60_000).await;let events=f.requested();assert_eq!(events.len(),2);
    let before=f.runs.get(&f.run).await.unwrap().unwrap();
    let mut unauthorized=f.resolution(&events[0],"deny");unauthorized.resolver_actor_id="intruder".into();
    assert!(matches!(f.interactions.resolve(unauthorized).await,Err(InteractionServiceError::Unauthorized)));
    assert_eq!(f.agent.requests.lock().unwrap().len(),1);
    f.interactions.resolve(f.resolution(&events[0],"allow_once")).await.unwrap();
    let partial=f.runs.get(&f.run).await.unwrap().unwrap();assert_eq!(partial.state,A2aRunState::InputRequired);
    assert_eq!(partial.deadline_ms,before.deadline_ms);assert_eq!(partial.budget,before.budget);
    assert_eq!(f.interactions.list_pending("session").await.unwrap().len(),1);
    f.interactions.resolve(f.resolution(&events[0],"allow_once")).await.unwrap();
    assert_eq!(f.agent.requests.lock().unwrap().len(),2,"local duplicate does not send again");
    assert!(f.interactions.resolve(f.resolution(&events[0],"deny")).await.is_err());
    f.interactions.resolve(f.resolution(&events[1],"deny")).await.unwrap();
    wait_until(||f.ingest.terminal.load(Ordering::SeqCst)).await;
    assert_eq!(f.runs.get(&f.run).await.unwrap().unwrap().state,A2aRunState::Completed);
}

#[tokio::test]
async fn successful_rpc_without_persisted_receipt_is_not_acceptance() {
    let f=fixture(1,false,60_000).await;let event=f.requested().remove(0);
    assert!(f.interactions.resolve(f.resolution(&event,"allow_once")).await.is_err());
    wait_until(||f.agent.cancels.load(Ordering::SeqCst)>0).await;
    assert!(!f.frontend.events.lock().unwrap().iter().any(|e|e.payload["phase"]=="resolved"));
    assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),0);
}

#[tokio::test]
async fn unconfirmed_cleanup_retains_slot_and_never_publishes_a_terminal() {
    let f=fixture(1,false,60_000).await;
    f.agent.cancel_rejected.store(true,Ordering::SeqCst);
    let event=f.requested().remove(0);
    assert!(f.interactions.resolve(f.resolution(&event,"allow_once")).await.is_err());
    tokio::time::timeout(Duration::from_secs(4),async {
        loop {
            if f.runs.get(&f.run).await.unwrap().unwrap().state==A2aRunState::Unknown {break;}
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.unwrap();
    assert!(!f.ingest.terminal.load(Ordering::SeqCst));
    assert_eq!(f.agent.cancels.load(Ordering::SeqCst),1);
    assert_eq!(f.managed.active_count("bot").await.unwrap(),1);
    let row=f.managed.snapshot(Some("session")).await.unwrap().remove(0);
    assert_eq!(row.state.status,bcs_domain::message_delivery::MessageDeliveryStatus::Unknown);
    assert!(f.interactions.list_pending("session").await.unwrap().is_empty());
    assert!(!f.contexts.get_context(&f.run).await.unwrap().terminal);
    f.agent.cancel_rejected.store(false,Ordering::SeqCst);
    let cancelled=f.transport.abort(BotAbortDeliveryCommand {command_id:"recover-cancel".into(),target:f.target.clone(),run_id:Some(f.run.clone()),
        group_id:"group".into(),session_id:"session".into(),timeout_ms:1_000,provider_bypass_headers:vec![]}).await.unwrap();
    assert_eq!(cancelled.aborted_run_ids,vec![f.run.clone()]);
    assert_eq!(f.runs.get(&f.run).await.unwrap().unwrap().state,A2aRunState::Canceled);
    assert_eq!(f.agent.cancels.load(Ordering::SeqCst),2,"explicit recovery cancels the same Task without another Send");
    assert_eq!(f.agent.requests.lock().unwrap().len(),2);
}

#[tokio::test]
async fn human_wait_expiry_cancels_task_without_polling_or_renewing_wait() {
    let f=fixture(1,true,150).await;
    wait_until(||f.agent.cancels.load(Ordering::SeqCst)>0).await;
    assert_eq!(f.agent.queries.load(Ordering::SeqCst),0);assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),0);
    assert_eq!(f.agent.requests.lock().unwrap().len(),1);
}

#[tokio::test]
async fn waiting_cancel_uses_frozen_exact_task_and_stops_its_owner() {
    let f=fixture(1,true,60_000).await;
    let mut cancel=BotAbortDeliveryCommand {command_id:"cancel".into(),target:f.target.clone(),run_id:Some(f.run.clone()),group_id:"group".into(),session_id:"session".into(),
        timeout_ms:1_000,provider_bypass_headers:vec![]};
    cancel.session_id="foreign".into();assert!(f.transport.abort(cancel.clone()).await.is_err());
    assert_eq!(f.agent.cancels.load(Ordering::SeqCst),0);
    cancel.session_id="session".into();assert_eq!(f.transport.abort(cancel).await.unwrap().aborted_run_ids,vec![f.run.clone()]);
    assert_eq!(f.runs.get(&f.run).await.unwrap().unwrap().state,A2aRunState::Canceled);
    assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),0);
}

#[tokio::test]
async fn continuation_retries_transient_subscription_without_resending_decision() {
    let f=fixture(1,true,60_000).await;f.agent.subscribe_failures.store(1,Ordering::SeqCst);
    let event=f.requested().remove(0);f.interactions.resolve(f.resolution(&event,"allow_once")).await.unwrap();
    wait_until(||f.ingest.terminal.load(Ordering::SeqCst)).await;
    assert_eq!(f.runs.get(&f.run).await.unwrap().unwrap().state,A2aRunState::Completed);
    assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),2);assert_eq!(f.agent.requests.lock().unwrap().len(),2);
    assert_eq!(*f.ingest.text.lock().unwrap(),"开始完成");assert_eq!(f.agent.cancels.load(Ordering::SeqCst),0);
}
