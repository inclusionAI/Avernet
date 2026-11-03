//! Extracted verbatim from `server.rs`: fusion service and leader-election lifecycle wiring.
use super::*;

/// Create fusion service: bcsfuse HTTP delegation or local fallback.
pub(super) fn create_fusion_service(
    config: &BcsConfig,
) -> (
    Arc<dyn bcs_service_api::FusionCoreService>,
    Option<Arc<FuseClient>>,
) {
    if config.bcsfuse.enabled {
        match FuseClientService::new(&config.bcsfuse, &config.bots_base_dir) {
            Ok(svc) => {
                info!(url = %config.bcsfuse.url, "bcsfuse integration enabled");
                let shared_client = svc.client();
                (Arc::new(svc), Some(shared_client))
            }
            Err(e) => {
                warn!(error = %e, "Failed to create FuseClientService, falling back to local fusion");
                (
                    Arc::new(LocalFusionService::new(config.bots_base_dir.clone())),
                    None,
                )
            }
        }
    } else {
        (
            Arc::new(LocalFusionService::new(config.bots_base_dir.clone())),
            None,
        )
    }
}

pub(super) fn create_standalone_leader_lifecycle() -> (
    Arc<dyn LeaderElectionPort>,
    Arc<Mutex<LifecycleOrchestrator>>,
) {
    let leader = Arc::new(StandaloneLeaderElection::local());
    lifecycle_with_leader("leader_election", leader)
}

pub(super) fn create_leader_lifecycle(
    leader_election: Option<LeaderElectionRegistration>,
) -> (
    Arc<dyn LeaderElectionPort>,
    Arc<Mutex<LifecycleOrchestrator>>,
) {
    if let Some(registration) = leader_election {
        let mut lifecycle = LifecycleOrchestrator::new();
        if let Some(service) = registration.lifecycle {
            lifecycle.register("leader_election", service);
        }
        info!("Using configured leader election provider");
        return (registration.leader, Arc::new(Mutex::new(lifecycle)));
    }

    create_standalone_leader_lifecycle()
}

pub(super) async fn create_configured_leader_election(
    config: &BcsConfig,
) -> Result<Option<LeaderElectionRegistration>> {
    let Some(election) = config.leader_election.as_ref() else {
        return Ok(None);
    };
    if !election.enabled {
        return Ok(None);
    }

    let provider = election
        .provider
        .as_deref()
        .map(str::trim)
        .filter(|provider| !provider.is_empty())
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(
                "leader_election.provider is required when leader_election.enabled = true"
                    .to_string(),
            )
        })?;

    let provider_config = election
        .providers
        .get(provider)
        .cloned()
        .unwrap_or_default();

    build_registered_leader_election(config, provider, provider_config)
        .await?
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(format!(
                "leader_election provider '{provider}' is not available in this binary"
            ))
        })
        .map(Some)
}

fn lifecycle_with_leader<L>(
    name: &'static str,
    leader: Arc<L>,
) -> (
    Arc<dyn LeaderElectionPort>,
    Arc<Mutex<LifecycleOrchestrator>>,
)
where
    L: LeaderElectionPort + ServiceLifecycle + 'static,
{
    let leader_election: Arc<dyn LeaderElectionPort> = leader.clone();
    let lifecycle_service: Arc<dyn ServiceLifecycle> = leader;
    let mut lifecycle = LifecycleOrchestrator::new();
    lifecycle.register(name, lifecycle_service);
    (leader_election, Arc::new(Mutex::new(lifecycle)))
}

/// Register FuseClientLifecycle (and any other late-bound lifecycle adapters)
/// onto the orchestrator. Must run after fuse_client is constructed but
/// before BcsServer::run begins driving initialize_all/shutdown_all.
///
/// Sync helper — orchestrator is freshly built and has zero contention at
/// this point, so try_lock always succeeds. Avoids polluting the call sites
/// with async/await chains.
pub(super) fn register_late_lifecycles(
    lifecycle: &Arc<Mutex<LifecycleOrchestrator>>,
    fuse_client: Option<&Arc<FuseClient>>,
) {
    if let Some(client) = fuse_client {
        let adapter = Arc::new(bcs_fusion::FuseClientLifecycle::new(client.clone()));
        // try_lock cannot fail here: the orchestrator has just been built and
        // is not yet shared with any other task.
        let mut guard = lifecycle
            .try_lock()
            .expect("orchestrator should be uncontended at registration time");
        guard.register("fuse_client", adapter as Arc<dyn ServiceLifecycle>);
    }
}

pub(super) fn register_channel_lifecycles(
    lifecycle: &Arc<Mutex<LifecycleOrchestrator>>,
    channel_lifecycles: &[Arc<dyn ServiceLifecycle>],
) {
    if channel_lifecycles.is_empty() {
        return;
    }
    let mut guard = lifecycle
        .try_lock()
        .expect("orchestrator should be uncontended at registration time");
    for (idx, service) in channel_lifecycles.iter().enumerate() {
        let name = match idx {
            0 => "channel_provider",
            1 => "channel_provider_1",
            2 => "channel_provider_2",
            _ => "channel_provider_extra",
        };
        guard.register(name, service.clone());
    }
}

pub(super) fn register_eventing_lifecycles(
    lifecycle: &Arc<Mutex<LifecycleOrchestrator>>,
    eventing_lifecycle: Option<&Arc<dyn ServiceLifecycle>>,
    provisioning_lifecycle: Option<&Arc<dyn ServiceLifecycle>>,
) {
    let mut guard = lifecycle
        .try_lock()
        .expect("orchestrator should be uncontended at registration time");
    if let Some(service) = eventing_lifecycle {
        guard.register("eventing", service.clone());
    }
    if let Some(service) = provisioning_lifecycle {
        guard.register("group-provisioning-reconciler", service.clone());
    }
}
