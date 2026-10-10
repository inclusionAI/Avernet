use crate::{Active, Inner, Input, Options, Peer, MAX_BODY, QUEUE_SIZE};
use axum::{
    extract::{
        ws::{Message, WebSocket},
        DefaultBodyLimit, State as AxumState, WebSocketUpgrade,
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use futures::StreamExt;
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex, MutexGuard,
    },
    time::Duration,
};
use tokio::sync::{mpsc, Notify, Semaphore};
use tokio_util::sync::CancellationToken;

pub(crate) struct State {
    pub options: Options,
    inner: Mutex<Inner>,
    pub faulted: AtomicBool,
    pub ready: Notify,
    lookup: Semaphore,
}

impl State {
    pub fn new(options: Options) -> Self {
        Self {
            options,
            inner: Mutex::new(Inner {
                peer: None,
                active: Default::default(),
                sessions: Default::default(),
            }),
            faulted: AtomicBool::new(false),
            ready: Notify::new(),
            lookup: Semaphore::new(1),
        }
    }
    pub fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
    pub fn remove(&self, request: &str) {
        let mut inner = self.lock();
        inner.active.remove(request);
        inner.sessions.retain(|_, req| req != request);
    }
    pub fn add(
        &self,
        request: &str,
        conversation: &str,
        tx: mpsc::Sender<Input>,
        prompt: &str,
    ) -> Result<(), String> {
        let mut inner = self.lock();
        if inner.active.len() >= crate::MAX_ACTIVE {
            return Err("QwenWork active turn limit reached".into());
        }
        if inner
            .active
            .values()
            .any(|a| a.conversation == conversation)
        {
            return Err("QwenWork conversation already running".into());
        }
        inner.active.insert(
            request.into(),
            Active {
                conversation: conversation.into(),
                prompt: prompt.into(),
                request_set: None,
                tx,
            },
        );
        Ok(())
    }
}

pub(crate) fn router(state: Arc<State>) -> Router {
    Router::new()
        .route("/", get(upgrade))
        .route("/ws", get(upgrade))
        .route("/hooks", post(hook))
        .route("/health", get(health))
        .layer(DefaultBodyLimit::max(MAX_BODY))
        .with_state(state)
}

async fn health(AxumState(state): AxumState<Arc<State>>) -> Json<Value> {
    let inner = state.lock();
    Json(
        json!({"qwen_connected": inner.peer.is_some(), "active_runs": inner.active.len(),
        "faulted": state.faulted.load(Ordering::SeqCst)}),
    )
}

async fn upgrade(AxumState(state): AxumState<Arc<State>>, ws: WebSocketUpgrade) -> Response {
    ws.max_message_size(MAX_BODY)
        .max_frame_size(MAX_BODY)
        .on_upgrade(|socket| connection(state, socket))
}

fn ack(frame: &Value, code: u32) -> Value {
    json!({"headers":{"req_id":frame.pointer("/headers/req_id")},"errcode":code,"errmsg":if code==0 {"ok"} else {"rejected"}})
}

async fn send(socket: &mut WebSocket, value: &Value) -> Result<(), ()> {
    tokio::time::timeout(
        Duration::from_secs(5),
        socket.send(Message::Text(value.to_string().into())),
    )
    .await
    .map_err(|_| ())?
    .map_err(|_| ())
}

