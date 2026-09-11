//! Real application final handling and SQLite terminal transactions; synthetic Bot only.
use super::*;
use bcs_message_flow::BcsMessageFlow;
use bcs_service_api::port::repo::MessageRepoPort;
use serde_json::json;
use tracing::Instrument;

#[derive(Default)]
struct TimingFields { stage: Option<String>, run: Option<String>, elapsed: Option<u64> }
impl tracing::field::Visit for TimingFields {
    fn record_debug(&mut self, _: &tracing::field::Field, _: &dyn std::fmt::Debug) {}
    fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
        match field.name() { "stage" => self.stage = Some(value.into()), "run_id" => self.run = Some(value.into()), _ => {} }
    }
    fn record_u64(&mut self, field: &tracing::field::Field, value: u64) { if field.name() == "elapsed_us" { self.elapsed = Some(value); } }
}
type RunTimings = BTreeMap<String, BTreeMap<String, u64>>;
static PROFILE: std::sync::OnceLock<Arc<Mutex<RunTimings>>> = std::sync::OnceLock::new();
struct TimingLayer(Arc<Mutex<RunTimings>>);
impl<S> tracing_subscriber::Layer<S> for TimingLayer where S: tracing::Subscriber + for<'a> tracing_subscriber::registry::LookupSpan<'a> {
    fn on_new_span(&self, attrs: &tracing::span::Attributes<'_>, id: &tracing::Id, ctx: tracing_subscriber::layer::Context<'_, S>) {
        let mut fields = TimingFields::default(); attrs.record(&mut fields);
        if let (Some(run), Some(span)) = (fields.run, ctx.span(id)) { span.extensions_mut().insert(run); }
    }
    fn on_event(&self, event: &tracing::Event<'_>, ctx: tracing_subscriber::layer::Context<'_, S>) {
        let mut fields = TimingFields::default(); event.record(&mut fields);
        let Some(scope) = ctx.event_scope(event) else { return; };
        let mut run = None;
        let mut writer_held = false;
        for span in scope {
            if span.metadata().name() == "repo_writer_held" { writer_held = true; }
            if let Some(id) = span.extensions().get::<String>() { run = Some(id.clone()); }
        }
        if let (Some(run), Some(stage), Some(elapsed)) = (run, fields.stage, fields.elapsed) {
            let stage = if stage.starts_with("sql.") { format!("{}.{}", if writer_held { "writer" } else { "outside" }, stage) } else { stage };
            let mut data = self.0.lock().unwrap();
            let parts = data.entry(run).or_default();
            if stage.contains(".sql.") { *parts.entry(format!("calls.{stage}")).or_default() += 1; }
            *parts.entry(stage).or_default() += elapsed;
        }
    }
}
fn install_profile() {
    if std::env::var("BCS_REPLY_PROFILE").as_deref() != Ok("1") { return; }
    if PROFILE.get().is_some() { return; }
    use tracing_subscriber::prelude::*;
    let data = Arc::new(Mutex::new(BTreeMap::new()));
    PROFILE.set(data.clone()).unwrap();
    let layer = TimingLayer(data).with_filter(tracing_subscriber::filter::filter_fn(|meta| meta.target() == "bcs_reply_profile"));
    tracing::subscriber::set_global_default(tracing_subscriber::registry().with(layer)).unwrap();
}
fn report_profile(scene: &str) {
    let Some(profile) = PROFILE.get() else { return; };
    let runs = std::mem::take(&mut *profile.lock().unwrap());
    let mut stages: BTreeMap<String, Vec<u64>> = BTreeMap::new();
    for parts in runs.values() { for (stage, us) in parts { stages.entry(stage.clone()).or_default().push(*us); } }
    let mut counts = BTreeMap::new();
    stages.retain(|stage, samples| {
        if let Some(sql) = stage.strip_prefix("calls.") {
            counts.insert(sql.to_string(), json!({"total":samples.iter().sum::<u64>(),"runs_with_calls":samples.len(),"max_per_run":samples.iter().max()})); false
        } else { true }
    });
    let stats: BTreeMap<_, _> = stages.into_iter().map(|(stage, mut samples)| {
        samples.sort_unstable();
        let p = |q: f64| samples[((samples.len()-1) as f64*q) as usize] as f64 / 1000.;
        (stage, json!({"runs":samples.len(),"p50_ms":p(0.5),"p95_ms":p(0.95),"p99_ms":p(0.99),"max_ms":p(1.0)}))
    }).collect();
    let mut slow: Vec<_> = runs.into_iter().collect();
    for (_, parts) in &mut slow { parts.retain(|stage, _| !stage.starts_with("calls.")); }
    slow.sort_by_key(|(_, parts)| std::cmp::Reverse(parts.get("final.total").copied().unwrap_or(0)));
    slow.truncate(5);
    println!("REPLY_PROFILE {}", json!({"scene":scene,"per_run_stage_sums":stats,"sql_calls":counts,"slowest_runs_us":slow}));
}

