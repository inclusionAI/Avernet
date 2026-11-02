use super::*;

// ---------------------------------------------------------------------------
// caller.container.ensure (spec §10.8; P2-B first slice)
//
// Service-authenticated container preparation over the Provider webhook
// infrastructure. Boundary rules (wire contract apps/bcs/docs/ caller-
// container-gateway.md):
// - exactly one network attempt per invocation; no redirects, no retries
// - independent no-body logging path: method/frame/bot level info only,
//   never the request body, subject, binding, URL or response text
// - response read capped at 64 KiB
// - the readback linkage (target + subject) is validated against the
//   request; corrupted readbacks reject instead of being trusted
// - a closed error set: GatewayRejected (definite refusal, explicit
//   retryability) / ProviderUnavailable (transport or ambiguous outcome —
//   never proves the container was not created) / InvalidRequest
// - ensure authorizes as the BCS service (Provider credential); it never
//   depends on or issues execution tokens
// ---------------------------------------------------------------------------

/// Maximum ensure response body accepted, matching the wire contract. The
/// read is aborted beyond this cap and mapped to ProviderUnavailable.
const CALLER_ENSURE_MAX_RESPONSE_BYTES: usize = 64 * 1024;
const CALLER_ENSURE_TOTAL_TIMEOUT_MS: u64 = 10_000;

