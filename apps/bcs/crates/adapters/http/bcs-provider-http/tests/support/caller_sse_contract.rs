//! Real protected HTTP -> shared SSE reader -> ingest-port regressions.
use super::*;
use std::{collections::HashMap, sync::atomic::{AtomicBool, AtomicUsize, Ordering}, time::Duration};
use axum::{body::{Body, Bytes}, http::HeaderMap, response::Response};
use bcs_service_api::{
    ActiveBotRunContext, BotEventOutcome, BotRunContext, BotRunContextPort,
    ChatEventState, ProviderEventIngestCommand, ProviderEventIngestService,
    ProviderEventSource, ProviderRunTransport, ServiceResult,
};
use tokio::sync::{mpsc, RwLock};

#[derive(Default)]
struct RunContexts {
    contexts: RwLock<HashMap<String, BotRunContext>>,
    transports: RwLock<HashMap<String, ProviderRunTransport>>,
    active: RwLock<HashMap<String, ActiveBotRunContext>>,
    lose_context_write: AtomicBool,
    fail_active_write: AtomicBool,
    lose_binding_write: AtomicBool,
    context_writes: AtomicUsize,
    active_writes: AtomicUsize,
}

#[async_trait::async_trait]
impl BotRunContextPort for RunContexts {
    async fn put_context(&self, context: BotRunContext) {
        self.context_writes.fetch_add(1, Ordering::Relaxed);
        if self.lose_context_write.load(Ordering::Relaxed) { return; }
        self.contexts.write().await.insert(context.run_id.clone(), context);
    }
    async fn get_context(&self, run: &str) -> Option<BotRunContext> {
        self.contexts.read().await.get(run).cloned()
    }
    async fn try_begin_terminal(&self, run: &str) -> bool {
        self.get_context(run).await.is_some_and(|ctx| !ctx.terminal)
    }
    async fn mark_terminal(&self, run: &str) -> bool {
        let mut contexts = self.contexts.write().await;
        let Some(ctx) = contexts.get_mut(run) else { return false; };
        let changed = !ctx.terminal;
        ctx.terminal = true;
        changed
    }
    async fn release_terminal(&self, _run: &str) {}
    async fn register_active_run(&self, context: ActiveBotRunContext) -> ServiceResult<()> {
        self.active_writes.fetch_add(1, Ordering::Relaxed);
        if self.fail_active_write.load(Ordering::Relaxed) {
            return Err(bcs_service_api::ServiceError::InternalError("injected cache failure".into()));
        }
        self.active.write().await.insert(context.canonical_run_id.clone(), context);
        Ok(())
    }
    async fn find_active_run(&self, run: &str) -> ServiceResult<Option<ActiveBotRunContext>> {
        Ok(self.active.read().await.get(run).cloned())
    }
    async fn begin_provider_transport(&self, run: &str, _deadline: u64) -> bool {
        let mut transports = self.transports.write().await;
        if transports.contains_key(run) { return false; }
        transports.insert(run.into(), ProviderRunTransport::Negotiating);
        true
    }
    async fn bind_provider_transport(&self, run: &str, source: ProviderRunTransport) -> bool {
        if self.lose_binding_write.load(Ordering::Relaxed) { return true; }
        let mut transports = self.transports.write().await;
        let Some(current) = transports.get_mut(run) else { return false; };
        if *current != ProviderRunTransport::Negotiating && *current != source { return false; }
        *current = source;
        true
    }
    async fn get_provider_transport(&self, run: &str) -> Option<ProviderRunTransport> {
        self.transports.read().await.get(run).copied()
    }
    async fn mark_provider_transport_terminal(&self, run: &str) {
        self.transports.write().await.insert(run.into(), ProviderRunTransport::Terminal);
    }
    async fn clear_provider_transport(&self, run: &str) { self.transports.write().await.remove(run); }
}

struct EventSink(mpsc::UnboundedSender<ProviderEventIngestCommand>);
#[async_trait::async_trait]
impl ProviderEventIngestService for EventSink {
    async fn ingest_provider_event(&self, cmd: ProviderEventIngestCommand) -> ServiceResult<BotEventOutcome> {
        self.0.send(cmd).unwrap();
        Ok(BotEventOutcome {
            bot_deliveries: vec![], frontend_deliveries: vec![], unregistered_run_ids: vec![],
            mentions: vec![], delivered_count: 1, failed_count: 0, delivery_results: vec![],
        })
    }
}

