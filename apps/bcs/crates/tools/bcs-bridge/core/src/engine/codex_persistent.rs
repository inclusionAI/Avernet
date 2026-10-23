//! One owned app-server per engine instance. RPC IDs and thread subscriptions
//! are multiplexed so independent BCS conversations can run concurrently.
//! A process failure fails in-flight work; only a subsequent request restarts it.
use std::collections::{HashMap, HashSet, VecDeque};
use std::io;
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use serde_json::{Value, json};
use tokio::sync::{Mutex, mpsc, oneshot};
use tokio_util::sync::CancellationToken;

use super::cli::CliSession;
use super::codex_app_server::CodexAppServer;
use super::codex_transport::CodexConnection;
use super::trace::TraceContext;
use super::{TurnError, TurnRequest};

#[derive(Default)]
pub(super) struct NativeServer {
    server: Mutex<Option<Server>>,
    next_lease: AtomicU64,
    stopped: AtomicBool,
}

struct Server {
    commands: mpsc::UnboundedSender<Command>,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for Server {
    fn drop(&mut self) { self.task.abort(); }
}

enum Command {
    Open {
        lease: u64,
        thread: Option<String>,
        messages: mpsc::Sender<io::Result<String>>,
        ready: oneshot::Sender<io::Result<bool>>,
        trace: Option<TraceContext>,
    },
    Write { lease: u64, value: Value },
    Close { lease: u64, done: Option<oneshot::Sender<io::Result<()>>> },
}

struct Client {
    thread: Option<String>,
    turn: Option<String>,
    messages: mpsc::Sender<io::Result<String>>,
    trace: Option<TraceContext>,
}

struct Pending {
    lease: u64,
    id: Value,
    method: String,
    thread: Option<String>,
}

pub(super) struct ManagedConnection {
    lease: u64,
    commands: Option<mpsc::UnboundedSender<Command>>,
    messages: mpsc::Receiver<io::Result<String>>,
}

impl ManagedConnection {
    pub fn write_line(&mut self, line: &str) -> io::Result<()> {
        let value = serde_json::from_str(line).map_err(io::Error::other)?;
        self.commands.as_ref().ok_or_else(disconnected)?
            .send(Command::Write { lease: self.lease, value }).map_err(|_| disconnected())
    }

    pub async fn next_line(&mut self) -> io::Result<Option<String>> {
        self.messages.recv().await.transpose()
    }

    pub async fn cancel(&mut self) {
        if let Some(commands) = self.commands.take() {
            let (done, result) = oneshot::channel();
            if commands.send(Command::Close { lease: self.lease, done: Some(done) }).is_ok() {
                // The router bounds both the late start ACK and the interrupt ACK.
                // Do not return while either phase still owns the thread.
                let _ = result.await;
            }
        }
    }

    pub fn close(&mut self) {
        if let Some(commands) = self.commands.take() {
            let _ = commands.send(Command::Close { lease: self.lease, done: None });
        }
    }
}

impl Drop for ManagedConnection {
    fn drop(&mut self) { self.close(); }
}

impl NativeServer {
    pub async fn shutdown(&self) {
        self.stopped.store(true, Ordering::Release);
        if let Some(mut server) = self.server.lock().await.take() {
            server.task.abort();
            let _ = (&mut server.task).await;
        }
    }

