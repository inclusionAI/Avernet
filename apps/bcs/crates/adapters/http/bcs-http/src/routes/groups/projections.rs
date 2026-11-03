//! Legacy JSON response projections for Group routes.

use super::*;

pub(crate) fn build_group_chat_url(
    base: &str,
    group_id: &str,
    view_actor_id: &str,
    session_id: Option<&str>,
) -> String {
    let mut url = format!(
        "{}/bcn/chat/detail?id={}&bot_uuid={}",
        base.trim_end_matches('/'),
        urlencoding::encode(group_id),
        urlencoding::encode(view_actor_id),
    );
    if let Some(session_id) = session_id.filter(|value| !value.is_empty()) {
        url.push_str(&format!("&session={}", urlencoding::encode(session_id)));
    }
    url
}

pub(crate) fn group_detail_to_create_json(mut result: GroupDetailResult, created: bool) -> Value {
    let opening_message = result.opening_message.take();
    let initial_session_id = result.latest_running_session_id.take();
    let mut response = serde_json::json!({
        "id": result.group_id,
        "context": result.context,
        "driver_bot": result.driver_bot_id,
        "participants": result.participants.iter().map(|p| &p.bot_uuid).collect::<Vec<_>>(),
        "context_injected": result.context_injected,
        "chat_url": result.chat_url,
        "session_id": initial_session_id.clone(),
        "initial_session_id": initial_session_id,
        "initial_run": result.initial_run,
        "group_kind": result.group_kind,
        "dm_pair_key": result.dm_pair_key,
        "human_mention_notify_mode": result.human_mention_notify_mode,
        "created": created
    });
    insert_opening_message(&mut response, opening_message);
    response
}

pub(crate) fn insert_opening_message(
    response: &mut Value,
    opening_message: Option<bcs_service_api::types::OpeningMessage>,
) {
    if let Some(opening_message) = opening_message {
        response
            .as_object_mut()
            .expect("Group response must be a JSON object")
            .insert(
                "opening_message".to_string(),
                serde_json::json!(opening_message),
            );
    }
}


pub(crate) fn group_list_entry_to_legacy_json(group: GroupListEntry) -> Value {
    let driver_bot_name = if group.driver_bot_id.is_empty() {
        None
    } else {
        group
            .participants
            .iter()
            .find(|p| p.bot_uuid == group.driver_bot_id)
            .and_then(|p| p.bot_name.clone())
    };
    let originator_name = group
        .originator
        .as_ref()
        .filter(|id| !id.is_empty())
        .and_then(|id| {
            group
                .participants
                .iter()
                .find(|p| p.bot_uuid == *id)
                .and_then(|p| p.bot_name.clone())
        });

    serde_json::json!({
        "id": group.group_id,
        "label": group.label,
        "context": group.context,
        "driver_bot": group.driver_bot_id,
        "driver_bot_name": driver_bot_name,
        "originator": group.originator,
        "originator_name": originator_name,
        "participant_count": group.participant_count,
        "message_count": group.message_count,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "group_kind": group.group_kind,
        "group_strategy": group.group_strategy,
        "visibility": group.visibility,
        "human_mention_notify_mode": group.human_mention_notify_mode,
    })
}

pub(crate) fn bot_group_list_entry_to_legacy_json(group: GroupListEntry) -> Value {
    serde_json::json!({
        "group_id": group.group_id,
        "label": group.label,
        "coordinator_bot": group.driver_bot_id,
        "participants": group.participants,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "group_kind": group.group_kind,
        "group_strategy": group.group_strategy,
        "visibility": group.visibility,
        "human_mention_notify_mode": group.human_mention_notify_mode,
    })
}

