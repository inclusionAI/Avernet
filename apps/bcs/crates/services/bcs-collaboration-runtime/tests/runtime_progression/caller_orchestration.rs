//! Caller orchestration & S2 human-input rearm tests carried from the
//! pre-split monolithic runtime_progression shell (branch rebase).
use super::*;

#[tokio::test]
async fn caller_orchestration_wired_reports_composition_capability() {
    use bcs_service_api::CollaborationRuntimeService;
    let group_store = Arc::new(GroupStore::new());
    group_store
        .upsert(state_machine_test_group())
        .await
        .expect("seed group");
    let sessions = test_sessions();
    let store = Arc::new(MemoryCollaborationStore::new());
    let bare = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group_store.clone(),
        sessions.clone(),
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    );
    assert!(!CollaborationRuntimeService::caller_orchestration_wired(&bare));

    #[derive(Default)]
    struct CountingRegistrar;
    #[async_trait::async_trait]
    impl bcs_service_api::port::OrchestrationInstanceRegistrar for CountingRegistrar {
        async fn register_instance(
            &self,
            _association: bcs_service_api::port::OrchestrationInstanceAssociation,
        ) -> Result<bool, bcs_service_api::port::OrchestrationDispatchError> {
            Ok(true)
        }
        async fn release_instance(
            &self,
            _workflow_run_id: &str,
        ) -> Result<bool, bcs_service_api::port::OrchestrationDispatchError> {
            Ok(false)
        }
    }

    let wired = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store.clone(),
        group_store,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    )
    .with_caller_instance_registrar(Arc::new(CountingRegistrar));
    assert!(CollaborationRuntimeService::caller_orchestration_wired(&wired));
}

// §15.83 S2 human-input rearm (spec 2026-10-22, G1 entry B): after a
// HumanInput node consumes a human response, the runtime refreshes the
// run's delegation-only instance association window with the trusted
// human ingress carried on the command.

struct RecordingInstanceRegistrar {
    registrations: StdMutex<Vec<bcs_service_api::port::OrchestrationInstanceAssociation>>,
    releases: StdMutex<Vec<String>>,
}

struct RefusingRegistrar;

async fn human_input_rearm_setup(
    registrar: Arc<dyn bcs_service_api::port::OrchestrationInstanceRegistrar>,
) -> (CollaborationRuntime, String, String) {
    let group_store = Arc::new(GroupStore::new());
    let group = session_collaboration_group(GroupStrategy::ManagerWorker);
    group_store
        .upsert(group.clone())
        .await
        .expect("seed manager-worker group");
    let mut participants = group.participants.clone();
    let mut human = Participant::human("human_1001", ParticipantRole::Observer);
    human.mode = Some(ParticipantMode::Present);
    human.message_view_scope = MessageViewScope::Participant;
    participants.push(human);
    let sessions = test_sessions();
    let session = sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants,
                message_visibility_version: 1,
                ..Default::default()
            },
        })
        .await
        .expect("seed manager-worker session")
        .session;
    let store = Arc::new(MemoryCollaborationStore::new());
    let runtime = test_runtime!(
        store.clone(),
        store.clone(),
        store.clone(),
        store,
        group_store,
        sessions,
        Arc::new(RecordingDelivery::default()),
        noop_judge(),
    )
    .with_caller_instance_registrar(registrar)
    .with_message_repo(Arc::new(MemoryMessageRepo::new()))
    .with_result_publisher(Arc::new(RecordingResultPublisher::default()));
    (runtime, group.id, session.id)
}

#[tokio::test]
async fn human_input_response_refused_association_refresh_still_consumes_the_reply() {
    let (runtime, group_id, session_id) =
        human_input_rearm_setup(Arc::new(RefusingRegistrar)).await;

    let started = runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session_id.clone(),
            caller_bot_id: "driver-bot".to_string(),
            definition_yaml: one_shot_human_input_authoring_yaml(),
            participant_bindings: BTreeMap::new(),
            opening_message: None,
            input: Value::Null,
            judge_available: false, caller_association: None,
        })
        .await
        .expect("start one-shot HumanInput run");
    assert_eq!(
        started.view.nodes[0].status,
        StateMachineNodeStatus::Running
    );

    // The registrar refuses the refresh (a full index / cross-subject
    // submission): the human response's primary purpose is node data, so
    // the consumption must still succeed and the node must complete.
    let outcome = runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: group_id,
            session_id: Some(session_id),
            caller_actor_id: "human_1001".to_string(),
            content: "确认".to_string(),
            source: HumanResponseSource::Http,
            caller_ingress: Some(human_input_rearm_ingress()),
        })
        .await
        .expect("refused association refresh must not fail the human reply");
    let HandleSessionHumanInputOutcome::Consumed { response } = outcome else {
        panic!("assigned Human reply must stay consumed despite refused rearm");
    };
    assert_eq!(response.node.status, StateMachineNodeStatus::Completed);
    assert_eq!(response.node.artifact_text.as_deref(), Some("确认"));
    assert_eq!(response.node.responded_by.as_deref(), Some("human_1001"));
    assert_eq!(response.run.status, StateMachineRunStatus::Completed);
}


