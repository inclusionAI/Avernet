mod helpers;

use async_trait::async_trait;
use bcs::metrics::MetricsRuntime;
use bcs_service_api::{
    ActorKind, ActorStatus, BotMetricCount, BotMetricsSnapshotPort, ChatRunMetricCount,
    DirectChatClientKind, DirectChatRunSnapshotPort, DirectChatRunState,
    GroupKind, GroupMetricCount, GroupMetricsSnapshotPort, GroupSessionMetricCount,
    GroupSessionMetricsSnapshotPort, GroupStatus, GroupStrategy, ServiceError, ServiceResult,
    SessionKind, SessionStatus,
};
use serial_test::serial;

#[cfg(feature = "prometheus-metrics")]
#[tokio::test]
#[serial]
async fn metrics_snapshot_refreshes_zeros_and_retains_on_failure() {
    let runtime = install_runtime();

    runtime
        .refresh_group_snapshot_for_test(&GroupSnapshot::counts(vec![
            GroupMetricCount {
                status: GroupStatus::Active,
                kind: GroupKind::Normal,
                group_strategy: GroupStrategy::StateMachine,
                service_mode: Some("custom-service-mode".to_string()),
                count: 2,
            },
        ]))
        .await;
    let body = runtime.render();
    assert_metric(&body,
        "bcs_groups_current{env=\"dev\",status=\"active\",kind=\"normal\",group_strategy=\"state_machine\",service_mode=\"other\"} 2"
    );
    assert!(!body.contains("custom-service-mode"));

    runtime
        .refresh_group_snapshot_for_test(&GroupSnapshot::error())
        .await;
    let body = runtime.render();
    assert_metric(&body,
        "bcs_groups_current{env=\"dev\",status=\"active\",kind=\"normal\",group_strategy=\"state_machine\",service_mode=\"other\"} 2"
    );

    runtime
        .refresh_group_snapshot_for_test(&GroupSnapshot::counts(Vec::new()))
        .await;
    let body = runtime.render();
    assert_metric(&body,
        "bcs_groups_current{env=\"dev\",status=\"active\",kind=\"normal\",group_strategy=\"state_machine\",service_mode=\"other\"} 0"
    );

    runtime
        .refresh_group_session_snapshot_for_test(&GroupSessionSnapshot::counts(vec![
            GroupSessionMetricCount {
                status: SessionStatus::Running,
                session_kind: SessionKind::ServiceInvocation,
                count: 4,
            },
        ]))
        .await;
    let body = runtime.render();
    assert_metric(&body,
        "bcs_group_sessions_current{env=\"dev\",status=\"running\",session_kind=\"service_invocation\"} 4"
    );

    runtime
        .refresh_group_session_snapshot_for_test(&GroupSessionSnapshot::counts(Vec::new()))
        .await;
    let body = runtime.render();
    assert_metric(&body,
        "bcs_group_sessions_current{env=\"dev\",status=\"running\",session_kind=\"service_invocation\"} 0"
    );

    runtime
        .refresh_bot_snapshot_for_test(&BotSnapshot::counts(vec![BotMetricCount {
            actor_kind: ActorKind::Bot,
            status: ActorStatus::Online,
            visibility: Some("custom-visibility".to_string()),
            count: 5,
        }]))
        .await;
    let body = runtime.render();
    assert_metric(&body,
        "bcs_bots_current{env=\"dev\",actor_kind=\"bot\",status=\"online\",visibility=\"other\"} 5"
    );
    assert!(!body.contains("custom-visibility"));

    runtime
        .refresh_bot_snapshot_for_test(&BotSnapshot::counts(Vec::new()))
        .await;
    let body = runtime.render();
    assert_metric(&body,
        "bcs_bots_current{env=\"dev\",actor_kind=\"bot\",status=\"online\",visibility=\"other\"} 0"
    );

    runtime
        .refresh_direct_chat_snapshot_for_test(&DirectChatSnapshot::counts(vec![
            ChatRunMetricCount {
                state: DirectChatRunState::Completed,
                client_kind: DirectChatClientKind::HttpChat,
                count: 3,
            },
        ]))
        .await;
    let body = runtime.render();
    assert_metric(&body,
        "bcs_direct_chat_runs_current{env=\"dev\",state=\"completed\",client_kind=\"http_chat\"} 3"
    );

    runtime
        .refresh_direct_chat_snapshot_for_test(&DirectChatSnapshot::counts(Vec::new()))
        .await;
    let body = runtime.render();
    assert_metric(&body,
        "bcs_direct_chat_runs_current{env=\"dev\",state=\"completed\",client_kind=\"http_chat\"} 0"
    );
}

