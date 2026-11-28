#![cfg(feature = "dist-direct-a2a")]
#![allow(clippy::unwrap_used, reason = "test fixtures and assertions")]
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs::dist_direct_a2a::DirectSessionDirectory;
use bcs_service_api::port::dist_agent::{
    DirectoryError, DistSessionAuthorizerPort, DistSessionDirectoryPort,
};
use bcs_test_support::contract::distributed::services::{
    dist_session_authorizer_port_contract_tests, dist_session_directory_port_contract_tests,
};
use std::sync::{Arc, Mutex};

struct RecordingAuthorizer {
    decision: Result<(), DirectoryError>,
    calls: Mutex<Vec<(TaskScope, SessionTarget)>>,
}

#[async_trait]
impl DistSessionAuthorizerPort for RecordingAuthorizer {
    async fn authorize(&self, scope: &TaskScope, target: &SessionTarget) -> Result<(), DirectoryError> {
        self.calls.lock().unwrap().push((scope.clone(), target.clone()));
        self.decision.clone()
    }
}

fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("tenant".into()),
        principal: PrincipalId("opaque-caller".into()),
    }
}

fn target() -> SessionTarget {
    SessionTarget::DirectAgent { agent: AgentId("target".into()) }
}

fn limits() -> SessionLimits {
    SessionLimits { max_queued_tasks: 8, max_task_steps: 20, task_timeout_millis: 1_000, max_inline_bytes: 1024 }
}

fn fixture(decision: Result<(), DirectoryError>) -> (DirectSessionDirectory, Arc<RecordingAuthorizer>) {
    let authorizer = Arc::new(RecordingAuthorizer { decision, calls: Mutex::new(vec![]) });
    let directory = DirectSessionDirectory::new(authorizer.clone(), scope().tenant, PoolId("pool".into()), limits());
    (directory, authorizer)
}

#[tokio::test]
async fn conformance_direct_session_directory_freezes_configuration_without_calling_authorization() {
    let (directory, authorizer) = fixture(Err(DirectoryError::Unavailable));
    dist_session_directory_port_contract_tests(&directory, &scope(), &target()).await.unwrap();
    let frozen = directory.resolve(&scope(), &target()).await.unwrap();
    let config = serde_json::json!({"agent": "target"});
    assert_eq!(frozen.target, target());
    assert_eq!(frozen.mode_envelope.mode, CollaborationMode::DirectA2a);
    assert_eq!(frozen.mode_envelope.mode_version, 1);
    assert_eq!(frozen.mode_envelope.revision, "1");
    assert_eq!(frozen.mode_envelope.digest, bcs_dist_encoding::digest_json(&config));
    assert_eq!(frozen.mode_envelope.payload, Payload::Inline(config));
    assert_eq!(frozen.authorized_agents, vec![AuthorizedAgent { agent: AgentId("target".into()), pool: PoolId("pool".into()) }]);
    assert_eq!(frozen.limits, limits());
    assert!(authorizer.calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn conformance_direct_session_directory_forwards_identity_target_and_policy_result_unchanged() {
    for decision in [Ok(()), Err(DirectoryError::AccessDenied), Err(DirectoryError::Unavailable), Err(DirectoryError::NotFound)] {
        let (directory, authorizer) = fixture(decision.clone());
        dist_session_authorizer_port_contract_tests(authorizer.as_ref(), &scope(), &[(target(), decision.clone())]).await.unwrap();
        authorizer.calls.lock().unwrap().clear();
        // No parsing of human/bot prefixes or substitution of caller identity.
        for principal in ["opaque-caller", "human:user-id", "bot:bot-id", "integration:client-id"] {
            let scope = TaskScope { principal: PrincipalId(principal.into()), ..scope() };
            assert_eq!(directory.authorize(&scope, &target()).await, decision);
            assert_eq!(authorizer.calls.lock().unwrap().pop(), Some((scope, target())));
            assert!(authorizer.calls.lock().unwrap().is_empty());
        }
    }
}

#[tokio::test]
async fn direct_session_directory_rejects_other_tenants_and_invalid_targets_before_authorization() {
    let (directory, authorizer) = fixture(Ok(()));
    let cases = [
        (TaskScope { tenant: TenantId("other".into()), ..scope() }, target()),
        (scope(), SessionTarget::Group { group: GroupId("group".into()) }),
        (scope(), SessionTarget::DirectAgent { agent: AgentId(String::new()) }),
    ];
    for (scope, target) in cases {
        assert_eq!(directory.authorize(&scope, &target).await, Err(DirectoryError::NotFound));
        assert_eq!(directory.resolve(&scope, &target).await, Err(DirectoryError::NotFound));
    }
    assert!(authorizer.calls.lock().unwrap().is_empty());
}
