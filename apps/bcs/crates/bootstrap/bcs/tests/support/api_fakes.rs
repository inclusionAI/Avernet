//! Counting fakes for the Task 13 acceptance fixtures — exactly the
//! seams bootstrap owns, and nothing else: the SecretAccessPort (counting
//! in-memory store), the Bot registry the Stateful gate revalidates
//! against (scripted per outcome), the business application services
//! behind the routes (counting stubs), the gateway principal token
//! minters (the real wire shape), and the strict-session seed signer.

#![allow(dead_code)] // shared support: each suite target uses a subset

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
use serde_json::{Value, json};

use bcs_secret_local::InMemorySecretAccess;
use bcs_service_api::application::v1::{
    ApplicationError, GroupService, ListGroups, ListPublicGroups,
};
use bcs_service_api::core::BotRegistryCoreService;
use bcs_service_api::port::secret::{SecretAccessError, SecretAccessPort, SecretRecord};
use bcs_service_api::types::error::ServiceError;

use bcs_jwt::{OAuthSessionClaims, OAuthSessionJwt};

use super::api_chain::{
    ENV, GATEWAY_SIGNING_KEY_REF, GATEWAY_SIGNING_MATERIAL, SESSION_SECRET_REF,
    SESSION_SIGNING_MATERIAL, unix_now,
};

// Counting secret port (N4 evidence: which logical references get resolved)
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub struct CountingSecretAccess {
    store: InMemorySecretAccess,
    calls: Arc<Mutex<Vec<String>>>,
}

