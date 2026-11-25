use super::*;
use bcs_service_api::application::agent_invoke::*;
use dto::Params;
use serde::de::DeserializeOwned;
fn key(headers: &axum::http::HeaderMap) -> AgentInvokeResult<String> {
    let mut values = headers.get_all("idempotency-key").iter();
    let value = values
        .next()
        .and_then(|v| v.to_str().ok())
        .ok_or(AgentInvokeError::InvalidInput)?;
    if values.next().is_some()
        || value.is_empty()
        || value.len() > 128
        || !value.bytes().all(|c| c.is_ascii_graphic())
    {
        return Err(AgentInvokeError::InvalidInput);
    }
    Ok(value.to_owned())
}
async fn body<T: DeserializeOwned>(req: Request) -> AgentInvokeResult<T> {
    let content = req.headers().get_all("content-type");
    if content.iter().count() != 1
        || !content
            .iter()
            .next()
            .and_then(|h| h.to_str().ok())
            .is_some_and(|h| {
                h.split(';')
                    .next()
                    .is_some_and(|h| h.trim().eq_ignore_ascii_case("application/json"))
            })
    {
        return Err(AgentInvokeError::InvalidInput);
    }
    let bytes = axum::body::to_bytes(req.into_body(), 16 * 1024)
        .await
        .map_err(|_| AgentInvokeError::InvalidInput)?;
    serde_json::from_slice(&bytes).map_err(|_| AgentInvokeError::InvalidInput)
}
pub(super) async fn handle(
    s: &AgentInvokeHttpServices,
    principal: AgentInvokePrincipal,
    route: &str,
    path: &BTreeMap<String, String>,
    p: Params,
    req: Request,
    id: &RequestId,
) -> AgentInvokeResult<Response> {
    let idempotency_key = key(req.headers())?;
    let (pair, change, reason, status) = if route.ends_with("/grants") {
        dto::allowed(&p, &[])?;
        let b: dto::GrantBody = body(req).await?;
        (
            AgentInvokePair {
                from_bot_id: b.from_bot_id,
                to_bot_id: b.to_bot_id,
            },
            AgentInvokeChange::Grant {
                expected_version: b.expected_version,
            },
            b.reason,
            StatusCode::CREATED,
        )
    } else if route.ends_with("/requests") {
        dto::allowed(&p, &[])?;
        let b: dto::RequestBody = body(req).await?;
        (
            AgentInvokePair {
                from_bot_id: b.from_bot_id,
                to_bot_id: b.to_bot_id,
            },
            AgentInvokeChange::Request,
            b.reason,
            StatusCode::CREATED,
        )
    } else {
        dto::allowed(&p, &["bot_id", "peer_bot_id", "direction"])?;
        let b: dto::VersionBody = body(req).await?;
        let pair = if matches!(principal, AgentInvokePrincipal::Governance(_)) {
            // A delegated action does not imply Query delegation. Governance
            // must supply its exact pair, then command Core/store checks the
            // action, persisted resource ID and pair atomically; no graph read.
            let target = dto::target(&p)?;
            let peer = target.peer_bot_id.ok_or(AgentInvokeError::Forbidden)?;
            match dto::direction(&p)? {
                AgentInvokeDirection::Outbound => AgentInvokePair {
                    from_bot_id: target.bot_id,
                    to_bot_id: peer,
                },
                AgentInvokeDirection::Inbound => AgentInvokePair {
                    from_bot_id: peer,
                    to_bot_id: target.bot_id,
                },
            }
        } else {
            // Human lookup is independently authorized on the controlled
            // anchor. Never discover a pair from an unscoped ID repository.
            let r = reads::exact(s, principal.clone(), path, &p).await?;
            match r.data {
                AgentInvokeQueryData::Grant(g) => AgentInvokePair {
                    from_bot_id: g.from.bot_id,
                    to_bot_id: g.to.bot_id,
                },
                AgentInvokeQueryData::Request(r) => AgentInvokePair {
                    from_bot_id: r.from.bot_id,
                    to_bot_id: r.to.bot_id,
                },
                _ => return Err(AgentInvokeError::InvalidState),
            }
        };
        let request_id = path.get("request_id").cloned().unwrap_or_default();
        let change = match route.rsplit('/').next() {
            Some("approve") => AgentInvokeChange::Approve {
                request_id,
                expected_version: b.expected_version,
            },
            Some("reject") => AgentInvokeChange::Reject {
                request_id,
                expected_version: b.expected_version,
            },
            Some("cancel") => AgentInvokeChange::Cancel {
                request_id,
                expected_version: b.expected_version,
            },
            Some("revoke") => AgentInvokeChange::Revoke {
                edge_id: dto::positive(path.get("edge_id").ok_or(AgentInvokeError::InvalidInput)?)?,
                expected_version: b.expected_version,
            },
            _ => return Err(AgentInvokeError::InvalidInput),
        };
        (pair, change, b.reason, StatusCode::OK)
    };
    let receipt = s
        .management
        .change(AgentInvokeCommand {
            principal,
            authenticated_scope: s.scope.clone(),
            pair: pair.clone(),
            change,
            idempotency_key,
            reason,
        })
        .await?;
    // Immutable receipt and current references stay distinct. No follow-up
    // query may turn an already committed write into a partial read failure.
    let mut response = success(
        id,
        status,
        serde_json::json!({"action_id":receipt.action_id,"replayed":receipt.replayed,"from_bot_id":pair.from_bot_id,"to_bot_id":pair.to_bot_id,
        "original":{"grant":receipt.original_grant.as_ref().map(dto::grant_view),"request":receipt.original_request.as_ref().map(dto::request_view)},
        "current":{"grant":receipt.current_grant.as_ref().map(dto::grant_view),"request":receipt.current_request.as_ref().map(dto::request_view)}}),
    );
    if status == StatusCode::CREATED {
        let location = if route.ends_with("/requests") {
            receipt.original_request.as_ref().map(|r| {
                format!(
                    "{route}/{}?bot_id={}&direction=outbound",
                    urlencoding::encode(&r.request_id),
                    urlencoding::encode(&pair.from_bot_id)
                )
            })
        } else {
            receipt.original_grant.as_ref().map(|g| {
                format!(
                    "{route}/{}?bot_id={}&direction=inbound",
                    g.edge_id,
                    urlencoding::encode(&pair.to_bot_id)
                )
            })
        };
        let value = location
            .and_then(|value| HeaderValue::from_str(&value).ok())
            .ok_or(AgentInvokeError::CommitUnknown)?;
        response.headers_mut().insert("location", value);
    }
    Ok(response)
}
