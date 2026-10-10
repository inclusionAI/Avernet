use super::*;

use super::interaction::send_interaction_event;
use super::replies::{send_error, send_ok};

#[derive(Debug, Deserialize)]
struct ConnectParams {
    group_id: String,
    #[serde(default, alias = "bcs_session_id", alias = "sessionId")]
    session_id: Option<String>,
    #[serde(default, alias = "viewActorId")]
    view_actor_id: Option<String>,
}

#[derive(Debug, Serialize)]
struct ConnectResponse {
    group_id: String,
    participants: Vec<Value>,
    view_actor_id: String,
    message_view_scope: bcs_domain::MessageViewScope,
}

pub(super) async fn handle_connect(
    state: &Arc<WebDispatchState>,
    req: &RequestFrame,
    tx: &mpsc::Sender<WorkbenchOutbound>,
    connection_state: &mut WebClientConnectionState,
    auth: &WorkbenchConnectionAuth,
) -> Result<()> {
    let bound_actor_id = auth.actor_id();
    let params: ConnectParams = serde_json::from_value(req.params.clone().unwrap_or(Value::Null))
        .map_err(|e| {
        WebWsDispatchError::InvalidFrameFormat(format!("Invalid connect params: {}", e))
    })?;

    if connection_state.phase == WebConnectionPhase::Connected
        && matches!(auth, WorkbenchConnectionAuth::SessionBound { .. })
    {
        send_error(
            tx,
            &req.id,
            "already_connected",
            "This WebSocket is already connected",
        )
        .await?;
        return Ok(());
    }

    let (group_id, session_id) = match auth {
        WorkbenchConnectionAuth::UserBound { .. } => {
            (params.group_id.clone(), params.session_id.clone())
        }
        WorkbenchConnectionAuth::SessionBound {
            group_id,
            session_id,
            ..
        } => {
            if params.group_id != *group_id || params.session_id.as_deref() != Some(session_id) {
                send_error(
                    tx,
                    &req.id,
                    "token_scope_mismatch",
                    "Connect scope does not match the connection token",
                )
                .await?;
                connection_state.phase = WebConnectionPhase::Closed;
                return Ok(());
            }
            (group_id.clone(), Some(session_id.clone()))
        }
    };

    debug!(
        group_id = %group_id,
        session_id = ?session_id,
        bound_actor_id = ?bound_actor_id,
        "Processing connect request"
    );

    let outcome = match auth {
        WorkbenchConnectionAuth::UserBound { .. } => match state
            .workbench_sessions
            .connect(WorkbenchConnectCommand {
                bound_actor_id: bound_actor_id.map(str::to_string),
                view_actor_id: params.view_actor_id.clone(),
                group_id: group_id.clone(),
                session_id: session_id.clone(),
            })
            .await
        {
            Ok(outcome) => outcome,
            Err(err) => {
                warn!(
                    request_id = %bcs_observability::CurrentRequestId,
                    group_id = %group_id,
                    session_id = ?session_id,
                    bound_actor_id = ?bound_actor_id,
                    error = ?err,
                    "connect rejected by Workbench WS authorization"
                );
                let message = err.message();
                send_error(tx, &req.id, err.code(), &message).await?;
                return Ok(());
            }
        },
        WorkbenchConnectionAuth::SessionBound {
            tenant,
            actor_id,
            group_id,
            session_id,
        } => {
            if params
                .view_actor_id
                .as_deref()
                .is_some_and(|view_actor_id| view_actor_id != actor_id)
            {
                send_error(
                    tx,
                    &req.id,
                    "forbidden_view_actor",
                    "Session-bound connections may only use the authenticated Human view",
                )
                .await?;
                return Ok(());
            }
            let user_id = actor_id
                .strip_prefix("human_")
                .filter(|user_id| !user_id.is_empty());
            let service = state.group_session_connections.as_ref();
            let authorized = match (service, user_id) {
                (Some(service), Some(user_id)) => {
                    service
                        .authorize_connect(AuthorizeGroupSessionConnection {
                            binding: GroupSessionConnectionBinding {
                                tenant: tenant.clone(),
                                user_id: user_id.to_string(),
                                group_id: group_id.clone(),
                                session_id: session_id.clone(),
                            },
                        })
                        .await
                }
                _ => {
                    warn!(request_id = %bcs_observability::CurrentRequestId, "session-bound connect is missing a valid V1 authorization context");
                    send_session_access_revoked(tx, &req.id, connection_state).await?;
                    return Ok(());
                }
            };
            match authorized {
                Ok(authorized) => WorkbenchConnectOutcome {
                    group_id: group_id.clone(),
                    participants: authorized
                        .participants
                        .into_iter()
                        .map(|participant| WorkbenchParticipantView {
                            bot_uuid: participant.actor_id,
                            role: participant_role_to_wire(participant.role).to_string(),
                            kind: ParticipantKind::Bot,
                            mode: Some(participant.mode),
                            message_view_scope: participant.message_view_scope,
                        })
                        .collect(),
                },
                Err(err) => {
                    warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        error = ?err,
                        "connect rejected by V1 group-session authorization"
                    );
                    send_session_access_revoked(tx, &req.id, connection_state).await?;
                    return Ok(());
                }
            }
        }
    };

    // The bound actor remains the registry identity for legacy clients, but it
    // must not implicitly opt those clients into participant projection.
    let explicit_view_actor_id = params.view_actor_id.as_deref();
    let resolved_view_actor_id = explicit_view_actor_id.or(bound_actor_id);
    let resolved_participant = match explicit_view_actor_id {
        Some(actor_id) => Some(
            outcome
                .participants
                .iter()
                .find(|participant| participant.bot_uuid == actor_id)
                .ok_or_else(|| {
                    WebWsDispatchError::InvalidFrameFormat(
                        "authorized view actor is not a participant".to_string(),
                    )
                })?,
        ),
        None => None,
    };
    let resolved_message_view_scope = resolved_participant
        .map(|participant| participant.message_view_scope)
        .unwrap_or_default();
    let connection_human_view = explicit_view_actor_id
        .filter(|actor_id| actor_id.starts_with("human_"))
        .and_then(|actor_id| {
            resolved_participant.map(|participant| HumanMessageView {
                actor_id: actor_id.to_string(),
                scope: participant.message_view_scope,
                allow_legacy_unclassified_chat: true,
            })
        });
    let subscription_key = session_id.clone().unwrap_or_else(|| group_id.clone());
    // Task 16: persist the REAL User / selected view / binding generation on
    // the subscription. The binding is built ONLY from verified identity
    // facts (the connection auth and the authorized view), never from client
    // request payloads, and never from the registry's legacy actor slot.
    // A cookie-bound connection only becomes a protected connection when the
    // client explicitly SELECTED a participant view; implicit legacy
    // unprojected connections stay on the PublicControl lane with the
    // pre-existing visibility behavior. Session-bound token connections
    // always bind their verified Human view.
    let protected_view_actor_id = match auth {
        WorkbenchConnectionAuth::UserBound { .. } => explicit_view_actor_id,
        WorkbenchConnectionAuth::SessionBound { .. } => resolved_view_actor_id,
    };
    let protected_binding = binding_from_auth(
        auth,
        protected_view_actor_id,
        if session_id.is_some() {
            bcs_service_api::application::v1::delivery_authorization::DeliveryResourceKind::Session
        } else {
            bcs_service_api::application::v1::delivery_authorization::DeliveryResourceKind::Group
        },
        &subscription_key,
        state.frontend_connections.trusted_env(),
    );
    let (conn_id, binding_id) = state
        .frontend_connections
        .subscribe_bound(
            subscription_key.clone(),
            tx.clone(),
            resolved_view_actor_id.map(str::to_string),
            connection_human_view.clone(),
            protected_binding.clone(),
            connection_state.shutdown.clone(),
        )
        .await?;
    connection_state.subscribed_sessions.push((
        subscription_key,
        conn_id,
        connection_human_view.clone(),
    ));
    connection_state.phase = WebConnectionPhase::Connected;

    let participants: Vec<Value> = outcome
        .participants
        .into_iter()
        .map(|participant| serde_json::to_value(participant).unwrap_or(Value::Null))
        .collect();

    let response = ConnectResponse {
        group_id: outcome.group_id,
        participants,
        view_actor_id: resolved_view_actor_id.unwrap_or_default().to_string(),
        message_view_scope: resolved_message_view_scope,
    };

    send_ok(tx, &req.id, serde_json::to_value(response)?).await?;
    if let Some(session_id) = session_id.as_deref() {
        match state.interactions.list_pending(session_id).await {
            Ok(pending) => match protected_binding {
                // Interaction replay is a NEW protected dispatch (Task 16):
                // each replayed frame re-authorizes at enqueue (position 1)
                // and again before the actual send (position 2).
                Some(binding) => {
                    for event in pending {
                        let payload = interaction_event_json(&event)?;
                        enqueue_single_protected(
                            &state.frontend_connections.protected_delivery(),
                            tx,
                            &binding,
                            binding_id,
                            payload,
                            bcs_service_api::application::v1::delivery_authorization::DeliveryAction::ReplayFrame,
                            bcs_domain::MessageVisibilityDomain::StateMachine,
                            Some(&bcs_domain::MessageAudience::FullOnly),
                        )
                        .await;
                    }
                }
                None => {
                    let pending_visible = connection_human_view.as_ref().is_none_or(|view| {
                        view.allows_artifact(
                            bcs_domain::MessageVisibilityDomain::StateMachine,
                            Some(&bcs_domain::MessageAudience::FullOnly),
                        )
                    });
                    if pending_visible {
                        for event in pending {
                            send_interaction_event(tx, &event).await?;
                        }
                    }
                }
            },
            Err(error) => {
                warn!(request_id = %bcs_observability::CurrentRequestId, session_id, %error, "pending interaction replay failed after connect");
            }
        }
    }
    Ok(())
}

