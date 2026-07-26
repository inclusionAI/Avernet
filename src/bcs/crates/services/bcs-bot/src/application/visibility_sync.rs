//! Shared best-effort coordination for bot visibility synchronization.

use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

use bcs_service_api::{
    ActorKind, BotRegistryCoreService,
    port::{VisibilitySyncPort, VisibilitySyncRequest},
};

#[derive(Clone)]
pub struct VisibilitySyncCoordinator {
    registry: Arc<dyn BotRegistryCoreService>,
    sync: Arc<dyn VisibilitySyncPort>,
    jobs: Arc<Mutex<HashMap<String, VisibilitySyncJob>>>,
}

struct VisibilitySyncJob {
    dirty: bool,
    token: Arc<()>,
}

struct VisibilitySyncJobCleanup {
    coordinator: VisibilitySyncCoordinator,
    bot_uuid: String,
    token: Arc<()>,
}

impl Drop for VisibilitySyncJobCleanup {
    fn drop(&mut self) {
        let should_restart = {
            let mut jobs = self.coordinator.jobs.lock().unwrap();
            match jobs.get(&self.bot_uuid) {
                Some(job) if Arc::ptr_eq(&job.token, &self.token) => {
                    let dirty = job.dirty;
                    jobs.remove(&self.bot_uuid);
                    dirty
                }
                _ => false,
            }
        };
        if should_restart && tokio::runtime::Handle::try_current().is_ok() {
            self.coordinator.schedule(self.bot_uuid.clone());
        }
    }
}

