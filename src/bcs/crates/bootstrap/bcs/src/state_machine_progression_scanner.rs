//! Schedules bounded progression pages; runtime owns every recovery decision.

use std::{sync::Arc, time::Duration};

use bcs_service_api::{CollaborationRuntimeService, LeaderElectionPort};
use tracing::warn;
use crate::recovery_backoff::{RecoveryBackoff, when_ready};

const SCAN_INTERVAL: Duration = Duration::from_secs(1);
const BATCH_SIZE: usize = 32;

pub struct ProgressionRecoveryTask(Option<tokio::task::JoinHandle<()>>);

impl ProgressionRecoveryTask {
    pub(crate) async fn shutdown(&mut self) {
        if let Some(task) = self.0.as_mut() {
            task.abort();
            // Abort only requests cancellation. Join before dependencies drain
            // so all in-flight recovery futures have actually been dropped.
            if let Err(error) = task.await {
                if !error.is_cancelled() {
                    warn!(target: "state_machine_progression_scanner", %error, "progression scanner stopped unexpectedly");
                }
            }
            self.0 = None;
        }
    }
}

impl Drop for ProgressionRecoveryTask {
    fn drop(&mut self) {
        if let Some(task) = &self.0 { task.abort(); }
    }
}

async fn is_leader(election: &dyn LeaderElectionPort) -> bool {
    match election.is_leader().await {
        Ok(leader) => leader,
        Err(error) => {
            warn!(target: "state_machine_progression_scanner", error = %error, "progression leader check failed");
            false
        }
    }
}

pub fn spawn(
    election: Arc<dyn LeaderElectionPort>,
    runtime: Arc<dyn CollaborationRuntimeService>,
) -> ProgressionRecoveryTask {
    ProgressionRecoveryTask(Some(tokio::spawn(run(election, runtime, SCAN_INTERVAL, BATCH_SIZE))))
}

