use std::{net::IpAddr, sync::Arc};
use std::time::{Duration, Instant};

use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use bcs_protocol::{
    AgentEventPayload, AgentStream, Attachment, BCN_MESSAGE_ID_HEADER, BCN_PROTOCOL_VERSION_HEADER,
    BCN_TIMESTAMP_HEADER, BCN_TRANSPORT_HEADER, BcsFrame, ChatEventPayload,
    ChatEventState as WireChatState, ContentBlock, MessageContent,
    ProviderAckResponse, ProviderHistoryResponse, ProviderWebhookBotRef, ProviderWebhookRequest,
    ProviderWebhookSender, RequestFrame,
};
use bcs_protocol::stream::{ChatState, StreamEvent, parse_stream_event};
use bcs_route_security::{OutboundUrlError, OutboundUrlGuard};
use bcs_service_api::{
    BotDeliveryCommand, BotDeliveryKind, BotDeliveryPort, BotDeliveryResult, BotEventCommand,
    BotRunContext, BotRunContextPort, ChatEventState, GroupHistoryBotRequestPort, MessageFlowService,
    ProviderTransportPreference, ServiceError, ServiceResult,
};
use serde::de::DeserializeOwned;
use serde_json::Value;
use tracing::{info, warn};

mod sse;

use crate::sse::{IngestKind, SeqDecision, SeqDedup, classify, parse_sse_block};

const DEFAULT_PROVIDER_DOWNLINK_TIMEOUT_MS: u64 = 60 * 60 * 1_000;

/// Idle timeout for an SSE read loop: if no bytes arrive within this window the
/// run is considered stuck and closed with a synthesized error terminal (#3).
const SSE_IDLE_TIMEOUT_MS: u64 = 120_000;
/// Bounded retry for resolving run context after `deliver()` returns but before
/// `put_context` lands (#2 put_context race): ~50ms * 20 ≈ 1s.
const SSE_CTX_RETRY_INTERVAL_MS: u64 = 50;
const SSE_CTX_RETRY_MAX: u32 = 20;
/// When a run's consumption lag crosses this, emit a single WARN (rising edge)
/// that the run is falling behind the producer; a matching WARN is emitted once
/// it recovers below the threshold. Edge-triggered so a sustained backlog logs
/// twice (enter + recover), not once per frame.
const SSE_LAG_ALERT_MS: u64 = 5_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct ProviderClientPolicy {
    total_timeout: Option<Duration>,
    read_timeout: Option<Duration>,
    http2_only: bool,
}

impl ProviderClientPolicy {
    fn for_request(accept_sse: bool) -> Self {
        if accept_sse {
            Self {
                total_timeout: None,
                read_timeout: Some(Duration::from_secs(125)),
                http2_only: true,
            }
        } else {
            Self {
                total_timeout: Some(Duration::from_secs(65)),
                read_timeout: None,
                http2_only: false,
            }
        }
    }
}

/// Edge-triggered tracker for a run's consumption lag, so a sustained backlog
/// produces exactly one "falling behind" WARN and one "recovered" WARN rather
/// than a per-frame flood.
#[derive(Default)]
struct LagTracker {
    alerting: bool,
    peak_lag_ms: u64,
}

pub struct HttpProviderTransport {
    /// Callback / history client with a 65s total timeout.
    client: reqwest::Client,
    /// HTTP/2-only SSE client with NO total timeout (#3): a total `.timeout()`
    /// would cut a long-lived stream. Idle detection is handled in the read loop.
    sse_client: reqwest::Client,
    url_guard: OutboundUrlGuard,
    message_flow: std::sync::RwLock<Option<Arc<dyn MessageFlowService>>>,
    bot_run_context: std::sync::RwLock<Option<Arc<dyn BotRunContextPort>>>,
}

impl HttpProviderTransport {
    pub fn new() -> Self {
        Self::with_url_guard(OutboundUrlGuard::strict())
    }

    pub fn allowing_private_networks_for_tests() -> Self {
        // Local contract servers are HTTP/1. Production constructors and every
        // DNS-pinned SSE client keep the strict HTTP/2-only policy.
        Self::with_url_guard_and_sse_policy(
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            ProviderClientPolicy {
                http2_only: false,
                ..ProviderClientPolicy::for_request(true)
            },
        )
    }

    pub fn with_url_guard(url_guard: OutboundUrlGuard) -> Self {
        Self::with_url_guard_and_sse_policy(
            url_guard,
            ProviderClientPolicy::for_request(true),
        )
    }

    fn with_url_guard_and_sse_policy(
        url_guard: OutboundUrlGuard,
        sse_policy: ProviderClientPolicy,
    ) -> Self {
        Self {
            client: provider_client_builder(ProviderClientPolicy::for_request(false))
                .build()
                .expect("build provider http client"),
            sse_client: provider_client_builder(sse_policy)
                .build()
                .expect("build provider sse client"),
            url_guard,
            message_flow: std::sync::RwLock::new(None),
            bot_run_context: std::sync::RwLock::new(None),
        }
    }

    /// Inject the ingest dependencies needed by the 2.0 SSE branch after
    /// construction. Using a shared `&self` setter allows the transport to be
    /// Arc-shared into bot_delivery before message_flow exists, resolving the
    /// circular-dependency bootstrap cycle.
    pub fn set_ingest(
        &self,
        message_flow: Arc<dyn MessageFlowService>,
        bot_run_context: Arc<dyn BotRunContextPort>,
    ) {
        *self.message_flow.write().expect("message_flow lock poisoned") = Some(message_flow);
        *self.bot_run_context.write().expect("bot_run_context lock poisoned") = Some(bot_run_context);
    }
}

impl Default for HttpProviderTransport {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl BotDeliveryPort for HttpProviderTransport {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool {
        target.is_http_provider()
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let target_bot_id = cmd.target_bot_id().to_string();
        if matches!(
            cmd.delivery_kind,
            BotDeliveryKind::Send
                | BotDeliveryKind::TaskDispatch
                | BotDeliveryKind::TaskMessage
                | BotDeliveryKind::TaskResult
        ) {
            let BcsFrame::Request(request) = &cmd.frame else {
                return Err(ServiceError::InvalidOperation {
                    message: "chat.send provider delivery requires request frame".to_string(),
                    request_id: None,
                });
            };
            if request.id != cmd.run_id {
                return Err(ServiceError::InvalidOperation {
                    message: "chat.send frame id must match run_id".to_string(),
                    request_id: Some(request.id.clone()),
                });
            }
        }
        let body = provider_request_from_frame(
            &cmd.target,
            &cmd.frame,
            DEFAULT_PROVIDER_DOWNLINK_TIMEOUT_MS,
        )?;
        let provider_id = body.to_bot.provider_id.clone();
        let provider_bot_ref = body.to_bot.provider_bot_ref.clone();
        let method = body.method.clone();
        let run_id = cmd.run_id.clone();
        let delivery_kind = format!("{:?}", cmd.delivery_kind);
        info!(
            target_bot_id = %target_bot_id,
            provider_id = %provider_id,
            provider_bot_ref = %provider_bot_ref,
            method = %method,
            run_id = %run_id,
            delivery_kind = %delivery_kind,
            "provider downlink: deliver start"
        );