impl VisibilitySyncCoordinator {
    pub fn new(
        registry: Arc<dyn BotRegistryCoreService>,
        sync: Arc<dyn VisibilitySyncPort>,
    ) -> Self {
        Self {
            registry,
            sync,
            jobs: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Request a best-effort sync without waiting for the external port.
    ///
    /// At most one worker runs for a bot. Requests that arrive while its
    /// worker is active mark the bot dirty, causing one more read of the
    /// registry and sync of the latest state.
    pub fn schedule(&self, bot_uuid: String) {
        let token = Arc::new(());
        let should_start = {
            let mut jobs = self.jobs.lock().unwrap();
            if let Some(job) = jobs.get_mut(&bot_uuid) {
                job.dirty = true;
                false
            } else {
                jobs.insert(
                    bot_uuid.clone(),
                    VisibilitySyncJob {
                        dirty: false,
                        token: token.clone(),
                    },
                );
                true
            }
        };
        if !should_start {
            return;
        }

        let coordinator = self.clone();
        let _task = tokio::spawn(async move {
            let _cleanup = VisibilitySyncJobCleanup {
                coordinator: coordinator.clone(),
                bot_uuid: bot_uuid.clone(),
                token: token.clone(),
            };
            coordinator.run(bot_uuid, token).await;
        });
    }

    async fn run(&self, bot_uuid: String, token: Arc<()>) {
        loop {
            if let Some(bot) = self.registry.get(&bot_uuid).await {
                if bot.actor_kind != ActorKind::Human {
                    self.sync
                        .sync_visibility(VisibilitySyncRequest {
                            bot_uuid: bot_uuid.clone(),
                            capabilities: bot.capabilities,
                        })
                        .await;
                }
            }

            let should_repeat = {
                let mut jobs = self.jobs.lock().unwrap();
                match jobs.get_mut(&bot_uuid) {
                    Some(job) if Arc::ptr_eq(&job.token, &token) && job.dirty => {
                        job.dirty = false;
                        true
                    }
                    Some(job) if Arc::ptr_eq(&job.token, &token) => {
                        jobs.remove(&bot_uuid);
                        false
                    }
                    _ => false,
                }
            };
            if !should_repeat {
                break;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::{
        collections::HashSet,
        sync::{
            Arc,
            atomic::{AtomicUsize, Ordering},
        },
        time::Duration,
    };

    use async_trait::async_trait;
    use bcs_service_api::{
        BotCapabilities, BotRegistryCoreService,
        port::{VisibilitySyncPort, VisibilitySyncRequest},
    };
    use tempfile::TempDir;
    use tokio::sync::{Mutex, Notify, Semaphore};

    use super::VisibilitySyncCoordinator;
    use crate::core::BotCore;

    struct BlockingVisibilitySyncPort {
        requests: Mutex<Vec<VisibilitySyncRequest>>,
        request_recorded: Notify,
        request_finished: Notify,
        release: Semaphore,
        active: AtomicUsize,
        max_active: AtomicUsize,
    }

    struct PanicFirstVisibilitySyncPort {
        attempts: AtomicUsize,
        requests: Mutex<Vec<VisibilitySyncRequest>>,
        first_started: Notify,
        release_first: Semaphore,
        request_recorded: Notify,
    }

    impl PanicFirstVisibilitySyncPort {
        fn new() -> Self {
            Self {
                attempts: AtomicUsize::new(0),
                requests: Mutex::new(Vec::new()),
                first_started: Notify::new(),
                release_first: Semaphore::new(0),
                request_recorded: Notify::new(),
            }
        }
    }

    #[async_trait]
    impl VisibilitySyncPort for PanicFirstVisibilitySyncPort {
        async fn sync_visibility(&self, request: VisibilitySyncRequest) {
            if self.attempts.fetch_add(1, Ordering::SeqCst) == 0 {
                self.first_started.notify_waiters();
                let permit = self.release_first.acquire().await.unwrap();
                permit.forget();
                panic!("intentional first visibility sync panic");
            }
            self.requests.lock().await.push(request);
            self.request_recorded.notify_waiters();
        }
    }

    impl BlockingVisibilitySyncPort {
        fn new() -> Self {
            Self {
                requests: Mutex::new(Vec::new()),
                request_recorded: Notify::new(),
                request_finished: Notify::new(),
                release: Semaphore::new(0),
                active: AtomicUsize::new(0),
                max_active: AtomicUsize::new(0),
            }
        }

        async fn wait_for_requests(&self, count: usize) {
            tokio::time::timeout(Duration::from_secs(1), async {
                loop {
                    let notified = self.request_recorded.notified();
                    if self.requests.lock().await.len() >= count {
                        break;
                    }
                    notified.await;
                }
            })
            .await
            .expect("visibility sync request should arrive");
        }

        async fn wait_until_idle(&self) {
            tokio::time::timeout(Duration::from_secs(1), async {
                loop {
                    let notified = self.request_finished.notified();
                    if self.active.load(Ordering::SeqCst) == 0 {
                        break;
                    }
                    notified.await;
                }
            })
            .await
            .expect("visibility sync requests should finish");
        }
    }

    #[async_trait]
    impl VisibilitySyncPort for BlockingVisibilitySyncPort {
        async fn sync_visibility(&self, request: VisibilitySyncRequest) {
            let active = self.active.fetch_add(1, Ordering::SeqCst) + 1;
            self.max_active.fetch_max(active, Ordering::SeqCst);
            self.requests.lock().await.push(request);
            self.request_recorded.notify_waiters();

            let permit = self.release.acquire().await.unwrap();
            permit.forget();

            self.active.fetch_sub(1, Ordering::SeqCst);
            self.request_finished.notify_waiters();
        }
    }

    async fn register_bot(registry: &Arc<BotCore>, bot_uuid: &str, visibility: &str) {
        registry
            .register_with_owner_and_token(
                bot_uuid.to_string(),
                BotCapabilities {
                    name: Some(bot_uuid.to_string()),
                    visibility: visibility.to_string(),
                    ..BotCapabilities::default()
                },
                "test-owner",
                "test-token",
            )
            .await
            .unwrap();
    }

    async fn wait_for_job_to_finish(coordinator: &VisibilitySyncCoordinator, bot_uuid: &str) {
        tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                if !coordinator.jobs.lock().unwrap().contains_key(bot_uuid) {
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("visibility sync job should finish");
    }

    #[tokio::test]
    async fn schedule_returns_while_sync_port_is_still_blocked() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        register_bot(&registry, "bot-a", "public").await;
        let sync = Arc::new(BlockingVisibilitySyncPort::new());
        let coordinator = VisibilitySyncCoordinator::new(registry, sync.clone());

        coordinator.schedule("bot-a".to_string());
        sync.wait_for_requests(1).await;

        assert_eq!(sync.active.load(Ordering::SeqCst), 1);
        sync.release.add_permits(1);
        sync.wait_until_idle().await;
    }

    #[tokio::test]
    async fn same_bot_is_serial_and_coalesces_to_latest_visibility() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        register_bot(&registry, "bot-a", "public").await;
        let sync = Arc::new(BlockingVisibilitySyncPort::new());
        let coordinator = VisibilitySyncCoordinator::new(registry.clone(), sync.clone());

        coordinator.schedule("bot-a".to_string());
        sync.wait_for_requests(1).await;

        registry
            .update_visibility("bot-a", "protected")
            .await
            .unwrap();
        coordinator.schedule("bot-a".to_string());
        registry
            .update_visibility("bot-a", "private")
            .await
            .unwrap();
        coordinator.schedule("bot-a".to_string());

        sync.release.add_permits(1);
        sync.wait_for_requests(2).await;

        let requests = sync.requests.lock().await;
        assert_eq!(requests[0].capabilities.visibility, "public");
        assert_eq!(requests[1].capabilities.visibility, "private");
        drop(requests);
        assert_eq!(sync.max_active.load(Ordering::SeqCst), 1);

        sync.release.add_permits(1);
        sync.wait_until_idle().await;
        wait_for_job_to_finish(&coordinator, "bot-a").await;
        assert_eq!(sync.requests.lock().await.len(), 2);
    }

    #[tokio::test]
    async fn different_bots_sync_concurrently() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        register_bot(&registry, "bot-a", "public").await;
        register_bot(&registry, "bot-b", "protected").await;
        let sync = Arc::new(BlockingVisibilitySyncPort::new());
        let coordinator = VisibilitySyncCoordinator::new(registry, sync.clone());

        coordinator.schedule("bot-a".to_string());
        coordinator.schedule("bot-b".to_string());
        sync.wait_for_requests(2).await;

        assert_eq!(sync.max_active.load(Ordering::SeqCst), 2);
        let requests = sync.requests.lock().await;
        let bot_uuids = requests
            .iter()
            .map(|request| request.bot_uuid.as_str())
            .collect::<HashSet<_>>();
        assert_eq!(bot_uuids, HashSet::from(["bot-a", "bot-b"]));
        drop(requests);

        sync.release.add_permits(2);
        sync.wait_until_idle().await;
    }

    #[tokio::test]
    async fn missing_bot_exits_without_calling_port_or_sticking_job() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        let sync = Arc::new(BlockingVisibilitySyncPort::new());
        let coordinator = VisibilitySyncCoordinator::new(registry.clone(), sync.clone());

        coordinator.schedule("missing-bot".to_string());
        wait_for_job_to_finish(&coordinator, "missing-bot").await;
        assert!(sync.requests.lock().await.is_empty());

        register_bot(&registry, "missing-bot", "private").await;
        coordinator.schedule("missing-bot".to_string());
        sync.wait_for_requests(1).await;
        assert_eq!(
            sync.requests.lock().await[0].capabilities.visibility,
            "private"
        );

        sync.release.add_permits(1);
        sync.wait_until_idle().await;
    }

    #[tokio::test]
    async fn human_actor_is_not_sent_to_visibility_sync_port() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        registry
            .ensure_human_actor("001", "Apple")
            .await
            .expect("register human actor");
        let sync = Arc::new(BlockingVisibilitySyncPort::new());
        let coordinator = VisibilitySyncCoordinator::new(registry, sync.clone());

        coordinator.schedule("human_001".to_string());
        wait_for_job_to_finish(&coordinator, "human_001").await;

        assert!(sync.requests.lock().await.is_empty());
    }

    #[tokio::test]
    async fn panicked_worker_restarts_an_already_dirty_job_with_latest_state() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        register_bot(&registry, "bot-a", "public").await;
        let sync = Arc::new(PanicFirstVisibilitySyncPort::new());
        let coordinator = VisibilitySyncCoordinator::new(registry.clone(), sync.clone());

        coordinator.schedule("bot-a".to_string());
        tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                let notified = sync.first_started.notified();
                if sync.attempts.load(Ordering::SeqCst) >= 1 {
                    break;
                }
                notified.await;
            }
        })
        .await
        .expect("first visibility sync should start");

        registry
            .update_visibility("bot-a", "private")
            .await
            .unwrap();
        coordinator.schedule("bot-a".to_string());
        sync.release_first.add_permits(1);
        tokio::time::timeout(Duration::from_secs(1), async {
            loop {
                let notified = sync.request_recorded.notified();
                if !sync.requests.lock().await.is_empty() {
                    break;
                }
                notified.await;
            }
        })
        .await
        .expect("later visibility sync request should recover");

        assert_eq!(sync.attempts.load(Ordering::SeqCst), 2);
        assert_eq!(
            sync.requests.lock().await[0].capabilities.visibility,
            "private"
        );
    }
}
