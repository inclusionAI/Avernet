//! Real SQL management -> registered hook -> inline/queued transport. These are
//! in-process integration tests, not HTTP/Singlebox or production-host proof.
use super::*;
use bcs_service_api::application::agent_invoke::*;
use bcs_service_api::types::agent_invoke::{AgentInvokeRole, AgentInvokeOperation};
use bcs_service_api::interceptor::InterceptorChain;
use bcs_edge_permission::agent_invoke::AgentInvokeInterceptor;
use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
use bcs_domain::message_delivery::PersistedMessageDelivery;
use std::sync::atomic::{AtomicUsize, AtomicU8, Ordering::SeqCst};
#[path = "../../../bcs-edge-permission-store/tests/support/agent_invoke_sql_driver.rs"]
mod driver;

struct ObservedInvocation {
    driver: Arc<driver::Driver>,
    calls: AtomicUsize,
    fault: AtomicU8,
    pause_at: AtomicUsize,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}
#[async_trait::async_trait]
impl AgentInvocationService for ObservedInvocation {
    async fn authorize_invocation(&self, input: AgentInvocationInput) -> AgentInvokeResult<AgentInvocationPermit> {
        let call = self.calls.fetch_add(1, SeqCst) + 1;
        if self.pause_at.load(SeqCst) == call {
            self.entered.notify_one();
            tokio::time::timeout(Duration::from_secs(5), self.release.notified()).await.unwrap();
        }
        match self.fault.load(SeqCst) {
            1 => return Err(AgentInvokeError::Unavailable),
            2 => return Err(AgentInvokeError::InvalidState),
            _ => {}
        }
        self.driver.invocation().authorize_invocation(input).await
    }
}
struct Fixture {
    h: Harness,
    auth: Arc<ObservedInvocation>,
}
impl Fixture {
    async fn new(queued: bool, registered: bool) -> Self {
        let db = Arc::new(ObservedDb::new());
        migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
        let driver = Arc::new(driver::Driver::new_on(db.clone(), bcs_db_api::DbSqlFlavor::Sqlite, "dispatch").await);
        for bot in ["bot-driver", "bot-observer"] { driver.register(bot).await; }
        driver.role("human_source", "bot-driver", Some(AgentInvokeRole::Owner)).await;
        driver.role("human_target", "bot-observer", Some(AgentInvokeRole::Owner)).await;
        let auth = Arc::new(ObservedInvocation { driver, calls: AtomicUsize::new(0), fault: AtomicU8::new(0),
            pause_at: AtomicUsize::new(0), entered: Default::default(), release: Default::default() });
        let mut chain = InterceptorChain::new();
        if registered { chain.push(AgentInvokeInterceptor::new(auth.clone())); }
        let h = Harness::with_options(true, Some(db), None, Some(Arc::new(chain))).await;
        h.policy.snapshot.write().await.policy.flow_enabled.direct_a2a = queued;
        h.support.registry.set_visibility("bot-observer", "protected").await;
        // Authorization must NOT sit in the legacy credential-dependent hook.
        h.support.registry.without_agent_credentials("bot-driver").await;
        h.support.registry.without_agent_credentials("bot-observer").await;
        Self { h, auth }
    }
    fn input(&self) -> AgentInvocationInput {
        AgentInvocationInput { principal: AgentInvokePrincipal::Agent("bot-driver".into()),
            authenticated_scope: self.auth.driver.scope.clone(), pair: AgentInvokePair {
                from_bot_id: "bot-driver".into(), to_bot_id: "bot-observer".into() },
            originator: "bot-driver".into(), operation: AgentInvokeOperation::DirectInvocation }
    }
    async fn change(&self, change: AgentInvokeChange, key: &str) -> AgentInvokeReceipt {
        self.auth.driver.management().change(AgentInvokeCommand {
            principal: AgentInvokePrincipal::Human("human_target".into()),
            authenticated_scope: self.auth.driver.scope.clone(), pair: self.input().pair,
            change, idempotency_key: key.into(), reason: "dispatch contract".into(),
        }).await.unwrap()
    }
    async fn grant(&self) -> AgentInvokeGrantView {
        self.change(AgentInvokeChange::Grant { expected_version: 0 }, "grant").await.current_grant.unwrap()
    }
    async fn revoke(&self, grant: &AgentInvokeGrantView) {
        self.change(AgentInvokeChange::Revoke { edge_id: grant.edge_id, expected_version: grant.version }, "revoke").await;
    }
    fn command(&self) -> AsyncA2aChatCommand {
        AsyncA2aChatCommand { a2a_test_user: None, caller: caller(), target_bot_id: "bot-observer".into(),
            message: " direct text 世界 ".into(), from_actor_id: None, run_channel_from: None,
            authenticated_staff_id: None, run_id: "agent-run".into(), session_key: "agent-session".into(),
            timeout_ms: 60_000, client: Some("contract".into()), tags: vec![], response_mode: ChatResponseMode::Full,
            caller_wait_mode: None, organization_code: None, provider_bypass_headers: vec![] }
    }
    async fn submit(&self) -> ServiceResult<AsyncA2aChatAccepted> {
        self.h.direct.start_async_agent_invocation(self.command(), self.input()).await
    }
    async fn assert_no_effects(&self) {
        assert_eq!(self.h.admission_events.registered.load(SeqCst), 0, "not register-then-cleanup");
        assert!(self.h.contexts.get_context("agent-run").await.is_none());
        assert!(self.h.sessions.session_registration("agent-session").await.unwrap().is_none());
        assert!(self.h.direct.run_store().get("agent-run").await.is_none());
        assert!(self.h.service.snapshot(None).await.unwrap().is_empty());
        assert!(self.h.support.bot_delivery.frames().await.is_empty());
    }
    fn clear_queries(&self) {
        self.h.db.as_ref().unwrap().queries.lock().unwrap().clear();
    }
    fn assert_pair_reads(&self, expected: usize) {
        let queries = self.h.db.as_ref().unwrap().queries.lock().unwrap();
        let reads: Vec<_> = queries.iter().filter(|(sql, _)| sql.contains("FROM agent_invoke_scopes s ")
            && sql.contains("LEFT JOIN agent_invoke_facts sf ")).collect();
        assert_eq!(reads.len(), expected, "one current SQL snapshot per operation hook");
        assert!(reads.iter().all(|(sql, rows)| sql.ends_with("LIMIT 2") && *rows <= 1), "bounded exact pair, no growing graph scan");
    }
    fn preparation(&self) -> QueuedGroupPreparation {
        QueuedGroupPreparation { flow: Arc::downgrade(&self.h.flow), deliveries: self.h.service.clone() }
    }
    async fn start(&self, row: &PersistedMessageDelivery, prepared: &mut PreparedManagedDelivery) -> PersistedMessageDelivery {
        prepared.transport_context_json["policy_version"] = json!(self.h.policy.snapshot.read().await.version);
        let mut command = regressions::transition(row, DeliveryLifecycleEvent::StartSend);
        command.transport_context_json = Some(prepared.transport_context_json.clone());
        command.deadline_at_ms = Some(i64::MAX);
        let started = self.h.service.transition(command).await.unwrap();
        if let bcs_protocol::BcsFrame::Request(frame) = &mut prepared.command.frame { frame.id = started.request_id.clone().unwrap(); }
        started
    }
}