#[tokio::test]
async fn run_terminal_cancels_the_caller_instance_association_release() {
    // Audit P1 2026-10-25: a run converging to a terminal state MUST release
    // its caller instance association — the bounded runtime-only lineage
    // ledger tracks in-flight runs, not run history. Abort is representative:
    // the same hook covers complete and fail.
    let registrar = Arc::new(RecordingInstanceRegistrar::new());
    let (runtime, _group_id, session_id) =
        human_input_rearm_setup(registrar.clone()).await;

    use bcs_service_api::CollaborationRuntimeService as _;
    let started = runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session_id.clone(),
            caller_bot_id: "driver-bot".to_string(),
            definition_yaml: one_shot_human_input_authoring_yaml(),
            participant_bindings: BTreeMap::new(),
            opening_message: None,
            input: Value::Null,
            judge_available: false, caller_association: None,
        })
        .await
        .expect("start run");
    assert!(
        registrar.releases.lock().expect("registrar lock").is_empty(),
        "no release before the terminal event"
    );

    runtime
        .cancel_state_machine_run(bcs_service_api::application::collaboration_runtime::CancelStateMachineRunCommand {
            run_id: started.view.run.run_id.clone(),
            reason: Some("terminal_release_test".to_string()),
        })
        .await
        .expect("cancel converges the run terminal");

    let releases = registrar.releases.lock().expect("registrar lock").clone();
    assert_eq!(
        releases,
        vec![started.view.run.run_id.clone()],
        "the aborted run's caller association must release exactly once"
    );
}

fn human_input_rearm_ingress() -> AuthenticatedHumanCallerIngress {
    let now = bcs_protocol::now_ms() as i64;
    AuthenticatedHumanCallerIngress::new(
        HumanActor {
            actor_id: "human_1001".into(),
            staff_no: "1001".into(),
        },
        HumanCallerAuthSource::HttpAuth,
        now - 1_000,
        bcs_config::resolve_env_str(),
    )
    .expect("valid trusted ingress")
}


#[tokio::test]
async fn human_input_response_re_arms_the_run_caller_instance_association() {
    let registrar = Arc::new(RecordingInstanceRegistrar::new());
    let (runtime, group_id, session_id) =
        human_input_rearm_setup(registrar.clone()).await;

    let started = runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session_id.clone(),
            caller_bot_id: "driver-bot".to_string(),
            definition_yaml: one_shot_human_input_authoring_yaml(),
            participant_bindings: BTreeMap::new(),
            opening_message: None,
            input: Value::Null,
            judge_available: false, caller_association: None,
        })
        .await
        .expect("start one-shot HumanInput run");
    assert_eq!(
        started.view.nodes[0].status,
        StateMachineNodeStatus::Running
    );

    let outcome = runtime
        .handle_session_human_input(HandleSessionHumanInputCommand {
            group_id: group_id.clone(),
            session_id: Some(session_id.clone()),
            caller_actor_id: "human_1001".to_string(),
            content: "确认".to_string(),
            source: HumanResponseSource::Http,
            caller_ingress: Some(human_input_rearm_ingress()),
        })
        .await
        .expect("route manager-worker chat reply to one-shot HumanInput");
    let HandleSessionHumanInputOutcome::Consumed { response } = outcome else {
        panic!("assigned Human reply must be consumed by the one-shot run");
    };
    assert_eq!(response.node.status, StateMachineNodeStatus::Completed);
    assert_eq!(response.run.status, StateMachineRunStatus::Completed);

    let registered = registrar.registrations.lock().expect("registrar lock");
    assert_eq!(registered.len(), 1);
    assert_eq!(registered[0].workflow_run_id, started.view.run.run_id);
    assert_eq!(registered[0].group_id, group_id);
    assert_eq!(registered[0].session_id, response.run.session_id);
    assert_eq!(
        registered[0].message_turn_id,
        format!("{}:000-panel", started.view.run.run_id)
    );
    assert_eq!(registered[0].environment, bcs_config::resolve_env_str());
    // Run-budget fact, not an identity window: the association carries the
    // instance budget (24h) — bounded, so the shared lineage ledger can age
    // out strays even when a terminal release path misses.
    assert_eq!(
        registered[0].expires_at_ms,
        registered[0].now_ms + 24 * 60 * 60 * 1000
    );
    assert_eq!(registered[0].ingress.actor().staff_no, "1001");
    assert_eq!(
        registered[0].history_cutoff_seq, 0,
        "rearm mirror must keep the §15.90 hardcoded ceiling"
    );
    assert!(
        registered[0].now_ms > 0,
        "rearm mirror must stamp a real clock"
    );
}

