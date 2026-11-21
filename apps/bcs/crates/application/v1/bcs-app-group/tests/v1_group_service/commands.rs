//! Shared request/projection fixtures, kept separate from suite assembly.
use super::*;
pub(super) fn collaboration_create_command() -> CreateGroup {
    CreateGroup {
        caller: bot_principal("alice"),
        group: CreateGroupSpec::Collaboration(CreateCollaborationGroup { catalog_labels:Default::default(),
            human_mention_notify_mode: Default::default(),
            originator: None,
            name: Some("Provisioned Group".to_string()),
            context: None,
            opening_message: None,
            visibility: GroupVisibility::Private,
            driver_bot_uuid: "driver".to_string(),
            participants: Vec::new(),
            collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                delivery_policy: GroupDeliveryPolicy {
                    bot_final_delivery: BotFinalDelivery::SendToDriver,
                },
            }),
        }),
    }
}

pub(super) fn dm_create_command() -> CreateGroup {
    CreateGroup {
        caller: bot_principal("alice"),
        group: CreateGroupSpec::DirectMessage(CreateDirectMessageGroup { catalog_labels:Default::default(),
            name: Some("Alice and Assistant".to_string()),
            context: None,
            target_actor_id: "assistant".to_string(),
        }),
    }
}

pub(super) fn bot_principal_in_tenant(bot_uuid: &str, tenant: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some(tenant.to_string()),
        user: Some(AuthenticatedUserIdentity {
            id: bot_uuid.to_string(),
            username: bot_uuid.to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

pub(super) fn human_principal_with_profile(
    subject_id: &str,
    username: &str,
    display_name: Option<&str>,
    full_name: Option<&str>,
) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: subject_id.into(),
            username: username.into(),
            display_name: display_name.map(str::to_string),
            full_name: full_name.map(str::to_string),
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

pub(super) fn normal_group(
    group_id: &str,
    driver: &str,
    participants: Vec<Participant>,
    strategy: GroupStrategy,
    updated_at: u64,
) -> Group {
    let mut group = Group::new(group_id, driver, participants);
    group.originator = Some(format!("human_{driver}"));
    group.label = Some(group_id.to_string());
    group.group_strategy = strategy;
    group.updated_at = updated_at;
    // V1 list_groups sorts by `created_at DESC, group_id ASC`; pin both
    // timestamps to the same value so existing ordering assertions remain
    // deterministic under the new comparator.
    group.created_at = updated_at;
    group
}

pub(super) fn detail_group_id(detail: &GroupDetail) -> &str {
    match detail {
        GroupDetail::Collaboration(detail) => &detail.group_id,
        GroupDetail::DirectMessage(detail) => &detail.group_id,
    }
}

pub(super) fn detail_notify_mode(detail: &GroupDetail) -> bcs_service_api::HumanMentionNotifyMode {
    match detail {
        GroupDetail::Collaboration(detail) => detail.human_mention_notify_mode,
        GroupDetail::DirectMessage(detail) => detail.human_mention_notify_mode,
    }
}

pub(super) fn summary_notify_mode(summary: &GroupSummary) -> bcs_service_api::HumanMentionNotifyMode {
    match summary {
        GroupSummary::Normal(summary) => summary.human_mention_notify_mode,
        GroupSummary::DirectMessage(summary) => summary.human_mention_notify_mode,
    }
}
