use super::*;

#[tokio::test]
async fn chat_abort_delivers_abort_frame_to_bot_participants() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    register_active_run(
        &run_context,
        "run-1",
        "group-1",
        "session-1",
        "bot-driver",
        BotRunTransportOwner::WebSocket,
    )
    .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context);

    let outcome = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "session-1".to_string(),
            bot_id: "bot-driver".to_string(),
            run_id: Some("run-1".to_string()),
        })
        .await
        .unwrap();

    assert!(outcome.aborted);
    assert_eq!(outcome.aborted_run_ids, vec!["run-1".to_string()]);
    let aborts = support.bot_delivery.aborts().await;
    assert_eq!(aborts.len(), 1);
    assert_eq!(aborts[0].run_id.as_deref(), Some("run-1"));
    assert_eq!(aborts[0].session_id, "session-1");
}

#[tokio::test]
async fn chat_abort_reuses_the_session_key_from_the_original_plugin_delivery() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    run_context
        .put_context(BotRunContext {
            run_id: "run-wire-key".to_string(),
            bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("session-1".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;
    run_context
        .register_active_run(ActiveBotRunContext {
            canonical_run_id: "run-wire-key".to_string(),
            downstream_run_id: "run-wire-key".to_string(),
            downstream_session_key: Some("group:legacy-wire-key".to_string()),
            scope: BotRunScope {
                group_id: "group-1".to_string(),
                session_id: "session-1".to_string(),
                bot_id: "bot-driver".to_string(),
            },
            transport_owner: BotRunTransportOwner::WebSocket,
            provider_bypass_headers: Vec::new(),
            deadline_ms: u64::MAX,
        })
        .await
        .unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context);

    flow.handle_chat_abort(ChatAbortCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: "session-1".to_string(),
        bot_id: "bot-driver".to_string(),
        run_id: Some("run-wire-key".to_string()),
    })
    .await
    .unwrap();

    assert_eq!(
        support.bot_delivery.aborts().await[0].session_id,
        "group:legacy-wire-key"
    );
}

#[tokio::test]
async fn chat_abort_publishes_frontend_event_through_port() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    register_active_run(
        &run_context,
        "run-1",
        "group-1",
        "session-1",
        "bot-driver",
        BotRunTransportOwner::WebSocket,
    )
    .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context);

    let outcome = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "session-1".to_string(),
            bot_id: "bot-driver".to_string(),
            run_id: Some("run-1".to_string()),
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 1);
    assert!(events[0].contains(r#""event":"chat.abort""#));
    assert!(events[0].contains(r#""run_id":"run-1""#));
}

#[tokio::test]
async fn chat_abort_with_session_rejects_a_run_from_another_session() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    register_active_run(
        &run_context,
        "run-other-session",
        "group-1",
        "session-other",
        "bot-driver",
        BotRunTransportOwner::WebSocket,
    )
    .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context);

    let outcome = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "session-bound".to_string(),
            bot_id: "bot-driver".to_string(),
            run_id: Some("run-other-session".to_string()),
        })
        .await
        .unwrap();

    assert!(!outcome.aborted);
    assert!(outcome.aborted_run_ids.is_empty());
    assert!(support.bot_delivery.aborts().await.is_empty());
    assert!(support.frontend_delivery.events().await.is_empty());
}

#[tokio::test]
async fn chat_abort_with_session_accepts_a_run_from_the_same_session() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    register_active_run(
        &run_context,
        "run-bound",
        "group-1",
        "session-bound",
        "bot-driver",
        BotRunTransportOwner::WebSocket,
    )
    .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context);

    let outcome = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "session-bound".to_string(),
            bot_id: "bot-driver".to_string(),
            run_id: Some("run-bound".to_string()),
        })
        .await
        .unwrap();

    assert!(outcome.aborted);
    assert_eq!(outcome.aborted_run_ids, vec!["run-bound".to_string()]);
    assert_eq!(support.bot_delivery.aborts().await.len(), 1);
}

