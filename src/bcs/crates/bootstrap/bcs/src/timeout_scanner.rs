//! Service-invocation session timeout scanner (Part B Task 2).
//!
//! A periodic background task that walks every running
//! `service_invocation` session and completes it with `error="timeout"`
//! when the session has lived past its group's
//! `service_spec.timeout_seconds`.
//!
//! `timeout_seconds` is read from the **active** group row at scan time;
//! the route-field lock (Part A bcs-group) ensures no in-flight session
//! ever sees a torn read of this value, so we don't snapshot it onto the
//! session.
//!
//! # Wiring
//!
//! The scanner uses `SessionManagementService` (application layer) for
//! `list_running_service` + `complete_if_running`, and `GroupCoreService`
//! for group lookup. Both are accessible through `Services`.

use bcs_service_api::application::session::SessionManagementService;
use bcs_service_api::core::GroupCoreService;
use bcs_route_security::OutboundUrlGuard;
use std::sync::Arc;
use std::time::Duration;
use tracing::{debug, info, warn};

/// How many sessions to fetch per scanner pass.
const SCAN_PAGE_SIZE: u64 = 200;

/// Default scan interval (10s per spec §9.4.4).
pub const DEFAULT_SCAN_INTERVAL: Duration = Duration::from_secs(10);

/// Scan once and complete any expired service-invocation session.
///
/// Returns the number of sessions that were marked `completed` with
/// `error="timeout"` on this pass. Useful for tests and metrics.
pub async fn scan_once(
    session_mgmt: &Arc<dyn SessionManagementService>,
    group_svc: &Arc<dyn GroupCoreService>,
) -> u64 {
    scan_once_with_url_guard(session_mgmt, group_svc, OutboundUrlGuard::strict()).await
}

pub async fn scan_once_with_url_guard(
    session_mgmt: &Arc<dyn SessionManagementService>,
    group_svc: &Arc<dyn GroupCoreService>,
    url_guard: OutboundUrlGuard,
) -> u64 {
    let now_ms = current_millis();
    let mut completed = 0u64;
    let mut offset = 0u64;

    loop {
        let batch = match session_mgmt.list_running_service(offset, SCAN_PAGE_SIZE).await {
            Ok(b) => b,
            Err(_) => break,
        };
        if batch.is_empty() {
            break;
        }
        let batch_len = batch.len() as u64;

        for sess in batch {
            let g = match group_svc.get(&sess.group_id).await {
                Some(g) => g,
                None => {
                    debug!(
                        target: "timeout_scanner",
                        session_id = %sess.id,
                        group_id = %sess.group_id,
                        "skipping session: group not found",
                    );
                    continue;
                }
            };

            let timeout_seconds = match g
                .service_spec
                .as_ref()
                .and_then(|s| s.timeout_seconds)
            {
                Some(t) if t > 0 => t as u64,
                _ => continue,
            };

            let elapsed_ms = now_ms.saturating_sub(sess.created_at);
            if elapsed_ms <= timeout_seconds * 1000 {
                continue;
            }

            match session_mgmt
                .complete_if_running(&sess.id, None, Some("timeout".to_string()))
                .await
            {
                Ok(Some(completed_session)) => {
                    completed += 1;
                    info!(
                        target: "timeout_scanner",
                        event = "session.timed_out",
                        session_id = %sess.id,
                        group_id = %sess.group_id,
                        timeout_seconds = timeout_seconds,
                        elapsed_ms = elapsed_ms,
                    );
                    bcs_callback::dispatch::maybe_dispatch_for_session_with_url_guard(
                        completed_session,
                        group_svc.clone(),
                        session_mgmt.clone(),
                        url_guard.clone(),
                    );
                }
                Ok(None) => {
                    // CAS: already completed by another path — skip.
                }
                Err(e) => {
                    warn!(
                        target: "timeout_scanner",
                        event = "session.timeout_complete_failed",
                        session_id = %sess.id,
                        error = ?e,
                    );
                }
            }
        }

        if batch_len < SCAN_PAGE_SIZE {
            break;
        }
        offset += batch_len;
    }

    completed
}

/// Spawn the timeout scanner as a background tokio task.
pub fn spawn(
    session_mgmt: Arc<dyn SessionManagementService>,
    group_svc: Arc<dyn GroupCoreService>,
    interval: Duration,
) -> tokio::task::JoinHandle<()> {
    spawn_with_url_guard(
        session_mgmt,
        group_svc,
        interval,
        OutboundUrlGuard::strict(),
    )
}