#[tokio::test]
async fn mandatory_authorization_precedes_all_effects_even_without_codes_or_public_target() {
    for queued in [false, true] {
        for scenario in ["missing-provider", "no-grant", "outage", "corrupt", "foreign-scope"] {
            let f = Fixture::new(queued, scenario != "missing-provider").await;
            f.h.support.registry.set_visibility("bot-observer", "public").await;
            if scenario != "no-grant" { f.grant().await; }
            if scenario == "outage" { f.auth.fault.store(1, SeqCst); }
            if scenario == "corrupt" { f.auth.fault.store(2, SeqCst); }
            let mut input = f.input();
            if scenario == "foreign-scope" { input.authenticated_scope.namespace = "foreign".into(); }
            assert!(f.h.direct.start_async_agent_invocation(f.command(), input).await.is_err(), "{queued} {scenario}");
            assert_eq!(f.auth.calls.load(SeqCst), usize::from(scenario != "missing-provider"));
            f.assert_no_effects().await;
        }
    }
}

#[tokio::test]
async fn exact_bot_pair_operation_and_trusted_lineage_cannot_be_replaced_or_delegated() {
    let f = Fixture::new(false, true).await;
    f.grant().await;
    for scenario in ["integration", "anonymous", "target", "principal", "from", "staff", "org", "originator", "tool", "group", "delegated", "channel", "test-user"] {
        let mut cmd = f.command(); let mut input = f.input();
        match scenario {
            "integration" => cmd.caller = CallerContext::Integration(IntegrationClient { client_id: "bot-driver".into(), scopes: vec![] }),
            "anonymous" => cmd.caller = CallerContext::Public,
            "target" => cmd.target_bot_id = "different-bot".into(),
            "principal" => input.principal = AgentInvokePrincipal::Human("human_target".into()),
            "from" => cmd.from_actor_id = Some("human_target".into()),
            "staff" => cmd.authenticated_staff_id = Some("target".into()),
            "org" => cmd.organization_code = Some("org".into()),
            "originator" => input.originator = "parent-bot".into(),
            "tool" => input.operation = AgentInvokeOperation::ToolInvocation,
            "group" => input.operation = AgentInvokeOperation::GroupOperation,
            "delegated" => input.operation = AgentInvokeOperation::DelegatedInvocation,
            "channel" => cmd.run_channel_from = Some("human_target".into()),
            "test-user" => cmd.a2a_test_user = Some(a2a_test_user::A2aTestUserSelection::new("bot-driver".into(), "12345678".into()).unwrap()),
            _ => unreachable!(),
        }
        assert!(matches!(f.h.direct.start_async_agent_invocation(cmd, input).await, Err(ServiceError::Forbidden(_))), "{scenario}");
        f.assert_no_effects().await;
    }
}

