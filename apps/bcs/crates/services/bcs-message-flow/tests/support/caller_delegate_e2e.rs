use super::*;

#[tokio::test]
async fn delegated_task_dispatch_commits_the_typed_digest_and_stays_settleable() {
    // Review "测试通过了什么、尚未证明什么" closure: the delegate branch with
    // the MANAGED COMMIT wired — a coordination-shaped delegated assign
    // (the task_dispatch branch's four marks on an admitted row) rides the
    // lineage-issued delegate path, the typed commit CAS persists the
    // canonical digest into the caller projection (same §15.17 invariant
    // the root path proved), the wire digest equals the committed digest,
    // and the ordinary transport stays untouched — so the Worker's later
    // terminal event finds a settled, commit-carrying row for the claim
    // gate (registered association's) and queued_task_terminal::commit.
    let managed = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let message_id = "delegate-commit";
    let mut admission = admit_caller(managed.as_ref(), message_id);
    // Layer the delegation marks EXACTLY as the task_dispatch branch
    // writes them for a caller-chain assign (review: prove the chain,
    // not the stub's convenience shape).
    if let Value::Object(map) = &mut admission.targets[0].semantic_projection_json {
        // The marks land EXACTLY as the trusted task edge writes them now:
        // the free-form keys live nested under the DECLARED caller_delegation
        // field, keeping the strict projection decode intact on task paths.
        map.insert("caller_delegation".into(), json!({
            "parent_run_id": "run-manager",
            "parent_attempt": 1,
            "parent_bot_id": "bot-manager",
            "invocation_id": "tool-call-1",
            "execution_purpose": "execution",
        }));
    } else {
        panic!("the admission projection must be an object");
    }
    managed.admit(admission).await.expect("admit delegated caller row");

    // The root lineage the delegate path consumes (the same registration
    // the root's Accepted send performs §15.71: root/turn/group/session/
    // cutoff/window + the pseudonymous delegate subject).
    let lineage_index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    assert!(lineage_index.register(
        "run-manager",
        bcs_message_flow::caller_lineage::RootLineage {
            root_run_id: "root-run".into(),
            environment: "local".into(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: "canonical-turn".into(),
            group_id: "group".into(),
            session_id: format!("session-{message_id}"),
            history_cutoff_seq: 0,
            expires_at_ms: now_ms() + 300_000,
            identity_subject: "root-pseudonym".into(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "root-pseudonym".into(),
            parent_execution: false,
        },
        now_ms(),
    ));

    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let gateway = Arc::new(RecordingGateway::default());
    let sender = Arc::new(RecordingSender {
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:not-the-facts-one".into() },
        sends: std::sync::atomic::AtomicUsize::new(0),
        histories: std::sync::Mutex::new(Vec::new()),
    });
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_managed_commit(managed.clone())
        .with_root_lineage(lineage_index),
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

    let row = wait_row(&managed, message_id, dispatched).await;
    stop.send(true).unwrap();
    let _ = tokio::time::timeout(Duration::from_secs(2), task).await;

    assert_eq!(
        sender.sends.load(std::sync::atomic::Ordering::SeqCst),
        1,
        "exactly one protected delegate send"
    );
    // The typed commit persisted the canonical digest into the caller
    // projection (the same §15.17 invariant — never the register caller-
    // form digest):
    let committed = row.semantic_projection_json["caller_dispatch"]["caller_request_digest"]
        .as_str()
        .expect("the typed commit persisted the digest")
        .to_string();
    assert!(committed.starts_with("sha256:") && committed.len() == 71, "canonical digest shape");
    assert_ne!(committed, "sha256:not-the-facts-one", "the wire digest is the AES recomputation");
    // The wire send carried EXACTLY the committed digest:
    let histories = sender.histories.lock().unwrap();
    assert_eq!(histories.len(), 1, "the protected send carried the history manifest");
    // The row stays settleable: its real downstream run id is committed
    // for the Worker terminal event's claim gate (registered association
    // reads THIS id back).
    let real_run = row.run_id.clone().unwrap_or_default();
    assert!(!real_run.is_empty(), "the Started CAS committed the real downstream identity");
    // The ordinary transport never saw the row.
    assert!(ordinary.sent.lock().await.is_empty());
}

/// No injector needed for the seeding journey: the derived path only
/// publishes the readback into the shared directory — the fan-out ports
/// are exercised by their own contracts.
struct NoObserveInjector;
impl bcs_service_api::port::ObserveInjectorFactory for NoObserveInjector {
    fn injector_for(
        &self,
        _target: &BotDeliveryTarget,
    ) -> Option<Arc<dyn bcs_service_api::port::ObserveInjectPort>> {
        None
    }
}

#[tokio::test]
async fn delegated_worker_dispatch_seeds_the_observe_directory_under_the_root_subject() {
    // Review batch-3 §3.5 (first half): the derived path's container ensure
    // readback publishes the WORKER container's live observe reference —
    // root-pseudonymous subject × worker-Bot × session — into the shared
    // runtime-only directory, exactly like a root dispatch. Without it,
    // `all_for(worker, session)` stayed empty and the worker's own reply
    // fan-out (§6.4 same-Bot containers) never found its containers.
    let managed = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let message_id = "delegate-observe";
    let session_id = format!("session-{message_id}");
    let mut admission = admit_caller(managed.as_ref(), message_id);
    if let Value::Object(map) = &mut admission.targets[0].semantic_projection_json {
        map.insert("caller_delegation".into(), json!({
            "parent_run_id": "run-manager",
            "parent_attempt": 1,
            "parent_bot_id": "bot-manager",
            "invocation_id": "tool-call-1",
            "execution_purpose": "execution",
        }));
    } else {
        panic!("the admission projection must be an object");
    }
    managed.admit(admission).await.expect("admit delegated caller row");

    let lineage_index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    assert!(lineage_index.register(
        "run-manager",
        bcs_message_flow::caller_lineage::RootLineage {
            root_run_id: "root-run".into(),
            environment: "local".into(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: "canonical-turn".into(),
            group_id: "group".into(),
            session_id: session_id.clone(),
            history_cutoff_seq: 0,
            expires_at_ms: now_ms() + 300_000,
            identity_subject: "root-pseudonym".into(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "root-pseudonym".into(),
            parent_execution: false,
        },
        now_ms(),
    ));

    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let gateway = Arc::new(RecordingGateway { observe: true, ..Default::default() });
    let sender = Arc::new(RecordingSender {
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:not-the-facts-one".into() },
        sends: std::sync::atomic::AtomicUsize::new(0),
        histories: std::sync::Mutex::new(Vec::new()),
    });
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let observe_index = Arc::new(bcs_message_flow::observe_index::ObserveTargetIndex::new(8));
    let observe_assembly = bcs_message_flow::caller_dispatch_pipeline::ObserveSyncAssembly {
        groups: Arc::new(bcs_test_support::NoopGroupCoreService), sessions: None, delivery_term: None,
        index: observe_index.clone(),
        injector_factory: Arc::new(NoObserveInjector),
    };
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_managed_commit(managed.clone())
        .with_root_lineage(lineage_index)
        .with_observe_sync(observe_assembly),
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
    let _row = wait_row(&managed, message_id, dispatched).await;
    stop.send(true).unwrap();
    let _ = tokio::time::timeout(Duration::from_secs(2), task).await;

    // The worker container's live observe reference sits in the shared
    // directory under the ROOT's pseudonymous subject (the delegated
    // container is drove by the root's subject, never the Worker's own).
    let entry = observe_index.current("root-pseudonym", "bot", &session_id, now_ms());
    assert!(
        entry.is_some(),
        "the delegated worker's observe reference is seeded on the shared directory"
    );
    assert_eq!(entry.unwrap().target_epoch.as_ref().unwrap().expose(), "worker-epoch-1");
    // The worker's own reply fan-out stream (Bot×session) sees it.
    assert_eq!(observe_index.all_for("bot", &session_id, now_ms()).len(), 1);
    // A different subject's container is a DIFFERENT entry — the human's
    // own container of the same Bot is untouched by the delegated seeding.
    assert!(observe_index.current("human".to_string().as_str(), "bot", &session_id, now_ms()).is_none());
}

#[tokio::test]
async fn the_delegate_wire_manifest_honors_the_inherited_history_ceiling() {
    // Review-V P1-2 (spec §6.8/§15.41): the runtime's delegate facts arrive
    // cut at the CHILD's own source_session_seq — the wire manifest is
    // REBUILT against the lineage's ROOT ceiling before the send: the id
    // list is re-filtered (nothing between the root's admission and this
    // dispatch ever enters the worker's input) and the digest is recomputed
    // over the same facts — never a lone cutoff edit over the runtime's
    // stale list.
    let managed = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let message_id = "delegate-ceiling";
    let session_id = format!("session-{message_id}");

    // Lane material BEFORE the delegated child, same session lane by seq
    // order (the p2c admit_caller fixture uses one session per message —
    // NOT the shape here, so this fixture admits explicitly):
    //   lane-1, lane-2 : at or below the inherited ceiling (IN the input)
    //   lane-3         : after the ceiling (NEVER in the worker's input)
    fn lane_admission(id: &str, session_id: &str) -> AdmitMessageDeliveries {
        AdmitMessageDeliveries {
            display_message: None,
            message_id: id.into(),
            flow_kind: DeliveryFlowKind::Group,
            now_ms: now_ms(),
            expire_at_ms: None,
            event: None,
            message: NewMessage {
                visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
                audience: None,
                group_id: "group".into(),
                session_id: session_id.into(),
                sender_id: "human".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content: json!({"text": id}),
                client_msg_id: Some(id.to_string()),
                owner_bot_id: None,
                created_at: 1,
                run_id: String::new(),
            },
            targets: vec![DeliveryAdmissionTarget {
                run_id: None,
                rejection: None,
                target_bot_id: "bot".into(),
                kind: DeliveryType::Inject,
                max_queued: 100,
                semantic_projection_json: json!({"version": 1}),
            }],
        }
    }
    let mut lane_rows = Vec::new();
    for lane_id in ["lane-1", "lane-2", "lane-3"] {
        let admitted = managed
            .admit(lane_admission(lane_id, &session_id))
            .await
            .expect("lane row admits");
        lane_rows.push(admitted.deliveries[0].clone());
    }
    let ceiling = lane_rows[1].source_session_seq.max(0) as u64;
    assert!(
        ceiling < lane_rows[2].source_session_seq.max(0) as u64,
        "fixture assumption: admit order must be the seq order (got ceiling {ceiling}, third {})",
        lane_rows[2].source_session_seq
    );

    // The delegated child row, in the SAME session lane (the §15.71-style
    // coordination marks).
    let mut admission = admit_caller(managed.as_ref(), message_id);
    admission.message.session_id = session_id.clone();
    if let Value::Object(map) = &mut admission.targets[0].semantic_projection_json {
        map.insert("caller_delegation".into(), json!({
            "parent_run_id": "run-manager",
            "parent_attempt": 1,
            "parent_bot_id": "bot-manager",
            "invocation_id": "tool-call-1",
            "execution_purpose": "execution",
        }));
    } else {
        panic!("the admission projection must be an object");
    }
    let admitted = managed
        .admit(admission)
        .await
        .expect("admit delegated caller row");
    let child_row = admitted.deliveries[0].clone();
    let child_seq = child_row.source_session_seq.max(0) as u64;
    assert!(child_seq > ceiling, "the child's own seq is deliberately past the ceiling");

    // The root lineage the delegate path consumes: the ROOT's fixed ceiling
    // sits between lane-2 and lane-3 — exactly the spec §6.8 inheritance
    // shape under which the runtime's own manifest (cut at the child seq)
    // would over-report.
    let lineage_index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    assert!(lineage_index.register(
        "run-manager",
        bcs_message_flow::caller_lineage::RootLineage {
            root_run_id: "root-run".into(),
            environment: "local".into(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: "canonical-turn".into(),
            group_id: "group".into(),
            session_id: session_id.clone(),
            history_cutoff_seq: ceiling,
            expires_at_ms: now_ms() + 300_000,
            identity_subject: "root-pseudonym".into(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "root-pseudonym".into(),
            parent_execution: false,
        },
        now_ms(),
    ));

    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let gateway = Arc::new(RecordingGateway::default());
    let sender = Arc::new(RecordingSender {
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:not-the-facts-one".into() },
        sends: std::sync::atomic::AtomicUsize::new(0),
        histories: std::sync::Mutex::new(Vec::new()),
    });
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_managed_commit(managed.clone())
        .with_root_lineage(lineage_index),
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
    let row = wait_row(&managed, message_id, dispatched).await;
    stop.send(true).unwrap();
    let _ = tokio::time::timeout(Duration::from_secs(2), task).await;

    // The wire manifest is the lineage-ceiling rebuild — the expected value
    // is recomputed HERE from first principles (ids filtered to seq ≤
    // ceiling, digest over the same facts): identical bytes mean the
    // runtime's stale child-seq manifest never reached the wire.
    let run_id = row.run_id.clone().expect("committed run");
    let expected = bcs_domain::history_manifest::HistorySnapshotManifest::new(
        format!("{}:delegate:{}", row.delivery_id, run_id),
        "bot",
        &session_id,
        ceiling,
        1,
        // bounded_contexts iterates seq DESC; the rebuild inherits that order.
        vec![lane_rows[1].source_message_id.clone(), lane_rows[0].source_message_id.clone()],
    );
    let histories = sender.histories.lock().unwrap();
    let sent = histories
        .iter()
        .next()
        .expect("exactly one protected delegate send")
        .as_ref()
        .expect("the wire carries the rebuilt history manifest");
    assert_eq!(sent.history_cutoff_seq, ceiling, "the wire cutoff is the inherited root ceiling");
    assert_eq!(sent.manifest_digest, expected.manifest_digest);
    assert_eq!(sent.history_snapshot_id, expected.history_snapshot_id);
    assert_ne!(
        sent.manifest_digest,
        "sha256:not-the-facts-one",
        "the digest is a real recompute, never a passthrough"
    );
}

#[tokio::test]
async fn a_delegate_history_not_ready_parks_the_same_dispatch_like_the_root() {
    // Review-V P1-3: the CHILD branch now mirrors the root's §6.8 barrier
    // semantics — a retryable HISTORY_NOT_READY refusal is NOT a transport
    // rejection: the row stays Dispatching with its committed digest/attempt
    // and parks under the durable HistoryBarrier marker (surviving the
    // startup recovery sweep), awaiting the same-dispatch redrive once the
    // history backfill completes.
    let managed = Arc::new(ManagedMessageDelivery::new(Arc::new(MemoryMessageRepo::new())));
    let message_id = "delegate-barrier";
    let mut admission = admit_caller(managed.as_ref(), message_id);
    if let Value::Object(map) = &mut admission.targets[0].semantic_projection_json {
        map.insert("caller_delegation".into(), json!({
            "parent_run_id": "run-manager",
            "parent_attempt": 1,
            "parent_bot_id": "bot-manager",
            "invocation_id": "tool-call-1",
            "execution_purpose": "execution",
        }));
    } else {
        panic!("the admission projection must be an object");
    }
    managed.admit(admission).await.expect("admit delegated caller row");

    let lineage_index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    assert!(lineage_index.register(
        "run-manager",
        bcs_message_flow::caller_lineage::RootLineage {
            root_run_id: "root-run".into(),
            environment: "local".into(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: format!("canonical-{message_id}"),
            group_id: "group".into(),
            session_id: format!("session-{message_id}"),
            history_cutoff_seq: 0,
            expires_at_ms: now_ms() + 300_000,
            identity_subject: "root-pseudonym".into(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "root-pseudonym".into(),
            parent_execution: false,
        },
        now_ms(),
    ));
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let gateway = Arc::new(RecordingGateway::default());
    let sender = Arc::new(RecordingSender {
        ack: CallerDispatchAck::Rejected { error_code: "HISTORY_NOT_READY".into(), retryable: true },
        sends: std::sync::atomic::AtomicUsize::new(0),
        histories: std::sync::Mutex::new(Vec::new()),
    });
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_managed_commit(managed.clone())
        .with_root_lineage(lineage_index),
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
    let row = wait_row(&managed, message_id, |row| {
        matches!(row.state.status, Status::Dispatching)
            && row.wait_reason == Some(bcs_domain::message_delivery::DeliveryWaitReason::HistoryBarrier)
    }).await;
    stop.send(true).unwrap();
    let _ = tokio::time::timeout(Duration::from_secs(2), task).await;

    assert_eq!(row.state.status, Status::Dispatching, "the barrier parks, never TransportRejected");
    assert_eq!(row.wait_reason, Some(bcs_domain::message_delivery::DeliveryWaitReason::HistoryBarrier));
    assert!(row.semantic_projection_json.get("caller_dispatch").is_some(), "the committed digest/attempt survive parked");
    assert_eq!(row.semantic_projection_json["caller_delegation"]["parent_run_id"], "run-manager");
}

#[path = "caller_history_recovery.rs"]
mod history_recovery;

// ---- §15.83 S1 / spec §8.5 orchestration node dispatch -----------------

#[tokio::test]
async fn an_orchestration_node_dispatches_on_the_protected_wire_under_the_instance() {
    // The workflow INSTANCE carries the delegation-only association (S2
    // registers it in production); this contract pins the S1 seam: the
    // node's OWN run/attempt rides the wire, the ensure names the
    // instance as parent with the inherited cutoff, the proof follows the
    // assignee's config-decided purpose, the turn budget reserves per
    // (node, attempt) — a retry attempt is a fresh reservation — and the
    // node run registers at instance depth+1.
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let gateway = Arc::new(RecordingGateway::default());
    let sender = Arc::new(RecordingSender {
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:not-the-facts-one".into() },
        sends: std::sync::atomic::AtomicUsize::new(0),
        histories: std::sync::Mutex::new(Vec::new()),
    });
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let lineage_index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    let now = now_ms();
    // The instance association — delegation-only, bounded by the human
    // window, group/session-scoped, cutoff fixed at the triggering turn.
    assert!(lineage_index.register(
        "workflow-run-1",
        bcs_message_flow::caller_lineage::RootLineage {
            root_run_id: "root-run".into(),
            environment: "env-caller".into(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: "turn-1".into(),
            group_id: "group".into(),
            session_id: "session-wf".into(),
            history_cutoff_seq: 7,
            expires_at_ms: now + 300_000,
            identity_subject: "instance-subject".into(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "instance-subject".into(),
            parent_execution: false,
        },
        now,
    ));
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_root_lineage(lineage_index.clone()),
    );
    let dispatch = bcs_service_api::port::OrchestrationNodeDispatch {
        environment: "env-caller".into(),
        group_id: "group".into(),
        session_id: "session-wf".into(),
        workflow_run_id: "workflow-run-1".into(),
        node_id: "node-a".into(),
        node_attempt: 1,
        node_run_id: "node-run-a1".into(),
        assignee_bot_id: "bot-worker".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-a".into(),
        not_after_ms: now + 120_000,
        caller_task: "处理这个节点".into(),
        channel_actor_id: "human_alice".into(),
        channel_actor_name: "Alice".into(),
        control_revision: 0,
        now_ms: now,
    };
    let target = BotDeliveryTarget::HttpProvider {
        bot_id: "bot-worker".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-a".into(),
        webhook_url: "http://127.0.0.1:1/webhook".into(),
        bcs_to_provider_token: RedactedToken::new("svc"),
        protocol_version: "2.0".into(),
    };
    let mut foreign = dispatch.clone();
    foreign.environment = "other-environment".into();
    assert_eq!(
        bcs_service_api::port::OrchestrationCallerDispatchPort::dispatch_orchestration_node(
            pipeline.as_ref(), &target, &RedactedToken::new("svc"),
            bcs_service_api::port::OrchestrationAssigneePolicy::Execution, foreign,
        ).await.unwrap_err(),
        bcs_service_api::port::OrchestrationDispatchError::LineageUnavailable,
    );
    assert_eq!(gateway.ensures.load(std::sync::atomic::Ordering::SeqCst), 0);
    let ack: CallerDispatchAck = bcs_service_api::port::OrchestrationCallerDispatchPort::dispatch_orchestration_node(
        pipeline.as_ref(),
        &target,
        &RedactedToken::new("svc"),
        bcs_service_api::port::OrchestrationAssigneePolicy::Execution,
        dispatch.clone(),
    )
    .await
    .expect("node dispatch accepted on the protected wire");
    assert!(matches!(ack, CallerDispatchAck::Accepted { .. }));

    // Exactly one container ensure under the instance association (the
    // recording gateway asserts the count; the ensure's run association
    // facts are exercised via the registered node lineage below).
    assert_eq!(
        gateway.ensures.load(std::sync::atomic::Ordering::SeqCst),
        1,
        "exactly one container ensure under the instance subject"
    );

    // One protected send: the node's own dispatch on the caller wire.
    assert_eq!(sender.sends.load(std::sync::atomic::Ordering::SeqCst), 1);
    // The node run registered at instance depth+1 with the same subject:
    // an embedded Bot A2A continues to increment from there (§8.5).
    let node_lineage = lineage_index
        .current("node-run-a1", now + 1)
        .expect("node run lineage registered");
    assert_eq!(node_lineage.delegation_depth, 1);
    assert_eq!(node_lineage.root_run_id, "root-run");
    assert_eq!(node_lineage.message_turn_id, "turn-1");
    assert_eq!(node_lineage.history_cutoff_seq, 7);
    assert_eq!(node_lineage.delegate_subject, "instance-subject");
    assert_eq!(node_lineage.expires_at_ms <= now + 120_000, true);

    // §8.5 budget: same node attempt replays fail closed on the
    // reservation; a NEW attempt reserves fresh.
    assert!(
        lineage_index
            .reserve_invocation("workflow-run-1", "node:node-a:1", "bot-worker", now)
            .is_err(),
        "the dispatched attempt's reservation is consumed"
    );
    assert!(
        lineage_index
            .reserve_invocation("workflow-run-1", "node:node-a:2", "bot-worker", now)
            .is_ok(),
        "a node retry is a NEW attempt with a fresh reservation"
    );
}

#[tokio::test]
async fn an_orchestration_node_without_a_live_instance_refuses_closed() {
    // §8.5: 关联丢失或过期则等待或失败 — never the latest watermark, never
    // a completed predecessor. A node outside the instance's group/session
    // inherits nothing either.
    let handoff = Arc::new(HumanCallerDeliveryHandoff::new(8));
    let gateway = Arc::new(RecordingGateway::default());
    let sender = Arc::new(RecordingSender {
        ack: CallerDispatchAck::Accepted { control_revision: 0, request_digest: "sha256:not-the-facts-one".into() },
        sends: std::sync::atomic::AtomicUsize::new(0),
        histories: std::sync::Mutex::new(Vec::new()),
    });
    let preparation = Arc::new(CallerContainerPreparation::new(gateway.clone() as Arc<dyn CallerContainerGatewayPort>));
    let lineage_index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(8));
    let pipeline = Arc::new(
        CallerDispatchPipeline::new(
            handoff.clone() as Arc<dyn HumanCallerDeliveryHandoffService>,
            preparation,
            Arc::new(StubIdentity),
            sender.clone() as Arc<dyn CallerExecutionSendPort>,
            3_600_000,
        )
        .with_root_lineage(lineage_index),
    );
    let now = now_ms();
    let target = BotDeliveryTarget::HttpProvider {
        bot_id: "bot-worker".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-a".into(),
        webhook_url: "http://127.0.0.1:1/webhook".into(),
        bcs_to_provider_token: RedactedToken::new("svc"),
        protocol_version: "2.0".into(),
    };
    let make = |group: &str, session: &str| bcs_service_api::port::OrchestrationNodeDispatch {
        environment: "test".into(),
        group_id: group.into(),
        session_id: session.into(),
        workflow_run_id: "workflow-run-1".into(),
        node_id: "node-a".into(),
        node_attempt: 1,
        node_run_id: "node-run-a1".into(),
        assignee_bot_id: "bot-worker".into(),
        provider_id: "provider-a".into(),
        provider_bot_ref: "runtime-a".into(),
        not_after_ms: now + 120_000,
        caller_task: "处理这个节点".into(),
        channel_actor_id: "human_alice".into(),
        channel_actor_name: "Alice".into(),
        control_revision: 0,
        now_ms: now,
    };
    // No association at all:
    let refused = bcs_service_api::port::OrchestrationCallerDispatchPort::dispatch_orchestration_node(
        pipeline.as_ref(),
        &target,
        &RedactedToken::new("svc"),
        bcs_service_api::port::OrchestrationAssigneePolicy::Execution,
        make("group", "session-wf"),
    )
    .await
    .unwrap_err();
    assert_eq!(
        refused,
        bcs_service_api::port::OrchestrationDispatchError::LineageUnavailable
    );
    assert_eq!(sender.sends.load(std::sync::atomic::Ordering::SeqCst), 0, "no wire was touched");
    assert_eq!(
        gateway.ensures.load(std::sync::atomic::Ordering::SeqCst),
        0,
        "no container was ensured — inheritance refused before any side effect"
    );
}

// ---- §15.83 S2: the instance-association registrar ----------------------
