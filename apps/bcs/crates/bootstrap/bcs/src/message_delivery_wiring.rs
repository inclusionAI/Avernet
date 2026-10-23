//! Durable scheduler composition gated by the existing master election port.

/// Protected dispatch assembly inputs for the full caller wiring: the
/// Provider-http transport (ensure + protected send) and the identity plugin
/// instance selected by configuration (P1.7). All pieces must be present —
/// partial assemblies fail closed before any caller row can dispatch.
/// The optional observe injector factory attaches the same-bot shared-
/// context fan-out (spec §6.4): the observe containers of one Bot live
/// behind the same Provider webhook as its executing container.
pub struct CallerProtectedAssembly {
    pub provider_core: Arc<dyn bcs_service_api::core::ProviderCoreService>,
    pub gateway: Arc<dyn bcs_service_api::port::CallerContainerGatewayPort>,
    pub sender: Arc<dyn bcs_service_api::port::CallerExecutionSendPort>,
    pub identity: Arc<dyn bcs_caller_identity_api::CallerIdentityPlugin>,
    pub observe_injector_factory:
        Option<Arc<dyn bcs_service_api::port::ObserveInjectorFactory>>,
}

/// Runtime-only caller state that must die with a master term (spec
/// §7.5.2): parked handoff identity, observe references and root lineage
/// are TERM-scoped on the delivery master's epoch boundary. Every new
/// epoch starts them clean — identity admitted under an old term is never
/// claimed, an old term's observe reference never injects, an old term's
/// lineage never derives. Empty when the composition carries no caller
/// assembly.
struct CallerTermState {
    handoff: Option<
        Arc<bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff>,
    >,
    observe: Option<Arc<bcs_message_flow::observe_index::ObserveTargetIndex>>,
    lineage: Option<Arc<bcs_message_flow::caller_lineage::RootLineageIndex>>,
    /// Review-VI P1: the master-term token this wiring hands to the
    /// dispatch pipeline. run_on_master bumps it at BOTH epoch boundaries —
    /// a new term starts, or this instance demotes — so every detached
    /// pipeline effect (barrier redrives, observe fan-outs, accepted
    /// lineage writes) fenced against the captured term folds on the term
    /// change instead of sending or writing into the fresh incarnation.
    term: Arc<std::sync::atomic::AtomicU64>,
}

impl CallerTermState {
    /// Seal the old term at a new master epoch's start. Returns how many
    /// parked identities, observe references and lineage entries were
    /// wiped from the previous term.
    fn seal_for_new_epoch(&self) -> (usize, usize, usize) {
        let parked = self.handoff.as_ref().map_or(0, |handoff| handoff.seal_term());
        let observes = self.observe.as_ref().map_or(0, |index| index.seal_term());
        let lineages = self.lineage.as_ref().map_or(0, |index| index.seal_term());
        (parked, observes, lineages)
    }
}

/// Invalidates detached Caller work on every epoch exit, including task drop.
struct CallerEpochGuard(Arc<std::sync::atomic::AtomicU64>);
impl Drop for CallerEpochGuard {
    fn drop(&mut self) { self.0.fetch_add(1, std::sync::atomic::Ordering::SeqCst); }
}

/// Observe injector factory over the shared Provider transport: builds the
/// observe injector for the CURRENT dispatch's own Provider target,
/// reusing the transport's client and outbound-URL guard (the same policy
/// that governs ensure/send). Non-Provider targets return None — that
/// shape's fan-out stays paused.
pub struct ProviderObserveInjectorFactory {
    pub transport: Arc<bcs_provider_http::HttpProviderTransport>,
}

impl bcs_service_api::port::ObserveInjectorFactory for ProviderObserveInjectorFactory {
    fn history_backfill(&self) -> Option<Arc<dyn bcs_service_api::port::HistoryBackfillPort>> {
        Some(self.transport.clone())
    }

    fn injector_for(
        &self,
        target: &bcs_domain::BotDeliveryTarget,
    ) -> Option<Arc<dyn bcs_service_api::port::ObserveInjectPort>> {
        self.transport
            .observe_injector(target.clone())
            .map(|injector| Arc::new(injector) as Arc<dyn bcs_service_api::port::ObserveInjectPort>)
    }
}
use bcs_message_flow::{
    BcsMessageFlow,
    delivery_runtime::{DeliveryRuntime, DeliveryRuntimeConfig},
    managed_delivery::ManagedMessageDelivery,
    queued_group::QueuedGroupPreparation,
};
use bcs_service_api::application::message_delivery::ManagedMessageDeliveryService;
use std::{collections::BTreeMap, sync::Arc, time::Duration};
use tracing::instrument::WithSubscriber;

#[cfg(test)]
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod wiring_support;
#[cfg(test)]
#[path = "../../../services/bcs-message-flow/tests/support/session.rs"]
mod session_support;