#[tokio::test]
async fn chat_abort_without_run_id_uses_only_the_bound_session_key() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    for run_id in ["run-bound-1", "run-bound-2"] {
        register_active_run(
            &run_context,
            run_id,
            "group-1",
            "session-bound",
            "bot-driver",
            BotRunTransportOwner::WebSocket,
        )
        .await;
    }
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context);

    flow.handle_chat_abort(ChatAbortCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: "session-bound".to_string(),
        bot_id: "bot-driver".to_string(),
        run_id: None,
    })
    .await
    .unwrap();

    let aborts = support.bot_delivery.aborts().await;
    assert_eq!(aborts.len(), 2);
    assert!(
        aborts
            .iter()
            .all(|abort| abort.session_id == "session-bound")
    );
    assert!(aborts.iter().all(|abort| abort.run_id.is_some()));
}

#[tokio::test]
async fn chat_abort_provider_sends_one_scope_request_for_parallel_runs() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .set_delivery_target(
            "bot-driver",
            support::FakeRegistryService::provider_target("bot-driver"),
        )
        .await;
    support
        .bot_delivery
        .set_provider_abort_run_ids(vec![
            "provider-run-1".to_string(),
            "provider-run-2".to_string(),
        ])
        .await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    for (canonical, downstream) in [
        ("bcs-run-1", "provider-run-1"),
        ("bcs-run-2", "provider-run-2"),
    ] {
        register_active_run_with_downstream(
            &run_context,
            canonical,
            downstream,
            "group-1",
            "session-provider",
            "bot-driver",
            BotRunTransportOwner::HttpProvider {
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "bot-driver".to_string(),
            },
        )
        .await;
        {
            let mut context = run_context
                .find_active_run(canonical)
                .await
                .unwrap()
                .unwrap();
            context.downstream_session_key = Some("group:legacy-provider-wire-key".to_string());
            context.provider_bypass_headers = vec![(
                "x-sandbox-bypass".to_string(),
                "sandbox-route-1".to_string(),
            )];
            run_context.register_active_run(context).await.unwrap();
        }
    }
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context.clone());

    let outcome = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "session-provider".to_string(),
            bot_id: "bot-driver".to_string(),
            run_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.aborted_run_ids, ["bcs-run-1", "bcs-run-2"]);
    assert!(outcome.failures.is_empty());
    let aborts = support.bot_delivery.aborts().await;
    assert_eq!(aborts.len(), 1);
    assert!(aborts[0].run_id.is_none());
    assert_eq!(aborts[0].session_id, "session-provider");
    assert_eq!(
        aborts[0].provider_bypass_headers,
        vec![(
            "x-sandbox-bypass".to_string(),
            "sandbox-route-1".to_string(),
        )]
    );
    assert!(run_context.get_context("bcs-run-1").await.unwrap().terminal);
    assert!(run_context.get_context("bcs-run-2").await.unwrap().terminal);
}

#[tokio::test]
async fn chat_abort_provider_rejects_ids_outside_the_requested_scope() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .set_delivery_target(
            "bot-driver",
            support::FakeRegistryService::provider_target("bot-driver"),
        )
        .await;
    support
        .bot_delivery
        .set_provider_abort_run_ids(vec!["outside-run".to_string()])
        .await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    register_active_run_with_downstream(
        &run_context,
        "bcs-run-1",
        "provider-run-1",
        "group-1",
        "session-provider",
        "bot-driver",
        BotRunTransportOwner::HttpProvider {
            provider_id: "provider-1".to_string(),
            provider_bot_ref: "bot-driver".to_string(),
        },
    )
    .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context.clone());

    let outcome = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "session-provider".to_string(),
            bot_id: "bot-driver".to_string(),
            run_id: None,
        })
        .await
        .unwrap();

    assert!(!outcome.aborted);
    assert!(outcome.aborted_run_ids.is_empty());
    assert_eq!(outcome.failures.len(), 1);
    assert_eq!(outcome.failures[0].run_id, "outside-run");
    assert!(!run_context.get_context("bcs-run-1").await.unwrap().terminal);
}