    pub async fn connect(
        &self, engine: &CodexAppServer, bin: &Path, args: &[String],
        req: &TurnRequest, abort: &CancellationToken,
    ) -> Result<(CodexConnection, bool), TurnError> {
        let mut guard = tokio::select! {
            _ = abort.cancelled() => return Err(TurnError::Aborted),
            guard = self.server.lock() => guard,
        };
        if self.stopped.load(Ordering::Acquire) {
            return Err(TurnError::Protocol("Codex engine has been shut down".into()));
        }
        if guard.as_ref().is_some_and(|server| server.task.is_finished() || server.commands.is_closed())
            && let Some(mut server) = guard.take() {
            // Await cleanup before starting another process with the same threads.
            let _ = (&mut server.task).await;
        }
        if guard.is_none() {
            // Raw stdout is traced after demultiplexing, under the current run.
            let mut process = CliSession::spawn(bin, args, &req.cwd, &req.env,
                req.trace.as_ref().map(TraceContext::shared_process)).await.map_err(TurnError::Spawn)?;
            process.disable_raw_trace();
            let mut connection = CodexConnection::Process(process);
            let mut backlog = VecDeque::new();
            let mut next_id = 1;
            let initialization = engine.initialize(&mut connection, &mut next_id, &mut backlog, abort);
            tokio::time::timeout(std::time::Duration::from_secs(30), initialization).await
                .map_err(|_| TurnError::Protocol("Codex app-server initialization timed out".into()))??;
            let (commands, receiver) = mpsc::unbounded_channel();
            let task = tokio::spawn(async move { serve(connection, receiver, backlog).await });
            *guard = Some(Server { commands, task });
        }
        let commands = guard.as_ref().ok_or_else(|| TurnError::Io(disconnected()))?.commands.clone();
        drop(guard);
        let lease = self.next_lease.fetch_add(1, Ordering::Relaxed);
        let (messages, receiver) = mpsc::channel(256);
        let (ready, result) = oneshot::channel();
        let connection = ManagedConnection { lease, commands: Some(commands.clone()), messages: receiver };
        commands.send(Command::Open {
            lease, thread: req.engine_session_id.clone(), messages, ready, trace: req.trace.clone(),
        }).map_err(|_| disconnected())?;
        let loaded = tokio::select! {
            _ = abort.cancelled() => return Err(TurnError::Aborted),
            result = result => result.map_err(|_| disconnected())??,
        };
        Ok((CodexConnection::Managed(connection), loaded))
    }
}

struct InterruptWaiter {
    thread: Option<String>,
    deadline: tokio::time::Instant,
    done: Option<oneshot::Sender<io::Result<()>>>,
}

struct Router {
    clients: HashMap<u64, Client>,
    pending: HashMap<u64, Pending>,
    loaded: HashSet<String>,
    next_id: u64,
    interrupts: HashMap<u64, InterruptWaiter>,
    closing: HashMap<u64, InterruptWaiter>,
    server_requests: HashMap<String, (u64, Value)>,
}

impl Router {
    async fn write(&mut self, cli: &mut CodexConnection, lease: u64, mut value: Value) -> io::Result<()> {
        if !self.clients.contains_key(&lease) { return Ok(()); }
        if value.get("method").is_none() {
            // Responses to server requests retain the engine's original RPC id.
            // A different lease (or a stale reply after closure) cannot answer.
            let key = value["id"].to_string();
            if self.server_requests.get(&key).is_some_and(|(owner, _)| *owner == lease) {
                self.server_requests.remove(&key);
                return write_line(cli, &value.to_string()).await;
            }
            return Ok(());
        }
        if let Some(id) = value.get("id").cloned() {
            let wire_id = self.next_id;
            self.next_id += 1;
            self.pending.insert(wire_id, Pending {
                lease, id,
                method: value["method"].as_str().unwrap_or_default().into(),
                thread: value.pointer("/params/threadId").and_then(Value::as_str).map(str::to_owned),
            });
            value["id"] = json!(wire_id);
        }
        write_line(cli, &value.to_string()).await
    }

    async fn interrupt(&mut self, cli: &mut CodexConnection, thread: &str, turn: &str, done: Option<oneshot::Sender<io::Result<()>>>) -> io::Result<()> {
        let id = self.next_id;
        self.next_id += 1;
        self.interrupts.insert(id, InterruptWaiter {
            thread: Some(thread.to_owned()),
            deadline: tokio::time::Instant::now() + std::time::Duration::from_secs(5), done,
        });
        write_line(cli, &json!({"jsonrpc":"2.0", "id":id, "method":"turn/interrupt",
            "params":{"threadId":thread,"turnId":turn}}).to_string()).await
    }

    fn deliver(&self, lease: u64, value: &Value) -> io::Result<()> {
        if let Some(client) = self.clients.get(&lease) {
            if client.messages.is_closed() { return Ok(()); }
            let line = value.to_string();
            if let Some(trace) = &client.trace { trace.record_raw(&line); }
            match client.messages.try_send(Ok(line)) {
                Ok(()) | Err(mpsc::error::TrySendError::Closed(_)) => {},
                Err(mpsc::error::TrySendError::Full(_)) => return Err(io::Error::other("Codex event consumer is too slow")),
            }
        }
        Ok(())
    }

