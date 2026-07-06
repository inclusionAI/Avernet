use std::sync::Arc;

use async_trait::async_trait;
use bcs_fuse_client::FuseClient;
use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};

/// Lifecycle adapter kept in the service layer so the HTTP client crate
/// remains independent from service-api contracts.
#[derive(Clone, Debug)]
pub struct FuseClientLifecycle {
    client: Arc<FuseClient>,
}

impl FuseClientLifecycle {
    pub fn new(client: Arc<FuseClient>) -> Self {
        Self { client }
    }

    pub fn client(&self) -> Arc<FuseClient> {
        Arc::clone(&self.client)
    }
}

#[async_trait]
impl ServiceLifecycle for FuseClientLifecycle {
    async fn initialize(&self) -> Result<(), LifecycleError> {
        Ok(())
    }

    async fn shutdown(&self) -> Result<(), LifecycleError> {
        Ok(())
    }
}
