//! Legacy create-response serialization and initial state-machine run start.

use super::*;

pub(crate) fn v1_group_id(group: &V1GroupDetail) -> &str {
    match group {
        V1GroupDetail::Collaboration(group) => &group.group_id,
        V1GroupDetail::DirectMessage(group) => &group.group_id,
    }
}

pub(crate) fn v1_group_detail_to_legacy_create_json(
    group: V1GroupDetail,
    created: bool,
    event_subscriptions: Vec<bcs_service_api::application::v1::EventSubscription>,
    session_id: Option<String>,
    botchat_url: Option<&str>,
) -> Value {
    match group {
        V1GroupDetail::Collaboration(mut group) => {
            let opening_message = group.opening_message.take();
            let chat_url = botchat_url.map(|base| {
                build_group_chat_url(
                    base,
                    &group.group_id,
                    &group.driver_bot_uuid,
                    session_id.as_deref(),
                )
            });
            let mut response = serde_json::json!({
                "id": group.group_id,
                "context": group.context,
                "driver_bot": group.driver_bot_uuid,
                "participants": group.participants.into_iter().map(|p| p.actor_id).collect::<Vec<_>>(),
                "context_injected": false,
                "chat_url": chat_url,
                "session_id": session_id,
                "group_kind": "normal",
                "dm_pair_key": Value::Null,
                "human_mention_notify_mode": group.human_mention_notify_mode,
                "created": created,
                "event_subscriptions": event_subscriptions,
            });
            insert_opening_message(&mut response, opening_message);
            response
        }
        V1GroupDetail::DirectMessage(group) => {
            let driver_bot = group
                .participants
                .iter()
                .find(|participant| participant.actor_kind == bcs_domain::ActorKind::Bot)
                .map(|participant| participant.actor_id.clone())
                .unwrap_or_default();
            let chat_url = botchat_url
                .filter(|_| !driver_bot.is_empty())
                .map(|base| {
                    build_group_chat_url(
                        base,
                        &group.group_id,
                        &driver_bot,
                        session_id.as_deref(),
                    )
                });
            serde_json::json!({
                "id": group.group_id,
                "context": group.context,
                "driver_bot": driver_bot,
                "participants": group.participants.into_iter().map(|p| p.actor_id).collect::<Vec<_>>(),
                "context_injected": false,
                "chat_url": chat_url,
                "session_id": session_id,
                "group_kind": "dm",
                "dm_pair_key": Value::Null,
                "human_mention_notify_mode": group.human_mention_notify_mode,
                "created": created,
                "event_subscriptions": event_subscriptions,
            })
        }
    }
}

pub(crate) async fn start_initial_state_machine_run_for_group(
    state: &HttpAppState,
    group: &GroupDetailResult,
    caller_id: Option<String>,
    authenticated_human: Option<bcs_service_api::AuthenticatedHumanCaller>,
) -> Result<Option<String>, HttpAdapterError> {
    let Some(session_id) = group.latest_running_session_id.as_deref() else {
        return Ok(None);
    };
    let session = state
        .services
        .session_management
        .get(session_id)
        .await
        .map_err(|error| {
            HttpAdapterError::Service(ServiceError::InternalError(error.to_string()))
        })?;
    let Some(session) = session else {
        tracing::warn!(
            request_id = %bcs_observability::CurrentRequestId,
            group_id = %group.group_id,
            session_id = %session_id,
            "default state-machine session not found after group creation"
        );
        return Ok(None);
    };
    if session.session_kind != SessionKind::ServiceInvocation {
        return Ok(None);
    }
    let run = state
        .services
        .collaboration_runtime
        .start_state_machine_run(StartStateMachineRunCommand {
            group_id: group.group_id.clone(),
            session_id: Some(session.id.clone()),
            definition_yaml: None,
            definition: None,
            definition_ref: None,
            participant_bindings: None,
            opening_message_override: None,
            input: session.input.clone().unwrap_or(Value::Null),
            caller_id,
            authenticated_human,
        })
        .await
        .map_err(collaboration_runtime_error_to_http)?;
    Ok(Some(run.view.run.run_id))
}