    async fn message(&mut self, cli: &mut CodexConnection, mut value: Value) -> io::Result<()> {
        if value.get("method").is_some() && value.get("id").is_some() {
            // Only forward questions with an exact active thread/turn owner.
            if value["method"] == "item/tool/requestUserInput" {
                let owner = self.clients.iter().find_map(|(lease, client)| {
                    match (&client.thread, &client.turn) {
                        (Some(thread), Some(turn)) if super::codex_app_server::matches_turn(&value, thread, turn, true)
                            && !client.messages.is_closed() => Some(*lease),
                        _ => None,
                    }
                });
                if let Some(lease) = owner {
                    let key = value["id"].to_string();
                    if !self.server_requests.contains_key(&key) {
                        self.server_requests.insert(key, (lease, value["id"].clone()));
                        self.deliver(lease, &value)?;
                    }
                    return Ok(());
                }
            }
            // Unsupported or unowned requests never reach another conversation.
            return write_line(cli, &json!({"jsonrpc":"2.0", "id":value["id"],
                "error":{"code":-32000,"message":"bridge-provider does not support app-server server requests"}}).to_string()).await;
        }
        if let Some(id) = value.get("id").and_then(Value::as_u64) {
            if let Some(InterruptWaiter { done, .. }) = self.interrupts.remove(&id) {
                if value.get("error").is_some() {
                    return Err(io::Error::other("Codex rejected turn interruption; restarting the owned server"));
                }
                if let Some(done) = done { let _ = done.send(Ok(())); }
                return Ok(());
            }
            if let Some(pending) = self.pending.remove(&id) {
                if let Some(thread) = value.pointer("/result/thread/id").and_then(Value::as_str)
                    && matches!(pending.method.as_str(), "thread/start" | "thread/resume") {
                    self.loaded.insert(thread.into());
                    if let Some(client) = self.clients.get_mut(&pending.lease) {
                        client.thread = Some(thread.into());
                    }
                }
                if pending.method == "turn/start"
                    && let Some(turn) = value.pointer("/result/turn/id").and_then(Value::as_str) {
                    if let Some(client) = self.clients.get_mut(&pending.lease) {
                        client.turn = Some(turn.into());
                    } else if let Some(thread) = &pending.thread {
                        // Cancellation can happen before turn/start acknowledges.
                        let done = self.closing.remove(&pending.lease).and_then(|waiter| waiter.done);
                        self.interrupt(cli, thread, turn, done).await?;
                    }
                }
                if pending.method == "turn/start"
                    && let Some(waiter) = self.closing.remove(&pending.lease) {
                    if value.get("error").is_none() {
                        return Err(io::Error::other("Cancelled Codex turn/start response lacked a turn id; restarting the owned server"));
                    }
                    if let Some(done) = waiter.done { let _ = done.send(Ok(())); }
                }
                value["id"] = pending.id;
                self.deliver(pending.lease, &value)?;
            }
            return Ok(());
        }
        if value["method"] == "serverRequest/resolved" {
            let key = value["params"]["requestId"].to_string();
            if let Some((lease, _)) = self.server_requests.remove(&key) {
                self.deliver(lease, &value)?;
            }
            return Ok(());
        }
        let thread = value.pointer("/params/threadId").and_then(Value::as_str);
        if value["method"] == "thread/closed" && let Some(thread) = thread {
            self.loaded.remove(thread);
        }
        let owners: Vec<_> = self.clients.iter().filter_map(|(lease, client)| {
            let matches = if let Some(thread) = thread {
                client.thread.as_deref() == Some(thread)
            } else {
                // Compatibility with older dedicated stdio notification shapes.
                self.clients.len() == 1
            };
            matches.then_some(*lease)
        }).collect();
        for lease in owners {
            if let Some(client) = self.clients.get_mut(&lease) {
                if let (Some(thread), Some(turn)) = (&client.thread, &client.turn)
                    && !super::codex_app_server::matches_turn(&value, thread, turn, false) {
                    // Interrupt ACKs may precede a cancelled turn's final event.
                    // Never deliver that stale event to a follow-up on this thread.
                    continue;
                }
                if matches!(value["method"].as_str(), Some("turn/completed" | "agent/turn_completed" | "agent/turn_failed")) {
                    client.turn = None;
                }
            }
            self.deliver(lease, &value)?;
        }
        Ok(())
    }
}

async fn serve(mut cli: CodexConnection, mut commands: mpsc::UnboundedReceiver<Command>, mut backlog: VecDeque<Value>) {
    let mut router = Router { clients: HashMap::new(), pending: HashMap::new(), loaded: HashSet::new(), next_id: 10, interrupts: HashMap::new(), closing: HashMap::new(), server_requests: HashMap::new() };
    let mut waiting = VecDeque::new();
    let result: io::Result<()> = async {
        loop {
            if let Some(value) = backlog.pop_front() {
                router.message(&mut cli, value).await?;
                continue;
            }
            let ready = waiting.iter().position(|command| match command {
                Command::Open { thread, .. } => !thread.as_ref().is_some_and(|thread|
                    router.closing.values().chain(router.interrupts.values()).any(|waiter| waiter.thread.as_ref() == Some(thread))),
                _ => true,
            });
            tokio::select! {
                command = async {
                    if let Some(index) = ready { waiting.remove(index) } else { commands.recv().await }
                } => match command {
                    None => return Ok(()),
                    Some(Command::Open { lease, thread, messages, ready, trace }) => {
                        if thread.as_ref().is_some_and(|thread| router.closing.values().chain(router.interrupts.values()).any(|waiter| waiter.thread.as_ref() == Some(thread))) {
                            waiting.push_back(Command::Open { lease, thread, messages, ready, trace });
                            continue;
                        }
                        if thread.as_ref().is_some_and(|thread| router.clients.values().any(|client| client.thread.as_ref() == Some(thread))) {
                            let _ = ready.send(Err(io::Error::other("Codex thread already has an active Bridge turn")));
                            continue;
                        }
                        let loaded = thread.as_ref().is_some_and(|thread| router.loaded.contains(thread));
                        router.clients.insert(lease, Client { thread, turn: None, messages, trace });
                        let _ = ready.send(Ok(loaded));
                    }
                    Some(Command::Write { lease, value }) => router.write(&mut cli, lease, value).await?,
                    Some(Command::Close { lease, done }) => {
                        waiting.retain(|command| !matches!(command, Command::Open { lease: waiting_lease, .. } if *waiting_lease == lease));
                        let abandoned: Vec<_> = router.server_requests.iter()
                            .filter(|(_, (owner, _))| *owner == lease).map(|(key, (_, id))| (key.clone(), id.clone())).collect();
                        for (key, id) in abandoned {
                            router.server_requests.remove(&key);
                            write_line(&mut cli, &json!({"jsonrpc":"2.0", "id":id,
                                "error":{"code":-32000,"message":"Bridge turn closed before user input"}}).to_string()).await?;
                        }
                        if let Some(client) = router.clients.remove(&lease)
                            && let (Some(thread), Some(turn)) = (client.thread, client.turn) {
                            router.interrupt(&mut cli, &thread, &turn, done).await?;
                        } else if let Some(pending) = router.pending.values().find(|pending| pending.lease == lease && pending.method == "turn/start") {
                            router.closing.insert(lease, InterruptWaiter {
                                thread: pending.thread.clone(),
                                deadline: tokio::time::Instant::now() + std::time::Duration::from_secs(5), done,
                            });
                        } else if let Some(done) = done { let _ = done.send(Ok(())); }
                    }
                },
                _ = tokio::time::sleep_until(router.interrupts.values().chain(router.closing.values()).map(|waiter| waiter.deadline).min()
                    .unwrap_or_else(|| tokio::time::Instant::now() + std::time::Duration::from_secs(86400))),
                    if !router.interrupts.is_empty() || !router.closing.is_empty() => {
                    return Err(io::Error::new(io::ErrorKind::TimedOut, "Codex turn interruption was not acknowledged; restarting the owned server"));
                }
                line = cli.next_line() => {
                    let line = line?.ok_or_else(disconnected)?;
                    let value = serde_json::from_str(&line).map_err(io::Error::other)?;
                    router.message(&mut cli, value).await?;
                }
            }
        }
    }.await;
    // Close admissions before notifying callers, so their next turn can restart.
    commands.close();
    if let Err(error) = result {
        tracing::warn!(target: "bcs_bridge_core::engine", "Codex app-server connection lost: {error}");
        for client in router.clients.values() {
            let _ = client.messages.try_send(Err(io::Error::new(error.kind(), error.to_string())));
        }
    }
    cli.kill().await;
}

async fn write_line(cli: &mut CodexConnection, line: &str) -> io::Result<()> {
    tokio::time::timeout(std::time::Duration::from_secs(5), cli.write_line(line)).await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "Codex app-server stdin stalled"))?
}

fn disconnected() -> io::Error {
    io::Error::new(io::ErrorKind::BrokenPipe, "Codex app-server connection closed; the next request will restart it (current request was not replayed)")
}
