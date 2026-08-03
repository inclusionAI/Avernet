//! Application-layer coordination for bot visibility synchronization.

use std::sync::Arc;

use bcs_service_api::{
    ActorKind, BotRegistryCoreService,
    port::{VisibilitySyncPort, VisibilitySyncRequest},
};

#[derive(Clone)]
pub struct VisibilitySyncCoordinator {
    registry: Arc<dyn BotRegistryCoreService>,
    sync: Arc<dyn VisibilitySyncPort>,
}

impl VisibilitySyncCoordinator {
    pub fn new(
        registry: Arc<dyn BotRegistryCoreService>,
        sync: Arc<dyn VisibilitySyncPort>,
    ) -> Self {
        Self { registry, sync }
    }

    /// Read the latest persisted bot state and wait for the best-effort sync.
    pub async fn sync_now(&self, bot_uuid: &str) {
        let Some(bot) = self.registry.get(bot_uuid).await else {
            return;
        };
        if bot.actor_kind == ActorKind::Human {
            return;
        }

        self.sync
            .sync_visibility(VisibilitySyncRequest {
                bot_uuid: bot_uuid.to_string(),
                capabilities: bot.capabilities,
            })
            .await;
    }

    /// Start one best-effort sync without waiting for the external port.
    pub fn schedule(&self, bot_uuid: String) {
        let coordinator = self.clone();
        let _task = tokio::spawn(async move {
            coordinator.sync_now(&bot_uuid).await;
        });
    }
}

#[cfg(test)]
mod tests {
    use std::{sync::Arc, time::Duration};

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
        release: Semaphore,
    }

    impl BlockingVisibilitySyncPort {
        fn new() -> Self {
            Self {
                requests: Mutex::new(Vec::new()),
                request_recorded: Notify::new(),
                release: Semaphore::new(0),
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
    }

    #[async_trait]
    impl VisibilitySyncPort for BlockingVisibilitySyncPort {
        async fn sync_visibility(&self, request: VisibilitySyncRequest) {
            self.requests.lock().await.push(request);
            self.request_recorded.notify_waiters();

            let permit = self.release.acquire().await.unwrap();
            permit.forget();
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

    #[tokio::test]
    async fn sync_now_waits_until_sync_port_completes() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        register_bot(&registry, "bot-a", "public").await;
        let sync = Arc::new(BlockingVisibilitySyncPort::new());
        let coordinator = VisibilitySyncCoordinator::new(registry, sync.clone());

        let task = tokio::spawn({
            let coordinator = coordinator.clone();
            async move { coordinator.sync_now("bot-a").await }
        });
        sync.wait_for_requests(1).await;

        assert!(!task.is_finished());
        sync.release.add_permits(1);
        tokio::time::timeout(Duration::from_secs(1), task)
            .await
            .expect("visibility sync should finish after release")
            .unwrap();
    }

    #[tokio::test]
    async fn sync_now_reads_latest_persisted_visibility() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        register_bot(&registry, "bot-a", "public").await;
        registry
            .update_visibility("bot-a", "private")
            .await
            .unwrap();
        let sync = Arc::new(BlockingVisibilitySyncPort::new());
        sync.release.add_permits(1);
        let coordinator = VisibilitySyncCoordinator::new(registry, sync.clone());

        coordinator.sync_now("bot-a").await;

        assert_eq!(
            sync.requests.lock().await[0].capabilities.visibility,
            "private"
        );
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

        assert_eq!(sync.requests.lock().await.len(), 1);
        sync.release.add_permits(1);
    }

    #[tokio::test]
    async fn sync_now_skips_missing_and_human_actors() {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        registry
            .ensure_human_actor("001", "Apple")
            .await
            .expect("register human actor");
        let sync = Arc::new(BlockingVisibilitySyncPort::new());
        let coordinator = VisibilitySyncCoordinator::new(registry, sync.clone());

        coordinator.sync_now("missing-bot").await;
        coordinator.sync_now("human_001").await;

        assert!(sync.requests.lock().await.is_empty());
    }
}
