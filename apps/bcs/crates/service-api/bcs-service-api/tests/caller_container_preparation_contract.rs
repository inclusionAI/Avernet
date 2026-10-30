//! Caller container preparation conformance (spec §10.8 application
//! consumer; wire doc "应用准备流程"). The service under test is the real
//! orchestration; the Gateway is a recording double because the ensure wire
//! itself is already proven by its own adapter contract (§15.9).

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{BotDeliveryTarget, RedactedToken};
use bcs_service_api::application::caller_container::{
    CallerContainerPreparation, CallerPreparationError, PrepareOutcome,
};
use bcs_service_api::port::{
    CallerContainerDescriptor, CallerContainerError, CallerContainerGatewayPort,
    CallerContainerIsolation, CallerContainerMinimum, CallerOpaqueRef, EnsureCallerContainer,
    ContainerRunAssociation, EnsureCreateMode,
};
use bcs_service_api::ServiceResult;

const NOW: i64 = 1_780_000_000_000;
const IDENTITY_DEADLINE: i64 = NOW + 300_000;

struct RecordingGateway {
    calls: AtomicUsize,
    descriptor: Option<CallerContainerDescriptor>,
    fail_with: Option<CallerContainerError>,
}

impl RecordingGateway {
    fn ok(target: &str, container_expiry: i64, binding: Option<&str>) -> Self {
        Self {
            calls: AtomicUsize::new(0),
            descriptor: Some(CallerContainerDescriptor {
                sandbox_container_id: None,
                target_bot_id: target.to_string(),
                caller_subject_ref: CallerOpaqueRef::new("subject-1"),
                ready: true,
                isolation: CallerContainerIsolation::ContainerBound,
                binding_ref: binding.map(CallerOpaqueRef::new),
                runtime_generation: Some(2),
                expires_at_ms: container_expiry,
                observe_target: None,
            }),
            fail_with: None,
        }
    }

    fn failing(error: CallerContainerError) -> Self {
        Self { calls: AtomicUsize::new(0), descriptor: None, fail_with: Some(error) }
    }
}

#[async_trait]
impl CallerContainerGatewayPort for RecordingGateway {
    async fn ensure_container(
        &self,
        _target: &BotDeliveryTarget,
        _credential: &RedactedToken,
        _request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, CallerContainerError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if let Some(error) = &self.fail_with {
            return Err(error.clone());
        }
        Ok(self.descriptor.clone().expect("descriptor configured"))
    }
}

fn ensure() -> EnsureCallerContainer {
    EnsureCallerContainer {
        caller_subject_ref: CallerOpaqueRef::new("subject-1"),
        target_bot_id: "bot-b".into(),
        session_id: "session-1".into(),
        environment: "test".into(),
        minimum_isolation: CallerContainerMinimum::CallerContainer,
        idempotency_key: "ensure:bot-b:session-1:run-1".into(),
        run: ContainerRunAssociation {
            run_id: "run-1".into(),
            root_run_id: "run-1".into(),
            parent_run_id: None,
            message_turn_id: "turn-1".into(),
            history_cutoff_seq: 0,
        },
        create_mode: EnsureCreateMode::Lazy,
        container_ttl_ms: 3_600_000,
        deadline_ms: IDENTITY_DEADLINE,
        now_ms: NOW,
    }
}


fn delivery_target() -> BotDeliveryTarget {
    BotDeliveryTarget::WebSocket { bot_id: "bot-b".into() }
}

fn prep(gateway: Arc<RecordingGateway>) -> CallerContainerPreparation {
    CallerContainerPreparation::new(gateway)
}

#[tokio::test]
async fn happy_prepare_carries_the_requested_advisory_deadline() {
    let gateway = Arc::new(RecordingGateway::ok("bot-b", NOW + 60_000, Some("bind-1")));
    let service = prep(gateway.clone());
    let outcome = service.prepare(&delivery_target(), &RedactedToken::new("svc-token"), ensure(), NOW + 1_000).await.expect("prepared");
    assert!(matches!(outcome, PrepareOutcome::Prepared { .. }), "outcome: {outcome:?}");
    let PrepareOutcome::Prepared { descriptor, effective_deadline_ms } = outcome;
    // The carried advisory is the request's own deadline: container TTLs stay
    // container housekeeping and never trim it.
    assert_eq!(effective_deadline_ms, IDENTITY_DEADLINE);
    // The optional binding rides along without entering any identity grant.
    assert_eq!(descriptor.binding_ref.as_ref().map(CallerOpaqueRef::expose), Some("bind-1".to_string().as_str()));
    assert_eq!(gateway.calls.load(Ordering::SeqCst), 1, "ensure must fire exactly once");
}

