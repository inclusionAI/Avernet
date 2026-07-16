use bcs_channel_store::{
    MemoryChannelBindingRepo, MemoryConversationSessionRepo, MemoryImParticipantRepo,
};
use bcs_domain::{
    BindingStatus, BindingTarget, ChannelBinding, ConversationSessionMap, GroupChatScope,
    ImParticipantMap, SessionScope, Visibility,
};
use bcs_service_api::ServiceResult;
use bcs_service_api::port::repo::{
    ChannelBindingRepoPort, ConversationSessionRepoPort, ImParticipantRepoPort,
};

#[tokio::test]
async fn conversation_repo_round_trips_and_reverse_lookups_session() -> ServiceResult<()> {
    let repo = MemoryConversationSessionRepo::new();

    repo.upsert(conversation_map(
        "binding_1",
        "conv_1",
        SessionScope::Conversation,
        None,
        "session_old",
        1,
    ))
    .await?;
    repo.upsert(conversation_map(
        "binding_1",
        "conv_1",
        SessionScope::Conversation,
        None,
        "session_new",
        2,
    ))
    .await?;

    let by_key = repo
        .get("binding_1", "conv_1", SessionScope::Conversation, None)
        .await?;
    assert_eq!(
        by_key.as_ref().map(|map| map.bcs_session_id.as_str()),
        Some("session_new")
    );
    assert_eq!(by_key.as_ref().map(|map| map.last_active_at), Some(2));

    let by_session = repo.find_by_session("binding_1", "session_new").await?;
    assert_eq!(
        by_session.as_ref().map(|map| map.im_conversation_id.as_str()),
        Some("conv_1")
    );
    let by_bcs_session = repo.list_by_bcs_session("session_new").await?;
    assert_eq!(by_bcs_session.len(), 1);
    assert_eq!(by_bcs_session[0].binding_id, "binding_1");
    assert_eq!(by_bcs_session[0].im_conversation_id, "conv_1");

    Ok(())
}

#[tokio::test]
async fn conversation_repo_isolates_per_sender_scope_for_same_im_conversation(
) -> ServiceResult<()> {
    let repo = MemoryConversationSessionRepo::new();

    repo.upsert(conversation_map(
        "binding_1",
        "conv_group",
        SessionScope::PerSender,
        Some("staff_a"),
        "session_a",
        1,
    ))
    .await?;
    repo.upsert(conversation_map(
        "binding_1",
        "conv_group",
        SessionScope::PerSender,
        Some("staff_b"),
        "session_b",
        1,
    ))
    .await?;
    repo.upsert(conversation_map(
        "binding_1",
        "conv_group",
        SessionScope::Conversation,
        None,
        "session_shared",
        1,
    ))
    .await?;

    let staff_a = repo
        .get(
            "binding_1",
            "conv_group",
            SessionScope::PerSender,
            Some("staff_a"),
        )
        .await?;
    let staff_b = repo
        .get(
            "binding_1",
            "conv_group",
            SessionScope::PerSender,
            Some("staff_b"),
        )
        .await?;
    let shared = repo
        .get("binding_1", "conv_group", SessionScope::Conversation, None)
        .await?;

    assert_eq!(
        staff_a.as_ref().map(|map| map.bcs_session_id.as_str()),
        Some("session_a")
    );
    assert_eq!(
        staff_b.as_ref().map(|map| map.bcs_session_id.as_str()),
        Some("session_b")
    );
    assert_eq!(
        shared.as_ref().map(|map| map.bcs_session_id.as_str()),
        Some("session_shared")
    );

    Ok(())
}

#[tokio::test]
async fn im_participant_repo_round_trips_and_replaces_external_identity(
) -> ServiceResult<()> {
    let repo = MemoryImParticipantRepo::new();

    repo.upsert(participant("staff_1", "human_old", Some("Old Name")))
        .await?;
    repo.upsert(participant("staff_1", "human_new", Some("New Name")))
        .await?;
    repo.upsert(participant("staff_2", "human_2", None)).await?;

    let staff_1 = repo
        .get("dingtalk".to_string(), "robot_1", "staff_1")
        .await?;
    let staff_2 = repo
        .get("dingtalk".to_string(), "robot_1", "staff_2")
        .await?;

    assert_eq!(
        staff_1.as_ref().map(|map| map.actor_id.as_str()),
        Some("human_new")
    );
    assert_eq!(
        staff_1.as_ref().and_then(|map| map.display_name.as_deref()),
        Some("New Name")
    );
    assert_eq!(
        staff_2.as_ref().map(|map| map.actor_id.as_str()),
        Some("human_2")
    );

    Ok(())
}

