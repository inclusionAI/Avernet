//! Caller-targeted history replay on the existing Provider chat.inject envelope.
//! Ordinary inject ACK is not ready: completion must echo the exact snapshot and
//! digest after applying all messages in the addressed Caller/session environment.
use std::time::Duration;

use async_trait::async_trait;
use bcs_domain::BotDeliveryTarget;
use bcs_protocol::{BcsFrame, RequestFrame, BCN_PROTOCOL_VERSION_HEADER, BCN_TIMESTAMP_HEADER};

use super::{provider_request_from_frame, HttpProviderTransport};

const HISTORY_COMPLETE_TOTAL_TIMEOUT_MS: u64 = 10_000;

#[async_trait]
impl bcs_service_api::port::HistoryBackfillPort for HttpProviderTransport {
    async fn inject_history_message(
        &self, target: &BotDeliveryTarget, credential: &bcs_domain::RedactedToken,
        command: bcs_service_api::port::ObserveInjectCommand,
    ) -> Result<(), bcs_service_api::port::HistoryBackfillError> {
        use bcs_service_api::port::{ObserveInjectPort, ObserveInjectError, HistoryBackfillError as Error};
        let injector = self.observe_injector(target.clone()).ok_or(Error::NotSent)?;
        injector.inject_shared_context(credential, command).await.map_err(|error| match error {
            ObserveInjectError::InvalidRequest => Error::NotSent,
            ObserveInjectError::Unavailable { retryable } => Error::Unavailable { retryable },
        })
    }

    async fn inject_history_complete(
        &self,
        target: &BotDeliveryTarget,
        service_credential: &bcs_domain::RedactedToken,
        command: bcs_service_api::port::HistoryCompleteCommand,
    ) -> Result<(), bcs_service_api::port::HistoryBackfillError> {
        use bcs_service_api::port::HistoryBackfillError as Error;

        // Structural gates BEFORE any network attempt: the attestation names
        // a snapshot, a full id list and the digest that covers it.
        if command.caller_subject.expose().trim().is_empty()
            || command.session_id.trim().is_empty()
            || command.snapshot_id.trim().is_empty()
            || command.manifest_digest.trim().is_empty()
            || command.now_ms <= 0
        {
            return Err(Error::NotSent);
        }
        // The id list is never structurally rejected for emptiness here:
        // an empty snapshot is legitimate (spec §6.8: 空快照也要确认) and a
        // real snapshot id + digest attest it — the gateway validates the
        // pairing, BCS never skips the confirmation.
        let BotDeliveryTarget::HttpProvider {
            webhook_url,
            protocol_version,
            ..
        } = target
        else {
            return Err(Error::NotSent);
        };
        let frame_id = uuid::Uuid::new_v4().to_string();
        let frame = BcsFrame::Request(RequestFrame::new(
            frame_id.clone(),
            "chat.inject",
            Some(serde_json::json!({
                "bcs_session_id": command.session_id,
                "extensions": {
                    "contract_version": 1,
                    "kind": "history_complete",
                    "caller_context": { "subject": { "kind": "human", "ref": command.caller_subject.expose() } },
                    "execution": { "mode": "history_complete" },
                    "history": {
                        "history_snapshot_id": command.snapshot_id,
                        "history_cutoff_seq": command.history_cutoff_seq,
                        "projection_version": command.projection_version,
                        "message_ids": command.message_ids,
                        "manifest_digest": command.manifest_digest,
                    },
                    // The attestation's idempotency key: repeated completes
                    // (history_ready loss) dedupe on the snapshot itself.
                    "message_dedup_key": format!("history_complete:{}", command.snapshot_id),
                },
            })),
        ));
        let body =
            provider_request_from_frame(target, &frame, HISTORY_COMPLETE_TOTAL_TIMEOUT_MS)
                .map_err(|_| Error::NotSent)?;

        tracing::info!(
            session_id = %command.session_id,
            frame_id = %frame_id,
            cutoff = command.history_cutoff_seq,
            messages = command.message_ids.len(),
            method = "chat.inject",
            "history_complete: posting snapshot attestation"
        );
        let guarded_url = match self.url_guard.resolve_request_http_url(webhook_url).await {
            Ok(url) => url,
            Err(_) => return Err(Error::Unavailable { retryable: false }),
        };
        let request_fut = self
            .client
            .post(guarded_url.as_str())
            .bearer_auth(service_credential.expose_secret())
            .header("Accept", "application/json")
            .header("Content-Type", "application/json; charset=utf-8")
            .header(BCN_PROTOCOL_VERSION_HEADER, protocol_version.as_str())
            .header(BCN_TIMESTAMP_HEADER, bcs_protocol::now_ms().to_string())
            .json(&body)
            .send();
        let deadline = tokio::time::Instant::now() + Duration::from_millis(HISTORY_COMPLETE_TOTAL_TIMEOUT_MS);
        let mut response = tokio::time::timeout_at(deadline, request_fut)
            .await
            .map_err(|_| Error::Unavailable { retryable: true })?
            .map_err(|_| Error::Unavailable { retryable: true })?;

        if !response.status().is_success() {
            return Err(Error::Unavailable { retryable: response.status().is_server_error() });
        }
        // Cap both elapsed time and memory while reading; never log ACK content.
        // A plain acceptance ACK does not prove that history was applied.
        let ack = tokio::time::timeout_at(deadline, async {
            let mut bytes = Vec::new();
            while let Some(chunk) = response.chunk().await
                .map_err(|_| Error::Unavailable { retryable: true })? {
                if bytes.len().saturating_add(chunk.len()) > 64 * 1024 {
                    return Err(Error::Unavailable { retryable: false });
                }
                bytes.extend_from_slice(&chunk);
            }
            serde_json::from_slice::<HistoryReadyAck>(&bytes)
                .map_err(|_| Error::Unavailable { retryable: false })
        }).await.map_err(|_| Error::Unavailable { retryable: true })??;
        if ack.ok {
            let ready = ack.history_ready.ok_or(Error::Unavailable { retryable: false })?;
            if ready.history_snapshot_id != command.snapshot_id || ready.manifest_digest != command.manifest_digest {
                return Err(Error::Unavailable { retryable: false });
            }
            Ok(())
        } else {
            Err(Error::Unavailable { retryable: ack.retryable.unwrap_or(false) })
        }
    }
}

/// HTTP-local response contract, not an additional run/attempt lifecycle.
#[derive(serde::Deserialize)]
struct HistoryReadyAck {
    ok: bool,
    retryable: Option<bool>,
    history_ready: Option<HistoryReady>,
}
#[derive(serde::Deserialize)]
struct HistoryReady {
    history_snapshot_id: String,
    manifest_digest: String,
}
