//! Runtime-level retention contracts; no real engine, network, or changed limits.
use std::{path::PathBuf, sync::{Arc, atomic::{AtomicUsize, Ordering}}, time::Duration};
use bcs_bridge_core::{
    config::ProviderConfig,
    engine::{Engine, EngineFactory, EngineRegistry, TurnError, TurnOutcome, TurnRequest},
    run::{DeliveryCursorError, RunEvent, RunHandle},
    runtime::{self, AppState, DownstreamRequest, RunDelivery, RuntimeReply},
    sse,
};
use bcs_protocol::stream::{ChatState, StreamEvent};
use futures::StreamExt;
use serde_json::json;
use tokio::sync::{mpsc, Semaphore};
use tokio_util::sync::CancellationToken;

// Public contract boundary: double the original record budget; bytes stay at 32 MiB.
const EXPECTED_EVENT_LIMIT: usize = 16_384;

struct ScriptedEngine {
    count: usize,
    starts: AtomicUsize,
    start: Semaphore,
    finish: Semaphore,
}

#[async_trait::async_trait]
impl Engine for ScriptedEngine {
    async fn run_turn(&self, req: TurnRequest, events: mpsc::Sender<StreamEvent>, abort: CancellationToken) -> Result<TurnOutcome, TurnError> {
        self.starts.fetch_add(1, Ordering::SeqCst);
        tokio::select! {
            _ = abort.cancelled() => return Err(TurnError::Aborted),
            permit = self.start.acquire() => permit.unwrap().forget(),
        }
        for index in 0..self.count {
            let event = if index % 3 == 0 { sse::chat_delta(&req.run_id, "text") }
                else { sse::agent_thinking(&req.run_id, Some("thinking".into()), None) };
            tokio::select! {
                _ = abort.cancelled() => return Err(TurnError::Aborted),
                result = events.send(event) => { result.map_err(|_| TurnError::Aborted)?; },
            }
            tokio::task::yield_now().await;
        }
        tokio::select! {
            _ = abort.cancelled() => return Err(TurnError::Aborted),
            permit = self.finish.acquire() => permit.unwrap().forget(),
        }
        Ok(TurnOutcome {engine_session_id:None, final_text:Some("complete".into())})
    }
}

struct Factory(Arc<ScriptedEngine>);
impl EngineFactory for Factory {
    fn id(&self) -> &str { "scripted" }
    fn default_bin(&self) -> &str { "" }
    fn requires_bin(&self) -> bool { false }
    fn build(&self, _: PathBuf, _: &toml::Table) -> Result<Arc<dyn Engine>, String> { Ok(self.0.clone()) }
}

fn fixture(plugin: bool, count: usize) -> (tempfile::TempDir, Arc<AppState>, Arc<ScriptedEngine>) {
    let dir = tempfile::tempdir().unwrap();
    let mode = if plugin {"plugin"} else {"gateway"};
    let config = ProviderConfig::from_toml(&format!(r#"
provider_id = "test-provider"
mode = "{mode}"
listen = "127.0.0.1:1"
bcs_to_provider_token = "test-only"
state_path = "state.sqlite3"
[plugin]
url = "ws://127.0.0.1:1/ws/bot"
[[bot]]
provider_bot_ref = "worker"
bot_id = "test-bot"
engine = "scripted"
cwd = "{}"
"#, dir.path().display()), &dir.path().join("bridge.toml")).unwrap();
    let engine = Arc::new(ScriptedEngine {count, starts:AtomicUsize::new(0), start:Semaphore::new(0), finish:Semaphore::new(0)});
    let mut registry = EngineRegistry::new();
    registry.register(Factory(engine.clone()));
    let state = Arc::new(AppState::new(config, &registry).unwrap());
    (dir, state, engine)
}

fn request() -> DownstreamRequest {
    serde_json::from_value(json!({"id":"run", "method":"chat.send", "session_id":"group:session",
        "to_bot":{"provider_id":"test-provider", "provider_bot_ref":"worker"},
        "message":{"content":[{"type":"text","text":"original request"}]}})).unwrap()
}

async fn next(handle: &RunHandle, cursor: u64) -> RunEvent {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if let Some(event) = handle.next_after(cursor).unwrap() { return event; }
            assert!(!handle.is_terminal(), "run terminated before expected event");
            tokio::task::yield_now().await;
        }
    }).await.expect("run stopped making progress")
}

async fn terminal(handle: &RunHandle) {
    tokio::time::timeout(Duration::from_secs(5), async {
        while !handle.is_terminal() { tokio::task::yield_now().await; }
    }).await.expect("run cleanup did not finish");
}

