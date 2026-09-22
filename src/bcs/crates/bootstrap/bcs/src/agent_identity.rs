//! Optional deployment-owned Agent identity verifier selection. Public builds fail
//! closed without bringing proprietary identity SDKs into the public workspace.

use std::sync::Arc;
use async_trait::async_trait;
use bcs_service_api::port::agent_identity::{AgentIdentityError, AgentIdentityPort, VerifiedAgentIdentity};

pub struct AgentIdentityFactoryRegistration {
    pub name: &'static str,
    pub build: fn() -> Arc<dyn AgentIdentityPort>,
}

inventory::collect!(AgentIdentityFactoryRegistration);

pub(crate) fn build_agent_identity_port() -> crate::Result<Arc<dyn AgentIdentityPort>> {
    let mut registrations = inventory::iter::<AgentIdentityFactoryRegistration>.into_iter();
    let Some(registration) = registrations.next() else {
        return Ok(Arc::new(UnavailableAgentIdentity));
    };
    if registrations.next().is_some() {
        return Err(crate::BcsError::InvalidConfig("multiple Agent identity verifiers registered".into()));
    }
    tracing::info!(verifier = registration.name, "registered Agent identity verifier");
    Ok((registration.build)())
}

struct UnavailableAgentIdentity;

#[async_trait]
impl AgentIdentityPort for UnavailableAgentIdentity {
    async fn verify(&self, _: &str) -> Result<VerifiedAgentIdentity, AgentIdentityError> {
        Err(AgentIdentityError::Unavailable)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn public_build_without_verifier_fails_closed() {
        let port = build_agent_identity_port().expect("public verifier selection");
        bcs_test_support::contract::bot_self::agent_identity_port_contract_tests(
            port.as_ref(), "untrusted.jwt.payload", Err(AgentIdentityError::Unavailable),
        ).await;
    }
}