async fn connection(state: Arc<State>, mut socket: WebSocket) {
    let frame = match tokio::time::timeout(Duration::from_secs(5), socket.next()).await {
        Ok(Some(Ok(Message::Text(text)))) => match serde_json::from_str::<Value>(&text) {
            Ok(v) => v,
            Err(_) => return,
        },
        _ => return,
    };
    if frame.get("cmd").and_then(Value::as_str) != Some("aibot_subscribe")
        || frame.pointer("/body/bot_id").and_then(Value::as_str) != Some(&state.options.bot_id)
        || frame.pointer("/body/secret").and_then(Value::as_str) != Some(&state.options.secret)
    {
        let _ = send(&mut socket, &ack(&frame, 1)).await;
        return;
    }
    let (tx, mut rx) = mpsc::channel(QUEUE_SIZE);
    let peer = Peer {
        id: uuid::Uuid::new_v4().to_string(),
        tx,
        closed: CancellationToken::new(),
    };
    let accepted = {
        let mut inner = state.lock();
        if inner.peer.is_some() {
            false
        } else {
            inner.peer = Some(peer.clone());
            true
        }
    };
    if !accepted {
        let _ = send(&mut socket, &ack(&frame, 1)).await;
        return;
    }
    if send(&mut socket, &ack(&frame, 0)).await.is_ok() {
        state.ready.notify_waiters();
        loop {
            tokio::select! {
                _ = peer.closed.cancelled() => break,
                outgoing = rx.recv() => {
                    let Some(value) = outgoing else { break; };
                    if send(&mut socket, &value).await.is_err() { break; }
                }
                incoming = socket.next() => {
                    let Some(Ok(message)) = incoming else { break; };
                    match message {
                        Message::Text(text) => {
                            let Ok(frame) = serde_json::from_str::<Value>(&text) else { break; };
                            let result = receive(&state, &frame);
                            if send(&mut socket, &ack(&frame, if result {0} else {1})).await.is_err() { break; }
                        }
                        Message::Ping(data) => { if socket.send(Message::Pong(data)).await.is_err() { break; } }
                        Message::Close(_) => break,
                        _ => {}
                    }
                }
            }
        }
    }
    // A rejected/new connection can never remove an older connection's sender.
    {
        let mut inner = state.lock();
        if inner.peer.as_ref().is_some_and(|p| p.id == peer.id) {
            inner.peer = None;
        }
    }
    peer.closed.cancel();
}

fn receive(state: &State, frame: &Value) -> bool {
    let Some(cmd) = frame.get("cmd").and_then(Value::as_str) else {
        return false;
    };
    if cmd == "ping" {
        return true;
    }
    if cmd != "aibot_respond_msg" {
        return false;
    }
    let Some(request) = frame.pointer("/headers/req_id").and_then(Value::as_str) else {
        return false;
    };
    let Some(text) = frame
        .pointer("/body/stream/content")
        .and_then(Value::as_str)
        .or_else(|| frame.pointer("/body/text/content").and_then(Value::as_str))
    else {
        return false;
    };
    let finished = frame
        .pointer("/body/stream/finish")
        .and_then(Value::as_bool)
        .unwrap_or_else(|| frame.pointer("/body/text/content").is_some());
    let inner = state.lock();
    let Some(active) = inner.active.get(request) else {
        return true;
    }; // late reply; acknowledge but don't reroute
    active
        .tx
        .try_send(Input::Snapshot {
            text: text.into(),
            finished,
        })
        .is_ok()
}

async fn hook(
    AxumState(state): AxumState<Arc<State>>,
    headers: HeaderMap,
    Json(value): Json<Value>,
) -> Response {
    let status = handle_hook(state, headers, value).await;
    if status == StatusCode::NO_CONTENT {
        Json(json!({"continue":true})).into_response()
    } else {
        status.into_response()
    }
}