struct SchedulerGuard {
    policy: Arc<bcs_message_flow::delivery_policy::LiveDeliveryPolicy>,
    service: Arc<ManagedMessageDelivery>,
    completion: tokio::sync::watch::Sender<bool>,
}

/// Stop a scheduler if later composition fails, including collaborators with
/// legacy reference cycles. Disarm only once the server owns shutdown.
pub(crate) struct StartupGuard(pub Option<Arc<dyn bcs_service_api::MessageFlowService>>);
impl Drop for StartupGuard {
    fn drop(&mut self) {
        if let Some(flow) = self.0.take() {
            if let Ok(runtime) = tokio::runtime::Handle::try_current() {
                runtime.spawn(async move {
                    if let Err(error) = flow.shutdown_managed_delivery().await {
                        tracing::error!(%error, "failed to stop delivery scheduler after composition failure");
                    }
                });
            }
        }
    }
}
impl Drop for SchedulerGuard {
    fn drop(&mut self) {
        self.service.set_admission_available(false);
        self.policy.scheduler_available.store(false, std::sync::atomic::Ordering::SeqCst);
        let _ = self.completion.send(true);
    }
}

#[cfg(test)]
pub async fn wire(flow: BcsMessageFlow, config: &crate::BcsConfig) -> crate::Result<Arc<BcsMessageFlow>> {
    wire_with_caller_assembly(
        flow,
        config,
        Arc::new(bcs_leader_election::StandaloneLeaderElection::local()),
        None,
        None,
        None,
    )
    .await
}

