//! Ordinary owner-lineage compatibility alongside the protected caller chain.
use super::*;

#[tokio::test]
async fn an_owner_lineage_row_registers_its_association_and_stays_ordinary() {
    // Spec §7.2.1 / review batch-3 §3.1: an ordinary row carrying the
    // trusted `caller_lineage` marker — an Owner target of a lease-admitted
    // Human turn in a caller group — registers its delegation-only root
    // association from the handoff-parked identity at StartSend, while its
    // execution stays the ORDINARY transport (no protected branch, no
    // container ensure, no proof).
    let managed = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let message_id = "owner-lineage";
    let session_id = format!("session-{message_id}");
    let mut admission = admit_caller(managed.as_ref(), message_id);
    // Rewind the caller marks: this row is an OWNER target — only the
    // delegation-only association marker, exactly the shape the §7.2.1
    // seam produces for a caller-disabled routed target.
    admission.targets[0].semantic_projection_json = json!({
        "version": 1,
        "caller_lineage": "delegation_only",
    });
    let admitted = managed.admit(admission).await.expect("owner row admits ordinarily");
    let owner_row = &admitted.deliveries[0];

    // Park the identity for the owner scope the way web_send's make_scope
    // does (env by resolve_env_str; group/session/message/the target).
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let scope = bcs_service_api::application::caller_handoff::HandoffScope {
        env: bcs_config::resolve_env_str(),
        session_id: session_id.clone(),
        group_id: Some("group".into()),
        source_message_id: message_id.into(),
        target_bot_id: "bot".into(),
    };
    let now = now_ms();
    let ingress = AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
        HumanCallerAuthSource::HttpAuth,
        now,
        bcs_config::resolve_env_str(),
    )
    .expect("valid ingress");
    handoff
        .reserve(&scope, 30_000, now)
        .await
        .expect("reserve");
    handoff
        .publish(
            &scope,
            ingress,
            QueuedAdmissionReceipt::new(owner_row.delivery_id.clone(), owner_row.state.state_version, now),
        )
        .await
        .expect("publish");

    // The composition: an ordinary runtime whose caller_dispatch pipeline
    // carries ONLY the lineage registry (no observe, no managed commit —
    // none of that is on the owner path).
    let gateway = Arc::new(RecordingGateway::default());
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let lineage_index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            Arc::new(RecordingSender {
                ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:not-the-facts-one".into() },
                sends: std::sync::atomic::AtomicUsize::new(0),
                histories: std::sync::Mutex::new(Vec::new()),
            }) as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_root_lineage(lineage_index.clone()),
    );
    let ordinary = Arc::new(OrdinaryIo::default());
    let runtime = DeliveryRuntime {
        policy: None,
        service: managed.clone(),
        preparation: ordinary.clone(),
        transport: ordinary.clone(),
        config: DeliveryRuntimeConfig {
            max_safe_retries: 0,
            pause_dispatch: false,
            bots: std::collections::BTreeMap::from([(
                "bot".into(),
                DeliveryRuntimePolicy { max_running: 2, min_send_interval_ms: 0 },
            )]),
            tick: Duration::from_millis(5),
            expiry_tick: Duration::from_millis(50),
            io_timeout: Duration::from_secs(1),
            run_timeout: Duration::from_secs(30),
            cancel_timeout: Duration::from_secs(1),
            startup_recovery_grace: Duration::from_secs(2),
            max_tasks: 2,
            max_abort_tasks: 1,
        },
        caller_dispatch: Some(pipeline),
    };
    let (stop, shutdown) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(runtime.run(shutdown));
    let started = wait_row(&managed, message_id, |row| {
        matches!(row.state.status, Status::Dispatching | Status::Running)
            && row.run_id.is_some()
    })
    .await;
    stop.send(true).unwrap();
    let _ = tokio::time::timeout(Duration::from_secs(2), task).await;

    // The ordinary transport delivered the row (never the protected path).
    assert!(
        !ordinary.sent.lock().await.is_empty(),
        "the owner row rides the ordinary transport"
    );
    // The delegation-only association registered against the row's real
    // run, bound to this Human's subject — bounded by the identity window
    // and the message's group/session/turn boundary (§7.2.1).
    let run_id = started.run_id.clone().unwrap();
    let lineage = lineage_index.current(&run_id, now_ms()).expect("the delegation-only association registered");
    assert_eq!(lineage.root_run_id, run_id);
    assert_eq!(lineage.delegation_depth, 0);
    assert_eq!(lineage.message_turn_id, message_id);
    assert_eq!(lineage.group_id, "group");
    assert_eq!(lineage.session_id, session_id);
    assert_eq!(lineage.delegate_subject, "S12345");

    // The identity is consumed (one-way): a second registration read for
    // the same scope never hands it out again.
    let sealed = handoff
        .claim(
            &scope,
            bcs_service_api::application::caller_handoff::HandoffClaim::new(
                started.delivery_id.clone(),
                0,
                Some(run_id.clone()),
                started.attempt_no,
                now_ms(),
            ),
        )
        .await
        .unwrap_err();
    assert!(
        matches!(sealed, bcs_service_api::application::caller_handoff::HandoffError::AlreadyClaimed),
        "the association claim consumed the identity exactly once"
    );
}