#[tokio::test]
async fn long_plugin_runtime_has_no_gateway_subscriber_and_preserves_idempotency() {
    let (_dir, state, engine) = fixture(true, 20_000);
    let RuntimeReply::Run {handle, delivery:RunDelivery::Plugin} = runtime::dispatch(state.clone(), request()).await.unwrap()
        else { panic!("expected Plugin handle without a Gateway stream") };
    assert_eq!(handle.tx.receiver_count(), 0);
    assert!(bcs_bridge_core::run::forward_stream(handle.clone()).is_err());
    engine.start.add_permits(1);
    for seq in 1..=20_000 {
        let event = next(&handle, seq - 1).await;
        assert_eq!(event.seq, seq);
        handle.commit_delivery_through(seq).unwrap();
        if seq == 10_000 {
            let RuntimeReply::Run {handle:repeated, delivery:RunDelivery::Plugin} = runtime::dispatch(state.clone(), request()).await.unwrap()
                else { panic!("retry created a Gateway stream") };
            assert_eq!(repeated.retention_stats().delivery_cursor, seq);
            assert_eq!(repeated.tx.receiver_count(), 0);
            let mut conflict = request();
            conflict.message = Some(json!({"content":[{"type":"text","text":"changed"}]}));
            assert_eq!(runtime::dispatch(state.clone(), conflict).await.err().unwrap().code, "conflict");
        }
    }
    engine.finish.add_permits(1);
    let final_event = next(&handle, 20_000).await;
    assert!(matches!(final_event.event, StreamEvent::Chat(chat) if chat.state == ChatState::Final));
    handle.commit_delivery_through(final_event.seq).unwrap();
    terminal(&handle).await;
    handle.release_delivery();
    assert_eq!(handle.retention_stats().retained_bytes, 0);
    assert!(handle.retention_stats().peak_retained_events < EXPECTED_EVENT_LIMIT);
    let RuntimeReply::Run {handle:repeated, delivery:RunDelivery::Plugin} = runtime::dispatch(state.clone(), request()).await.unwrap()
        else { panic!("terminal retry did not reuse Plugin handle") };
    assert!(repeated.is_terminal());
    assert_eq!(engine.starts.load(Ordering::SeqCst), 1);
    assert_eq!(repeated.retention_stats().last_emitted_seq, 20_001);
    assert_eq!(repeated.retention_stats().delivery_cursor, 20_001);
}

#[tokio::test]
async fn gateway_retries_keep_full_history_for_both_readers() {
    let (_dir, state, engine) = fixture(false, 5);
    let RuntimeReply::Run {handle, delivery:RunDelivery::Gateway {mut stream}} = runtime::dispatch(state.clone(), request()).await.unwrap()
        else { panic!("expected Gateway stream") };
    assert_eq!(handle.tx.receiver_count(), 1, "subscribed before engine starts producing");
    engine.start.add_permits(1);
    for seq in 1..=5 {
        assert_eq!(tokio::time::timeout(Duration::from_secs(5), stream.next()).await.unwrap().unwrap().seq, seq);
    }
    assert_eq!(handle.commit_delivery_through(1), Err(DeliveryCursorError::WrongMode));
    let RuntimeReply::Run {delivery:RunDelivery::Gateway {stream:mut replay}, ..} = runtime::dispatch(state.clone(), request()).await.unwrap()
        else { panic!("expected replay stream") };
    for seq in 1..=5 {
        assert_eq!(tokio::time::timeout(Duration::from_secs(5), replay.next()).await.unwrap().unwrap().seq, seq);
    }
    engine.finish.add_permits(1);
    for reader in [&mut stream, &mut replay] {
        let event = tokio::time::timeout(Duration::from_secs(5), reader.next()).await.unwrap().unwrap();
        assert_eq!(event.seq, 6);
        assert!(matches!(event.event, StreamEvent::Chat(chat) if chat.state == ChatState::Final));
        assert!(tokio::time::timeout(Duration::from_secs(5), reader.next()).await.unwrap().is_none());
    }
    handle.release_delivery(); // Plugin release cannot discard Gateway history.
    assert_eq!(handle.retention_stats().retained_events, 6);
    assert_eq!(engine.starts.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn plugin_without_consumption_still_hits_the_real_count_budget() {
    let (_dir, state, engine) = fixture(true, 20_000);
    let RuntimeReply::Run {handle, delivery:RunDelivery::Plugin} = runtime::dispatch(state, request()).await.unwrap()
        else { panic!("expected Plugin handle") };
    engine.start.add_permits(1);
    terminal(&handle).await;
    assert!(handle.abort.is_cancelled());
    assert_eq!(handle.retention_stats().retained_events, EXPECTED_EVENT_LIMIT + 1);
    for seq in 1..=EXPECTED_EVENT_LIMIT as u64 + 1 {
        let event = handle.next_after(seq - 1).unwrap().unwrap();
        if seq == EXPECTED_EVENT_LIMIT as u64 + 1 {
            assert!(matches!(event.event, StreamEvent::Chat(chat) if chat.error_kind.as_deref() == Some("buffer_overflow")));
        }
        handle.commit_delivery_through(seq).unwrap();
    }
    assert_eq!(handle.retention_stats().retained_bytes, 0);
}

#[tokio::test]
async fn abort_after_consuming_all_current_events_keeps_a_final_aborted_record() {
    let (_dir, state, engine) = fixture(true, 1);
    let RuntimeReply::Run {handle, delivery:RunDelivery::Plugin} = runtime::dispatch(state, request()).await.unwrap()
        else { panic!("expected Plugin handle") };
    engine.start.add_permits(1);
    let first = next(&handle, 0).await;
    handle.commit_delivery_through(first.seq).unwrap();
    handle.release_delivery(); // An idle active queue is not a completed run.
    assert!(!handle.is_terminal());
    handle.request_abort("user_cancelled");
    let aborted = next(&handle, 1).await;
    assert_eq!(aborted.seq, 2);
    assert!(matches!(aborted.event, StreamEvent::Chat(chat) if chat.state == ChatState::Aborted));
    handle.commit_delivery_through(2).unwrap();
    terminal(&handle).await;
    handle.release_delivery();
    assert_eq!(handle.retention_stats().retained_bytes, 0);
}
