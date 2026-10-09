//! Outgoing Bot WebSocket V2 transport. Runs and delivery cursors outlive sockets.
use std::{collections::{BTreeMap, VecDeque}, sync::Arc, time::{Duration, Instant}};
use anyhow::{anyhow, Context};
use bcs_protocol::{BcsFrame, BotConnectParams, BotConnectResponse, RequestFrame};
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::{net::TcpStream, task::JoinSet};
use tokio_tungstenite::{connect_async_with_config, tungstenite::{Message, protocol::WebSocketConfig}, MaybeTlsStream, WebSocketStream};
use tokio_util::sync::CancellationToken;

use crate::{config::{BotConfig, PluginConfig}, encoder::{Encoder, PluginEncoder, WebSocketV2Encoder, plugin_reply}, error::BridgeError,
    run::{RunEvent, RunHandle}, runtime::{self, AppState, RuntimeReply}, session::ConnectionIdentity};

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;
// Admission is bounded independently of each run's bounded structured buffer.
const MAX_PENDING_RUNS: usize = 16;
const MAX_ROUTES: usize = 1024;
const ROUTE_TTL: Duration = Duration::from_secs(300);

struct PendingRun {
    handle: RunHandle,
    encoder: PluginEncoder,
    cursor: u64,
    failed_delivery: bool,
}
struct Route {
    session: String,
    alias: String,
    group: String,
    /// Canonical session id exactly as the downlink delivery carried it
    /// (`bcs_session_id`, legacy `bcs_group_id` form). V3 uplink frames echo
    /// it byte-for-byte; BCS rejects a run/session identity mismatch.
    wire_session: String,
    cursor: u64,
    touched: Instant,
}
struct BotClient {
    state: Arc<AppState>,
    bot: BotConfig,
    config: PluginConfig,
    identity: ConnectionIdentity,
    /// Whether the current connection negotiated protocol V3. V3 selects the
    /// canonical Run Event uplink and enables interaction (HITL) support.
    protocol_v3: bool,
    pending: BTreeMap<String, PendingRun>,
    routes: BTreeMap<String, Route>,
    replies: VecDeque<BcsFrame>,
    wire_seq: u64,
}

enum ConnectionFailure {
    Retry(&'static str),
    Fatal(anyhow::Error),
    Kicked,
}

/// Run one independent client per configured Bot. Shutdown cancels and reaps
/// active engine turns; an ordinary socket error only reconnects its client.
pub async fn serve(state: Arc<AppState>, shutdown: CancellationToken) -> anyhow::Result<()> {
    let config = state.config.plugin.clone().context("plugin config required")?;
    let stop = shutdown.child_token();
    let mut clients = JoinSet::new();
    for bot in &state.config.bots {
        let state = state.clone();
        let bot = bot.clone();
        let config = config.clone();
        let stop = stop.clone();
        clients.spawn(async move { BotClient::new(state, bot, config).await?.run(stop).await });
    }
    let mut first_error = None;
    while let Some(result) = clients.join_next().await {
        match result {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                if first_error.is_none() { first_error = Some(error); }
                stop.cancel();
            }
            Err(error) => {
                if first_error.is_none() { first_error = Some(anyhow!("upstream client task failed: {error}")); }
                stop.cancel();
            }
        }
    }
    match first_error { Some(error) => Err(error), None => Ok(()) }
}

impl BotClient {
    async fn new(state: Arc<AppState>, bot: BotConfig, config: PluginConfig) -> anyhow::Result<Self> {
        let bot_id = bot.bot_id.clone().context("plugin bot_id required")?;
        let saved = state.sessions.load_identity(&config.url, &bot.provider_bot_ref).await?;
        let identity = match saved {
            Some(identity) if identity.bot_id != bot_id => return Err(anyhow!("stored identity does not match configured bot_id")),
            Some(identity) => identity,
            None => ConnectionIdentity { bot_id, token:bot.token.clone().unwrap_or_default() },
        };
        Ok(Self {state,bot,config,identity,protocol_v3:false,pending:BTreeMap::new(),routes:BTreeMap::new(),replies:VecDeque::new(),wire_seq:0})
    }