fn wired() -> (HttpProviderTransport, Arc<RunContexts>, mpsc::UnboundedReceiver<ProviderEventIngestCommand>) {
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let contexts = Arc::new(RunContexts::default());
    let (tx, rx) = mpsc::unbounded_channel();
    transport.set_ingest(Arc::new(EventSink(tx)), contexts.clone());
    (transport, contexts, rx)
}

fn frame(seq: u64, state: &str, content: &str) -> Bytes {
    Bytes::from(format!("event: chat\ndata: {}\n\n", json!({
        "runId": "run-1", "seq": seq, "state": state, "content": content,
    })))
}

async fn sse_server() -> (String, mpsc::Sender<Bytes>, tokio::task::JoinHandle<()>) {
    let (tx, rx) = mpsc::channel::<Bytes>(8);
    let receiver = Arc::new(Mutex::new(Some(rx)));
    let app = Router::new().route("/webhook", post(move |headers: HeaderMap| {
        let receiver = receiver.clone();
        async move {
            assert_eq!(headers.get("x-bcn-transport").unwrap(), "sse");
            assert!(headers.get("accept").unwrap().to_str().unwrap().contains("text/event-stream"));
            let rx = receiver.lock().await.take().unwrap();
            let stream = futures::stream::unfold(rx, |mut rx| async {
                rx.recv().await.map(|chunk| (Ok::<_, std::convert::Infallible>(chunk), rx))
            });
            Response::builder().header("content-type", "text/event-stream; charset=utf-8")
                .body(Body::from_stream(stream)).unwrap()
        }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
    tx.send(Bytes::from_static(b": connected\n\n")).await.unwrap();
    (addr.to_string(), tx, task)
}

async fn event(rx: &mut mpsc::UnboundedReceiver<ProviderEventIngestCommand>, state: ChatEventState, group: &str) {
    let cmd = tokio::time::timeout(Duration::from_secs(2), rx.recv()).await
        .expect("protected SSE must deliver its event without waiting for EOF").unwrap();
    assert_eq!(cmd.source, ProviderEventSource::Sse);
    assert_eq!(cmd.event.run_id, "run-1");
    assert_eq!(cmd.event.bot_id, "bot-b");
    assert_eq!(cmd.event.group_id, group);
    assert_eq!(cmd.event.bcs_session_id.as_deref(), Some("session-1"));
    assert_eq!(cmd.event.state, state);
}

#[tokio::test]
async fn protected_sse_accepts_headers_before_body_finishes_and_ingests_scoped_events() {
    let (transport, contexts, mut events) = wired();
    let (addr, tx, server) = sse_server().await;
    let mut cmd = command(now());
    cmd.group_id = Some("group-1".into());
    cmd.callback_timeout_ms = 150;
    let deadline = cmd.not_after_ms as u64;
    let ack = transport.send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), cmd).await;
    assert_eq!(ack, Ok(CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() }));
    assert_eq!(contexts.get_provider_transport("run-1").await, Some(ProviderRunTransport::Sse));
    assert_eq!(contexts.get_context("run-1").await.unwrap().deadline_ms, deadline);
    assert!(contexts.find_active_run("run-1").await.unwrap().is_some());
    // The response remains open longer than the ACK budget. Do not collect it
    // as JSON or let the ACK deadline cancel subsequent events.
    tokio::time::sleep(Duration::from_millis(200)).await;
    tx.send(frame(1, "delta", "working")).await.unwrap();
    event(&mut events, ChatEventState::Delta, "group-1").await;
    tx.send(frame(2, "final", "done")).await.unwrap();
    event(&mut events, ChatEventState::Final, "group-1").await;
    server.abort();
}

