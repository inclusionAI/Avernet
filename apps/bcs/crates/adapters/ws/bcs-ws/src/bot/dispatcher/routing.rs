//! route.resolve request handling and route selector resolution.

use std::sync::Arc;

use bcs_protocol::RouteSelectorWire;
use bcs_service_api::{Participant, ParticipantRole};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::mpsc;

use super::{BotDispatchState, unwrap_outbound_group_id};
use super::error::{BotWsDispatchError, Result, map_session_error, send_error, send_ok};

#[derive(Debug, Clone, Deserialize)]
struct RouteResolveParams {
    group_id: String,
    #[serde(default)]
    session_id: Option<String>,
    selectors: Vec<RouteSelectorWire>,
}

#[derive(Debug, Clone, Serialize)]
pub(super) struct RouteResolveTarget {
    #[serde(rename = "type")]
    pub(super) selector_type: String,
    pub(super) value: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) bot_name: Option<String>,
    pub(super) role: String,
}

#[derive(Debug, Clone, Serialize)]
pub(super) struct RouteResolveCandidate {
    pub(super) bot_uuid: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(super) bot_name: Option<String>,
    pub(super) role: String,
}

#[derive(Debug, Clone, Serialize)]
pub(super) struct RouteResolveResult {
    pub(super) ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(super) message: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub(super) resolved: Vec<RouteResolveTarget>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub(super) candidates: Vec<RouteResolveCandidate>,
}

pub(super) fn resolve_route_selectors(
    participants: &[Participant],
    selectors: &[RouteSelectorWire],
) -> RouteResolveResult {
    if selectors.is_empty() {
        return RouteResolveResult {
            ok: false,
            error: Some("INVALID_ROUTE_SELECTOR".to_string()),
            message: Some("'selectors' must be a non-empty array".to_string()),
            resolved: Vec::new(),
            candidates: route_candidates(participants, None),
        };
    }

    let mut resolved = Vec::new();
    for selector in selectors {
        let value = selector.value.as_deref().unwrap_or_default().trim();
        match selector.selector_type.as_str() {
            "bot" => {
                if value.is_empty() {
                    return invalid_route_selector(
                        participants,
                        "'bot' selector requires a non-empty value",
                    );
                }
                match participants
                    .iter()
                    .find(|participant| participant.is_bot() && participant.bot_uuid == value)
                {
                    Some(participant) => push_resolved_target(&mut resolved, participant),
                    None => {
                        return unknown_route_target(
                            participants,
                            value,
                            format!("No participant matched bot \"{value}\""),
                        );
                    }
                }
            }
            "name" => {
                if value.is_empty() {
                    return invalid_route_selector(
                        participants,
                        "'name' selector requires a non-empty value",
                    );
                }
                let matches: Vec<_> = participants
                    .iter()
                    .filter(|participant| {
                        participant.is_bot()
                            && participant
                                .bot_name
                                .as_deref()
                                .map_or(false, |name| name == value)
                    })
                    .collect();
                match matches.as_slice() {
                    [participant] => push_resolved_target(&mut resolved, participant),
                    [] => {
                        return unknown_route_target(
                            participants,
                            value,
                            format!("No participant matched name \"{value}\""),
                        );
                    }
                    _ => {
                        return RouteResolveResult {
                            ok: false,
                            error: Some("AMBIGUOUS_ROUTE_TARGET".to_string()),
                            message: Some(format!(
                                "Multiple participants matched name \"{value}\""
                            )),
                            resolved: Vec::new(),
                            candidates: matches.into_iter().map(route_candidate).collect(),
                        };
                    }
                }
            }
            other => {
                return invalid_route_selector(
                    participants,
                    &format!("unsupported route selector type \"{other}\""),
                );
            }
        }
    }

    RouteResolveResult {
        ok: true,
        error: None,
        message: None,
        resolved,
        candidates: Vec::new(),
    }
}

fn push_resolved_target(resolved: &mut Vec<RouteResolveTarget>, participant: &Participant) {
    if resolved
        .iter()
        .any(|target| target.value == participant.bot_uuid)
    {
        return;
    }
    resolved.push(RouteResolveTarget {
        selector_type: "bot".to_string(),
        value: participant.bot_uuid.clone(),
        bot_name: participant.bot_name.clone(),
        role: participant_role_slug(participant.role).to_string(),
    });
}

