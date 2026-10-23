//! Observe-only delivery through the existing Provider envelope.
use std::time::Duration;
use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use bcs_protocol::{BcsFrame, RequestFrame, BCN_PROTOCOL_VERSION_HEADER, BCN_TIMESTAMP_HEADER};
use bcs_route_security::OutboundUrlGuard;
use tracing::info;
use super::provider_request_from_frame;

// ---------------------------------------------------------------------------
// Observe-only chat.inject v1 (spec §10.10): shared-context sync toward a
// NON-executing target's container. The runtime_target opaque ref + epoch
// ride the same runtime-only extensions zone as ensure; the observe_only
// mode is explicit. caller_context contains only the destination subject,
// never an execution token or model-visible identity material.
// A successful response requires an explicit ok=true JSON ACK; a
// 5xx/transport failure maps to Unavailable{retryable} — the retry resends
// the SAME message id, never fabricating success.
//
// The observe transport derives the Gateway coordinates from a BotDelivery
// target passed at construction (the provider webhook of the OBSERVER's
// provider), reusing the outbound-URL guard and the provider envelope.
// ---------------------------------------------------------------------------

const OBSERVE_INJECT_TOTAL_TIMEOUT_MS: u64 = 10_000;

/// Standalone ObserveInjectPort over one Provider webhook. Built by the
/// composition root from the observer target's provider coordinates; the
/// runtime never re-derives it from the inject path itself.
pub struct HttpObserveInjector {
    client: reqwest::Client,
    url_guard: OutboundUrlGuard,
    target: BotDeliveryTarget,
}

impl HttpObserveInjector {
    pub fn new(client: reqwest::Client, url_guard: OutboundUrlGuard, target: BotDeliveryTarget) -> Self {
        Self { client, url_guard, target }
    }
}

#[async_trait]
impl bcs_service_api::port::ObserveInjectPort for HttpObserveInjector {
    async fn inject_shared_context(
        &self,
        service_credential: &bcs_domain::RedactedToken,
        command: bcs_service_api::port::ObserveInjectCommand,
    ) -> Result<(), bcs_service_api::port::ObserveInjectError> {
        use bcs_service_api::port::ObserveInjectError as Error;

        if command.message_id.trim().is_empty() || command.text.is_empty() || command.now_ms <= 0
            || command.caller_subject.expose().trim().is_empty()
            || command.session_id.trim().is_empty()
            || command.runtime_target_ref.is_some() != command.runtime_target_epoch.is_some()
            || command.runtime_target_ref.as_ref().is_some_and(|v| v.expose().trim().is_empty())
            || command.runtime_target_epoch.as_ref().is_some_and(|v| v.expose().trim().is_empty())
        {
            return Err(Error::InvalidRequest);
        }
        let BotDeliveryTarget::HttpProvider {
            bot_id,
            webhook_url,
            protocol_version,
            ..
        } = &self.target
        else {
            return Err(Error::InvalidRequest);
        };
        if bot_id != &command.target_bot_id {
            return Err(Error::InvalidRequest);
        }
        let frame_id = uuid::Uuid::new_v4().to_string();
        let mut params = serde_json::json!({
            "bcs_session_id": command.session_id,
            "message": { "text": command.text },
            "extensions": {
                "contract_version": 1,
                "caller_context": {
                    "subject": { "kind": "human", "ref": command.caller_subject.expose() }
                },
                "execution": {
                    "mode": "observe_only",
                },
                "message_dedup_key": command.message_id,
            },
        });
        // Subject is authoritative in caller_context; references only refine
        // the destination and never duplicate identity or authorize execution.
        if let (Some(reference), Some(epoch)) = (&command.runtime_target_ref, &command.runtime_target_epoch) {
            params["extensions"]["runtime_target"] = serde_json::json!({
                "caller_ref": reference.expose(),
                "target_epoch": epoch.expose(),
                "purpose": "observe_only",
            });
        }
        let frame = BcsFrame::Request(RequestFrame::new(
            frame_id.clone(), "chat.inject", Some(params),
        ));
        let body = provider_request_from_frame(&self.target, &frame, OBSERVE_INJECT_TOTAL_TIMEOUT_MS)
            .map_err(|_| Error::InvalidRequest)?;

        // No-body log path: correlation facts only.
        info!(
            session_id = %command.session_id,
            frame_id = %frame_id,
            method = "chat.inject",
            "observe inject: posting gateway request"
        );

        let guarded_url = match self.url_guard.resolve_request_http_url(webhook_url).await {
            Ok(url) => url,
            Err(_) => return Err(Error::Unavailable { retryable: false }),
        };
        let request_fut = self.client
            .post(guarded_url.as_str())
            .bearer_auth(service_credential.expose_secret())
            .header("Accept", "application/json")
            .header("Content-Type", "application/json; charset=utf-8")
            .header(BCN_PROTOCOL_VERSION_HEADER, protocol_version.as_str())
            .header(BCN_TIMESTAMP_HEADER, bcs_protocol::now_ms().to_string())
            .json(&body)
            .send();
        let deadline = tokio::time::Instant::now() + Duration::from_millis(OBSERVE_INJECT_TOTAL_TIMEOUT_MS);
        let mut response = tokio::time::timeout_at(
            deadline,
            request_fut,
        )
        .await
        .map_err(|_| Error::Unavailable { retryable: true })?
        .map_err(|_| Error::Unavailable { retryable: true })?;

        if !response.status().is_success() {
            return Err(Error::Unavailable { retryable: response.status().is_server_error() });
        }
        // Cap both elapsed time and memory while reading; never log ACK content.
        let ack = tokio::time::timeout_at(deadline, async {
            let mut bytes = Vec::new();
            while let Some(chunk) = response.chunk().await
                .map_err(|_| Error::Unavailable { retryable: true })? {
                if bytes.len().saturating_add(chunk.len()) > 64 * 1024 {
                    return Err(Error::Unavailable { retryable: false });
                }
                bytes.extend_from_slice(&chunk);
            }
            serde_json::from_slice::<bcs_protocol::ProviderAckResponse>(&bytes)
                .map_err(|_| Error::Unavailable { retryable: false })
        }).await.map_err(|_| Error::Unavailable { retryable: true })??;
        if ack.ok {
            Ok(())
        } else {
            Err(Error::Unavailable { retryable: ack.retryable.unwrap_or(false) })
        }
    }
}