/// Full opt-in wiring: control plane plus the protected dispatch assembly
/// (gateway ensure/send + identity plugin). The handoff instance is created
/// ONCE here and shared by the admission seam and the runtime pipeline —
/// the identity admitted is exactly the identity the same-epoch dispatcher
/// can claim (spec §7.5.1).
pub async fn wire_with_caller_assembly(
    mut flow: BcsMessageFlow,
    config: &crate::BcsConfig,
    leader: Arc<dyn bcs_service_api::LeaderElectionPort>,
    caller_control_plane: Option<Arc<dyn bcs_service_api::BotControlPlaneCoreService>>,
    caller_protected: Option<CallerProtectedAssembly>,
    selected_lease_store: Option<
        Arc<dyn bcs_service_api::port::repo::session_speaking_lease::SpeakingLeaseRepoPort>,
    >,
) -> crate::Result<Arc<BcsMessageFlow>> {
    use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
    use std::sync::atomic::Ordering;
    let invalid = |message: &str| crate::BcsError::InvalidConfig(message.into());
    if config.message_delivery != Default::default() {
        return Err(invalid("message_delivery business policy moved to DB: remove TOML flow_enabled/bots/TTL/retry/pause values and use the management API"));
    }
    let repository = flow.message_repo.clone().and_then(|repo| repo.delivery_repository())
        .ok_or_else(|| invalid("message store does not support delivery transactions"))?;
    let initial = LiveDeliveryPolicy::load_compatible(repository.as_ref()).await
        .map_err(|_| invalid("cannot load or migrate durable delivery policy"))?;
    let durable = repository.is_durable();
    config.provider_http.validate().map_err(|e| invalid(&e))?;
    flow.queue_persistable_headers = config.provider_http.queue_persistable_headers.clone();
    flow.new_participant_visible_limit = config.message_history.new_participant_visible_limit;
    // §15.99: the configured speaking-lease idle window must be the SAME
    // value workbench submission checks and acquire/preempt grant.
    flow.speaking_lease_idle_timeout_ms = config.speaking_lease.idle_timeout_ms;
    let live = Arc::new(LiveDeliveryPolicy::new(repository.clone(), initial.clone()));
    let service = ManagedMessageDelivery::new(repository).with_policy(live.clone());
    let metrics = Arc::new(crate::delivery_metrics::DeliveryMetrics::default());
    let service = service.with_instrumentation(metrics.clone());
    let service = Arc::new(service);
    service.set_admission_available(false);
    let rows = service.queue_statistics().await.map_err(|_| invalid("cannot read message delivery recovery state"))?;
    let pending = !rows.is_empty();
    flow.delivery_policy = Some(live.clone());
    flow = flow.with_managed_deliveries(service.clone());
    // P3.2 speaking lease (spec §6.3): the in-memory lease store backs
    // local/ephemeral compositions and every wiring test harness — the
    // SQL production composition leaves it unset until the SQL lease
    // store lands, keeping caller groups fail-closed there (P3 does not
    // open production execution before P4 anyway).
    let lease_store: std::sync::Arc<dyn bcs_service_api::port::repo::session_speaking_lease::SpeakingLeaseRepoPort> =
        match selected_lease_store {
            Some(store) => store,
            // Local/ephemeral wiring and every test harness: the in-memory
            // lease store. Production SQL compositions pass the durable
            // store instead.
            None => std::sync::Arc::new(bcs_session_store::MemorySpeakingLeaseRepo::new()),
        };
    flow = flow.with_speaking_leases(lease_store);
    // Caller admission seam (spec §7.5.1): opt in only when the composition
    // supplies a caller-capable control plane. The runtime-side enforcement
    // (§15.4 guard) exists regardless; the seam only controls identity
    // parking and the caller_required marker at admission.
    // One handoff instance for the whole epoch: admission parks here, the
    // same-epoch runtime pipeline claims here (spec §7.5.1/§7.5.2).
    let caller_handoff = Arc::new(bcs_message_flow::caller_delivery_handoff::HumanCallerDeliveryHandoff::new(256));
    // Term-scoped caller state (spec §7.5.2): the concrete instances the
    // master's epoch boundary seals — identity parked, observe references
    // and root lineage of an old term never outlive the term.
    let caller_term_token = Arc::new(std::sync::atomic::AtomicU64::new(0));
    let mut caller_term = CallerTermState {
        handoff: Some(caller_handoff.clone()),
        observe: None,
        lineage: None,
        term: caller_term_token.clone(),
    };
    let mut caller_pipeline = None;
    if let Some(control_plane) = caller_control_plane.clone() {
        if let Some(protected) = caller_protected {
            let preparation = Arc::new(bcs_service_api::application::caller_container::CallerContainerPreparation::new(
                protected.gateway,
            ).with_provider_core(protected.provider_core));
            let pipeline = bcs_message_flow::caller_dispatch_pipeline::CallerDispatchPipeline::new(
                caller_handoff.clone() as Arc<dyn bcs_service_api::application::caller_execution::HumanCallerDeliveryHandoffService>,
                preparation,
                protected.identity,
                protected.sender,
                3_600_000,
            )
            .with_managed_commit(service.clone())
            .with_bot_registry(flow.registry.clone());
            // Review-VI P1: the pipeline carries the master-term token so
            // its detached effects fence across delivery-master boundaries.
            let pipeline = pipeline.with_delivery_term(caller_term_token.clone());
            // Observe fan-out assembly (spec §6.4): only when the
            // composition supplies an injector factory. The index instance
            // lives exactly with this wiring epoch, shared by the seeding
            // (every successful ensure) and the same-bot fan-out. Capacity
            // bounds the Human×Bot×session directory; idle containers are
            // expiry-swept by the index itself.
            let mut caller_observe: Option<Arc<bcs_message_flow::caller_dispatch_pipeline::ObserveSyncAssembly>> = None;
            let pipeline = match protected.observe_injector_factory {
                Some(factory) => {
                    let pipeline = match (factory.history_backfill(), flow.message_repo.clone()) {
                        (Some(backfill), Some(messages)) => pipeline.with_history_backfill(backfill).with_history_messages(messages),
                        _ => pipeline,
                    };
                    let assembly = Arc::new(bcs_message_flow::caller_dispatch_pipeline::ObserveSyncAssembly {
                        index: Arc::new(bcs_message_flow::observe_index::ObserveTargetIndex::new(1024)),
                        injector_factory: factory,
                        groups: flow.group.clone(),
                        sessions: flow.session_management.clone(),
                        delivery_term: Some(caller_term_token.clone()),
                    });
                    caller_term.observe = Some(assembly.index.clone());
                    caller_observe = Some(assembly.clone());
                    pipeline.with_observe_sync((*assembly).clone())
                }
                None => pipeline,
            };
            // Root-lineage + derivation collaborators (spec §8.3, L243-A):
            // the runtime-only index lives exactly with this epoch's wiring —
            // the pipeline's Accepted root dispatches are the single trusted
            // producer; the coordination assign edge (on the flow) is the
            // single reader. Both must exist or derivations stay absent.
            let index = Arc::new(bcs_message_flow::caller_lineage::RootLineageIndex::new(1024));
            caller_term.lineage = Some(index.clone());
            flow = flow.with_caller_derivation(index.clone());
            let pipeline = pipeline.with_root_lineage(index);
            // Reply-side observe sync (spec §6.4, L243③): the SAME observe
            // assembly the runtime pipeline uses — the reply commit path
            // on the flow fans a bot's own reply out to its own containers
            // through it.
            if let Some(observe) = caller_observe.as_ref() {
                flow = flow.with_caller_observe(observe.clone());
            }
            caller_pipeline = Some(Arc::new(pipeline));
        }
        flow.caller_dispatch_pipeline = caller_pipeline.clone();
        flow.caller_admission = Some(Arc::new(bcs_message_flow::caller_admission::CallerAdmissionDeps {
            handoff: caller_handoff.clone() as Arc<dyn bcs_service_api::application::caller_execution::HumanCallerDeliveryHandoffService>,
            admission: service.clone() as Arc<dyn ManagedMessageDeliveryService>,
            control_plane,
            reserve_ttl_ms: 30_000,
        }));
    }
    let flow = Arc::new(flow);
    flow.retain_terminal_events();
    // Durable storage always installs an idle scheduler, even when all policy
    // switches are off. Only the current master's epoch may consume work.
    if !durable {
        if initial.policy.needs_scheduler() || pending {
            return Err(invalid("message delivery scheduler requires SQLite or MySQL/OceanBase"));
        }
        return Ok(flow);
    }
    let (completion_sender, completion_receiver) = tokio::sync::watch::channel(false);
    let guard = SchedulerGuard { service: service.clone(), policy: live.clone(), completion: completion_sender };
    let runtime = DeliveryRuntime {
        policy: Some(live.clone()),
        service: service.clone(),
        preparation: Arc::new(QueuedGroupPreparation {
            flow: Arc::downgrade(&flow), deliveries: service.clone(),
        }),
        transport: flow.bot_delivery.clone(),
        config: DeliveryRuntimeConfig {
            max_safe_retries: 0, bots: BTreeMap::new(), pause_dispatch: false,
            tick: Duration::from_millis(100), expiry_tick: Duration::from_secs(1),
            io_timeout: Duration::from_secs(30),
            run_timeout: Duration::from_millis(config.provider_chat_run_timeout_ms),
            cancel_timeout: Duration::from_secs(30),
            startup_recovery_grace: Duration::from_secs(10),
            max_tasks: 32, max_abort_tasks: 2,
        },
    caller_dispatch: caller_pipeline,
    };
    let (sender, receiver) = tokio::sync::watch::channel(false);
    flow.delivery_shutdown.set((sender, completion_receiver)).map_err(|_| invalid("queue shutdown hook already installed"))?;
    service.set_admission_available(true);
    live.scheduler_available.store(true, Ordering::SeqCst);
    let notifications = service.subscribe();
    tokio::spawn(bcs_message_flow::delivery_notifications::run(Arc::downgrade(&flow), notifications, receiver.clone()));
    let caller_term_seal = caller_term;
    tokio::spawn(async move {
        let _guard = guard;
        if let Err(error) = run_on_master(runtime, leader, service, live, metrics, caller_term_seal, receiver).await {
            tracing::error!(%error, "message delivery scheduler stopped; new admissions are disabled");
        }
        _guard.service.set_admission_available(false);
        _guard.policy.scheduler_available.store(false, Ordering::SeqCst);
    }.with_current_subscriber());
    Ok(flow)
}

