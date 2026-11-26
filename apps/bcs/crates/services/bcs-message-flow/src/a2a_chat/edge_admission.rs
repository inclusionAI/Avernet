//! Existing directed-edge reads for direct Agent calls; no new call API.
use super::*;

impl A2aChat {
    /// Attach the same edge store/environment used by the host's admission API.
    /// Only AgentInvoke extends legacy direct reachability; existing profile,
    /// rules, friendship and authority behavior is not redefined here.
    pub fn with_edge_grants(
        mut self,
        grants: Arc<dyn bcs_service_api::port::repo::EdgeGrantRepoPort>,
        env: String,
    ) -> Self {
        self.edge_grants = Some((grants, env));
        self
    }

    pub(super) async fn has_directed_edge_access(&self, from: &str, to: &str) -> ServiceResult<bool> {
        let Some((grants, env)) = &self.edge_grants else { return Ok(false); };
        Ok(grants.list_runtime_grants_checked(from, to, env).await?.iter()
            .any(|grant| grant.grant_kind == bcs_domain::edge_permission::GrantKind::AgentInvoke))
    }
}
