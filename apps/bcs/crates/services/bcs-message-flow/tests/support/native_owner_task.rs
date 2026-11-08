//! Application → real Managed/DeliveryRuntime/protected pipeline regression seam.
use super::*;
use bcs_message_flow::caller_dispatch_pipeline::CallerDispatchPipeline;
use bcs_message_flow::caller_lineage::RootLineageIndex;
use bcs_message_flow::delivery_runtime::{DeliveryRuntime, DeliveryRuntimeConfig, DeliveryRuntimePolicy};
use bcs_service_api::application::caller_container::CallerContainerPreparation;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent;
use bcs_service_api::port::chat_run::BotRunContext;
use bcs_service_api::{BotEventCommand, DeliveryTransitionCommand, WebSendCommand};
use bcs_domain::message_delivery::PersistedMessageDelivery;
use std::time::Duration;

fn now_ms() -> i64 { chrono::Utc::now().timestamp_millis() }
#[path = "protected_dispatch_io.rs"]
mod protected_io;
use protected_io::*;

struct NativeFixture {
    f: Fixture,
    lineage: Arc<RootLineageIndex>,
    pipeline: Arc<CallerDispatchPipeline>,
    sender: Arc<StubSender>,
    identity: Arc<StubIdentity>,
    gateway: Arc<StubGateway>,
    io: Arc<RecordingIo>,
    parent: PersistedMessageDelivery,
    stop: Option<tokio::sync::watch::Sender<bool>>,
    runtime: Option<tokio::task::JoinHandle<Result<(), bcs_service_api::ManagedDeliveryError>>>,
}
impl NativeFixture {
    async fn new(owner: bool) -> Self {
        let mut f = fixture(true).await;
        let flow = Arc::get_mut(&mut f.flow).unwrap();
        let deps = Arc::get_mut(flow.caller_admission.as_mut().unwrap()).unwrap();
        deps.control_plane = Arc::new(CallerEnabledControlPlane { owner_driver: owner });
        let lineage = Arc::new(RootLineageIndex::new(8));
        let sender = Arc::new(StubSender::default());
        let identity = Arc::new(StubIdentity::default());
        let gateway = Arc::new(StubGateway::default());
        let pipeline = Arc::new(CallerDispatchPipeline::new(
            f.handoff.clone(), Arc::new(CallerContainerPreparation::new(gateway.clone())),
            identity.clone(), sender.clone(), 600_000,
        ).with_root_lineage(lineage.clone()).with_managed_commit(f.service.clone()));
        let flow = Arc::get_mut(&mut f.flow).unwrap();
        flow.caller_root_lineage = Some(lineage.clone());
        flow.caller_dispatch_pipeline = Some(pipeline.clone());
        let lease = acquire_lease(&f, "human_alice").await;
        f.flow.handle_web_send(WebSendCommand {
            caller_ingress:Some(ingress("human_alice")), speaking_lease:Some(lease), caller_execution:None,
            caller:CallerContext::Human(HumanActor { actor_id:"human_alice".into(), staff_no:"S12345".into() }),
            group_id:"group-1".into(), session_id:Some(SESSION.into()), from_actor_id:"human_alice".into(),
            from_name:Some("Alice".into()), message:"ask the worker".into(), mentions:vec!["bot-driver".into()],
            attachments:None, thinking:None, idempotency_key:Some("native-owner-turn".into()),
            source_im_message_id:None, channel_sender_identity:None, sender_conn_id:None, provider_bypass_headers:vec![],
        }).await.unwrap();
        let parent = f.service.snapshot(Some(SESSION)).await.unwrap().into_iter()
            .find(|r| r.target_bot_id == "bot-driver").unwrap();
        let mut this = Self { f, lineage, pipeline, sender, identity, gateway, io:Arc::new(RecordingIo::default()), parent, stop:None, runtime:None };
        this.start_runtime();
        // Run the real Owner ordinary / Caller protected dispatch, not fabricated lineage.
        this.parent = this.drive("bot-driver", &this.parent.delivery_id).await;
        assert_eq!(this.parent.state.status, Status::Running);
        assert_eq!(this.lineage.current(this.run(), now_ms()).unwrap().parent_execution, !owner);
        // The transport fixture's accepted Run registration represents the adapter's context port.
        this.f.flow.bot_run_context.as_ref().unwrap().put_context(this.context()).await;
        this
    }
    fn run(&self) -> &str { self.parent.run_id.as_deref().unwrap() }
    fn context(&self) -> BotRunContext {
        BotRunContext { run_id:self.run().into(), bot_id:"bot-driver".into(), group_id:"group-1".into(),
            bcs_session_id:Some(SESSION.into()), deadline_ms:(now_ms()+60_000) as u64, terminal:false }
    }
    fn command(&self) -> TaskDispatchCommand {
        let mut cmd = dispatch_command();
        cmd.caller_delegation = None;
        cmd
    }
    fn start(&self, invocation: &str) -> BotEventCommand {
        BotEventCommand {
            bot_id:"bot-driver".into(), run_id:self.run().into(), group_id:"group-1".into(),
            bcs_session_id:Some(SESSION.into()), event_type:"agent".into(), state:bcs_service_api::ChatEventState::Delta,
            event_payload:json!({"stream":"tool", "data":{"phase":"start", "name":"bcs_assign_task",
                "toolCallId":invocation, "args":{"target_bot":"bot-observer", "message":"RELAY_BODY", "response_mode":"full"}}}),
        }
    }
    async fn observe(&self, invocation: &str) {
        self.f.flow.handle_bot_event(self.start(invocation)).await.unwrap();
    }
    async fn rows(&self) -> Vec<PersistedMessageDelivery> { self.f.service.snapshot(Some(SESSION)).await.unwrap() }
    fn start_runtime(&mut self) {
        let runtime = DeliveryRuntime {
            policy:None, service:self.f.service.clone(), preparation:self.io.clone(), transport:self.io.clone(),
            caller_dispatch:Some(self.pipeline.clone()), config:DeliveryRuntimeConfig {
                max_safe_retries:0, pause_dispatch:false,
                bots:["bot-driver", "bot-observer"].into_iter().map(|bot| (bot.into(), DeliveryRuntimePolicy { max_running:2, min_send_interval_ms:0 })).collect(),
                tick:Duration::from_millis(5), expiry_tick:Duration::from_millis(50),
                io_timeout:Duration::from_secs(1), run_timeout:Duration::from_secs(30),
                cancel_timeout:Duration::from_secs(1), startup_recovery_grace:Duration::from_secs(2),
                max_tasks:2, max_abort_tasks:1,
            },
        };
        let (stop, shutdown) = tokio::sync::watch::channel(false);
        self.runtime = Some(tokio::spawn(runtime.run(shutdown)));
        self.stop = Some(stop);
    }
    async fn drive(&self, target: &str, id: &str) -> PersistedMessageDelivery {
        let result = tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let row = self.rows().await.into_iter().find(|r| r.delivery_id == id).unwrap();
                if matches!(row.state.status, Status::Running | Status::Failed) { break row; }
                // Submission (ordinary or protected) is not a Bot receipt.
                let sent = self.io.sent.lock().await.iter().any(|bot| bot == target)
                    || self.sender.commands.lock().unwrap().iter().any(|cmd| cmd.association.target_bot_id == target);
                if row.state.status == Status::Dispatching && sent {
                    self.f.service.accept_run(row.request_id.as_deref().unwrap(), target, None, now_ms()).await.unwrap();
                }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        }).await;
        result.unwrap_or_else(|_| panic!("delivery {id} did not reach Running/Failed"))
    }
}

