//! The metrics wrapper must forward the explicit capability, never downgrade to
//! ordinary chat or inherit the backwards-compatible default refusal.
use super::*;
use bcs_service_api::application::agent_invoke::*;
use bcs_service_api::{BotActor, CallerContext, ChatResponseMode};
use std::sync::atomic::{AtomicUsize, Ordering::SeqCst};

struct Recording {
    fail: bool,
    calls: AtomicUsize,
}
#[async_trait::async_trait]
impl A2aChatRunService for Recording {
    async fn start_async_chat(&self, _: AsyncA2aChatCommand) -> ServiceResult<AsyncA2aChatAccepted> {
        panic!("explicit invocation must not downgrade to legacy chat")
    }
    async fn start_async_agent_invocation(
        &self, cmd: AsyncA2aChatCommand, input: AgentInvocationInput,
    ) -> ServiceResult<AsyncA2aChatAccepted> {
        self.calls.fetch_add(1, SeqCst);
        assert_eq!(cmd.caller, CallerContext::Bot(BotActor { bot_uuid: "source".into() }));
        assert_eq!(cmd.target_bot_id, "target");
        assert_eq!(cmd.message, "unchanged text");
        assert_eq!(cmd.from_actor_id.as_deref(), Some("source"));
        assert_eq!(input, invocation());
        if self.fail { return Err(ServiceError::Forbidden("current invocation denied".into())); }
        Ok(AsyncA2aChatAccepted { delivery: None, run_id: cmd.run_id, bot_uuid: cmd.target_bot_id,
            session_id: cmd.session_key, status: "running".into(), expires_at_ms: 100 })
    }
    async fn get_run(&self, _: ChatRunQueryCommand) -> ServiceResult<A2aRunStatus> { panic!("unused") }
    async fn cancel_run(&self, _: ChatRunCancelCommand) -> ServiceResult<A2aRunStatus> { panic!("unused") }
}
fn invocation() -> AgentInvocationInput {
    AgentInvocationInput { authentication: AgentInvocationAuthentication::TrustedInProcess,
            principal: AgentInvokePrincipal::Agent("source".into()), authenticated_scope: AgentInvokeScope {
        namespace: "isolated".into(), env: "test".into() }, pair: AgentInvokePair { from_bot_id: "source".into(), to_bot_id: "target".into() },
        originator: "source".into(), operation: AgentInvokeOperation::DirectInvocation }
}
#[tokio::test]
async fn agent_invocation_metrics_wrapper_preserves_input_success_and_refusal() {
    for fail in [false, true] {
        let inner = Arc::new(Recording { fail, calls: AtomicUsize::new(0) });
        let wrapper = InstrumentedA2aChatRunService::new(inner.clone(), "test".into());
        let cmd = AsyncA2aChatCommand { caller: CallerContext::Bot(BotActor { bot_uuid: "source".into() }), target_bot_id: "target".into(),
            message: "unchanged text".into(), from_actor_id: Some("source".into()), run_channel_from: None,
            authenticated_staff_id: None, run_id: "run".into(), session_key: "session".into(), timeout_ms: 60_000,
            client: None, tags: vec![], response_mode: ChatResponseMode::Full, caller_wait_mode: None,
            organization_code: None, provider_bypass_headers: vec![], a2a_test_user: None };
        let result = wrapper.start_async_agent_invocation(cmd, invocation()).await;
        if fail { assert!(matches!(result, Err(ServiceError::Forbidden(ref msg)) if msg == "current invocation denied")); }
        else { let result = result.unwrap(); assert_eq!(result.run_id, "run"); assert_eq!(result.status, "running"); }
        assert_eq!(inner.calls.load(SeqCst), 1);
    }
}
