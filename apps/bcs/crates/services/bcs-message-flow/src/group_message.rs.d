use bcs_domain::{DeliveryType, Group, Participant, ParticipantRole};
use bcs_protocol::{
    DirectiveAction, GroupContext, RequestSource, ResponseDirective, ResponseMode,
};

fn display_participant(participant: &Participant) -> String {
    display_bot(&participant.bot_uuid, participant.bot_name.as_deref())
}

fn display_bot(bot_uuid: &str, bot_name: Option<&str>) -> String {
    match bot_name {
        Some(name) if !name.is_empty() && name != bot_uuid => {
            format!("{}({})", name, bot_uuid)
        }
        _ => bot_uuid.to_string(),
    }
}

fn role_slug(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Observer => "observer",
    }
}

fn delivery_slug(delivery_type: DeliveryType) -> &'static str {
    match delivery_type {
        DeliveryType::Send => "send",
        DeliveryType::Inject => "inject",
    }
}

pub(crate) fn response_directive_for_delivery(
    delivery_type: DeliveryType,
    request_source: RequestSource,
) -> ResponseDirective {
    let action = match delivery_type {
        DeliveryType::Send => DirectiveAction::Respond,
        DeliveryType::Inject => DirectiveAction::Observe,
    };

    ResponseDirective {
        action,
        mode: if action == DirectiveAction::Respond {
            Some(ResponseMode::Required)
        } else {
            None
        },
        reason: None,
        request_source,
        matched_by: None,
    }
}

pub(crate) fn build_recipient_group_context(
    group: &Group,
    target_bot: &str,
    from: &str,
    message: &str,
    mentions: &[String],
    delivery_type: DeliveryType,
    response_directive: Option<ResponseDirective>,
    routing_mode: Option<String>,
    group_type: Option<String>,
    from_bot_owner: Option<String>,
) -> GroupContext {
    let target = group.participants.iter().find(|p| p.bot_uuid == target_bot);
    let recipient_name = target.and_then(|p| p.bot_name.clone());
    let recipient_role = target.map(|p| role_slug(p.role).to_string()).or_else(|| {
        if target_bot == group.driver_bot {
            Some(role_slug(ParticipantRole::Driver).to_string())
        } else {
            None
        }
    });

    GroupContext {
        session_id: group.id.clone(),
        participants: group
            .participants
            .iter()
            .filter(|p| p.is_bot())
            .map(display_participant)
            .collect(),
        recipient: Some(target_bot.to_string()),
        recipient_name,
        recipient_role,
        delivery_type: Some(delivery_slug(delivery_type).to_string()),
        originator: group
            .participants
            .iter()
            .find(|p| p.bot_uuid == group.originator())
            .map(display_participant)
            .unwrap_or_else(|| group.originator().to_string()),
        from: group
            .participants
            .iter()
            .find(|p| p.bot_uuid == from)
            .map(display_participant)
            .unwrap_or_else(|| from.to_string()),
        from_bot_id: Some(from.to_string()),
        from_bot_owner,
        you_are_mentioned: mentions.iter().any(|m| m == target_bot),
        is_sender: from == target_bot,
        mentions: mentions.to_vec(),
        response_directive,
        message: message.to_string(),
        routing_mode,
        group_type,
    }
}