#[tokio::test]
async fn approved_nonfriend_reaches_real_inline_delivery_but_does_not_change_legacy_admission() {
    for blocking in [false, true] {
        let f = Fixture::new(false, true).await;
        f.grant().await;
        f.clear_queries();
        let cmd = f.command();
        let sync = A2aChatCommand { a2a_test_user: None, caller: cmd.caller, target_bot_id: cmd.target_bot_id, message: cmd.message,
            from_actor_id: Some("bot-driver".into()), authenticated_staff_id: None, run_id: Some(cmd.run_id),
            async_mode: !blocking, session_key: Some(cmd.session_key), timeout_ms: Some(cmd.timeout_ms), client: None,
            tags: vec![], response_mode: ChatResponseMode::Full, caller_wait_mode: None, organization_code: None, provider_bypass_headers: vec![] };
        assert!(matches!(f.h.direct.chat(sync.clone()).await, Err(ServiceError::NotFriends(_))));
        assert_eq!(f.auth.calls.load(SeqCst), 0, "legacy never falls back to AgentInvoke");
        if blocking { f.h.direct.chat_agent_invocation(sync, f.input()).await.unwrap(); }
        else { f.submit().await.unwrap(); }
        assert_eq!(f.auth.calls.load(SeqCst), if blocking { 2 } else { 3 });
        let frames = f.h.support.bot_delivery.frames().await;
        assert_eq!(frames.len(), 1);
        f.assert_pair_reads(if blocking { 2 } else { 3 });
        let bcs_protocol::BcsFrame::Request(frame) = &frames[0] else { panic!("expected chat.send") };
        let params = frame.params.as_ref().unwrap();
        assert_eq!(params["session_context"]["originator"], "bot-driver");
        assert_eq!(params["message"]["content"][0]["text"], " direct text 世界 ");
    }
}

