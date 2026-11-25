use std::sync::Arc;
use async_trait::async_trait;
use bcs_message_flow::agent_invoke_ingress::AgentInvokeDirectApplicationService;
use bcs_service_api::application::agent_invoke::*;
use bcs_service_api::port::agent_invoke::AgentInvokeIdentityResolutionPort;
use bcs_service_api::{A2aChatRunService,AsyncA2aChatCommand,AsyncA2aChatAccepted,ServiceError};
use bcs_service_api::{A2aRunStatus,ChatRunQueryCommand,ChatRunCancelCommand};
use std::{num::NonZeroU64,sync::atomic::{AtomicU8,AtomicUsize,Ordering::SeqCst}};
struct Identity(AtomicUsize);
#[async_trait]
impl AgentInvokeIdentityResolutionPort for Identity {
    async fn resolve_invocation_source(&self,_:&AgentInvokeScope,_:&AgentInvokeVerifiedBot)->AgentInvokeResult<AgentInvokeAuthenticatedSource>{
        self.0.fetch_add(1,SeqCst);
        Ok(AgentInvokeAuthenticatedSource{registration:AgentInvokeRegistrationBinding{registration_id:NonZeroU64::MIN,identity_revision:NonZeroU64::MIN},binding_generation:NonZeroU64::MIN,eligibility_generation:NonZeroU64::MIN})
    }
}
struct Runs(AtomicU8);
#[async_trait]
impl A2aChatRunService for Runs {
    async fn start_async_chat(&self,_:AsyncA2aChatCommand)->bcs_service_api::ServiceResult<AsyncA2aChatAccepted>{panic!("never downgrade")}
    async fn start_async_agent_invocation(&self,cmd:AsyncA2aChatCommand,input:AgentInvocationInput)->bcs_service_api::ServiceResult<AsyncA2aChatAccepted>{
        assert!(matches!(input.authentication,AgentInvocationAuthentication::VerifiedSource(_)));
        assert_eq!(cmd.from_actor_id.as_deref(),Some("source"));assert_eq!(input.originator,"source");
        Err(match self.0.load(SeqCst){
            1=>ServiceError::Forbidden("sensitive refusal".into()),
            2=>ServiceError::Unauthorized("sensitive auth".into()),
            3=>ServiceError::BotNotFound("private missing".into()),
            4=>ServiceError::Conflict("private conflict".into()),
            5=>ServiceError::InvalidOperation{message:"private invalid".into(),request_id:None},
            6=>ServiceError::BotNotConnected("private offline".into()),
            _=>ServiceError::InternalError("private DB details".into()),
        })
    }
    async fn get_run(&self,_:ChatRunQueryCommand)->bcs_service_api::ServiceResult<A2aRunStatus>{panic!("unused")}
    async fn cancel_run(&self,_:ChatRunCancelCommand)->bcs_service_api::ServiceResult<A2aRunStatus>{panic!("unused")}
}
fn limits()->AgentInvokeDirectLimits{AgentInvokeDirectLimits{default_timeout_ms:NonZeroU64::new(10).unwrap(),max_timeout_ms:NonZeroU64::new(100).unwrap()}}
fn scope()->AgentInvokeScope{AgentInvokeScope{namespace:"isolated".into(),env:"test".into()}}
fn command()->AgentInvokeDirectCommand{AgentInvokeDirectCommand{identity:AgentInvokeVerifiedBot{bot_id:"source".into(),agent_code:"code".into()},authenticated_scope:scope(),to_bot_id:"target".into(),message:"text".into(),session_id:None,timeout_ms:None}}
#[tokio::test]
async fn runtime_errors_remain_closed_without_retry_or_downgrade(){
    let identity=Arc::new(Identity(AtomicUsize::new(0)));let runs=Arc::new(Runs(AtomicU8::new(0)));
    let service=AgentInvokeDirectApplicationService::new(scope(),identity.clone(),runs.clone(),limits()).unwrap();
    for (n,expected) in [(1,AgentInvokeError::Forbidden),(2,AgentInvokeError::Forbidden),(3,AgentInvokeError::NotFound),
        (4,AgentInvokeError::Conflict),(5,AgentInvokeError::InvalidInput),(6,AgentInvokeError::Unavailable),(7,AgentInvokeError::Unavailable)]{
        runs.0.store(n,SeqCst);assert_eq!(service.invoke(command()).await.unwrap_err(),expected);
    }
    assert_eq!(identity.0.load(SeqCst),7,"one identity read per valid call, no retry");
}
#[test]
fn finite_limits_and_scope_are_validated_before_assembly(){
    for scenario in ["namespace","env","default-over-max","max-over-day"]{
        let mut scope=scope();let mut bounds=limits();
        match scenario{"namespace"=>scope.namespace.clear(),"env"=>scope.env.clear(),"default-over-max"=>bounds.default_timeout_ms=NonZeroU64::new(101).unwrap(),
            _=>bounds.max_timeout_ms=NonZeroU64::new(86_400_001).unwrap()}
        assert!(AgentInvokeDirectApplicationService::new(scope,Arc::new(Identity(AtomicUsize::new(0))),Arc::new(Runs(AtomicU8::new(0))),bounds).is_err());
    }
}
#[tokio::test]
async fn required_identity_target_and_session_validation_precedes_any_resolution(){
    let identity=Arc::new(Identity(AtomicUsize::new(0)));
    let service=AgentInvokeDirectApplicationService::new(scope(),identity.clone(),Arc::new(Runs(AtomicU8::new(0))),limits()).unwrap();
    for scenario in ["empty-source","human-source","empty-code","empty-target","human-target","session-control"]{
        let mut c=command();match scenario{
            "empty-source"=>c.identity.bot_id.clear(),"human-source"=>c.identity.bot_id="human_1".into(),"empty-code"=>c.identity.agent_code.clear(),
            "empty-target"=>c.to_bot_id.clear(),"human-target"=>c.to_bot_id="human_1".into(),_=>c.session_id=Some("bad\n".into()),}
        assert!(service.invoke(c).await.is_err(),"{scenario}");
    }
    assert_eq!(identity.0.load(SeqCst),0);
}