#[tokio::test]
async fn container_outliving_the_advisory_never_trims_it() {
    let gateway = Arc::new(RecordingGateway::ok("bot-b", NOW + 9_000_000, None));
    let service = prep(gateway);
    let outcome = service.prepare(&delivery_target(), &RedactedToken::new("svc-token"), ensure(), NOW + 1_000).await.expect("prepared");
    let PrepareOutcome::Prepared { effective_deadline_ms, .. } = outcome;
    assert_eq!(effective_deadline_ms, IDENTITY_DEADLINE);
    // Binding-less descriptors are legal: absence never weakens isolation.
}

#[tokio::test]
async fn empty_scope_commands_reject_before_any_gateway_call() {
    let gateway = Arc::new(RecordingGateway::ok("bot-b", NOW + 60_000, None));
    let calls = Arc::new(AtomicUsize::new(0));
    let recorded = Arc::new(CountingGateway { inner: gateway.clone(), calls: calls.clone() });
    let service = CallerContainerPreparation::new(recorded);
    type Mutate = Box<dyn FnMut(&mut EnsureCallerContainer)>;
    let mutants: [Mutate; 7] = [
        Box::new(|c| c.target_bot_id = String::new()),
        Box::new(|c| c.session_id = String::new()),
        Box::new(|c| c.environment = String::new()),
        Box::new(|c| c.idempotency_key = String::new()),
        Box::new(|c| c.run.run_id = String::new()),
        Box::new(|c| c.caller_subject_ref = CallerOpaqueRef::new(String::new())),
        Box::new(|c| c.container_ttl_ms = 0),
    ];
    for mut mutate in mutants {
        let mut command = ensure();
        mutate(&mut command);
        let error = service.prepare(&delivery_target(), &RedactedToken::new("svc-token"), command, NOW + 1_000).await.unwrap_err();
        assert_eq!(error, CallerPreparationError::InvalidRequest, "mutant rejected");
    }
    assert_eq!(calls.load(Ordering::SeqCst), 0, "no gateway call for invalid commands");
}

