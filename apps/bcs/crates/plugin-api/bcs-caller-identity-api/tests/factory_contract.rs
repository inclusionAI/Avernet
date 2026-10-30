use std::sync::Arc;
use bcs_caller_identity_api::*;
use bcs_config_api::CallerIdentityServiceConfig;

struct RecordingPlugin;
#[async_trait::async_trait]
impl CallerIdentityPlugin for RecordingPlugin {
    async fn establish_subject(&self, _: EstablishCallerSubject) -> Result<EstablishedCallerSubject, CallerIdentityError> {
        Err(CallerIdentityError::UpstreamEvidenceRequired)
    }
    async fn issue_execution(&self, _: IssueCallerExecution<'_>) -> Result<CallerExecutionToken, CallerIdentityError> {
        Err(CallerIdentityError::IdentityExchangeRejected)
    }
    async fn verify_execution(&self, _: VerifyCallerExecution) -> Result<VerifiedCallerExecution, CallerIdentityError> {
        Err(CallerIdentityError::TokenInvalid)
    }
    fn proof_type(&self) -> &'static str { "external-test" }
}

fn build(config: &CallerIdentityServiceConfig) -> Result<Arc<dyn CallerIdentityPlugin>, CallerIdentityError> {
    assert!(config.jwt.is_none(), "external implementations do not need JWT keys");
    if !config.options.is_empty() { return Err(CallerIdentityError::InvalidRequest); }
    Ok(Arc::new(RecordingPlugin))
}
inventory::submit! { CallerIdentityPluginFactory { name: "external-test", build } }
inventory::submit! { CallerIdentityPluginFactory { name: "duplicate", build } }
inventory::submit! { CallerIdentityPluginFactory { name: "duplicate", build } }

fn config(name: &str) -> CallerIdentityServiceConfig {
    CallerIdentityServiceConfig { plugin: name.into(), jwt: None, relay: None, options: Default::default() }
}

#[test]
fn external_factory_is_selected_without_public_implementation_dependencies() {
    let plugin = build_caller_identity_plugin(&config("external-test")).unwrap();
    assert_eq!(plugin.proof_type(), "external-test");
}

#[test]
fn unavailable_duplicate_and_failed_factories_never_fall_back() {
    assert_eq!(build_caller_identity_plugin(&config("not-linked")).err(), Some(CallerIdentityError::IdentityProviderUnavailable));
    assert_eq!(build_caller_identity_plugin(&config("duplicate")).err(), Some(CallerIdentityError::InvalidRequest));
    let mut invalid = config("external-test");
    invalid.options.insert("unknown".into(), serde_json::Value::Bool(true));
    assert_eq!(build_caller_identity_plugin(&invalid).err(), Some(CallerIdentityError::InvalidRequest));
}
