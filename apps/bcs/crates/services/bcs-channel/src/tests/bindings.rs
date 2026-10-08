use super::*;

#[tokio::test]
async fn create_binding_rejects_empty_account_and_target_refs() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;

    let empty_account = harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: " ".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await;
    assert!(empty_account.is_err());

    let ignored_client_env = harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: " ".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await;
    assert!(ignored_client_env.is_ok());

    let empty_group = harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: " ".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await;
    assert!(empty_group.is_err());

    Ok(())
}

#[tokio::test]
async fn create_binding_uses_service_runtime_env() -> TestResult {
    let harness = TestHarness::new_with_env(manager_group("group_1"), "pre").await?;

    let binding = harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "local".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    assert_eq!(binding.env, "pre");
    assert_eq!(
        harness.binding_repo.get("generated_id").await?.unwrap().env,
        "pre"
    );

    Ok(())
}

#[tokio::test]
async fn create_binding_allows_free_chat_group_visibility_modes() -> TestResult {
    let harness = TestHarness::new(chat_group("group_chat")).await?;

    for (id, visibility) in [
        ("robot_full", Visibility::FullTranscript),
        ("robot_lead", Visibility::LeadOnly),
    ] {
        let binding = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: id.to_string(),
                target: BindingTarget::Group {
                    group_id: "group_chat".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: visibility,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config(id),
            })
            .await?;
        assert_eq!(binding.outbound_visibility, visibility);
    }

    Ok(())
}

#[tokio::test]
async fn group_binding_cleanup_removes_only_bindings_for_requested_group() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let group_1 = BindingTarget::Group {
        group_id: "group_1".to_string(),
    };
    let group_2 = BindingTarget::Group {
        group_id: "group_2".to_string(),
    };
    let bot = BindingTarget::Bot {
        bot_id: "bot_1".to_string(),
    };

    harness
        .binding_repo
        .create(active_binding(
            "group_1_dingtalk",
            "robot_1",
            group_1.clone(),
            Visibility::FullTranscript,
        ))
        .await?;
    let mut group_1_other_channel = active_binding(
        "group_1_other_channel",
        "account_2",
        group_1.clone(),
        Visibility::FullTranscript,
    );
    group_1_other_channel.channel_type = "test_im".to_string();
    harness.binding_repo.create(group_1_other_channel).await?;
    harness
        .binding_repo
        .create(active_binding(
            "group_2_dingtalk",
            "robot_2",
            group_2.clone(),
            Visibility::FullTranscript,
        ))
        .await?;
    harness
        .binding_repo
        .create(active_binding(
            "bot_dingtalk",
            "robot_3",
            bot.clone(),
            Visibility::FullTranscript,
        ))
        .await?;

    let removed = harness.service.delete_bindings_for_group("group_1").await?;

    assert_eq!(removed, 2);
    assert!(
        harness
            .binding_repo
            .list_by_target(&group_1, None)
            .await?
            .is_empty()
    );
    assert_eq!(
        harness
            .binding_repo
            .list_by_target(&group_2, None)
            .await?
            .len(),
        1
    );
    assert_eq!(
        harness.binding_repo.list_by_target(&bot, None).await?.len(),
        1
    );

    Ok(())
}

#[tokio::test]
async fn bot_binding_cleanup_removes_only_bindings_for_requested_bot() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    let bot_1 = BindingTarget::Bot {
        bot_id: "bot_1".to_string(),
    };
    let bot_2 = BindingTarget::Bot {
        bot_id: "bot_2".to_string(),
    };
    let group = BindingTarget::Group {
        group_id: "group_1".to_string(),
    };

    harness
        .binding_repo
        .create(active_binding(
            "bot_1_dingtalk",
            "robot_1",
            bot_1.clone(),
            Visibility::FullTranscript,
        ))
        .await?;
    let mut bot_1_other_channel = active_binding(
        "bot_1_other_channel",
        "account_2",
        bot_1.clone(),
        Visibility::FullTranscript,
    );
    bot_1_other_channel.channel_type = "test_im".to_string();
    harness.binding_repo.create(bot_1_other_channel).await?;
    harness
        .binding_repo
        .create(active_binding(
            "bot_2_dingtalk",
            "robot_2",
            bot_2.clone(),
            Visibility::FullTranscript,
        ))
        .await?;
    harness
        .binding_repo
        .create(active_binding(
            "group_dingtalk",
            "robot_3",
            group.clone(),
            Visibility::FullTranscript,
        ))
        .await?;

    let removed = harness.service.delete_bindings_for_bot("bot_1").await?;

    assert_eq!(removed, 2);
    assert!(
        harness
            .binding_repo
            .list_by_target(&bot_1, None)
            .await?
            .is_empty()
    );
    assert_eq!(
        harness
            .binding_repo
            .list_by_target(&bot_2, None)
            .await?
            .len(),
        1
    );
    assert_eq!(
        harness
            .binding_repo
            .list_by_target(&group, None)
            .await?
            .len(),
        1
    );

    Ok(())
}