#[path = "../../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "../../../../services/bcs-message-flow/tests/support/session.rs"]
mod session_support;

struct EventFactory;
impl bcs_service_api::port::EventRecordFactoryPort for EventFactory {
    fn prepare(&self, event: bcs_service_api::port::NewEvent) -> Result<Option<bcs_service_api::port::repo::AppendEventRecord>, bcs_service_api::port::EventRecordError> {
        Ok(Some(bcs_service_api::port::repo::AppendEventRecord { event, env: "dev".into(), recorded_at: chrono::Utc::now().to_rfc3339(), retention_until_ms: (chrono::Utc::now().timestamp_millis() + 86_400_000) as u64 }))
    }
}

struct DiscardFrontend;
#[async_trait]
impl FrontendDeliveryPort for DiscardFrontend {
    async fn publish(&self, cmd: FrontendDeliveryCommand) -> ServiceResult<FrontendDeliveryResult> {
        assert!(!cmd.event_json.contains("\"normalization\""));
        Ok(FrontendDeliveryResult { target: cmd.target, delivered: 1 })
    }
    async fn unregister_run(&self, _: &str) -> ServiceResult<()> { Ok(()) }
}

struct ReplyMock {
    base: MockIo,
    flows: BTreeMap<String, Arc<BcsMessageFlow>>,
    callbacks: Mutex<Vec<tokio::task::JoinHandle<()>>>,
    final_ms: Arc<Mutex<Vec<f64>>>,
    text_size: usize,
}

#[async_trait]
impl ManagedDeliveryPreparationService for ReplyMock {
    async fn is_available(&self, bot: &str) -> bool { ManagedDeliveryPreparationService::is_available(&self.base, bot).await }
    async fn prepare(&self, row: &PersistedMessageDelivery) -> ServiceResult<PreparedManagedDelivery> {
        // Keep input preparation identical to the original worker baseline.
        self.base.prepare(row).await
    }
    async fn before_send(&self, _: &PersistedMessageDelivery, _: &BotDeliveryCommand) -> ServiceResult<()> { Ok(()) }
    async fn prepare_abort(&self, row: &PersistedMessageDelivery) -> ServiceResult<BotAbortDeliveryCommand> { self.base.prepare_abort(row).await }
}

#[async_trait]
impl BotDeliveryPort for ReplyMock {
    async fn is_available(&self, _: &BotDeliveryTarget) -> bool { true }
    async fn abort(&self, _: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> { Err(ServiceError::InternalError("abort outside benchmark".into())) }
    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let bot = cmd.target_bot_id().to_string();
        let BcsFrame::Request(frame) = cmd.frame else { panic!("request required") };
        let row = self.base.service.lookup(DeliveryLookup::Request(frame.id)).await.unwrap().remove(0);
        let flow = self.flows[&row.session_id].clone();
        let timings = self.final_ms.clone();
        let size = self.text_size;
        self.callbacks.lock().unwrap().push(tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(50)).await;
            let run = row.run_id.as_ref().unwrap();
            let event = |state, event_type: &str, payload| BotEventCommand {
                bot_id: row.target_bot_id.clone(), run_id: run.clone(), group_id: row.group_id.clone(),
                bcs_session_id: Some(row.session_id.clone()), state, event_type: event_type.into(), event_payload: payload,
            };
            let prefix = format!("工具前{}", "a".repeat(size / 2));
            let current = format!("工具后{}", "b".repeat(size / 2));
            flow.handle_bot_event(event(ChatEventState::Delta, "chat", json!({"delta_text":prefix}))).await.unwrap();
            flow.handle_bot_event(event(ChatEventState::Delta, "agent", json!({"stream":"tool","data":{"phase":"result","name":"search","toolCallId":run,"result":"TOOL_BODY_NOT_REPLY"}}))).await.unwrap();
            flow.handle_bot_event(event(ChatEventState::Delta, "chat", json!({"delta_text":current}))).await.unwrap();
            let mode = row.source_session_seq % 4;
            let suffix = if mode == 3 { "" } else { "补充" };
            let expected = format!("{prefix}\n{current}{suffix}");
            let final_text = match mode { 0 => expected.clone(), 1 => format!("{current}{suffix}"), 2 => suffix.into(), _ => String::new() };
            let terminal = event(ChatEventState::Final, "chat", json!({"message":{"role":"assistant","content":[{"type":"text","text":final_text}]}}));
            let started = Instant::now();
            async {
                let measured = Instant::now();
                flow.handle_bot_event(terminal.clone()).await.unwrap();
                tracing::debug!(target: "bcs_reply_profile", stage = "final.total", elapsed_us = measured.elapsed().as_micros() as u64, "final complete");
            }.instrument(tracing::debug_span!(target: "bcs_reply_profile", "profile_final", run_id = run.as_str())).await;
            timings.lock().unwrap().push(started.elapsed().as_secs_f64() * 1000.);
            // All persistence assertions and duplicate-final probes run after
            // drain, so validation reads do not contend with the measured load.
        }));
        Ok(BotDeliveryResult { target_bot_id: bot, delivered: true, error: None })
    }
}