#[tokio::test]
async fn revocation_after_initial_checks_cannot_redeem_an_inline_permit() {
    let f = Fixture::new(false, true).await;
    let grant = f.grant().await;
    f.auth.pause_at.store(3, SeqCst);
    let result = async { f.submit().await };
    let revoke = async {
        tokio::time::timeout(Duration::from_secs(5), f.auth.entered.notified()).await.unwrap();
        f.revoke(&grant).await;
        f.auth.release.notify_one();
    };
    let (result, _) = tokio::join!(result, revoke);
    assert!(matches!(result, Err(ServiceError::Forbidden(_))));
    assert!(f.h.support.bot_delivery.frames().await.is_empty());
    assert_eq!(f.h.direct.run_store().get("agent-run").await.unwrap().state, ChatRunState::Failed);
}

#[tokio::test]
async fn queue_rechecks_revocation_at_pickup_and_after_send_start_cas() {
    for revoke_after_prepare in [false, true] {
        let f = Fixture::new(true, true).await;
        let grant = f.grant().await;
        assert_eq!(f.submit().await.unwrap().status, "pending");
        assert_eq!(f.auth.calls.load(SeqCst), 2);
        let row = f.h.row("agent-run").await;
        assert_eq!(row.semantic_projection_json["version"], 2);
        assert!(!row.semantic_projection_json.to_string().contains("permit"));
        if revoke_after_prepare {
            let mut prepared = f.preparation().prepare(&row).await.unwrap();
            let started = f.start(&row, &mut prepared).await;
            f.revoke(&grant).await;
            assert!(matches!(f.preparation().before_send(&started, &prepared.command).await, Err(ServiceError::Forbidden(_))));
            assert_eq!(f.auth.calls.load(SeqCst), 5);
        } else {
            f.revoke(&grant).await;
            assert!(matches!(f.preparation().prepare(&row).await, Err(ServiceError::Forbidden(_))));
            assert_eq!(f.auth.calls.load(SeqCst), 3);
        }
        assert!(f.h.support.bot_delivery.frames().await.is_empty());
    }
}

#[tokio::test]
async fn loss_restore_rebind_and_storage_errors_cannot_send_already_queued_work() {
    for scenario in ["qualification-aba", "binding-aba", "storage", "disabled", "provider-scope"] {
        let f = Fixture::new(true, true).await;
        f.grant().await; f.submit().await.unwrap();
        match scenario {
            "qualification-aba" => { f.auth.driver.publish("bot-observer", false, false).await; f.auth.driver.publish("bot-observer", true, false).await; }
            "binding-aba" => f.auth.driver.publish("bot-observer", true, true).await,
            "storage" => { f.auth.driver.sql("DROP TABLE agent_invoke_facts", vec![]).await; }
            "provider-scope" => f.h.support.registry.deny_collaboration(),
            "disabled" => { let activation = f.auth.driver.store.scope_state().await.unwrap(); f.auth.driver.store.set_enabled(activation.revision, false, false).await.unwrap(); }
            _ => unreachable!(),
        }
        assert!(f.preparation().prepare(&f.h.row("agent-run").await).await.is_err(), "{scenario}");
        assert!(f.h.support.bot_delivery.frames().await.is_empty());
    }
}