impl Drop for NativeFixture {
    fn drop(&mut self) {
        if let Some(stop) = self.stop.take() { let _ = stop.send(true); }
    }
}

#[tokio::test]
async fn human_owner_native_assign_reaches_protected_worker_transport() {
    let h = NativeFixture::new(true).await;
    assert_eq!(h.parent.semantic_projection_json["caller_lineage"], "delegation_only");
    assert_ne!(h.parent.semantic_projection_json["caller_required"], true);
    assert!(h.identity.issued.lock().unwrap().is_empty(), "Owner must not mint an execution token");
    assert!(h.gateway.targets.lock().unwrap().is_empty(), "Owner must not ensure a Caller container");
    // A newer session capture must not replace the run-bound Human origin.
    let bob = AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id:"human_bob".into(), staff_no:"S54321".into() },
        HumanCallerAuthSource::HttpAuth, 1, &resolve_env(),
    ).unwrap();
    h.f.ledger.as_ref().unwrap().record(&resolve_env(), SESSION, "newer-turn", bob, now_ms(), 60_000);
    h.observe("native-tool-call").await;
    let outcome = h.f.flow.handle_native_task_dispatch("native-request", h.command()).await.unwrap();
    let worker = h.rows().await.into_iter().find(|r| r.semantic_projection_json["task"]["task_id"] == outcome.task_id).unwrap();
    assert_eq!(h.f.handoff.identity_readiness(&worker_scope(&worker)).await, HandoffIdentityReadiness::Gone,
        "native dispatch must not consume/park the legacy session capture");
    let row = h.drive("bot-observer", &worker.delivery_id).await;
    assert_eq!(row.state.status, Status::Running);
    assert_eq!(h.sender.commands.lock().unwrap().len(), 1);
    assert_eq!(h.sender.commands.lock().unwrap()[0].association.target_bot_id, "bot-observer");
    assert_eq!(*h.io.sent.lock().await, ["bot-driver"], "Caller worker never uses ordinary content transport");
    assert_eq!(*h.identity.issued.lock().unwrap(), [Source::Human]);
    assert_eq!(h.sender.commands.lock().unwrap()[0].caller_subject_ref.expose(), "S12345");
    assert_eq!(*h.gateway.targets.lock().unwrap(), ["bot-observer"]);
    // All sixteen legacy capture redeems remain available after native dispatch.
    for _ in 0..16 { assert!(h.f.ledger.as_ref().unwrap().redeem(&resolve_env(), SESSION, now_ms()).is_some()); }
    assert!(row.semantic_projection_json["caller_dispatch"]["caller_request_digest"].as_str().is_some());
}

