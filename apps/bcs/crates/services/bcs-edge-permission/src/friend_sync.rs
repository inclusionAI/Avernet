//! `EdgePermissionFriendSyncService` — the legacy friend-graph → edge-permission
//! dual-write bridge, split out of the former over-limit `lib.rs`
//! (plan Task 12 fix round, behavior-preserving move).
//!
//! This is an INDEPENDENT system lane of the store: the audit identity of its
//! edge writes is an honest `system_lane_operation` (verified callers do not
//! exist on this bridge), and the runtime whitelist keeps role rows out of
//! every write the bridge performs.

use std::sync::Arc;

use async_trait::async_trait;
use tracing::warn;

use bcs_domain::actor::ActorKind;
use bcs_domain::edge_permission::GrantKind;
use bcs_service_api::port::repo::EdgeGrantRepoPort;
use bcs_service_api::{
    EdgePermissionFriendSyncService, ServiceError, ServiceResult,
};

use bcs_service_api::application::connect::ConnectService as _;

use crate::{actor_kind_of, DbConnectService};

#[async_trait]
impl EdgePermissionFriendSyncService for DbConnectService {
    async fn sync_add_friendship(&self, a: &str, b: &str) -> ServiceResult<()> {
        if a == b {
            warn!(actor = %a, env = %self.env, "skip edge-permission friendship sync for self friendship");
            return Ok(());
        }

        let a_kind = actor_kind_of(a);
        let b_kind = actor_kind_of(b);
        let (caller, target, caller_kind, target_kind) = match (a_kind, b_kind) {
            (ActorKind::Human, ActorKind::Bot) => (a, b, a_kind, b_kind),
            (ActorKind::Bot, ActorKind::Human) => (b, a, b_kind, a_kind),
            (ActorKind::Bot, ActorKind::Bot) => (a, b, a_kind, b_kind),
            (ActorKind::Human, ActorKind::Human) => {
                warn!(left = %a, right = %b, env = %self.env, "skip edge-permission friendship sync for unsupported human-human friendship");
                return Ok(());
            }
        };

        if caller_kind == ActorKind::Human
            && self.edge_grants.has_friend_edge(caller, target, &self.env).await
        {
            return Ok(());
        }

        // The edge-permission friendship sync is an independent system lane:
        // an HONEST system operation context (spec §12.5), never a forged
        // Human. Role rows never flow through here (the ports only write
        // non-role edges) and role changes never call this service.
        let operation = bcs_service_api::types::system_lane_operation(
            "edge-permission-friendship-sync",
        );
        self.build_connect_edges_with_profile_retry(caller, target, caller_kind, target_kind, &operation)
            .await?;
        Ok(())
    }

    async fn sync_remove_friendship(&self, a: &str, b: &str) -> ServiceResult<()> {
        if a == b {
            return Ok(());
        }

        let a_kind = actor_kind_of(a);
        let b_kind = actor_kind_of(b);
        let (caller, target) = match (a_kind, b_kind) {
            (ActorKind::Human, ActorKind::Bot) => (a, b),
            (ActorKind::Bot, ActorKind::Human) => (b, a),
            (ActorKind::Bot, ActorKind::Bot) => (a, b),
            (ActorKind::Human, ActorKind::Human) => {
                warn!(left = %a, right = %b, env = %self.env, "skip edge-permission friendship removal sync for unsupported human-human friendship");
                return Ok(());
            }
        };

        // The edge-permission sync path has no inbound HTTP principal; pass
        // None request auth (the revoke trigger degrades to a best-effort,
        // auth-less call). The audit identity is an honest system lane.
        let operation = bcs_service_api::types::system_lane_operation(
            "edge-permission-friendship-sync",
        );
        self.revoke_friend(caller, target, None, operation).await.map(|_| ())
    }
}

#[cfg(test)]
#[path = "connect_tests.rs"]
mod tests;