impl CountingSecretAccess {
    pub fn with_entries(entries: Vec<(String, String)>) -> Self {
        Self {
            store: InMemorySecretAccess::with_entries(
                entries
                    .into_iter()
                    .map(|(name, value)| (name, String::new(), value)),
            ),
            calls: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// Entries for the standard gateway+github chain (session key, gateway
    /// key, github client secret).
    pub fn standard_entries() -> Vec<(String, String)> {
        vec![
            (
                SESSION_SECRET_REF.to_string(),
                SESSION_SIGNING_MATERIAL.to_string(),
            ),
            (
                GATEWAY_SIGNING_KEY_REF.to_string(),
                String::from_utf8_lossy(GATEWAY_SIGNING_MATERIAL).to_string(),
            ),
            (
                "task13-github-client-secret".to_string(),
                "gh-secret".to_string(),
            ),
        ]
    }

    pub fn requests(&self) -> Vec<String> {
        self.calls.lock().expect("log lock").clone()
    }

    pub fn count(&self, name: &str) -> u64 {
        self.requests().iter().filter(|entry| entry == &name).count() as u64
    }
}

#[async_trait]
impl SecretAccessPort for CountingSecretAccess {
    async fn get_secret(&self, name: &str) -> Result<SecretRecord, SecretAccessError> {
        self.calls.lock().expect("log lock").push(name.to_string());
        self.store.get_secret(name).await
    }
}

// ---------------------------------------------------------------------------
// Business-service call counters
// ---------------------------------------------------------------------------

#[derive(Default)]
pub struct CallCounter {
    counts: Mutex<std::collections::BTreeMap<String, u64>>,
}

impl CallCounter {
    pub fn bump(&self, op: &str) {
        *self
            .counts
            .lock()
            .expect("counter lock")
            .entry(op.to_string())
            .or_insert(0) += 1;
    }

    pub fn calls(&self, op: &str) -> u64 {
        self.counts
            .lock()
            .expect("counter lock")
            .get(op)
            .copied()
            .unwrap_or(0)
    }
}

/// A counting `GroupService`: the methods a protected route can reach
/// record one counter entry and answer with an empty page (so a REACHED
/// handler is observable both by the counter and a 200 with empty data —
/// the positive control for "business == 0" assertions).
pub struct CountingGroupService {
    pub calls: Arc<CallCounter>,
}

macro_rules! counted_unreachable {
    ($self:expr, $op:literal) => {{
        $self.calls.bump($op);
        Err(ApplicationError::internal(
            "group business service is not part of this fixture",
        ))
    }};
}

#[async_trait]
impl GroupService for CountingGroupService {
    async fn list_groups(
        &self,
        _command: ListGroups,
    ) -> Result<bcs_service_api::application::v1::Page<bcs_service_api::application::v1::GroupSummary>, ApplicationError> {
        self.calls.bump("list_groups");
        Ok(bcs_service_api::application::v1::Page::empty(0, 20))
    }

    async fn list_public_groups(
        &self,
        _command: ListPublicGroups,
    ) -> Result<bcs_service_api::application::v1::Page<bcs_service_api::application::v1::GroupSummary>, ApplicationError> {
        self.calls.bump("list_public_groups");
        Ok(bcs_service_api::application::v1::Page::empty(0, 20))
    }

    async fn create(
        &self,
        _command: bcs_service_api::application::v1::CreateGroup,
    ) -> Result<bcs_service_api::application::v1::GroupDetail, ApplicationError> {
        counted_unreachable!(self, "create")
    }

    async fn get(
        &self,
        _command: bcs_service_api::application::v1::GetGroup,
    ) -> Result<bcs_service_api::application::v1::GroupDetail, ApplicationError> {
        counted_unreachable!(self, "get")
    }

    async fn update(
        &self,
        _command: bcs_service_api::application::v1::UpdateGroup,
    ) -> Result<bcs_service_api::application::v1::GroupDetail, ApplicationError> {
        counted_unreachable!(self, "update")
    }

    async fn delete(
        &self,
        _command: bcs_service_api::application::v1::DeleteGroup,
    ) -> Result<bcs_service_api::application::v1::DeleteResult, ApplicationError> {
        counted_unreachable!(self, "delete")
    }

    async fn add_participant(
        &self,
        _command: bcs_service_api::application::v1::AddGroupParticipant,
    ) -> Result<bcs_service_api::application::v1::Participant, ApplicationError> {
        counted_unreachable!(self, "add_participant")
    }

    async fn update_participant(
        &self,
        _command: bcs_service_api::application::v1::UpdateGroupParticipant,
    ) -> Result<bcs_service_api::application::v1::Participant, ApplicationError> {
        counted_unreachable!(self, "update_participant")
    }

    async fn delete_participant(
        &self,
        _command: bcs_service_api::application::v1::DeleteGroupParticipant,
    ) -> Result<bcs_service_api::application::v1::DeleteResult, ApplicationError> {
        counted_unreachable!(self, "delete_participant")
    }
}

// ---------------------------------------------------------------------------
// Special-purpose-surface fakes (N2): the draft public GET and the
// Bot Self / Agent Self bearer surfaces keep their OWN protocols — the
// counting records that the application actually ran.
// ---------------------------------------------------------------------------

pub struct CountingDraftService {
    pub calls: Arc<CallCounter>,
}

#[async_trait]
impl bcs_service_api::application::collaboration_draft::CollaborationDraftService
    for CountingDraftService
{
    async fn create(
        &self,
        _owner: &str,
        _id: &str,
        _content: bcs_service_api::application::collaboration_draft::DraftContent,
    ) -> Result<bcs_service_api::application::collaboration_draft::SavedDraftRevision, bcs_service_api::application::collaboration_draft::DraftError> {
        self.calls.bump("draft.create");
        Err(bcs_service_api::application::collaboration_draft::DraftError::Forbidden)
    }
    async fn update(
        &self,
        _owner: &str,
        _id: &str,
        _expected: u64,
        _content: bcs_service_api::application::collaboration_draft::DraftContent,
    ) -> Result<bcs_service_api::application::collaboration_draft::DraftRevision, bcs_service_api::application::collaboration_draft::DraftError> {
        self.calls.bump("draft.update");
        Err(bcs_service_api::application::collaboration_draft::DraftError::Forbidden)
    }
    async fn metadata(
        &self,
        _owner: &str,
        _id: &str,
    ) -> Result<bcs_service_api::application::collaboration_draft::DraftMetadata, bcs_service_api::application::collaboration_draft::DraftError> {
        self.calls.bump("draft.metadata");
        Err(bcs_service_api::application::collaboration_draft::DraftError::Forbidden)
    }
    async fn read(
        &self,
        _id: &str,
        _revision: u64,
    ) -> Result<bcs_service_api::application::collaboration_draft::DraftRevision, bcs_service_api::application::collaboration_draft::DraftError> {
        self.calls.bump("draft.read");
        Err(bcs_service_api::application::collaboration_draft::DraftError::Unavailable)
    }
    async fn validate(
        &self,
        _id: &str,
        _revision: u64,
    ) -> Result<bcs_service_api::application::collaboration_draft::DraftValidation, bcs_service_api::application::collaboration_draft::DraftError> {
        self.calls.bump("draft.validate");
        Err(bcs_service_api::application::collaboration_draft::DraftError::Forbidden)
    }
    async fn reference(
        &self,
        _owner: &str,
        _id: &str,
        _revision: u64,
    ) -> Result<bcs_service_api::application::collaboration_draft::DraftReference, bcs_service_api::application::collaboration_draft::DraftError> {
        self.calls.bump("draft.reference");
        Err(bcs_service_api::application::collaboration_draft::DraftError::Forbidden)
    }
    async fn revoke(
        &self,
        _owner: &str,
        _id: &str,
        _revision: u64,
    ) -> Result<(), bcs_service_api::application::collaboration_draft::DraftError> {
        self.calls.bump("draft.revoke");
        Err(bcs_service_api::application::collaboration_draft::DraftError::Forbidden)
    }
    async fn delete(
        &self,
        _owner: &str,
        _id: &str,
    ) -> Result<(), bcs_service_api::application::collaboration_draft::DraftError> {
        self.calls.bump("draft.delete");
        Err(bcs_service_api::application::collaboration_draft::DraftError::Forbidden)
    }
}

pub struct CountingBotSelfService {
    pub calls: Arc<CallCounter>,
}

#[async_trait]
impl bcs_service_api::application::v1::BotSelfService for CountingBotSelfService {
    async fn get_me(
        &self,
        _token: &str,
    ) -> Result<bcs_service_api::application::v1::BotSelfView, ApplicationError> {
        self.calls.bump("bot_self.get_me");
        Ok(bcs_service_api::application::v1::BotSelfView {
            registration_status: bcs_service_api::application::v1::BotRegistrationStatus::Unregistered,
            agent_code: "task13-agent".to_string(),
            bot: None,
        })
    }

    async fn get_agent_me(
        &self,
        _token: &str,
        _provider: Option<&str>,
        _cursor: Option<&str>,
        _limit: usize,
    ) -> Result<bcs_service_api::application::v1::AgentSelfView, ApplicationError> {
        self.calls.bump("bot_self.get_agent_me");
        Ok(bcs_service_api::application::v1::AgentSelfView {
            agent_code: "task13-agent".to_string(),
            bots: Vec::new(),
            next_cursor: None,
        })
    }
}

// ---------------------------------------------------------------------------
// Scripted Bot registry (the Stateful gate's revalidation dependency)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RegistryScript {
    Ok,
    /// The signed Bot was deleted (`denied("Bot has been deleted")` in the
    /// real registry).
    Deleted,
    /// The signed Bot is disabled (Forbidden).
    Disabled,
    /// The signed Actor does not match the persisted registration
    /// (`denied("Actor does not match ...")`).
    ActorMismatch,
    /// The persisted-state lookup itself fails (store fault) — the 502
    /// `bot_identity_unavailable` class.
    StoreFailure,
}

pub struct ScriptedRegistry {
    script: Mutex<RegistryScript>,
    pub validate_calls: AtomicU64,
}

impl ScriptedRegistry {
    pub fn new(script: RegistryScript) -> Self {
        Self {
            script: Mutex::new(script),
            validate_calls: AtomicU64::new(0),
        }
    }