async fn handle_hook(state: Arc<State>, headers: HeaderMap, value: Value) -> StatusCode {
    if headers.get("authorization").and_then(|h| h.to_str().ok())
        != Some(&format!("Bearer {}", state.options.hook_secret))
    {
        return StatusCode::UNAUTHORIZED;
    }
    let Some(event) = value.get("hook_event_name").and_then(Value::as_str) else {
        return StatusCode::BAD_REQUEST;
    };
    if !matches!(
        event,
        "PreToolUse" | "PostToolUse" | "PostToolUseFailure" | "Stop"
            | "UserPromptSubmit" | "QueryEnd" | "PreCompact" | "PostCompact"
    ) {
        return StatusCode::NO_CONTENT;
    }
    // Internal forks share the SDK session, but must never end a main run.
    if value.get("agent_id").and_then(Value::as_str).is_some_and(|id| !id.is_empty())
        || value.get("parent_request_set_id").and_then(Value::as_str).is_some_and(|id| !id.is_empty())
    {
        return StatusCode::NO_CONTENT;
    }
    let Some(session) = value
        .get("session_id")
        .and_then(Value::as_str)
        .map(str::to_owned)
    else {
        return StatusCode::BAD_REQUEST;
    };
    let cached = state.lock().sessions.get(&session).cloned();
    let request = match cached {
        Some(request) => request,
        None => {
            if state.lock().active.is_empty() {
                return StatusCode::NO_CONTENT;
            }
            // Serialize cold lookups; a short bounded timeout avoids holding up agent Hooks.
            let Ok(Ok(_permit)) =
                tokio::time::timeout(Duration::from_millis(500), state.lookup.acquire()).await
            else {
                return StatusCode::SERVICE_UNAVAILABLE;
            };
            let cached = state.lock().sessions.get(&session).cloned();
            if let Some(request) = cached {
                drop(_permit);
                return correlated_hook(&state, &request, &session, value);
            }
            let database = state.options.database_path.clone();
            let bot = state.options.bot_id.clone();
            let lookup_session = session.clone();
            let result = tokio::task::spawn_blocking(move || {
                crate::session::conversation(&database, &lookup_session, &bot)
            })
            .await;
            let conversation = match result {
                Ok(Ok(Some(conversation))) => conversation,
                Ok(Ok(None)) => return StatusCode::NO_CONTENT,
                _ => return StatusCode::SERVICE_UNAVAILABLE,
            };
            let mut inner = state.lock();
            let Some(request) = inner
                .active
                .iter()
                .find(|(_, a)| a.conversation == conversation)
                .map(|(r, _)| r.clone())
            else {
                return StatusCode::NO_CONTENT;
            };
            if inner.sessions.len() >= 4096 {
                return StatusCode::SERVICE_UNAVAILABLE;
            }
            inner.sessions.insert(session.clone(), request.clone());
            request
        }
    };
    correlated_hook(&state, &request, &session, value)
}

fn correlated_hook(state: &State, request: &str, session: &str, value: Value) -> StatusCode {
    let event = value["hook_event_name"].as_str().unwrap_or_default();
    if event == "UserPromptSubmit" || event == "QueryEnd" {
        let Some(query) = value.get("request_set_id").and_then(Value::as_str).filter(|id| !id.is_empty()) else {
            return StatusCode::BAD_REQUEST;
        };
        let mut inner = state.lock();
        let Some(active) = inner.active.get_mut(request) else { return StatusCode::NO_CONTENT; };
        if event == "UserPromptSubmit" {
            if active.request_set.is_some() || value.get("prompt").and_then(Value::as_str) != Some(&active.prompt) {
                return StatusCode::NO_CONTENT;
            }
            active.request_set = Some((session.into(), query.into()));
        } else if active.request_set.as_ref().map(|(_, id)| id.as_str()) != Some(query) {
            // A delayed QueryEnd from the previous turn cannot complete this turn.
            return StatusCode::NO_CONTENT;
        }
    }
    enqueue(state, request, value)
}

fn enqueue(state: &State, request: &str, value: Value) -> StatusCode {
    let tx = state.lock().active.get(request).map(|a| a.tx.clone());
    match tx {
        Some(tx) => match tx.try_send(Input::Hook(value)) {
            Ok(()) => StatusCode::NO_CONTENT,
            Err(mpsc::error::TrySendError::Full(_)) => {
                // No Hook retry guarantee exists. Fence every affected turn so
                // a dropped tool Hook cannot turn into a false final success.
                state.faulted.store(true, Ordering::SeqCst);
                if let Some(peer) = state.lock().peer.as_ref() {
                    peer.closed.cancel();
                }
                StatusCode::SERVICE_UNAVAILABLE
            }
            Err(mpsc::error::TrySendError::Closed(_)) => StatusCode::NO_CONTENT,
        },
        None => StatusCode::NO_CONTENT,
    }
}
