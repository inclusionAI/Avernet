//! A2A task registry: one projection, event log and subscriber fan-out per task.
//!
//! A task outlives every client connection: the projection driver is the run
//! stream's permanent consumer, so a client disconnect never aborts the engine
//! (unlike the BCN gateway path). Streams, `GetTask` and push notifications are
//! all views over the same seq-numbered event log.
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use bcs_a2a_protocol::{meta, StreamResponse, Task, TaskPushNotificationConfig, TaskState};
use futures::{Stream, StreamExt};
use serde_json::json;
use tokio_util::sync::CancellationToken;

use super::projection::Projection;
use crate::run::RunHandle;
use crate::runtime::{AppState, RunStream};
use crate::session::A2aTaskRow;

/// Retained events per task for replay; older ones fall back to a snapshot.
const MAX_RETAINED_EVENTS: usize = 16_384;
/// Terminal tasks stay in memory this long; afterwards GetTask reads SQLite.
const MEMORY_GRACE: Duration = Duration::from_secs(600);

pub const TERMINAL_STATES: &[&str] = &["TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"];

pub struct A2aTask {
    pub id: String,
    pub owner: String,
    pub bot: String,
    pub context_id: String,
    pub session_id: String,
    pub run: RunHandle,
    inner: Mutex<TaskInner>,
    changed: tokio::sync::watch::Sender<u64>,
}

struct TaskInner {
    projection: Projection,
    events: VecDeque<(u64, StreamResponse)>,
    released_through: u64,
    seq: u64,
    push: Vec<PushEntry>,
    finished_at: Option<Instant>,
}

struct PushEntry {
    config: TaskPushNotificationConfig,
    stop: CancellationToken,
}

/// Where a subscription starts.
#[derive(Debug, Clone, Copy)]
pub enum Cursor {
    /// Every retained event from the beginning (the task's own stream).
    Start,
    /// Events strictly after this seq (SSE `Last-Event-ID`, push config).
    After(u64),
    /// A fresh snapshot, then only new events (standard re-subscribe).
    Snapshot,
}

impl A2aTask {
    pub fn new(id: String, owner: String, bot: String, context_id: String, session_id: String, run: RunHandle, projection: Projection) -> Arc<Self> {
        let (changed, _) = tokio::sync::watch::channel(0);
        Arc::new(Self { id, owner, bot, context_id, session_id, run, changed, inner: Mutex::new(TaskInner {
            projection, events: VecDeque::new(), released_through: 0, seq: 0, push: Vec::new(), finished_at: None }) })
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, TaskInner> { self.inner.lock().unwrap_or_else(|p| p.into_inner()) }

    pub fn snapshot(&self) -> Task { self.lock().projection.task().clone() }
    pub fn state(&self) -> TaskState { self.lock().projection.state() }
    pub fn latest_seq(&self) -> u64 { self.lock().seq }
    pub fn pending_interactions(&self) -> Vec<String> { self.lock().projection.pending_interactions() }

    pub fn pending_interaction(&self, id: &str) -> Option<(bcs_protocol::stream::InteractionKind, serde_json::Value)> {
        self.lock().projection.pending_interaction(id)
    }

    /// Wait (bounded) until `done` holds for the task.
    pub async fn wait_for<F: Fn(&Self) -> bool>(&self, timeout: Duration, done: F) {
        let mut changed = self.changed.subscribe();
        let _ = tokio::time::timeout(timeout, async {
            while !done(self) {
                if changed.changed().await.is_err() { return; }
            }
        }).await;
    }

    /// Mutate the projection and publish whatever events it produces.
    pub fn update<F: FnOnce(&mut Projection) -> Vec<StreamResponse>>(&self, work: F) {
        let mut inner = self.lock();
        let events = work(&mut inner.projection);
        if events.is_empty() { return; }
        for mut event in events {
            inner.seq += 1;
            let seq = inner.seq;
            event.metadata_mut().get_or_insert_with(Default::default).insert(meta::SEQ.into(), json!(seq));
            inner.events.push_back((seq, event));
            if inner.events.len() > MAX_RETAINED_EVENTS
                && let Some((dropped, _)) = inner.events.pop_front() {
                inner.released_through = dropped;
            }
        }
        if inner.projection.state().is_terminal() && inner.finished_at.is_none() {
            inner.finished_at = Some(Instant::now());
        }
        let seq = inner.seq;
        drop(inner);
        self.changed.send_replace(seq);
    }

    /// Ordered event stream from `cursor`, ending after the terminal event.
    /// `Cursor::After` below the retained window degrades to a snapshot.
    pub fn subscribe(self: &Arc<Self>, cursor: Cursor) -> impl Stream<Item = (u64, StreamResponse)> + Send + 'static {
        let mut changed = self.changed.subscribe();
        let (mut position, preface) = {
            let inner = self.lock();
            match cursor {
                Cursor::Start if inner.released_through == 0 => (0, None),
                Cursor::After(seq) if seq >= inner.released_through => (seq, None),
                _ => (inner.seq, Some((inner.seq, StreamResponse::Task(inner.projection.task().clone())))),
            }
        };
        let task = self.clone();
        let (tx, rx) = tokio::sync::mpsc::channel(64);
        tokio::spawn(async move {
            if let Some(preface) = preface && tx.send(preface).await.is_err() { return; }
            loop {
                let (batch, terminal) = {
                    let inner = task.lock();
                    let batch: Vec<_> = inner.events.iter().filter(|(seq, _)| *seq > position).cloned().collect();
                    (batch, inner.projection.state().is_terminal())
                };
                for (seq, event) in batch {
                    position = seq;
                    if tx.send((seq, event)).await.is_err() { return; }
                }
                if terminal && task.latest_seq() <= position { return; }
                tokio::select! {
                    _ = tx.closed() => return,
                    result = changed.changed() => if result.is_err() { return; },
                }
            }
        });
        tokio_stream::wrappers::ReceiverStream::new(rx)
    }