        // Protocol 2.0: prefer SSE. Send with an SSE-capable Accept header on the
        // no-total-timeout client, then branch on the response Content-Type.
        let is_proto2 = matches!(
            &cmd.target,
            BotDeliveryTarget::HttpProvider { protocol_version, .. } if protocol_version == "2.0"
        );
        if is_proto2 {
            let wants_sse = matches!(
                cmd.provider_transport,
                ProviderTransportPreference::CallbackSse
            ) && method == "chat.send";
            let client = if wants_sse { &self.sse_client } else { &self.client };
            let resp =
                send_provider_request(client, &self.url_guard, &cmd.target, &body, wants_sse)
                    .await?;
            let ctype = resp
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|value| value.to_str().ok())
                .unwrap_or("")
                .to_string();
            if wants_sse && ctype.starts_with("text/event-stream") {
                let (Some(flow), Some(ctx)) =
                    (self.message_flow.read().expect("message_flow lock poisoned").clone(), self.bot_run_context.read().expect("bot_run_context lock poisoned").clone())
                else {
                    warn!(
                        target_bot_id = %target_bot_id,
                        provider_id = %provider_id,
                        run_id = %run_id,
                        "2.0 SSE response but ingest deps not wired"
                    );
                    return Err(ServiceError::InternalError(
                        "sse ingest deps not wired".to_string(),
                    ));
                };
                let spawn_run_id = run_id.clone();
                let spawn_bot_id = target_bot_id.clone();
                info!(
                    target_bot_id = %target_bot_id,
                    provider_id = %provider_id,
                    run_id = %run_id,
                    "provider downlink: 2.0 SSE stream accepted; spawning reader"
                );
                tokio::spawn(async move {
                    stream_and_drive(resp, spawn_run_id, spawn_bot_id, flow, ctx).await;
                });
                return Ok(BotDeliveryResult {
                    target_bot_id,
                    delivered: true,
                    error: None,
                });
            }
            // 2.0 + application/json: branch on the request method (D5 relaxed).
            //   - inject / abort / history (and any non-send): the provider
            //     ack's the POST with JSON; treat it as a simple ack, exactly
            //     like the 1.0 path. The events (if any) arrive separately via
            //     the upstream /bot/events callback (handled by submit_event).
            //   - send: a JSON response means "callback streaming" (transport
            //     =callback) — the actual chat/agent events come later over
            //     /bot/events. We still accept the ack here; whether downstream
            //     events are honored is gated by submit_event's protocol_version
            //     check (Capability B). We do NOT require SSE for send anymore.
            let status = resp.status();
            let ack = resp
                .json::<ProviderAckResponse>()
                .await
                .map_err(|error| {
                    warn!(
                        target_bot_id = %target_bot_id,
                        provider_id = %provider_id,
                        method = %method,
                        run_id = %run_id,
                        status = %status.as_u16(),
                        error = %error,
                        "provider downlink: 2.0 JSON ack decode failed"
                    );
                    ServiceError::InternalError(format!("decode 2.0 json ack: {error}"))
                })?;
            if ack.ok {
                info!(
                    target_bot_id = %target_bot_id,
                    provider_id = %provider_id,
                    method = %method,
                    run_id = %run_id,
                    "provider downlink: 2.0 JSON ack accepted (callback transport)"
                );
            } else {
                warn!(
                    target_bot_id = %target_bot_id,
                    provider_id = %provider_id,
                    method = %method,
                    run_id = %run_id,
                    error = %ack.error.as_deref().unwrap_or("provider rejected"),
                    "provider downlink: 2.0 JSON ack rejected by provider"
                );
            }
            return Ok(BotDeliveryResult {
                target_bot_id,
                delivered: ack.ok,
                error: (!ack.ok).then(|| {
                    ServiceError::InternalError(
                        ack.error.unwrap_or_else(|| "provider rejected".to_string()),
                    )
                }),
            });
        }

        let started = Instant::now();
        let ack_result: ServiceResult<ProviderAckResponse> =
            post_provider(&self.client, &self.url_guard, &cmd.target, &body).await;
        let elapsed_ms = started.elapsed().as_millis();
        let ack = match ack_result {
            Ok(ack) => ack,
            Err(error) => {
                warn!(
                    target_bot_id = %target_bot_id,
                    provider_id = %provider_id,
                    method = %method,
                    run_id = %run_id,
                    elapsed_ms = %elapsed_ms,
                    error = %error,
                    "provider downlink: deliver failed"
                );
                return Err(error);
            }
        };
        if ack.ok {
            info!(
                target_bot_id = %target_bot_id,
                provider_id = %provider_id,
                method = %method,
                run_id = %run_id,
                elapsed_ms = %elapsed_ms,
                "provider downlink: deliver acked"
            );
        } else {
            warn!(
                target_bot_id = %target_bot_id,
                provider_id = %provider_id,
                method = %method,
                run_id = %run_id,
                elapsed_ms = %elapsed_ms,
                error = %ack.error.as_deref().unwrap_or("provider rejected"),
                "provider downlink: deliver rejected by provider"
            );
        }
        Ok(BotDeliveryResult {
            target_bot_id,
            delivered: ack.ok,
            error: (!ack.ok).then(|| {
                ServiceError::InternalError(
                    ack.error.unwrap_or_else(|| "provider rejected".to_string()),
                )
            }),
        })
    }
}

#[async_trait]
impl GroupHistoryBotRequestPort for HttpProviderTransport {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        params: Value,
        timeout_ms: u64,
    ) -> Result<Value, String> {
        let request_id = uuid::Uuid::new_v4().to_string();
        let frame = BcsFrame::Request(RequestFrame::new(
            request_id,
            method.to_string(),
            Some(params),
        ));
        let body = provider_request_from_frame(&target, &frame, timeout_ms)
            .map_err(|error| error.to_string())?;
        let target_bot_id = target.bot_id().to_string();
        let response: ProviderHistoryResponse =
            post_provider(&self.client, &self.url_guard, &target, &body)
                .await
                .map_err(|error| error.to_string())?;
        let history_body = provider_history_log(&response);
        info!(
            target_bot_id = %target_bot_id,
            provider_id = %body.to_bot.provider_id,
            provider_bot_ref = %body.to_bot.provider_bot_ref,
            method = %body.method,
            frame_id = %body.id,
            session_id = %body.session_id,
            provider_session_id = ?response.session_id,
            bcn_group_id = %body.bcn_group_id,
            before = ?body.before,
            after = ?body.after,
            limit = ?body.limit,
            response_ok = %response.ok,
            message_count = %response.messages.len(),
            has_more = %response.has_more,
            next_before = ?response.next_before,
            next_after = ?response.next_after,
            history_body = %history_body,
            "provider downlink: history response"
        );
        if !response.ok {
            return Err("provider history response not ok".to_string());
        }
        Ok(serde_json::json!({
            "messages": response.messages,
            "has_more": response.has_more,
            "next_before": response.next_before,
            "next_after": response.next_after,
        }))
    }
}

pub struct BotTransportMux {
    websocket: Arc<dyn BotDeliveryPort>,
    provider: Arc<HttpProviderTransport>,
}

impl BotTransportMux {
    pub fn new(
        websocket: Arc<dyn BotDeliveryPort>,
        provider: Arc<HttpProviderTransport>,
    ) -> Self {
        Self {
            websocket,
            provider,
        }
    }
}

#[async_trait]
impl BotDeliveryPort for BotTransportMux {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool {
        match target {
            BotDeliveryTarget::WebSocket { .. } => self.websocket.is_available(target).await,
            BotDeliveryTarget::HttpProvider { .. } => self.provider.is_available(target).await,
        }
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        match &cmd.target {
            BotDeliveryTarget::WebSocket { .. } => self.websocket.deliver(cmd).await,
            BotDeliveryTarget::HttpProvider { .. } => self.provider.deliver(cmd).await,
        }
    }
}

pub struct HistoryRequestMux {
    websocket: Arc<dyn GroupHistoryBotRequestPort>,
    provider: Arc<HttpProviderTransport>,
}

impl HistoryRequestMux {
    pub fn new(
        websocket: Arc<dyn GroupHistoryBotRequestPort>,
        provider: Arc<HttpProviderTransport>,
    ) -> Self {
        Self {
            websocket,
            provider,
        }
    }
}

#[async_trait]
impl GroupHistoryBotRequestPort for HistoryRequestMux {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        params: Value,
        timeout_ms: u64,
    ) -> Result<Value, String> {
        if target.is_http_provider() {
            self.provider
                .send_history_request(target, method, params, timeout_ms)
                .await
        } else {
            self.websocket
                .send_history_request(target, method, params, timeout_ms)
                .await
        }
    }
}

