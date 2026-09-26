//! Leader election implementations for the public BCS workspace.

use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_service_api::lifecycle::ServiceLifecycle;
use bcs_service_api::{LeaderElectionPort, LeaderInfo, LeaderStatus, ServiceResult};
use tracing::{info, warn};

static LOCAL_IP: OnceLock<String> = OnceLock::new();

/// Get the local IP address by enumerating network interfaces.
pub fn get_local_ip() -> &'static str {
    LOCAL_IP.get_or_init(|| {
        if let Ok(interfaces) = if_addrs::get_if_addrs() {
            for iface in interfaces {
                if iface.is_loopback() {
                    continue;
                }
                if let std::net::IpAddr::V4(ipv4) = iface.addr.ip() {
                    let ip = ipv4.to_string();
                    info!(ip = %ip, interface = %iface.name, "Resolved local IP address");
                    return ip;
                }
            }
        }
        warn!("No non-loopback IP found, using 127.0.0.1");
        "127.0.0.1".to_string()
    })
}

#[derive(Debug, Clone)]
pub struct StandaloneLeaderElection {
    node_id: String,
    elected_at_ms: u64,
}

impl StandaloneLeaderElection {
    pub fn new(node_id: impl Into<String>) -> Self {
        Self {
            node_id: node_id.into(),
            elected_at_ms: current_timestamp_ms(),
        }
    }

    pub fn local() -> Self {
        Self::new("standalone")
    }

    fn leader_info(&self) -> LeaderInfo {
        LeaderInfo {
            node_id: self.node_id.clone(),
            elected_at_ms: self.elected_at_ms,
        }
    }
}

#[async_trait]
impl LeaderElectionPort for StandaloneLeaderElection {
    async fn campaign(&self) -> ServiceResult<LeaderStatus> {
        Ok(LeaderStatus::Leader)
    }

    async fn is_leader(&self) -> ServiceResult<bool> {
        Ok(true)
    }

    async fn current_leader(&self) -> ServiceResult<Option<LeaderInfo>> {
        Ok(Some(self.leader_info()))
    }
}

#[async_trait]
impl ServiceLifecycle for StandaloneLeaderElection {}

fn current_timestamp_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn standalone_is_always_leader() {
        let election = StandaloneLeaderElection::new("node-a");
        assert_eq!(election.campaign().await.unwrap(), LeaderStatus::Leader);
        assert!(election.is_leader().await.unwrap());
        assert_eq!(
            election.current_leader().await.unwrap().unwrap().node_id,
            "node-a"
        );
    }

}