async fn run(
    election: Arc<dyn LeaderElectionPort>,
    runtime: Arc<dyn CollaborationRuntimeService>,
    interval: Duration,
    batch_size: usize,
) {
    let mut cursor = None;
    let mut session_cursor = None;
    let mut im_cursor = None;
    let mut cleanup_cursor = None;
    let mut run_backoff = RecoveryBackoff::new(interval);
    let mut session_backoff = RecoveryBackoff::new(interval);
    let mut im_backoff = RecoveryBackoff::new(interval);
    let mut cleanup_backoff = RecoveryBackoff::new(interval);
    let mut ticker = tokio::time::interval(interval);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        ticker.tick().await;
        if !is_leader(election.as_ref()).await {
            cursor = None;
            session_cursor = None;
            im_cursor = None;
            cleanup_cursor = None;
            continue;
        }
        let run_page = when_ready(run_backoff.ready(), runtime.recover_state_machine_progression(cursor.clone(), batch_size));
        let session_page = when_ready(session_backoff.ready(), runtime.recover_state_machine_sessions(session_cursor.clone(), batch_size));
        let im_page = when_ready(im_backoff.ready(), runtime.recover_state_machine_terminal_im(im_cursor.clone(), batch_size));
        let cleanup_page = when_ready(cleanup_backoff.ready(), runtime.cleanup_state_machine_terminal_work(cleanup_cursor.clone(), batch_size));
        let scan = async { tokio::join!(run_page, session_page, im_page, cleanup_page) };
        tokio::pin!(scan);
        let result = loop {
            tokio::select! {
                result = &mut scan => break Some(result),
                _ = ticker.tick() => {
                    if !is_leader(election.as_ref()).await {
                        cursor = None;
                        session_cursor = None;
                        im_cursor = None;
                        cleanup_cursor = None;
                        break None;
                    }
                }
            }
        };
        // Demotion drops the in-flight page. Any completed CAS stays committed;
        // Bot requests with a send marker are not resent. An unsent checkpoint
        // or abandoned Judge lease can be claimed after expiry using saved input.
        if let Some((runs, sessions, notifications, cleanup)) = result {
            if let Some(cleanup) = cleanup {
                match cleanup {
                    Ok(page) => {
                        cleanup_backoff.record_page(page.scanned, !page.failures.is_empty());
                        cleanup_cursor = page.next_run_id;
                        for failure in page.failures {
                            warn!(target: "state_machine_progression_scanner", run_id = %failure.run_id,
                                error = %failure.error, "terminal checkpoint cleanup failed");
                        }
                    }
                    Err(error) => {
                        cleanup_backoff.record(true);
                        warn!(target: "state_machine_progression_scanner", error = %error, "terminal cleanup page failed");
                    }
                }
            }
            if let Some(runs) = runs {
                match runs {
                    Ok(page) => {
                        run_backoff.record_page(page.scanned, !page.failures.is_empty());
                        cursor = page.next_run_id;
                        for failure in page.failures {
                            warn!(target: "state_machine_progression_scanner", run_id = %failure.run_id,
                                error = %failure.error, "progression Run recovery failed");
                        }
                    }
                    Err(error) => {
                        run_backoff.record(true);
                        warn!(target: "state_machine_progression_scanner", error = %error, "progression page failed");
                    }
                }
            }
            if let Some(notifications) = notifications {
                match notifications {
                    Ok(page) => {
                        im_backoff.record_page(page.scanned, !page.failures.is_empty());
                        im_cursor = page.next_run_id;
                        for failure in page.failures {
                            warn!(target: "state_machine_progression_scanner", run_id = %failure.run_id,
                                error = %failure.error, "terminal IM recovery failed");
                        }
                    }
                    Err(error) => {
                        im_backoff.record(true);
                        warn!(target: "state_machine_progression_scanner", error = %error, "terminal IM page failed");
                    }
                }
            }
            if let Some(sessions) = sessions {
                match sessions {
                    Ok(page) => {
                        session_backoff.record_page(page.scanned, !page.failures.is_empty());
                        session_cursor = page.next_session_id;
                        for failure in page.failures {
                            warn!(target: "state_machine_progression_scanner", session_id = %failure.session_id,
                                error = %failure.error, "terminal Session recovery failed");
                        }
                    }
                    Err(error) => {
                        session_backoff.record(true);
                        warn!(target: "state_machine_progression_scanner", error = %error, "Session recovery page failed");
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Mutex, atomic::{AtomicBool, AtomicI8, AtomicUsize, Ordering}};
    use async_trait::async_trait;
    use bcs_service_api::*;
    use bcs_test_support::NoopCollaborationRuntimeService;

    struct Election { leader: AtomicI8, checks: AtomicUsize }
    #[async_trait]
    impl LeaderElectionPort for Election {
        async fn campaign(&self) -> ServiceResult<LeaderStatus> { Ok(LeaderStatus::Follower) }
        async fn current_leader(&self) -> ServiceResult<Option<LeaderInfo>> { Ok(None) }
        async fn is_leader(&self) -> ServiceResult<bool> {
            self.checks.fetch_add(1, Ordering::SeqCst);
            match self.leader.load(Ordering::SeqCst) {
                -1 => Err(ServiceError::InternalError("election unavailable".into())),
                value => Ok(value == 1),
            }
        }
    }

    struct Runtime {
        calls: Mutex<Vec<Option<String>>>,
        session_calls: Mutex<Vec<Option<String>>>,
        im_calls: Mutex<Vec<Option<String>>>,
        cleanup_calls: Mutex<Vec<Option<String>>>,
        fail_cleanup: AtomicBool,
        cleanup_cancelled: Arc<AtomicUsize>,
        fail_im: AtomicBool,
        im_cancelled: Arc<AtomicUsize>,
        fail_run: AtomicBool,
        fail_session: AtomicBool,
        session_cancelled: Arc<AtomicUsize>,
        blocked: bool,
        cancelled: Arc<AtomicUsize>,
    }
    struct Cancelled(Arc<AtomicUsize>);
    impl Drop for Cancelled {
        fn drop(&mut self) { self.0.fetch_add(1, Ordering::SeqCst); }
    }

    #[async_trait]
    impl CollaborationRuntimeService for Runtime {
        async fn cleanup_state_machine_terminal_work(&self, cursor: Option<String>, limit: usize) -> Result<StateMachineProgressionRecoveryPage, CollaborationRuntimeError> {
            assert_eq!(limit, 2); self.cleanup_calls.lock().unwrap().push(cursor.clone());
            if self.blocked { let _cancel = Cancelled(self.cleanup_cancelled.clone()); return std::future::pending().await; }
            if self.fail_cleanup.swap(false, Ordering::SeqCst) { return Err(CollaborationRuntimeError::InvalidRequest("cleanup page failed".into())); }
            Ok(StateMachineProgressionRecoveryPage { scanned: if cursor.is_none() { 2 } else { 1 },
                next_run_id: cursor.is_none().then(|| "cleanup-2".into()), ..Default::default() })
        }
        async fn recover_state_machine_terminal_im(&self, cursor: Option<String>, limit: usize) -> Result<StateMachineProgressionRecoveryPage, CollaborationRuntimeError> {
            assert_eq!(limit, 2); self.im_calls.lock().unwrap().push(cursor.clone());
            if self.blocked { let _cancel = Cancelled(self.im_cancelled.clone()); return std::future::pending().await; }
            if self.fail_im.swap(false, Ordering::SeqCst) { return Err(CollaborationRuntimeError::InvalidRequest("IM page failed".into())); }
            Ok(StateMachineProgressionRecoveryPage { scanned: if cursor.is_none() { 2 } else { 1 },
                next_run_id: cursor.is_none().then(|| "im-2".into()), ..Default::default() })
        }
        async fn recover_state_machine_progression(&self, cursor: Option<String>, limit: usize) -> Result<StateMachineProgressionRecoveryPage, CollaborationRuntimeError> {
            assert_eq!(limit, 2);
            self.calls.lock().unwrap().push(cursor.clone());
            if self.blocked {
                let _cancel = Cancelled(self.cancelled.clone());
                return std::future::pending().await;
            }
            if self.fail_run.swap(false, Ordering::SeqCst) { return Err(CollaborationRuntimeError::InvalidRequest("run page failed".into())); }
            Ok(StateMachineProgressionRecoveryPage {
                scanned: if cursor.is_none() { 2 } else { 1 }, reconciled: 0,
                next_run_id: cursor.is_none().then(|| "run-2".into()),
                failures: vec![StateMachineProgressionRecoveryFailure { run_id: "poison".into(), error: "injected".into() }],
            })
        }
        async fn recover_state_machine_sessions(&self, cursor: Option<String>, limit: usize) -> Result<StateMachineSessionRecoveryPage, CollaborationRuntimeError> {
            assert_eq!(limit, 2);
            self.session_calls.lock().unwrap().push(cursor.clone());
            if self.blocked {
                let _cancel = Cancelled(self.session_cancelled.clone());
                return std::future::pending().await;
            }
            if self.fail_session.swap(false, Ordering::SeqCst) { return Err(CollaborationRuntimeError::InvalidRequest("session page failed".into())); }
            Ok(StateMachineSessionRecoveryPage {
                scanned: if cursor.is_none() { 2 } else { 1 }, next_session_id: cursor.is_none().then(|| "session-2".into()),
                ..Default::default()
            })
        }
        async fn start_state_machine_run(&self, cmd: StartStateMachineRunCommand) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> { NoopCollaborationRuntimeService.start_state_machine_run(cmd).await }
        async fn get_state_machine_run(&self, id: &str) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> { NoopCollaborationRuntimeService.get_state_machine_run(id).await }
        async fn handle_session_human_input(&self, cmd: HandleSessionHumanInputCommand) -> Result<HandleSessionHumanInputOutcome, CollaborationRuntimeError> { NoopCollaborationRuntimeService.handle_session_human_input(cmd).await }
        async fn get_state_machine_session_history(&self, id: &str, limit: u64, before: Option<u64>) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> { NoopCollaborationRuntimeService.get_state_machine_session_history(id, limit, before).await }
        async fn cancel_state_machine_run(&self, cmd: CancelStateMachineRunCommand) -> Result<StateMachineRunView, CollaborationRuntimeError> { NoopCollaborationRuntimeService.cancel_state_machine_run(cmd).await }
        async fn lookup_delivery_correlation(&self, id: &str) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> { NoopCollaborationRuntimeService.lookup_delivery_correlation(id).await }
        async fn register_delivery_alias(&self, id: &str, alias: String) -> Result<(), CollaborationRuntimeError> { NoopCollaborationRuntimeService.register_delivery_alias(id, alias).await }
        async fn handle_bot_terminal_event(&self, cmd: HandleBotTerminalEventCommand) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> { NoopCollaborationRuntimeService.handle_bot_terminal_event(cmd).await }
        async fn upsert_definition(&self, definition: CollaborationDefinition) -> Result<(), CollaborationRuntimeError> { NoopCollaborationRuntimeService.upsert_definition(definition).await }
        async fn configure_group_runtime(&self, cmd: ConfigureGroupRuntimeCommand) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> { NoopCollaborationRuntimeService.configure_group_runtime(cmd).await }
    }

    fn start(leader: i8, blocked: bool) -> (Arc<Election>, Arc<Runtime>, ProgressionRecoveryTask) {
        let election = Arc::new(Election { leader: AtomicI8::new(leader), checks: AtomicUsize::new(0) });
        let runtime = Arc::new(Runtime {
            calls: Mutex::new(Vec::new()), session_calls: Mutex::new(Vec::new()), blocked,
            im_calls: Mutex::new(Vec::new()), fail_im: AtomicBool::new(false), im_cancelled: Arc::new(AtomicUsize::new(0)),
            cleanup_calls: Mutex::new(Vec::new()), fail_cleanup: AtomicBool::new(false), cleanup_cancelled: Arc::new(AtomicUsize::new(0)),
            fail_run: AtomicBool::new(false), fail_session: AtomicBool::new(false),
            cancelled: Arc::new(AtomicUsize::new(0)), session_cancelled: Arc::new(AtomicUsize::new(0)),
        });
        let task = ProgressionRecoveryTask(Some(tokio::spawn(run(election.clone(), runtime.clone(), Duration::from_millis(1), 2))));
        (election, runtime, task)
    }

    async fn until(condition: impl Fn() -> bool) {
        tokio::time::timeout(Duration::from_secs(1), async {
            while !condition() { tokio::task::yield_now().await; }
        }).await.unwrap();
    }

    #[tokio::test]
    async fn follower_and_uncertain_leader_never_scan() {
        for state in [0, -1] {
            let (election, runtime, task) = start(state, false);
            until(|| election.checks.load(Ordering::SeqCst) >= 3).await;
            assert!(runtime.calls.lock().unwrap().is_empty());
            assert!(runtime.session_calls.lock().unwrap().is_empty());
            assert!(runtime.im_calls.lock().unwrap().is_empty());
            assert!(runtime.cleanup_calls.lock().unwrap().is_empty());
            drop(task);
        }
    }

    #[tokio::test]
    async fn cursor_advances_past_failure_and_wraps_at_end_of_sweep() {
        let (_, runtime, task) = start(1, false);
        until(|| runtime.calls.lock().unwrap().len() >= 3).await;
        assert_eq!(&runtime.calls.lock().unwrap()[..3], &[None, Some("run-2".into()), None]);
        drop(task);
    }

    #[tokio::test]
    async fn candidate_failures_back_off_without_delaying_healthy_pages() {
        let (_, runtime, task) = start(1, false);
        // Run pages always report a poison candidate; Session pages succeed.
        until(|| runtime.session_calls.lock().unwrap().len() >= 20).await;
        assert!(runtime.calls.lock().unwrap().len() < 10);
        drop(task);
    }

    #[tokio::test]
    async fn page_errors_retain_only_the_failed_cursor() {
        for fail_run in [false, true] {
            let (_, runtime, task) = start(1, false);
            runtime.fail_run.store(fail_run, Ordering::SeqCst);
            runtime.fail_session.store(!fail_run, Ordering::SeqCst);
            until(|| runtime.calls.lock().unwrap().len() >= 3 && runtime.session_calls.lock().unwrap().len() >= 3).await;
            let runs = runtime.calls.lock().unwrap();
            let sessions = runtime.session_calls.lock().unwrap();
            if fail_run {
                assert_eq!(&runs[..3], &[None, None, Some("run-2".into())]);
                assert_eq!(&sessions[..3], &[None, Some("session-2".into()), None]);
            } else {
                assert_eq!(&runs[..3], &[None, Some("run-2".into()), None]);
                assert_eq!(&sessions[..3], &[None, None, Some("session-2".into())]);
            }
            drop(task);
        }
    }

    #[tokio::test]
    async fn terminal_im_uses_independent_cursor_and_retries_failed_page() {
        let (_, runtime, task) = start(1, false);
        runtime.fail_im.store(true, Ordering::SeqCst);
        until(|| runtime.im_calls.lock().unwrap().len() >= 3 && runtime.calls.lock().unwrap().len() >= 3).await;
        assert_eq!(&runtime.im_calls.lock().unwrap()[..3], &[None, None, Some("im-2".into())]);
        assert_eq!(&runtime.calls.lock().unwrap()[..3], &[None, Some("run-2".into()), None]);
        drop(task);
    }

    #[tokio::test]
    async fn terminal_cleanup_uses_independent_cursor_and_retries_failed_page() {
        let (_, runtime, task) = start(1, false);
        runtime.fail_cleanup.store(true, Ordering::SeqCst);
        until(|| runtime.cleanup_calls.lock().unwrap().len() >= 3 && runtime.calls.lock().unwrap().len() >= 3).await;
        assert_eq!(&runtime.cleanup_calls.lock().unwrap()[..3], &[None, None, Some("cleanup-2".into())]);
        assert_eq!(&runtime.calls.lock().unwrap()[..3], &[None, Some("run-2".into()), None]);
        drop(task);
    }

    #[tokio::test]
    async fn shutdown_joins_all_inflight_pages_before_dependencies_can_stop() {
        let (election, runtime, mut task) = start(1, true);
        until(|| !runtime.calls.lock().unwrap().is_empty()
            && !runtime.session_calls.lock().unwrap().is_empty()
            && !runtime.im_calls.lock().unwrap().is_empty()
            && !runtime.cleanup_calls.lock().unwrap().is_empty()).await;

        task.shutdown().await;
        // These are synchronous assertions at the dependency-teardown boundary:
        // abort-without-join would leave the four page futures alive here.
        assert_eq!(runtime.cancelled.load(Ordering::SeqCst), 1);
        assert_eq!(runtime.session_cancelled.load(Ordering::SeqCst), 1);
        assert_eq!(runtime.im_cancelled.load(Ordering::SeqCst), 1);
        assert_eq!(runtime.cleanup_cancelled.load(Ordering::SeqCst), 1);
        let checks = election.checks.load(Ordering::SeqCst);
        task.shutdown().await; // The final cleanup path can repeat shutdown.
        tokio::time::sleep(Duration::from_millis(5)).await;
        assert_eq!(election.checks.load(Ordering::SeqCst), checks);
    }

    #[tokio::test]
    async fn demotion_and_shutdown_cancel_inflight_recovery() {
        let (election, runtime, task) = start(1, true);
        until(|| !runtime.calls.lock().unwrap().is_empty()).await;
        election.leader.store(0, Ordering::SeqCst);
        until(|| runtime.cancelled.load(Ordering::SeqCst) == 1 && runtime.session_cancelled.load(Ordering::SeqCst) == 1 && runtime.im_cancelled.load(Ordering::SeqCst) == 1 && runtime.cleanup_cancelled.load(Ordering::SeqCst) == 1).await;
        election.leader.store(1, Ordering::SeqCst);
        until(|| runtime.calls.lock().unwrap().len() == 2).await;
        assert_eq!(&*runtime.calls.lock().unwrap(), &[None, None]);
        assert_eq!(&*runtime.session_calls.lock().unwrap(), &[None, None]);
        assert_eq!(&*runtime.cleanup_calls.lock().unwrap(), &[None, None]);
        drop(task);
        until(|| runtime.cancelled.load(Ordering::SeqCst) == 2 && runtime.session_cancelled.load(Ordering::SeqCst) == 2 && runtime.im_cancelled.load(Ordering::SeqCst) == 2 && runtime.cleanup_cancelled.load(Ordering::SeqCst) == 2).await;
    }
}
