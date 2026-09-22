//! One bounded election observation per instance, independent of socket count.
use std::{sync::Arc, time::Duration};
use bcs_service_api::LeaderElectionPort;
use bcs_ws::{bot::BotConnectionRegistry, web::WorkbenchConnectionRegistry};
use tokio_util::sync::CancellationToken;
use crate::server::BcsServerState;

const CHECK_INTERVAL: Duration = Duration::from_secs(1);
const CHECK_TIMEOUT: Duration = Duration::from_secs(1);

struct Connections {
    bots: Arc<BotConnectionRegistry>,
    workbench: Arc<WorkbenchConnectionRegistry>,
}

impl Connections {
    fn set_accepting(&self, accepting: bool) {
        self.bots.connection_epoch.set_accepting(accepting);
        self.workbench.connection_epoch.set_accepting(accepting);
    }
}

// Also close admissions when an aborted or panicking task is dropped.
struct CloseAdmissions(Arc<Connections>);
impl Drop for CloseAdmissions {
    fn drop(&mut self) { self.0.set_accepting(false); }
}

pub(crate) struct WsLeadershipTask {
    task: tokio::task::JoinHandle<()>,
    stop: CancellationToken,
    connections: Arc<Connections>,
}

impl WsLeadershipTask {
    pub(crate) fn stop_handle(&self) -> CancellationToken { self.stop.clone() }
}

impl Drop for WsLeadershipTask {
    fn drop(&mut self) {
        self.stop.cancel();
        self.task.abort();
        self.connections.set_accepting(false);
    }
}

pub(crate) async fn start(state: Arc<BcsServerState>) -> WsLeadershipTask {
    start_monitor(state.leader_election.clone(), Arc::new(Connections {
        bots: state.bot_connections.clone(),
        workbench: state.frontend_connections.clone(),
    })).await
}

async fn observe(election: &dyn LeaderElectionPort) -> Option<bool> {
    // Errors/timeouts are unknown, not evidence of demotion.
    tokio::time::timeout(CHECK_TIMEOUT, election.is_leader()).await.ok()?.ok()
}

async fn start_monitor(election: Arc<dyn LeaderElectionPort>, connections: Arc<Connections>) -> WsLeadershipTask {
    // No admissions until the first positive observation; standalone resolves true.
    connections.set_accepting(false);
    match observe(election.as_ref()).await {
        Some(leader) => connections.set_accepting(leader),
        None => tracing::warn!("Initial WS leadership check unavailable; admissions remain closed"),
    }
    let stop = CancellationToken::new();
    let task_stop = stop.clone();
    let task_connections = connections.clone();
    let close_admissions = CloseAdmissions(connections.clone());
    let task = tokio::spawn(async move {
        let _close_admissions = close_admissions;
        let mut next_warning = tokio::time::Instant::now();
        loop {
            // Sleep after each query: at most one check/sec, no overlapping queries.
            tokio::select! {
                biased;
                _ = task_stop.cancelled() => break,
                _ = tokio::time::sleep(CHECK_INTERVAL) => {}
            }
            let observation = tokio::select! {
                biased;
                _ = task_stop.cancelled() => break,
                observation = observe(election.as_ref()) => observation,
            };
            match observation {
                Some(leader) => task_connections.set_accepting(leader),
                None if tokio::time::Instant::now() >= next_warning => {
                    tracing::warn!("WS leadership check failed or timed out; retaining previous admission state");
                    next_warning = tokio::time::Instant::now() + Duration::from_secs(30);
                }
                None => {}
            }
        }
        task_connections.set_accepting(false);
    });
    WsLeadershipTask { task, stop, connections }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use bcs_service_api::{LeaderInfo, LeaderStatus, ServiceError, ServiceResult};

    struct Election { mode: AtomicUsize, calls: AtomicUsize }
    #[async_trait::async_trait]
    impl LeaderElectionPort for Election {
        async fn campaign(&self) -> ServiceResult<LeaderStatus> { panic!("monitor must not campaign") }
        async fn current_leader(&self) -> ServiceResult<Option<LeaderInfo>> { panic!("not used") }
        async fn is_leader(&self) -> ServiceResult<bool> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            match self.mode.load(Ordering::SeqCst) {
                0 => Ok(false),
                1 => Ok(true),
                2 => Err(ServiceError::BotNotFound("election unavailable test".into())),
                _ => std::future::pending().await,
            }
        }
    }

    fn connections() -> Arc<Connections> {
        Arc::new(Connections {
            bots: Arc::new(BotConnectionRegistry::new()),
            workbench: Arc::new(WorkbenchConnectionRegistry::new()),
        })
    }

    async fn wait_for(mut condition: impl FnMut() -> bool) {
        tokio::time::timeout(Duration::from_secs(5), async {
            while !condition() { tokio::time::sleep(Duration::from_millis(10)).await; }
        }).await.expect("monitor state transition");
    }

    #[tokio::test]
    async fn monitors_both_transports_preserves_unknown_and_stops_on_drop() {
        let election = Arc::new(Election { mode: AtomicUsize::new(1), calls: AtomicUsize::new(0) });
        let connections = connections();
        let task = start_monitor(election.clone(), connections.clone()).await;
        let bot = connections.bots.connection_epoch.subscribe();
        let web = connections.workbench.connection_epoch.subscribe();
        assert!(!bot.is_cancelled());
        assert!(!web.is_cancelled());
        election.mode.store(2, Ordering::SeqCst);
        wait_for(|| election.calls.load(Ordering::SeqCst) >= 2).await;
        assert!(!bot.is_cancelled(), "error must retain last state");
        election.mode.store(3, Ordering::SeqCst);
        wait_for(|| election.calls.load(Ordering::SeqCst) >= 3).await;
        tokio::time::sleep(CHECK_TIMEOUT + Duration::from_millis(50)).await;
        assert!(!bot.is_cancelled(), "timeout must retain last state");
        election.mode.store(0, Ordering::SeqCst);
        wait_for(|| bot.is_cancelled()).await;
        assert!(web.is_cancelled());
        assert!(connections.bots.connection_epoch.subscribe().is_cancelled());
        election.mode.store(1, Ordering::SeqCst);
        wait_for(|| !connections.bots.connection_epoch.subscribe().is_cancelled()).await;
        assert!(bot.is_cancelled(), "promotion cannot revive old sockets");
        let fresh = connections.workbench.connection_epoch.subscribe();
        drop(task);
        assert!(fresh.is_cancelled());
        let count = election.calls.load(Ordering::SeqCst);
        tokio::time::sleep(CHECK_INTERVAL + Duration::from_millis(50)).await;
        assert_eq!(election.calls.load(Ordering::SeqCst), count);
    }

    #[tokio::test]
    async fn startup_unknown_is_closed_and_shutdown_signal_closes_admissions() {
        let election = Arc::new(Election { mode: AtomicUsize::new(2), calls: AtomicUsize::new(0) });
        let connections = connections();
        let task = start_monitor(election.clone(), connections.clone()).await;
        assert!(connections.bots.connection_epoch.subscribe().is_cancelled());
        election.mode.store(1, Ordering::SeqCst);
        wait_for(|| !connections.bots.connection_epoch.subscribe().is_cancelled()).await;
        let socket = connections.bots.connection_epoch.subscribe();
        task.stop_handle().cancel();
        wait_for(|| socket.is_cancelled()).await;
    }
}
