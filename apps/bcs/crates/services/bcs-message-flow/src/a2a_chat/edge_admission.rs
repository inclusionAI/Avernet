//! Existing directed-edge admission for direct Agent calls; no new call API.
use super::*;

impl A2aChat {
    /// Attach the same admission service/environment used by the host's existing
    /// admission API. Minimal setups without an edge store retain legacy paths.
    pub fn with_edge_admission(
        mut self,
        admission: Arc<dyn bcs_service_api::application::AdmissionService>,
        env: String,
    ) -> Self {
        self.edge_admission = Some((admission, env));
        self
    }

    pub(super) async fn has_directed_edge_access(&self, from: &str, to: &str) -> ServiceResult<bool> {
        let Some((admission, env)) = &self.edge_admission else { return Ok(false); };
        Ok(admission.check_admission(from, to, from, env).await?.allowed)
    }
}
