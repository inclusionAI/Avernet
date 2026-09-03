//! Dummy human mention notification backend: logs each notification and
//! performs no delivery. Default backend for public/local deployments.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_config_api::HumanNotifyProviderConfig;
use bcs_human_notify_api::{
    HumanMentionNotifier, HumanMentionNotifierFactory, HumanNotifyResult, MentionNotification,
};
use futures::future::BoxFuture;

pub const DUMMY_BACKEND_NAME: &str = "dummy";

/// Notification backend that only emits log lines.
pub struct DummyHumanMentionNotifier;

impl DummyHumanMentionNotifier {
    pub fn new() -> Self {
        Self
    }
}

impl Default for DummyHumanMentionNotifier {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl HumanMentionNotifier for DummyHumanMentionNotifier {
    fn backend_name(&self) -> &'static str {
        DUMMY_BACKEND_NAME
    }

    async fn notify(&self, notification: &MentionNotification) -> HumanNotifyResult<()> {
        for human in &notification.mentioned {
            tracing::info!(
                backend = DUMMY_BACKEND_NAME,
                session_id = %notification.session_id,
                group_id = %notification.group_id,
                sender_actor_id = %notification.sender_actor_id,
                actor_id = %human.actor_id,
                display_name = %human.display_name,
                message_chars = notification.message_text.chars().count(),
                "dummy human mention notification (no delivery)"
            );
        }
        Ok(())
    }
}

pub fn build_dummy_notifier(
    _config: HumanNotifyProviderConfig,
) -> BoxFuture<'static, HumanNotifyResult<Arc<dyn HumanMentionNotifier>>> {
    Box::pin(async move {
        Ok(Arc::new(DummyHumanMentionNotifier::new()) as Arc<dyn HumanMentionNotifier>)
    })
}

inventory::submit! {
    HumanMentionNotifierFactory {
        name: DUMMY_BACKEND_NAME,
        build: build_dummy_notifier,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn dummy_backend_reports_name_and_succeeds() {
        let notifier = DummyHumanMentionNotifier::new();
        assert_eq!(notifier.backend_name(), "dummy");
        let notification = MentionNotification {
            session_id: String::new(),
            group_id: "group-1".to_string(),
            sender_actor_id: "bot-driver".to_string(),
            sender_label: "Driver".to_string(),
            mentioned: vec![bcs_human_notify_api::MentionedHuman {
                actor_id: "human_1".to_string(),
                display_name: "Human One".to_string(),
            }],
            message_text: "hello".to_string(),
            timestamp_ms: 0,
        };
        notifier.notify(&notification).await.expect("dummy succeeds");
    }
}