fn provider_request_from_frame(
    target: &BotDeliveryTarget,
    frame: &BcsFrame,
    timeout_ms: u64,
) -> ServiceResult<ProviderWebhookRequest> {
    let BotDeliveryTarget::HttpProvider {
        provider_id,
        provider_bot_ref,
        ..
    } = target
    else {
        return Err(ServiceError::InvalidOperation {
            message: "provider_request_from_frame requires http provider target".to_string(),
            request_id: None,
        });
    };
    let BcsFrame::Request(request) = frame else {
        return Err(ServiceError::InvalidOperation {
            message: "provider delivery requires request frame".to_string(),
            request_id: None,
        });
    };
    let params = request.params.clone().unwrap_or(Value::Null);
    let bcs_group_id = params
        .get("bcs_group_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let session_id = provider_session_id(&params, &bcs_group_id);

    let callback_timeout_ms = if request.method == "chat.send" {
        params
            .get("timeout_ms")
            .and_then(Value::as_u64)
            .unwrap_or(timeout_ms)
    } else {
        timeout_ms
    };
    let attachments = params
        .get("attachments")
        .cloned()
        .map(serde_json::from_value::<Vec<Attachment>>)
        .transpose()
        .map_err(|error| ServiceError::InvalidOperation {
            message: format!("provider attachment payload is invalid: {error}"),
            request_id: Some(request.id.clone()),
        })?
        .unwrap_or_default();

    Ok(ProviderWebhookRequest {
        frame_type: "req".to_string(),
        id: request.id.clone(),
        method: request.method.clone(),
        session_id,
        bcn_group_id: bcs_group_id,
        to_bot: ProviderWebhookBotRef {
            provider_id: provider_id.clone(),
            provider_bot_ref: provider_bot_ref.clone(),
            tags: provider_tags_from_params(&params),
        },
        from: provider_sender_from_params(&params),
        message: params.get("message").cloned(),
        attachments,
        before: params.get("before").and_then(Value::as_u64),
        after: params.get("after").and_then(Value::as_u64),
        limit: params.get("limit").and_then(Value::as_u64),
        timeout_ms: callback_timeout_ms,
        extensions: provider_extensions_from_params(&params),
    })
}

fn provider_extensions_from_params(params: &Value) -> Option<Value> {
    let extensions = params.get("extensions")?;
    match extensions {
        Value::Object(map) if !map.is_empty() => Some(extensions.clone()),
        _ => None,
    }
}

fn provider_tags_from_params(params: &Value) -> Vec<String> {
    params
        .get("tags")
        .and_then(Value::as_array)
        .map(|tags| {
            tags.iter()
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|tag| !tag.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn provider_sender_from_params(params: &Value) -> Option<ProviderWebhookSender> {
    let channel = params.get("channel")?;
    let actor_id = channel
        .get("actor_id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|actor_id| !actor_id.is_empty());
    let name = channel
        .get("actor_name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .or(actor_id)?;
    Some(ProviderWebhookSender {
        kind: "bot".to_string(),
        name: name.to_string(),
        actor_id: actor_id.map(str::to_string),
    })
}

fn provider_session_id(params: &Value, bcs_group_id: &str) -> String {
    if let Some(session_id) = params.get("bcs_session_id").and_then(Value::as_str) {
        return session_id.to_string();
    }
    if let Some(session_id) = params.get("session_id").and_then(Value::as_str) {
        return session_id.to_string();
    }
    if let Some(session_key) = params.get("session_key").and_then(Value::as_str) {
        if !session_key.starts_with("group:") {
            return session_key.to_string();
        }
    }
    bcs_group_id.to_string()
}

async fn post_provider<T: DeserializeOwned>(
    client: &reqwest::Client,
    url_guard: &OutboundUrlGuard,
    target: &BotDeliveryTarget,
    body: &ProviderWebhookRequest,
) -> ServiceResult<T> {
    let response = send_provider_request(client, url_guard, target, body, false).await?;
    let status = response.status();
    let method = body.method.clone();
    let frame_id = body.id.clone();
    let provider_id = body.to_bot.provider_id.clone();
    response.json::<T>().await.map_err(|error| {
        warn!(
            provider_id = %provider_id,
            method = %method,
            frame_id = %frame_id,
            status = %status.as_u16(),
            error = %error,
            "provider downlink: decode response failed"
        );
        ServiceError::InternalError(format!("decode provider response: {error}"))
    })
}

/// Send the webhook request and return the raw response (status checked, body
/// NOT parsed). Shared by the JSON ack/history paths (`post_provider`) and the
/// 2.0 SSE branch. `accept_sse` selects the `Accept` header: when true the
/// request prefers `text/event-stream` but still allows JSON fallback.
async fn send_provider_request(
    client: &reqwest::Client,
    url_guard: &OutboundUrlGuard,
    target: &BotDeliveryTarget,
    body: &ProviderWebhookRequest,
    accept_sse: bool,
) -> ServiceResult<reqwest::Response> {
    let BotDeliveryTarget::HttpProvider {
        webhook_url,
        bcs_to_provider_token,
        protocol_version,
        ..
    } = target
    else {
        return Err(ServiceError::InternalError(
            "not http provider target".to_string(),
        ));
    };
    let guarded_url = match url_guard.resolve_request_http_url(webhook_url).await {
        Ok(url) => url,
        Err(error) => {
            warn!(
                provider_id = %body.to_bot.provider_id,
                provider_bot_ref = %body.to_bot.provider_bot_ref,
                webhook_url = %webhook_url_for_log(webhook_url),
                resolved_ip = ?blocked_outbound_ip(&error),
                reason = %error,
                "provider downlink: webhook blocked by outbound URL policy"
            );
            return Err(ServiceError::InvalidOperation {
                message: format!("provider webhook_url is not allowed: {error}"),
                request_id: Some(body.id.clone()),
            });
        }
    };
    let client_policy = ProviderClientPolicy::for_request(accept_sse);
    let pinned_client = provider_client_for_url(&guarded_url, client_policy).map_err(|error| {
        ServiceError::InternalError(format!("provider HTTP client build failed: {error}"))
    })?;
    let dns_pinned = pinned_client.is_some();
    let request_client = pinned_client.as_ref().unwrap_or(client);

    let message_id = uuid::Uuid::new_v4().to_string();
    let accept = if accept_sse {
        "text/event-stream, application/json"
    } else {
        "application/json"
    };
    let transport = if accept_sse {
        "sse"
    } else {
        "callback"
    };
    let request_body = provider_body_log(body);
    info!(
        provider_id = %body.to_bot.provider_id,
        provider_bot_ref = %body.to_bot.provider_bot_ref,
        method = %body.method,
        frame_id = %body.id,
        session_id = %body.session_id,
        bcn_group_id = %body.bcn_group_id,
        from = ?body.from,
        message = ?body.message,
        before = ?body.before,
        after = ?body.after,
        limit = ?body.limit,
        timeout_ms = %body.timeout_ms,
        request_body = %request_body,
        "provider downlink: request body"
    );
    let request_started_ms = bcs_protocol::now_ms();
    let request_started = Instant::now();
    info!(
        provider_id = %body.to_bot.provider_id,
        method = %body.method,
        frame_id = %body.id,
        message_id = %message_id,
        webhook_url = %webhook_url,
        protocol_version = %protocol_version,
        accept = %accept,
        transport = %transport,
        dns_pinned,
        http2_only = client_policy.http2_only,
        total_timeout_ms = ?client_policy.total_timeout.map(|timeout| timeout.as_millis()),
        read_timeout_ms = ?client_policy.read_timeout.map(|timeout| timeout.as_millis()),
        request_started_ms,
        "provider downlink: posting webhook"
    );
    let mut request = request_client
        .post(guarded_url.as_str())
        .bearer_auth(bcs_to_provider_token.expose_secret())
        .header("Accept", accept)
        .header("Content-Type", "application/json; charset=utf-8")
        .header(BCN_PROTOCOL_VERSION_HEADER, protocol_version)
        .header(BCN_MESSAGE_ID_HEADER, &message_id)
        .header(BCN_TIMESTAMP_HEADER, bcs_protocol::now_ms().to_string());
    if protocol_version == "2.0" {
        request = request.header(BCN_TRANSPORT_HEADER, transport);
    }
    let response = request
        .json(body)
        .send()
        .await
        .map_err(|error| {
            let elapsed_ms = request_started.elapsed().as_millis();
            warn!(
                provider_id = %body.to_bot.provider_id,
                method = %body.method,
                frame_id = %body.id,
                message_id = %message_id,
                webhook_url = %webhook_url,
                dns_pinned,
                http2_only = client_policy.http2_only,
                elapsed_ms,
                error = %error,
                "provider downlink: webhook transport error"
            );
            ServiceError::InternalError(format!("provider request failed: {error}"))
        })?;

    let status = response.status();
    let response_version = response.version();
    let content_type = response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    let transfer_encoding = response
        .headers()
        .get(reqwest::header::TRANSFER_ENCODING)
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    info!(
        target_bot_id = %target.bot_id(),
        provider_id = %body.to_bot.provider_id,
        method = %body.method,
        frame_id = %body.id,
        message_id = %message_id,
        webhook_url = %webhook_url,
        dns_pinned,
        http2_only = client_policy.http2_only,
        accept_sse,
        status = %status.as_u16(),
        http_version = ?response_version,
        content_type,
        content_length = ?response.content_length(),
        transfer_encoding,
        headers_elapsed_ms = request_started.elapsed().as_millis(),
        "provider downlink: response headers received"
    );
    if !status.is_success() {
        warn!(
            provider_id = %body.to_bot.provider_id,
            method = %body.method,
            frame_id = %body.id,
            message_id = %message_id,
            webhook_url = %webhook_url,
            status = %status.as_u16(),
            "provider downlink: webhook non-2xx"
        );
        return Err(ServiceError::InternalError(format!(
            "provider returned status {status}"
        )));
    }

    Ok(response)
}

fn blocked_outbound_ip(error: &OutboundUrlError) -> Option<IpAddr> {
    match error {
        OutboundUrlError::UnsafeAddress(address) => Some(*address),
        _ => None,
    }
}

fn webhook_url_for_log(webhook_url: &str) -> String {
    let Ok(mut url) = reqwest::Url::parse(webhook_url) else {
        return "<invalid webhook URL>".to_string();
    };
    let _ = url.set_username("");
    let _ = url.set_password(None);
    url.set_query(None);
    url.set_fragment(None);
    url.to_string()
}

fn provider_client_builder(policy: ProviderClientPolicy) -> reqwest::ClientBuilder {
    let mut builder = reqwest::Client::builder().redirect(reqwest::redirect::Policy::none());
    if let Some(timeout) = policy.total_timeout {
        builder = builder.timeout(timeout);
    }
    if let Some(read_timeout) = policy.read_timeout {
        builder = builder.read_timeout(read_timeout);
    }
    if policy.http2_only {
        builder = builder.http2_prior_knowledge();
    }
    builder
}

fn provider_client_for_url(
    guarded_url: &bcs_route_security::ValidatedRequestUrl,
    policy: ProviderClientPolicy,
) -> Result<Option<reqwest::Client>, reqwest::Error> {
    let Some((host, addrs)) = guarded_url.dns_override() else {
        return Ok(None);
    };
    provider_client_builder(policy)
        .resolve_to_addrs(host, addrs)
        .build()
        .map(Some)
}

fn provider_body_log(body: &ProviderWebhookRequest) -> String {
    let mut redacted = match serde_json::to_value(body) {
        Ok(value) => value,
        Err(error) => return format!("{{\"serialize_error\":\"{}\"}}", error),
    };
    if let Some(attachments) = redacted
        .get_mut("attachments")
        .and_then(Value::as_array_mut)
    {
        for attachment in attachments {
            if let Some(url) = attachment.get_mut("url") {
                *url = Value::String("<redacted>".to_string());
            }
        }
    }
    serde_json::to_string(&redacted).unwrap_or_else(|error| {
        format!("{{\"serialize_error\":\"{}\"}}", error)
    })
}

fn provider_history_log(response: &ProviderHistoryResponse) -> String {
    serde_json::to_string(response).unwrap_or_else(|error| {
        format!("{{\"serialize_error\":\"{}\"}}", error)
    })
}

// ---------------------------------------------------------------------------
// SSE read loop (2.0 downlink streaming)
// ---------------------------------------------------------------------------

/// Two ASCII bytes `\n\n` (LF LF) — a frame separator.
const FRAME_SEP_LF: &[u8] = b"\n\n";
/// Four ASCII bytes `\r\n\r\n` (CRLF CRLF) — the other frame separator.
const FRAME_SEP_CRLF: &[u8] = b"\r\n\r\n";

/// Find the earliest frame separator (`\n\n` or `\r\n\r\n`) in `buf`.
/// Returns `(start_index, separator_len)` of the match closest to the front.
fn find_frame_sep(buf: &[u8]) -> Option<(usize, usize)> {
    let lf = find_subslice(buf, FRAME_SEP_LF);
    let crlf = find_subslice(buf, FRAME_SEP_CRLF);
    match (lf, crlf) {
        (Some(a), Some(b)) => {
            if a <= b {
                Some((a, FRAME_SEP_LF.len()))
            } else {
                Some((b, FRAME_SEP_CRLF.len()))
            }
        }
        (Some(a), None) => Some((a, FRAME_SEP_LF.len())),
        (None, Some(b)) => Some((b, FRAME_SEP_CRLF.len())),
        (None, None) => None,
    }
}

fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || haystack.len() < needle.len() {
        return None;
    }
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

/// Read the SSE byte stream from `resp`, split into frames, parse, dedupe by
/// `StreamEvent.seq`, and ingest into the message-flow pipeline. Closes the run
/// with a synthesized error terminal on idle timeout, read error, or a stream
/// that ends without a chat terminal (#3). Resolves run context (with bounded
/// retry for the put_context race, #2) before ingesting any frame.
async fn stream_and_drive(
    resp: reqwest::Response,
    bcn_run_id: String,
    bot_id: String,
    flow: Arc<dyn MessageFlowService>,
    ctx: Arc<dyn BotRunContextPort>,
) {
    use futures::StreamExt;

    // Resolve run context first (group_id needed for every payload). Retry to
    // cover the put_context race: deliver() returns -> put_context runs, but the
    // spawned reader may reach here first.
    let Some(run_ctx) = resolve_run_context(&ctx, &bcn_run_id).await else {
        warn!(run_id = %bcn_run_id, "sse: run context never became available; closing");
        return;
    };
    let group_id = run_ctx.group_id.clone();
    let bcs_session_id = run_ctx.bcs_session_id.clone();
    // #2: stash the run deadline so every frame can be guarded against an
    // already-expired run before it is ingested (re-checked per frame below).
    let deadline_ms = run_ctx.deadline_ms;

    let mut stream = resp.bytes_stream();
    let mut buf: Vec<u8> = Vec::new();
    let mut dedup = SeqDedup::default();
    let mut lag = LagTracker::default();
    let mut saw_terminal = false;
    let idle = Duration::from_millis(SSE_IDLE_TIMEOUT_MS);

    // SSE-detail diagnostics: how many frames we consumed, when we started, and
    // how long we blocked waiting on the socket vs processing frames. The gap
    // between `stream.next()` returns (idle waits) and total elapsed tells us
    // whether a stall (idle timeout) or a truncated/reset stream closed the run.
    let started_ms = bcs_protocol::now_ms();
    let mut frames: u64 = 0;
    tracing::info!(
        target: "bcs_sse_detail",
        run_id = %bcn_run_id,
        group_id = %group_id,
        started_ms,
        idle_timeout_ms = SSE_IDLE_TIMEOUT_MS,
        deadline_ms,
        "sse begin reading downlink stream"
    );

    'read: loop {
        let next = tokio::time::timeout(idle, stream.next()).await;
        match next {
            Err(_) => {
                warn!(
                    target: "bcs_sse_detail",
                    run_id = %bcn_run_id,
                    frames,
                    saw_terminal,
                    elapsed_ms = bcs_protocol::now_ms().saturating_sub(started_ms),
                    "sse idle timeout; closing run as error"
                );
                break 'read;
            }
            Ok(None) => {
                // Stream ended. Flush any trailing buffered (non-empty) frame.
                if !buf.is_empty() {
                    frames += 1;
                    if let Some(done) = drive_frame_bytes(
                        &buf,
                        &bcn_run_id,
                        &group_id,
                        &bot_id,
                        &bcs_session_id,
                        deadline_ms,
                        &mut dedup,
                        &mut lag,
                        &flow,
                    )
                    .await
                    {
                        saw_terminal = done;
                    }
                }
                tracing::info!(
                    target: "bcs_sse_detail",
                    run_id = %bcn_run_id,
                    frames,
                    saw_terminal,
                    elapsed_ms = bcs_protocol::now_ms().saturating_sub(started_ms),
                    "sse stream ended (EOF)"
                );
                break 'read;
            }
            Ok(Some(Err(error))) => {
                warn!(
                    target: "bcs_sse_detail",
                    run_id = %bcn_run_id,
                    %error,
                    frames,
                    saw_terminal,
                    elapsed_ms = bcs_protocol::now_ms().saturating_sub(started_ms),
                    "sse read error; closing run as error"
                );
                break 'read;
            }
            Ok(Some(Ok(chunk))) => {
                buf.extend_from_slice(&chunk);
                while let Some((idx, sep_len)) = find_frame_sep(&buf) {
                    let frame: Vec<u8> = buf.drain(..idx + sep_len).collect();
                    frames += 1;
                    if let Some(done) = drive_frame_bytes(
                        &frame,
                        &bcn_run_id,
                        &group_id,
                        &bot_id,
                        &bcs_session_id,
                        deadline_ms,
                        &mut dedup,
                        &mut lag,
                        &flow,
                    )
                    .await
                    {
                        if done {
                            saw_terminal = true;
                            break 'read;
                        }
                    }
                }
            }
        }
    }

    // #3: any close path that did NOT see a chat terminal synthesizes one so the
    // run is cleanly closed and the frontend is never left hanging.
    if !saw_terminal {
        warn!(
            target: "bcs_sse_detail",
            run_id = %bcn_run_id,
            frames,
            elapsed_ms = bcs_protocol::now_ms().saturating_sub(started_ms),
            "sse closed without chat terminal; synthesizing error terminal"
        );
        ingest_synthesized_error(&bcn_run_id, &group_id, &bot_id, &bcs_session_id, &flow).await;
    }
    // #2: mark the run terminal in the run-context store after closing.
    ctx.mark_terminal(&bcn_run_id).await;
}

/// Resolve the run context for `run_id`, retrying a bounded number of times to
/// cover the put_context race (#2). Returns `None` once retries are exhausted.
async fn resolve_run_context(
    ctx: &Arc<dyn BotRunContextPort>,
    run_id: &str,
) -> Option<BotRunContext> {
    for _ in 0..SSE_CTX_RETRY_MAX {
        if let Some(found) = ctx.get_context(run_id).await {
            return Some(found);
        }
        tokio::time::sleep(Duration::from_millis(SSE_CTX_RETRY_INTERVAL_MS)).await;
    }
    ctx.get_context(run_id).await
}

/// Decode one complete frame's bytes, parse + classify + dedupe + ingest.
/// Returns `Some(true)` if this frame closed the run (terminal), `Some(false)`
/// if a non-terminal event was ingested, and `None` if the frame was dropped
/// (ping / unknown / duplicate / not-json / no run context).
async fn drive_frame_bytes(
    frame_bytes: &[u8],
    bcn_run_id: &str,
    group_id: &str,
    bot_id: &str,
    bcs_session_id: &Option<String>,
    deadline_ms: u64,
    dedup: &mut SeqDedup,
    lag: &mut LagTracker,
    flow: &Arc<dyn MessageFlowService>,
) -> Option<bool> {
    // Decode only the complete frame bytes (#5). Lossy + WARN on invalid UTF-8.
    let text = match std::str::from_utf8(frame_bytes) {
        Ok(text) => text.to_string(),
        Err(error) => {
            warn!(run_id = %bcn_run_id, %error, "sse frame not valid utf-8; lossy-decoding");
            String::from_utf8_lossy(frame_bytes).into_owned()
        }
    };
    if text.trim().is_empty() {
        return None;
    }
    drive_sse_frame(
        &text,
        bcn_run_id,
        group_id,
        bot_id,
        bcs_session_id,
        deadline_ms,
        dedup,
        lag,
        flow,
    )
    .await
}

/// Core per-frame ingest logic, decoupled from the byte source so tests can
/// drive it from in-memory text. Returns the same tri-state as
/// `drive_frame_bytes`.
async fn drive_sse_frame(
    block: &str,
    bcn_run_id: &str,
    group_id: &str,
    bot_id: &str,
    bcs_session_id: &Option<String>,
    deadline_ms: u64,
    dedup: &mut SeqDedup,
    lag: &mut LagTracker,
    flow: &Arc<dyn MessageFlowService>,
) -> Option<bool> {
    let frame = parse_sse_block(block)?;
    let data: Value = match serde_json::from_str(&frame.data) {
        Ok(value) => value,
        Err(error) => {
            warn!(
                target: "bcs_sse_detail",
                run_id = %bcn_run_id,
                event = %frame.event,
                %error,
                sse_data = %frame.data,
                "sse data not json; dropping frame"
            );
            warn!(run_id = %bcn_run_id, %error, "sse data not json; dropping frame");
            return None;
        }
    };
    let event = parse_stream_event(&frame.event, data);
    let kind = classify(&event);

    // SSE-detail per-frame trace: `lag_ms` (receipt time minus the engine
    // frame's own ts) measures how far BCS consumption trails the producer.
    // Goes only to the bcs-sse-detail.log target. Every frame is logged at INFO
    // with the raw SSE `data:` payload so incidents can be reconstructed by
    // grepping a run_id from the detail log.
    {
        let recv_ms = bcs_protocol::now_ms();
        let frame_ts = stream_event_ts(&event);
        let seq = stream_event_seq(&event).unwrap_or(0);
        let lag_ms = frame_ts.map(|t| recv_ms.saturating_sub(t)).unwrap_or(0);
        let state = ingest_kind_state_slug(&kind);

        // Edge-triggered lag alert: WARN once when the run crosses the alert
        // threshold (falling behind the producer) and once when it recovers, so
        // a sustained backlog is visible without a per-frame WARN flood.
        if lag_ms > SSE_LAG_ALERT_MS {
            lag.peak_lag_ms = lag.peak_lag_ms.max(lag_ms);
            if !lag.alerting {
                lag.alerting = true;
                warn!(
                    target: "bcs_sse_detail",
                    run_id = %bcn_run_id,
                    seq,
                    lag_ms,
                    alert_threshold_ms = SSE_LAG_ALERT_MS,
                    "sse consumption falling behind producer"
                );
            }
        } else if lag.alerting {
            lag.alerting = false;
            warn!(
                target: "bcs_sse_detail",
                run_id = %bcn_run_id,
                seq,
                lag_ms,
                peak_lag_ms = lag.peak_lag_ms,
                "sse consumption recovered"
            );
            lag.peak_lag_ms = 0;
        }

        tracing::info!(
            target: "bcs_sse_detail",
            run_id = %bcn_run_id,
            event = %frame.event,
            state = state,
            seq,
            frame_ts = frame_ts.unwrap_or(0),
            recv_ms,
            lag_ms,
            sse_data = %frame.data,
            "sse frame recv"
        );
    }

    let (event_type, state, payload, terminal) = match kind {
        IngestKind::Drop => return None,
        IngestKind::CloseUnsupported => {
            // Approval/HITL is gated this round (#4/D11): close the run with a
            // chat error terminal so the frontend isn't stuck. Not deduped.
            warn!(
                run_id = %bcn_run_id,
                "approval/HITL received but resolve is out of scope; closing run as unsupported"
            );
            let payload = build_chat_error_payload(bcn_run_id, group_id);
            ("chat.event".to_string(), ChatEventState::Error, payload, true)
        }
        IngestKind::Pipeline { event_type, state } => {
            // #4: dedupe off the parsed StreamEvent's seq, never the SSE id.
            match dedup.accept(stream_event_seq(&event)) {
                SeqDecision::Duplicate => {
                    warn!(run_id = %bcn_run_id, "duplicate/regressed seq; dropping");
                    return None;
                }
                SeqDecision::Gap(gap) => warn!(run_id = %bcn_run_id, gap, "seq gap"),
                SeqDecision::Accept => {}
            }
            let payload = build_event_payload(&event, bcn_run_id, group_id);
            (event_type, state, payload, false)
        }
        IngestKind::Terminal { event_type, state } => {
            match dedup.accept(stream_event_seq(&event)) {
                SeqDecision::Duplicate => {
                    warn!(run_id = %bcn_run_id, "duplicate/regressed terminal seq; dropping");
                    return None;
                }
                SeqDecision::Gap(gap) => {
                    warn!(run_id = %bcn_run_id, gap, "seq gap before terminal")
                }
                SeqDecision::Accept => {}
            }
            let payload = build_event_payload(&event, bcn_run_id, group_id);
            (event_type, state, payload, true)
        }
    };

    // #2: re-check the run deadline per frame. The SSE connection is itself the
    // terminal writer, so a cheap monotonic deadline check is enough to avoid
    // feeding an already-expired run; drop + WARN (no raw payload) and move on.
    if bcs_protocol::now_ms() > deadline_ms {
        warn!(run_id = %bcn_run_id, "run deadline exceeded; dropping frame");
        return None;
    }

    ingest(bcn_run_id, group_id, bot_id, bcs_session_id, event_type, state, payload, flow).await;
    Some(terminal)
}

/// Read the per-variant seq from a parsed `StreamEvent` (#4). ping / unknown
/// carry no seq and must never reach the dedupe counter anyway.
fn stream_event_seq(event: &StreamEvent) -> Option<u64> {
    match event {
        StreamEvent::Agent(agent) => agent.seq,
        StreamEvent::Chat(chat) => chat.seq,
        _ => None,
    }
}

/// The engine-stamped `ts` (ms) of a parsed frame, for SSE-detail lag tracing.
fn stream_event_ts(event: &StreamEvent) -> Option<u64> {
    match event {
        StreamEvent::Agent(agent) => agent.ts,
        // ChatEvent has no typed `ts`; read it from the retained raw frame.
        StreamEvent::Chat(chat) => chat.raw.get("ts").and_then(Value::as_u64),
        StreamEvent::Ping { ts } => *ts,
        _ => None,
    }
}

fn ingest_kind_state_slug(kind: &IngestKind) -> &'static str {
    match kind {
        IngestKind::Pipeline { state, .. } | IngestKind::Terminal { state, .. } => {
            chat_event_state_slug(state)
        }
        IngestKind::CloseUnsupported => "close_unsupported",
        IngestKind::Drop => "drop",
    }
}

fn chat_event_state_slug(state: &ChatEventState) -> &'static str {
    match state {
        ChatEventState::Delta => "delta",
        ChatEventState::Final => "final",
        ChatEventState::Error => "error",
        ChatEventState::Aborted => "aborted",
        ChatEventState::ToolCallStart => "tool_call_start",
        ChatEventState::ToolCallEnd => "tool_call_end",
    }
}

/// Build the downstream `event_payload` by FILLING the existing protocol structs
/// (#1) so the wire names match the WS plugin path byte-for-byte. `run_id` and
/// `bcs_group_id` come from the run context (BCN ids), NOT the engine frame.
fn build_event_payload(event: &StreamEvent, run_id: &str, group_id: &str) -> Value {
    match event {
        StreamEvent::Agent(agent) => {
            let stream = match &agent.data {
                bcs_protocol::stream::AgentData::Tool(_) => AgentStream::Tool,
                bcs_protocol::stream::AgentData::Thinking(_) => AgentStream::Thinking,
                bcs_protocol::stream::AgentData::Lifecycle(_) => AgentStream::Lifecycle,
                // Phase has no AgentStream counterpart; fall back to Assistant
                // (the closest "model output" stream) and WARN so the choice is
                // visible. Approval is handled upstream as CloseUnsupported.
                bcs_protocol::stream::AgentData::Phase(_) => {
                    warn!(run_id, "agent phase stream has no AgentStream variant; using assistant");
                    AgentStream::Assistant
                }
                other => {
                    warn!(run_id, ?other, "unexpected agent data in payload build; using assistant");
                    AgentStream::Assistant
                }
            };
            // data is opaque to BCS: reuse the frame's `data` sub-object when
            // present, else the whole raw frame.
            let data = agent
                .raw
                .get("data")
                .cloned()
                .unwrap_or_else(|| agent.raw.clone());
            let payload = AgentEventPayload {
                run_id: run_id.to_string(),
                bcs_group_id: group_id.to_string(),
                stream,
                ts: agent.ts.unwrap_or(0),
                data,
            };
            serde_json::to_value(payload).unwrap_or(Value::Null)
        }
        StreamEvent::Chat(chat) => {
            let state = match chat.state {
                ChatState::Delta => WireChatState::Delta,
                ChatState::Final => WireChatState::Final,
                ChatState::Aborted => WireChatState::Aborted,
                ChatState::Error => WireChatState::Error,
            };
            // message is strongly typed (Option<MessageContent>). If the engine
            // frame's message shape doesn't deserialize, WARN rather than
            // silently dropping the body (#1 risk note).
            let mut message = match chat.message.as_ref() {
                Some(raw_message) => match serde_json::from_value(raw_message.clone()) {
                    Ok(parsed) => Some(parsed),
                    Err(error) => {
                        warn!(
                            run_id,
                            %error,
                            "chat message did not match MessageContent; body omitted"
                        );
                        None
                    }
                },
                None => None,
            };
            if matches!(chat.state, ChatState::Error) && !message_has_text(&message) {
                if let Some(error_message) = chat
                    .error_message
                    .as_deref()
                    .map(str::trim)
                    .filter(|text| !text.is_empty())
                {
                    message = Some(assistant_text_message(error_message));
                }
            }
            let payload = ChatEventPayload {
                run_id: run_id.to_string(),
                bcs_group_id: group_id.to_string(),
                state,
                message,
                // Forward the frame's incremental delta so BCS can accumulate
                // segments itself instead of re-deriving from cumulative message.
                delta_text: chat.delta_text.clone(),
                usage: None,
                stop_reason: chat.stop_reason.clone(),
                error_message: chat.error_message.clone(),
                error_kind: chat.error_kind.clone(),
                tool_call_id: None,
                tool_name: None,
                args: None,
                result: None,
                is_error: None,
                success: None,
                routing: None,
            };
            serde_json::to_value(payload).unwrap_or(Value::Null)
        }
        _ => Value::Null,
    }
}

fn message_has_text(message: &Option<MessageContent>) -> bool {
    message.as_ref().is_some_and(|message| {
        message
            .content
            .iter()
            .any(|block| block.text.as_deref().is_some_and(|text| !text.trim().is_empty()))
    })
}

fn assistant_text_message(text: &str) -> MessageContent {
    MessageContent {
        role: "assistant".to_string(),
        content: vec![ContentBlock::text(text)],
        timestamp: bcs_protocol::now_ms(),
    }
}

/// Build a chat error terminal payload (#3 synthesized terminal / approval gate).
fn build_chat_error_payload(run_id: &str, group_id: &str) -> Value {
    let payload = ChatEventPayload {
        run_id: run_id.to_string(),
        bcs_group_id: group_id.to_string(),
        state: WireChatState::Error,
        message: None,
        delta_text: None,
        usage: None,
        stop_reason: None,
        error_message: None,
        error_kind: None,
        tool_call_id: None,
        tool_name: None,
        args: None,
        result: None,
        is_error: None,
        success: None,
        routing: None,
    };
    serde_json::to_value(payload).unwrap_or(Value::Null)
}

/// Synthesize and ingest a chat error terminal to close a run cleanly (#3).
async fn ingest_synthesized_error(
    bcn_run_id: &str,
    group_id: &str,
    bot_id: &str,
    bcs_session_id: &Option<String>,
    flow: &Arc<dyn MessageFlowService>,
) {
    warn!(run_id = %bcn_run_id, "sse closed without chat terminal; synthesizing error terminal");
    let payload = build_chat_error_payload(bcn_run_id, group_id);
    ingest(
        bcn_run_id,
        group_id,
        bot_id,
        bcs_session_id,
        "chat.event".to_string(),
        ChatEventState::Error,
        payload,
        flow,
    )
    .await;
}

/// Build the `BotEventCommand` and hand it to the message-flow pipeline.
#[allow(clippy::too_many_arguments)]
async fn ingest(
    bcn_run_id: &str,
    group_id: &str,
    bot_id: &str,
    bcs_session_id: &Option<String>,
    event_type: String,
    state: ChatEventState,
    payload: Value,
    flow: &Arc<dyn MessageFlowService>,
) {
    let cmd = BotEventCommand {
        bot_id: bot_id.to_string(),
        run_id: bcn_run_id.to_string(), // BCN run id from run_ctx, NOT engine runId (D9)
        group_id: group_id.to_string(),
        event_type,
        event_payload: payload,
        state,
        bcs_session_id: bcs_session_id.clone(),
    };
    if let Err(error) = flow.handle_bot_event(cmd).await {
        warn!(run_id = %bcn_run_id, %error, "ingest handle_bot_event failed");
    }
}

#[cfg(test)]
mod client_policy_tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    async fn spawn_http1_server() -> std::net::SocketAddr {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = [0_u8; 1024];
            let _ = stream.read(&mut request).await.unwrap();
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                .await
                .unwrap();
        });
        addr
    }

    #[test]
    fn sse_policy_is_http2_only_without_total_timeout() {
        let policy = ProviderClientPolicy::for_request(true);

        assert_eq!(policy.total_timeout, None);
        assert_eq!(policy.read_timeout, Some(Duration::from_secs(125)));
        assert!(policy.http2_only);
    }

    #[test]
    fn callback_policy_keeps_total_timeout_and_protocol_negotiation() {
        let policy = ProviderClientPolicy::for_request(false);

        assert_eq!(policy.total_timeout, Some(Duration::from_secs(65)));
        assert_eq!(policy.read_timeout, None);
        assert!(!policy.http2_only);
    }

    #[test]
    fn provider_body_log_redacts_temporary_attachment_urls() {
        let body = ProviderWebhookRequest {
            frame_type: "event".to_string(),
            id: "frame-1".to_string(),
            method: "chat.send".to_string(),
            session_id: "session-1".to_string(),
            bcn_group_id: "group-1".to_string(),
            to_bot: ProviderWebhookBotRef {
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "bot-1".to_string(),
                tags: Vec::new(),
            },
            from: None,
            message: None,
            attachments: vec![Attachment {
                attachment_id: "att-1".to_string(),
                attachment_type: bcs_protocol::AttachmentType::Image,
                file_name: "image".to_string(),
                mime_type: None,
                size: None,
                sha256: None,
                url: "https://download.example.com/image?token=secret".to_string(),
                expires_at: None,
            }],
            before: None,
            after: None,
            limit: None,
            timeout_ms: 1_000,
            extensions: None,
        };

        let logged = provider_body_log(&body);

        assert!(logged.contains("\"url\":\"<redacted>\""));
        assert!(!logged.contains("token=secret"));
        assert_eq!(
            body.attachments[0].url,
            "https://download.example.com/image?token=secret"
        );
    }

    #[tokio::test]
    async fn sse_builder_rejects_http1_while_callback_builder_accepts_it() {
        let callback_addr = spawn_http1_server().await;
        let callback_client = provider_client_builder(ProviderClientPolicy::for_request(false))
            .build()
            .unwrap();
        let callback_response = callback_client
            .get(format!("http://{callback_addr}"))
            .send()
            .await
            .unwrap();
        assert_eq!(callback_response.version(), reqwest::Version::HTTP_11);

        let sse_addr = spawn_http1_server().await;
        let sse_client = provider_client_builder(ProviderClientPolicy::for_request(true))
            .build()
            .unwrap();
        let error = sse_client
            .get(format!("http://{sse_addr}"))
            .send()
            .await
            .unwrap_err();
        assert!(error.is_request());
    }
}