#[tokio::test]
async fn an_unbound_second_start_cannot_borrow_an_older_authorized_candidate() {
    let h = NativeFixture::new(true).await;
    h.observe("first").await;
    // A different accepted Run has no lineage (e.g. a non-Human system turn).
    let mut second = h.start("second");
    second.run_id = "unbound-system-run".into();
    let mut context = h.context();
    context.run_id = second.run_id.clone();
    h.f.flow.bot_run_context.as_ref().unwrap().put_context(context).await;
    h.f.flow.handle_bot_event(second).await.unwrap();
    let before = h.rows().await.len();
    assert!(h.f.flow.handle_native_task_dispatch("ambiguous", h.command()).await.is_err(),
        "a start without lineage must still make same-parameter correlation ambiguous");
    assert_eq!(h.rows().await.len(), before, "refuse before admission");
}

#[tokio::test]
async fn caller_parent_exchanges_its_exact_execution_token() {
    let h = NativeFixture::new(false).await;
    h.observe("caller-child").await;
    let outcome = h.f.flow.handle_native_task_dispatch("caller-request", h.command()).await.unwrap();
    let worker = h.rows().await.into_iter().find(|r| r.semantic_projection_json["task"]["task_id"] == outcome.task_id).unwrap();
    let row = h.drive("bot-observer", &worker.delivery_id).await;
    assert_eq!(row.state.status, Status::Running);
    assert_eq!(*h.identity.issued.lock().unwrap(), [Source::Human,
        Source::Parent { run:h.run().into(), attempt:h.parent.attempt_no, token:format!("<test-proof-{}>", h.run()) }]);
    assert!(h.io.sent.lock().await.is_empty());
}

#[tokio::test]
async fn replay_and_concurrent_claim_admit_only_one_child() {
    let h = NativeFixture::new(true).await;
    h.observe("once").await;
    let before = h.rows().await.len();
    let (a, b) = tokio::join!(
        h.f.flow.handle_native_task_dispatch("request-a", h.command()),
        h.f.flow.handle_native_task_dispatch("request-b", h.command()));
    assert_ne!(a.is_ok(), b.is_ok(), "one tool invocation has one atomic claim");
    let (request, result) = if let Ok(result) = a { ("request-a", result) } else { ("request-b", b.unwrap()) };
    let duplicate = h.f.flow.handle_native_task_dispatch(request, h.command()).await.unwrap();
    assert_eq!(duplicate.task_id, result.task_id);
    assert!(duplicate.bot_deliveries.is_empty() && duplicate.frontend_deliveries.is_empty());
    let mut mutation = h.command();
    mutation.payload["message"] = json!("different work");
    assert!(h.f.flow.handle_native_task_dispatch(request, mutation).await.is_err());
    h.observe("once").await;
    assert!(h.f.flow.handle_native_task_dispatch("changed-request-id", h.command()).await.is_err());
    assert_eq!(h.rows().await.len(), before + 1);
}

#[tokio::test]
async fn reordered_start_cannot_revive_a_failed_request() {
    let h = NativeFixture::new(true).await;
    let before = h.rows().await.len();
    assert!(tokio::time::timeout(Duration::from_millis(300),
        h.f.flow.handle_native_task_dispatch("early", h.command())).await.unwrap().is_err(),
        "do not await a later frame on the serial WS loop");
    h.observe("later").await;
    assert!(h.f.flow.handle_native_task_dispatch("early", h.command()).await.is_err());
    assert_eq!(h.rows().await.len(), before);
}

#[tokio::test]
async fn distinct_valid_starts_and_malformed_invocations_fail_closed() {
    for malformed in [false, true] {
        let h = NativeFixture::new(true).await;
        h.observe("first").await;
        let mut next = h.start("second");
        if malformed { next.event_payload["data"]["toolCallId"] = serde_json::Value::Null; }
        h.f.flow.handle_bot_event(next).await.unwrap();
        let before = h.rows().await.len();
        assert!(h.f.flow.handle_native_task_dispatch("ambiguous", h.command()).await.is_err());
        assert_eq!(h.rows().await.len(), before);
    }
}