#[tokio::test]
async fn binding_repo_lifecycle_filters_active_bindings() -> ServiceResult<()> {
    let repo = MemoryChannelBindingRepo::new();

    repo.create(binding("binding_1", "robot_1", BindingStatus::Active))
        .await?;
    repo.create(binding("binding_2", "robot_2", BindingStatus::Disabled))
        .await?;

    let created = repo.get("binding_1").await?;
    assert_eq!(
        created.as_ref().map(|binding| binding.account_ref.as_str()),
        Some("robot_1")
    );
    assert_eq!(repo.list().await?.len(), 2);

    let active = repo
        .find_active_by_account("dingtalk".to_string(), "robot_1")
        .await?;
    assert_eq!(
        active.as_ref().map(|binding| binding.id.as_str()),
        Some("binding_1")
    );

    let disabled = repo
        .find_active_by_account("dingtalk".to_string(), "robot_2")
        .await?;
    assert_eq!(disabled, None);

    repo.set_status("binding_1", false).await?;
    let inactive = repo
        .find_active_by_account("dingtalk".to_string(), "robot_1")
        .await?;
    assert_eq!(inactive, None);

    let updated_config = serde_json::json!({
        "robot_code": "robot_1",
        "client_id": "client_id",
        "client_secret": "secret",
        "send_mode": {
            "mode": "streaming_card",
            "card_template_id": "card_tpl_123",
            "fallback_message_type": "markdown"
        }
    });
    repo.set_config("binding_1", updated_config).await?;
    let updated = repo.get("binding_1").await?.expect("binding exists");
    assert_eq!(updated.config["send_mode"]["mode"], "streaming_card");
    assert_eq!(
        updated.config["send_mode"]["card_template_id"],
        "card_tpl_123"
    );

    repo.set_status("binding_2", true).await?;
    let enabled = repo
        .find_active_by_account("dingtalk".to_string(), "robot_2")
        .await?;
    assert_eq!(
        enabled.as_ref().map(|binding| binding.id.as_str()),
        Some("binding_2")
    );

    repo.delete("binding_2").await?;
    assert_eq!(repo.get("binding_2").await?, None);

    Ok(())
}

#[tokio::test]
async fn binding_repo_lists_only_requested_target_and_optional_channel() -> ServiceResult<()> {
    let repo = MemoryChannelBindingRepo::new();

    let mut group_dingtalk = binding("group_dingtalk", "robot_1", BindingStatus::Active);
    group_dingtalk.target = BindingTarget::Group {
        group_id: "group_1".to_string(),
    };
    repo.create(group_dingtalk).await?;

    let mut group_other_channel = binding(
        "group_other_channel",
        "account_2",
        BindingStatus::Active,
    );
    group_other_channel.target = BindingTarget::Group {
        group_id: "group_1".to_string(),
    };
    group_other_channel.channel_type = "test_im".to_string();
    repo.create(group_other_channel).await?;

    let mut other_group = binding("other_group", "robot_2", BindingStatus::Active);
    other_group.target = BindingTarget::Group {
        group_id: "group_2".to_string(),
    };
    repo.create(other_group).await?;

    let mut bot_binding = binding("bot_binding", "robot_3", BindingStatus::Active);
    bot_binding.target = BindingTarget::Bot {
        bot_id: "bot_1:user_1".to_string(),
    };
    repo.create(bot_binding).await?;

    let group_target = BindingTarget::Group {
        group_id: "group_1".to_string(),
    };
    let group_all_channels = repo.list_by_target(&group_target, None).await?;
    assert_eq!(group_all_channels.len(), 2);

    let group_dingtalk = repo
        .list_by_target(&group_target, Some("dingtalk"))
        .await?;
    assert_eq!(group_dingtalk.len(), 1);
    assert_eq!(group_dingtalk[0].id, "group_dingtalk");

    let bot_target = BindingTarget::Bot {
        bot_id: "bot_1:user_1".to_string(),
    };
    let bot_bindings = repo
        .list_by_target(&bot_target, Some("dingtalk"))
        .await?;
    assert_eq!(bot_bindings.len(), 1);
    assert_eq!(bot_bindings[0].id, "bot_binding");

    Ok(())
}

#[tokio::test]
async fn binding_repo_preserves_generic_channel_type_and_config() -> ServiceResult<()> {
    let repo = MemoryChannelBindingRepo::new();
    let mut binding = binding("binding_generic", "account_1", BindingStatus::Active);
    binding.channel_type = "test_im".to_string();
    binding.config = serde_json::json!({
        "nested": {
            "secret": "raw"
        },
        "mode": "normal"
    });

    repo.create(binding).await?;
    let active = repo
        .find_active_by_account("test_im".to_string(), "account_1")
        .await?
        .expect("active binding");

    assert_eq!(active.channel_type, "test_im");
    assert_eq!(active.config["nested"]["secret"], "raw");
    assert_eq!(active.config["mode"], "normal");

    Ok(())
}

fn conversation_map(
    binding_id: &str,
    im_conversation_id: &str,
    session_scope: SessionScope,
    im_user_id: Option<&str>,
    bcs_session_id: &str,
    last_active_at: u64,
) -> ConversationSessionMap {
    ConversationSessionMap {
        binding_id: binding_id.to_string(),
        im_conversation_id: im_conversation_id.to_string(),
        im_conversation_type: "2".to_string(),
        session_scope,
        im_user_id: im_user_id.map(str::to_string),
        bcs_session_id: bcs_session_id.to_string(),
        last_active_at,
    }
}

fn participant(
    im_user_id: &str,
    actor_id: &str,
    display_name: Option<&str>,
) -> ImParticipantMap {
    ImParticipantMap {
        channel_type: "dingtalk".to_string(),
        account_ref: "robot_1".to_string(),
        im_user_id: im_user_id.to_string(),
        actor_id: actor_id.to_string(),
        display_name: display_name.map(str::to_string),
    }
}

fn binding(id: &str, account_ref: &str, status: BindingStatus) -> ChannelBinding {
    ChannelBinding {
        id: id.to_string(),
        channel_type: "dingtalk".to_string(),
        account_ref: account_ref.to_string(),
        target: BindingTarget::Group {
            group_id: "group_1".to_string(),
        },
        group_chat_scope: Some(GroupChatScope::ConversationShared),
        outbound_visibility: Visibility::FullTranscript,
        env: "dev".to_string(),
        status,
        created_by: Some("creator".to_string()),
        config: serde_json::json!({
            "robot_code": account_ref,
            "client_id": "client_id",
            "client_secret": "secret",
            "send_mode": {
                "mode": "normal",
                "message_type": "markdown"
            }
        }),
    }
}
