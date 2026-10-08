use std::sync::Arc;

use bcs_service_api::port::HumanMentionNotifyPort;
use futures::future::join_all;

use crate::config::BcsConfig;

/// Build the human mention notification port from the provider array.
///
/// Every enabled `[[human_notify.providers]]` entry is built and fans out in
/// parallel; a single backend failure is logged and isolated. Returns a
/// no-op port when no entry is configured or all entries are disabled.
/// Returns a startup error when two entries share a name, a name is blank,
/// an enabled provider is not linked into this binary, or its config is
/// invalid.
pub async fn build_human_mention_notify_port(
    config: &BcsConfig,
) -> crate::Result<Arc<dyn bcs_service_api::port::HumanMentionNotifyPort>> {
    let mut seen: std::collections::BTreeSet<String> = Default::default();
    for provider in &config.human_notify.providers {
        let name = provider.name.trim();
        if name.is_empty() {
            return Err(crate::BcsError::InvalidConfig(
                "human_notify provider name must not be blank".to_string(),
            ));
        }
        if !seen.insert(name.to_string()) {
            return Err(crate::BcsError::InvalidConfig(format!(
                "human_notify provider '{name}' is configured more than once"
            )));
        }
    }
    let mut adapters: Vec<HumanMentionNotifyAdapter> = Vec::new();
    for provider in &config.human_notify.providers {
        // 与上面的重复/空白校验一致，统一用 trimmed name 查找 factory，
        // 避免名字带空白时绕过配置校验、落到 "not available" 报错。
        let name = provider.name.trim();
        if !provider.enabled {
            tracing::info!(provider = %name, "human_notify provider disabled: skipping");
            continue;
        }
        let factory = inventory::iter::<bcs_human_notify_api::HumanMentionNotifierFactory>
            .into_iter()
            .find(|factory| factory.name == name)
            .ok_or_else(|| {
                crate::BcsError::InvalidConfig(format!(
                    "human_notify provider '{name}' is not available in this binary"
                ))
            })?;
        let notifier = (factory.build)(provider.clone())
            .await
            .map_err(human_notify_build_error)?;
        tracing::info!(provider = %name, "human_notify backend selected");
        adapters.push(HumanMentionNotifyAdapter::new(notifier));
    }
    if adapters.is_empty() {
        tracing::info!("human_notify disabled: no enabled provider configured");
        return Ok(Arc::new(bcs_service_api::port::NoopHumanMentionNotifyPort));
    }
    Ok(Arc::new(CompositeHumanMentionNotifyAdapter { adapters }))
}

fn human_notify_build_error(error: bcs_human_notify_api::HumanNotifyError) -> crate::BcsError {
    match error {
        bcs_human_notify_api::HumanNotifyError::Config(message) => {
            crate::BcsError::InvalidConfig(message)
        }
        // A factory is expected to fail with `Config`; a `Delivery` here means
        // the backend could not be initialized (e.g. reachability self-check),
        // which is the same failure class as other plugin init failures.
        bcs_human_notify_api::HumanNotifyError::Delivery(message) => {
            crate::BcsError::StorageInitError(message)
        }
    }
}

const HUMAN_NOTIFY_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);

/// Adapts a selected [`bcs_human_notify_api::HumanMentionNotifier`] to the
/// service port: translates the port DTO into the plugin-owned schema, logs
/// errors, and bounds runtime with a timeout.
pub struct HumanMentionNotifyAdapter {
    pub notifier: Arc<dyn bcs_human_notify_api::HumanMentionNotifier>,
    pub timeout: std::time::Duration,
}

impl HumanMentionNotifyAdapter {
    pub fn new(notifier: Arc<dyn bcs_human_notify_api::HumanMentionNotifier>) -> Self {
        Self {
            notifier,
            timeout: HUMAN_NOTIFY_TIMEOUT,
        }
    }
}

#[async_trait::async_trait]
impl bcs_service_api::port::HumanMentionNotifyPort for HumanMentionNotifyAdapter {
    fn is_available(&self) -> bool {
        true
    }

