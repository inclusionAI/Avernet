use super::*;

#[tokio::test]
async fn history_dual_write_retains_judge_failed_attempt_after_retry_and_loop_progression() {
    let mut h = Harness::new(&["again", "done"]).await;
    let messages = Arc::new(MemoryMessageRepo::new());
    h.runtime = h
        .runtime
        .with_history_persistence(true)
        .with_message_repo(messages.clone());
    let run = h.start(loop_yaml(2, true, false, 2), false).await.view.run;
    let plan = h.plan(&run.run_id).await;
    let first = iteration_id(&plan, 1);
    // Lose the caller after durable acceptance, then fail and retry the attempt.
    let node = h
        .store
        .get_node_run(&run.run_id, &first)
        .await
        .unwrap()
        .unwrap();
    let key = bcs_domain::state_machine_history::output_message_key(&run.run_id, &first, 0);
    let payload = bcs_service_api::port::repo::StateMachineHistoryPayload {
        schema_version: 1,
        message_id: bcs_domain::state_machine_history::physical_message_id(&key),
        message: NewMessage {
            group_id: run.group_id.clone(),
            session_id: run.session_id.clone(),
            run_id: run.run_id.clone(),
            sender_id: node.assignee_bot_id.unwrap(),
            sender_type: bcs_domain::SenderType::Bot,
            message_type: "state_machine_output".into(),
            content: json!({"text":"rejected attempt", "metadata":{"state_machine":{"history_schema_version":1,"run_id":run.run_id,"node_id":first,"attempt":0,"event":"output","execution":plan.node_metadata[&first]}}}),
            client_msg_id: Some(key),
            owner_bot_id: None,
            visibility_domain: MessageVisibilityDomain::StateMachine,
            audience: Some(MessageAudience::FullOnly),
            created_at: run.created_at + 1,
        },
    };
    h.store
        .commit_eventful_transition(
            bcs_service_api::port::repo::StateMachineEventfulTransition::AcceptHistory(
                bcs_service_api::port::repo::AcceptStateMachineHistory {
                    payload,
                    mutation:
                        bcs_service_api::port::repo::StateMachineHistoryMutation::AcceptOutput {
                            judging: true,
                        },
                    event: None,
                },
            ),
        )
        .await
        .unwrap();
    let now = bcs_protocol::now_ms();
    let claim = h
        .store
        .claim_node_judging(
            &run.run_id,
            &first,
            0,
            "failed-judge".into(),
            now,
            now + 10000,
        )
        .await
        .unwrap()
        .unwrap();
    h.store
        .commit_eventful_transition(
            bcs_service_api::port::repo::StateMachineEventfulTransition::FinishJudge(
                bcs_service_api::FinishStateMachineJudge {
                    claim,
                    result: bcs_service_api::StateMachineJudgeResult::Failed {
                        error: "judge unavailable".into(),
                        action: bcs_service_api::StateMachineFailureAction::Retry,
                        details: json!({}),
                    },
                    completed_at_ms: now + 1,
                    event: None,
                },
            ),
        )
        .await
        .unwrap();
    let page = h
        .runtime
        .recover_state_machine_progression(None, 32)
        .await
        .unwrap();
    assert!(page.failures.is_empty(), "{:?}", page.failures);
    h.finish(1, &run, "accepted retry").await;
    h.finish(2, &run, "next iteration").await;
    let history = h
        .runtime
        .get_state_machine_session_history(&run.session_id, 100, None)
        .await
        .unwrap()
        .unwrap();
    for text in ["rejected attempt", "accepted retry", "next iteration"] {
        assert_eq!(
            history
                .messages
                .iter()
                .filter(|m| m.content == text)
                .count(),
            1,
            "{text}"
        );
    }
    let identities = |page: &bcs_service_api::SessionHistoryResult| page.messages.iter()
        .map(|m| (m.id.clone(), (m.content.clone(), m.metadata.clone())))
        .collect::<BTreeMap<_, _>>();
    h.runtime = h.runtime.with_history_read_source(bcs_config_api::StateMachineHistoryReadSource::Messages);
    let persisted = h.runtime.get_state_machine_session_history(&run.session_id, 100, None).await.unwrap().unwrap();
    assert_eq!(identities(&history), identities(&persisted), "Loop iterations and rejected attempt survive cutover");
    h.runtime = h.runtime.with_history_read_source(bcs_config_api::StateMachineHistoryReadSource::Runtime);
    let rollback = h.runtime.get_state_machine_session_history(&run.session_id, 100, None).await.unwrap().unwrap();
    assert_eq!(identities(&persisted), identities(&rollback), "rollback retains all attempts");
}