    pub fn set(&self, script: RegistryScript) {
        *self.script.lock().expect("script lock") = script;
    }
}

// The remaining trait surface is fixture-irrelevant: every required method
// answers with the same inert shape Noop registries use, so the scripted
// registry is usable anywhere the real `BotCore` would be injected.
#[async_trait]
impl BotRegistryCoreService for ScriptedRegistry {
    async fn validate_signed_bot(&self, _id: &str) -> Result<(), ServiceError> {
        self.validate_calls.fetch_add(1, Ordering::SeqCst);
        match *self.script.lock().expect("script lock") {
            RegistryScript::Ok => Ok(()),
            RegistryScript::Deleted => Err(ServiceError::Unauthorized(
                "Bot has been deleted".to_string(),
            )),
            RegistryScript::Disabled => {
                Err(ServiceError::Forbidden("Bot is disabled".to_string()))
            }
            RegistryScript::ActorMismatch => Err(ServiceError::Unauthorized(
                "Actor does not match the Bot registration".to_string(),
            )),
            RegistryScript::StoreFailure => Err(ServiceError::InternalError(
                "bot identity store unavailable".to_string(),
            )),
        }
    }

    async fn register(
        &self,
        _bot_id: String,
        _capabilities: bcs_service_api::BotCapabilities,
    ) -> Result<(), ServiceError> {
        Ok(())
    }
    async fn update_status(&self, _bot_id: &str) -> bool {
        false
    }
    async fn get(&self, _bot_id: &str) -> Option<bcs_service_api::RegisteredBot> {
        None
    }
    async fn get_agent_credentials(
        &self,
        _bot_id: &str,
    ) -> Option<bcs_service_api::AgentCredentials> {
        None
    }
    async fn list_active(&self) -> Vec<bcs_service_api::RegisteredBot> {
        Vec::new()
    }
    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<bcs_service_api::RegisteredBot> {
        Vec::new()
    }
    async fn list_uninitialized_bots_by_creator(
        &self,
        _created_by: &str,
        _limit: usize,
    ) -> Result<Vec<bcs_service_api::RegisteredBot>, ServiceError> {
        Ok(Vec::new())
    }
    async fn discover(&self, _query: &str) -> Vec<bcs_service_api::RegisteredBot> {
        Vec::new()
    }
    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<bcs_service_api::RegisteredBot> {
        Vec::new()
    }
    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<bcs_service_api::RegisteredBot> {
        Vec::new()
    }
    async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<bcs_service_api::RegisteredBot> {
        Vec::new()
    }
    async fn unregister(&self, _bot_id: &str) -> bool {
        false
    }
    async fn cleanup_expired(&self) {}
    async fn load_from_storage(&self, _bot_id: &str) -> Option<bcs_service_api::BotCapabilities> {
        None
    }
    async fn save_to_storage(
        &self,
        _bot_id: &str,
        _caps: &bcs_service_api::BotCapabilities,
    ) -> Result<(), ServiceError> {
        Ok(())
    }
    async fn save_created_by(
        &self,
        _bot_id: &str,
        _created_by: &str,
        _overwrite: bool,
    ) -> Result<(), ServiceError> {
        Ok(())
    }
    async fn update_visibility(&self, _bot_id: &str, _visibility: &str) -> Result<(), ServiceError> {
        Ok(())
    }
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> Result<(), ServiceError> {
        Ok(())
    }
    async fn has_been_onboarded(&self, _bot_id: &str) -> bool {
        false
    }
    async fn save_token(&self, _bot_id: &str, _token: &str) -> Result<(), ServiceError> {
        Ok(())
    }
    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }
    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }
    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }
    async fn reconnect_streaming(&self, _existing_token: String) -> Result<(String, String), ()> {
        Err(())
    }
    async fn disconnect_streaming(&self, _bot_id: &str) {}
    async fn is_connected(&self, _bot_id: &str) -> bool {
        false
    }
    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Err(())
    }
    async fn list_connected(&self) -> Vec<String> {
        Vec::new()
    }
    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}
    async fn register_http_connection(&self, _bot_id: String, _token: String) -> String {
        String::new()
    }
}