    async fn notify_mentioned_humans(
        &self,
        notification: bcs_service_api::port::human_notify::MentionNotification,
    ) -> bcs_service_api::ServiceResult<()> {
        let backend = self.notifier.backend_name();
        let group_id = notification.group_id.clone();
        let session_id = notification.session_id.clone();
        let mentioned_actors: Vec<String> = notification
            .mentioned
            .iter()
            .map(|human| human.actor_id.clone())
            .collect();
        let plugin_notification = bcs_human_notify_api::MentionNotification {
            session_id: notification.session_id,
            group_id: notification.group_id,
            group_name: notification.group_name,
            session_name: notification.session_name,
            sender_actor_id: notification.sender_actor_id,
            sender_label: notification.sender_label,
            mentioned: notification
                .mentioned
                .into_iter()
                .map(|human| bcs_human_notify_api::MentionedHuman {
                    actor_id: human.actor_id,
                    display_name: human.display_name,
                })
                .collect(),
            message_text: notification.message_text,
            timestamp_ms: notification.timestamp_ms,
        };
        match tokio::time::timeout(self.timeout, self.notifier.notify(&plugin_notification)).await
        {
            Ok(Ok(())) => Ok(()),
            Ok(Err(error)) => {
                tracing::error!(
                    backend,
                    group_id = %group_id,
                    session_id = %session_id,
                    mentioned = ?mentioned_actors,
                    "human mention notification failed: {error}"
                );
                Err(bcs_service_api::ServiceError::InternalError(error.to_string()))
            }
            Err(_elapsed) => {
                tracing::error!(
                    backend,
                    group_id = %group_id,
                    session_id = %session_id,
                    mentioned = ?mentioned_actors,
                    "human mention notification timed out"
                );
                Err(bcs_service_api::ServiceError::InternalError(
                    "human mention notification timed out".to_string(),
                ))
            }
        }
    }
}

/// Fans one port notification out to every selected notifier in parallel.
/// Each backend keeps its own DTO translation, timeout, and error logging
/// (see [`HumanMentionNotifyAdapter`]); a failing backend never fails the
/// batch because the message flow treats notifications as fire-and-forget.
pub struct CompositeHumanMentionNotifyAdapter {
    pub adapters: Vec<HumanMentionNotifyAdapter>,
}

#[async_trait::async_trait]
impl HumanMentionNotifyPort for CompositeHumanMentionNotifyAdapter {
    fn is_available(&self) -> bool {
        !self.adapters.is_empty()
    }

    async fn notify_mentioned_humans(
        &self,
        notification: bcs_service_api::port::human_notify::MentionNotification,
    ) -> bcs_service_api::ServiceResult<()> {
        let futures = self
            .adapters
            .iter()
            .map(|adapter| adapter.notify_mentioned_humans(notification.clone()))
            .collect::<Vec<_>>();
        let _ = join_all(futures).await;
        Ok(())
    }
}

#[cfg(test)]
mod human_notify_selection_tests {
    use super::*;
    use bcs_service_api::port::HumanMentionNotifyPort;
    use std::sync::Mutex;

    fn failing_notifier_factory(
        _config: bcs_config_api::HumanNotifyProviderConfig,
    ) -> futures::future::BoxFuture<
        'static,
        bcs_human_notify_api::HumanNotifyResult<
            Arc<dyn bcs_human_notify_api::HumanMentionNotifier>,
        >,
    > {
        Box::pin(async move {
            Err(bcs_human_notify_api::HumanNotifyError::Config("boom".to_string()))
        })
    }

    inventory::submit! {
        bcs_human_notify_api::HumanMentionNotifierFactory {
            name: "test-failing-notifier",
            build: failing_notifier_factory,
        }
    }

    static RECORDED_NOTIFICATIONS: Mutex<Vec<bcs_human_notify_api::MentionNotification>> =
        Mutex::new(Vec::new());

    struct RecordingNotifier;

    #[async_trait::async_trait]
    impl bcs_human_notify_api::HumanMentionNotifier for RecordingNotifier {
        fn backend_name(&self) -> &'static str {
            "test-recorder"
        }

