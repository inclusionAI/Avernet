use std::sync::Arc;

use bcs_service_api::application::v1::GroupService;

use super::PrincipalVerifier;

#[derive(Clone)]
pub struct ApiState {
    pub group_service: Arc<dyn GroupService>,
    pub principal_verifier: Arc<dyn PrincipalVerifier>,
}

impl ApiState {
    pub fn new(
        group_service: Arc<dyn GroupService>,
        principal_verifier: Arc<dyn PrincipalVerifier>,
    ) -> Self {
        Self {
            group_service,
            principal_verifier,
        }
    }
}