    /// Wait until the task is terminal or waiting for input; returns the snapshot.
    pub async fn wait_settled(&self, timeout: Duration) -> Task {
        let mut changed = self.changed.subscribe();
        let _ = tokio::time::timeout(timeout, async {
            loop {
                let state = self.state();
                if state.is_terminal() || state.is_interrupted() { return; }
                if changed.changed().await.is_err() { return; }
            }
        }).await;
        self.snapshot()
    }

    pub fn push_configs(&self) -> Vec<TaskPushNotificationConfig> {
        self.lock().push.iter().map(|entry| entry.config.clone()).collect()
    }

    pub fn push_config(&self, id: &str) -> Option<TaskPushNotificationConfig> {
        self.lock().push.iter().find(|entry| entry.config.id.as_deref() == Some(id)).map(|entry| entry.config.clone())
    }

    /// Register (or replace by id) a push config; returns its stop token.
    pub fn add_push(&self, config: TaskPushNotificationConfig) -> CancellationToken {
        let stop = CancellationToken::new();
        let mut inner = self.lock();
        if let Some(existing) = inner.push.iter().position(|entry| entry.config.id == config.id) {
            inner.push.remove(existing).stop.cancel();
        }
        inner.push.push(PushEntry { config, stop: stop.clone() });
        stop
    }

    pub fn remove_push(&self, id: &str) {
        let mut inner = self.lock();
        inner.push.retain(|entry| {
            let keep = entry.config.id.as_deref() != Some(id);
            if !keep { entry.stop.cancel(); }
            keep
        });
    }

    fn expired(&self, now: Instant) -> bool {
        self.lock().finished_at.is_some_and(|at| now.saturating_duration_since(at) >= MEMORY_GRACE)
    }

    pub fn row(&self) -> A2aTaskRow {
        let task = self.snapshot();
        A2aTaskRow { task_id: self.id.clone(), owner: self.owner.clone(), bot: self.bot.clone(),
            state: state_name(task.status.state), snapshot: serde_json::to_string(&task).unwrap_or_default() }
    }
}

pub fn state_name(state: TaskState) -> String {
    serde_json::to_value(state).ok().and_then(|v| v.as_str().map(str::to_owned)).unwrap_or_default()
}

