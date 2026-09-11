//! Opt-in, isolated file-backed SQLite worker load test. No network or live DB.
//! cargo test -p bcs --release --test delivery_worker_load -- --ignored --nocapture
use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_domain::{BotDeliveryTarget, DeliveryType, NewMessage, SenderType};
use bcs_message_flow::delivery_runtime::{DeliveryRuntime, DeliveryRuntimeConfig, DeliveryRuntimePolicy};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_store::MySqlMessageStore;
use bcs_protocol::{BcsFrame, RequestFrame};
use bcs_service_api::application::message_delivery::DeliveryInstrumentation;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent;
use bcs_service_api::port::repo::message_delivery::*;
use bcs_service_api::*;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[path = "support/reply_load.rs"]
mod reply_load;

#[derive(Default)]
struct Measurements {
    starts: BTreeMap<String, Instant>,
    first_bot: BTreeMap<String, Instant>,
    active_lanes: BTreeSet<(String, String)>,
    active_bots: BTreeMap<String, usize>,
    last_seq: BTreeMap<(String, String), i64>,
    completed: usize,
    peak_bot: usize,
    latencies_ms: Vec<f64>,
    operations: BTreeMap<&'static str, (usize, f64, usize, usize)>,
    errors: Vec<String>,
}
#[derive(Default)]
struct Probe(Mutex<Measurements>);
impl DeliveryInstrumentation for Probe {
    fn operation(&self, name: &'static str, seconds: f64, rows: usize, success: bool) {
        let mut m = self.0.lock().unwrap();
        let op = m.operations.entry(name).or_default();
        op.0 += 1; op.1 += seconds; op.2 = op.2.max(rows); op.3 += usize::from(!success);
    }
    fn event(&self, event: &'static str, row: &PersistedMessageDelivery) {
        let mut m = self.0.lock().unwrap();
        let lane = (row.target_bot_id.clone(), row.session_id.clone());
        if event == "started" {
            if m.starts.insert(row.delivery_id.clone(), Instant::now()).is_some() {
                m.errors.push("duplicate dispatch".into());
            }
            if !m.active_lanes.insert(lane.clone()) { m.errors.push("overlapping lane".into()); }
            if m.last_seq.insert(lane, row.source_session_seq).is_some_and(|seq| seq >= row.source_session_seq) {
                m.errors.push("non-FIFO dispatch".into());
            }
            let active = m.active_bots.entry(row.target_bot_id.clone()).or_default();
            *active += 1; let active = *active;
            m.peak_bot = m.peak_bot.max(active);
            m.first_bot.entry(row.target_bot_id.clone()).or_insert_with(Instant::now);
        } else if event == "terminal" {
            m.active_lanes.remove(&lane);
            *m.active_bots.get_mut(&row.target_bot_id).unwrap() -= 1;
            if let Some(start) = m.starts.get(&row.delivery_id) {
                let elapsed = start.elapsed().as_secs_f64() * 1000.; m.latencies_ms.push(elapsed);
            }
            m.completed += 1;
        }
    }
}