/// Assert a metric fragment is present, substituting the canonical `env="dev"`
/// label with the env label the metrics runtime actually resolved at install
/// time (`SERVER_ENV`/`REAL_SERVER_ENV`/`ALIPAY_APP_ENV`, default `dev`).
#[cfg(feature = "prometheus-metrics")]
fn assert_metric(body: &str, expected_dev_form: &str) {
    let expected =
        expected_dev_form.replace("env=\"dev\"", &format!("env=\"{}\"", bcs::resolve_env()));
    assert!(
        body.contains(&expected),
        "missing metrics fragment: {expected}"
    );
}

#[cfg(feature = "prometheus-metrics")]
fn install_runtime() -> std::sync::Arc<MetricsRuntime> {
    let bots_dir = helpers::create_temp_bots_dir();
    let mut config = helpers::create_test_config(&bots_dir.path().to_path_buf());
    config.metrics.enabled = true;
    MetricsRuntime::install(&config)
        .expect("install metrics")
        .expect("metrics enabled")
}

struct GroupSnapshot {
    counts: ServiceResult<Vec<GroupMetricCount>>,
}

impl GroupSnapshot {
    fn counts(counts: Vec<GroupMetricCount>) -> Self {
        Self { counts: Ok(counts) }
    }

    fn error() -> Self {
        Self {
            counts: Err(ServiceError::InternalError("snapshot failed".to_string())),
        }
    }
}

#[async_trait]
impl GroupMetricsSnapshotPort for GroupSnapshot {
    async fn group_counts(&self) -> ServiceResult<Vec<GroupMetricCount>> {
        match &self.counts {
            Ok(counts) => Ok(counts.clone()),
            Err(_) => Err(ServiceError::InternalError("snapshot failed".to_string())),
        }
    }
}

struct GroupSessionSnapshot {
    counts: Vec<GroupSessionMetricCount>,
}

impl GroupSessionSnapshot {
    fn counts(counts: Vec<GroupSessionMetricCount>) -> Self {
        Self { counts }
    }
}

#[async_trait]
impl GroupSessionMetricsSnapshotPort for GroupSessionSnapshot {
    async fn group_session_counts(&self) -> ServiceResult<Vec<GroupSessionMetricCount>> {
        Ok(self.counts.clone())
    }
}

struct BotSnapshot {
    counts: Vec<BotMetricCount>,
}

impl BotSnapshot {
    fn counts(counts: Vec<BotMetricCount>) -> Self {
        Self { counts }
    }
}

#[async_trait]
impl BotMetricsSnapshotPort for BotSnapshot {
    async fn bot_counts(&self) -> ServiceResult<Vec<BotMetricCount>> {
        Ok(self.counts.clone())
    }
}

struct DirectChatSnapshot {
    counts: Vec<ChatRunMetricCount>,
}

impl DirectChatSnapshot {
    fn counts(counts: Vec<ChatRunMetricCount>) -> Self {
        Self { counts }
    }
}

#[async_trait]
impl DirectChatRunSnapshotPort for DirectChatSnapshot {
    async fn direct_chat_run_counts(&self) -> ServiceResult<Vec<ChatRunMetricCount>> {
        Ok(self.counts.clone())
    }
}
