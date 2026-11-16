use super::*;

pub(super) fn create_cmd(
    caller_actor_id: Option<&str>,
    driver_bot_id: &str,
    participants: Vec<GroupCreateParticipantCommand>,
) -> GroupCreateCommand {
    GroupCreateCommand {
        human_mention_notify_mode: Default::default(),
        create_initial_session: true,
        group_id: Some("group-under-test".to_string()),
        caller_actor_id: caller_actor_id.map(str::to_string),
        driver_bot_id: driver_bot_id.to_string(),
        originator: None,
        label: None,
        topic: Some("debug incident".to_string()),
        context: Some("prod checkout outage".to_string()),
        opening_message: None,
        routing_policy: None,
        member_bot_ids: Vec::new(),
        participants,
        group_kind: None,
        group_strategy: None,
        visibility: None,
        provisioning: false,
    }
}

pub(super) fn participant(bot_id: &str, role: Option<&str>) -> GroupCreateParticipantCommand {
    GroupCreateParticipantCommand {
        bot_id: bot_id.to_string(),
        role: role.map(str::to_string),
        tags: Vec::new(),
        message_view_scope: None,
    }
}

pub(super) fn bot(bot_uuid: &str, name: &str, visibility: &str, created_by: Option<&str>) -> RegisteredBot {
    RegisteredBot {
        bot_uuid: bot_uuid.to_string(),
        capabilities: BotCapabilities {
            name: Some(name.to_string()),
            visibility: visibility.to_string(),
            ..Default::default()
        },
        env: None,
        created_by: created_by.map(str::to_string),
        actor_kind: ActorKind::Bot,
        status: Default::default(),
    }
}

pub(super) fn human(actor_id: &str, name: &str) -> RegisteredBot {
    RegisteredBot {
        bot_uuid: actor_id.to_string(),
        capabilities: BotCapabilities {
            name: Some(name.to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        env: None,
        created_by: None,
        actor_kind: ActorKind::Human,
        status: Default::default(),
    }
}