struct MockIo {
    service: Arc<ManagedMessageDelivery>,
    completions: Mutex<Vec<tokio::task::JoinHandle<()>>>,
    offline: bool,
}
#[async_trait]
impl ManagedDeliveryPreparationService for MockIo {
    async fn is_available(&self, bot: &str) -> bool { !(self.offline && bot == "bot000") }
    async fn prepare(&self, row: &PersistedMessageDelivery) -> ServiceResult<PreparedManagedDelivery> {
        Ok(PreparedManagedDelivery {
            command: BotDeliveryCommand {
                target: BotDeliveryTarget::WebSocket { bot_id: row.target_bot_id.clone() },
                run_id: row.run_id.clone().unwrap(),
                frame: BcsFrame::Request(RequestFrame::new("prepared", "chat.send", None)),
                delivery_kind: BotDeliveryKind::Send, provider_transport: Default::default(),
                provider_bypass_headers: vec![],
            },
            transport_context_json: serde_json::json!({"version":1,"kind":"websocket"}),
        })
    }
    async fn before_send(&self, _: &PersistedMessageDelivery, _: &BotDeliveryCommand) -> ServiceResult<()> { Ok(()) }
    async fn prepare_abort(&self, _: &PersistedMessageDelivery) -> ServiceResult<BotAbortDeliveryCommand> {
        Err(ServiceError::InternalError("abort outside this benchmark".into()))
    }
}
#[async_trait]
impl BotDeliveryPort for MockIo {
    async fn is_available(&self, _: &BotDeliveryTarget) -> bool { true }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let BotDeliveryTarget::WebSocket { bot_id } = cmd.target else { panic!("unexpected provider") };
        let BcsFrame::Request(frame) = cmd.frame else { panic!("unexpected frame") };
        let service = self.service.clone();
        let request = frame.id;
        self.completions.lock().unwrap().push(tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            // Read current version after transport handoff; no global snapshot.
            for _ in 0..16 {
                let row = service.lookup(DeliveryLookup::Request(request.clone())).await.unwrap().remove(0);
                let result = service.transition(DeliveryTransitionCommand {
                    delivery_id: row.delivery_id, expected_state_version: row.state.state_version,
                    event: DeliveryLifecycleEvent::Completed, now_ms: chrono::Utc::now().timestamp_millis(),
                    request_id: None, actor_id: None, reply: None, transport_context_json: None, deadline_at_ms: None,
                }).await;
                match result {
                    Ok(_) => return,
                    Err(ManagedDeliveryError::Conflict)
                    | Err(ManagedDeliveryError::Repository(MessageDeliveryRepoError::Conflict))
                    | Err(ManagedDeliveryError::Lifecycle(bcs_service_api::core::message_delivery::DeliveryLifecycleError::StaleVersion { .. })) => {
                        // The transport-result update may win between read and CAS.
                        // Retry only terminal projection, never the send itself.
                        tokio::time::sleep(Duration::from_millis(1)).await;
                    }
                    Err(error) => panic!("mock terminal failed: {error}"),
                }
            }
            panic!("mock terminal version conflicts did not settle");
        }));
        Ok(BotDeliveryResult { target_bot_id: bot_id, delivered: true, error: None })
    }
    async fn abort(&self, _: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        Err(ServiceError::InternalError("abort outside this benchmark".into()))
    }
}