/// Drop the epoch futures on demotion: no follower recovery and no new I/O.
/// JoinSet drop aborts local I/O tasks; persisted send-start records remain
/// uncertain until the next master's recovery (never blindly re-enqueued).
async fn run_on_master(
    runtime: DeliveryRuntime,
    leader: Arc<dyn bcs_service_api::LeaderElectionPort>,
    service: Arc<ManagedMessageDelivery>,
    policy: Arc<bcs_message_flow::delivery_policy::LiveDeliveryPolicy>,
    metrics: Arc<crate::delivery_metrics::DeliveryMetrics>,
    caller_term: CallerTermState,
    mut shutdown: tokio::sync::watch::Receiver<bool>,
) -> Result<(), bcs_service_api::ManagedDeliveryError> {
    let mut poll = tokio::time::interval(Duration::from_millis(100));
    poll.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! { biased;
            _ = shutdown.changed() => return Ok(()),
            _ = poll.tick() => {}
        }
        if *shutdown.borrow() { return Ok(()); }
        if !master(&leader).await { continue; }
        let refreshed = tokio::select! { biased;
            _ = shutdown.changed() => return Ok(()),
            result = tokio::time::timeout(Duration::from_secs(5), policy.refresh_for_takeover()) => matches!(result, Ok(Ok(()))),
        };
        if !refreshed {
            tracing::warn!("delivery master takeover policy refresh failed; dispatch remains paused");
            tokio::select! { _ = shutdown.changed() => return Ok(()), _ = tokio::time::sleep(Duration::from_secs(5)) => {} }
            continue;
        }
        if !master(&leader).await { continue; }
        tracing::info!("delivery master epoch started");
        caller_term.term.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        // Term boundary (spec §7.5.2): a new master epoch never inherits
        // the previous term's caller state — parked identity, observe
        // references and root lineage are wiped BEFORE the epoch consumes
        // anything, so old-term material fails closed at claim/inject/
        // derive and converges through the existing rescue paths.
        let (parked, observes, lineages) = caller_term.seal_for_new_epoch();
        if parked + observes + lineages > 0 {
            tracing::warn!(
                parked,
                observes,
                lineages,
                "caller state of the previous master term sealed (spec §7.5.2)"
            );
        }
        let _caller_epoch = CallerEpochGuard(caller_term.term.clone());
        let epoch = runtime.clone().run(shutdown.clone());
        let sampler = crate::delivery_metrics::run(service.clone(), policy.clone(), metrics.clone(), shutdown.clone());
        let reconciliation = reconcile_policy(policy.clone());
        tokio::pin!(epoch, sampler, reconciliation);
        loop {
            tokio::select! { biased;
                _ = shutdown.changed() => return Ok(()),
                _ = poll.tick() => {
                    if !master(&leader).await {
                        tracing::info!("delivery master epoch stopped; unresolved sends retained for takeover");
                        // Review-VI P1: demotion folds the OLD term's detached
                        // effects immediately — only the current master's
                        // runtime performs protected dispatch or writes
                        // identity material into the shared in-memory indexes.
                        break;
                    }
                }
                result = &mut epoch => return result,
                _ = &mut sampler => return Err(bcs_service_api::ManagedDeliveryError::Conflict),
                _ = &mut reconciliation => return Err(bcs_service_api::ManagedDeliveryError::Conflict),
            }
        }
    }
}

