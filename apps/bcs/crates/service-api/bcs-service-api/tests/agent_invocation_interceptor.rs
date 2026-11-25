//! Central operation-hook semantics, independent of credentials or transport.
use std::sync::{Arc, atomic::{AtomicUsize, Ordering::SeqCst}};
use async_trait::async_trait;
use bcs_service_api::application::agent_invoke::*;
use bcs_service_api::interceptor::*;
use bcs_domain::edge_permission::{GrantKind, GrantSource};

struct Legacy;
#[async_trait]
impl MessageInterceptor for Legacy {
    async fn on_outbound(&self, _: &mut OutboundMessage) -> InterceptorDecision { InterceptorDecision::Pass }
}
struct Provider { result: AgentInvokeResult<AgentInvocationPermit>, hits: Arc<AtomicUsize> }
#[async_trait]
impl MessageInterceptor for Provider {
    async fn on_outbound(&self, _: &mut OutboundMessage) -> InterceptorDecision { InterceptorDecision::Pass }
    async fn on_agent_invocation(&self, input: &AgentInvocationInput) -> Option<AgentInvokeResult<AgentInvocationPermit>> {
        assert_eq!(input, &invocation());
        self.hits.fetch_add(1, SeqCst);
        Some(self.result.clone())
    }
}
fn invocation() -> AgentInvocationInput {
    AgentInvocationInput { principal: AgentInvokePrincipal::Agent("a".into()), authenticated_scope: AgentInvokeScope {
        namespace: "isolated".into(), env: "test".into() }, pair: AgentInvokePair { from_bot_id: "a".into(), to_bot_id: "b".into() },
        originator: "a".into(), operation: AgentInvokeOperation::DirectInvocation }
}
fn permit() -> AgentInvocationPermit {
    AgentInvocationPermit { kind: GrantKind::AgentInvoke, ref_id: 9, revision: Some(1), digest: None, source: GrantSource::EdgeGrant }
}
fn provider(result: AgentInvokeResult<AgentInvocationPermit>, hits: &Arc<AtomicUsize>) -> Provider {
    Provider { result, hits: hits.clone() }
}
#[tokio::test]
async fn empty_or_legacy_only_chains_never_authorize_explicit_agent_invocation() {
    let mut chain = InterceptorChain::new();
    assert_eq!(chain.authorize_agent_invocation(&invocation()).await, Err(AgentInvokeError::Unavailable));
    assert_eq!(Legacy.on_agent_invocation(&invocation()).await, None);
    chain.push(Legacy);
    assert_eq!(chain.authorize_agent_invocation(&invocation()).await, Err(AgentInvokeError::Unavailable));
    bcs_test_support::contract::interceptor::message_interceptor_contract_tests(&Legacy).await;
}
#[tokio::test]
async fn registered_providers_all_agree_and_recheck_each_operation() {
    let hits = Arc::new(AtomicUsize::new(0));
    let mut chain = InterceptorChain::new();
    chain.push(Legacy);
    chain.push(provider(Ok(permit()), &hits));
    chain.push(provider(Ok(permit()), &hits));
    for _ in 0..2 { assert_eq!(chain.authorize_agent_invocation(&invocation()).await, Ok(permit())); }
    assert_eq!(hits.load(SeqCst), 4, "no positive cache");
    bcs_test_support::contract::interceptor::agent_invocation_interceptor_contract_tests(
        &provider(Ok(permit()), &hits), &invocation(), Ok(permit())).await;
}
#[tokio::test]
async fn failures_and_conflicting_provider_decisions_stop_without_fallback() {
    for error in [AgentInvokeError::Forbidden, AgentInvokeError::Unavailable, AgentInvokeError::InvalidState,
        AgentInvokeError::Unauthenticated, AgentInvokeError::Conflict, AgentInvokeError::CommitUnknown] {
        let hits = Arc::new(AtomicUsize::new(0));
        let mut chain = InterceptorChain::new();
        chain.push(provider(Ok(permit()), &hits));
        chain.push(provider(Err(error), &hits));
        chain.push(provider(Ok(permit()), &hits));
        assert_eq!(chain.authorize_agent_invocation(&invocation()).await, Err(error));
        assert_eq!(hits.load(SeqCst), 2);
    }
    let hits = Arc::new(AtomicUsize::new(0));
    for differing_id in [true, false] {
        let mut chain = InterceptorChain::new();
        chain.push(provider(Ok(permit()), &hits));
        let mut second = permit();
        if differing_id { second.ref_id += 1; } else { second.revision = Some(2); }
        chain.push(provider(Ok(second), &hits));
        assert_eq!(chain.authorize_agent_invocation(&invocation()).await, Err(AgentInvokeError::Conflict));
    }
}
#[tokio::test]
async fn malformed_or_non_agent_permits_cannot_pass_a_mandatory_hook() {
    for scenario in ["kind", "id", "no-version", "zero-version", "source", "digest"] {
        let hits = Arc::new(AtomicUsize::new(0));
        let mut bad = permit();
        match scenario {
            "kind" => bad.kind = GrantKind::Rules,
            "id" => bad.ref_id = 0,
            "no-version" => bad.revision = None,
            "zero-version" => bad.revision = Some(0),
            "source" => bad.source = GrantSource::PublicDefault,
            "digest" => bad.digest = Some("untrusted".into()),
            _ => unreachable!(),
        }
        let mut chain = InterceptorChain::new();
        chain.push(provider(Ok(bad), &hits));
        assert_eq!(chain.authorize_agent_invocation(&invocation()).await, Err(AgentInvokeError::InvalidState), "{scenario}");
    }
}

#[tokio::test]
async fn old_chat_implementations_cannot_accidentally_claim_new_invocation_capability() {
    use bcs_service_api::{A2aChatCommand, AsyncA2aChatCommand, A2aChatService, A2aChatRunService,
        CallerContext, BotActor, ChatResponseMode, ServiceError};
    let caller = CallerContext::Bot(BotActor { bot_uuid: "a".into() });
    let command = A2aChatCommand { caller: caller.clone(), target_bot_id: "b".into(), message: "text".into(),
        from_actor_id: None, authenticated_staff_id: None, run_id: None, async_mode: true,
        session_key: None, timeout_ms: None, client: None, tags: vec![], response_mode: ChatResponseMode::Full,
        caller_wait_mode: None, organization_code: None, provider_bypass_headers: vec![], a2a_test_user: None };
    assert!(matches!(bcs_test_support::NoopA2aChatService.chat_agent_invocation(command, invocation()).await,
        Err(ServiceError::Forbidden(_))));
    let command = AsyncA2aChatCommand { caller, target_bot_id: "b".into(), message: "text".into(),
        from_actor_id: None, run_channel_from: None, authenticated_staff_id: None, run_id: "run".into(),
        session_key: "session".into(), timeout_ms: 60_000, client: None, tags: vec![], response_mode: ChatResponseMode::Full,
        caller_wait_mode: None, organization_code: None, provider_bypass_headers: vec![], a2a_test_user: None };
    assert!(matches!(bcs_test_support::NoopA2aChatRunService.start_async_agent_invocation(command, invocation()).await,
        Err(ServiceError::Forbidden(_))));
}