// ---------------------------------------------------------------------------
// Gateway principal tokens (the real wire shape the Gateway verifier reads)
// ---------------------------------------------------------------------------

pub fn gateway_user_principal_token(user_id: &str) -> String {
    gateway_token(json!([{
        "type": "user",
        "tenant": "tenant-a",
        "subject": { "id": user_id, "username": user_id, "tenant_id": "tenant-a" }
    }]))
}

pub fn gateway_bot_principal_token(bot_uuid: &str) -> String {
    gateway_token(json!([{
        "type": "bot",
        "tenant": "tenant-a",
        "bot": {
            "bot_uuid": bot_uuid,
            "owner_id": "task13-owner",
            "app_id": 7,
            "agent_code": "task13-agent",
            "tenant": "tenant-a"
        }
    }]))
}

fn gateway_token(principals: Value) -> String {
    let now = unix_now();
    let mut header = Header::new(Algorithm::HS256);
    header.typ = Some("JWT".to_string());
    header.kid = Some("bare".to_string());
    encode(
        &header,
        &json!({
            "iss": "gateway",
            "aud": "bcs",
            "iat": now.saturating_sub(1),
            "exp": now + 300,
            "principals": principals,
        }),
        &EncodingKey::from_secret(GATEWAY_SIGNING_MATERIAL),
    )
    .expect("test gateway principal token signs")
}

// ---------------------------------------------------------------------------
// Strict-session seeding (the store side of a completed exchange)
// ---------------------------------------------------------------------------

pub struct SeededSession {
    pub user_id: String,
    pub token: String,
    pub sid: String,
    pub revision: u64,
}

pub fn sign_session_token(
    jwt: &OAuthSessionJwt,
    user_id: &str,
    session_id: &str,
    revision: u64,
) -> String {
    let now = unix_now();
    jwt.sign(&OAuthSessionClaims {
        sub: user_id.to_string(),
        src: "github".to_string(),
        env: ENV.to_string(),
        session_id: session_id.to_string(),
        revision,
        iat: now,
        exp: now + 1800,
        name: Some("Task 13 用户".to_string()),
    })
    .expect("fixture session token signs")
}