        async fn notify(
            &self,
            notification: &bcs_human_notify_api::MentionNotification,
        ) -> bcs_human_notify_api::HumanNotifyResult<()> {
            RECORDED_NOTIFICATIONS
                .lock()
                .unwrap()
                .push(notification.clone());
            Ok(())
        }
    }

    fn recording_notifier_factory(
        _config: bcs_config_api::HumanNotifyProviderConfig,
    ) -> futures::future::BoxFuture<
        'static,
        bcs_human_notify_api::HumanNotifyResult<
            Arc<dyn bcs_human_notify_api::HumanMentionNotifier>,
        >,
    > {
        Box::pin(async move {
            Ok(Arc::new(RecordingNotifier) as Arc<dyn bcs_human_notify_api::HumanMentionNotifier>)
        })
    }

    inventory::submit! {
        bcs_human_notify_api::HumanMentionNotifierFactory {
            name: "test-recorder",
            build: recording_notifier_factory,
        }
    }

    fn entry(name: &str, enabled: bool) -> bcs_config_api::HumanNotifyProviderConfig {
        bcs_config_api::HumanNotifyProviderConfig {
            name: name.to_string(),
            enabled,
            options: std::collections::BTreeMap::new(),
        }
    }

    fn config_with(entries: &[bcs_config_api::HumanNotifyProviderConfig]) -> BcsConfig {
        let mut config = BcsConfig::default();
        config.human_notify.providers = entries.to_vec();
        config
    }

    #[tokio::test]
    async fn no_providers_selects_noop() {
        let port = build_human_mention_notify_port(&config_with(&[]))
            .await
            .unwrap();
        assert!(!port.is_available());
    }

    #[tokio::test]
    async fn disabled_entry_selects_noop() {
        let port = build_human_mention_notify_port(&config_with(&[entry("dummy", false)]))
            .await
            .unwrap();
        assert!(!port.is_available());
    }

    #[tokio::test]
    async fn duplicate_provider_name_is_startup_error() {
        let error = build_human_mention_notify_port(&config_with(&[
            entry("dummy", true),
            entry("dummy", true),
        ]))
        .await
        .err()
        .expect("duplicate names must fail startup");
        assert!(error.to_string().contains("more than once"));
    }

    #[tokio::test]
    async fn blank_provider_name_is_startup_error() {
        let error = build_human_mention_notify_port(&config_with(&[entry("  ", true)]))
            .await
            .err()
            .expect("blank name must fail startup");
        assert!(error.to_string().contains("blank"));
    }

    #[tokio::test]
    async fn unregistered_provider_is_startup_error() {
        let error =
            build_human_mention_notify_port(&config_with(&[entry("missing-backend", true)]))
                .await
                .err()
                .expect("unregistered backend must fail startup");
        assert!(error.to_string().contains("not available in this binary"));
    }

    #[tokio::test]
    async fn failing_factory_is_startup_error() {
        let error = build_human_mention_notify_port(&config_with(&[entry(
            "test-failing-notifier",
            true,
        )]))
        .await
        .err()
        .expect("factory config error must fail startup");
        assert!(matches!(error, crate::BcsError::InvalidConfig(_)));
        assert!(
            error.to_string().contains("boom"),
            "error must come from the registered failing factory: {error}"
        );
    }

    #[tokio::test]
    async fn dummy_backend_builds_available_port() {
        let port = build_human_mention_notify_port(&config_with(&[entry("dummy", true)]))
            .await
            .unwrap();
        assert!(port.is_available());
    }

    #[tokio::test]
    async fn all_enabled_backends_fan_out() {
        RECORDED_NOTIFICATIONS.lock().unwrap().clear();
        let port = build_human_mention_notify_port(&config_with(&[
            entry("dummy", true),
            entry("test-recorder", true),
        ]))
        .await
        .unwrap();
        assert!(port.is_available());

        port.notify_mentioned_humans(port_notification())
            .await
            .expect("fan-out never fails the message flow");

        let recorded = RECORDED_NOTIFICATIONS.lock().unwrap();
        assert_eq!(
            recorded.len(),
            1,
            "each enabled backend receives exactly one notification"
        );
        assert_eq!(recorded[0].mentioned.len(), 1);
    }

    // SlowNotifier / port_notification / adapter_bounds_runtime_with_timeout /
    // adapter_translates_port_dto_into_plugin_schema 从原模块原样保留（见
    // 现有 plugins.rs:1155-1246，无改动）。
    struct SlowNotifier;

    #[async_trait::async_trait]
    impl bcs_human_notify_api::HumanMentionNotifier for SlowNotifier {
        fn backend_name(&self) -> &'static str {
            "slow"
        }

        async fn notify(
            &self,
            _notification: &bcs_human_notify_api::MentionNotification,
        ) -> bcs_human_notify_api::HumanNotifyResult<()> {
            tokio::time::sleep(std::time::Duration::from_secs(60)).await;
            Ok(())
        }
    }

    fn port_notification() -> bcs_service_api::port::human_notify::MentionNotification {
        bcs_service_api::port::human_notify::MentionNotification {
            session_id: "group-1:s1".to_string(),
            group_id: "group-1".to_string(),
            group_name: Some("研发协作群".to_string()),
            session_name: Some("发布问题排查".to_string()),
            sender_actor_id: "human_1".to_string(),
            sender_label: "Human One".to_string(),
            mentioned: vec![bcs_service_api::port::human_notify::MentionedHuman {
                actor_id: "human_2".to_string(),
                display_name: "Human Two".to_string(),
            }],
            message_text: "hello".to_string(),
            timestamp_ms: 1_700_000_000_000,
        }
    }

    #[tokio::test]
    async fn adapter_bounds_runtime_with_timeout() {
        let adapter = HumanMentionNotifyAdapter {
            notifier: Arc::new(SlowNotifier),
            timeout: std::time::Duration::from_millis(50),
        };
        let error = adapter
            .notify_mentioned_humans(port_notification())
            .await
            .expect_err("slow backend must hit the timeout");
        assert!(
            error.to_string().contains("timed out"),
            "timeout must surface as the error: {error}"
        );
    }

    #[tokio::test]
    async fn adapter_translates_port_dto_into_plugin_schema() {
        use std::sync::Mutex;

        #[derive(Default)]
        struct RecordingNotifier {
            received: Mutex<Vec<bcs_human_notify_api::MentionNotification>>,
        }

        #[async_trait::async_trait]
        impl bcs_human_notify_api::HumanMentionNotifier for RecordingNotifier {
            fn backend_name(&self) -> &'static str {
                "recording"
            }

            async fn notify(
                &self,
                notification: &bcs_human_notify_api::MentionNotification,
            ) -> bcs_human_notify_api::HumanNotifyResult<()> {
                self.received.lock().unwrap().push(notification.clone());
                Ok(())
            }
        }

        let recorder = Arc::new(RecordingNotifier::default());
        let adapter = HumanMentionNotifyAdapter::new(recorder.clone());
        adapter
            .notify_mentioned_humans(port_notification())
            .await
            .expect("recording backend succeeds");

        let received = recorder.received.lock().unwrap();
        assert_eq!(received.len(), 1);
        let notification = &received[0];
        assert_eq!(notification.session_id, "group-1:s1");
        assert_eq!(notification.group_id, "group-1");
        assert_eq!(notification.group_name.as_deref(), Some("研发协作群"));
        assert_eq!(notification.session_name.as_deref(), Some("发布问题排查"));
        assert_eq!(notification.sender_actor_id, "human_1");
        assert_eq!(notification.sender_label, "Human One");
        assert_eq!(notification.mentioned.len(), 1);
        assert_eq!(notification.mentioned[0].actor_id, "human_2");
        assert_eq!(notification.mentioned[0].display_name, "Human Two");
        assert_eq!(notification.message_text, "hello");
        assert_eq!(notification.timestamp_ms, 1_700_000_000_000);
    }
}
