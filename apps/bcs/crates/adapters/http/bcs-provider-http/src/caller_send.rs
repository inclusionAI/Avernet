//! Protected Caller send over the existing Provider webhook. Association
//! fields reuse envelope routing; dispatch control remains internal to BCS.
use std::time::{Duration, Instant};

use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use bcs_protocol::{
    BCN_PROTOCOL_VERSION_HEADER, BCN_TIMESTAMP_HEADER, BcsFrame, ChannelInfo, ChannelSource,
    RequestFrame,
};
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
        // Shape-only guards. Expiry values (run budget, proof/context lifetimes)
        // are advisory wire facts and are NEVER compared to the clock — a
        // redrive that outlives an advisory must still ship (2026-10-25
        // incident follow-up: identity windows are gone end to end).
        let wall_now = bcs_protocol::now_ms() as i64;
        if command.association.attempt == 0
            || command.association.run_id.is_empty()
            || command.association.session_id.is_empty()
            || command.association.dispatch_id.is_empty()
            || command.association.environment.is_empty()
            || command.association.target_bot_id.is_empty()
            || command.caller_subject_ref.expose().trim().is_empty()
            || command.channel_actor_id.trim().is_empty()
            || command.text.is_empty()
            || command.request_digest.len() != 71 // sha256: + 64
        {
            return Err(Error::NotSent);
        }

        // Protected chat.send frame: protocol 2.0, id = run id, H2A empty group.
        // The channel zone is what the webhook envelope derives `from` from
        // (`provider_sender_from_params`): without a non-empty actor the
        // envelope ships `from: null`, which the Provider downlink contract
        // rejects with a 400 (BaaS `ChatSendRequest.from` is required).
        // The actor is the ORIGINAL message's trusted ordinary sender
        // (facts committed by the caller admission seam) — never the
        // protected caller subject, which stays confined to the
        // `caller_context` extension zone; and identity_forwarding stays
        // off so the sender is never prepended into the message body.
        let channel = serde_json::to_value(ChannelInfo {
            source: ChannelSource::Api,
            user_id: None,
            actor_id: Some(command.channel_actor_id.clone()),
            actor_name: Some(command.channel_actor_name.clone()),
            thread_id: Some(command.association.session_id.clone()),
            identity_forwarding: None,
        })
        .map_err(|_| Error::NotSent)?;
        // The envelope's `message` must satisfy the Provider downlink's
        // `DownlinkMessage` contract (role + content blocks, string text
        // alone is a required-field 400) — the same shape the ordinary
        // chat.send frame serializes.
        let message = serde_json::to_value(bcs_protocol::MessageContent {
            role: "user".to_string(),
            content: vec![bcs_protocol::ContentBlock::text(command.text.clone())],
            timestamp: wall_now.max(0) as u64,
        })
        .map_err(|_| Error::NotSent)?;
        // The wire `timeout_ms` is the Provider-side EXECUTION budget (its run
        // guardian cancels the run at `gmt_create + timeout_ms`): it carries
        // the dispatch's committed window (`not_after_ms` minus wire time),
        // the same budget concept the ordinary chat.send sends (3h). The
        // local synchronous-ACK wait stays `callback_timeout_ms` and never
        // rides the wire — shipping it here cancelled every protected run
        // after 30 seconds ("executor safety-net: execution cancelled").
        let execution_budget_ms = command
            .not_after_ms
            .saturating_sub(wall_now)
            .max(1) as u64;
        let mut params = serde_json::json!({
            "bcs_session_id": command.association.session_id,
            "bcs_group_id": command.group_id.as_deref().unwrap_or_default(),
            "message": message,
            "timeout_ms": execution_budget_ms,
            "channel": channel,
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
        // DNS-rebinding guard: post through the pinning client bound to the
        // validated addresses — never the shared client that would re-resolve
        // the hostname at connect time.
        let request_client = match super::request_client_for_guarded_url(
            &self.client,
            &guarded_url,
            super::ProviderClientPolicy::for_request(false),
        ) {
            Ok(client) => client,
            Err(_) => return Err(Error::NotSent),
        };
        let started = Instant::now();
        let request_fut = request_client
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
            // The response body diagnosticises the Provider's own validation
            // errors — a 400 from a required-field miss (e.g. the historic
            // `from: null`) is otherwise indistinguishable from a silence. The
            // two runtime-only secrets this request carries are redacted out
            // before logging, then the preview truncates on a char boundary.
            let body = tokio::time::timeout(
                Duration::from_millis(command.callback_timeout_ms),
                response.bytes(),
            )
            .await
            .ok()
            .and_then(|bytes| bytes.ok())
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
            .unwrap_or_default();
            let redacted = body
                .replace(command.execution_token.expose(), "[redacted]")
                .replace(command.caller_subject_ref.expose(), "[redacted]");
            let preview: &str = match redacted.char_indices().nth(512) {
                Some((idx, _)) => &redacted[..idx],
                None => &redacted,
            };
            warn!(
                provider_id = %provider_id,
                run_id = %command.association.run_id,
                status = %status.as_u16(),
                body = %preview,
                "protected caller send: non-ack status"
            );
            return Err(Error::GatewayFailed { retryable: status.is_server_error() });
        }
        // A 2xx means the request reached the Provider: any ACK-shape failure
        // from here on (body timeout, oversized body, unparsable JSON) cannot
        // falsify acceptance, so it stays OutcomeUnknown — never a fabricated
        // rejection (wire doc §3.2 "传输超时/出错：请求已离栈的一律记
        // OutcomeUnknown").
        let bytes = match tokio::time::timeout(
            Duration::from_millis(command.callback_timeout_ms),
            response.bytes(),
        )
        .await
        {
            Ok(Ok(bytes)) => bytes,
            _ => return Err(Error::OutcomeUnknown),
        };
        if bytes.len() > CALLER_EXECUTION_ACK_MAX_BYTES {
            warn!(
                provider_id = %provider_id,
                run_id = %command.association.run_id,
                "protected caller send: ack body over the wire cap"
            );
            return Err(Error::OutcomeUnknown);
        }
        let parsed: Value = match serde_json::from_slice(&bytes) {
            Ok(value) => value,
            Err(_) => return Err(Error::OutcomeUnknown),
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
                return Err(Error::OutcomeUnknown);
            }
        };
        Ok(acknowledged)
    }
}

