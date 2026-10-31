//! Infrastructure composition helpers; protocol adapters remain at the root.
use super::*;

pub(super) async fn wire_caller_flow(
    config: &BcsConfig, db_plugin: Arc<dyn bcs_db_api::DbPlugin>, db_flavor: bcs_db_api::DbSqlFlavor,
    message_flow_builder: bcs_message_flow::BcsMessageFlow,
    leader_election: Arc<dyn bcs_service_api::LeaderElectionPort>,
    provider_core: Arc<dyn bcs_service_api::ProviderCoreService>,
    provider_control_plane: Arc<dyn bcs_service_api::BotControlPlaneCoreService>,
    provider_transport: Arc<bcs_provider_http::HttpProviderTransport>,
) -> crate::Result<(Arc<bcs_message_flow::BcsMessageFlow>, Arc<dyn bcs_service_api::port::repo::session_speaking_lease::SpeakingLeaseRepoPort>)> {
        // P3.3 SQL speaking leases (spec §6.3): the durable store keyed by
        // the resolved environment shares the SQL wiring family. The SAME
        // instance feeds the flow's admission fence AND the WS lease
        // command entrance — one store, one epoch.
        let lease_store: Arc<
            dyn bcs_service_api::port::repo::session_speaking_lease::SpeakingLeaseRepoPort,
        > = Arc::new(bcs_session_store::SqlSpeakingLeaseRepo::new(
            db_plugin.clone(),
            crate::env::resolve_env(),
            db_flavor,
        ));
        // Caller identity chain (spec §10.13.7): the optional
        // [caller_identity] section assembles the identity plugin; absent
        // keeps the protected dispatch unmounted (fail-closed, no caller
        // execution is fabricated). The SAME provider transport instance
        // serves the container gateway (H2A chat.send downlink) and the
        // execution send port.
        let caller_identity =
            crate::caller_identity_wiring::assemble_caller_identity_plugin(&config)?;
        let caller_protected = caller_identity.map(|identity| {
            crate::message_delivery_wiring::CallerProtectedAssembly {
                provider_core: provider_core.clone(),
                gateway: provider_transport.clone(),
                sender: provider_transport.clone(),
                identity,
                // Observe fan-out (§6.4) over the SAME shared provider
                // transport — the factory reuses its client and outbound-URL
                // guard; non-Provider targets simply stay un-fanned.
                observe_injector_factory: Some(Arc::new(
                    crate::message_delivery_wiring::ProviderObserveInjectorFactory {
                        transport: provider_transport.clone(),
                    },
                )),
            }
        });
        let wired_message_flow =
            crate::message_delivery_wiring::wire_with_caller_assembly(
            message_flow_builder,
            &config,
            leader_election.clone(),
            // The same caller-capable control plane GroupManagement reads:
            // presence here arms the per-message speaking-lease fence
            // (web_send re-resolves over live per-bot caller
            // configurations), so a lease-less Human send is rejected in
            // lease_required groups. Without it the fence reads
            // any_bot_caller_enabled as false (Plain) and never consults
            // the lease store.
            Some(provider_control_plane.clone()),
            caller_protected,
            Some(lease_store.clone()),
        )
        .await?;
    Ok((wired_message_flow, lease_store))
}

pub(super) fn group_context_service(
    db_kind: DbPluginKind, db_plugin: Arc<dyn bcs_db_api::DbPlugin>,
    sessions: Arc<dyn bcs_service_api::GroupCoreService>,
    session_repo: Arc<dyn bcs_service_api::SessionRepoPort>,
) -> Arc<dyn bcs_service_api::GroupContextApplicationService> {
        let group_context_repo: Arc<dyn bcs_group_context_store::GroupContextRepo> = match db_kind {
            DbPluginKind::LocalSqlite => {
                Arc::new(bcs_group_context_store::SqlGroupContextStore::sqlite(db_plugin.clone()))
            }
            DbPluginKind::Mysql => {
                Arc::new(bcs_group_context_store::SqlGroupContextStore::oceanbase(db_plugin.clone()))
            }
            DbPluginKind::External(provider) => {
                panic!(
                    "external database plugin '{provider}' has no group-context store wiring"
                )
            }
        };
        let group_context_service: Arc<dyn bcs_service_api::GroupContextApplicationService> =
            Arc::new(bcs_app_session::GroupContextAddAccessGuard::new(
                Arc::new(bcs_app_group_context::GroupContextApplicationServiceImpl::new(
                    group_context_repo,
                )),
                sessions.clone(),
                session_repo.clone(),
            ));
    group_context_service
}

// Caller association and gate invariants retained from the composition:
        // §15.83 S4 (review-VI P1): the production composition's single
        // orchestration mount. The same pipeline instance that serves the
        // delegate arm also implements the orchestration dispatch port and
        // the instance registrar; with_caller_orchestration mounts the
        // control-plane resolver alongside. No protected pipeline (no
        // [caller_identity]) degrades to the Refusing resolver so a
        // caller-enabled assignee never silently dispatches on the
        // ordinary wire. The BcsMessageFlow type annotation keeps the
        // concrete Arc so the pipeline can be read off it before the
        // wider dyn MessageFlowService binding below.
        // Owner→caller relay (design 2026-10-30): the same CallerRelayState
        // the admission seam captured into — bot-submitted caller-aware runs
        // redeem here. Absent relay state (an explicit deployment opt-out)
        // stays None and every bot submission keeps caller-closed behavior.
        // P1.4 caller dispatch gate: caller-enabled Bots are never
        // content-dispatched on ordinary transports — they answer only on
        // the protected caller wire. The decorator consults the
        // authoritative configuration through the same control plane
        // (cached path); unconfigured/disabled targets pass through
        // unchanged, aborts and availability checks are never blocked.
        // Not installable over the memory composition (no caller
        // configuration storage there).
