use super::*;

#[derive(Default)]
struct FallbackParamHistoryBotRequest {
    calls: RwLock<Vec<(String, serde_json::Value)>>,
}

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for FallbackParamHistoryBotRequest {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        _method: &str,
        params: serde_json::Value,
        _timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        let bot_uuid = target.bot_id().to_string();
        self.calls
            .write()
            .await
            .push((bot_uuid.clone(), params.clone()));
        if bot_uuid != "bot-driver" {
            return Err("unexpected_source".to_string());
        }
        if params.get("bcs_session_id").is_some() {
            return Err("explicit_session_not_supported".to_string());
        }
        Ok(json!({
            "messages": [
                {"id": "fallback-1", "role": "assistant", "content": "fallback session history", "timestamp": 1}
            ]
        }))
    }
}

#[tokio::test]
async fn session_history_allows_human_who_is_only_session_participant() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group
        .participants
        .retain(|participant| participant.is_bot());
    support.group.upsert(group).await.unwrap();

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Ok(json!({
            "messages": [
                {"id": "session-human-1", "role": "assistant", "content": "session visible", "timestamp": 1}
            ]
        })),
    );
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let result = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![
                Participant::bot("bot-observer", ParticipantRole::Observer),
                {
                    let mut human = Participant::human("human_1", ParticipantRole::Observer);
                    human.mode = Some(ParticipantMode::Present);
                    human
                },
            ],
            view_bot_id: Some("human_1".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "session visible");
}

#[tokio::test]
async fn session_history_allows_human_whose_owned_bot_is_session_participant() {
    // Reproduces: driver bot pulled the human's bot into the session, but the
    // human's bot is NOT a group participant and the human is not directly in
    // the group. The session still shows in the bot tab (filtered by session
    // participation), so reading its messages must be allowed.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;

    // Group only has the driver bot — no human, no human-owned bot.
    let mut group = support.group.get("group-1").await.unwrap();
    group
        .participants
        .retain(|participant| participant.bot_uuid == "bot-driver");
    support.group.upsert(group).await.unwrap();

    // The human owns bot-observer (the pulled-in bot).
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Ok(json!({
            "messages": [
                {"id": "pulled-1", "role": "assistant", "content": "pulled bot history", "timestamp": 1}
            ]
        })),
    );
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    )
    .with_bot_authority_hook(Some(staff_authority(&[("bot-driver", "1"), ("bot-observer", "1")])));

    let result = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![
                Participant::bot("bot-driver", ParticipantRole::Driver),
                Participant::bot("bot-observer", ParticipantRole::Observer),
            ],
            view_bot_id: Some("bot-observer".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "pulled bot history");
}

#[tokio::test]
async fn session_history_denies_human_with_no_session_or_group_stake() {
    // Boundary check: a human who is neither a session participant, nor owns a
    // session-participant Bot, nor has any stake in the group must still be
    // denied — the ownership allowance above must not over-grant.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group
        .participants
        .retain(|participant| participant.bot_uuid == "bot-driver");
    support.group.upsert(group).await.unwrap();

    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        HashMap::new(),
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    )
    .with_bot_authority_hook(Some(
        bcs_test_support::lazy_authority_chain().hook,
    ));

    let err = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_2".to_string(),
                staff_no: "2".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![
                Participant::bot("bot-driver", ParticipantRole::Driver),
                Participant::bot("bot-observer", ParticipantRole::Observer),
            ],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .expect_err("unrelated human must be denied");

    assert!(
        matches!(err, bcs_service_api::GroupUseCaseError::Forbidden(_)),
        "expected Forbidden, got {err:?}"
    );
}

#[tokio::test]
async fn session_history_rejects_public_caller() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        HashMap::new(),
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let err = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Public,
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .expect_err("public session history reads must be rejected");

    assert!(
        matches!(err, bcs_service_api::GroupUseCaseError::Unauthorized(_)),
        "expected Unauthorized, got {err:?}"
    );
}

#[tokio::test]
async fn session_history_denies_group_owner_when_not_in_session_and_owns_no_session_bot() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();

    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        HashMap::new(),
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    )
    .with_bot_authority_hook(Some(staff_authority(&[("bot-observer", "1")])));

    let err = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .expect_err("group ownership alone must not grant session history access");

    assert!(
        matches!(err, bcs_service_api::GroupUseCaseError::Forbidden(_)),
        "expected Forbidden, got {err:?}"
    );
}

#[tokio::test]
async fn session_history_denies_group_participant_bot_not_in_session() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        HashMap::new(),
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let err = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-observer".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .expect_err("group participant bot must be rejected when absent from the session");

    assert!(
        matches!(err, bcs_service_api::GroupUseCaseError::Forbidden(_)),
        "expected Forbidden, got {err:?}"
    );
}

#[tokio::test]
async fn session_history_denies_non_participant_bot() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        HashMap::new(),
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let err = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "intruder-bot".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .expect_err("non-participant bot must be rejected");

    assert!(
        matches!(err, bcs_service_api::GroupUseCaseError::Forbidden(_)),
        "expected Forbidden, got {err:?}"
    );
}

#[tokio::test]
async fn session_history_resolves_from_prefix_using_session_participants() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group
        .participants
        .retain(|participant| participant.is_bot());
    support.group.upsert(group).await.unwrap();

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Ok(json!({
            "messages": [
                {
                    "id": "session-human-injected",
                    "role": "assistant",
                    "content": "[from:Human One]hello from session human",
                    "timestamp": 1
                }
            ]
        })),
    );
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let result = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![
                Participant::bot("bot-observer", ParticipantRole::Observer),
                {
                    let mut human = Participant::human("human_1", ParticipantRole::Observer);
                    human.mode = Some(ParticipantMode::Present);
                    human
                },
            ],
            view_bot_id: Some("human_1".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].sender, "human_1");
    assert_eq!(result.messages[0].content, "hello from session human");
    assert_eq!(result.messages[0].role, MessageRole::User);
}

#[tokio::test]
async fn session_history_human_view_prefers_session_bot_before_group_lead() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    if let Some(human) = group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "human_1")
    {
        human.mode = Some(ParticipantMode::Present);
    }
    support.group.upsert(group).await.unwrap();

    let mut responses = HashMap::new();
    responses.insert(
        "bot-driver".to_string(),
        Ok(json!({
            "messages": [
                {"id": "driver-1", "role": "assistant", "content": "group lead history", "timestamp": 1}
            ]
        })),
    );
    responses.insert(
        "bot-observer".to_string(),
        Ok(json!({
            "messages": [
                {"id": "observer-1", "role": "assistant", "content": "session bot history", "timestamp": 1}
            ]
        })),
    );
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let result = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![
                Participant::bot("bot-observer", ParticipantRole::Observer),
                {
                    let mut human = Participant::human("human_1", ParticipantRole::Observer);
                    human.mode = Some(ParticipantMode::Present);
                    human
                },
            ],
            view_bot_id: Some("human_1".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].sender, "bot-observer");
    assert_eq!(result.messages[0].content, "session bot history");
}

#[tokio::test]
async fn session_history_v3_falls_back_to_session_key_when_explicit_session_request_errors() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.set_protocol_version("bot-driver", 3).await;
    let bot_request = Arc::new(FallbackParamHistoryBotRequest::default());
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request.clone(),
    );

    let result = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-driver".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "fallback session history");
    let calls = bot_request.calls.read().await;
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[0].1["session_key"], "group-1");
    assert_eq!(calls[0].1["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(calls[1].1["session_key"], "group-1:abcdef12");
}
