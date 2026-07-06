//! Application service implementing [`SecretService`] over a
//! [`SecretAccessPort`]. Today it's a thin pass-through with logging; future
//! audit / rate-limit / redaction policy belongs here.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::{SecretService, SecretServiceError, SecretView};
use bcs_service_api::port::secret::{SecretAccessPort, SecretRecord};
use tracing::warn;

#[derive(Clone)]
pub struct DefaultSecretService {
    access: Arc<dyn SecretAccessPort>,
}

impl DefaultSecretService {
    pub fn new(access: Arc<dyn SecretAccessPort>) -> Self {
        Self { access }
    }
}

#[async_trait]
impl SecretService for DefaultSecretService {
    async fn get_secret(&self, name: &str) -> Result<SecretView, SecretServiceError> {
        if name.is_empty() {
            return Err(SecretServiceError::InvalidInput("secret name is empty".into()));
        }
        let record = self.access.get_secret(name).await.map_err(|err| {
            warn!(secret = %name, error = %err, "SecretAccessPort::get_secret failed");
            SecretServiceError::from(err)
        })?;
        Ok(into_view(record))
    }
}

fn into_view(record: SecretRecord) -> SecretView {
    SecretView {
        name: record.name,
        user: record.user,
        value: record.value,
    }
}