#[tokio::test]
async fn persisted_invocation_version_scope_and_lineage_fail_closed_without_legacy_fallback() {
    let f = Fixture::new(true, true).await;
    f.grant().await; f.submit().await.unwrap();
    let row = f.h.row("agent-run").await;
    for scenario in ["v1-with-invocation", "v2-without-invocation", "unknown-version", "scope", "operation", "lineage", "source", "org", "run"] {
        let mut bad = row.clone();
        match scenario {
            "v1-with-invocation" => bad.semantic_projection_json["version"] = json!(1),
            "v2-without-invocation" => { bad.semantic_projection_json.as_object_mut().unwrap().remove("agent_invocation"); }
            "unknown-version" => bad.semantic_projection_json["version"] = json!(3),
            "scope" => bad.semantic_projection_json["agent_invocation"]["namespace"] = json!("other"),
            "operation" => bad.semantic_projection_json["agent_invocation"]["operation"] = json!("group_operation"),
            "lineage" => bad.semantic_projection_json["from_actor_id"] = json!("parent-bot"),
            "source" => bad.semantic_projection_json["from_bot_id"] = json!("other-bot"),
            "org" => bad.semantic_projection_json["organization_code"] = json!("org"),
            "run" => bad.run_id = Some("other-run".into()),
            _ => unreachable!(),
        }
        assert!(f.preparation().prepare(&bad).await.is_err(), "{scenario}");
    }
    assert!(f.h.support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn registered_hook_dispatches_approved_queued_work_through_actual_scheduler() {
    let f = Fixture::new(true, true).await;
    f.grant().await; f.clear_queries(); f.submit().await.unwrap();
    let (stop, rx) = tokio::sync::watch::channel(false);
    let worker = tokio::spawn(f.h.runtime().run(rx));
    tokio::time::timeout(Duration::from_secs(5), async {
        while f.h.support.bot_delivery.frames().await.is_empty() { tokio::task::yield_now().await; }
    }).await.unwrap();
    stop.send(true).unwrap(); worker.await.unwrap().unwrap();
    assert_eq!(f.h.support.bot_delivery.frames().await.len(), 1);
    assert_eq!(f.auth.calls.load(SeqCst), 5, "admission twice, preparation twice, final before_send once");
    f.assert_pair_reads(5);
}

#[tokio::test]
async fn real_operation_provider_conforms_without_authorizing_ordinary_outbound_messages() {
    let f = Fixture::new(false, true).await;
    let provider = AgentInvokeInterceptor::new(f.auth.clone());
    bcs_test_support::contract::interceptor::agent_invocation_interceptor_contract_tests(
        &provider, &f.input(), Err(AgentInvokeError::Forbidden)).await;
    let grant = f.grant().await;
    let expected = f.auth.driver.invocation().authorize_invocation(f.input()).await;
    bcs_test_support::contract::interceptor::agent_invocation_interceptor_contract_tests(
        &provider, &f.input(), expected).await;
    f.revoke(&grant).await;
    bcs_test_support::contract::interceptor::agent_invocation_interceptor_contract_tests(
        &provider, &f.input(), Err(AgentInvokeError::Forbidden)).await;
    f.h.support.registry.deny_collaboration();
    assert!(f.submit().await.is_err());
    f.assert_no_effects().await;
}

pub(super) async fn unapproved_provider() -> (AgentInvokeInterceptor, AgentInvocationInput) {
    let f = Fixture::new(false, true).await;
    (AgentInvokeInterceptor::new(f.auth.clone()), f.input())
}

#[tokio::test]
async fn final_send_hook_rejects_corrupt_or_downgraded_persisted_operation() {
    let f = Fixture::new(true, true).await;
    f.grant().await; f.submit().await.unwrap();
    let row = f.h.row("agent-run").await;
    let mut prepared = f.preparation().prepare(&row).await.unwrap();
    let started = f.start(&row, &mut prepared).await;
    for corrupt_json in [true, false] {
        let mut bad = started.clone();
        if corrupt_json { bad.semantic_projection_json = json!(null); }
        else { bad.semantic_projection_json["version"] = json!(1); }
        assert!(f.preparation().before_send(&bad, &prepared.command).await.is_err());
    }
    assert!(f.h.support.bot_delivery.frames().await.is_empty());
}
