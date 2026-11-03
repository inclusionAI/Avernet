use std::time::{Duration, Instant};
use std::{net::IpAddr, sync::Arc};

use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use bcs_protocol::stream::{
    ChatState, InteractionKind as WireInteractionKind, InteractionPhase, StreamEvent,
    TASK_INTENT_ELIGIBLE_KEY, parse_stream_event,
};
use bcs_protocol::{
    AgentEventPayload, AgentStream, Attachment, BCN_MESSAGE_ID_HEADER, BCN_PROTOCOL_VERSION_HEADER,
    BCN_TIMESTAMP_HEADER, BCN_TRANSPORT_HEADER, BcsFrame, ChatEventPayload,
    ChatEventState as WireChatState, ContentBlock, MessageContent, ProviderAbortResponse,
    ProviderAckResponse, ProviderHistoryResponse, ProviderWebhookBotRef, ProviderWebhookRequest,
    ProviderWebhookSender, RequestFrame,
};
use bcs_route_security::{OutboundUrlError, OutboundUrlGuard};
use bcs_service_api::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotDeliveryCommand, BotDeliveryKind,
    BotDeliveryPort, BotDeliveryResult, BotEventCommand, BotRunContext, BotRunContextPort,
    ChatEventState, DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS, GroupHistoryBotRequestPort,
    InteractionKind, InteractionProviderAck, InteractionProviderCommand, InteractionProviderPort,
    InteractionService, ProviderEventIngestCommand, ProviderEventIngestService,
    ProviderEventSource, ProviderInteractionRequestedCommand, ProviderInteractionResolvedCommand,
    ProviderRunTransport, ProviderTransportPreference, ServiceError, ServiceResult,
};
use opentelemetry::global;
use opentelemetry_http::HeaderInjector;
use serde::de::DeserializeOwned;
use serde_json::Value;
use tracing::{info, warn};
use tracing_opentelemetry::OpenTelemetrySpanExt;

mod caller_send;
mod sse;

use crate::sse::{IngestKind, SeqDecision, SeqDedup, classify, parse_sse_block};

/// Idle timeout for an SSE read loop: if no bytes arrive within this window the
/// run is considered stuck and closed with a synthesized error terminal (#3).
const SSE_IDLE_TIMEOUT_MS: u64 = 15 * 60 * 1_000;
/// Hard limit for a single SSE frame or an unterminated frame buffer.
const SSE_MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
/// Maximum time for an SSE request to receive response headers. Body liveness
/// is governed separately by `SSE_IDLE_TIMEOUT_MS` after the stream is accepted.
const SSE_RESPONSE_HEADER_TIMEOUT_MS: u64 = 125_000;
/// Maximum time to read the finite JSON acknowledgement when an SSE-preferred
/// request falls back to `application/json`.
const JSON_FALLBACK_BODY_TIMEOUT_MS: u64 = SSE_RESPONSE_HEADER_TIMEOUT_MS;
/// Existing provider execution budget for methods other than `chat.send`.
/// This is intentionally independent from configurable chat runs.
const NON_CHAT_PROVIDER_REQUEST_TIMEOUT_MS: u64 = 60 * 60 * 1_000;
/// Bounded retry for resolving run context after `deliver()` returns but before
/// `put_context` lands (#2 put_context race): ~50ms * 20 ≈ 1s.
const SSE_CTX_RETRY_INTERVAL_MS: u64 = 50;
const SSE_CTX_RETRY_MAX: u32 = 20;
/// When a run's consumption lag crosses this, emit a single WARN (rising edge)
/// that the run is falling behind the producer; a matching WARN is emitted once
/// it recovers below the threshold. Edge-triggered so a sustained backlog logs
/// twice (enter + recover), not once per frame.
const SSE_LAG_ALERT_MS: u64 = 5_000;

fn sse_next_read_timeout(now_ms: u64, deadline_ms: u64) -> (Duration, bool) {
    let idle = Duration::from_millis(SSE_IDLE_TIMEOUT_MS);
    if deadline_ms == u64::MAX {
        return (idle, false);
    }
    let remaining_ms = deadline_ms.saturating_sub(now_ms);
    if remaining_ms <= SSE_IDLE_TIMEOUT_MS {
        (Duration::from_millis(remaining_ms), true)
    } else {
        (idle, false)
    }
}

#[derive(Debug)]
enum ProviderAckBodyError {
    Decode(reqwest::Error),
    Timeout,
}

async fn read_provider_ack_body(
    response: reqwest::Response,
    timeout: Duration,
) -> Result<ProviderAckResponse, ProviderAckBodyError> {
    bcs_observability::observe_result("provider.read_provider_ack_body", async {
    match tokio::time::timeout(timeout, response.json::<ProviderAckResponse>()).await {
        Ok(Ok(ack)) => Ok(ack),
        Ok(Err(error)) => Err(ProviderAckBodyError::Decode(error)),
        Err(_) => Err(ProviderAckBodyError::Timeout),
    }
    }).await
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct ProviderClientPolicy {
    total_timeout: Option<Duration>,
    read_timeout: Option<Duration>,
    response_header_timeout: Option<Duration>,
    http2_only: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ProviderStatusPolicy {
    RequireSuccess,
    ReturnExplicitRejection,
}

impl ProviderClientPolicy {
    fn for_request(accept_sse: bool) -> Self {
        if accept_sse {
            Self {
                total_timeout: None,
                read_timeout: None,
                response_header_timeout: Some(Duration::from_millis(
                    SSE_RESPONSE_HEADER_TIMEOUT_MS,
                )),
                // Content negotiation must also reach HTTP/1.1 providers so
                // they can select JSON callback fallback on this same POST.
                http2_only: false,
            }
        } else {
            Self {
                total_timeout: Some(Duration::from_secs(65)),
                read_timeout: None,
                response_header_timeout: None,
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

#[derive(Clone)]
struct SseInteractionContext {
    service: Arc<dyn InteractionService>,
    provider_target: BotDeliveryTarget,
    provider_bypass_headers: Vec<(String, String)>,
}

pub struct HttpProviderTransport {
    /// Callback / history client with a 65s total timeout.
    client: reqwest::Client,
    /// SSE-capable client with NO total timeout: a total `.timeout()` would cut
    /// a long-lived stream. HTTP/1.1 remains enabled for JSON callback fallback.
    sse_client: reqwest::Client,
    url_guard: OutboundUrlGuard,
    chat_run_timeout_ms: u64,
    event_ingest: std::sync::RwLock<Option<Arc<dyn ProviderEventIngestService>>>,
    bot_run_context: std::sync::RwLock<Option<Arc<dyn BotRunContextPort>>>,
    interactions: std::sync::RwLock<Option<std::sync::Weak<dyn InteractionService>>>,
}

mod transport;
mod multiplex;
mod wire;
mod network;
mod stream;
use wire::*;
use network::*;
use stream::*;
pub use multiplex::{BotTransportMux, HistoryRequestMux, InteractionProviderMux};

fn provider_delivery_rejection(
    target_bot_id: String,
    status: reqwest::StatusCode,
) -> BotDeliveryResult {
    BotDeliveryResult {
        target_bot_id,
        delivered: false,
        error: Some(ServiceError::InternalError(format!(
            "provider explicitly rejected delivery with HTTP {}",
            status.as_u16()
        ))),
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
            post_provider(&self.client, &self.url_guard, &target, &body, &[])
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

#[cfg(test)]
mod client_policy_tests;
#[cfg(test)]
mod sse_loop_tests;

mod caller_container;

mod caller_observe;
pub use caller_observe::HttpObserveInjector;
mod caller_history;
