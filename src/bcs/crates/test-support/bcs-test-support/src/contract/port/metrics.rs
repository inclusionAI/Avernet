//! Metrics port and hook contract harnesses.

use std::collections::HashSet;
use std::time::Duration;

use bcs_service_api::{
    BotMetricsSnapshotPort, DeliveryBlockContext, DeliveryBlockReason, DeliveryBlockSurface, DeliveryMetricKind,
    DeliveryMetricTarget, DeliveryPolicyBlockInstrumentationHook, DirectChatClientKind,
    DirectChatRunEvent, DirectChatRunLifecycleHook, DirectChatRunReason, DirectChatRunSnapshotPort,
    GroupMetricsSnapshotPort, GroupSessionMetricsSnapshotPort, GroupStrategy, MetricsResult,
    WsCloseReason, WsErrorKind, WsLifecycleInstrumentationHook, WsPeer,
};

pub async fn group_metrics_snapshot_port_contract_tests<T: GroupMetricsSnapshotPort + ?Sized>(
    port: &T,
) {
    let counts = port
        .group_counts()
        .await
        .expect("group_counts");
    let mut seen = HashSet::new();
    for count in counts {
        assert!(count.count > 0, "snapshot counts should omit zero rows");
        if let Some(service_mode) = count.service_mode.as_deref() {
            assert!(
                matches!(service_mode, "none" | "master_slave" | "other"),
                "service_mode must be low-cardinality, got {service_mode}"
            );
        }
        assert!(
            matches!(
                count.group_strategy,
                GroupStrategy::Chat | GroupStrategy::ManagerWorker | GroupStrategy::StateMachine
            ),
            "group_strategy must be a closed enum value"
        );
        let key = format!(
            "{:?}:{:?}:{:?}:{}",
            count.status,
            count.kind,
            count.group_strategy,
            count.service_mode.as_deref().unwrap_or("none")
        );
        assert!(seen.insert(key), "duplicate group metric tuple");
    }
}

pub async fn group_session_metrics_snapshot_port_contract_tests<
    T: GroupSessionMetricsSnapshotPort + ?Sized,
>(
    port: &T,
) {
    let counts = port
        .group_session_counts()
        .await
        .expect("group_session_counts");
    let mut seen = HashSet::new();
    for count in counts {
        assert!(count.count > 0, "snapshot counts should omit zero rows");
        let key = format!("{:?}:{:?}", count.status, count.session_kind);
        assert!(
            seen.insert(key),
            "duplicate group session metric tuple"
        );
    }
}

pub async fn bot_metrics_snapshot_port_contract_tests<T: BotMetricsSnapshotPort + ?Sized>(
    port: &T,
) {
    let counts = port
        .bot_counts()
        .await
        .expect("bot_counts");
    let mut seen = HashSet::new();
    for count in counts {
        assert!(count.count > 0, "snapshot counts should omit zero rows");
        if let Some(visibility) = count.visibility.as_deref() {
            assert!(
                matches!(visibility, "public" | "protected" | "private" | "other"),
                "visibility must be low-cardinality, got {visibility}"
            );
        }
        let key = format!(
            "{:?}:{:?}:{}",
            count.actor_kind,
            count.status,
            count.visibility.as_deref().unwrap_or("other")
        );
        assert!(seen.insert(key), "duplicate bot metric tuple");
    }
}

pub async fn direct_chat_run_snapshot_port_contract_tests<T: DirectChatRunSnapshotPort + ?Sized>(
    port: &T,
) {
    let counts = port
        .direct_chat_run_counts()
        .await
        .expect("direct_chat_run_counts");
    let mut seen = HashSet::new();
    for count in counts {
        assert!(count.count > 0, "snapshot counts should omit zero rows");
        assert!(
            seen.insert((count.state, count.client_kind)),
            "duplicate direct chat run metric tuple"
        );
    }
}

pub async fn ws_lifecycle_instrumentation_hook_contract_tests<
    T: WsLifecycleInstrumentationHook + ?Sized,
>(
    hook: &T,
) {
    hook.accepted(WsPeer::Bot, "/ws/bot").await;
    hook.registered(WsPeer::Bot, "/ws/bot").await;
    hook.error(WsPeer::Frontend, "/ws", WsErrorKind::DispatchError).await;
    hook.closed(
        WsPeer::Frontend,
        "/ws",
        WsCloseReason::IdleTimeout,
        Duration::from_secs(1),
    )
    .await;
}

pub async fn direct_chat_run_lifecycle_hook_contract_tests<
    T: DirectChatRunLifecycleHook + ?Sized,
>(
    hook: &T,
) {
    hook.event(
        DirectChatRunEvent::Created,
        MetricsResult::Success,
        DirectChatClientKind::HttpChat,
        DirectChatRunReason::None,
    )
    .await;
    hook.event(
        DirectChatRunEvent::Failed,
        MetricsResult::Error,
        DirectChatClientKind::Unknown,
        DirectChatRunReason::InternalError,
    )
    .await;
}

pub async fn delivery_policy_block_instrumentation_hook_contract_tests<
    T: DeliveryPolicyBlockInstrumentationHook + ?Sized,
>(
    hook: &T,
) {
    hook.blocked(DeliveryBlockContext {
        target: DeliveryMetricTarget::Bot,
        delivery_kind: DeliveryMetricKind::Send,
        surface: DeliveryBlockSurface::GroupMessage,
        reason: DeliveryBlockReason::PolicyBlocked,
    })
    .await;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        NoopBotMetricsSnapshotPort, NoopDeliveryPolicyBlockInstrumentationHook, NoopDirectChatRunLifecycleHook,
        NoopDirectChatRunSnapshotPort, NoopGroupMetricsSnapshotPort,
        NoopGroupSessionMetricsSnapshotPort, NoopWsLifecycleInstrumentationHook,
    };

    #[tokio::test]
    async fn noop_group_metrics_snapshot_port_contract() {
        group_metrics_snapshot_port_contract_tests(&NoopGroupMetricsSnapshotPort).await;
    }

    #[tokio::test]
    async fn noop_group_session_metrics_snapshot_port_contract() {
        group_session_metrics_snapshot_port_contract_tests(&NoopGroupSessionMetricsSnapshotPort)
            .await;
    }

    #[tokio::test]
    async fn noop_bot_metrics_snapshot_port_contract() {
        bot_metrics_snapshot_port_contract_tests(&NoopBotMetricsSnapshotPort).await;
    }

    #[tokio::test]
    async fn noop_direct_chat_run_snapshot_port_contract() {
        direct_chat_run_snapshot_port_contract_tests(&NoopDirectChatRunSnapshotPort).await;
    }

    #[tokio::test]
    async fn noop_ws_lifecycle_hook_contract() {
        ws_lifecycle_instrumentation_hook_contract_tests(&NoopWsLifecycleInstrumentationHook)
            .await;
    }

    #[tokio::test]
    async fn noop_direct_chat_run_lifecycle_hook_contract() {
        direct_chat_run_lifecycle_hook_contract_tests(&NoopDirectChatRunLifecycleHook).await;
    }

    #[tokio::test]
    async fn noop_delivery_policy_block_hook_contract() {
        delivery_policy_block_instrumentation_hook_contract_tests(
            &NoopDeliveryPolicyBlockInstrumentationHook,
        )
        .await;
    }
}
