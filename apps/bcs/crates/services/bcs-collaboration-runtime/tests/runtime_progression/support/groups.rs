use super::super::{
    ActorKind,
    Arc,
    Group,
    GroupStrategy,
    MemoryGroupRepo,
    MemorySessionRepo,
    MessageViewScope,
    Participant,
    ParticipantMode,
    ParticipantRole,
    SessionManagementServiceImpl
};

pub(in super::super) fn test_sessions() -> Arc<SessionManagementServiceImpl> {
    Arc::new(SessionManagementServiceImpl::new(
        Arc::new(MemorySessionRepo::new()),
        Arc::new(MemoryGroupRepo::new()),
    ))
}

pub(in super::super) fn test_group() -> Group {
    Group::new(
        "group-1",
        "driver-bot",
        vec![Participant {
            bot_uuid: "driver-bot".to_string(),
            bot_name: Some("Driver".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        }],
    )
}

pub(in super::super) fn session_collaboration_group(strategy: GroupStrategy) -> Group {
    let mut group = Group::new(
        "group-1",
        "driver-bot",
        vec![
            Participant {
                bot_uuid: "driver-bot".to_string(),
                bot_name: Some("Driver".to_string()),
                kind: None,
                role: match strategy {
                    GroupStrategy::ManagerWorker => ParticipantRole::Manager,
                    GroupStrategy::Chat | GroupStrategy::StateMachine => ParticipantRole::Driver,
                },
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::Auto),
                tags: Vec::new(),
                message_view_scope: MessageViewScope::Full,
            },
            Participant {
                bot_uuid: "worker-bot".to_string(),
                bot_name: Some("Worker".to_string()),
                kind: None,
                role: match strategy {
                    GroupStrategy::ManagerWorker => ParticipantRole::Worker,
                    GroupStrategy::Chat | GroupStrategy::StateMachine => {
                        ParticipantRole::Consultant
                    }
                },
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::Auto),
                tags: Vec::new(),
                message_view_scope: MessageViewScope::Full,
            },
        ],
    );
    group.group_strategy = strategy;
    group
}

pub(in super::super) fn state_machine_test_group() -> Group {
    let mut group = test_group();
    group.group_strategy = GroupStrategy::StateMachine;
    group
}
