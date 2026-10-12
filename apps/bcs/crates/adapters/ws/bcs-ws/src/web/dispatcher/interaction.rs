use super::*;

use super::replies::{send_error, send_error_shape, send_ok};

#[derive(Debug, Deserialize)]
struct InteractionResolveParams {
    #[serde(rename = "bcsRunId", alias = "bcs_run_id")]
    bcs_run_id: String,
    #[serde(rename = "interactionId", alias = "interaction_id")]
    interaction_id: String,
    #[serde(rename = "idempotencyKey", alias = "idempotency_key")]
    idempotency_key: String,
    #[serde(
        default,
        rename = "bcsSessionId",
        alias = "bcs_session_id",
        alias = "sessionId"
    )]
    bcs_session_id: Option<String>,
    #[serde(default, rename = "groupId", alias = "group_id")]
    group_id: Option<String>,
    #[serde(default)]
    kind: Option<String>,
    #[serde(flatten)]
    resolution: HashMap<String, Value>,
}

pub(super) async fn handle_interaction_resolve(
    state: &Arc<WebDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<WorkbenchOutbound>,
    connection_state: &mut WebClientConnectionState,
    auth: &WorkbenchConnectionAuth,
) -> Result<()> {
    let params: InteractionResolveParams =
        match serde_json::from_value(req.params.clone().unwrap_or(Value::Null)) {
            Ok(params) => params,
            Err(error) => {
                send_error(
                    tx,
                    &req.id,
                    "invalid_request",
                    &format!("Invalid interaction.resolve params: {error}"),
                )
                .await?;
                return Ok(());
            }
        };
    let Some(resolver_actor_id) = auth.actor_id().map(str::to_string) else {
        send_error(
            tx,
            &req.id,
            "unauthorized",
            "An authenticated Human is required to resolve an interaction",
        )
        .await?;
        return Ok(());
    };
    let (expected_bcs_session_id, expected_group_id) = match auth {
        WorkbenchConnectionAuth::SessionBound {
            group_id,
            session_id,
            ..
        } => (Some(session_id.clone()), Some(group_id.clone())),
        WorkbenchConnectionAuth::UserBound { .. } => (None, None),
    };

    if let WorkbenchConnectionAuth::SessionBound {
        group_id,
        session_id,
        ..
    } = auth
    {
        let mismatched = params
            .group_id
            .as_deref()
            .is_some_and(|provided| provided != group_id)
            || params
                .bcs_session_id
                .as_deref()
                .is_some_and(|provided| provided != session_id);
        if mismatched {
            send_error(
                tx,
                &req.id,
                "token_scope_mismatch",
                "Request scope does not match the connection token",
            )
            .await?;
            connection_state.phase = WebConnectionPhase::Closed;
            return Ok(());
        }
    }

    // `kind` is presentation context only. The Application service loads the
    // authoritative kind and Provider route from its server-owned record.
    let _ = params.kind;
    match state
        .interactions
        .resolve(ResolveInteractionCommand {
            bcs_run_id: params.bcs_run_id,
            interaction_id: params.interaction_id.clone(),
            idempotency_key: params.idempotency_key,
            resolver_actor_id,
            expected_bcs_session_id,
            expected_group_id,
            resolution: Value::Object(params.resolution.into_iter().collect()),
        })
        .await
    {
        Ok(result) => {
            send_ok(
                tx,
                &req.id,
                serde_json::json!({
                    "accepted": result.accepted,
                    "interactionId": result.interaction_id,
                    "interactionStatus": interaction_status_slug(result.status),
                    "idempotencyKey": result.idempotency_key,
                }),
            )
            .await?;
        }
        Err(InteractionServiceError::InvalidRequest(message)) => {
            send_error(tx, &req.id, "invalid_request", &message).await?;
        }
        Err(InteractionServiceError::Unauthorized) => {
            send_error(
                tx,
                &req.id,
                "unauthorized",
                "The current Human cannot resolve this interaction",
            )
            .await?;
        }
        Err(InteractionServiceError::NotFound) => {
            send_error(
                tx,
                &req.id,
                "not_found",
                "The interaction does not exist or is no longer retained",
            )
            .await?;
        }
        Err(InteractionServiceError::ResolveFailed {
            message,
            retryable,
            status,
        }) => {
            send_error_shape(
                tx,
                &req.id,
                ErrorShape {
                    code: "interaction_resolve_failed".to_string(),
                    message,
                    details: Some(serde_json::json!({
                        "interactionId": params.interaction_id,
                        "interactionStatus": interaction_status_slug(status),
                    })),
                    retryable,
                    retry_after_ms: None,
                },
            )
            .await?;
        }
        Err(InteractionServiceError::Internal(message)) => {
            warn!(request_id = %bcs_observability::CurrentRequestId, %message, "interaction resolve application service failed");
            send_error_shape(
                tx,
                &req.id,
                ErrorShape {
                    code: "interaction_resolve_failed".to_string(),
                    message: "Interaction resolution could not be processed".to_string(),
                    details: Some(serde_json::json!({
                        "interactionId": params.interaction_id,
                        "interactionStatus": "pending",
                    })),
                    retryable: true,
                    retry_after_ms: None,
                },
            )
            .await?;
        }
    }
    Ok(())
}

fn interaction_status_slug(status: InteractionStatus) -> &'static str {
    match status {
        InteractionStatus::Pending => "pending",
        InteractionStatus::Accepted => "accepted",
        InteractionStatus::Resolved => "resolved",
        InteractionStatus::Invalidated => "invalidated",
    }
}

pub(super) async fn send_interaction_event(
    tx: &mpsc::Sender<WorkbenchOutbound>,
    event: &InteractionFrontendEvent,
) -> Result<()> {
    let json = interaction_event_json(event)?;
    tx.send(WorkbenchOutbound::PublicControl(json))
        .await
        .map_err(|error| {
            WebWsDispatchError::WsProtocolError(format!("Failed to replay interaction event: {error}"))
        })?;
    Ok(())
}