#[cfg(test)]
mod sse_loop_tests {
    use super::*;
    use bcs_service_api::{
        BotEventOutcome, ChatAbortCommand, ChatAbortOutcome, GroupCallbackCommand,
        GroupCallbackOutcome, TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand,
        TaskDispatchOutcome, TaskRunAliasRegistration, WebSendCommand, WebSendOutcome,
    };
    use std::sync::Mutex;

    /// Records each ingested (event_type, state, event_payload) tuple.
    #[derive(Default)]
    struct RecordingFlow {
        events: Mutex<Vec<(String, ChatEventState, Value)>>,
    }

    impl RecordingFlow {
        fn snapshot(&self) -> Vec<(String, ChatEventState, Value)> {
            self.events.lock().unwrap().clone()
        }
        fn pairs(&self) -> Vec<(String, ChatEventState)> {
            self.events
                .lock()
                .unwrap()
                .iter()
                .map(|(event_type, state, _)| (event_type.clone(), state.clone()))
                .collect()
        }
    }

    #[async_trait]
    impl MessageFlowService for RecordingFlow {
        async fn handle_web_send(&self, _cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
            unimplemented!("not used in sse loop tests")
        }
        async fn handle_bot_event(
            &self,
            cmd: BotEventCommand,
        ) -> ServiceResult<BotEventOutcome> {
            self.events.lock().unwrap().push((
                cmd.event_type.clone(),
                cmd.state.clone(),
                cmd.event_payload.clone(),
            ));
            Ok(BotEventOutcome {
                bot_deliveries: vec![],
                frontend_deliveries: vec![],
                unregistered_run_ids: vec![],
                mentions: vec![],
                delivered_count: 1,
                failed_count: 0,
                delivery_results: vec![],
            })
        }
        async fn handle_group_callback(
            &self,
            _cmd: GroupCallbackCommand,
        ) -> ServiceResult<GroupCallbackOutcome> {
            unimplemented!("not used in sse loop tests")
        }
        async fn handle_chat_abort(
            &self,
            _cmd: ChatAbortCommand,
        ) -> ServiceResult<ChatAbortOutcome> {
            unimplemented!("not used in sse loop tests")
        }
        async fn register_task_run_alias(
            &self,
            _task_id: &str,
            _run_id: &str,
            _bot_id: &str,
        ) -> ServiceResult<TaskRunAliasRegistration> {
            unimplemented!("not used in sse loop tests")
        }
        async fn handle_task_dispatch(
            &self,
            _cmd: TaskDispatchCommand,
        ) -> ServiceResult<TaskDispatchOutcome> {
            unimplemented!("not used in sse loop tests")
        }
        async fn handle_task_complete(
            &self,
            _cmd: TaskCompleteCommand,
        ) -> ServiceResult<TaskCompleteOutcome> {
            unimplemented!("not used in sse loop tests")
        }
    }

