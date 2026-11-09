//! Deployment supplies policy-checked frozen Card/endpoint and credential-bound HTTP client.
//! No discovery, credential lookup or automatic transport fallback occurs here.
use std::sync::Arc;
use bcn_runtime_model::*;
use bcn_runtime_api::port::AgentConnector;
use bcn_agent_connectors::{a2a::A2aConnector,bcn_sse::{ProviderConfig,ProviderConnector}};
use bcs_a2a_http::card::SelectedCard;
use bcs_network_tracing::ClientWithDefaults;
pub enum ConnectorSelection {
    A2a { card:SelectedCard, http:ClientWithDefaults<reqwest::Client> },
    Provider { config:ProviderConfig, http:ClientWithDefaults<reqwest::Client> },
    Local(Arc<dyn AgentConnector>),
}
pub fn connector(binding:FrozenTargetBinding,selection:ConnectorSelection)->ConnectorResult<(FrozenTargetBinding,Arc<dyn AgentConnector>)>{
    let client:Arc<dyn AgentConnector>=match selection {
        ConnectorSelection::A2a{card,http}=>Arc::new(A2aConnector::new(binding.clone(),card,http)?),
        ConnectorSelection::Provider{config,http}=>Arc::new(ProviderConnector::new(binding.clone(),config,http)?),
        ConnectorSelection::Local(client)=>client,
    };
    if client.capabilities()!=binding.capabilities{return Err(ConnectorError::NotSent("binding capabilities disagree".into()))}
    Ok((binding,client))
}