#[tokio::test]
async fn delete_binding_completes_sessions_and_removes_conversation_mappings() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_1",
            "robot_1",
            BindingTarget::Bot {
                bot_id: "bot_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;
    let session = harness
        .session_repo
        .create("group_1", NewSessionParams::default())
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conversation_1".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::PerSender,
            im_user_id: Some("human_1".to_string()),
            bcs_session_id: session.id.clone(),
            last_active_at: 1,
        })
        .await?;

    harness.service.delete_binding("binding_1").await?;

    assert!(harness.binding_repo.get("binding_1").await?.is_none());
    assert!(
        harness
            .conversation_repo
            .list_by_binding("binding_1")
            .await?
            .is_empty()
    );
    let completed = harness.session_repo.get(&session.id).await.unwrap();
    assert_eq!(completed.status, SessionStatus::Completed);
    assert_eq!(
        completed.error_message.as_deref(),
        Some("channel_binding_deleted")
    );
    assert_eq!(
        harness
            .collaboration_runtime
            .cancelled_sessions
            .lock()
            .await
            .as_slice(),
        &[(session.id, "channel_binding_deleted".to_string())]
    );

    Ok(())
}

#[tokio::test]
async fn delete_binding_keeps_cleanup_state_retryable_when_run_cancellation_fails() -> TestResult
{
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_1",
            "robot_1",
            BindingTarget::Bot {
                bot_id: "bot_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;
    let session = harness
        .session_repo
        .create("group_1", NewSessionParams::default())
        .await?;
    harness
        .conversation_repo
        .upsert(bcs_domain::ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conversation_1".to_string(),
            im_conversation_type: "2".to_string(),
            session_scope: SessionScope::PerSender,
            im_user_id: Some("human_1".to_string()),
            bcs_session_id: session.id.clone(),
            last_active_at: 1,
        })
        .await?;
    *harness.collaboration_runtime.cancel_error.lock().await =
        Some("cancel failed".to_string());

    let error = harness
        .service
        .delete_binding("binding_1")
        .await
        .unwrap_err();

    assert!(error.to_string().contains("cancel failed"));
    assert_eq!(
        harness.binding_repo.get("binding_1").await?.unwrap().status,
        BindingStatus::Disabled
    );
    assert_eq!(
        harness
            .conversation_repo
            .list_by_binding("binding_1")
            .await?
            .len(),
        1
    );
    assert_eq!(
        harness.session_repo.get(&session.id).await.unwrap().status,
        SessionStatus::Running
    );

    Ok(())
}

#[tokio::test]
async fn delete_binding_preserves_session_used_by_another_binding() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    for (binding_id, account_ref) in [("binding_1", "robot_1"), ("binding_2", "robot_2")] {
        harness
            .binding_repo
            .create(active_binding(
                binding_id,
                account_ref,
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;
    }
    let session = harness
        .session_repo
        .create("group_1", NewSessionParams::default())
        .await?;
    for (binding_id, conversation_id) in [
        ("binding_1", "conversation_1"),
        ("binding_2", "conversation_2"),
    ] {
        harness
            .conversation_repo
            .upsert(bcs_domain::ConversationSessionMap {
                binding_id: binding_id.to_string(),
                im_conversation_id: conversation_id.to_string(),
                im_conversation_type: "2".to_string(),
                session_scope: SessionScope::Conversation,
                im_user_id: None,
                bcs_session_id: session.id.clone(),
                last_active_at: 1,
            })
            .await?;
    }

    harness.service.delete_binding("binding_1").await?;

    assert!(
        harness
            .conversation_repo
            .list_by_binding("binding_1")
            .await?
            .is_empty()
    );
    assert_eq!(
        harness
            .conversation_repo
            .list_by_binding("binding_2")
            .await?
            .len(),
        1
    );
    assert_eq!(
        harness.session_repo.get(&session.id).await.unwrap().status,
        SessionStatus::Running
    );
    assert!(
        harness
            .collaboration_runtime
            .cancelled_sessions
            .lock()
            .await
            .is_empty()
    );

    Ok(())
}

#[tokio::test]
async fn create_direct_bot_binding_defaults_group_scope_to_per_sender() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;

    let binding = harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Bot {
                bot_id: "target_bot".to_string(),
            },
            group_chat_scope: None,
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    assert_eq!(binding.group_chat_scope, Some(GroupChatScope::PerSender));

    Ok(())
}