    /// Fixed run-context fake: always resolves with the given group/bot and a
    /// configurable deadline (so the per-frame deadline guard can be exercised).
    struct FixedCtx {
        deadline_ms: u64,
    }

    #[async_trait]
    impl BotRunContextPort for FixedCtx {
        async fn put_context(&self, _context: BotRunContext) {}
        async fn get_context(&self, run_id: &str) -> Option<BotRunContext> {
            Some(BotRunContext {
                run_id: run_id.to_string(),
                bot_id: "bot-1".into(),
                group_id: "grp-1".into(),
                bcs_session_id: None,
                deadline_ms: self.deadline_ms,
                terminal: false,
            })
        }
        async fn try_begin_terminal(&self, _run_id: &str) -> bool {
            true
        }
        async fn mark_terminal(&self, _run_id: &str) -> bool {
            true
        }
        async fn release_terminal(&self, _run_id: &str) {}
    }

    /// Test wrapper: drive the per-frame ingest core over an in-memory SSE text
    /// split on `\n\n`, with one dedupe state spanning the whole stream. Returns
    /// whether a terminal was seen.
    async fn run_sse_text_for_test(
        sse_text: &str,
        bcn_run_id: &str,
        group_id: &str,
        bot_id: &str,
        flow: &Arc<dyn MessageFlowService>,
    ) -> bool {
        run_sse_text_with_deadline(sse_text, bcn_run_id, group_id, bot_id, u64::MAX, flow).await
    }