async fn scene(name: &str, bots: usize, sessions: usize, cap: usize, ballast: bool, offline: bool) -> anyhow::Result<()> {
    println!("LOAD_BEGIN {name}");
    let temp = tempfile::tempdir()?;
    let db = Arc::new(LocalSqliteDbPlugin::new_file(temp.path().join("load.db"))?);
    bcs::migrations::run_sqlite_migrations(db.as_ref()).await?;
    if ballast {
        // Query-only background fixtures in unrelated lanes; never dispatched.
        db.execute(DbStatement::new("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<110000) INSERT INTO bcs_message_deliveries (delivery_id,env,source_message_id,target_bot_id,session_id,group_id,source_session_seq,flow_kind,kind,status,state_version,may_have_been_sent,available_at_ms,created_at_ms,updated_at_ms,attempt_no,semantic_projection_json) SELECT printf('ballast%06d',x),'dev',printf('old%06d',x),'background','background','group',x,'group',CASE WHEN x<=100000 THEN 'send' ELSE 'inject' END,CASE WHEN x<=100000 THEN 'completed' ELSE 'pending_context' END,1,0,1,1,1,0,'{}' FROM n")).await?;
    }
    let probe = Arc::new(Probe::default());
    let service = Arc::new(ManagedMessageDelivery::new(Arc::new(MySqlMessageStore::sqlite(db.clone(), "dev".into()))).with_instrumentation(probe.clone()));
    let admission_start = Instant::now();
    for bot in 0..bots {
        for session in 0..sessions {
            let sid = format!("session{bot:03}-{session:03}");
            db.execute(DbStatement::with_params("INSERT INTO bcs_group_sessions (session_id,group_id,env,participants) VALUES (?,'group','dev','[]')", vec![bcs_db_api::DbValue::String(sid.clone())])).await?;
            for n in 0..10 {
                let id = format!("message{bot:03}-{session:03}-{n:03}");
                let now = chrono::Utc::now().timestamp_millis();
                service.admit(AdmitMessageDeliveries {
                    display_message: None,
                    message_id: id.clone(), flow_kind: DeliveryFlowKind::Group, now_ms: now,
                    expire_at_ms: None, event: None,
                    message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None, group_id: "group".into(), session_id: sid.clone(), sender_id: "human".into(), sender_type: SenderType::Human, message_type: "chat".into(), content: serde_json::json!({"text":id}), client_msg_id: Some(id), owner_bot_id: None, created_at: now as u64, run_id: String::new() },
                    targets: vec![DeliveryAdmissionTarget { target_bot_id: format!("bot{bot:03}"), kind: DeliveryType::Send, max_queued: 10000, semantic_projection_json: serde_json::json!({"version":1}) }],
                }).await?;
            }
        }
    }
    let admission_seconds = admission_start.elapsed().as_secs_f64();
    probe.0.lock().unwrap().operations.clear();
    let io = Arc::new(MockIo { service: service.clone(), completions: Mutex::new(vec![]), offline });
    let worker = DeliveryRuntime { policy: None, service: service.clone(), preparation: io.clone(), transport: io.clone(), config: DeliveryRuntimeConfig {
        max_safe_retries: 0, pause_dispatch: false,
        bots: (0..bots).map(|b| (format!("bot{b:03}"), DeliveryRuntimePolicy { max_running: cap, min_send_interval_ms: 0 })).collect(),
        tick: Duration::from_millis(100), io_timeout: Duration::from_secs(10), run_timeout: Duration::from_secs(120), cancel_timeout: Duration::from_secs(10), max_tasks: 32, max_abort_tasks: 2,
    }};
    let expected = (bots - usize::from(offline)) * sessions * 10;
    let start = Instant::now();
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    while start.elapsed() < Duration::from_secs(60) && probe.0.lock().unwrap().completed < expected {
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    let seconds = start.elapsed().as_secs_f64();
    stop.send(true)?; task.await??;
    let handles = std::mem::take(&mut *io.completions.lock().unwrap());
    for handle in handles { handle.await?; }
    let counts = db.query(DbStatement::new("SELECT status, count(*) AS n FROM bcs_message_deliveries WHERE target_bot_id != 'background' GROUP BY status")).await?;
    let counts: BTreeMap<String, i64> = counts.iter().map(|r| Ok((bcs_db_api::db_get_column(r, "status")?, bcs_db_api::db_get_column(r, "n")?))).collect::<bcs_db_api::DbResult<_>>()?;
    let m = probe.0.lock().unwrap();
    let mut latency = m.latencies_ms.clone(); latency.sort_by(f64::total_cmp);
    let percentile = |p: f64| latency.get(((latency.len().saturating_sub(1)) as f64 * p) as usize).copied().unwrap_or(0.);
    let first_max = m.first_bot.values().map(|t| t.duration_since(start).as_secs_f64()).fold(0., f64::max);
    println!("LOAD_RESULT {}", serde_json::json!({"scene":name,"bots":bots,"sessions_per_bot":sessions,"cap":cap,"admitted":bots*sessions*10,"completed":m.completed,"seconds":seconds,"completed_per_second":m.completed as f64/seconds,"admission_seconds":admission_seconds,"first_dispatch_all_bots_seconds":first_max,"dispatch_to_terminal_ms_p50":percentile(0.5),"dispatch_to_terminal_ms_p95":percentile(0.95),"dispatch_to_terminal_ms_p99":percentile(0.99),"peak_active_per_bot":m.peak_bot,"operations":m.operations,"states":counts,"errors":m.errors}));
    assert_eq!(m.completed, expected, "did not drain within 60 seconds");
    assert_eq!(m.starts.len(), expected);
    assert_eq!(m.first_bot.len(), bots - usize::from(offline));
    assert!(m.errors.is_empty(), "{:?}", m.errors);
    assert!(m.peak_bot <= cap);
    for (operation, limit) in [("queued_bots", 32), ("queued_heads", 8), ("control_batch", 32), ("expiry_batch", 100), ("recovery_batch", 200)] {
        if let Some(stats) = m.operations.get(operation) { assert!(stats.2 <= limit, "unbounded {operation}"); }
    }
    assert_eq!(counts.get("completed").copied(), Some(expected as i64));
    assert_eq!(counts.get("queued").copied().unwrap_or(0), if offline { (sessions * 10) as i64 } else { 0 });
    assert!(counts.keys().all(|state| state == "completed" || state == "queued"));
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "manual isolated load test"]
async fn isolated_worker_load() -> anyhow::Result<()> {
    scene("baseline", 32, 8, 4, false, false).await?;
    scene("many_bots", 128, 4, 4, false, false).await?;
    scene("history_ballast", 32, 8, 4, true, false).await?;
    scene("offline_and_cap_one", 32, 8, 1, false, true).await?;
    Ok(())
}
