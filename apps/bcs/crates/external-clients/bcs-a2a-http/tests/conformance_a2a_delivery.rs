//! Public, listening-HTTP transport integration. Does not require Harness artifacts.
use std::{collections::HashMap, convert::Infallible, sync::{Arc, Mutex, atomic::{AtomicUsize, Ordering}}, time::{Duration, SystemTime, UNIX_EPOCH}};
use async_trait::async_trait;
use axum::{Json, Router, extract::{Path, State}, http::{HeaderMap, StatusCode}, response::{IntoResponse, Response, sse::{Event, Sse}}, routing::{get, post}};
use futures::{stream, StreamExt};
use bcs_a2a_http::delivery::A2aHttpTransport;
use bcs_bot_store::MemoryProviderStore;
use bcs_chat_run_store::a2a::DbA2aRunStore;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_message_flow::MemoryBotRunContextStore;
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::{BotAbortDeliveryCommand, BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort,
    BotDeliveryTarget, BotEventCommand, BotEventOutcome, BotRunContext, BotRunContextPort,
    ChatEventState, ProviderCredential, ProviderCredentialRepoPort, ProviderEventIngestCommand,
    ProviderEventIngestService, ProviderEventSource, RedactedToken, ServiceResult};
use bcs_service_api::port::repo::a2a_run::{A2aRunRepoPort, A2aRunState};
use bcs_service_api::types::a2a_bot::{A2aBinding, A2aInterface, A2aUpdateMode};
use serde_json::{json, Value};

