//! The independent read-only Card boundary of published FSM groups (plan T8).
//!
//! [`GroupDiscovery`] answers an authenticated caller with what one group's
//! frozen publication declares and what this boundary implements. It reads
//! no task, admission or session state and writes nothing ever; a group the
//! current grants do not publish is not disclosed, and no capability is
//! declared that the surface does not implement.

use crate::scope_of;
use bcs_dist_model::GroupId;
use bcs_service_api::application::dist_gateway::*;
use bcs_service_api::port::dist_agent::{DistGroupDirectoryPort, GroupDirectoryError};
use std::sync::Arc;

/// What the group surface declares: only implemented capabilities. The card
/// of a published group never advertises push delivery, and extension URIs
/// appear only when the host explicitly enabled them.
#[derive(Debug, Clone)]
pub struct GroupDiscoveryConfig {
    /// Enabled by-request extension URIs; none unless explicitly enabled.
    pub extensions: Vec<String>,
}

impl Default for GroupDiscoveryConfig {
    fn default() -> Self {
        Self {
            extensions: Vec::new(),
        }
    }
}

pub struct GroupDiscovery {
    groups: Arc<dyn DistGroupDirectoryPort>,
    policy: Arc<dyn GroupGrantPolicy>,
    config: GroupDiscoveryConfig,
}

impl GroupDiscovery {
    pub fn new(
        groups: Arc<dyn DistGroupDirectoryPort>,
        policy: Arc<dyn GroupGrantPolicy>,
        config: GroupDiscoveryConfig,
    ) -> Self {
        Self {
            groups,
            policy,
            config,
        }
    }
}

#[async_trait::async_trait]
impl GroupDiscoveryService for GroupDiscovery {
    async fn card(&self, caller: &GatewayCaller, group: &GroupId) -> Result<GroupCard, GatewayError> {
        // Current authorization first: an unpublished or denied group is not
        // disclosed at all, to any entrance, with no anonymous fallback.
        let grants = self.policy.grants(caller, group).await;
        if !grants.published {
            return Err(GatewayError::NotFound);
        }
        // The frozen revision check: the directory answers the group's
        // published definition or the group is not resolvable here.
        let _frozen = self.groups.resolve(&scope_of(caller)?, group).await.map_err(|e| match e {
            GroupDirectoryError::NotFound => GatewayError::NotFound,
            GroupDirectoryError::Unavailable => GatewayError::Unavailable {
                code: "group_directory_unavailable".to_owned(),
            },
            GroupDirectoryError::InvalidRevision => GatewayError::Unavailable {
                code: "group_revision_invalid".to_owned(),
            },
        })?;
        Ok(GroupCard {
            group: group.clone(),
            // Implemented and served by this boundary.
            streaming: true,
            // Not implemented by the group surface: never declared.
            push_notifications: false,
            extensions: self.config.extensions.clone(),
            input_modes: vec!["text/plain".to_owned()],
            output_modes: vec!["text/plain".to_owned()],
        })
    }
}