async fn reply_scene(name: &str, bots: usize, sessions: usize, offline: bool, size: usize, history: usize) -> anyhow::Result<()> {
    println!("REPLY_LOAD_BEGIN {name}");
    let temp = tempfile::tempdir()?;
    let db = Arc::new(LocalSqliteDbPlugin::new_file(temp.path().join("reply.db"))?);
    bcs::migrations::run_sqlite_migrations(db.as_ref()).await?;
    let probe = Arc::new(Probe::default());
    let repo = Arc::new(MySqlMessageStore::sqlite(db.clone(), "dev".into()));
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()).with_instrumentation(probe.clone()));
    let fixture = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut flows = BTreeMap::new();
    let admission = Instant::now();
    for b in 0..bots {
        let bot = format!("bot{b:03}");
        let observer = format!("observer{b:03}");
        fixture.registry.insert_named_actor(&bot, &bot).await;
        fixture.registry.insert_named_actor(&observer, &observer).await;
        let group = Group::new(format!("group{b:03}"), bot.clone(), vec![Participant::bot(&bot, ParticipantRole::Driver), Participant::bot(&observer, ParticipantRole::Observer)]);
        fixture.group.upsert(group.clone()).await?;
        for s in 0..sessions {
            let sid = format!("session{b:03}-{s:03}");
            db.execute(DbStatement::with_params("INSERT INTO bcs_group_sessions (session_id,group_id,env,participants) VALUES (?,?,'dev','[]')", vec![sid.clone().into(), group.id.clone().into()])).await?;
            let flow = Arc::new(BcsMessageFlow::new(fixture.group.clone(), Arc::new(bcs_routing::MessageRouter::new()), fixture.registry.clone(), fixture.bot_delivery.clone(), Arc::new(DiscardFrontend))
                .with_message_repo(repo.clone()).with_managed_deliveries(service.clone()).with_event_record_factory(Arc::new(EventFactory))
                .with_group_delivery_limits(BTreeMap::from([(bot.clone(), 10000), (observer.clone(), 10000)]))
                .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session_support::test_session(&sid, &group.id, group.participants.clone())))));
            flows.insert(sid.clone(), flow);
            for n in 0..10 {
                let id = format!("input{b:03}-{s:03}-{n}");
                let now = chrono::Utc::now().timestamp_millis();
                service.admit(AdmitMessageDeliveries { display_message: None, message_id: id.clone(), flow_kind: DeliveryFlowKind::Group, now_ms: now, expire_at_ms: None, event: None,
                    message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None, group_id: group.id.clone(), session_id: sid.clone(), sender_id: "human".into(), sender_type: SenderType::Human, message_type: "chat".into(), content: json!({"text":id}), client_msg_id: Some(id), owner_bot_id: None, created_at: now as u64, run_id: String::new() },
                    targets: vec![DeliveryAdmissionTarget { target_bot_id: bot.clone(), kind: DeliveryType::Send, max_queued: 10000, semantic_projection_json: json!({"version":1}) }] }).await?;
            }
        }
    }
    // Persist real chat + summary bodies as historical ballast, in unrelated runs.
    if history > 0 {
        db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id,group_id,env,participants) VALUES ('background','background','dev','[]')")).await?;
    }
    for n in 0..history {
        repo.append_message(NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None, group_id: "background".into(), session_id: "background".into(), sender_id: "background".into(), sender_type: SenderType::Bot,
            message_type: if n % 2 == 0 { "chat" } else { "run_reply" }.into(), content: if n % 2 == 0 { json!("old reply") } else { json!({"text":"old reply"}) },
            client_msg_id: None, owner_bot_id: None, created_at: 1, run_id: format!("old{}", n / 2) }).await?;
    }
    let admission_seconds = admission.elapsed().as_secs_f64();
    probe.0.lock().unwrap().operations.clear();
    let io = Arc::new(ReplyMock { base: MockIo { service: service.clone(), completions: Mutex::new(vec![]), offline }, flows, callbacks: Mutex::new(vec![]), final_ms: Arc::new(Mutex::new(vec![])), text_size: size });
    let cap = if offline { 1 } else { 4 };
    let worker = DeliveryRuntime { policy: None, service: service.clone(), preparation: io.clone(), transport: io.clone(), config: DeliveryRuntimeConfig {
        max_safe_retries: 0, pause_dispatch: false, bots: (0..bots).map(|b| (format!("bot{b:03}"), DeliveryRuntimePolicy { max_running: cap, min_send_interval_ms: 0 })).collect(),
        tick: Duration::from_millis(100), io_timeout: Duration::from_secs(10), run_timeout: Duration::from_secs(120), cancel_timeout: Duration::from_secs(10), max_tasks: 32, max_abort_tasks: 2,
    }};
    let expected = (bots - usize::from(offline)) * sessions * 10;
    let start = Instant::now();
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(worker.run(shutdown));
    while start.elapsed() < Duration::from_secs(120) && probe.0.lock().unwrap().completed < expected {
        // Surface callback panics promptly instead of waiting for drain timeout.
        let finished = {
            let mut handles = io.callbacks.lock().unwrap();
            let mut done = Vec::new();
            let mut i = 0;
            while i < handles.len() { if handles[i].is_finished() { done.push(handles.swap_remove(i)); } else { i += 1; } }
            done
        };
        for handle in finished {
            if let Err(error) = handle.await { stop.send(true)?; task.await??; return Err(error.into()); }
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    let seconds = start.elapsed().as_secs_f64();
    stop.send(true)?; task.await??;
    let handles = std::mem::take(&mut *io.callbacks.lock().unwrap());
    for handle in handles { handle.await?; }
    report_profile(name);
    let mut latency = io.final_ms.lock().unwrap().clone(); latency.sort_by(f64::total_cmp);
    let p = |q: f64| latency.get(((latency.len().saturating_sub(1)) as f64 * q) as usize).copied().unwrap_or(0.);
    let rows = db.query(DbStatement::new("SELECT message_type, count(*) AS n FROM bcs_messages WHERE session_id != 'background' GROUP BY message_type")).await?;
    let counts: BTreeMap<String, i64> = rows.iter().map(|r| Ok((bcs_db_api::db_get_column(r,"message_type")?, bcs_db_api::db_get_column(r,"n")?))).collect::<bcs_db_api::DbResult<_>>()?;
    assert_eq!(counts.get("run_reply"), Some(&(expected as i64)));
    let bad = db.query(DbStatement::new("SELECT count(*) AS n FROM bcs_messages WHERE message_type='run_reply' AND session_id != 'background' AND (json_extract(content,'$.text') NOT LIKE '工具前%工具后%' OR json_extract(content,'$.text') LIKE '%TOOL_BODY_NOT_REPLY%')")).await?;
    assert_eq!(bcs_db_api::db_get_column::<i64>(&bad[0], "n")?, 0);
    let outputs = db.query(DbStatement::new("SELECT count(*) AS n FROM bcs_message_deliveries d JOIN bcs_messages m ON m.message_id=d.source_message_id WHERE m.message_type='run_reply' AND d.kind='inject' AND d.status='pending_context'")).await?;
    assert_eq!(bcs_db_api::db_get_column::<i64>(&outputs[0], "n")?, expected as i64);
    let states = db.query(DbStatement::new("SELECT status, count(*) AS n FROM bcs_message_deliveries WHERE kind='send' GROUP BY status")).await?;
    let states: BTreeMap<String, i64> = states.iter().map(|r| Ok((bcs_db_api::db_get_column(r,"status")?, bcs_db_api::db_get_column(r,"n")?))).collect::<bcs_db_api::DbResult<_>>()?;
    assert_eq!(states.get("completed"), Some(&(expected as i64)));
    assert_eq!(states.get("queued").copied().unwrap_or(0), if offline { (sessions * 10) as i64 } else { 0 });
    assert!(states.keys().all(|s| s == "completed" || s == "queued"));
    for sid in io.flows.keys() {
        let page = repo.list_session_history(sid, bcs_domain::MessageOwnerFilter::Any, None, None, None, 100).await?;
        assert!(page.messages.iter().all(|m| m.message_type != "run_reply"));
        let deliveries = service.snapshot(Some(sid)).await?;
        let mut reply_ids = BTreeSet::new();
        for output in deliveries.iter().filter(|d| d.state.kind == DeliveryType::Inject) {
            assert!(reply_ids.insert(output.source_message_id.clone()), "duplicate reply delivery");
            let summary = repo.get_message_by_id(sid, &output.source_message_id).await?.unwrap();
            assert_eq!(summary.message_type, "run_reply");
            let input = deliveries.iter().find(|d| d.run_id.as_deref() == Some(summary.run_id.as_str())).unwrap();
            let suffix = if input.source_session_seq % 4 == 3 { "" } else { "补充" };
            let expected_text = format!("工具前{}\n工具后{}{suffix}", "a".repeat(size / 2), "b".repeat(size / 2));
            assert_eq!(summary.content["text"].as_str(), Some(expected_text.as_str()));
            let visible = repo.run_chat_segments(sid, &summary.sender_id, &summary.run_id).await?;
            assert_eq!(visible.iter().filter_map(|m| m.content.as_str()).collect::<Vec<_>>().join("\n"), expected_text);
            if input.source_session_seq == 1 {
                io.flows[sid].handle_bot_event(BotEventCommand { bot_id: summary.sender_id.clone(), run_id: summary.run_id.clone(), group_id: summary.group_id.clone(),
                    bcs_session_id: Some(sid.clone()), state: ChatEventState::Final, event_type: "chat".into(),
                    event_payload: json!({"message":{"role":"assistant","content":[{"type":"text","text":summary.content["normalization"]["raw_final"]}]}}) }).await?;
                assert_eq!(service.snapshot(Some(sid)).await?.len(), deliveries.len());
                assert_eq!(repo.run_chat_segments(sid, &summary.sender_id, &summary.run_id).await?.len(), visible.len());
            }
            // Exercise production send-time reconstruction from the persisted
            // summary. This dry-run does not start an observer or mutate its lane.
            let mut sample = output.clone(); sample.run_id = Some("verify-reply-payload".into());
            sample.state.kind = DeliveryType::Send;
            sample.state.status = bcs_domain::message_delivery::MessageDeliveryStatus::Queued;
            let prepared = bcs_message_flow::queued_group::prepare_queued_group(&io.flows[sid], &sample, &[], false).await?;
            let payload = serde_json::to_string(&prepared.command.frame)?;
            assert!(payload.contains("工具前"));
            assert!(payload.contains("工具后"));
            assert!(!payload.contains("TOOL_BODY_NOT_REPLY"));
        }
    }
    let m = probe.0.lock().unwrap();
    let mut terminal_latency = m.latencies_ms.clone(); terminal_latency.sort_by(f64::total_cmp);
    let terminal_p95 = terminal_latency.get((terminal_latency.len().saturating_sub(1) as f64 * 0.95) as usize).copied().unwrap_or(0.);
    println!("REPLY_LOAD_RESULT {}", json!({"scene":name,"completed":m.completed,"seconds":seconds,"per_second":m.completed as f64/seconds,"setup_seconds":admission_seconds,"final_handler_p95_ms":p(0.95),"final_handler_p99_ms":p(0.99),"dispatch_terminal_p95_ms":terminal_p95,"messages":counts,"operations":m.operations,"errors":m.errors,"reply_text_bytes_approx":size,"history_rows":history}));
    assert_eq!(m.completed, expected); assert_eq!(m.starts.len(), expected); assert!(m.errors.is_empty()); assert!(m.peak_bot <= cap);
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "manual isolated full reply load"]
#[serial_test::serial(reply_load)]
async fn full_reply_load() -> anyhow::Result<()> {
    install_profile();
    reply_scene("reply_smoke", 1, 1, false, 32, 0).await?;
    reply_scene("reply_baseline", 32, 8, false, 1024, 0).await?;
    reply_scene("reply_many_bots", 128, 4, false, 1024, 0).await?;
    reply_scene("reply_history", 32, 8, false, 1024, 100000).await?;
    reply_scene("reply_offline", 32, 8, true, 1024, 0).await?;
    reply_scene("reply_128k", 32, 8, false, 128 * 1024, 0).await?;
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "manual focused reply profiling; run in a separate process"]
#[serial_test::serial(reply_load)]
async fn profile_reply_hotspots() -> anyhow::Result<()> {
    install_profile();
    reply_scene("reply_history", 32, 8, false, 1024, 100000).await?;
    reply_scene("reply_128k", 32, 8, false, 128 * 1024, 0).await?;
    Ok(())
}