#[derive(Clone, Copy)]
enum Mode { Complete, Wait, MissingInitialTask, RecoverGet, DedupRetry, HttpError(u16), Terminal(&'static str), Message, CancelDenied, RecoverState, UnspecifiedFlood, Flapping, EmptyRecoveries }
struct Agent {
    mode: Mode, sequence: AtomicUsize, requests: Mutex<Vec<Value>>, tasks: Mutex<HashMap<String, Value>>,
    cancels: Mutex<Vec<String>>,
    queries: AtomicUsize, subscriptions: AtomicUsize,
}

async fn send(State(agent): State<Arc<Agent>>, headers: HeaderMap, Json(body): Json<Value>) -> Response {
    assert_eq!(headers.get("authorization").unwrap(), "Bearer test-a2a-bearer");
    assert_eq!(headers.get("a2a-version").unwrap(), "1.0");
    assert!(!headers.contains_key("x-bcs-secret"));
    assert_eq!(body["configuration"]["acceptedOutputModes"],json!(["text/plain"]));
    agent.requests.lock().unwrap().push(body);
    let sequence = agent.sequence.fetch_add(1, Ordering::SeqCst) + 1;
    if let Mode::HttpError(status)=agent.mode {
        let status=StatusCode::from_u16(status).unwrap();
        return (status,Json(json!({"error":{"message":"DO_NOT_FORWARD_REMOTE_DIAGNOSTICS","details":[
            {"@type":"type.googleapis.com/google.rpc.ErrorInfo","reason":"CONTENT_TYPE_NOT_SUPPORTED",
                "domain":"a2a-protocol.org","metadata":{"private":"DO_NOT_FORWARD_METADATA"}}
        ]}}))).into_response();
    }
    let id = format!("remote-{}", if matches!(agent.mode,Mode::DedupRetry) {1} else {sequence});
    let final_state=match agent.mode {Mode::Terminal(state)=>state,Mode::Complete | Mode::RecoverGet | Mode::DedupRetry | Mode::RecoverState=>"TASK_STATE_COMPLETED",_=>"TASK_STATE_WORKING"};
    let final_task = json!({"id":id,"contextId":"remote-context", "status":{"state":final_state},
        "artifacts":[{"artifactId":"answer","parts":[{"text":"你好世界"}]}]});
    agent.tasks.lock().unwrap().insert(id.clone(), final_task.clone());
    let mut events = vec![json!({"task":{"id":id,"contextId":"remote-context", "status":{"state":"TASK_STATE_SUBMITTED"}}})];
    if matches!(agent.mode,Mode::Message) {
        events=vec![json!({"message":{"messageId":"standalone","role":"ROLE_AGENT","parts":[{"text":"无任务回复"}]}})];
    } else if let Mode::Terminal(state)=agent.mode {
        events.push(json!({"statusUpdate":{"taskId":id,"contextId":"remote-context","status":{"state":state}}}));
    } else if matches!(agent.mode,Mode::DedupRetry) {
        assert_eq!(headers["a2a-extensions"],bcs_a2a_http::delivery::DEDUP_EXTENSION);
        events = if sequence==1 { vec![] } else { vec![json!({"task":final_task})] };
    } else if matches!(agent.mode,Mode::MissingInitialTask) {
        events = vec![json!({"statusUpdate":{"taskId":id,"contextId":"remote-context","status":{"state":"TASK_STATE_WORKING"}}})];
    } else if matches!(agent.mode, Mode::Complete) {
        events.push(json!({"artifactUpdate":{"taskId":id,"contextId":"remote-context","artifact":{"artifactId":"answer","parts":[{"text":"你好"}]},"append":false}}));
        if headers.contains_key("a2a-extensions") {
            assert_eq!(headers["a2a-extensions"], bcs_a2a_http::tools::EXTENSION);
            for record in [
                json!({"phase":"start","toolCallId":"call","name":"query_order","args":{"id":"1"}}),
                json!({"phase":"result","toolCallId":"call","name":"query_order","isError":false,
                    "result":{"content":[{"type":"text","text":"ok"}]}}),
            ] {
                let event=json!({"statusUpdate":{"taskId":id,"contextId":"remote-context","status":{
                    "state":"TASK_STATE_WORKING","message":{"messageId":"observation","role":"ROLE_AGENT",
                    "metadata":{bcs_a2a_http::tools::EXTENSION:{"version":"1"}},"parts":[{"data":record}]}}}});
                events.push(event.clone()); events.push(event);
            }
        }
        events.push(json!({"artifactUpdate":{"taskId":id,"contextId":"remote-context","artifact":{"artifactId":"answer","parts":[{"text":"世界"}]},"append":true}}));
        events.push(json!({"statusUpdate":{"taskId":id,"contextId":"remote-context","status":{"state":"TASK_STATE_COMPLETED"}}}));
    } else if matches!(agent.mode, Mode::RecoverGet | Mode::RecoverState | Mode::UnspecifiedFlood) {
        events.push(json!({"artifactUpdate":{"taskId":id,"contextId":"remote-context","artifact":{"artifactId":"answer",
            "parts":[{"text":"你好"}]},"append":false}}));
        if matches!(agent.mode,Mode::RecoverState | Mode::UnspecifiedFlood) {
            for _ in 0..if matches!(agent.mode,Mode::UnspecifiedFlood) {4} else {1} {
                events.push(json!({"statusUpdate":{"taskId":id,"contextId":"remote-context","status":{"state":"TASK_STATE_UNSPECIFIED"}}}));
            }
        }
    }
    let stream = stream::iter(events.into_iter().map(|event|Ok::<_,Infallible>(Event::default().data(event.to_string()))));
    if matches!(agent.mode, Mode::Wait | Mode::CancelDenied) { Sse::new(stream.chain(stream::pending()).boxed()).into_response() }
    else { Sse::new(stream.boxed()).into_response() }
}
async fn task(State(agent): State<Arc<Agent>>, Path(id): Path<String>) -> Response {
    let query=agent.queries.fetch_add(1,Ordering::SeqCst);
    if matches!(agent.mode,Mode::RecoverGet) && query==0 { return StatusCode::SERVICE_UNAVAILABLE.into_response(); }
    Json(agent.tasks.lock().unwrap().get(&id).unwrap().clone()).into_response()
}
async fn cancel(State(agent): State<Arc<Agent>>, Path(id): Path<String>) -> Response {
    let id = id.strip_suffix(":cancel").expect("canonical cancel suffix").to_owned();
    agent.cancels.lock().unwrap().push(id.clone());
    if matches!(agent.mode,Mode::CancelDenied) {
        return (StatusCode::CONFLICT,Json(json!({"error":{"message":"DO_NOT_FORWARD_CANCEL_DIAGNOSTIC","details":[
            {"@type":"type.googleapis.com/google.rpc.ErrorInfo","reason":"TASK_NOT_CANCELABLE","domain":"a2a-protocol.org"}
        ]}}))).into_response();
    }
    let mut tasks = agent.tasks.lock().unwrap(); let task = tasks.get_mut(&id).unwrap();
    task["status"]["state"] = json!("TASK_STATE_CANCELED"); Json(task.clone()).into_response()
}

async fn task_post(State(agent): State<Arc<Agent>>, Path(id): Path<String>, headers:HeaderMap) -> Response {
    if let Some(task_id)=id.strip_suffix(":subscribe") {
        assert_eq!(headers["authorization"],"Bearer test-a2a-bearer");
        assert_eq!(headers["a2a-version"],"1.0");
        let subscription=agent.subscriptions.fetch_add(1,Ordering::SeqCst)+1;
        let events=if matches!(agent.mode,Mode::EmptyRecoveries) {vec![]} else {
            assert!(matches!(agent.mode,Mode::Flapping));
            let state=if subscription>=5 {"TASK_STATE_COMPLETED"} else {"TASK_STATE_WORKING"};
            agent.tasks.lock().unwrap().get_mut(task_id).unwrap()["status"]["state"]=json!(state);
            vec![json!({"statusUpdate":{"taskId":task_id,"contextId":"remote-context","status":{"state":state}}})]
        };
        Sse::new(stream::iter(events.into_iter().map(|event|Ok::<_,Infallible>(Event::default().data(event.to_string()))))).into_response()
    } else if id.ends_with(":cancel") {
        cancel(State(agent),Path(id)).await
    } else { StatusCode::NOT_FOUND.into_response() }
}

#[derive(Default)]
struct Ingest { events: Mutex<Vec<BotEventCommand>> }
#[async_trait]
impl ProviderEventIngestService for Ingest {
    async fn ingest_provider_event(&self, command: ProviderEventIngestCommand) -> ServiceResult<BotEventOutcome> {
        assert!(matches!(command.source, ProviderEventSource::A2a { .. }));
        self.events.lock().unwrap().push(command.event);
        Ok(BotEventOutcome { bot_deliveries: vec![], frontend_deliveries: vec![], unregistered_run_ids: vec![],
            mentions: vec![], delivered_count: 0, failed_count: 0, delivery_results: vec![] })
    }
}

struct Fixture {
    transport: A2aHttpTransport, target: BotDeliveryTarget, runs: Arc<DbA2aRunStore>, contexts: Arc<MemoryBotRunContextStore>,
    ingest: Arc<Ingest>, agent: Arc<Agent>, server: tokio::task::JoinHandle<()>,
}
impl Drop for Fixture { fn drop(&mut self) { self.server.abort(); } }
fn now() -> u64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64 }
async fn fixture(mode: Mode) -> Fixture {
    let agent = Arc::new(Agent { mode, sequence: AtomicUsize::new(0), requests: Mutex::new(vec![]), tasks: Mutex::new(HashMap::new()), cancels: Mutex::new(vec![]), queries:AtomicUsize::new(0), subscriptions:AtomicUsize::new(0) });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}/a2a/bots/demo", listener.local_addr().unwrap());
    let router = Router::new().route("/a2a/bots/demo/message:stream", post(send))
        .route("/a2a/bots/demo/tasks/{id}", get(task).post(task_post)).with_state(agent.clone());
    let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    db.execute(DbStatement::new("CREATE TABLE bcs_bots (bot_uuid TEXT, env TEXT, is_deleted INTEGER DEFAULT 0, gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP)")).await.unwrap();
    for sql in bcs_db_api::split_sql_statements(include_str!("../../../../migrations/sqlite/039_bot_a2a.sql")).into_iter() {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    let runs = Arc::new(DbA2aRunStore::new(db, "test".into(), DbSqlFlavor::Sqlite));
    let credentials = Arc::new(MemoryProviderStore::new());
    credentials.insert_credential(ProviderCredential { provider_id:"provider".into(), credential_kind:"downlink_bcs_to_provider".into(),
        secret_value:"test-a2a-bearer".into(), disabled:false, created_at:1, updated_at:1 }).await.unwrap();
    let transport = A2aHttpTransport::new(OutboundUrlGuard::new(true,true), runs.clone(), credentials);
    let contexts = Arc::new(MemoryBotRunContextStore::new()); let ingest = Arc::new(Ingest::default());
    transport.set_ingest(ingest.clone(), contexts.clone()).unwrap();
    let target = BotDeliveryTarget::A2a { bot_id:"bot".into(), provider_id:"provider".into(), provider_bot_ref:"demo".into(),
        interface:A2aInterface { url:base, tenant:None, protocol_version:"1.0".into(), binding:A2aBinding::HttpJson, update_mode:A2aUpdateMode::Stream },
        credential_kind:"downlink_bcs_to_provider".into(), bearer:RedactedToken::new("test-a2a-bearer"), card_revision:1, extensions:vec![] };
    Fixture { transport,target,runs,contexts,ingest,agent,server }
}
async fn command(f: &Fixture, run: &str) -> BotDeliveryCommand {
    f.contexts.put_context(BotRunContext { run_id:run.into(),bot_id:"bot".into(),group_id:"group".into(),bcs_session_id:Some("session".into()),deadline_ms:now()+60_000,terminal:false }).await;
    BotDeliveryCommand { target:f.target.clone(), run_id:run.into(), frame:BcsFrame::Request(RequestFrame::new("attempt", "chat.send",Some(json!({
        "session_key":"session","bcs_session_id":"session","bcs_group_id":"group", "message":{"role":"assistant","content":[{"type":"text","text":"测试"}],"timestamp":1}})))),
        delivery_kind:BotDeliveryKind::Send,provider_transport:Default::default(),provider_bypass_headers:vec![("authorization".into(),"untrusted".into()),("x-bcs-secret".into(),"untrusted".into())] }
}
async fn completed(f: &Fixture, run: &str) {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if f.runs.get(run).await.unwrap().is_some_and(|r|r.state==A2aRunState::Completed) { break; }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.unwrap();
}

#[tokio::test]
async fn unsupported_input_is_a_definite_rejection_before_submission() {
    let f=fixture(Mode::Complete).await;
    for (index,patch) in [
        json!({"attachments":[{"name":"fixture.txt"}]}),
        json!({"message":{"role":"user","content":[{"type":"image","url":"https://example.invalid/image.png"}],"timestamp":1}}),
        json!({"message":{"role":"user","content":[{"type":"text"}],"timestamp":1}}),
    ].into_iter().enumerate() {
        let run=format!("unsupported-input-{index}");let mut cmd=command(&f,&run).await;
        let BcsFrame::Request(frame)=&mut cmd.frame else {panic!("request");};
        for (key,value) in patch.as_object().unwrap() {frame.params.as_mut().unwrap()[key]=value.clone();}
        let result=f.transport.deliver(cmd).await.unwrap();
        assert!(!result.delivered);assert!(result.error.is_some());
        assert!(f.runs.get(&run).await.unwrap().is_none());
    }
    assert_eq!(f.agent.sequence.load(Ordering::SeqCst),0);
    assert!(f.ingest.events.lock().unwrap().is_empty());
}

#[tokio::test]
async fn repeated_unspecified_status_cannot_create_unbounded_queries() {
    let f=fixture(Mode::UnspecifiedFlood).await;
    f.transport.deliver(command(&f,"state-flood").await).await.unwrap();
    tokio::time::timeout(Duration::from_secs(4),async {
        loop {
            let emitted=f.ingest.events.lock().unwrap().iter().any(|event|event.state==ChatEventState::Error);
            if emitted && f.contexts.get_provider_transport("state-flood").await
                ==Some(bcs_service_api::ProviderRunTransport::Terminal) {break;}
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.unwrap();
    assert_eq!(f.agent.queries.load(Ordering::SeqCst),3);
    assert_eq!(f.agent.cancels.lock().unwrap().as_slice(),["remote-1"]);
    assert_eq!(f.runs.get("state-flood").await.unwrap().unwrap().state,A2aRunState::Canceled);
    assert_eq!(f.contexts.get_provider_transport("state-flood").await,
        Some(bcs_service_api::ProviderRunTransport::Terminal));
    assert!(!f.ingest.events.lock().unwrap().iter().any(|event|event.state==ChatEventState::Final));
}

#[tokio::test]
async fn unspecified_status_queries_before_cancellation_and_preserves_accepted_prefix() {
    let f=fixture(Mode::RecoverState).await;
    f.transport.deliver(command(&f,"recover-state").await).await.unwrap();
    completed(&f,"recover-state").await;
    assert_eq!(f.agent.queries.load(Ordering::SeqCst),1);
    assert!(f.agent.cancels.lock().unwrap().is_empty());
    let events=f.ingest.events.lock().unwrap();
    let text:String=events.iter().filter_map(|event|event.event_payload["delta_text"].as_str()).collect();
    assert_eq!(text,"你好世界");
    assert_eq!(events.iter().filter(|event|event.state==ChatEventState::Final).count(),1);
}

#[tokio::test]
async fn cancel_error_retains_protocol_reason_without_a_false_task_terminal() {
    let f=fixture(Mode::CancelDenied).await;
    f.transport.deliver(command(&f,"cancel-denied").await).await.unwrap();
    let error=f.transport.abort(BotAbortDeliveryCommand {command_id:"cancel-denied".into(),target:f.target.clone(),
        run_id:Some("cancel-denied".into()),group_id:"group".into(),session_id:"session".into(),timeout_ms:1000,provider_bypass_headers:vec![]}).await.unwrap_err();
    let diagnostic=error.to_string();
    assert!(diagnostic.contains("TASK_NOT_CANCELABLE"));assert!(diagnostic.contains("409"));
    assert!(!diagnostic.contains("DO_NOT_FORWARD"));
    assert_eq!(f.runs.get("cancel-denied").await.unwrap().unwrap().state,A2aRunState::Running);
    assert!(f.ingest.events.lock().unwrap().is_empty());
    assert_eq!(f.agent.cancels.lock().unwrap().as_slice(),["remote-1"]);
    f.agent.tasks.lock().unwrap().get_mut("remote-1").unwrap()["status"]["state"]=json!("TASK_STATE_COMPLETED");
    let raced=f.transport.abort(BotAbortDeliveryCommand {command_id:"completion-race".into(),target:f.target.clone(),
        run_id:Some("cancel-denied".into()),group_id:"group".into(),session_id:"session".into(),timeout_ms:1000,provider_bypass_headers:vec![]}).await.unwrap();
    assert!(raced.aborted_run_ids.is_empty(),"completion is never reported as cancellation");
    assert!(f.ingest.events.lock().unwrap().is_empty(),"cancel does not fabricate a Final without text calibration");
}

#[tokio::test]
async fn remote_terminal_and_unsupported_wait_states_never_fabricate_success() {
    use bcs_service_api::ChatEventState as State;
    for (remote,expected,stored) in [
        ("TASK_STATE_COMPLETED",State::Final,A2aRunState::Completed),
        ("TASK_STATE_FAILED",State::Error,A2aRunState::Failed),
        ("TASK_STATE_REJECTED",State::Error,A2aRunState::Failed),
        ("TASK_STATE_CANCELED",State::Aborted,A2aRunState::Canceled),
        ("TASK_STATE_AUTH_REQUIRED",State::Error,A2aRunState::Canceled),
        ("TASK_STATE_INPUT_REQUIRED",State::Error,A2aRunState::Canceled),
        ("TASK_STATE_UNSPECIFIED",State::Error,A2aRunState::Canceled),
    ] {
        let f=fixture(Mode::Terminal(remote)).await;
        f.transport.deliver(command(&f,"state-mapping").await).await.unwrap();
        tokio::time::timeout(Duration::from_secs(4),async {
            loop {
                let persisted=f.runs.get("state-mapping").await.unwrap().unwrap().state.is_terminal();
                let published=f.ingest.events.lock().unwrap().iter().any(|event|matches!(event.state,State::Final | State::Error | State::Aborted));
                if persisted && published {break;}
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }).await.unwrap();
        assert_eq!(f.runs.get("state-mapping").await.unwrap().unwrap().state,stored,"{remote}");
        let events=f.ingest.events.lock().unwrap();
        let terminals:Vec<_>=events.iter().filter(|event|matches!(event.state,State::Final | State::Error | State::Aborted)).collect();
        assert_eq!(terminals.len(),1,"{remote}");assert_eq!(terminals[0].state,expected,"{remote}");
        if !matches!(remote,"TASK_STATE_COMPLETED" | "TASK_STATE_FAILED" | "TASK_STATE_REJECTED" | "TASK_STATE_CANCELED") {
            assert_eq!(f.agent.cancels.lock().unwrap().as_slice(),["remote-1"],"unsupported wait/status cancels the actual Task");
        }
    }
}

#[tokio::test]
async fn standalone_agent_message_finishes_but_cannot_invent_a_cancelable_task() {
    let f=fixture(Mode::Message).await;
    assert!(f.transport.deliver(command(&f,"message-only").await).await.unwrap().delivered);
    let row=f.runs.get("message-only").await.unwrap().unwrap();
    assert_eq!(row.state,A2aRunState::Completed);assert!(row.remote_task_id.is_none());
    let events=f.ingest.events.lock().unwrap();
    assert_eq!(events.len(),2);assert_eq!(events[0].event_payload["delta_text"],"无任务回复");
    assert_eq!(events[1].state,ChatEventState::Final);drop(events);
    let result=f.transport.abort(BotAbortDeliveryCommand {command_id:"cannot-cancel-message".into(),target:f.target.clone(),
        run_id:Some("message-only".into()),group_id:"group".into(),session_id:"session".into(),timeout_ms:1000,provider_bypass_headers:vec![]}).await;
    assert!(result.unwrap().aborted_run_ids.is_empty());assert!(f.agent.cancels.lock().unwrap().is_empty());
}

#[tokio::test]
async fn initial_http_rejection_preserves_safe_error_fields_but_server_errors_remain_unknown() {
    use bcs_service_api::types::a2a_bot::A2aExtensionDeclaration;
    for status in [400,401,403,408,409,429,503] {
        let rejected=matches!(status,400 | 401 | 403);
        let mut f=fixture(Mode::HttpError(status)).await;
        if let BotDeliveryTarget::A2a {extensions,..}=&mut f.target {
            extensions.push(A2aExtensionDeclaration {uri:bcs_a2a_http::delivery::DEDUP_EXTENSION.into(),required:None,params:Value::Null});
        }
        let result=f.transport.deliver(command(&f,"http-error").await).await;
        let row=f.runs.get("http-error").await.unwrap().unwrap();
        if rejected {
            assert!(!result.unwrap().delivered);assert_eq!(row.state,A2aRunState::Failed);
            assert_eq!(f.agent.sequence.load(Ordering::SeqCst),1,"a complete rejection is never retried");
            let events=f.ingest.events.lock().unwrap();assert_eq!(events.len(),1);
            let payload=&events[0].event_payload;
            assert_eq!(payload["errorKind"],"a2a_protocol");assert_eq!(payload["errorCode"],"CONTENT_TYPE_NOT_SUPPORTED");
            let diagnostic:Value=serde_json::from_str(payload["errorMessage"].as_str().unwrap()).unwrap();
            assert_eq!(diagnostic["httpStatus"],status);
            assert_eq!(diagnostic["details"][0]["reason"],"CONTENT_TYPE_NOT_SUPPORTED");
            assert!(!payload.to_string().contains("DO_NOT_FORWARD"));
        } else {
            assert!(result.is_err());assert_eq!(row.state,A2aRunState::Unknown);
            assert_eq!(f.agent.sequence.load(Ordering::SeqCst),2,"only negotiated identical retry is allowed");
            assert!(f.ingest.events.lock().unwrap().is_empty(),"uncertain work has no false terminal");
        }
        assert!(row.remote_task_id.is_none());
    }
}

#[tokio::test]
async fn streams_under_local_identity_and_reuses_durable_context_on_next_round() {
    let f=fixture(Mode::Complete).await;
    assert!(f.transport.deliver(command(&f,"local-1").await).await.unwrap().delivered);
    assert_eq!(f.runs.get("local-1").await.unwrap().unwrap().remote_task_id.as_deref(),Some("remote-1"));
    completed(&f,"local-1").await;
    assert!(f.transport.deliver(command(&f,"local-2").await).await.unwrap().delivered);
    completed(&f,"local-2").await;
    let requests=f.agent.requests.lock().unwrap();
    assert_eq!(requests[0]["message"]["messageId"],"local-1");
    assert_eq!(requests[0]["message"]["role"],"ROLE_USER");
    assert!(requests[0]["message"].get("contextId").is_none());
    assert_eq!(requests[1]["message"]["contextId"],"remote-context");
    assert!(!requests[0].to_string().contains("bcs_group_id"));
    let events=f.ingest.events.lock().unwrap();
    for run in ["local-1","local-2"] {
        let run_events=events.iter().filter(|event|event.run_id==run).collect::<Vec<_>>();
        assert!(run_events.iter().all(|e|e.bot_id=="bot" && e.group_id=="group" && e.bcs_session_id.as_deref()==Some("session")));
        let text=run_events.iter().filter_map(|event|event.event_payload.get("delta_text").and_then(Value::as_str)).collect::<String>();
        assert_eq!(text,"你好世界");
        assert_eq!(run_events.iter().filter(|e|e.state==ChatEventState::Final).count(),1);
    }
}

#[tokio::test]
async fn abort_uses_remote_task_id_and_rejects_foreign_scope_before_rpc() {
    let f=fixture(Mode::Wait).await;
    assert!(f.transport.deliver(command(&f,"local-run").await).await.unwrap().delivered);
    let mut abort=BotAbortDeliveryCommand { target:f.target.clone(),command_id:"abort".into(),group_id:"group".into(),session_id:"wrong-session".into(),
        run_id:Some("local-run".into()),provider_bypass_headers:vec![],timeout_ms:1000 };
    assert!(f.transport.abort(abort.clone()).await.is_err());
    assert!(f.agent.cancels.lock().unwrap().is_empty());
    abort.session_id="session".into();
    if let BotDeliveryTarget::A2a {interface,..}=&mut abort.target {
        interface.url="http://127.0.0.1:1/new-unavailable-route".into();
    }
    // An exact canonical Run retains the submitted route; a later current Card
    // endpoint cannot redirect cancellation or make it depend on that endpoint.
    assert_eq!(f.transport.abort(abort.clone()).await.unwrap().aborted_run_ids,vec!["local-run"]);
    assert_eq!(f.transport.abort(abort).await.unwrap().aborted_run_ids,vec!["local-run"]);
    assert_eq!(*f.agent.cancels.lock().unwrap(),vec!["remote-1"]);
    assert_eq!(f.runs.get("local-run").await.unwrap().unwrap().state,A2aRunState::Canceled);
}

#[tokio::test]
async fn first_task_loss_is_unknown_and_never_resubmits_without_dedup_negotiation() {
    let f=fixture(Mode::MissingInitialTask).await;
    assert!(f.transport.deliver(command(&f,"local-run").await).await.is_err());
    assert_eq!(f.runs.get("local-run").await.unwrap().unwrap().state,A2aRunState::Unknown);
    assert!(f.transport.deliver(command(&f,"local-run").await).await.is_err());
    assert_eq!(f.agent.requests.lock().unwrap().len(),1);
    assert!(f.agent.cancels.lock().unwrap().is_empty());
}

#[tokio::test]
async fn negotiates_tools_and_ingests_each_observation_once_between_text_deltas() {
    let mut f=fixture(Mode::Complete).await;
    if let BotDeliveryTarget::A2a { extensions, .. }=&mut f.target {
        extensions.push(bcs_service_api::types::a2a_bot::A2aExtensionDeclaration {
            uri:bcs_a2a_http::tools::EXTENSION.into(),required:Some(false),params:Value::Null });
    }
    f.transport.deliver(command(&f,"tool-run").await).await.unwrap();
    completed(&f,"tool-run").await;
    assert_eq!(f.runs.get("tool-run").await.unwrap().unwrap().extensions,vec![bcs_a2a_http::tools::EXTENSION]);
    let events=f.ingest.events.lock().unwrap();
    assert_eq!(events.iter().map(|event|event.state.clone()).collect::<Vec<_>>(),vec![
        ChatEventState::Delta,ChatEventState::ToolCallStart,ChatEventState::ToolCallEnd,ChatEventState::Delta,ChatEventState::Final]);
    assert_eq!(events[1].event_payload["data"]["name"],"query_order");
    assert_eq!(events[2].event_payload["run_id"],"tool-run");
    assert_eq!(events[1].event_type,"agent");
    assert_eq!(events[1].event_payload["stream"],"tool");
    assert_eq!(events[1].event_payload["data"]["toolCallId"],"call");
    assert_eq!(events[2].event_payload["data"]["result"]["content"][0]["text"],"ok");
}

#[tokio::test]
async fn disconnected_stream_retries_transient_query_and_calibrates_final_suffix() {
    let f=fixture(Mode::RecoverGet).await;
    f.transport.deliver(command(&f,"recover-run").await).await.unwrap();
    completed(&f,"recover-run").await;
    assert_eq!(f.agent.queries.load(Ordering::SeqCst),2);
    assert_eq!(f.agent.requests.lock().unwrap().len(),1);
    let events=f.ingest.events.lock().unwrap();
    let text=events.iter().filter_map(|event|event.event_payload.get("delta_text").and_then(Value::as_str)).collect::<String>();
    assert_eq!(text,"你好世界");
    assert_eq!(events.iter().filter(|event|event.state==ChatEventState::Final).count(),1);
}

#[tokio::test]
async fn shared_a2a_delivery_port_conformance() {
    let f=fixture(Mode::Wait).await;
    let abort=BotAbortDeliveryCommand { target:f.target.clone(),command_id:"abort".into(),group_id:"group".into(),
        session_id:"session".into(),run_id:Some("contract-run".into()),provider_bypass_headers:vec![],timeout_ms:1000 };
    bcs_test_support::contract::port::a2a_delivery::bot_delivery_port_contract_tests(
        &f.transport,command(&f,"contract-run").await,abort).await;
    assert_eq!(f.agent.requests.lock().unwrap().len(),1);
    assert_eq!(*f.agent.cancels.lock().unwrap(),vec!["remote-1"]);
}

#[tokio::test]
async fn first_task_loss_retries_identical_submission_only_with_negotiated_dedup() {
    let mut f=fixture(Mode::DedupRetry).await;
    if let BotDeliveryTarget::A2a { extensions, .. }=&mut f.target {
        extensions.push(bcs_service_api::types::a2a_bot::A2aExtensionDeclaration {
            uri:bcs_a2a_http::delivery::DEDUP_EXTENSION.into(),required:Some(false),params:Value::Null });
    }
    assert!(f.transport.deliver(command(&f,"retry-run").await).await.unwrap().delivered);
    completed(&f,"retry-run").await;
    let requests=f.agent.requests.lock().unwrap();
    assert_eq!(requests.len(),2);assert_eq!(requests[0],requests[1]);
    assert_eq!(requests[0]["message"]["messageId"],"retry-run");
    assert_eq!(f.runs.get("retry-run").await.unwrap().unwrap().remote_task_id.as_deref(),Some("remote-1"));
    let events=f.ingest.events.lock().unwrap();
    assert_eq!(events.iter().filter_map(|event|event.event_payload.get("delta_text").and_then(Value::as_str)).collect::<String>(),"你好世界");
}

#[tokio::test]
async fn run_abort_does_not_cancel_another_active_binding_of_the_same_task() {
    let f=fixture(Mode::Wait).await;
    f.transport.deliver(command(&f,"shared-abort-1").await).await.unwrap();
    let first=f.runs.get("shared-abort-1").await.unwrap().unwrap();
    let mut second=first.clone();second.run_id="shared-abort-2".into();second.message_id="follow-up".into();
    second.remote_task_id=None;second.task_version=None;second.pending_input=None;
    second.state=A2aRunState::Submitting;second.version=1;
    assert!(f.runs.create(&second).await.unwrap());
    second.remote_task_id=first.remote_task_id.clone();second.state=A2aRunState::Running;second.version=2;
    f.runs.compare_and_set(&second,1).await.unwrap().unwrap();
    let error=f.transport.abort(BotAbortDeliveryCommand {command_id:"shared-abort".into(),target:f.target.clone(),
        run_id:Some(first.run_id.clone()),group_id:"group".into(),session_id:"session".into(),timeout_ms:1000,provider_bypass_headers:vec![]}).await.unwrap_err();
    assert!(error.to_string().contains("another active Run"));
    assert!(f.agent.cancels.lock().unwrap().is_empty());
    for run in [&first.run_id,&second.run_id] {assert_eq!(f.runs.get(run).await.unwrap().unwrap().state,A2aRunState::Running);}
}

#[tokio::test]
async fn valid_recovered_events_reset_disconnect_budget_for_long_tasks() {
    let f=fixture(Mode::Flapping).await;
    f.transport.deliver(command(&f,"flapping").await).await.unwrap();
    completed(&f,"flapping").await;
    assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),5);
    assert_eq!(f.agent.sequence.load(Ordering::SeqCst),1,"recovery never resubmits work");
    assert!(f.agent.cancels.lock().unwrap().is_empty());
    let events=f.ingest.events.lock().unwrap();
    let text:String=events.iter().filter_map(|e|e.event_payload["delta_text"].as_str()).collect();
    assert_eq!(text,"你好世界");
    assert_eq!(events.iter().filter(|e|e.state==ChatEventState::Final).count(),1);
}

#[tokio::test]
async fn empty_successful_subscriptions_do_not_reset_consecutive_failure_budget() {
    let f=fixture(Mode::EmptyRecoveries).await;
    f.transport.deliver(command(&f,"empty-recoveries").await).await.unwrap();
    tokio::time::timeout(Duration::from_secs(5),async {
        while f.contexts.get_provider_transport("empty-recoveries").await
            !=Some(bcs_service_api::ProviderRunTransport::Terminal) {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.unwrap();
    assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),3);
    assert_eq!(f.agent.sequence.load(Ordering::SeqCst),1);
    assert_eq!(f.agent.cancels.lock().unwrap().as_slice(),["remote-1"]);
    assert_eq!(f.runs.get("empty-recoveries").await.unwrap().unwrap().state,A2aRunState::Canceled);
    assert_eq!(f.ingest.events.lock().unwrap().iter().filter(|e|e.state==ChatEventState::Error).count(),1);
}