#[tokio::test]
async fn a_late_clock_never_refuses_the_container_preparation() {
    // Identity windows are retired: the preparation succeeds regardless of
    // where the advisory deadline sits relative to the ensure clock.
    let gateway = Arc::new(RecordingGateway::ok("bot-b", NOW + 60_000, None));
    let service = prep(gateway.clone());
    let mut command = ensure();
    command.now_ms = IDENTITY_DEADLINE;
    let outcome = service
        .prepare(&delivery_target(), &RedactedToken::new("svc-token"), command, NOW)
        .await
        .expect("a late clock is never a preparation refusal");
    assert_eq!(gateway.calls.load(Ordering::SeqCst), 1);

    service
        .prepare(&delivery_target(), &RedactedToken::new("svc-token"), ensure(), IDENTITY_DEADLINE)
        .await
        .expect("an advisory past the wire clock still prepares");
    assert_eq!(gateway.calls.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn not_ready_and_linkage_corruption_never_become_a_prepared_container() {
    let gateway = Arc::new(RecordingGateway {
        calls: AtomicUsize::new(0),
        descriptor: Some(CallerContainerDescriptor {
            sandbox_container_id: None,
            target_bot_id: "bot-b".into(),
            caller_subject_ref: CallerOpaqueRef::new("subject-1"),
            ready: false,
            isolation: CallerContainerIsolation::ContainerBound,
            binding_ref: None,
            runtime_generation: None,
            expires_at_ms: NOW + 60_000,
            observe_target: None,
        }),
        fail_with: None,
    });
    let service = prep(gateway.clone());
    let error = service.prepare(&delivery_target(), &RedactedToken::new("svc-token"), ensure(), NOW + 1_000).await.unwrap_err();
    assert_eq!(error, CallerPreparationError::NotReady);
    assert_eq!(gateway.calls.load(Ordering::SeqCst), 1);

    // Linkage corruption (readback naming a different target/subject than the
    // bound request) is rejected rather than trusted as a new association.
    let gateway = Arc::new(RecordingGateway {
        calls: AtomicUsize::new(0),
        descriptor: Some(CallerContainerDescriptor {
            sandbox_container_id: None,
            target_bot_id: "bot-other".into(),
            caller_subject_ref: CallerOpaqueRef::new("subject-1"),
            ready: true,
            isolation: CallerContainerIsolation::ContainerBound,
            binding_ref: None,
            runtime_generation: None,
            expires_at_ms: NOW + 60_000,
            observe_target: None,
        }),
        fail_with: None,
    });
    let service = prep(gateway);
    let error = service.prepare(&delivery_target(), &RedactedToken::new("svc-token"), ensure(), NOW + 1_000).await.unwrap_err();
    assert_eq!(error, CallerPreparationError::LinkageMismatch);
}

#[tokio::test]
async fn gateway_failures_propagate_closed_without_a_second_ensure() {
    let gateway = Arc::new(RecordingGateway::failing(CallerContainerError::ProviderUnavailable { retryable: true }));
    let service = prep(gateway.clone());
    let error = service.prepare(&delivery_target(), &RedactedToken::new("svc-token"), ensure(), NOW + 1_000).await.unwrap_err();
    assert_eq!(
        error,
        CallerPreparationError::Gateway {
            retryable: true,
            unknown_outcome: true
        }
    );
    assert_eq!(gateway.calls.load(Ordering::SeqCst), 1, "no auto-retry inside the preparation");
}

#[test]
fn prepared_container_debug_redacts_everything_sensitive() {
    let gateway = Arc::new(RecordingGateway::ok("bot-b", NOW + 60_000, Some("bind-1")));
    let service = prep(gateway);
    let _ = service;
    let descriptor = CallerContainerDescriptor {
        sandbox_container_id: None,
        target_bot_id: "bot-b".into(),
        caller_subject_ref: CallerOpaqueRef::new("subject-1"),
        ready: true,
        isolation: CallerContainerIsolation::ContainerBound,
        binding_ref: Some(CallerOpaqueRef::new("bind-1")),
        runtime_generation: Some(2),
        expires_at_ms: NOW + 60_000,
        observe_target: None,
    };
    let debug = format!("{descriptor:?}");
    assert!(!debug.contains("subject-1") && !debug.contains("bind-1"), "leak: {debug}");
    assert!(debug.contains("<redacted>"));
}

struct CountingGateway {
    inner: Arc<RecordingGateway>,
    calls: Arc<AtomicUsize>,
}

#[async_trait]
impl CallerContainerGatewayPort for CountingGateway {
    async fn ensure_container(
        &self,
        target: &BotDeliveryTarget,
        credential: &RedactedToken,
        request: EnsureCallerContainer,
    ) -> Result<CallerContainerDescriptor, CallerContainerError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        self.inner.ensure_container(target, credential, request).await
    }
}

#[allow(dead_code)]
fn _unused_service_result_witness<T>(_value: ServiceResult<T>) {}
struct StubProviderCore(Option<bcs_domain::caller_identity::ProviderCallerCapabilities>);
#[async_trait]
impl bcs_service_api::core::ProviderCoreService for StubProviderCore {
    async fn get_caller_capabilities(&self, _: &str) -> ServiceResult<Option<bcs_domain::caller_identity::ProviderCallerCapabilities>> {
        if self.0.is_none() { return Err(bcs_service_api::ServiceError::InvalidOperation { message: "unavailable".into(), request_id: None }); }
        Ok(self.0)
    }
    async fn register_provider(
        &self,
        _name: String,
        _webhook_url: Option<String>,
        _auth_mode: bcs_domain::ProviderAuthMode,
        _created_by: String,
        _protocol_version: Option<String>,
        _coordination: Option<bcs_domain::ProviderCoordinationConfig>,
    ) -> ServiceResult<bcs_service_api::core::RegisteredProvider> {
        unreachable!("register_provider")
    }
    async fn authenticate_provider_admin(&self, _token: &str) -> ServiceResult<bcs_domain::ProviderRecord> {
        unreachable!("authenticate_provider_admin")
    }
    async fn get_downlink_credential(
        &self,
        _provider_id: &str,
    ) -> ServiceResult<bcs_domain::provider::ProviderCredential> {
        unreachable!("get_downlink_credential")
    }
    async fn get_provider(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
    ) -> ServiceResult<bcs_domain::ProviderRecord> {
        unreachable!("get_provider")
    }
    async fn update_provider(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _authenticated_staff_id: &str,
        _name: Option<String>,
        _webhook_url: Option<String>,
        _protocol_version: Option<String>,
        _coordination: Option<bcs_domain::ProviderCoordinationConfig>,
        _organization_management: Option<bcs_domain::ProviderOrganizationManagementConfig>,
    ) -> ServiceResult<bcs_domain::ProviderRecord> {
        unreachable!("update_provider")
    }
    async fn set_provider_disabled(
        &self,
        _provider_id: &str,
        _provider_admin_token: &str,
        _authenticated_staff_id: &str,
        _disabled: bool,
    ) -> ServiceResult<bcs_domain::ProviderRecord> {
        unreachable!("set_provider_disabled")
    }
}

#[tokio::test]
async fn explicit_subject_routing_skips_ensure_but_default_and_failures_do_not() {
    use bcs_domain::caller_identity::{CallerContainerRouting, ProviderCallerCapabilities};
    let base = serde_json::json!({"supports_caller":true,"supports_isolation_binding":false,
        "supports_observe_inject":true,"token_visibility":"runtime_only"});
    let caps: ProviderCallerCapabilities = serde_json::from_value(base).unwrap();
    assert_eq!(caps.container_routing, CallerContainerRouting::Ensure);
    let target = BotDeliveryTarget::HttpProvider {
        bot_id: "bot-b".into(), provider_id: "p".into(), provider_bot_ref: "b".into(),
        webhook_url: "http://localhost/unused".into(), bcs_to_provider_token: RedactedToken::new("svc"), protocol_version: "1".into(),
    };
    for mode in [CallerContainerRouting::Ensure, CallerContainerRouting::CallerSubject] {
        let gateway = Arc::new(RecordingGateway::failing(CallerContainerError::GatewayRejected { retryable: false }));
        let service = prep(gateway.clone()).with_provider_core(Arc::new(StubProviderCore(Some(ProviderCallerCapabilities { container_routing: mode, ..caps }))));
        let result = service.prepare(&target, &RedactedToken::new("svc"), ensure(), NOW+1).await;
        if mode == CallerContainerRouting::Ensure {
            assert!(matches!(result, Err(CallerPreparationError::Gateway { .. })));
            assert_eq!(gateway.calls.load(Ordering::SeqCst), 1);
        } else {
            let PrepareOutcome::Prepared { descriptor, effective_deadline_ms } = result.unwrap();
            let observe = descriptor.observe_target.unwrap();
            assert_eq!(observe.caller_subject.expose(), "subject-1");
            assert!(observe.caller_ref.is_none() && observe.target_epoch.is_none());
            assert!(descriptor.binding_ref.is_none());
            assert_eq!(effective_deadline_ms, IDENTITY_DEADLINE);
            assert_eq!(gateway.calls.load(Ordering::SeqCst), 0);
        }
    }
    for configuration in [None, Some(ProviderCallerCapabilities { supports_caller:false, container_routing:CallerContainerRouting::CallerSubject, ..caps })] {
        let gateway = Arc::new(RecordingGateway::ok("bot-b", IDENTITY_DEADLINE, None));
        let service = prep(gateway.clone()).with_provider_core(Arc::new(StubProviderCore(configuration)));
        assert!(service.prepare(&target, &RedactedToken::new("svc"), ensure(), NOW+1).await.is_err());
        assert_eq!(gateway.calls.load(Ordering::SeqCst), 0);
    }
}
