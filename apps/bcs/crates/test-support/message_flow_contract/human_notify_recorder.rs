//! Recording fake for the human mention notify port (contract tests).
#![allow(dead_code)]

use async_trait::async_trait;
use bcs_service_api::ServiceResult;


#[derive(Default)]
pub struct RecordingHumanMentionNotify {
    available: std::sync::atomic::AtomicBool,
    notifications: tokio::sync::Mutex<Vec<bcs_service_api::port::human_notify::MentionNotification>>,
}

impl RecordingHumanMentionNotify {
    pub fn available(available: bool) -> Self {
        Self {
            available: std::sync::atomic::AtomicBool::new(available),
            notifications: tokio::sync::Mutex::new(Vec::new()),
        }
    }

    pub async fn notifications(
        &self,
    ) -> Vec<bcs_service_api::port::human_notify::MentionNotification> {
        self.notifications.lock().await.clone()
    }

    /// Poll until `expected` notifications arrive or a 2s deadline passes.
    pub async fn wait_for(
        &self,
        expected: usize,
    ) -> Vec<bcs_service_api::port::human_notify::MentionNotification> {
        let deadline = tokio::time::Instant::now() + tokio::time::Duration::from_secs(2);
        loop {
            let count = self.notifications.lock().await.len();
            if count >= expected || tokio::time::Instant::now() >= deadline {
                return self.notifications.lock().await.clone();
            }
            tokio::time::sleep(tokio::time::Duration::from_millis(10)).await;
        }
    }

    /// Negative assertion helper: waits a fixed window during which a spawned
    /// notification would have arrived, then returns what was recorded
    /// (callers assert emptiness). `wait_for(0)` must NOT be used for
    /// negative assertions — it returns immediately.
    pub async fn wait_for_none(
        &self,
    ) -> Vec<bcs_service_api::port::human_notify::MentionNotification> {
        tokio::time::sleep(tokio::time::Duration::from_millis(200)).await;
        self.notifications.lock().await.clone()
    }
}

#[async_trait]
impl bcs_service_api::port::human_notify::HumanMentionNotifyPort for RecordingHumanMentionNotify {
    fn is_available(&self) -> bool {
        self.available.load(std::sync::atomic::Ordering::SeqCst)
    }

    async fn notify_mentioned_humans(
        &self,
        notification: bcs_service_api::port::human_notify::MentionNotification,
    ) -> ServiceResult<()> {
        self.notifications.lock().await.push(notification);
        Ok(())
    }
}