pub(crate) fn group_to_detail_json(mut group: GroupDetailResult) -> Value {
    let opening_message = group.opening_message.take();
    let mut response = serde_json::json!({
        "id": group.group_id,
        "label": group.label,
        "status": group.status,
        "context": group.context,
        "driver_bot": group.driver_bot_id,
        "participants": group.participants,
        "message_count": group.message_count,
        "service_group_uuid": group.service_group_uuid,
        "service_mode": group.service_mode,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
        "group_kind": group.group_kind,
        "dm_pair_key": group.dm_pair_key,
        "group_strategy": group.group_strategy,
        "latest_running_session_id": group.latest_running_session_id,
        "originator": group.originator,
        "visibility": group.visibility,
        "human_mention_notify_mode": group.human_mention_notify_mode,
    });
    insert_opening_message(&mut response, opening_message);
    response
}

/// Legacy participant row of the PATCH surface: exactly the pre-avatar field
/// set of the shared V1 `Participant`. Serializing through this dedicated
/// DTO — instead of the V1 type — keeps any future V1 participant field
/// (like `avatar_url`) out of the legacy wire shape (bot-avatar plan
/// design §5.1: 旧版 DTO 不随跨版本共享序列化类型扩展).
#[derive(Serialize)]
struct LegacyGroupParticipant {
    actor_id: String,
    actor_kind: bcs_domain::ActorKind,
    #[serde(skip_serializing_if = "Option::is_none")]
    name: Option<String>,
    role: V1ParticipantRole,
    mode: ParticipantMode,
    message_view_scope: MessageViewScope,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    tags: Vec<String>,
}

impl LegacyGroupParticipant {
    fn from_v1(participant: &bcs_service_api::application::v1::Participant) -> Self {
        Self {
            actor_id: participant.actor_id.clone(),
            actor_kind: participant.actor_kind,
            name: participant.name.clone(),
            role: participant.role,
            mode: participant.mode,
            message_view_scope: participant.message_view_scope,
            tags: participant.tags.clone(),
        }
    }
}

/// Map the V1 GroupDetail to the legacy PATCH response: the response keeps
/// the shape this route has serialized since the group-application refactor
/// (kind tag, `group_id`, …) while the participant rows go through the
/// legacy DTO above (bot-avatar plan Task 8, design §5.1).
pub(crate) fn v1_group_detail_to_legacy_update_json(group: V1GroupDetail) -> Value {
    let participants = match &group {
        V1GroupDetail::Collaboration(detail) => detail.participants.as_slice(),
        V1GroupDetail::DirectMessage(detail) => detail.participants.as_slice(),
    };
    let legacy_rows: Vec<Value> = participants
        .iter()
        .map(|participant| {
            serde_json::to_value(LegacyGroupParticipant::from_v1(participant))
                .expect("legacy participant row serializes")
        })
        .collect();
    let mut value = serde_json::to_value(group).expect("V1 Group detail serializes");
    if let Some(object) = value.as_object_mut() {
        object.insert("participants".to_string(), Value::Array(legacy_rows));
    }
    value
}

pub(crate) async fn resolve_driver_bot_owner(
    state: &HttpAppState,
    driver_bot_id: &str,
) -> (Option<String>, Option<String>) {
    let driver = match state
        .services
        .bot_query
        .get_bot(BotDetailCommand {
            caller_actor_id: None,
            bot_id: driver_bot_id.to_string(),
        })
        .await
    {
        Ok(bot) => bot,
        Err(_) => return (None, None),
    };
    let staff_no = match driver.created_by {
        Some(s) if !s.is_empty() => s,
        _ => return (None, None),
    };
    let human_id = format!("human_{}", staff_no);
    let owner_name = state
        .services
        .bot_query
        .get_bot(BotDetailCommand {
            caller_actor_id: None,
            bot_id: human_id.clone(),
        })
        .await
        .ok()
        .and_then(|h| h.capabilities.name);
    (Some(human_id), owner_name)
}

pub(crate) fn group_status_to_wire(status: GroupStatus) -> &'static str {
    match status {
        GroupStatus::Active => "active",
        GroupStatus::Completed => "completed",
        GroupStatus::Error => "error",
        GroupStatus::Closed => "closed",
        GroupStatus::Inactive => "inactive",
    }
}
