use super::*;

#[tokio::test]
async fn a_human_triggered_run_registers_its_instance_association_once_and_idempotently() {
    // Spec §8.5: the instance association is born AT SUBMISSION — depth 0,
    // the instance's own root, the triggering turn, the submission ceiling
    // and the trusted subject. A re-submission of the SAME run id only
    // refreshes the window (idempotent); a bot-only or invalid association
    // never进入 the index.
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let gateway = Arc::new(RecordingGateway::default());
    let sender = Arc::new(RecordingSender {
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:not-the-facts-one".into() },
        sends: std::sync::atomic::AtomicUsize::new(0),
        histories: std::sync::Mutex::new(Vec::new()),
    });
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let lineages = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    let now = now_ms();
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_root_lineage(lineages.clone()),
    );
    let association = bcs_service_api::port::OrchestrationInstanceAssociation {
        workflow_run_id: "sm-run-42".into(),
        environment: "env".into(),
        group_id: "group".into(),
        session_id: "session-wf".into(),
        message_turn_id: "sm-run-42:000-panel".into(),
        history_cutoff_seq: 7,
        ingress: AuthenticatedHumanCallerIngress::new(
            HumanActor { actor_id: "human_1001".into(), staff_no: "1001".into() },
            HumanCallerAuthSource::HttpAuth,
            now - 1000, now + 300_000, "env",
        ).unwrap(),
        expires_at_ms: now + 300_000,
        now_ms: now,
    };
    // First registration lands.
    assert!(
        bcs_service_api::port::OrchestrationInstanceRegistrar::register_instance(
            pipeline.as_ref(),
            association.clone(),
        )
        .await
        .expect("association registers")
    );
    // A duplicate submission of the same run refreshes the same window —
    // registered (not duplicated): the index keeps ONE entry per run.
    let refreshed = bcs_service_api::port::OrchestrationInstanceRegistrar::register_instance(
        pipeline.as_ref(),
        association,
    )
    .await
    .expect("re-registration is accepted");
    assert!(refreshed);
    let lineage = lineages.current("sm-run-42", now).expect("instance association live");
    assert_eq!(lineage.root_run_id, "sm-run-42");
    assert_eq!(lineage.delegation_depth, 0);
    assert_eq!(lineage.message_turn_id, "sm-run-42:000-panel");
    assert_eq!(lineage.group_id, "group");
    assert_eq!(lineage.session_id, "session-wf");
    assert_eq!(lineage.history_cutoff_seq, 7);
    assert_eq!(lineage.delegate_subject, "1001");

    // Inverted window / empty subject refuse closed BEFORE entering.
    let mut broken = bcs_service_api::port::OrchestrationInstanceAssociation {
        workflow_run_id: "sm-run-43".into(),
        environment: "env".into(),
        group_id: "group".into(),
        session_id: "session-wf".into(),
        message_turn_id: "turn-x".into(),
        history_cutoff_seq: 0,
        ingress: AuthenticatedHumanCallerIngress::new(
            HumanActor { actor_id: "human_1002".into(), staff_no: "1002".into() },
            HumanCallerAuthSource::HttpAuth,
            now - 2000, now - 1000, "env",
        ).unwrap(),
        expires_at_ms: now + 300_000,
        now_ms: now,
    };
    assert_eq!(
        bcs_service_api::port::OrchestrationInstanceRegistrar::register_instance(
            pipeline.as_ref(),
            broken.clone(),
        )
        .await
        .unwrap_err(),
        bcs_service_api::port::OrchestrationDispatchError::InvalidInput
    );
    broken.expires_at_ms = now - 1;
    assert_eq!(
        bcs_service_api::port::OrchestrationInstanceRegistrar::register_instance(
            pipeline.as_ref(),
            broken,
        )
        .await
        .unwrap_err(),
        bcs_service_api::port::OrchestrationDispatchError::InvalidInput
    );
    assert!(lineages.current("sm-run-43", now).is_none(), "refused facts never enter");
}