#[tokio::test]
async fn protected_finite_sse_delivers_final_instead_of_json_decode_unknown() {
    let (transport, contexts, mut events) = wired();
    let (addr, tx, server) = sse_server().await;
    tx.send(frame(1, "final", "done")).await.unwrap();
    drop(tx);
    let ack = transport.send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(now())).await;
    assert!(matches!(ack, Ok(CallerDispatchAck::Accepted { .. })), "{ack:?}");
    event(&mut events, ChatEventState::Final, "").await;
    tokio::time::timeout(Duration::from_secs(2), async {
        while contexts.get_provider_transport("run-1").await != Some(ProviderRunTransport::Terminal) {
            tokio::task::yield_now().await;
        }
    }).await.unwrap();
    assert!(contexts.get_context("run-1").await.unwrap().terminal);
    server.abort();
}

#[tokio::test]
async fn protected_sse_eof_without_final_emits_error_terminal() {
    let (transport, _, mut events) = wired();
    let (addr, tx, server) = sse_server().await;
    tx.send(frame(1, "delta", "partial")).await.unwrap();
    drop(tx);
    let ack = transport.send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(now())).await;
    assert!(matches!(ack, Ok(CallerDispatchAck::Accepted { .. })), "{ack:?}");
    event(&mut events, ChatEventState::Delta, "").await;
    event(&mut events, ChatEventState::Error, "").await;
    server.abort();
}

#[tokio::test]
async fn protected_json_fallback_binds_callback_and_preserves_local_ack_facts() {
    let (transport, contexts, _events) = wired();
    let addr = serve(default_ack_server()).await;
    let ack = transport.send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(now())).await;
    assert_eq!(ack, Ok(CallerDispatchAck::Accepted { control_revision: 0, request_digest: DIGEST.into() }));
    assert_eq!(contexts.get_provider_transport("run-1").await, Some(ProviderRunTransport::Callback));
    assert!(!contexts.get_context("run-1").await.unwrap().terminal);
}

#[tokio::test]
async fn protected_explicit_rejection_allows_same_run_history_redrive() {
    let (transport, contexts, _events) = wired();
    let rejection = Arc::new(AckServer {
        last_request: Mutex::new(None), respond_with: axum::http::StatusCode::OK,
        body: json!({"ok":false,"error":{"code":"HISTORY_NOT_READY","retryable":true}}),
    });
    let addr = serve(rejection.clone()).await;
    let cmd = command(now());
    let rejected = transport.send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), cmd.clone()).await;
    assert_eq!(rejected, Ok(CallerDispatchAck::Rejected { error_code: "HISTORY_NOT_READY".into(), retryable: true }));
    assert_eq!(contexts.get_provider_transport("run-1").await, None);
    let accepted = default_ack_server();
    let addr = serve(accepted.clone()).await;
    let ack = transport.send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), cmd).await;
    assert!(matches!(ack, Ok(CallerDispatchAck::Accepted { .. })), "{ack:?}");
    assert_eq!(contexts.get_provider_transport("run-1").await, Some(ProviderRunTransport::Callback));
    let first = rejection.last_request.lock().await.clone().unwrap();
    let second = accepted.last_request.lock().await.clone().unwrap();
    assert_eq!(first["id"], second["id"]);
    assert_eq!(first["extensions"], second["extensions"]);
    assert_eq!(contexts.context_writes.load(Ordering::Relaxed), 1, "redrive must reuse its producer");
    assert_eq!(contexts.active_writes.load(Ordering::Relaxed), 1, "redrive must reuse its abort index");
}

#[tokio::test]
async fn protected_duplicate_transport_does_not_send_or_overwrite_scope() {
    let (transport, contexts, _events) = wired();
    assert!(contexts.begin_provider_transport("run-1", u64::MAX).await);
    assert!(contexts.bind_provider_transport("run-1", ProviderRunTransport::Sse).await);
    let state = default_ack_server();
    let addr = serve(state.clone()).await;
    let ack = transport.send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(now())).await;
    assert_eq!(ack, Err(CallerSendError::OutcomeUnknown));
    assert!(state.last_request.lock().await.is_none());
    assert_eq!(contexts.get_provider_transport("run-1").await, Some(ProviderRunTransport::Sse));
}

