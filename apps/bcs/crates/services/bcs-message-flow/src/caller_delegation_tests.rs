use super::*;
use crate::{
    MemoryBotRunContextStore,
    caller_lineage::{RootLineage, RootLineageIndex},
    managed_delivery::ManagedMessageDelivery,
};
use bcs_domain::{DeliveryType, NewMessage, SenderType, message_delivery::DeliveryFlowKind};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent;
use bcs_service_api::port::{
    chat_run::{BotRunContext, BotRunContextPort},
    repo::message_delivery::*,
};
use bcs_service_api::{ChatEventState, DeliveryTransitionCommand, ManagedMessageDeliveryService};
use serde_json::json;
use std::sync::Arc;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

async fn fixture(
    caller: bool,
    live: bool,
) -> (
    crate::BcsMessageFlow,
    BotEventCommand,
    Arc<MemoryBotRunContextStore>,
) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let managed = Arc::new(ManagedMessageDelivery::new(Arc::new(
        MemoryMessageRepo::new(),
    )));
    let now = chrono::Utc::now().timestamp_millis();
    managed
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: "message".into(),
            flow_kind: DeliveryFlowKind::Group,
            now_ms: now,
            expire_at_ms: None,
            event: None,
            message: NewMessage {
                visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
                audience: None,
                group_id: "group-1".into(),
                session_id: "session".into(),
                sender_id: "human".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content: json!({"text":"task"}),
                client_msg_id: None,
                owner_bot_id: None,
                created_at: now as u64,
                run_id: String::new(),
            },
            targets: vec![DeliveryAdmissionTarget {
                run_id: None,
                rejection: None,
                target_bot_id: "bot-driver".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: json!({"version":1, "caller_required":caller}),
            }],
        })
        .await
        .unwrap();
    let row = managed.snapshot(Some("session")).await.unwrap().remove(0);
    let row = managed
        .transition(DeliveryTransitionCommand {
            caller_dispatch_commit: None,
            caller_cancel: None,
            caller_terminal: None,
            delivery_id: row.delivery_id,
            expected_state_version: row.state.state_version,
            event: DeliveryLifecycleEvent::StartSend,
            now_ms: now,
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: None,
            deadline_at_ms: Some(now + 60_000),
        })
        .await
        .unwrap();
    let run = row.run_id.unwrap();
    let runs = Arc::new(MemoryBotRunContextStore::default());
    runs.put_context(BotRunContext {
        run_id: run.clone(),
        bot_id: "bot-driver".into(),
        group_id: "group-1".into(),
        bcs_session_id: Some("session".into()),
        deadline_ms: (now + 60_000) as u64,
        terminal: false,
    })
    .await;
    let index = Arc::new(RootLineageIndex::new(8));
    if live {
        assert!(index.register(
            &run,
            RootLineage {
                root_run_id: run.clone(),
                environment: "env-caller".into(),
                delegation_depth: 0,
                attempt: 1,
                message_turn_id: "message".into(),
                group_id: "group-1".into(),
                session_id: "session".into(),
                history_cutoff_seq: 0,
                expires_at_ms: now + 60_000,
                identity_subject: "opaque".into(),
                identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
                delegate_subject: "opaque".into(),
                parent_execution: false,
            },
            now
        ));
    }
    let flow = crate::BcsMessageFlow::new(
        support.group,
        support.routing,
        support.registry,
        support.bot_delivery,
        support.frontend_delivery,
    )
    .with_managed_deliveries(managed)
    .with_bot_run_context(runs.clone())
    .with_caller_derivation(index);
    let event = BotEventCommand {
        bot_id: "bot-driver".into(),
        run_id: run,
        group_id: "group-1".into(),
        event_type: "agent".into(),
        event_payload: json!({}),
        state: ChatEventState::Delta,
        bcs_session_id: Some("session".into()),
    };
    (flow, event, runs)
}

#[tokio::test]
async fn lost_identity_is_not_downgraded_but_ordinary_tasks_are_unchanged() {
    let (flow, event, _) = fixture(true, false).await;
    assert!(
        prepare_assignment(&flow, &event, "call", "bot-observer")
            .await
            .is_err()
    );
    let (flow, event, _) = fixture(false, false).await;
    assert!(
        prepare_assignment(&flow, &event, "call", "bot-observer")
            .await
            .unwrap()
            .is_none()
    );
}

#[tokio::test]
async fn real_parent_metadata_and_turn_budget_are_reused() {
    let (flow, event, _) = fixture(true, true).await;
    for n in 0..16 {
        let assignment = prepare_assignment(&flow, &event, &format!("call-{n}"), "bot-observer")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(assignment.parent_run_id, event.run_id);
        assert_eq!(assignment.invocation_id, format!("call-{n}"));
    }
    assert!(
        prepare_assignment(&flow, &event, "overflow", "bot-observer")
            .await
            .is_err()
    );
    assert!(
        prepare_assignment(&flow, &event, "call-0", "different-target")
            .await
            .is_err()
    );
}

#[tokio::test]
async fn terminal_or_wrong_scope_parent_cannot_delegate() {
    let (flow, mut event, runs) = fixture(true, true).await;
    event.bcs_session_id = Some("foreign".into());
    assert!(
        prepare_assignment(&flow, &event, "wrong-scope", "bot-observer")
            .await
            .is_err()
    );
    event.bcs_session_id = Some("session".into());
    let mut context = runs.get_context(&event.run_id).await.unwrap();
    context.terminal = true;
    runs.put_context(context).await;
    assert!(
        prepare_assignment(&flow, &event, "terminal", "bot-observer")
            .await
            .is_err()
    );
}

