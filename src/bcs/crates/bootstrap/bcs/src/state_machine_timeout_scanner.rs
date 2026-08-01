//! State-machine node timeout scanner.
//!
//! The runtime owns timeout semantics and CAS protection; this module only
//! schedules periodic scans and drains full batches to avoid backlog growth.

use std::sync::Arc;
use std::time::Duration;

use bcs_service_api::{CollaborationRuntimeService, LeaderElectionPort};
use tracing::{debug, info, warn};

pub const DEFAULT_SCAN_INTERVAL: Duration = Duration::from_millis(1_000);
pub const DEFAULT_BATCH_SIZE: usize = 100;
pub const DEFAULT_TIMEOUT_GRACE_MS: u64 = 500;

async fn is_leader_for_tick(leader_election: &dyn LeaderElectionPort) -> bool {
    match leader_election.is_leader().await {
        Ok(true) => true,
        Ok(false) => {
            debug!(
                target: "state_machine_timeout_scanner",
                event = "scanner.tick_skipped",
                reason = "follower",
                "state-machine timeout scanner tick skipped on follower"
            );
            false
        }
        Err(error) => {
            warn!(
                target: "state_machine_timeout_scanner",
                event = "scanner.leader_check_failed",
                error = %error,
                "state-machine timeout scanner tick skipped because leader check failed"
            );
            false
        }
    }
}

pub async fn scan_once(
    runtime: &Arc<dyn CollaborationRuntimeService>,
    batch_size: usize,
    timeout_grace_ms: u64,
) -> usize {
    if batch_size == 0 {
        return 0;
    }
    let mut total = 0usize;
    loop {
        match runtime
            .process_expired_node_timeouts(batch_size, timeout_grace_ms)
            .await
        {
            Ok(processed) => {
                total += processed;
                if processed < batch_size {
                    break;
                }
            }
            Err(error) => {
                warn!(
                    target: "state_machine_timeout_scanner",
                    event = "scanner.scan_failed",
                    error = %error,
                );
                break;
            }
        }
    }
    total
}

pub fn spawn(
    leader_election: Arc<dyn LeaderElectionPort>,
    runtime: Arc<dyn CollaborationRuntimeService>,
    interval: Duration,
    batch_size: usize,
    timeout_grace_ms: u64,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        info!(
            target: "state_machine_timeout_scanner",
            event = "scanner.started",
            interval_ms = interval.as_millis() as u64,
            batch_size = batch_size,
            timeout_grace_ms = timeout_grace_ms,
        );
        let mut ticker = tokio::time::interval(interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            ticker.tick().await;
            if !is_leader_for_tick(leader_election.as_ref()).await {
                continue;
            }
            let processed = scan_once(&runtime, batch_size, timeout_grace_ms).await;
            if processed > 0 {
                debug!(
                    target: "state_machine_timeout_scanner",
                    event = "scanner.tick",
                    processed = processed,
                );
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use async_trait::async_trait;
    use bcs_service_api::{LeaderInfo, LeaderStatus, ServiceResult};
    use bcs_test_support::NoopCollaborationRuntimeService;

    use super::*;

    enum LeaderCheck {
        Leader,
        Follower,
        Error,
    }

    struct FixedLeaderElection {
        check: LeaderCheck,
    }

    struct CountingFollowerElection {
        checks: Arc<AtomicUsize>,
    }

    #[async_trait]
    impl LeaderElectionPort for FixedLeaderElection {
        async fn campaign(&self) -> ServiceResult<LeaderStatus> {
            Ok(match self.check {
                LeaderCheck::Leader => LeaderStatus::Leader,
                LeaderCheck::Follower | LeaderCheck::Error => LeaderStatus::Follower,
            })
        }

        async fn is_leader(&self) -> ServiceResult<bool> {
            match self.check {
                LeaderCheck::Leader => Ok(true),
                LeaderCheck::Follower => Ok(false),
                LeaderCheck::Error => Err(bcs_service_api::ServiceError::InternalError(
                    "leader unavailable".to_string(),
                )),
            }
        }

        async fn current_leader(&self) -> ServiceResult<Option<LeaderInfo>> {
            Ok(None)
        }
    }

    #[async_trait]
    impl LeaderElectionPort for CountingFollowerElection {
        async fn campaign(&self) -> ServiceResult<LeaderStatus> {
            Ok(LeaderStatus::Follower)
        }

        async fn is_leader(&self) -> ServiceResult<bool> {
            self.checks.fetch_add(1, Ordering::SeqCst);
            Ok(false)
        }

        async fn current_leader(&self) -> ServiceResult<Option<LeaderInfo>> {
            Ok(None)
        }
    }

    #[tokio::test]
    async fn leader_allows_tick() {
        let leader = FixedLeaderElection {
            check: LeaderCheck::Leader,
        };
        assert_eq!(
            leader.campaign().await.expect("campaign"),
            LeaderStatus::Leader
        );
        assert!(leader
            .current_leader()
            .await
            .expect("current leader")
            .is_none());

        assert!(is_leader_for_tick(&leader).await);
    }

    #[tokio::test]
    async fn follower_skips_tick() {
        let follower = FixedLeaderElection {
            check: LeaderCheck::Follower,
        };
        assert_eq!(
            follower.campaign().await.expect("campaign"),
            LeaderStatus::Follower
        );

        assert!(!is_leader_for_tick(&follower).await);
    }

    #[tokio::test]
    async fn leader_check_error_skips_tick() {
        let unavailable = FixedLeaderElection {
            check: LeaderCheck::Error,
        };

        assert!(!is_leader_for_tick(&unavailable).await);
    }

    #[tokio::test]
    async fn scanner_checks_leadership_on_every_tick() {
        let checks = Arc::new(AtomicUsize::new(0));
        let handle = spawn(
            Arc::new(CountingFollowerElection {
                checks: checks.clone(),
            }),
            Arc::new(NoopCollaborationRuntimeService),
            Duration::from_millis(1),
            DEFAULT_BATCH_SIZE,
            DEFAULT_TIMEOUT_GRACE_MS,
        );

        tokio::time::timeout(Duration::from_secs(1), async {
            while checks.load(Ordering::SeqCst) < 3 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("scanner should check leadership repeatedly");

        handle.abort();
        assert!(handle.await.expect_err("scanner aborted").is_cancelled());
    }
}