#[tokio::test]
async fn create_direct_bot_binding_canonicalizes_long_internal_owner_id() -> TestResult {
    let harness =
        TestHarness::new_with_generated_id(manager_group("group_1"), "x".repeat(55)).await?;

    let binding = harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Bot {
                bot_id: "target_bot".to_string(),
            },
            group_chat_scope: None,
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    let group_id = channel_owned_group_id(&binding.channel_type, GroupKind::Dm, &binding.id)?;
    assert!(format!("{group_id}:abcdef12").chars().count() <= 64);
    assert_eq!(harness.binding_repo.list().await?.len(), 1);

    Ok(())
}

#[test]
fn channel_owned_group_id_encodes_channel_and_group_kind() -> TestResult {
    let source_id = "bc7d5297-4947-474d-a2f1-cdea1c5642b6";

    assert_eq!(
        channel_owned_group_id("dingtalk", GroupKind::Normal, source_id)?,
        "bcs_grp_dingtalk_bc7d52974947474da2f1cdea1c5642b6"
    );
    let dm_group_id = channel_owned_group_id("dingtalk", GroupKind::Dm, source_id)?;
    assert_eq!(
        dm_group_id,
        "bcs_grp_dingtalk_dm_bc7d52974947474da2f1cdea1c5642b6"
    );
    assert_eq!(format!("{dm_group_id}:abcdef12").chars().count(), 61);

    Ok(())
}

#[tokio::test]
async fn managed_bot_group_reuses_legacy_channel_group_id() -> TestResult {
    let legacy_group_id = "dingtalk_binding_bot";
    let harness = TestHarness::new(manager_group(legacy_group_id)).await?;
    let binding = active_binding(
        "binding_bot",
        "robot_1",
        BindingTarget::Bot {
            bot_id: "target_bot".to_string(),
        },
        Visibility::FullTranscript,
    );

    let group_id = harness
        .service
        .ensure_managed_single_bot_group(&binding, "target_bot")
        .await?;

    assert_eq!(group_id, legacy_group_id);
    Ok(())
}

#[tokio::test]
async fn create_binding_reports_empty_service_env_as_internal_error() -> TestResult {
    let harness = TestHarness::new_with_env(manager_group("group_1"), " ").await?;

    let error = harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "local".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await
        .expect_err("empty service env should fail");

    match error {
        ChannelUseCaseError::Internal(ServiceError::InternalError(message)) => {
            assert!(message.contains("server environment"));
        }
        other => panic!("expected internal service env error, got {other:?}"),
    }

    Ok(())
}

#[tokio::test]
async fn create_binding_rejects_duplicate_active_account_and_provider_invalid_config()
-> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;

    harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await?;

    let duplicate = harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("robot_1"),
        })
        .await;
    assert!(duplicate.is_err());

    let invalid_config = harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: channel_type(),
            account_ref: "robot_2".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: invalid_provider_config("robot_2"),
        })
        .await;
    assert!(invalid_config.is_err());
    assert_eq!(harness.provider.validate_call_count(), 3);

    let unknown_provider = harness
        .service
        .create_binding(CreateBindingCommand {
            channel_type: "missing".to_string(),
            account_ref: "robot_4".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            created_by: Some("creator".to_string()),
            config: dingtalk_config("different_robot"),
        })
        .await;
    assert!(unknown_provider.is_err());

    Ok(())
}

#[tokio::test]
async fn enabling_binding_rejects_duplicate_active_account() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_active",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;
    let mut disabled = active_binding(
        "binding_disabled",
        "robot_1",
        BindingTarget::Group {
            group_id: "group_1".to_string(),
        },
        Visibility::FullTranscript,
    );
    disabled.status = BindingStatus::Disabled;
    harness.binding_repo.create(disabled).await?;

    let enable = harness
        .service
        .set_binding_status("binding_disabled", true)
        .await;
    assert!(enable.is_err());

    Ok(())
}

#[tokio::test]
async fn list_bindings_redacts_provider_config() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_1",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;

    let bindings = harness.service.list_bindings().await?;
    assert_eq!(bindings.len(), 1);
    assert_eq!(bindings[0].config["client_secret"], "<redacted>");

    let stored = harness
        .binding_repo
        .get("binding_1")
        .await?
        .expect("stored binding");
    assert_eq!(stored.config["client_secret"], "secret");

    Ok(())
}

#[tokio::test]
async fn list_bindings_by_target_filters_and_redacts_provider_config() -> TestResult {
    let harness = TestHarness::new(manager_group("group_1")).await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_1",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;
    harness
        .binding_repo
        .create(active_binding(
            "binding_2",
            "robot_2",
            BindingTarget::Group {
                group_id: "group_2".to_string(),
            },
            Visibility::FullTranscript,
        ))
        .await?;

    let bindings = harness
        .service
        .list_bindings_by_target(
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Some("dingtalk".to_string()),
        )
        .await?;

    assert_eq!(bindings.len(), 1);
    assert_eq!(bindings[0].id, "binding_1");
    assert_eq!(bindings[0].config["client_secret"], "<redacted>");

    Ok(())
}