#[cfg(test)]
mod dns_rebinding_pinning_tests {
    use super::*;

    /// Guard-validated addresses must be the ONLY route to the network: a
    /// post through `request_client_for_guarded_url` connects to the pinned
    /// loopback listener even though the hostname cannot resolve anywhere —
    /// connect-time DNS gets no second vote (the DNS-rebinding TOCTOU that
    /// the audit flagged on all four caller egress paths).
    #[tokio::test]
    async fn pinned_client_connects_only_to_the_guard_addrs() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0)).await.expect("bind");
        let addr = listener.local_addr().expect("local addr");
        let host = format!("rebind-{}.invalid", uuid::Uuid::new_v4());
        let raw_url = format!("http://{host}/");
        let guarded = bcs_route_security::ValidatedRequestUrl::pinned_for_tests(
            &raw_url,
            &host,
            std::slice::from_ref(&addr),
        );

        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.expect("accept");
            let mut buf = [0u8; 512];
            let read = socket.read(&mut buf).await.expect("read head");
            let head = String::from_utf8_lossy(&buf[..read]).to_string();
            let payload = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{\"ok\":true}";
            let _ = socket.write_all(payload.as_bytes()).await;
            head
        });

        let shared = crate::provider_client_builder(crate::ProviderClientPolicy::for_request(false))
            .build()
            .expect("shared client");
        let pinned = crate::request_client_for_guarded_url(
            &shared,
            &guarded,
            crate::ProviderClientPolicy::for_request(false),
        )
        .expect("pinned client");
        let response = pinned
            .post(guarded.as_str())
            .json(&serde_json::json!({ "probe": true }))
            .send()
            .await
            .expect("the pinned client must connect to the guarded addrs");
        assert!(response.status().is_success());
        let head = server.await.expect("server join");
        assert!(head.starts_with("POST /"), "unexpected request head: {head}");
    }
}
