use super::*;

pub(super) async fn make_human_bot_dm(support: &support::FlowTestSupport) {
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_kind = GroupKind::Dm;
    group.driver_bot = "bot-driver".to_string();
    group.dm_pair_key = Some("bot-driver|human_1".to_string());
    group.participants = vec![
        Participant {
            bot_uuid: "human_1".to_string(),
            bot_name: Some("Human One".to_string()),
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    },
        Participant {
            bot_uuid: "bot-driver".to_string(),
            bot_name: Some("Driver".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    },
    ];
    support.group.upsert(group).await.unwrap();
}