#[tokio::test]
async fn a_legacy_run_without_managed_facts_stays_ordinary_but_never_borrows_lineage() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let event = BotEventCommand {
        bot_id: "bot-driver".into(),
        run_id: "legacy-run".into(),
        group_id: "group-1".into(),
        event_type: "agent".into(),
        event_payload: json!({}),
        state: ChatEventState::Delta,
        bcs_session_id: Some("session".into()),
    };
    let without_lineage = crate::BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );
    assert!(
        prepare_assignment(&without_lineage, &event, "legacy-call", "bot-observer")
            .await
            .unwrap()
            .is_none()
    );

    let now = chrono::Utc::now().timestamp_millis();
    let index = Arc::new(RootLineageIndex::new(8));
    assert!(index.register(
        "legacy-run",
        RootLineage {
            root_run_id: "legacy-run".into(),
            environment: "local".into(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: "legacy-turn".into(),
            group_id: "group-1".into(),
            session_id: "session".into(),
            history_cutoff_seq: 0,
            expires_at_ms: now + 60_000,
            identity_subject: "opaque".into(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "opaque".into(),
            parent_execution: false,
        },
        now
    ));
    let with_lineage = crate::BcsMessageFlow::new(
        support.group,
        support.routing,
        support.registry,
        support.bot_delivery,
        support.frontend_delivery,
    )
    .with_caller_derivation(index);
    assert!(
        prepare_assignment(&with_lineage, &event, "lineage-call", "bot-observer")
            .await
            .is_err()
    );
}

// ---- review-VII P1: Workflow node parents --------------------------------

async fn workflow_fixture(
    live: bool,
    context: bool,
) -> (crate::BcsMessageFlow, BotEventCommand) {
    // A WORKFLOW node parent: the node run has NO Managed row — its trusted
    // facts are the runtime lineage the orchestration dispatch registered
    // (§15.89 S1) plus the live run context the collaboration runtime
    // published. This fixture reproduces exactly the review's shape.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let now = chrono::Utc::now().timestamp_millis();
    let runs = Arc::new(MemoryBotRunContextStore::default());
    let index = Arc::new(RootLineageIndex::new(8));
    let event = BotEventCommand {
        bot_id: "bot-driver".into(),
        run_id: "sm-node-run-1".into(),
        group_id: "group-1".into(),
        event_type: "agent".into(),
        event_payload: json!({}),
        state: ChatEventState::Delta,
        bcs_session_id: Some("session".into()),
    };
    if live {
        assert!(index.register(
            "sm-node-run-1",
            RootLineage {
                root_run_id: "sm-instance-1".into(),
                environment: "env-caller".into(),
                delegation_depth: 1,
                attempt: 3,
                message_turn_id: "sm-instance-1:000-panel".into(),
                group_id: "group-1".into(),
                session_id: "session".into(),
                history_cutoff_seq: 7,
                expires_at_ms: now + 60_000,
                identity_subject: "instance-subject".into(),
                identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
                delegate_subject: "instance-subject".into(),
                parent_execution: false,
            },
            now,
        ));
    }
    if context {
        runs.put_context(BotRunContext {
            run_id: "sm-node-run-1".into(),
            bot_id: "bot-driver".into(),
            group_id: "group-1".into(),
            bcs_session_id: Some("session".into()),
            deadline_ms: (now + 60_000) as u64,
            terminal: false,
        })
        .await;
    }
    let flow = crate::BcsMessageFlow::new(
        support.group,
        support.routing,
        support.registry,
        support.bot_delivery,
        support.frontend_delivery,
    )
    .with_bot_run_context(runs)
    .with_caller_derivation(index);
    (flow, event)
}

#[tokio::test]
async fn workflow_node_parents_derive_without_a_managed_row() {
    // Review-VII P1: the coordination A→B echo from a WORKFLOW node's
    // assignee previously died on "no Managed parent" — the exact gap the
    // review reproduced. With a live node lineage + live run context, the
    // facts resolve: parent = the node run, ATTEMPT = the node lineage's
    // recorded attempt (the Material Ledger key), bot from the context;
    // the invocation marker reserves on the node's own turn budget.
    let (flow, event) = workflow_fixture(true, true).await;
    let assignment = prepare_assignment(&flow, &event, "call-7", "bot-observer")
        .await
        .expect("workflow node parent derives")
        .expect("assignment");
    assert_eq!(assignment.parent_run_id, "sm-node-run-1");
    assert_eq!(assignment.parent_attempt, 3, "the node lineage's own attempt");
    assert_eq!(assignment.parent_bot_id, "bot-driver");
    assert_eq!(assignment.invocation_id, "call-7");
    // The replay of the SAME echo id fails on the reserved invocation — no
    // double minting.
    assert!(prepare_assignment(&flow, &event, "call-7", "bot-observer").await.is_err());
}

#[tokio::test]
async fn a_lineage_without_its_live_context_refuses_closed() {
    let (flow, event) = workflow_fixture(true, false).await;
    assert!(
        prepare_assignment(&flow, &event, "call-8", "bot-observer")
            .await
            .is_err(),
        "lineage alone never mints delegation facts"
    );
}

#[tokio::test]
async fn a_workflow_context_with_no_lineage_stays_an_ordinary_task() {
    let (flow, event) = workflow_fixture(false, true).await;
    assert!(
        prepare_assignment(&flow, &event, "call-9", "bot-observer")
            .await
            .expect("resolves")
            .is_none(),
        "no lineage → no caller derivation, the task rides ordinary"
    );
}