    async fn run(mut self, stop: CancellationToken) -> anyhow::Result<()> {
        let outcome = loop {
            let connected = tokio::select! {
                _ = stop.cancelled() => break Ok(()),
                connected = self.connect() => connected,
            };
            let result = match connected {
                Ok((mut socket, identity)) => {
                    // Never cancel a spawned SQLite commit midway through
                    // shutdown: await it before releasing the database owner.
                    match self.state.sessions.save_identity(&self.config.url, &self.bot.provider_bot_ref, identity.clone()).await {
                        Ok(()) => {
                            self.identity = identity;
                            tracing::info!(provider_bot_ref = %self.bot.provider_bot_ref, bot_id = %self.identity.bot_id,
                                version = if self.protocol_v3 { 3 } else { 2 },
                                "upstream Bot connected with the negotiated Bot WebSocket protocol");
                            // A renegotiated version retargets every live run: its
                            // uplink encoder and interaction support follow the
                            // new connection.
                            self.refresh_run_transport();
                            if stop.is_cancelled() { Ok(()) } else { self.connected(&mut socket, &stop).await }
                        }
                        Err(error) => Err(ConnectionFailure::Fatal(anyhow!("cannot persist plugin identity: {error}"))),
                    }
                },
                Err(error) => Err(error),
            };
            match result {
                Ok(()) => break Ok(()),
                Err(ConnectionFailure::Kicked) => {
                    tracing::warn!(provider_bot_ref = %self.bot.provider_bot_ref, "BCS kicked upstream Bot; reconnection stopped");
                    break Ok(());
                }
                Err(ConnectionFailure::Fatal(error)) => break Err(error),
                Err(ConnectionFailure::Retry(reason)) => {
                    tracing::warn!(provider_bot_ref = %self.bot.provider_bot_ref, reason, pending_runs = self.pending.len(),
                        "upstream disconnected; retaining runs for reconnect");
                }
            }
            tokio::select! {
                _ = stop.cancelled() => break Ok(()),
                _ = tokio::time::sleep(Duration::from_millis(self.config.reconnect_interval_ms)) => {}
            }
        };
        for pending in self.pending.values() {
            if !pending.handle.is_terminal() { pending.handle.request_abort("shutdown"); }
        }
        let cleanup = tokio::time::timeout(Duration::from_secs(5), async {
            while self.pending.values().any(|pending| !pending.handle.is_terminal()) {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }).await;
        for pending in self.pending.values() { pending.handle.release_delivery(); }
        if cleanup.is_err() { return Err(anyhow!("upstream engine shutdown did not finish within 5 seconds")); }
        outcome
    }

    fn timeout(&self) -> Duration { Duration::from_millis(self.config.connect_timeout_ms) }

    /// Re-point every pending run at this connection's negotiated protocol:
    /// swap the uplink encoder and re-declare interaction support. Runs born
    /// under this connection already carry the right state; this covers runs
    /// retained across a reconnect.
    fn refresh_run_transport(&mut self) {
        let provider_id = self.state.config.provider_id.clone();
        let bot_ref = self.bot.provider_bot_ref.clone();
        for pending in self.pending.values_mut() {
            let(v3, handle) = (self.protocol_v3, &pending.handle);
            handle.set_interaction_uplink(v3);
            if let Some(route) = self.routes.get(&pending.handle.run_id) {
                pending.encoder = PluginEncoder::for_protocol(v3, &provider_id, &bot_ref,
                    &route.group, &route.wire_session);
            }
        }
    }

    async fn connect(&mut self) -> Result<(Socket, ConnectionIdentity), ConnectionFailure> {
        let wire_config = WebSocketConfig::default()
            .max_message_size(Some(crate::sse::MAX_FRAME_BYTES))
            .max_frame_size(Some(crate::sse::MAX_FRAME_BYTES));
        let (mut socket, _) = tokio::time::timeout(self.timeout(), connect_async_with_config(&self.config.url, Some(wire_config), false))
            .await.map_err(|_| ConnectionFailure::Retry("WebSocket connect timeout"))?
            .map_err(|_| ConnectionFailure::Retry("WebSocket connect failed"))?;
        let id = format!("bridge-connect-{}",uuid::Uuid::new_v4());
        let params = BotConnectParams {
            bot_id:Some(self.identity.bot_id.clone()),
            token:(!self.identity.token.is_empty()).then(|| self.identity.token.clone()),
            // V3 uplink: canonical Run Event frames and HITL interaction
            // support. A server without V3 answers with its best version and
            // the client adapts (only 2 and 3 are accepted below).
            protocol_version:Some(3), client_kind:None,
        };
        let request = BcsFrame::Request(RequestFrame::new(&id,"bot.connect",Some(
            serde_json::to_value(params).map_err(|error| ConnectionFailure::Fatal(error.into()))?)));
        send(&mut socket, &request, self.timeout()).await?;
        let response = tokio::time::timeout(self.timeout(), async {
            loop {
                match receive(&mut socket, self.timeout()).await? {
                    Some(BcsFrame::Response(response)) if response.id == id => break Ok(response),
                    Some(BcsFrame::Event(event)) if event.event == "bot.kicked" => break Err(ConnectionFailure::Kicked),
                    // Application traffic before bot.connect is not accepted.
                    Some(BcsFrame::Request(request)) => {
                        send(&mut socket, &plugin_reply(&request.id,
                            Err(BridgeError::unavailable("bot.connect has not completed"))), self.timeout()).await?;
                    }
                    _ => {}
                }
            }
        }).await.map_err(|_| ConnectionFailure::Retry("bot.connect response timeout"))??;
        if !response.ok {
            let retry = response.error.as_ref().is_some_and(|error| error.retryable || error.code == "bot_id_conflict");
            return Err(if retry { ConnectionFailure::Retry("bot.connect temporarily rejected") }
                else { ConnectionFailure::Fatal(anyhow!("BCS rejected bot.connect; check Bot identity, token and protocol")) });
        }
        let connected: BotConnectResponse = serde_json::from_value(response.payload.unwrap_or(Value::Null))
            .map_err(|_| ConnectionFailure::Fatal(anyhow!("invalid bot.connect response")))?;
        if !matches!(connected.protocol_version, 2 | 3)
            || connected.bot_uuid != self.identity.bot_id || connected.token.trim().is_empty() {
            return Err(ConnectionFailure::Fatal(anyhow!("bot.connect returned a mismatched identity, protocol or empty token")));
        }
        self.protocol_v3 = connected.protocol_version == 3;
        let identity = ConnectionIdentity {bot_id:connected.bot_uuid,token:connected.token};
        Ok((socket, identity))
    }

    async fn connected(&mut self, socket: &mut Socket, stop: &CancellationToken) -> Result<(), ConnectionFailure> {
        // A failed ACK write is retained alongside its accepted run. It must
        // precede every event after the new handshake as well.
        self.flush_replies(socket).await?;
        let mut heartbeat = tokio::time::interval(Duration::from_millis(self.config.heartbeat_interval_ms));
        heartbeat.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut delivery = tokio::time::interval(Duration::from_millis(20));
        delivery.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let mut heartbeat_pending: Option<(String, Instant)> = None;
        loop {
            tokio::select! {
                _ = stop.cancelled() => return Ok(()),
                _ = heartbeat.tick() => {
                    if heartbeat_pending.is_none() {
                        let id = format!("bridge-status-{}",uuid::Uuid::new_v4());
                        let busy = self.pending.values().any(|run| !run.handle.is_terminal());
                        send(socket,&BcsFrame::Request(RequestFrame::new(&id,"bot.status",Some(json!({"status":if busy {"busy"} else {"idle"}})))),self.timeout()).await?;
                        heartbeat_pending = Some((id,Instant::now()));
                    }
                }
                _ = delivery.tick() => {
                    if heartbeat_pending.as_ref().is_some_and(|(_, sent)| sent.elapsed() > self.timeout()) {
                        return Err(ConnectionFailure::Retry("bot.status response timeout"));
                    }
                    self.flush_events(socket).await?;
                }
                frame = receive(socket, self.timeout()) => {
                    match frame? {
                        Some(BcsFrame::Request(request)) => self.request(socket,request).await?,
                        Some(BcsFrame::Response(response)) => {
                            if heartbeat_pending.as_ref().is_some_and(|(id,_)| *id == response.id) {
                                if !response.ok { return Err(ConnectionFailure::Retry("bot.status rejected")); }
                                heartbeat_pending = None;
                            }
                        }
                        Some(BcsFrame::Event(event)) if event.event == "bot.kicked" => return Err(ConnectionFailure::Kicked),
                        _ => {}
                    }
                }
            }
        }
    }

    async fn request(&mut self, socket: &mut Socket, request: RequestFrame) -> Result<(), ConnectionFailure> {
        let request_id = request.id.clone();
        let result = self.dispatch(request).await;
        self.replies.push_back(plugin_reply(&request_id,result));
        self.flush_replies(socket).await
    }

    async fn dispatch(&mut self, request: RequestFrame) -> Result<Value, BridgeError> {
        self.routes.retain(|run_id,route| self.pending.contains_key(run_id) || route.touched.elapsed() < ROUTE_TTL);
        let wire_group = request.params.as_ref().and_then(|p| p.get("bcs_group_id")).and_then(Value::as_str).unwrap_or_default().to_string();
        let alias = request.params.as_ref().and_then(|p| p.get("session_key")).and_then(Value::as_str).unwrap_or_default().to_string();
        let decoder = WebSocketV2Encoder::new(&self.state.config.provider_id,&self.bot.provider_bot_ref,&wire_group);
        let mut command = decoder.decode(request)?;
        if command.method == "chat.abort" {
            let target = command.params.as_ref().and_then(|p| p.get("run_id")).and_then(Value::as_str);
            if let Some(route) = target.and_then(|id| self.routes.get(id)) {
                if alias != route.alias && alias != route.session && alias != route.group {
                    return Err(BridgeError::invalid_request("abort session does not match run"));
                }
                command.session_id = Some(route.session.clone());
            } else {
                let mut sessions = self.routes.values().filter(|route| route.alias == alias).map(|route| &route.session);
                if let Some(session) = sessions.next() {
                    if sessions.any(|other| other != session) {
                        return Err(BridgeError::invalid_request("ambiguous session_key; specify run_id"));
                    }
                    command.session_id = Some(session.clone());
                }
            }
        }
        let run_id = command.id.clone();
        let session = command.session_id.clone().unwrap_or_default();
        if command.method == "chat.send" {
            if !self.pending.contains_key(&run_id) && self.pending.len() >= MAX_PENDING_RUNS {
                return Err(BridgeError::unavailable("upstream delivery queue is full"));
            }
            if !self.routes.contains_key(&run_id) && self.routes.len() >= MAX_ROUTES {
                return Err(BridgeError::unavailable("upstream routing cache is full"));
            }
            if let Some(route) = self.routes.get(&run_id) {
                if route.session != session || route.group != wire_group { return Err(BridgeError::conflict()); }
            }
        }
        // The canonical session id exactly as delivered: `bcs_session_id`
        // (V3 materializes it; V2 may rely on the legacy `bcs_group_id` form).
        // Mirrors the decoder's session selection so uplink echo stays exact.
        let wire_session = request_params_session(command.params.as_ref()).unwrap_or_else(|| wire_group.clone());
        match runtime::dispatch(self.state.clone(),command).await? {
            RuntimeReply::Json {status,body} if status >= 400 => {
                let mut error = BridgeError::invalid_request("runtime request rejected");
                error.status = status;
                if let Some(message) = body.pointer("/error/message").and_then(Value::as_str) { error.message = message.into(); }
                if status == 410 { error = BridgeError::run_terminated(); }
                Err(error)
            }
            RuntimeReply::Json {body,..} => Ok(body),
            RuntimeReply::Run {handle,stream} => {
                // The runtime stream is gateway-facing. Upstream reads its
                // durable-in-process event buffer using its own delivery cursor.
                drop(stream);
                handle.set_interaction_uplink(self.protocol_v3);
                let cursor = self.routes.get(&run_id).map_or(0, |route| route.cursor);
                self.routes.entry(run_id.clone()).or_insert_with(|| Route {
                    session,alias,group:wire_group.clone(),wire_session,cursor,touched:Instant::now(),
                });
                let route = self.routes.get(&run_id).expect("route just inserted");
                let encoder = PluginEncoder::for_protocol(self.protocol_v3,
                    &self.state.config.provider_id,&self.bot.provider_bot_ref,&route.group,&route.wire_session);
                self.pending.entry(run_id.clone()).or_insert_with(|| PendingRun {handle,encoder,cursor,failed_delivery:false});
                Ok(json!({"run_id":run_id}))
            }
        }
    }

    async fn flush_replies(&mut self, socket: &mut Socket) -> Result<(), ConnectionFailure> {
        while let Some(reply) = self.replies.front() {
            send(socket,reply,self.timeout()).await?;
            self.replies.pop_front();
        }
        Ok(())
    }

    async fn flush_events(&mut self, socket: &mut Socket) -> Result<(), ConnectionFailure> {
        let timeout = self.timeout();
        let mut delivered = Vec::new();
        for (run_id,pending) in &mut self.pending {
            if pending.failed_delivery {
                if pending.handle.is_terminal() {
                    pending.handle.release_delivery();
                    delivered.push(run_id.clone());
                }
                continue;
            }
            // One event per run per tick prevents a busy session starving others.
            if let Some(event) = pending.handle.next_after(pending.cursor) {
                let (frame, rejected) = match pending.encoder.encode(&event) {
                    Ok(frame) => (frame, false),
                    Err(error) => {
                        let terminal = RunEvent {run_id:run_id.clone(),seq:event.seq,ts:event.ts,
                            event:crate::sse::chat_error(run_id,&error.to_string(),Some("encoding_error"))};
                        (pending.encoder.encode(&terminal)
                            .map_err(|_| ConnectionFailure::Fatal(anyhow!("cannot encode bounded upstream error")))?, true)
                    }
                };
                if let Some(mut frame) = frame {
                    // V2 stamps the outer seq with the per-Bot wire counter;
                    // V3 requires the outer seq to equal the per-run payload
                    // seq the encoder already filled in.
                    if !self.protocol_v3 {
                        if let BcsFrame::Event(event) = &mut frame { event.seq = Some(self.wire_seq + 1); }
                    }
                    send(socket,&frame,timeout).await?;
                    self.wire_seq += 1;
                }
                if rejected {
                    // Report the transport failure before dropping the rest of
                    // this run. Other Bots/sessions keep their connections.
                    pending.failed_delivery = true;
                    pending.handle.request_abort("encoding_error");
                }
                pending.cursor = event.seq;
                if let Some(route) = self.routes.get_mut(run_id) { route.cursor = pending.cursor; route.touched = Instant::now(); }
            } else if pending.handle.is_terminal() {
                // Check terminal BEFORE the empty snapshot again: a final may
                // have been appended between the first snapshot and this flag.
                if pending.handle.next_after(pending.cursor).is_none() {
                    pending.handle.release_delivery();
                    delivered.push(run_id.clone());
                }
            }
        }
        for run_id in delivered { self.pending.remove(&run_id); }
        Ok(())
    }
}

/// The canonical session id a downlink chat frame carried, exactly as sent
/// (`bcs_session_id` when present, the legacy `bcs_group_id` form otherwise).
fn request_params_session(params: Option<&Value>) -> Option<String> {
    let params = params?;
    let session = params.get("bcs_session_id").and_then(Value::as_str)
        .or_else(|| params.get("bcs_group_id").and_then(Value::as_str))?;
    (!session.trim().is_empty()).then(|| session.to_string())
}

async fn send(socket: &mut Socket, frame: &BcsFrame, timeout: Duration) -> Result<(), ConnectionFailure> {
    let text = serde_json::to_string(frame).map_err(|error| ConnectionFailure::Fatal(error.into()))?;
    tokio::time::timeout(timeout,socket.send(Message::Text(text.into()))).await
        .map_err(|_| ConnectionFailure::Retry("WebSocket write timeout"))?
        .map_err(|_| ConnectionFailure::Retry("WebSocket write failed"))
}

async fn receive(socket: &mut Socket, timeout: Duration) -> Result<Option<BcsFrame>, ConnectionFailure> {
    match socket.next().await {
        Some(Ok(Message::Text(text))) => serde_json::from_str(&text).map(Some)
            .map_err(|_| ConnectionFailure::Retry("invalid BCS frame")),
        Some(Ok(Message::Ping(_))) => {
            // tungstenite has queued the corresponding Pong.
            tokio::time::timeout(timeout,socket.flush()).await
                .map_err(|_| ConnectionFailure::Retry("Pong write timeout"))?
                .map_err(|_| ConnectionFailure::Retry("Pong write failed"))?;
            Ok(None)
        }
        Some(Ok(Message::Pong(_))) => Ok(None),
        Some(Ok(Message::Close(_))) | None => Err(ConnectionFailure::Retry("WebSocket closed")),
        Some(Err(_)) => Err(ConnectionFailure::Retry("WebSocket read failed")),
        _ => Err(ConnectionFailure::Retry("unsupported WebSocket frame")),
    }
}