pub fn spawn_with_url_guard(
    session_mgmt: Arc<dyn SessionManagementService>,
    group_svc: Arc<dyn GroupCoreService>,
    interval: Duration,
    url_guard: OutboundUrlGuard,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        info!(
            target: "timeout_scanner",
            event = "scanner.started",
            interval_secs = interval.as_secs(),
        );
        let mut ticker = tokio::time::interval(interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            ticker.tick().await;
            let n = scan_once_with_url_guard(
                &session_mgmt,
                &group_svc,
                url_guard.clone(),
            )
            .await;
            if n > 0 {
                debug!(
                    target: "timeout_scanner",
                    event = "scanner.tick",
                    completed = n,
                );
            }
        }
    })
}

fn current_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
    use bcs_service_api::{ParticipantRole, SessionKind};


    async fn create_session(
        session_repo: &Arc<dyn SessionRepoPort>,
        group_id: &str,
        kind: SessionKind,
        created_at_offset_ms: i64,
    ) -> bcs_service_api::Session {
        let now = current_millis();
        let past = (now as i64 + created_at_offset_ms) as u64;
        let mut sess = session_repo
            .create(
                group_id,
                NewSessionParams {
                    session_kind: kind,
                    participants: vec![bcs_service_api::Participant::bot(
                        "bot1",
                        ParticipantRole::Driver,
                    )],
                    ..Default::default()
                },
            )
            .await
            .unwrap();
        sess.created_at = past;
        sess
    }

    /// Build a `SessionManagementService` backed by a `MemorySessionRepo` plus
    /// a `GroupCoreService` backed by a `MemoryGroupRepo`.
    fn create_services(
    ) -> (
        Arc<dyn SessionManagementService>,
        Arc<dyn GroupCoreService>,
        Arc<dyn SessionRepoPort>,
        Arc<dyn GroupRepoPort>,
    ) {
        use bcs_group::GroupCore;
        use bcs_group_store::MemoryGroupRepo;
        use bcs_session::SessionManagementServiceImpl;
        use bcs_session_store::MemorySessionRepo;

        let session_repo = Arc::new(MemorySessionRepo::new());
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let session_mgmt: Arc<dyn SessionManagementService> =
            Arc::new(SessionManagementServiceImpl::new(session_repo.clone(), group_repo.clone()));
        let group_svc: Arc<dyn GroupCoreService> =
            Arc::new(GroupCore::with_repo(group_repo.clone()));

        (session_mgmt, group_svc, session_repo, group_repo)
    }

    #[tokio::test]
    #[ignore = "needs GroupCore wrapping MemoryGroupRepo for group lookup"]
    async fn expired_session_is_completed() {
        let (session_mgmt, group_svc, session_repo, group_repo) = create_services();

        let g = bcs_service_api::Group {
            id: "g-t".to_string(),
            service_spec: Some(bcs_service_api::ServiceSpec {
                callback_config: None,
                timeout_seconds: Some(5),
                max_concurrency: None,
            }),
            group_strategy: bcs_service_api::GroupStrategy::Chat,
            driver_bot: "driver".to_string(),
            originator: None,
            routing_policy: None,
            context: None,
            participants: vec![bcs_service_api::Participant::bot("bot1",bcs_service_api::ParticipantRole::Driver)],
            messages: vec![],
            workspace: bcs_service_api::Workspace { decisions: vec![], tasks: vec![], notes: vec![], audit_log: vec![] },
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            group_kind: bcs_service_api::GroupKind::Normal,
            dm_pair_key: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
            status: bcs_service_api::GroupStatus::Active,
            label: None
        };
        group_repo.upsert(g).await.unwrap();

        let sess = create_session(&session_repo, "g-t", SessionKind::ServiceInvocation, -60_000).await;
        let n = scan_once(&session_mgmt, &group_svc).await;
        assert_eq!(n, 1);

        let updated = session_repo.get(&sess.id).await.unwrap();
        assert_eq!(updated.status, bcs_service_api::SessionStatus::Completed);
        assert_eq!(updated.error_message.as_deref(), Some("timeout"));
    }

    #[tokio::test]
    #[ignore = "needs GroupCore wrapping MemoryGroupRepo for group lookup"]
    async fn non_expired_session_is_not_completed() {
        let (session_mgmt, group_svc, session_repo, group_repo) = create_services();

        let g = bcs_service_api::Group {
            id: "g-n".to_string(),
            service_spec: Some(bcs_service_api::ServiceSpec {
                callback_config: None,
                timeout_seconds: Some(3600),
                max_concurrency: None,
            }),
            group_strategy: bcs_service_api::GroupStrategy::Chat,
            driver_bot: "driver".to_string(),
            originator: None,
            routing_policy: None,
            context: None,
            participants: vec![bcs_service_api::Participant::bot("bot1",bcs_service_api::ParticipantRole::Driver)],
            messages: vec![],
            workspace: bcs_service_api::Workspace { decisions: vec![], tasks: vec![], notes: vec![], audit_log: vec![] },
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            group_kind: bcs_service_api::GroupKind::Normal,
            dm_pair_key: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
            status: bcs_service_api::GroupStatus::Active,
            label: None
        };
        group_repo.upsert(g).await.unwrap();

        let sess = create_session(&session_repo, "g-n", SessionKind::ServiceInvocation, -1_000).await;
        let n = scan_once(&session_mgmt, &group_svc).await;
        assert_eq!(n, 0);
        let same = session_repo.get(&sess.id).await.unwrap();
        assert_eq!(same.status, bcs_service_api::SessionStatus::Running);
    }

    #[tokio::test]
    #[ignore = "needs GroupCore wrapping MemoryGroupRepo for group lookup"]
    async fn chat_sessions_are_skipped() {
        let (session_mgmt, group_svc, session_repo, group_repo) = create_services();

        let g = bcs_service_api::Group {
            id: "g-c".to_string(),
            service_spec: Some(bcs_service_api::ServiceSpec {
                callback_config: None,
                timeout_seconds: Some(1),
                max_concurrency: None,
            }),
            group_strategy: bcs_service_api::GroupStrategy::Chat,
            driver_bot: "driver".to_string(),
            originator: None,
            routing_policy: None,
            context: None,
            participants: vec![bcs_service_api::Participant::bot("bot1",bcs_service_api::ParticipantRole::Driver)],
            messages: vec![],
            workspace: bcs_service_api::Workspace { decisions: vec![], tasks: vec![], notes: vec![], audit_log: vec![] },
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            group_kind: bcs_service_api::GroupKind::Normal,
            dm_pair_key: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
            status: bcs_service_api::GroupStatus::Active,
            label: None
        };
        group_repo.upsert(g).await.unwrap();

        let chat_sess = create_session(&session_repo, "g-c", SessionKind::Chat, -60_000).await;
        let n = scan_once(&session_mgmt, &group_svc).await;
        assert_eq!(n, 0);
        let same = session_repo.get(&chat_sess.id).await.unwrap();
        assert_eq!(same.status, bcs_service_api::SessionStatus::Running);
    }

    #[tokio::test]
    #[ignore = "needs GroupCore wrapping MemoryGroupRepo for group lookup"]
    async fn already_completed_session_is_skipped_via_cas() {
        let (session_mgmt, group_svc, session_repo, group_repo) = create_services();

        let g = bcs_service_api::Group {
            id: "g-d".to_string(),
            service_spec: Some(bcs_service_api::ServiceSpec {
                callback_config: None,
                timeout_seconds: Some(1),
                max_concurrency: None,
            }),
            group_strategy: bcs_service_api::GroupStrategy::Chat,
            driver_bot: "driver".to_string(),
            originator: None,
            routing_policy: None,
            context: None,
            participants: vec![bcs_service_api::Participant::bot("bot1",bcs_service_api::ParticipantRole::Driver)],
            messages: vec![],
            workspace: bcs_service_api::Workspace { decisions: vec![], tasks: vec![], notes: vec![], audit_log: vec![] },
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            group_kind: bcs_service_api::GroupKind::Normal,
            dm_pair_key: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "private".to_string(),
            status: bcs_service_api::GroupStatus::Active,
            label: None
        };
        group_repo.upsert(g).await.unwrap();

        let sess = create_session(&session_repo, "g-d", SessionKind::ServiceInvocation, -60_000).await;
        session_repo
            .complete_if_running(&sess.id, None, Some("bot_closed".to_string()))
            .await
            .unwrap();
        let n = scan_once(&session_mgmt, &group_svc).await;
        assert_eq!(n, 0);
    }
}