/// Per-Provider A2A task index.
#[derive(Default)]
pub struct A2aRegistry {
    tasks: Mutex<HashMap<String, Arc<A2aTask>>>,
    /// `(owner, bot, messageId)` → task id: a retried SendMessage re-attaches.
    by_message: Mutex<HashMap<(String, String, String), String>>,
}

impl A2aRegistry {
    pub fn get(&self, id: &str) -> Option<Arc<A2aTask>> {
        self.sweep();
        self.tasks.lock().unwrap_or_else(|p| p.into_inner()).get(id).cloned()
    }

    pub fn by_message(&self, owner: &str, bot: &str, message_id: &str) -> Option<Arc<A2aTask>> {
        let id = self.by_message.lock().unwrap_or_else(|p| p.into_inner())
            .get(&(owner.into(), bot.into(), message_id.into())).cloned()?;
        self.get(&id)
    }

    pub fn insert(&self, message_id: &str, task: Arc<A2aTask>) {
        self.by_message.lock().unwrap_or_else(|p| p.into_inner())
            .insert((task.owner.clone(), task.bot.clone(), message_id.into()), task.id.clone());
        self.tasks.lock().unwrap_or_else(|p| p.into_inner()).insert(task.id.clone(), task);
    }

    pub fn active(&self) -> Vec<Arc<A2aTask>> {
        self.tasks.lock().unwrap_or_else(|p| p.into_inner()).values().filter(|t| !t.state().is_terminal()).cloned().collect()
    }

    fn sweep(&self) {
        let now = Instant::now();
        let mut tasks = self.tasks.lock().unwrap_or_else(|p| p.into_inner());
        tasks.retain(|_, task| !task.expired(now));
        self.by_message.lock().unwrap_or_else(|p| p.into_inner()).retain(|_, id| tasks.contains_key(id));
    }
}

/// Drive the projection from the run stream until the run ends, persisting
/// the snapshot at start, on input-required and on termination.
pub fn spawn_projection(state: Arc<AppState>, task: Arc<A2aTask>, mut stream: RunStream) {
    tokio::spawn(async move {
        task.update(Projection::start);
        persist(&state, &task).await;
        while let Some(event) = stream.next().await {
            let before = task.state();
            task.update(|projection| projection.apply(&event.event));
            let after = task.state();
            if after != before && (after.is_terminal() || after.is_interrupted()) { persist(&state, &task).await; }
            if after.is_terminal() { break; }
        }
        if !task.state().is_terminal() {
            task.update(|projection| projection.finish_without_terminal("agent run ended without a result"));
            persist(&state, &task).await;
        }
    });
}

pub async fn persist(state: &AppState, task: &A2aTask) {
    if let Err(error) = state.sessions.save_a2a_task(task.row()).await {
        tracing::warn!(%error, task_id = %task.id, "failed to persist A2A task snapshot");
    }
}

/// Mark tasks interrupted by a Bridge restart as failed and prune old rows.
pub async fn recover(state: &AppState, ttl: Duration) {
    let cutoff = bcs_protocol::now_ms().saturating_sub(u64::try_from(ttl.as_millis()).unwrap_or(u64::MAX));
    if let Err(error) = state.sessions.prune_a2a_tasks(cutoff).await {
        tracing::warn!(%error, "failed to prune A2A task snapshots");
    }
    let rows = match state.sessions.unfinished_a2a_tasks(TERMINAL_STATES).await {
        Ok(rows) => rows,
        Err(error) => { tracing::warn!(%error, "failed to load unfinished A2A tasks"); return; }
    };
    for row in rows {
        let Ok(task) = serde_json::from_str::<Task>(&row.snapshot) else { continue };
        let mut projection = Projection::from_snapshot(task);
        let _ = projection.finish_without_terminal("bridge restarted while the task was running");
        let task = projection.task();
        let row = A2aTaskRow { state: state_name(task.status.state), snapshot: serde_json::to_string(task).unwrap_or_default(), ..row };
        if let Err(error) = state.sessions.save_a2a_task(row).await {
            tracing::warn!(%error, "failed to mark interrupted A2A task as failed");
        }
    }
}
