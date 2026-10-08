//! Bootstrap-level lifecycle orchestration.

use std::sync::Arc;

use bcs_service_api::lifecycle::{LifecycleError, ServiceLifecycle};
use tracing::{error, info, warn};

pub struct LifecycleOrchestrator {
    services: Vec<(&'static str, Arc<dyn ServiceLifecycle>)>,
    initialized_up_to: Option<usize>,
}

impl LifecycleOrchestrator {
    pub fn new() -> Self {
        Self {
            services: Vec::new(),
            initialized_up_to: None,
        }
    }

    pub fn register(&mut self, name: &'static str, service: Arc<dyn ServiceLifecycle>) {
        self.services.push((name, service));
    }

    pub async fn initialize_all(&mut self) -> Result<(), LifecycleError> {
        for (idx, (name, service)) in self.services.iter().enumerate() {
            info!(service = %name, "initializing service");
            if let Err(error) = service.initialize().await {
                error!(
                    service = %name,
                    error = %error,
                    "service initialize failed; rolling back"
                );
                self.rollback_initialized(idx).await;
                return Err(error);
            }
            self.initialized_up_to = Some(idx);
        }
        Ok(())
    }

    pub async fn shutdown_all(&self) -> Result<(), LifecycleError> {
        let upper_bound = self
            .initialized_up_to
            .map(|idx| idx + 1)
            .unwrap_or(self.services.len());
        let mut last_error = None;
        for (name, service) in self.services[..upper_bound].iter().rev() {
            info!(service = %name, "shutting down service");
            if let Err(error) = service.shutdown().await {
                error!(service = %name, error = %error, "service shutdown failed");
                last_error = Some(error);
            }
        }
        if let Some(error) = last_error {
            Err(error)
        } else {
            Ok(())
        }
    }

    async fn rollback_initialized(&self, failed_idx: usize) {
        for (name, service) in self.services[..failed_idx].iter().rev() {
            if let Err(error) = service.shutdown().await {
                warn!(
                    service = %name,
                    error = %error,
                    "service shutdown during rollback failed"
                );
            }
        }
    }
}

impl Default for LifecycleOrchestrator {
    fn default() -> Self {
        Self::new()
    }
}