/// This future belongs to the master epoch, so demotion/shutdown also cancels
/// slow reconciliation without blocking leadership checks or restarting runs.
async fn reconcile_policy(policy: Arc<bcs_message_flow::delivery_policy::LiveDeliveryPolicy>) {
    let period = Duration::from_secs(5);
    let mut ticker = tokio::time::interval_at(tokio::time::Instant::now() + period, period);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut last_warning = None;
    loop {
        ticker.tick().await;
        if !matches!(tokio::time::timeout(period, policy.reconcile_durable_version()).await, Ok(Ok(()))) {
            let now = tokio::time::Instant::now();
            if last_warning.is_none_or(|at| now.duration_since(at) >= Duration::from_secs(30)) {
                tracing::warn!(operation = "policy_reconciliation", "delivery policy DB check failed; retaining snapshot and retrying in 5 seconds");
                last_warning = Some(now);
            }
        }
    }
}

async fn master(leader: &Arc<dyn bcs_service_api::LeaderElectionPort>) -> bool {
    matches!(tokio::time::timeout(Duration::from_millis(100), leader.is_leader()).await, Ok(Ok(true)))
}
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn delivery_queries_receive_configured_history_window() {
        let fixture = wiring_support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let flow = BcsMessageFlow::new(fixture.group, fixture.routing, fixture.registry,
            fixture.bot_delivery, fixture.frontend_delivery)
            .with_message_repo(Arc::new(bcs_message_store::MemoryMessageRepo::new()));
        let mut config = crate::BcsConfig::default();
        assert_eq!(flow.new_participant_visible_limit, config.message_history.new_participant_visible_limit);
        config.message_history.new_participant_visible_limit = 3;
        let flow = wire(flow, &config).await.unwrap();
        assert_eq!(flow.new_participant_visible_limit, 3);
    }

    #[tokio::test]
    async fn caller_epoch_is_invalidated_on_return_and_task_abort() {
        use std::sync::atomic::{AtomicU64, Ordering};
        let term = Arc::new(AtomicU64::new(1));
        { let _guard = CallerEpochGuard(term.clone()); }
        assert_eq!(term.load(Ordering::SeqCst), 2);
        let task_term = term.clone();
        let (ready, started) = tokio::sync::oneshot::channel();
        let task = tokio::spawn(async move {
            let _guard = CallerEpochGuard(task_term);
            ready.send(()).unwrap();
            std::future::pending::<()>().await;
        });
        started.await.unwrap();
        task.abort();
        assert!(task.await.unwrap_err().is_cancelled());
        assert_eq!(term.load(Ordering::SeqCst), 3);
    }

    #[test]
    fn provider_inject_factory_supplies_production_history_transport() {
        use bcs_service_api::port::ObserveInjectorFactory;
        let factory = ProviderObserveInjectorFactory {
            transport: Arc::new(bcs_provider_http::HttpProviderTransport::allowing_private_networks_for_tests()),
        };
        assert!(factory.history_backfill().is_some());
    }

    #[tokio::test]
    async fn master_reconciles_policy_after_five_seconds() {
        use bcs_service_api::port::repo::message_delivery::MessageDeliveryRepoPort;
        let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
        let policy = Arc::new(bcs_message_flow::delivery_policy::LiveDeliveryPolicy::new(repo.clone(), Default::default()));
        let reconciliation = reconcile_policy(policy.clone());
        tokio::pin!(reconciliation);
        let mut stored = bcs_config_api::message_delivery::DeliveryPolicyRecord::default();
        stored.version = 1;
        stored.policy.pause_dispatch = true;
        repo.replace_policy(0, stored).await.unwrap();
        tokio::select! { _ = &mut reconciliation => panic!("reconciliation exited"), _ = tokio::time::sleep(Duration::from_millis(100)) => {} }
        assert_eq!(policy.snapshot.read().await.version, 0, "no eager reload before 5-second interval");
        let observed = async {
            loop {
                if policy.snapshot.read().await.version == 1 { break; }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        };
        tokio::select! {
            _ = &mut reconciliation => panic!("reconciliation exited"),
            result = tokio::time::timeout(Duration::from_secs(6), observed) => result.unwrap(),
        }
        assert!(policy.snapshot.read().await.policy.pause_dispatch);
    }

    struct TestLeader(std::sync::atomic::AtomicU8);
    #[async_trait::async_trait]
    impl bcs_service_api::LeaderElectionPort for TestLeader {
        async fn campaign(&self) -> bcs_service_api::ServiceResult<bcs_service_api::LeaderStatus> { unreachable!() }
        async fn current_leader(&self) -> bcs_service_api::ServiceResult<Option<bcs_service_api::LeaderInfo>> { unreachable!() }
        async fn is_leader(&self) -> bcs_service_api::ServiceResult<bool> {
            match self.0.load(std::sync::atomic::Ordering::SeqCst) {
                0 => Ok(false), 1 => Ok(true),
                _ => Err(bcs_service_api::ServiceError::InternalError("unavailable".into())),
            }
        }
    }

    #[tokio::test]
    async fn leadership_errors_fail_closed() {
        let election = Arc::new(TestLeader(std::sync::atomic::AtomicU8::new(2)));
        let port: Arc<dyn bcs_service_api::LeaderElectionPort> = election.clone();
        assert!(!master(&port).await);
        election.0.store(0, std::sync::atomic::Ordering::SeqCst);
        assert!(!master(&port).await);
        election.0.store(1, std::sync::atomic::Ordering::SeqCst);
        assert!(master(&port).await);
    }

    #[test]
    fn removed_lock_path_is_rejected_by_full_config_schema() {
        let error = toml::from_str::<crate::BcsConfig>("[message_delivery]\nlock_path = '/obsolete/delivery.lock'\n").unwrap_err();
        assert!(error.to_string().contains("unknown field `lock_path`"));
    }

    #[tokio::test]
    async fn database_policy_can_enable_after_disabled_start_and_survive_restart() {
        use bcs_config_api::message_delivery::{DeliveryPolicy, BotDeliveryMode};
        use bcs_service_api::{HumanActor, CallerContext, GroupCoreService, MessageFlowService, WebSendCommand};
        let fixture = wiring_support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        crate::migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
        use bcs_db_api::{DbPlugin, DbStatement};
        db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('group-1:live','group-1','dev','[]')")).await.unwrap();
        let repo = Arc::new(bcs_message_store::MySqlMessageStore::sqlite(db, "dev".into()));
        let group = fixture.group.get("group-1").await.unwrap();
        let make_flow = || BcsMessageFlow::new(fixture.group.clone(), fixture.routing.clone(), fixture.registry.clone(), fixture.bot_delivery.clone(), fixture.frontend_delivery.clone())
            .with_message_repo(repo.clone())
            .with_bot_run_context(Arc::new(bcs_message_flow::MemoryBotRunContextStore::new()))
            .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session_support::test_session("group-1:live", "group-1", group.participants.clone()))));
        let admin = || CallerContext::Human(HumanActor { actor_id: "human_operator".into(), staff_no: "operator".into() });
        let mut config = crate::BcsConfig::default();
        config.metrics.enabled = false;
        config.provider_http.bypass_headers = vec!["x-routing-zone".into()];
        config.provider_http.queue_persistable_headers = vec!["x-routing-zone".into()];
        let mut policy = DeliveryPolicy::default();
        policy.flow_enabled.group = true;
        policy.flow_enabled.system = true;
        policy.defaults.mode = BotDeliveryMode::Enforce;
        let election = Arc::new(TestLeader(std::sync::atomic::AtomicU8::new(0)));
        let flow = wire_with_caller_assembly(make_flow(), &config, election.clone(), None, None, None).await.unwrap();
        assert!(flow.delivery_shutdown.get().is_some(), "off startup must install the idle scheduler");
        let enabled = flow.replace_delivery_policy(admin(), 0, policy).await.unwrap();
        assert_eq!(enabled.version, 1);
        let admission = flow.handle_web_send(WebSendCommand {
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
            group_id: "group-1".into(), session_id: Some("group-1:live".into()), from_actor_id: "human_1".into(),
            from_name: None, message: "@Driver dynamically enabled".into(), mentions: vec!["bot-driver".into()],
            attachments: None, thinking: None, idempotency_key: Some("dynamic-1".into()), source_im_message_id: None,
            channel_sender_identity: None, sender_conn_id: None, provider_bypass_headers: Vec::new(),
        }).await.unwrap();
        assert!(admission.queue_admission.is_some());
        tokio::time::sleep(Duration::from_millis(250)).await;
        assert!(fixture.bot_delivery.frames().await.is_empty(), "follower must not send queued work");
        election.0.store(1, std::sync::atomic::Ordering::SeqCst);
        let dispatched = tokio::time::timeout(Duration::from_secs(3), async {
            while fixture.bot_delivery.frames().await.is_empty() { tokio::time::sleep(Duration::from_millis(10)).await; }
        }).await;
        assert!(dispatched.is_ok(), "idle scheduler did not dispatch: {:?}", flow.managed_deliveries.as_ref().unwrap().snapshot(None).await.unwrap());
        election.0.store(0, std::sync::atomic::Ordering::SeqCst);
        tokio::time::sleep(Duration::from_millis(250)).await;
        let mut expiring_policy = enabled.policy.clone();
        expiring_policy.queue_ttl_ms = Some(100);
        let enabled = flow.replace_delivery_policy(admin(), 1, expiring_policy).await.unwrap();
        flow.handle_web_send(WebSendCommand {
            caller_ingress: None,
            speaking_lease: None,
            caller: CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() }),
            group_id: "group-1".into(), session_id: Some("group-1:live".into()), from_actor_id: "human_1".into(),
            from_name: None, message: "@Driver expire after takeover".into(), mentions: vec!["bot-driver".into()],
            attachments: None, thinking: None, idempotency_key: Some("dynamic-2".into()), source_im_message_id: None,
            channel_sender_identity: None, sender_conn_id: None, provider_bypass_headers: Vec::new(),
        }).await.unwrap();
        tokio::time::sleep(Duration::from_millis(250)).await;
        let rows = flow.managed_deliveries.as_ref().unwrap().snapshot(None).await.unwrap();
        assert!(rows.iter().any(|r| r.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Queued), "follower must not expire work");
        election.0.store(1, std::sync::atomic::Ordering::SeqCst);
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let rows = flow.managed_deliveries.as_ref().unwrap().snapshot(None).await.unwrap();
                if rows.iter().any(|r| r.state.status == bcs_domain::message_delivery::MessageDeliveryStatus::Expired) { break; }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }).await.expect("new master must process expiry");
        flow.shutdown_managed_delivery().await.unwrap();
        assert!(!flow.delivery_policy.as_ref().unwrap().scheduler_available.load(std::sync::atomic::Ordering::SeqCst));
        assert!(flow.replace_delivery_policy(admin(), 2, enabled.policy.clone()).await.is_err(), "stopped scheduler must remain fail closed");
        let restarted = wire(make_flow(), &config).await.unwrap();
        assert_eq!(restarted.get_delivery_policy(admin()).await.unwrap(), enabled);
        restarted.shutdown_managed_delivery().await.unwrap();
        config.message_delivery.flow_enabled.group = true;
        assert!(wire(make_flow(), &config).await.is_err(), "legacy TOML must not shadow DB policy");
    }

    #[tokio::test]
    async fn memory_storage_cannot_enable_scheduler() {
        use bcs_config_api::message_delivery::{DeliveryPolicy, BotDeliveryMode};
        use bcs_service_api::{HumanActor, CallerContext, MessageFlowService};
        let fixture = wiring_support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let flow = BcsMessageFlow::new(fixture.group, fixture.routing, fixture.registry, fixture.bot_delivery, fixture.frontend_delivery)
            .with_message_repo(Arc::new(bcs_message_store::MemoryMessageRepo::new()));
        let flow = wire(flow, &crate::BcsConfig::default()).await.unwrap();
        assert!(flow.delivery_shutdown.get().is_none());
        let mut policy = DeliveryPolicy::default();
        policy.flow_enabled.group = true;
        policy.flow_enabled.system = true;
        policy.defaults.mode = BotDeliveryMode::Enforce;
        let caller = CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() });
        assert!(flow.replace_delivery_policy(caller, 0, policy).await.is_err());
    }

    #[tokio::test]
    async fn disabled_config_recovers_sqlite_work_and_rejects_legacy_overtaking() {
        use bcs_db_api::{DbPlugin, DbStatement};
        use bcs_domain::{
            DeliveryType, NewMessage, SenderType,
            message_delivery::{DeliveryFlowKind, MessageDeliveryStatus},
        };
        use bcs_service_api::application::message_delivery::DeliveryTransitionCommand;
        use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent;
        use bcs_service_api::port::repo::message_delivery::{
            AdmitMessageDeliveries, DeliveryAdmissionTarget,
        };
        use bcs_service_api::{CallerContext, HumanActor, MessageFlowService, WebSendCommand};
        let fixture = wiring_support::FlowTestSupport::new_group_with_driver_and_observer().await;
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        crate::migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        db.execute(DbStatement::new("INSERT INTO bcs_group_sessions (session_id, group_id, env, participants) VALUES ('group-1:12345678','group-1','dev','[]')")).await.unwrap();
        let repo = Arc::new(bcs_message_store::MySqlMessageStore::sqlite(
            db,
            "dev".into(),
        ));
        let seed = ManagedMessageDelivery::new(repo.clone());
        let row = seed
            .admit(AdmitMessageDeliveries {
                display_message: None,
                message_id: "before-crash".into(),
                flow_kind: DeliveryFlowKind::Group,
                now_ms: 1,
                expire_at_ms: None,
                event: None,
                targets: vec![DeliveryAdmissionTarget { rejection: None,
                    target_bot_id: "bot-driver".into(),
                    kind: DeliveryType::Send,
                    max_queued: 10,
                    semantic_projection_json: serde_json::json!({"version":1}),
                }],
                message: NewMessage { visibility_domain: bcs_domain::MessageVisibilityDomain::Chat, audience: None,
                    group_id: "group-1".into(),
                    session_id: "group-1:12345678".into(),
                    sender_id: "human_1".into(),
                    sender_type: SenderType::Human,
                    message_type: "chat".into(),
                    content: serde_json::json!({"text":"before crash"}),
                    client_msg_id: None,
                    owner_bot_id: None,
                    created_at: 1,
                    run_id: String::new(),
                },
            })
            .await
            .unwrap()
            .deliveries
            .remove(0);
        seed.transition(DeliveryTransitionCommand { delivery_id: row.delivery_id.clone(), expected_state_version: row.state.state_version,
            event: DeliveryLifecycleEvent::StartSend, now_ms: 2, request_id: None, actor_id: None, reply: None,
            transport_context_json: Some(serde_json::json!({"version":1,"owner":{"kind":"web_socket"},"connection_id":"old","downstream_session_key":"old-key"})), deadline_at_ms: Some(i64::MAX),
            caller_dispatch_commit: None, caller_cancel: None, caller_terminal: None,
        }).await.unwrap();
        let config = crate::BcsConfig::default();
        let make_flow = || {
            BcsMessageFlow::new(
                fixture.group.clone(),
                fixture.routing.clone(),
                fixture.registry.clone(),
                fixture.bot_delivery.clone(),
                fixture.frontend_delivery.clone(),
            )
            .with_message_repo(repo.clone())
        };
        let election = Arc::new(TestLeader(std::sync::atomic::AtomicU8::new(0)));
        let flow = wire_with_caller_assembly(make_flow(), &config, election.clone(), None, None, None).await.unwrap();
        tokio::time::sleep(Duration::from_millis(250)).await;
        assert_eq!(seed.snapshot(None).await.unwrap()[0].state.status, MessageDeliveryStatus::Dispatching, "follower must not recover another master's work");
        election.0.store(1, std::sync::atomic::Ordering::SeqCst);
        let recovered = tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let rows = flow.managed_deliveries.as_ref().unwrap().snapshot(None).await.unwrap();
                if rows[0].state.status == MessageDeliveryStatus::Unknown { break rows; }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }).await.expect("master acquisition must recover outstanding work");
        assert_eq!(recovered[0].state.status, MessageDeliveryStatus::Unknown);
        assert_eq!(
            recovered[0].run_id,
            seed.snapshot(None).await.unwrap()[0].run_id
        );
        let rejected = flow
            .handle_web_send(WebSendCommand {
                caller_ingress: None,
                speaking_lease: None,
                caller: CallerContext::Human(HumanActor {
                    actor_id: "human_1".into(),
                    staff_no: "1".into(),
                }),
                group_id: "group-1".into(),
                session_id: Some("group-1:12345678".into()),
                from_actor_id: "human_1".into(),
                from_name: None,
                message: "@Driver after crash".into(),
                mentions: vec!["bot-driver".into()],
                attachments: None,
                thinking: None,
                idempotency_key: None,
                source_im_message_id: None,
                channel_sender_identity: None,
                sender_conn_id: None,
                provider_bypass_headers: Vec::new(),
            })
            .await;
        assert!(rejected.is_err_and(|error| error.to_string().contains("queue_draining")));
        flow.shutdown_managed_delivery().await.unwrap();
        let next = wire(make_flow(), &config).await.unwrap();
        next.shutdown_managed_delivery().await.unwrap();
    }
}
