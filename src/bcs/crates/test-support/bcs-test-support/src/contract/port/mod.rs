//! Port contract harnesses.

pub mod metrics;
pub mod bot_terminal_observer;

use bcs_service_api::{
    BotDeliveryPort, ChatRunCleanupPort, ChatRunEventPort, FrontendDeliveryPort,
    GroupHistoryBotRequestPort, LeaderElectionPort, LeaderStatus,
};

pub use metrics::{
    bot_metrics_snapshot_port_contract_tests,
    delivery_policy_block_instrumentation_hook_contract_tests,
    direct_chat_run_lifecycle_hook_contract_tests, direct_chat_run_snapshot_port_contract_tests,
    group_metrics_snapshot_port_contract_tests, group_session_metrics_snapshot_port_contract_tests,
    ws_lifecycle_instrumentation_hook_contract_tests,
};
pub use bot_terminal_observer::bot_terminal_observer_port_contract_tests;

pub async fn bot_delivery_port_contract_tests<T: BotDeliveryPort + ?Sized>(_port: &T) {}

pub async fn chat_run_cleanup_port_contract_tests<T: ChatRunCleanupPort + ?Sized>(_port: &T) {}

pub async fn chat_run_event_port_contract_tests<T: ChatRunEventPort + ?Sized>(_port: &T) {}

pub async fn frontend_delivery_port_contract_tests<T: FrontendDeliveryPort + ?Sized>(_port: &T) {}

pub async fn group_history_bot_request_port_contract_tests<
    T: GroupHistoryBotRequestPort + ?Sized,
>(
    _port: &T,
) {
}

pub async fn leader_election_port_contract_tests<T: LeaderElectionPort + ?Sized>(port: &T) {
    let status = port.campaign().await.expect("campaign");
    let is_leader = port.is_leader().await.expect("is_leader");
    match status {
        LeaderStatus::Leader => assert!(is_leader, "leader status must report is_leader"),
        LeaderStatus::Follower | LeaderStatus::Unknown => {
            assert!(!is_leader, "non-leader status must not report is_leader")
        }
    }

    let current = port.current_leader().await.expect("current_leader");
    if is_leader {
        assert!(current.is_some(), "leader implementations must expose leader info");
    }
}
