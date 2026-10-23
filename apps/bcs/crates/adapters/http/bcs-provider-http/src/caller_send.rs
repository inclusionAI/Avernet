//! Protected Caller send over the existing Provider webhook. Association
//! fields reuse envelope routing; dispatch control remains internal to BCS.
use std::time::{Duration, Instant};

use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use bcs_protocol::{BCN_PROTOCOL_VERSION_HEADER, BCN_TIMESTAMP_HEADER, BcsFrame, RequestFrame};
use serde_json::Value;
use tracing::{info, warn};

use super::{HttpProviderTransport, provider_request_from_frame};

// ---------------------------------------------------------------------------
// Protected root-H2A chat.send v1 (spec §10.9; wire doc "受保护 root-H2A
// chat.send v1"). Independent outbound CallerExecutionSendPort over the
// Provider webhook envelope: caller material rides runtime-only extension
// zones, never the ordinary request-body logger, DBs, queues or model input.
// The ACK requires explicit ok=true; local in-flight context owns correlation;
// transport errors after the request started are OutcomeUnknown, never
// fabricated rejections.
// ---------------------------------------------------------------------------

/// Response cap for the protected send ACK (wire contract: 64 KiB), applied
/// after the transport; a larger body maps to GatewayFailed.
const CALLER_EXECUTION_ACK_MAX_BYTES: usize = 64 * 1024;

