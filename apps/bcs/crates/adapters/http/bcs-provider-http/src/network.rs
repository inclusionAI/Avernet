use super::*;

pub(super) async fn post_provider<T: DeserializeOwned>(
    client: &reqwest::Client,
    url_guard: &OutboundUrlGuard,
    target: &BotDeliveryTarget,
    body: &ProviderWebhookRequest,
    provider_bypass_headers: &[(String, String)],
) -> ServiceResult<T> {
    bcs_observability::observe_result("provider.post_provider", async {
    let response = send_provider_request(
        client,
        url_guard,
        target,
        body,
        false,
        provider_bypass_headers,
    )
    .await?;
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
    }).await
}

/// Send the webhook request and return the raw response after requiring a
/// successful status (except the documented chat.abort 410). The delivery-only
/// sibling returns explicit non-success responses so they can become terminal
/// delivery rejections instead of ambiguous transport failures.
pub(super) async fn send_provider_request(
    client: &reqwest::Client,
    url_guard: &OutboundUrlGuard,
    target: &BotDeliveryTarget,
    body: &ProviderWebhookRequest,
    accept_sse: bool,
    provider_bypass_headers: &[(String, String)],
) -> ServiceResult<reqwest::Response> {
    send_provider_request_with_policy(
        client,
        url_guard,
        target,
        body,
        accept_sse,
        provider_bypass_headers,
        ProviderClientPolicy::for_request(accept_sse),
        ProviderStatusPolicy::RequireSuccess,
    )
    .await
}

pub(super) async fn send_provider_delivery_request(
    client: &reqwest::Client,
    url_guard: &OutboundUrlGuard,
    target: &BotDeliveryTarget,
    body: &ProviderWebhookRequest,
    accept_sse: bool,
    provider_bypass_headers: &[(String, String)],
) -> ServiceResult<reqwest::Response> {
    send_provider_request_with_policy(
        client,
        url_guard,
        target,
        body,
        accept_sse,
        provider_bypass_headers,
        ProviderClientPolicy::for_request(accept_sse),
        ProviderStatusPolicy::ReturnExplicitRejection,
    )
    .await
}

pub(super) fn is_safe_provider_bypass_header(name: &str) -> bool {
    let trimmed = name.trim();
    if trimmed.is_empty() || reqwest::header::HeaderName::try_from(trimmed).is_err() {
        return false;
    }
    let lower = trimmed.to_ascii_lowercase();
    !matches!(
        lower.as_str(),
        "authorization"
            | "cookie"
            | "host"
            | "content-length"
            | "content-type"
            | "x-bcs-bot-token"
            | "x-bcs-service-key"
    ) && lower != "bcn"
        && !lower.starts_with("bcn-")
        && !lower.starts_with("x-bcn-")
}

