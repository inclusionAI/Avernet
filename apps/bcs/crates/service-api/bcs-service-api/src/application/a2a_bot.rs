use async_trait::async_trait;
use crate::{ServiceResult, port::repo::a2a_bot::A2aBotRecord};

#[derive(Clone)]
pub struct ConfigureA2aBotCommand {
    pub provider_id: String,
    pub provider_admin_token: String,
    pub bot_uuid: String,
    pub source: crate::types::a2a_bot::A2aCardSource,
    /// Optional legacy compare-and-set; omission performs an atomic last-write-wins replacement.
    pub expected_revision: Option<u64>,
    pub now_ms: i64,
}

#[derive(Clone,Debug)]
pub struct NamedA2aCard {
    pub provider_bot_ref:String,
    pub bot_id:String,
    pub provider_name:String,
    pub bot_name:String,
    pub agent_card_url:Option<String>,
    pub agent_card:serde_json::Value,
}
#[derive(Clone)]
pub struct RegisterNamedA2aBotCommand {
    pub provider_bot_ref:String,
    pub provider_name:String,
    pub provider_admin_token:String,
    pub owners:Vec<String>,
    pub summary:Option<String>,
    pub source:crate::types::a2a_bot::A2aCardSource,
    pub now_ms:i64,
}
pub struct NamedA2aRegistration {pub card:NamedA2aCard,pub bot_runtime_token:Option<String>}

/// Public v0.1 management view, deliberately excludes runtime credentials.
#[derive(Clone, Debug, serde::Serialize)]
pub struct ManagedA2aCard {
    pub provider_bot_ref:String,
    pub provider_name:String, pub agent_name:String, pub bot_id:String,
    pub agent_card_url:Option<String>, pub agent_card:serde_json::Value,
}
#[derive(Clone)]
pub struct RegisterAgentCommand {
    pub provider_bot_ref:String,
    pub visibility: crate::types::a2a_bot::A2aRegistrationVisibility,
    /// None, null and an empty list request the Provider's configured default owner.
    pub provider_admin_token:String, pub owners:Option<Vec<String>>, pub summary:Option<String>,
    pub source:crate::types::a2a_bot::A2aCardSource, pub now_ms:i64,
}

#[async_trait]
pub trait A2aBotManagementService: Send + Sync {
    async fn register_agent(&self,_command:RegisterAgentCommand)->ServiceResult<ManagedA2aCard> {
        Err(crate::ServiceError::InternalError("public A2A management unavailable".into()))
    }
    /// Exact source key recovery, scoped by authenticated Provider; zero or one result.
    async fn find_agents_by_provider_ref(&self,_token:&str,_provider_bot_ref:&str)->ServiceResult<Vec<ManagedA2aCard>> {
        Err(crate::ServiceError::InternalError("public A2A management unavailable".into()))
    }
    /// Provider-scoped management uses the stable ID returned by registration, never Card.name.
    async fn get_agent_by_id(&self,_token:&str,_bot_id:&str)->ServiceResult<ManagedA2aCard> {
        Err(crate::ServiceError::InternalError("public A2A management unavailable".into()))
    }
    async fn update_agent_by_id(&self,_token:&str,_bot_id:&str,_source:crate::types::a2a_bot::A2aCardSource)->ServiceResult<ManagedA2aCard> {
        Err(crate::ServiceError::InternalError("public A2A management unavailable".into()))
    }
    async fn update_agent_by_id_with_visibility(&self,token:&str,bot_id:&str,source:Option<crate::types::a2a_bot::A2aCardSource>,visibility:Option<crate::types::a2a_bot::A2aRegistrationVisibility>)->ServiceResult<ManagedA2aCard> {
        if visibility.is_none() { if let Some(source)=source { return self.update_agent_by_id(token,bot_id,source).await; } }
        Err(crate::ServiceError::InternalError("visibility update unavailable".into()))
    }
    async fn refresh_agent_by_id(&self,_token:&str,_bot_id:&str)->ServiceResult<ManagedA2aCard> {
        Err(crate::ServiceError::InternalError("public A2A management unavailable".into()))
    }

    /// Resolve a target for the existing chat admission use case. This does not
    /// grant permission to call it; the caller must still pass chat authorization.
    async fn resolve_provider_ref_target(&self,_provider:&str,_bot:&str)->ServiceResult<String> {
        Err(crate::ServiceError::InternalError("named A2A resolution is not configured".into()))
    }
    async fn register_named(&self,_command:RegisterNamedA2aBotCommand)->ServiceResult<NamedA2aRegistration> {
        Err(crate::ServiceError::InternalError("named A2A registration is not configured".into()))
    }
    /// Provider-admin admission: fetch before the atomic Bot/ownership/Card
    /// create. Existing Provider/ref conflicts; this never updates a Bot.
    async fn register_provider_bot(
        &self, _command: crate::RegisterProviderBotCommand, _source: crate::types::a2a_bot::A2aCardSource, _now_ms: i64,
    ) -> ServiceResult<crate::RegisterProviderBotOutcome> {
        Err(crate::ServiceError::InvalidOperation {
            message: "A2A Provider registration is not configured".into(), request_id: None,
        })
    }
    async fn get(&self, provider_id: &str, admin_token: &str, bot_uuid: &str) -> ServiceResult<Option<A2aBotRecord>>;
    async fn configure(&self, command: ConfigureA2aBotCommand) -> ServiceResult<A2aBotRecord>;
    /// Re-read the saved source URL. Inline Cards require explicit configure.
    async fn refresh(&self, provider_id: &str, admin_token: &str, bot_uuid: &str, now_ms: i64)
        -> ServiceResult<A2aBotRecord>;
    /// Trusted background maintenance only; never expose without authorization
    /// as an HTTP endpoint. Reuses saved URLs and cannot configure new routes.
    async fn refresh_due(&self, now_ms: i64) -> ServiceResult<usize>;
}