    /// Like `run_sse_text_for_test` but with an explicit run deadline so the
    /// per-frame deadline guard can be exercised.
    async fn run_sse_text_with_deadline(
        sse_text: &str,
        bcn_run_id: &str,
        group_id: &str,
        bot_id: &str,
        deadline_ms: u64,
        flow: &Arc<dyn MessageFlowService>,
    ) -> bool {
        let mut dedup = SeqDedup::default();
        let mut lag = LagTracker::default();
        let session: Option<String> = None;
        for block in sse_text.split("\n\n") {
            if block.trim().is_empty() {
                continue;
            }
            if let Some(true) = drive_sse_frame(
                block,
                bcn_run_id,
                group_id,
                bot_id,
                &session,
                deadline_ms,
                &mut dedup,
                &mut lag,
                flow,
            )
            .await
            {
                return true;
            }
        }
        false
    }

    #[tokio::test]
    async fn read_loop_ingests_delta_then_final_and_dedupes() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn MessageFlowService> = recording.clone();
        let sse = "event: agent\nid: 1\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n\
event: ping\ndata: {\"ts\":1}\n\n\
event: agent\nid: 1\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n\
event: chat\nid: 2\ndata: {\"runId\":\"e\",\"seq\":2,\"state\":\"final\",\"message\":{\"role\":\"assistant\",\"content\":[],\"timestamp\":0}}\n\n";
        let terminal = run_sse_text_for_test(sse, "bcn-run-1", "grp-1", "bot-1", &flow).await;
        assert!(terminal, "final should close the run");