impl RecordingInstanceRegistrar {
    fn new() -> Self {
        Self {
            registrations: StdMutex::new(Vec::new()),
            releases: StdMutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl bcs_service_api::port::OrchestrationInstanceRegistrar for RefusingRegistrar {
    async fn register_instance(
        &self,
        _association: bcs_service_api::port::OrchestrationInstanceAssociation,
    ) -> Result<bool, bcs_service_api::port::OrchestrationDispatchError> {
        Ok(false)
    }

    async fn release_instance(
        &self,
        _workflow_run_id: &str,
    ) -> Result<bool, bcs_service_api::port::OrchestrationDispatchError> {
        Ok(false)
    }
}

#[async_trait]
impl bcs_service_api::port::OrchestrationInstanceRegistrar for RecordingInstanceRegistrar {
    async fn register_instance(
        &self,
        association: bcs_service_api::port::OrchestrationInstanceAssociation,
    ) -> Result<bool, bcs_service_api::port::OrchestrationDispatchError> {
        self.registrations
            .lock()
            .expect("registrar lock")
            .push(association);
        Ok(true)
    }

    async fn release_instance(
        &self,
        workflow_run_id: &str,
    ) -> Result<bool, bcs_service_api::port::OrchestrationDispatchError> {
        self.releases
            .lock()
            .expect("registrar lock")
            .push(workflow_run_id.to_string());
        Ok(true)
    }
}

// ---------------------------------------------------------------------------
// Owner→caller relay for orchestration submissions (design 2026-10-30):
// a BOT-submitted caller-aware run whose adapter association is absent
// redeems the session's captured identity at submission. Some(capture) →
// the instance association registers along the §8.5 bridge verbatim; None
// refuses the whole run fail-closed; no execution assignees → the relay
// seam stays untouched.
// ---------------------------------------------------------------------------

struct AllProtectedResolver;
#[async_trait]
impl bcs_service_api::port::OrchestrationCallerPolicyResolver for AllProtectedResolver {
    async fn resolve(
        &self,
        _assignee_bot_id: &str,
        _environment: &str,
    ) -> bcs_service_api::port::OrchestrationAssigneeResolution {
        bcs_service_api::port::OrchestrationAssigneeResolution::Protected(
            bcs_service_api::port::OrchestrationAssigneePolicy::Execution,
        )
    }
}

struct OrdinaryResolver;
#[async_trait]
impl bcs_service_api::port::OrchestrationCallerPolicyResolver for OrdinaryResolver {
    async fn resolve(
        &self,
        _assignee_bot_id: &str,
        _environment: &str,
    ) -> bcs_service_api::port::OrchestrationAssigneeResolution {
        bcs_service_api::port::OrchestrationAssigneeResolution::Ordinary
    }
}

struct CountingRedeemer {
    calls: StdMutex<Vec<(String, Vec<String>)>>,
    yields: bool,
}
impl CountingRedeemer {
    fn yielding() -> Arc<Self> {
        Arc::new(Self { calls: StdMutex::new(Vec::new()), yields: true })
    }
    fn refusing() -> Arc<Self> {
        Arc::new(Self { calls: StdMutex::new(Vec::new()), yields: false })
    }
}
#[async_trait]
impl bcs_service_api::port::CallerRelayRedeemerPort for CountingRedeemer {
    async fn redeem_submission(
        &self,
        _environment: &str,
        _group_id: &str,
        _session_id: &str,
        submitted_by_bot: &str,
        execution_assignees: &[String],
        _now_ms: i64,
    ) -> Option<bcs_service_api::port::CallerRelayCapture> {
        self.calls
            .lock()
            .expect("redeemer lock")
            .push((submitted_by_bot.to_string(), execution_assignees.to_vec()));
        if !self.yields {
            return None;
        }
        Some(bcs_service_api::port::CallerRelayCapture {
            ingress: human_input_rearm_ingress(),
            // Fixed sentinel: the registration must carry the capture's own
            // window verbatim, and a constant makes that assertable.
            expires_at_ms: 123_456_789,
        })
    }
}

#[tokio::test]
async fn a_bot_submitted_caller_run_relays_the_session_capture_into_the_instance_association() {
    let registrar = Arc::new(RecordingInstanceRegistrar::new());
    let (runtime, _group_id, session_id) = human_input_rearm_setup(registrar.clone()).await;
    let redeemer = CountingRedeemer::yielding();
    let runtime = runtime_assembly_with_relay(runtime, redeemer.clone()).await;
    runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session_id.clone(),
            caller_bot_id: "driver-bot".to_string(),
            definition_yaml: one_shot_authoring_yaml(),
participant_bindings: BTreeMap::from([(
                "writer".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["driver-bot".to_string()],
                    extensions: Default::default(),
                },
            )]),
            opening_message: None,
            input: Value::Null,
            judge_available: false, caller_association: None,
        })
        .await
        .expect("bot-submitted caller run redeems and starts");
    let calls = redeemer.calls.lock().expect("redeemer lock");
    assert_eq!(calls.len(), 1, "exactly one submission-level redemption");
    assert_eq!(calls[0].0, "driver-bot", "the submitting bot is the relay from-side");
    assert!(!calls[0].1.is_empty(), "at least one execution assignee was gated");
    let registrations = registrar.registrations.lock().expect("registrar lock");
    assert_eq!(registrations.len(), 1, "the relayed association registered along the §8.5 bridge");
    assert_eq!(registrations[0].session_id, session_id);
    assert_eq!(registrations[0].expires_at_ms, 123_456_789, "the capture's own window travels verbatim");
    assert!(!registrations[0].workflow_run_id.trim().is_empty());
    assert!(registrations[0].workflow_run_id.ends_with(":000-panel") || !registrations[0].message_turn_id.trim().is_empty());
}