#[async_trait]
impl bcs_service_api::port::CallerExecutionSendPort for HttpProviderTransport {
    async fn send_caller_execution(
        &self,
        target: &BotDeliveryTarget,
        service_credential: &bcs_domain::RedactedToken,
        command: bcs_service_api::port::SendCallerExecutionCommand,
    ) -> Result<bcs_service_api::port::CallerDispatchAck, bcs_service_api::port::CallerSendError> {
        use bcs_service_api::port::{CallerDispatchAck, CallerSendError as Error};

        let BotDeliveryTarget::HttpProvider {
            bot_id,
            webhook_url,
            protocol_version,
            provider_id,
            provider_bot_ref,
            ..
        } = target
        else {
            return Err(Error::NotSent);
        };
        // The resolved route and existing dispatch must identify one target;
        // never serialize competing copies of provider identity in extensions.
        if command.association.target_bot_id != *bot_id
            || command.association.provider_id != *provider_id
            || command.association.provider_bot_ref != *provider_bot_ref
        {
            return Err(Error::NotSent);
        }
        // Review-V P2-5: freshness is judged at WIRE TIME, not the (possibly
        // stale) producer entry clock — the ensure/issue/commit network
        // stages may have outlived the proof between entry and here. The
        // proof's own expiry is authoritative: an already-expired token is
        // never shipped, and the wire never advertises a longer token
        // validity than the proof actually has.
        let wall_now = bcs_protocol::now_ms() as i64;
        if command.association.attempt == 0
            || command.association.run_id.is_empty()
            || command.association.session_id.is_empty()
            || command.association.dispatch_id.is_empty()
            || command.association.environment.is_empty()
            || command.association.target_bot_id.is_empty()
            || command.caller_subject_ref.expose().trim().is_empty()
            || command.text.is_empty()
            || command.request_digest.len() != 71 // sha256: + 64
            || command.not_after_ms <= wall_now
            || command.execution_token_expires_at_ms <= wall_now
        {
            return Err(Error::NotSent);
        }

        // Protected chat.send frame: protocol 2.0, id = run id, H2A empty group.
        let mut params = serde_json::json!({
            "bcs_session_id": command.association.session_id,
            "bcs_group_id": command.group_id.as_deref().unwrap_or_default(),
            "message": { "text": command.text.clone() },
            "timeout_ms": command.callback_timeout_ms,
            "extensions": {
                "execution": {
                    "not_after_ms": command.not_after_ms,
                },
                "caller_context": {
                    "subject": { "kind": command.subject_kind, "ref": command.caller_subject_ref.expose() },
                    "expires_at_ms": command.caller_context_expires_at_ms,
                },
                "caller_execution_token": {
                    "version": "1.0",
                    "format": command.proof_format,
                    "type": command.proof_type,
                    "token": command.execution_token.expose(),
                    // Review-V P2-5: the PROOF's own lifetime, never the
                    // longer caller-context window — a plugin may legally
                    // issue shorter than the effective window.
                    "expires_at_ms": command.execution_token_expires_at_ms,
                },
            },
        });
        if let Some(history) = &command.history {
            params["extensions"]["execution"]["history"] = serde_json::json!({
                "history_snapshot_id": history.history_snapshot_id,
                "history_cutoff_seq": history.history_cutoff_seq,
                "projection_version": history.projection_version,
                "manifest_digest": history.manifest_digest,
            });
        }
        let frame = BcsFrame::Request(RequestFrame::new(
            command.association.run_id.clone(), "chat.send", Some(params),
        ));
        let body = match provider_request_from_frame(
            target,
            &frame,
            command.callback_timeout_ms.max(1),
        ) {
            Ok(body) => body,
            Err(_) => return Err(Error::NotSent),
        };

        let guarded_url = match self.url_guard.resolve_request_http_url(webhook_url).await {
            Ok(url) => url,
            Err(_) => return Err(Error::NotSent),
        };
        info!(
            provider_id = %provider_id,
            provider_bot_ref = %provider_bot_ref,
            method = "chat.send.protected",
            run_id = %command.association.run_id,
            attempt = command.association.attempt,
            "protected caller send: posting gateway request"
        );
        let started = Instant::now();
        let request_fut = self.client
            .post(guarded_url.as_str())
            .bearer_auth(service_credential.expose_secret())
            .header("Accept", "application/json")
            .header("Content-Type", "application/json; charset=utf-8")
            .header(BCN_PROTOCOL_VERSION_HEADER, protocol_version.as_str())
            .header(BCN_TIMESTAMP_HEADER, bcs_protocol::now_ms().to_string())
            .json(&body)
            .send();
        let response = match tokio::time::timeout(
            Duration::from_millis(command.callback_timeout_ms),
            request_fut,
        )
        .await
        {
            Ok(Ok(response)) => response,
            // Build errors mean nothing left the wire; send errors after
            // start stay ambiguous.
            Ok(Err(_)) if started.elapsed().is_zero() => return Err(Error::NotSent),
            Ok(Err(_)) | Err(_) => {
                warn!(
                    provider_id = %provider_id,
                    run_id = %command.association.run_id,
                    elapsed_ms = started.elapsed().as_millis() as u64,
                    "protected caller send: transport unknown"
                );
                return Err(Error::OutcomeUnknown);
            }
        };
        let status = response.status();
        if !status.is_success() {
            warn!(
                provider_id = %provider_id,
                run_id = %command.association.run_id,
                status = %status.as_u16(),
                "protected caller send: non-ack status"
            );
            return Err(Error::GatewayFailed { retryable: status.is_server_error() });
        }
        let bytes = match tokio::time::timeout(
            Duration::from_millis(command.callback_timeout_ms),
            response.bytes(),
        )
        .await
        {
            Ok(Ok(bytes)) => bytes,
            _ => return Err(Error::GatewayFailed { retryable: true }),
        };
        if bytes.len() > CALLER_EXECUTION_ACK_MAX_BYTES {
            return Err(Error::GatewayFailed { retryable: false });
        }
        let parsed: Value = match serde_json::from_slice(&bytes) {
            Ok(value) => value,
            Err(_) => return Err(Error::GatewayFailed { retryable: true }),
        };
        if parsed.get("ok") != Some(&Value::Bool(true)) {
            let retryable = parsed
                .get("error")
                .and_then(|error| error.get("retryable"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            let error_code = parsed
                .get("error")
                .and_then(|error| error.get("code"))
                .and_then(Value::as_str)
                .unwrap_or("UNKNOWN")
                .to_string();
            return Ok(CallerDispatchAck::Rejected { error_code, retryable });
        }
        // The synchronous HTTP response belongs to this in-flight request.
        // Control revision/digest remain trusted BCS facts, never Provider input.
        // Old Providers may still include echo fields; they are not authority.
        let state_value = match parsed.get("state") {
            None => "accepted",
            Some(Value::String(state)) => state.as_str(),
            Some(state) => state.get("state").and_then(Value::as_str).unwrap_or_default(),
        };
        let acknowledged = match state_value {
            "accepted" => CallerDispatchAck::Accepted {
                control_revision: command.control_revision,
                request_digest: command.request_digest,
            },
            "unknown" => CallerDispatchAck::Unknown,
            other => {
                warn!(
                    provider_id = %provider_id,
                    run_id = %command.association.run_id,
                    %other,
                    "protected caller send: unknown ack state"
                );
                return Err(Error::GatewayFailed { retryable: false });
            }
        };
        Ok(acknowledged)
    }
}