pub(super) async fn send_provider_request_with_policy(
    client: &reqwest::Client,
    url_guard: &OutboundUrlGuard,
    target: &BotDeliveryTarget,
    body: &ProviderWebhookRequest,
    accept_sse: bool,
    provider_bypass_headers: &[(String, String)],
    client_policy: ProviderClientPolicy,
    status_policy: ProviderStatusPolicy,
) -> ServiceResult<reqwest::Response> {
    bcs_observability::observe_result("provider.send_provider_request_with_policy", async {
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
            return Err(ServiceError::DeliveryNotSent {
                code: "provider_url_preflight_failed",
                retryable: matches!(error, OutboundUrlError::ResolveFailed(_)),
            });
        }
    };
    let pinned_client = provider_client_for_url(&guarded_url, client_policy).map_err(|_| {
        ServiceError::DeliveryNotSent { code: "provider_client_build_failed", retryable: false }
    })?;
    let dns_pinned = pinned_client.is_some();
    let request_client = pinned_client.as_ref().unwrap_or(client);

    let message_id = uuid::Uuid::new_v4().to_string();
    let accept = if accept_sse {
        "text/event-stream, application/json"
    } else {
        "application/json"
    };
    let transport = if accept_sse { "sse" } else { "callback" };
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
        response_header_timeout_ms = ?client_policy.response_header_timeout.map(|timeout| timeout.as_millis()),
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
    for (name, value) in provider_bypass_headers {
        if is_safe_provider_bypass_header(name) {
            request = request.header(name.as_str(), value.as_str());
        }
    }
    if protocol_version == "2.0" {
        request = request.header(BCN_TRANSPORT_HEADER, transport);
    }
    let context = tracing::Span::current().context();
    let mut trace_headers = reqwest::header::HeaderMap::new();
    global::get_text_map_propagator(|propagator| {
        propagator.inject_context(&context, &mut HeaderInjector(&mut trace_headers));
    });
    request = request.headers(trace_headers);
    // Build errors (including invalid headers) are known to precede any HTTP
    // submission. Errors after execute starts remain ambiguous.
    let request = request.json(body).build().map_err(|_| ServiceError::DeliveryNotSent {
        code: "provider_request_build_failed", retryable: false,
    })?;
    let send = request_client.execute(request);
    let response_result =
        if let Some(response_header_timeout) = client_policy.response_header_timeout {
            match tokio::time::timeout(response_header_timeout, send).await {
                Ok(result) => result,
                Err(_) => {
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
                        response_header_timeout_ms = response_header_timeout.as_millis(),
                        "provider downlink: response header timeout"
                    );
                    return Err(ServiceError::InternalError(format!(
                        "provider response header timeout after {}ms",
                        response_header_timeout.as_millis()
                    )));
                }
            }
        } else {
            send.await
        };
    let response = response_result.map_err(|error| {
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
    if status_policy == ProviderStatusPolicy::RequireSuccess
        && !status.is_success()
        && !(body.method == "chat.abort" && status == reqwest::StatusCode::GONE)
    {
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
    }).await
}

pub(super) fn blocked_outbound_ip(error: &OutboundUrlError) -> Option<IpAddr> {
    match error {
        OutboundUrlError::UnsafeAddress(address) => Some(*address),
        _ => None,
    }
}

pub(super) fn webhook_url_for_log(webhook_url: &str) -> String {
    let Ok(mut url) = reqwest::Url::parse(webhook_url) else {
        return "<invalid webhook URL>".to_string();
    };
    let _ = url.set_username("");
    let _ = url.set_password(None);
    url.set_query(None);
    url.set_fragment(None);
    url.to_string()
}

pub(super) fn provider_client_builder(policy: ProviderClientPolicy) -> reqwest::ClientBuilder {
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

pub(super) fn provider_client_for_url(
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

pub(super) fn provider_body_log(body: &ProviderWebhookRequest) -> String {
    let mut redacted = match serde_json::to_value(body) {
        Ok(value) => value,
        Err(error) => return format!("{{\"serialize_error\":\"{}\"}}", error),
    };
    if let Some(token) = redacted.pointer_mut("/extensions/caller_execution_token") {
        *token = serde_json::json!("<redacted>");
    }
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
    serde_json::to_string(&redacted)
        .unwrap_or_else(|error| format!("{{\"serialize_error\":\"{}\"}}", error))
}

pub(super) fn sse_data_log(_event: &str, data: &str) -> String {
    data.to_string()
}

pub(super) fn provider_history_log(response: &ProviderHistoryResponse) -> String {
    serde_json::to_string(response)
        .unwrap_or_else(|error| format!("{{\"serialize_error\":\"{}\"}}", error))
}


/// The client a guarded URL MUST be posted through: the guard validated ONE
/// resolution, so the request pins those addresses via
/// [`provider_client_for_url`] and connect-time DNS gets no second vote
/// (DNS-rebinding/TOCTOU). Without an override (loopback/test mode) the
/// shared client is reused unchanged. Every resolve_request_http_url
/// consumer — ordinary downlink, protected caller send, observe, ensure,
/// history — posts through this helper, never through the bare shared
/// client.
pub(super) fn request_client_for_guarded_url(
    shared: &reqwest::Client,
    guarded_url: &bcs_route_security::ValidatedRequestUrl,
    policy: ProviderClientPolicy,
) -> Result<reqwest::Client, reqwest::Error> {
    Ok(provider_client_for_url(guarded_url, policy)?
        .unwrap_or_else(|| shared.clone()))
}