#[tokio::test]
async fn a_bot_submission_without_a_live_capture_refuses_the_whole_run() {
    let registrar = Arc::new(RecordingInstanceRegistrar::new());
    let (runtime, _group_id, session_id) = human_input_rearm_setup(registrar.clone()).await;
    let runtime = runtime_assembly_with_relay(runtime, CountingRedeemer::refusing()).await;
    let refused = runtime
        .start_session_state_machine_run(StartSessionStateMachineRunCommand {
            session_id: session_id.clone(),
            caller_bot_id: "driver-bot".to_string(),
            definition_yaml: one_shot_authoring_yaml(),
participant_bindings: BTreeMap::from([(
                "writer".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["driver-bot".to_string()],
                    extensions: Default::default(),
                },
            )]),
            opening_message: None,
            input: Value::Null,
            judge_available: false, caller_association: None,
        })
        .await
        .expect_err("a refused relay redemption must fail the whole submission");
    assert!(refused.to_string().contains("caller relay refused"), "{refused}");
    let registrations = registrar.registrations.lock().expect("registrar lock");
    assert!(registrations.is_empty(), "nothing registers on refusal");
}

/// Relay tests need the full-shape caller assembly (resolver + registrar)
/// before the redeemer will attach — the same discipline the production
/// wiring enforces. The dispatch port is a no-op: these tests assert the
/// SUBMISSION-time effects, not the node wire.
#[path = "../../../../test-support/message_flow_contract/registry_routing_fakes.rs"]
mod registry_routing_fakes;

struct NoopOrchestrationDispatch;
#[async_trait]
impl bcs_service_api::port::OrchestrationCallerDispatchPort for NoopOrchestrationDispatch {
    async fn dispatch_orchestration_node(
        &self,
        _target: &BotDeliveryTarget,
        _service_credential: &bcs_domain::RedactedToken,
        _policy: bcs_service_api::port::OrchestrationAssigneePolicy,
        _dispatch: bcs_service_api::port::OrchestrationNodeDispatch,
    ) -> Result<bcs_service_api::port::CallerDispatchAck, bcs_service_api::port::OrchestrationDispatchError> {
        Ok(bcs_service_api::port::CallerDispatchAck::Accepted {
            control_revision: 0,
            request_digest: "sha256:not-the-facts-one".into(),
        })
    }
}

async fn runtime_assembly_with_relay(
    runtime: CollaborationRuntime,
    redeemer: Arc<CountingRedeemer>,
) -> CollaborationRuntime {
    let registry = Arc::new(registry_routing_fakes::FakeRegistryService::default());
    registry
        .set_delivery_target(
            "driver-bot",
            registry_routing_fakes::FakeRegistryService::provider_target("driver-bot"),
        )
        .await;
    let runtime = runtime
        .with_bot_registry(registry)
        .with_caller_node_dispatch(
            Arc::new(NoopOrchestrationDispatch) as Arc<dyn bcs_service_api::port::OrchestrationCallerDispatchPort>,
            Arc::new(AllProtectedResolver) as Arc<dyn bcs_service_api::port::OrchestrationCallerPolicyResolver>,
        )
        .with_caller_relay_redeemer(redeemer as Arc<dyn bcs_service_api::port::CallerRelayRedeemerPort>);
    let _ = OrdinaryResolver;
    runtime
}