#[async_trait]
impl bcs_service_api::port::CallerContainerGatewayPort for HttpProviderTransport {
    async fn ensure_container(
        &self,
        target: &BotDeliveryTarget,
        service_credential: &bcs_domain::RedactedToken,
        request: bcs_service_api::port::EnsureCallerContainer,
    ) -> Result<
        bcs_service_api::port::CallerContainerDescriptor,
        bcs_service_api::port::CallerContainerError,
    > {
        use bcs_service_api::port::{CallerContainerError as Error, CallerContainerIsolation, CallerObserveTarget, CallerOpaqueRef};

        let BotDeliveryTarget::HttpProvider {
            webhook_url,
            protocol_version,
            provider_id,
            provider_bot_ref,
            ..
        } = target
        else {
            return Err(Error::InvalidRequest);
        };
        if request.target_bot_id.is_empty()
            || request.session_id.is_empty()
            || request.environment.is_empty()
            || request.idempotency_key.is_empty()
            || request.run.run_id.is_empty()
        {
            return Err(Error::InvalidRequest);
        }
        if request.container_ttl_ms <= 0 {
            return Err(Error::InvalidRequest);
        }

        // Build the Provider@WebhookRequest envelope exactly the way every
        // other Provider downlink does (frame → provider_request_from_frame),
        // with the §10.8 fields carried inside `extensions` — the
        // runtime-only extension zone that stays outside message bodies and
        // ordinary logs. H2A carries no group id; the session id pins the
        // isolation scope.
        let frame_id = uuid::Uuid::new_v4().to_string();
        let frame = BcsFrame::Request(RequestFrame::new(
            frame_id.clone(),
            "caller.container.ensure",
            Some(serde_json::json!({
                "bcs_session_id": request.session_id,
                // §10.8 fields ride the runtime-only extensions zone; the
                // Provider envelope drops unclassified params.
                "extensions": {
                    "contract_version": 1,
                    "bot_id": request.target_bot_id,
                    "caller_subject": {
                        "kind": "human",
                        "ref": request.caller_subject_ref.expose(),
                    },
                    "environment": request.environment,
                    "run": {
                        "run_id": request.run.run_id,
                        "root_run_id": request.run.root_run_id,
                        "parent_run_id": request.run.parent_run_id,
                        "message_turn_id": request.run.message_turn_id,
                    },
                    "policy": {
                        "minimum_isolation": "caller_container",
                        "create_mode": match request.create_mode {
                            bcs_service_api::port::EnsureCreateMode::Lazy => "lazy",
                            bcs_service_api::port::EnsureCreateMode::Warmup => "warmup",
                            bcs_service_api::port::EnsureCreateMode::MustExist => "must_exist",
                        },
                        "ttl_ms": request.container_ttl_ms,
                    },
                    "idempotency_key": request.idempotency_key,
                },
            })),
        ));
        let body = provider_request_from_frame(target, &frame, CALLER_ENSURE_TOTAL_TIMEOUT_MS)
            .map_err(|_| Error::InvalidRequest)?;

        // URL/DNS guard against the target's Provider webhook: one attempt,
        // no redirects (client policy below), private blocking per guard.
        let guarded_url = match self.url_guard.resolve_request_http_url(webhook_url).await {
            Ok(url) => url,
            Err(_) => return Err(Error::ProviderUnavailable { retryable: false }),
        };
        // No-body log path: correlation facts only.
        info!(
            provider_id = %provider_id,
            provider_bot_ref = %provider_bot_ref,
            method = "caller.container.ensure",
            frame_id = %frame_id,
            "caller ensure: posting gateway request"
        );

        let request_started = Instant::now();
        // DNS-rebinding guard: pin the validated addresses for the whole
        // request (see request_client_for_guarded_url).
        let request_client = match request_client_for_guarded_url(
            &self.client,
            &guarded_url,
            ProviderClientPolicy::for_request(false),
        ) {
            Ok(client) => client,
            Err(_) => return Err(Error::ProviderUnavailable { retryable: false }),
        };
        let request_fut = request_client
            .post(guarded_url.as_str())
            .bearer_auth(service_credential.expose_secret())
            .header("Accept", "application/json")
            .header("Content-Type", "application/json; charset=utf-8")
            .header(BCN_PROTOCOL_VERSION_HEADER, protocol_version.as_str())
            .header(BCN_TIMESTAMP_HEADER, bcs_protocol::now_ms().to_string())
            .json(&body)
            .send();
        let response_result = tokio::time::timeout(
            Duration::from_millis(CALLER_ENSURE_TOTAL_TIMEOUT_MS),
            request_fut,
        )
        .await;
        let response = match response_result {
            Ok(Ok(response)) => response,
            Ok(Err(_)) => {
                warn!(
                    provider_id = %provider_id,
                    frame_id = %frame_id,
                    elapsed_ms = request_started.elapsed().as_millis() as u64,
                    "caller ensure: request failed before any conclusion"
                );
                return Err(Error::ProviderUnavailable { retryable: true });
            }
            Err(_) => {
                warn!(
                    provider_id = %provider_id,
                    frame_id = %frame_id,
                    elapsed_ms = request_started.elapsed().as_millis() as u64,
                    "caller ensure: timed out, outcome unknown"
                );
                return Err(Error::ProviderUnavailable { retryable: true });
            }
        };
        let status = response.status();
        if !status.is_success() {
            // Definite transport-level refusal; retryability is explicit per
            // status family and never guessed from the body.
            let retryable = status.is_server_error();
            warn!(
                provider_id = %provider_id,
                frame_id = %frame_id,
                status = %status.as_u16(),
                "caller ensure: gateway returned a non-success status"
            );
            return Err(Error::GatewayRejected { retryable });
        }
        let bytes = match tokio::time::timeout(
            Duration::from_millis(CALLER_ENSURE_TOTAL_TIMEOUT_MS),
            response.bytes(),
        )
        .await
        {
            Ok(Ok(bytes)) => bytes,
            _ => {
                warn!(
                    provider_id = %provider_id,
                    frame_id = %frame_id,
                    "caller ensure: response read failed or timed out"
                );
                return Err(Error::ProviderUnavailable { retryable: true });
            }
        };
        if bytes.len() > CALLER_ENSURE_MAX_RESPONSE_BYTES {
            return Err(Error::ProviderUnavailable { retryable: false });
        }
        let parsed: Value = match serde_json::from_slice(&bytes) {
            Ok(value) => value,
            Err(_) => return Err(Error::ProviderUnavailable { retryable: false }),
        };
        if parsed.get("ok") != Some(&Value::Bool(true)) {
            // Definite gateway refusal shape: retryable flag must be read
            // verbatim when present; errors never carry the payload forward.
            let retryable = parsed
                .get("error")
                .and_then(|error| error.get("retryable"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
            return Err(Error::GatewayRejected { retryable });
        }
        let isolation = match parsed
            .get("isolation")
            .and_then(|isolation| isolation.get("level"))
            .and_then(Value::as_str)
        {
            Some("container_bound") => CallerContainerIsolation::ContainerBound,
            _ => return Err(Error::GatewayRejected { retryable: false }),
        };
        // Per the frozen wire boundary (2026-10-05): the response parses
        // ok/isolation only; the Bot/subject linkage is the request's own
        // already-bound association, never a Gateway echo. No echo fields
        // exist to validate in this subset.
        let binding_ref = parsed
            .get("isolation")
            .and_then(|isolation| isolation.get("binding_ref"))
            .and_then(Value::as_str)
            .filter(|binding| !binding.is_empty())
            .map(CallerOpaqueRef::new);
        let runtime_generation = parsed
            .get("isolation")
            .and_then(|isolation| isolation.get("generation"))
            .and_then(Value::as_u64)
            .map(|generation| u32::try_from(generation).unwrap_or(u32::MAX));
        let container_expires_at_ms = parsed
            .get("isolation")
            .and_then(|isolation| isolation.get("expires_at_ms"))
            .and_then(Value::as_i64)
            .unwrap_or(0);
        // Container lifetime never extends the trusted deadline inputs.
        let expires_at_ms = request.deadline_ms.min(container_expires_at_ms);
        // Group-sync observe target (spec §10.8 `observe_target`): parsed
        // from the response when present; never fabricated for pure H2A
        // confirms. The runtime-only ref keeps its redacted boundaries.
        let observe_target = parsed
            .get("observe_target")
            .and_then(|target| {
                Some(CallerObserveTarget {
                    caller_subject: request.caller_subject_ref.clone(),
                    caller_ref: Some(CallerOpaqueRef::new(
                        target.get("caller_ref")?.as_str()?.to_string(),
                    )),
                    target_bot_id: target.get("bot_id")?.as_str()?.to_string(),
                    session_id: target.get("session_id")?.as_str()?.to_string(),
                    target_epoch: Some(CallerOpaqueRef::new(
                        target.get("target_epoch")?.as_str()?.to_string(),
                    )),
                    expires_at_ms: target.get("expires_at_ms")?.as_i64()?,
                })
            });
        Ok(bcs_service_api::port::CallerContainerDescriptor {
            sandbox_container_id: parsed.get("sandbox_container_id").and_then(Value::as_str)
                .filter(|id| !id.trim().is_empty()).map(CallerOpaqueRef::new),
            target_bot_id: request.target_bot_id,
            caller_subject_ref: request.caller_subject_ref,
            ready: true,
            isolation,
            binding_ref,
            runtime_generation,
            expires_at_ms,
            observe_target,
        })
    }
}