#[tokio::test]
async fn protected_sse_without_ingest_wiring_never_claims_acceptance() {
    let transport = HttpProviderTransport::allowing_private_networks_for_tests();
    let (addr, tx, server) = sse_server().await;
    tx.send(frame(1, "final", "done")).await.unwrap();
    drop(tx);
    let result = transport.send_caller_execution(&target_at(addr), &RedactedToken::new("svc-token"), command(now())).await;
    assert_eq!(result, Err(CallerSendError::OutcomeUnknown));
    server.abort();
}

#[tokio::test]
async fn protected_context_or_active_write_failure_never_reaches_provider() {
    let (transport, contexts, _events) = wired();
    let state = default_ack_server();
    let target = target_at(serve(state.clone()).await);
    let cmd = command(now());
    contexts.lose_context_write.store(true, Ordering::Relaxed);
    let result = transport.send_caller_execution(&target, &RedactedToken::new("svc-token"), cmd.clone()).await;
    assert_eq!(result, Err(CallerSendError::NotSent));
    assert_eq!(contexts.get_provider_transport("run-1").await, None);
    assert!(state.last_request.lock().await.is_none());
    contexts.lose_context_write.store(false, Ordering::Relaxed);
    contexts.fail_active_write.store(true, Ordering::Relaxed);
    let result = transport.send_caller_execution(&target, &RedactedToken::new("svc-token"), cmd).await;
    assert_eq!(result, Err(CallerSendError::NotSent));
    assert_eq!(contexts.get_provider_transport("run-1").await, None);
    assert!(state.last_request.lock().await.is_none());
}

#[tokio::test]
async fn protected_lost_transport_binding_write_does_not_claim_acceptance() {
    let (transport, contexts, _events) = wired();
    contexts.lose_binding_write.store(true, Ordering::Relaxed);
    let state = default_ack_server();
    let target = target_at(serve(state.clone()).await);
    let result = transport.send_caller_execution(&target, &RedactedToken::new("svc-token"), command(now())).await;
    assert_eq!(result, Err(CallerSendError::OutcomeUnknown));
    assert!(state.last_request.lock().await.is_some());
    assert_eq!(contexts.get_provider_transport("run-1").await, Some(ProviderRunTransport::Negotiating));
}

#[tokio::test]
async fn protected_send_does_not_resurrect_terminal_or_cross_scope_context() {
    let (transport, contexts, _events) = wired();
    let state = default_ack_server();
    let target = target_at(serve(state.clone()).await);
    let cmd = command(now());
    for (terminal, group) in [(true, ""), (false, "another-group")] {
        contexts.put_context(BotRunContext {
            run_id: "run-1".into(), bot_id: "bot-b".into(), group_id: group.into(),
            bcs_session_id: Some("session-1".into()), deadline_ms: cmd.not_after_ms as u64, terminal,
        }).await;
        let result = transport.send_caller_execution(&target, &RedactedToken::new("svc-token"), cmd.clone()).await;
        assert_eq!(result, Err(CallerSendError::NotSent));
        assert!(state.last_request.lock().await.is_none());
        let retained = contexts.get_context("run-1").await.unwrap();
        assert_eq!(retained.terminal, terminal);
        assert_eq!(retained.group_id, group);
    }
}

#[tokio::test]
async fn protected_unknown_json_ack_retains_callback_route_without_resending() {
    let (transport, contexts, _events) = wired();
    let state = Arc::new(AckServer {
        last_request: Mutex::new(None), respond_with: axum::http::StatusCode::OK,
        body: json!({"ok":true,"state":"unknown"}),
    });
    let target = target_at(serve(state.clone()).await);
    let cmd = command(now());
    let result = transport.send_caller_execution(&target, &RedactedToken::new("svc-token"), cmd.clone()).await;
    assert_eq!(result, Ok(CallerDispatchAck::Unknown));
    assert_eq!(contexts.get_provider_transport("run-1").await, Some(ProviderRunTransport::Callback));
    *state.last_request.lock().await = None;
    let result = transport.send_caller_execution(&target, &RedactedToken::new("svc-token"), cmd).await;
    assert_eq!(result, Err(CallerSendError::OutcomeUnknown));
    assert!(state.last_request.lock().await.is_none());
}