fn invalid_route_selector(
    participants: &[Participant],
    message: impl Into<String>,
) -> RouteResolveResult {
    RouteResolveResult {
        ok: false,
        error: Some("INVALID_ROUTE_SELECTOR".to_string()),
        message: Some(message.into()),
        resolved: Vec::new(),
        candidates: route_candidates(participants, None),
    }
}

fn unknown_route_target(
    participants: &[Participant],
    value: &str,
    message: String,
) -> RouteResolveResult {
    RouteResolveResult {
        ok: false,
        error: Some("UNKNOWN_ROUTE_TARGET".to_string()),
        message: Some(message),
        resolved: Vec::new(),
        candidates: route_candidates(participants, Some(value)),
    }
}

fn route_candidates(
    participants: &[Participant],
    query: Option<&str>,
) -> Vec<RouteResolveCandidate> {
    let query = query.map(|value| value.to_lowercase());
    let mut candidates: Vec<_> = participants
        .iter()
        .filter(|participant| participant.is_bot())
        .filter(|participant| match query.as_deref() {
            Some(query) if !query.is_empty() => {
                participant.bot_uuid.to_lowercase().contains(query)
                    || participant
                        .bot_name
                        .as_deref()
                        .map(|name| {
                            name.to_lowercase().contains(query)
                                || query.contains(&name.to_lowercase())
                        })
                        .unwrap_or(false)
            }
            _ => true,
        })
        .map(route_candidate)
        .collect();
    if candidates.is_empty() && query.is_some() {
        candidates = participants
            .iter()
            .filter(|participant| participant.is_bot())
            .map(route_candidate)
            .collect();
    }
    candidates
}

fn route_candidate(participant: &Participant) -> RouteResolveCandidate {
    RouteResolveCandidate {
        bot_uuid: participant.bot_uuid.clone(),
        bot_name: participant.bot_name.clone(),
        role: participant_role_slug(participant.role).to_string(),
    }
}

fn participant_role_slug(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}

pub(super) async fn handle_route_resolve(
    state: &Arc<BotDispatchState>,
    req_id: &str,
    params: Value,
    tx: &mpsc::Sender<String>,
    registered_bot_id: &Option<String>,
) -> Result<()> {
    if registered_bot_id.is_none() {
        send_error(
            tx,
            req_id,
            "not_registered",
            "Bot must register before resolving route targets",
        )
        .await?;
        return Ok(());
    }

    let params: RouteResolveParams = serde_json::from_value(params).map_err(|e| {
        BotWsDispatchError::InvalidFrameFormat(format!("Invalid route.resolve params: {}", e))
    })?;
    let (real_group_id, wire_session_id) =
        unwrap_outbound_group_id(&params.group_id, params.session_id.as_deref());
    let mut participants = match state.group_dispatch.participants(&real_group_id).await {
        Some(participants) => participants,
        None => {
            send_error(
                tx,
                req_id,
                "group_not_found",
                &format!("Group not found: {real_group_id}"),
            )
            .await?;
            return Ok(());
        }
    };

    if let Some(session_id) = wire_session_id {
        match state.session_management.get(&session_id).await {
            Ok(Some(session)) => {
                if session.group_id != real_group_id {
                    send_error(
                        tx,
                        req_id,
                        "invalid_session",
                        &format!("Session {session_id} does not belong to group {real_group_id}"),
                    )
                    .await?;
                    return Ok(());
                }
                if !session.participants.is_empty() {
                    participants = session.participants;
                }
            }
            Ok(None) => {
                send_error(
                    tx,
                    req_id,
                    "session_not_found",
                    &format!("Session not found: {session_id}"),
                )
                .await?;
                return Ok(());
            }
            Err(error) => {
                let (code, message) = map_session_error(error);
                send_error(tx, req_id, &code, &message).await?;
                return Ok(());
            }
        }
    }

    send_ok(
        tx,
        req_id,
        serde_json::to_value(resolve_route_selectors(&participants, &params.selectors))?,
    )
    .await
}