        // duplicate seq1 dropped, ping dropped: thinking(agent/Delta) + chat final.
        assert_eq!(
            recording.pairs(),
            vec![
                ("agent".to_string(), ChatEventState::Delta),
                ("chat.event".to_string(), ChatEventState::Final),
            ]
        );
        // chat payload must use snake_case BCS wire names, NOT deltaText.
        let snapshot = recording.snapshot();
        let chat_payload = &snapshot[1].2;
        assert_eq!(chat_payload["run_id"], Value::String("bcn-run-1".into()));
        assert_eq!(chat_payload["bcs_group_id"], Value::String("grp-1".into()));
        assert!(chat_payload.get("deltaText").is_none());
    }

    #[tokio::test]
    async fn read_loop_synthesizes_error_message_body() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn MessageFlowService> = recording.clone();
        let sse = "event: chat\nid: 1\ndata: {\"runId\":\"engine-run\",\"seq\":1,\"state\":\"error\",\"errorMessage\":\"engine crashed\",\"errorKind\":\"provider_error\"}\n\n";
        let terminal = run_sse_text_for_test(sse, "bcn-run-err", "grp-1", "bot-1", &flow).await;
        assert!(terminal, "error should close the run");

        let snapshot = recording.snapshot();
        assert_eq!(snapshot.len(), 1);
        let (event_type, state, payload) = &snapshot[0];
        assert_eq!(event_type, "chat.event");
        assert_eq!(*state, ChatEventState::Error);
        assert_eq!(payload["run_id"], Value::String("bcn-run-err".into()));
        assert_eq!(payload["bcs_group_id"], Value::String("grp-1".into()));
        assert_eq!(payload["errorMessage"], "engine crashed");
        assert_eq!(payload["errorKind"], "provider_error");
        assert_eq!(payload["message"]["content"][0]["text"], "engine crashed");
    }

    #[tokio::test]
    async fn read_loop_falls_back_from_blank_error_message_body() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn MessageFlowService> = recording.clone();
        let sse = "event: chat\nid: 1\ndata: {\"runId\":\"engine-run\",\"seq\":1,\"state\":\"error\",\"errorMessage\":\"engine crashed\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"   \"}],\"timestamp\":0}}\n\n";
        let terminal = run_sse_text_for_test(sse, "bcn-run-err", "grp-1", "bot-1", &flow).await;
        assert!(terminal, "error should close the run");

        let snapshot = recording.snapshot();
        assert_eq!(snapshot.len(), 1);
        let payload = &snapshot[0].2;
        assert_eq!(payload["errorMessage"], "engine crashed");
        assert_eq!(payload["message"]["content"][0]["text"], "engine crashed");
    }

    #[tokio::test]
    async fn crlf_frame_separators_are_split() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn MessageFlowService> = recording.clone();
        // CRLF line endings AND CRLF-CRLF frame separators across the byte path.
        let sse = "event: agent\r\nid: 1\r\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\r\n\r\n\
event: chat\r\nid: 2\r\ndata: {\"runId\":\"e\",\"seq\":2,\"state\":\"final\",\"message\":{\"role\":\"assistant\",\"content\":[],\"timestamp\":0}}\r\n\r\n";

        // Drive through the real byte splitter (find_frame_sep) to exercise CRLF.
        let mut dedup = SeqDedup::default();
        let mut lag = LagTracker::default();
        let session: Option<String> = None;
        let mut buf = sse.as_bytes().to_vec();
        let mut terminal = false;
        while let Some((idx, sep_len)) = find_frame_sep(&buf) {
            let frame: Vec<u8> = buf.drain(..idx + sep_len).collect();
            if let Some(true) = drive_frame_bytes(
                &frame,
                "bcn-run-1",
                "grp-1",
                "bot-1",
                &session,
                u64::MAX,
                &mut dedup,
                &mut lag,
                &flow,
            )
            .await
            {
                terminal = true;
                break;
            }
        }
        assert!(terminal);
        assert_eq!(
            recording.pairs(),
            vec![
                ("agent".to_string(), ChatEventState::Delta),
                ("chat.event".to_string(), ChatEventState::Final),
            ]
        );
    }

    #[tokio::test]
    async fn dedupe_works_without_sse_id_using_payload_seq() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn MessageFlowService> = recording.clone();
        // No `id:` lines at all — dedupe must come from StreamEvent.seq (#4).
        let sse = "event: agent\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n\
event: agent\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n\
event: agent\ndata: {\"runId\":\"e\",\"seq\":2,\"stream\":\"thinking\",\"delta\":\"b\"}\n\n";
        run_sse_text_for_test(sse, "bcn-run-1", "grp-1", "bot-1", &flow).await;
        // Second seq=1 is a duplicate and dropped: only two accepts.
        assert_eq!(
            recording.pairs(),
            vec![
                ("agent".to_string(), ChatEventState::Delta),
                ("agent".to_string(), ChatEventState::Delta),
            ]
        );
    }

    #[tokio::test]
    async fn stream_end_without_terminal_synthesizes_error() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn MessageFlowService> = recording.clone();
        let resp = sse_response(
            "event: agent\nid: 1\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n",
        )
        .await;
        let ctx: Arc<dyn BotRunContextPort> = Arc::new(FixedCtx { deadline_ms: u64::MAX });
        stream_and_drive(resp, "bcn-run-1".into(), "bot-1".into(), flow.clone(), ctx).await;
        let pairs = recording.pairs();
        // thinking delta, then a synthesized chat error terminal.
        assert_eq!(
            pairs,
            vec![
                ("agent".to_string(), ChatEventState::Delta),
                ("chat.event".to_string(), ChatEventState::Error),
            ]
        );
    }

    #[tokio::test]
    async fn approval_frame_closes_run_unsupported() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn MessageFlowService> = recording.clone();
        let sse = "event: agent\nid: 1\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"approval\",\"phase\":\"requested\",\"kind\":\"exec\"}\n\n";
        let terminal = run_sse_text_for_test(sse, "bcn-run-1", "grp-1", "bot-1", &flow).await;
        assert!(terminal, "approval gate must close the run");
        assert_eq!(
            recording.pairs(),
            vec![("chat.event".to_string(), ChatEventState::Error)]
        );
    }

    #[tokio::test]
    async fn past_deadline_frames_are_dropped_before_ingest() {
        // A run whose deadline is already in the past must not ingest any frame:
        // the per-frame guard (#2) drops every frame + WARNs before ingest.
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn MessageFlowService> = recording.clone();
        let sse = "event: agent\nid: 1\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n\
event: chat\nid: 2\ndata: {\"runId\":\"e\",\"seq\":2,\"state\":\"final\",\"message\":{\"role\":\"assistant\",\"content\":[],\"timestamp\":0}}\n\n";
        // deadline_ms = 0 is always in the past relative to now_ms().
        let terminal =
            run_sse_text_with_deadline(sse, "bcn-run-1", "grp-1", "bot-1", 0, &flow).await;
        assert!(!terminal, "expired run must not report a terminal");
        assert!(
            recording.pairs().is_empty(),
            "no frame may be ingested past the run deadline"
        );

        // Sanity: the very same stream with a fresh deadline ingests normally.
        let recording_ok = Arc::new(RecordingFlow::default());
        let flow_ok: Arc<dyn MessageFlowService> = recording_ok.clone();
        let terminal_ok = run_sse_text_with_deadline(
            sse, "bcn-run-1", "grp-1", "bot-1", u64::MAX, &flow_ok,
        )
        .await;
        assert!(terminal_ok, "fresh-deadline run should close on the final");
        assert_eq!(
            recording_ok.pairs(),
            vec![
                ("agent".to_string(), ChatEventState::Delta),
                ("chat.event".to_string(), ChatEventState::Final),
            ]
        );
    }

    // Helpers --------------------------------------------------------------
    /// Build a streaming reqwest::Response from a fixed SSE body for the
    /// `stream_and_drive` integration tests.
    async fn sse_response(body: &'static str) -> reqwest::Response {
        use axum::Router;
        use axum::routing::post;

        let app = Router::new().route(
            "/sse",
            post(move || async move {
                axum::response::Response::builder()
                    .header("Content-Type", "text/event-stream")
                    .body(axum::body::Body::from(body))
                    .unwrap()
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        reqwest::Client::new()
            .post(format!("http://{addr}/sse"))
            .send()
            .await
            .unwrap()
    }
}
