use super::*;
    use crate::caller_admission::CallerAdmissionDeps;
    use crate::caller_delivery_handoff::HumanCallerDeliveryHandoff;
    use bcs_service_api::application::caller_handoff::{
        HandoffIdentityReadiness, HandoffScope, HumanCallerDeliveryHandoffService,
    };
    use bcs_service_api::application::caller_identity::{
        AuthenticatedHumanCallerIngress, HumanCallerAuthSource,
    };
    use bcs_service_api::application::principal::HumanActor;
    use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoPort;
    use bcs_message_store::MemoryMessageRepo;
    use std::sync::Arc;
    use bcs_service_api::application::message_delivery::ManagedMessageDeliveryService;

    #[path = "../../../../test-support/message_flow_contract_support.rs"]
    mod support;

    struct CallerEnabledControlPlane;

    #[async_trait::async_trait]
    impl bcs_service_api::BotControlPlaneCoreService for CallerEnabledControlPlane {
        async fn get_caller_configuration(
            &self,
            _bot_id: &str,
            _env: &str,
        ) -> ServiceResult<Option<bcs_service_api::BotCallerConfiguration>> {
            Ok(Some(bcs_service_api::BotCallerConfiguration {
                snapshot: Some(bcs_service_api::CallerIdentitySnapshot {
                    source_revision: 1,
                    caller_identity: bcs_service_api::CallerIdentityConfig {
                        enabled: true,
                        minimum_isolation: bcs_service_api::CallerMinimumIsolation::CallerContainer,
                    },
                }),
            }))
        }

        async fn get_record(
            &self,
            _bot_id: &str,
            _env: &str,
        ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneRecord>> {
            unimplemented!("not needed by this test")
        }

        async fn get(
            &self,
            _bot_id: &str,
            _env: &str,
        ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
            unimplemented!("not needed by this test")
        }

        async fn get_by_ids(
            &self,
            _bot_ids: &[String],
            _env: &str,
        ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
            unimplemented!("not needed by this test")
        }

        async fn list_candidates(
            &self,
            _query: bcs_service_api::BotCandidateReadQuery,
        ) -> ServiceResult<(Vec<bcs_service_api::BotControlPlaneCandidate>, u64)> {
            unimplemented!("not needed by this test")
        }

        async fn list_by_creator(
            &self,
            _query: bcs_service_api::BotControlPlaneOwnedQuery,
        ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
            unimplemented!("not needed by this test")
        }

        async fn patch(
            &self,
            _bot_id: &str,
            _env: &str,
            _patch: bcs_service_api::BotControlPlanePatch,
        ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
            unimplemented!("not needed by this test")
        }
    }

    async fn fixture() -> (BcsMessageFlow, Arc<HumanCallerDeliveryHandoff>) {
        let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let repo: Arc<dyn MessageDeliveryRepoPort> = Arc::new(MemoryMessageRepo::new());
        let service = Arc::new(crate::managed_delivery::ManagedMessageDelivery::new(repo));
        let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
        let mut flow = BcsMessageFlow::new(
            support.group,
            support.routing,
            support.registry,
            support.bot_delivery,
            support.frontend_delivery,
        );
        flow.caller_admission = Some(Arc::new(CallerAdmissionDeps {
            handoff: handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            admission: service as Arc<dyn ManagedMessageDeliveryService>,
            control_plane: Arc::new(CallerEnabledControlPlane),
            reserve_ttl_ms: 30_000,
        }));
        (flow, handoff)
    }

    fn ingress() -> AuthenticatedHumanCallerIngress {
        let now = chrono::Utc::now().timestamp_millis();
        AuthenticatedHumanCallerIngress::new(
            HumanActor { actor_id: "human_alice".into(), staff_no: "alice".into() },
            HumanCallerAuthSource::HttpAuth,
            now,
            &bcs_config::resolve_env_str(),
        ).expect("valid ingress")
    }

    fn scope() -> HandoffScope {
        HandoffScope {
            env: bcs_config::resolve_env_str(),
            session_id: "session".into(),
            group_id: Some("group".into()),
            source_message_id: "task-result:1".into(),
            target_bot_id: "bot-driver".into(),
        }
    }

    fn identity() -> ResultLegIdentity {
        let scope = scope();
        let ingress = ingress();
        ResultLegIdentity { scope, ingress }
    }

    #[tokio::test]
    async fn publish_missing_row_releases_the_reserved_scope() {
        let (flow, handoff) = fixture().await;
        let parked = identity();
        let scope = parked.scope.clone();
        let now = chrono::Utc::now().timestamp_millis();
        publish_result_leg_identity(&flow, &parked, &[], "task-result:1", "bot-driver", now).await;
        assert_eq!(handoff.identity_readiness(&scope).await, HandoffIdentityReadiness::Gone);
    }

    #[tokio::test]
    async fn release_result_identity_ignores_none_and_cleans_some() {
        let (flow, handoff) = fixture().await;
        release_result_leg_identity(&flow, None).await;
        let parked = identity();
        let scope = parked.scope.clone();
        release_result_leg_identity(&flow, Some(&parked)).await;
        assert_eq!(handoff.identity_readiness(&scope).await, HandoffIdentityReadiness::Gone);
    }

    #[tokio::test]
    async fn terminal_result_text_uses_failure_body_for_nonfinal() {
        let cmd = BotEventCommand {
            bot_id:"bot-driver".into(), run_id:"run".into(), group_id:"group".into(),
            bcs_session_id:Some("session".into()), state:ChatEventState::Error,
            event_type:"chat.event".into(), event_payload:serde_json::json!({"error_message":"FAILED"}),
        };
        let normalized = crate::run_reply::RunReply {
            text: String::new(), display: String::new(), method: "error".into(),
            raw_final: String::new(), source_ids: Vec::new(),
        };
        assert_eq!(terminal_result_text(&cmd, "FINAL", &normalized), "情况：FAILED");
    }


fn persisted_row(manager: &str) -> PersistedMessageDelivery {
    PersistedMessageDelivery {
        delivery_id: "delivery-id".into(),
        env: bcs_config::resolve_env_str(),
        source_message_id: "task-result:1".into(),
        target_bot_id: manager.into(),
        session_id: "session".into(),
        group_id: "group".into(),
        source_session_seq: 0,
        flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Task,
        state: bcs_domain::message_delivery::MessageDeliveryState {
            kind: bcs_domain::DeliveryType::Send,
            status: bcs_domain::message_delivery::MessageDeliveryStatus::Queued,
            state_version: 1,
            may_have_been_sent: false,
        },
        wait_reason: None,
        available_at_ms: 1,
        expire_at_ms: None,
        created_at_ms: 1,
        updated_at_ms: 1,
        run_id: Some("run".into()),
        idempotency_key: None,
        attempt_no: 1,
        request_id: None,
        send_started_at_ms: None,
        submitted_at_ms: None,
        accepted_at_ms: None,
        run_deadline_at_ms: None,
        terminal_at_ms: None,
        bound_to_delivery_id: None,
        cancel_requested_at_ms: None,
        cancel_requested_by: None,
        cancel_reason: None,
        abort_request_id: None,
        abort_started_at_ms: None,
        cancel_deadline_at_ms: None,
        last_error_code: None,
        semantic_projection_json: serde_json::Value::Null,
        transport_context_json: None,
        context_selection_json: None,
    }
}

#[tokio::test]
async fn publish_matching_row_uses_the_row_receipt() {
    let (flow, handoff) = fixture().await;
    let parked = identity();
    let scope = parked.scope.clone();
    let row = persisted_row("bot-driver");
    let now = chrono::Utc::now().timestamp_millis();
    handoff.reserve(&scope, 30_000, now).await.unwrap();
    publish_result_leg_identity(&flow, &parked, std::slice::from_ref(&row), "task-result:1", "bot-driver", now).await;
    assert_eq!(handoff.identity_readiness(&scope).await, HandoffIdentityReadiness::Published);
}

#[test]
fn failure_reason_returns_the_failed_unknown_reason() {
    let row = persisted_row("bot-driver");
    let state = bcs_domain::message_delivery::MessageDeliveryState {
        kind: bcs_domain::DeliveryType::Send,
        status: bcs_domain::message_delivery::MessageDeliveryStatus::Failed,
        state_version: 1,
        may_have_been_sent: true,
    };
    let reason = crate::task_failure::failure_reason(&row, state, None, None).expect("failed reason");
    assert_eq!(reason, (false, "任务执行失败，未提供具体原因。".to_string()));
}


fn task() -> TaskIntent {
    TaskIntent {
        timeout_s: None,
        leg: TaskLeg::Result,
        task_id: "1".into(),
        manager: "bot-driver".into(),
        worker: "bot-observer".into(),
        worker_name: "Worker".into(),
        response_mode: bcs_service_api::ChatResponseMode::Full,
        assignment_intent_id: None,
        summary: String::new(),
    }
}

#[tokio::test]
async fn protect_result_leg_ignores_empty_text() {
    let (flow, handoff) = fixture().await;
    let now = chrono::Utc::now().timestamp_millis();
    let parked = protect_result_leg(&flow, &task(), "session", "group", "  ", now).await.unwrap();
    assert!(parked.is_none(), "empty protected text stays ordinary");
    assert_eq!(handoff.identity_readiness(&scope()).await, HandoffIdentityReadiness::Gone);
}

#[tokio::test]
async fn protect_result_leg_returns_none_when_slot_is_already_published() {
    let (mut flow, handoff) = fixture().await;
    let relay = crate::caller_relay::CallerRelayState::new(600_000);
    let now = chrono::Utc::now().timestamp_millis();
    assert!(relay.ledger.record(
        &bcs_config::resolve_env_str(),
        "session",
        "turn-1",
        ingress(),
        now,
        600_000,
    ));
    flow.caller_relay = Some(relay);

    let scope = scope();
    handoff.reserve(&scope, 30_000, now).await.unwrap();
    handoff.publish(
        &scope,
        ingress(),
        bcs_service_api::application::caller_handoff::QueuedAdmissionReceipt::new(
            "existing-slot".to_string(), 1, now,
        ),
    ).await.unwrap();

    let parked = protect_result_leg(&flow, &task(), "session", "group", "RESULT", now).await.unwrap();
    assert!(parked.is_none(), "a published slot stays authoritative");
}

#[tokio::test]
async fn re_publishing_an_identical_slot_returns_fail_closed() {
    let (flow, handoff) = fixture().await;
    let parked = identity();
    let row = persisted_row("bot-driver");
    let scope = parked.scope.clone();
    let now = chrono::Utc::now().timestamp_millis();
    handoff.reserve(&scope, 30_000, now).await.unwrap();
    // first publication is successful; the second one must hit the already-published
    // error path and stay fail-closed, never silently upgrading the row.
    publish_result_leg_identity(&flow, &parked, std::slice::from_ref(&row), "task-result:1", "bot-driver", now).await;
    publish_result_leg_identity(&flow, &parked, std::slice::from_ref(&row), "task-result:1", "bot-driver", now).await;
    assert_eq!(handoff.identity_readiness(&scope).await, HandoffIdentityReadiness::Published);
}


#[test]
fn result_message_id_uses_the_stable_task_result_key() {
    assert_eq!(result_message_id("1"), "task-result:1");
    assert_eq!(result_message_id("abc"), "task-result:abc");
}


#[test]
fn fallback_ignores_non_task_rows() {
    let row = PersistedMessageDelivery {
        delivery_id: "d".into(),
        env: "local".into(),
        source_message_id: "m".into(),
        target_bot_id: "bot-driver".into(),
        session_id: "s".into(),
        group_id: "g".into(),
        source_session_seq: 0,
        flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
        state: bcs_domain::message_delivery::MessageDeliveryState {
            kind: bcs_domain::DeliveryType::Send,
            status: bcs_domain::message_delivery::MessageDeliveryStatus::Failed,
            state_version: 1,
            may_have_been_sent: false,
        },
        wait_reason: None,
        available_at_ms: 1,
        expire_at_ms: None,
        created_at_ms: 1,
        updated_at_ms: 1,
        run_id: Some("run".into()),
        idempotency_key: None,
        attempt_no: 1,
        request_id: None,
        send_started_at_ms: None,
        submitted_at_ms: None,
        accepted_at_ms: None,
        run_deadline_at_ms: None,
        terminal_at_ms: None,
        bound_to_delivery_id: None,
        cancel_requested_at_ms: None,
        cancel_requested_by: None,
        cancel_reason: None,
        abort_request_id: None,
        abort_started_at_ms: None,
        cancel_deadline_at_ms: None,
        last_error_code: None,
        semantic_projection_json: serde_json::Value::Null,
        transport_context_json: None,
        context_selection_json: None,
    };
    let state = bcs_domain::message_delivery::MessageDeliveryState {
        kind: bcs_domain::DeliveryType::Send,
        status: bcs_domain::message_delivery::MessageDeliveryStatus::Failed,
        state_version: 1,
        may_have_been_sent: false,
    };
    assert!(crate::task_failure::fallback(&row, state, 1, None, None, None).unwrap().is_none());
}


#[test]
fn failure_body_replaces_no_response_reason_with_explicit_default() {
    assert_eq!(
        crate::task_failure::failure_body(false, "[no response]", false),
        "情况：任务执行失败，未提供具体原因。"
    );
}


#[test]
fn is_task_timeout_reads_the_transport_marker() {
    let mut row = persisted_row("bot-driver");
    assert!(!crate::task_failure::is_task_timeout(&row));
    row.transport_context_json = Some(serde_json::json!({"task_timeout": true}));
    assert!(crate::task_failure::is_task_timeout(&row));
}


#[test]
fn callback_body_preserves_error_reason_and_partial_note() {
    use bcs_service_api::ChatEventState;
    let cmd = BotEventCommand {
        bot_id: "bot-driver".into(),
        run_id: "run".into(),
        group_id: "group".into(),
        bcs_session_id: Some("session".into()),
        state: ChatEventState::Error,
        event_type: "chat.event".into(),
        event_payload: serde_json::json!({"errorMessage":"WORKER_ERROR"}),
    };
    assert!(
        crate::task_failure::callback_body(&cmd, true).contains("已返回部分内容")
    );
}

#[test]
fn is_timeout_callback_requires_timeout_reason_on_aborted() {
    let mut cmd = BotEventCommand {
        bot_id: "bot-driver".into(),
        run_id: "run".into(),
        group_id: "group".into(),
        bcs_session_id: Some("session".into()),
        state: ChatEventState::Aborted,
        event_type: "chat.event".into(),
        event_payload: serde_json::json!({"reason": "not timeout"}),
    };
    assert!(!crate::task_failure::is_timeout_callback(&cmd));
    cmd.event_payload["reason"] = serde_json::json!(crate::task_failure::TASK_TIMEOUT_REASON);
    assert!(crate::task_failure::is_timeout_callback(&cmd));
    cmd.state = ChatEventState::Error;
    assert!(!crate::task_failure::is_timeout_callback(&cmd));
}


#[test]
fn entry_intent_preserves_task_store_facts() {
    let entry = crate::task_store::new_task_entry(
        "task-1".to_string(),
        "group".to_string(),
        Some("session".to_string()),
        "bot-driver".to_string(),
        "bot-worker".to_string(),
        Some("Worker".to_string()),
        1,
        bcs_service_api::ChatResponseMode::Full,
    );
    let intent = crate::task_failure::entry_intent(&entry);
    assert_eq!(intent.task_id, "task-1");
    assert_eq!(intent.manager, "bot-driver");
    assert_eq!(intent.worker, "bot-worker");
    assert_eq!(intent.worker_name, "Worker");
}
