//! Recovery scanner for completed Service Session callbacks.
//!
//! Normal Session completion still starts callback delivery immediately. This
//! scanner only revisits FO-era activations whose callback remains pending and
//! whose activation-aware lease is idle or expired. The callback dispatcher
//! performs the actual row claim and fencing checks.

use std::sync::Arc;
use std::time::Duration;

use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::application::session::SessionManagementService;
use bcs_service_api::{GroupCoreService, LeaderElectionPort};
use futures::stream::{self, StreamExt};
use tracing::{debug, info, warn};

pub const DEFAULT_SCAN_INTERVAL: Duration = Duration::from_secs(10);
pub const DEFAULT_BATCH_SIZE: u64 = 100;
const MAX_CONCURRENT_DISPATCHES: usize = 16;

async fn is_leader_for_tick(leader_election: &dyn LeaderElectionPort) -> bool {
    match leader_election.is_leader().await {
        Ok(true) => true,
        Ok(false) => {
            debug!(
                target: "callback_recovery_scanner",
                event = "scanner.tick_skipped",
                reason = "follower",
            );
            false
        }
        Err(error) => {
            warn!(
                target: "callback_recovery_scanner",
                event = "scanner.leader_check_failed",
                error = %error,
            );
            false
        }
    }
}

pub async fn scan_once(
    session_mgmt: &Arc<dyn SessionManagementService>,
    group_svc: &Arc<dyn GroupCoreService>,
    batch_size: u64,
) -> usize {
    scan_once_with_url_guard(
        session_mgmt,
        group_svc,
        batch_size,
        OutboundUrlGuard::strict(),
    )
    .await
}

pub async fn scan_once_with_url_guard(
    session_mgmt: &Arc<dyn SessionManagementService>,
    group_svc: &Arc<dyn GroupCoreService>,
    batch_size: u64,
    url_guard: OutboundUrlGuard,
) -> usize {
    if batch_size == 0 {
        return 0;
    }

    let now_ms = current_millis();
    let mut after_session_id: Option<String> = None;
    let mut scanned = 0usize;

    loop {
        let batch = match session_mgmt
            .list_recoverable_callbacks(now_ms, after_session_id.as_deref(), batch_size)
            .await
        {
            Ok(batch) => batch,
            Err(error) => {
                warn!(
                    target: "callback_recovery_scanner",
                    event = "scanner.scan_failed",
                    error = %error,
                );
                break;
            }
        };
        if batch.is_empty() {
            break;
        }
        let batch_len = batch.len();
        let next_after_session_id = batch.last().map(|session| session.id.clone());

        stream::iter(batch)
            .for_each_concurrent(MAX_CONCURRENT_DISPATCHES, |session| {
                let group_svc = group_svc.clone();
                let session_mgmt = session_mgmt.clone();
                let url_guard = url_guard.clone();
                async move {
                    bcs_callback::dispatch_for_session_with_url_guard(
                        session,
                        group_svc,
                        session_mgmt,
                        url_guard,
                    )
                    .await;
                }
            })
            .await;
        after_session_id = next_after_session_id;
        scanned += batch_len;

        if (batch_len as u64) < batch_size {
            break;
        }
    }

    scanned
}

pub fn spawn(
    leader_election: Arc<dyn LeaderElectionPort>,
    session_mgmt: Arc<dyn SessionManagementService>,
    group_svc: Arc<dyn GroupCoreService>,
    interval: Duration,
    batch_size: u64,
    url_guard: OutboundUrlGuard,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        info!(
            target: "callback_recovery_scanner",
            event = "scanner.started",
            interval_ms = interval.as_millis() as u64,
            batch_size,
        );
        let mut ticker = tokio::time::interval(interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            ticker.tick().await;
            if !is_leader_for_tick(leader_election.as_ref()).await {
                continue;
            }
            let scanned = scan_once_with_url_guard(
                &session_mgmt,
                &group_svc,
                batch_size,
                url_guard.clone(),
            )
            .await;
            if scanned > 0 {
                debug!(
                    target: "callback_recovery_scanner",
                    event = "scanner.tick",
                    scanned,
                );
            }
        }
    })
}

fn current_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
    use bcs_service_api::{Group, Participant, ParticipantRole, ServiceSpec, SessionKind};

    #[tokio::test]
    async fn scanner_drains_keyset_pages_and_normalizes_missing_callback_config() {
        use bcs_group::GroupCore;
        use bcs_group_store::MemoryGroupRepo;
        use bcs_session::SessionManagementServiceImpl;
        use bcs_session_store::MemorySessionRepo;

        let group_id = "callback-recovery-group";
        let participant = Participant::bot("driver", ParticipantRole::Driver);
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let mut group = Group::new(group_id, "driver", vec![participant.clone()]);
        group.service_spec = Some(ServiceSpec {
            callback_config: None,
            timeout_seconds: None,
            max_concurrency: None,
        });
        group_repo.upsert(group).await.expect("insert Group");

        let session_repo = Arc::new(MemorySessionRepo::new());
        let session_ids = [
            format!("{group_id}:00000001"),
            format!("{group_id}:00000002"),
        ];
        for id in &session_ids {
            session_repo
                .create(
                    group_id,
                    NewSessionParams {
                        id: Some(id.clone()),
                        session_kind: SessionKind::ServiceInvocation,
                        participants: vec![participant.clone()],
                        ..Default::default()
                    },
                )
                .await
                .expect("create Service Session");
            session_repo
                .complete_if_running(id, None, None)
                .await
                .expect("complete Service Session");
        }

        let session_mgmt: Arc<dyn SessionManagementService> = Arc::new(
            SessionManagementServiceImpl::new(session_repo.clone(), group_repo.clone()),
        );
        let group_svc: Arc<dyn GroupCoreService> =
            Arc::new(GroupCore::with_repo(group_repo));

        assert_eq!(scan_once(&session_mgmt, &group_svc, 1).await, 2);
        for id in &session_ids {
            assert_eq!(
                session_repo
                    .get(id)
                    .await
                    .expect("recovered Service Session")
                    .callback_status
                    .as_deref(),
                Some("not_applicable")
            );
        }
        assert_eq!(scan_once(&session_mgmt, &group_svc, 1).await, 0);
    }
}