#[tokio::test]
async fn changed_scope_parameters_and_model_parent_fields_cannot_authorize() {
    let h = NativeFixture::new(true).await;
    h.observe("exact").await;
    let before = h.rows().await.len();
    for (i, field) in ["message", "response_mode", "timeout_s", "bcs_session_id"].iter().enumerate() {
        let mut cmd = h.command();
        cmd.payload[*field] = if *field == "timeout_s" { json!(100) } else { json!("different") };
        assert!(h.f.flow.handle_native_task_dispatch(&format!("mutated-{i}"), cmd).await.is_err());
    }
    let mut forged = h.command();
    forged.driver_bot_id = "bot-observer".into();
    assert!(h.f.flow.handle_native_task_dispatch("wrong-bot", forged).await.is_err());
    assert!(h.f.flow.handle_native_task_dispatch("forged-parent", dispatch_command()).await.is_err());
    assert_eq!(h.rows().await.len(), before);
}

#[tokio::test]
async fn terminal_expired_and_changed_attempt_parents_refuse_before_admission() {
    for fault in ["terminal", "expired", "attempt", "lineage_lost", "delivery_terminal"] {
        let h = NativeFixture::new(true).await;
        h.observe("parent-check").await;
        match fault {
            "terminal" | "expired" => {
                let mut context = h.context();
                if fault == "terminal" { context.terminal = true; } else { context.deadline_ms = 1; }
                h.f.flow.bot_run_context.as_ref().unwrap().put_context(context).await;
            }
            "attempt" => {
                let mut lineage = h.lineage.current(h.run(), now_ms()).unwrap();
                lineage.attempt += 1;
                assert!(h.lineage.register(h.run(), lineage, now_ms()));
            }
            "lineage_lost" => { assert!(h.lineage.release_root_run(h.run())); }
            _ => {
                let row = h.rows().await.into_iter().find(|r| r.delivery_id == h.parent.delivery_id).unwrap();
                h.f.service.transition(DeliveryTransitionCommand {
                    delivery_id:row.delivery_id, expected_state_version:row.state.state_version,
                    event:DeliveryLifecycleEvent::Completed, now_ms:now_ms(), request_id:None,
                    actor_id:None, reply:None, transport_context_json:None, deadline_at_ms:None,
                    caller_dispatch_commit:None, caller_cancel:None, caller_terminal:None,
                }).await.unwrap();
            }
        }
        let before = h.rows().await.len();
        assert!(h.f.flow.handle_native_task_dispatch("dead-parent", h.command()).await.is_err(), "{fault}");
        assert_eq!(h.rows().await.len(), before);
    }
}

#[tokio::test]
async fn caller_material_loss_must_not_downgrade_to_human_origin() {
    let h = NativeFixture::new(false).await;
    h.observe("caller-material").await;
    let mut lineage = h.lineage.current(h.run(), now_ms()).unwrap();
    lineage.parent_execution = false;
    assert!(h.lineage.register(h.run(), lineage, now_ms()));
    let before = h.rows().await.len();
    assert!(h.f.flow.handle_native_task_dispatch("no-material", h.command()).await.is_err());
    assert_eq!(h.rows().await.len(), before);
    assert_eq!(h.sender.commands.lock().unwrap().len(), 1, "only the parent was sent");
}

#[tokio::test]
async fn owner_to_owner_native_task_stays_ordinary_without_a_tool_start() {
    let f = fixture_with_plane(false, false).await;
    let mut cmd = dispatch_command();
    cmd.caller_delegation = None;
    let outcome = f.flow.handle_native_task_dispatch("ordinary", cmd).await.unwrap();
    let row = f.service.snapshot(Some(SESSION)).await.unwrap().into_iter()
        .find(|r| r.semantic_projection_json["task"]["task_id"] == outcome.task_id).unwrap();
    assert!(row.semantic_projection_json.get("caller_required").is_none());
    assert!(row.semantic_projection_json.get("caller_delegation").is_none());
}

#[tokio::test]
async fn a_tool_invocation_cannot_be_reused_with_changed_arguments() {
    let h = NativeFixture::new(true).await;
    h.observe("single-invocation").await;
    h.f.flow.handle_native_task_dispatch("first-request", h.command()).await.unwrap();
    let mut changed_start = h.start("single-invocation");
    changed_start.event_payload["data"]["args"]["message"] = json!("changed work");
    h.f.flow.handle_bot_event(changed_start).await.unwrap();
    let mut changed_request = h.command();
    changed_request.payload["message"] = json!("changed work");
    let before = h.rows().await.len();
    assert!(h.f.flow.handle_native_task_dispatch("second-request", changed_request).await.is_err(),
        "an invocation is single-use even when its request ID and argument fingerprint change");
    assert_eq!(h.rows().await.len(), before);
}