/// Build the protected-delivery binding from VERIFIED connection identity
/// facts. `None` keeps the connection on the legacy PublicControl lane:
/// anonymous connections (no real User) and token sessions that failed to
/// resolve their User never get a binding.
fn binding_from_auth(
    auth: &WorkbenchConnectionAuth,
    resolved_view_actor_id: Option<&str>,
    resource_kind: bcs_service_api::application::v1::delivery_authorization::DeliveryResourceKind,
    resource_id: &str,
    env: &str,
) -> Option<ProtectedDeliveryBinding> {
    let (tenant, user_id) = match auth {
        WorkbenchConnectionAuth::UserBound { actor_id } => {
            (None, actor_id.as_deref()?.strip_prefix("human_")?)
        }
        WorkbenchConnectionAuth::SessionBound {
            tenant,
            actor_id,
            ..
        } => (tenant.clone(), actor_id.strip_prefix("human_")?),
    };
    let user_id = user_id;
    if user_id.is_empty() {
        return None;
    }
    let view_actor_id = resolved_view_actor_id?;
    Some(ProtectedDeliveryBinding {
        tenant,
        env: env.to_string(),
        user_id: user_id.to_string(),
        resource_kind,
        resource_id: resource_id.to_string(),
        view_actor_id: view_actor_id.to_string(),
    })
}

async fn send_session_access_revoked(
    tx: &mpsc::Sender<WorkbenchOutbound>,
    request_id: &str,
    connection_state: &mut WebClientConnectionState,
) -> Result<()> {
    send_error(
        tx,
        request_id,
        "session_access_revoked",
        "Session access is no longer authorized",
    )
    .await?;
    connection_state.phase = WebConnectionPhase::Closed;
    Ok(())
}

fn participant_role_to_wire(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}