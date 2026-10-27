use async_trait::async_trait;
pub use bcs_domain::provider::ProviderCollaborationScope;
use bcs_domain::{
    BotCapabilities, ProviderAuthMode, ProviderBotBinding, ProviderBotConnectionMode,
    ProviderCoordinationConfig, ProviderCredential, ProviderOrganizationManagementConfig,
    ProviderRecord, Skill,
};

use crate::{ServiceError, ServiceResult};

/// Public discovery projection. Never contains Provider configuration or credentials.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProviderBasicInfo {
    pub provider_id: String,
    pub slug: String,
    pub name: String,
    pub auth_mode: ProviderAuthMode,
    pub enabled: bool,
    pub protocol_version: String,
    pub created_at: u64,
    pub updated_at: u64,
}

/// Outcome of updating a provider-managed bot's capabilities in place.
///
/// Carries the (unchanged) binding alongside the merged `BotCapabilities` so
/// callers can surface the post-update bot info without a second lookup. The
/// binding identity (`bot_uuid`, `provider_id`, `provider_bot_ref`) is never
/// mutated by an update.
#[derive(Debug, Clone)]
pub struct UpdateProviderBotCoreResult {
    pub binding: ProviderBotBinding,
    pub capabilities: BotCapabilities,
}

#[derive(Debug, Clone)]
pub struct RuntimeBotIdentity {
    pub bot_uuid: String,
    pub provider_id: String,
}

#[derive(Debug, Clone)]
pub struct RegisteredProvider {
    pub provider: ProviderRecord,
    pub provider_admin_token: String,
    pub bcs_to_provider_token: String,
}

/// Parameters for registering a provider-managed bot.
///
/// `domains`, `skills`, and `scopes` carry the same capability semantics as the
/// `POST /bots/onboard` flow.
#[derive(Debug, Clone, Default)]
pub struct RegisterProviderBotParams {
    pub webhook_url: Option<String>,
    pub bot_name: String,
    pub summary: Option<String>,
    pub owners: Vec<String>,
    pub provider_bot_ref: String,
    pub domains: Vec<String>,
    pub skills: Vec<Skill>,
    pub scopes: Vec<String>,
    pub bot_uuid: Option<String>,
    pub connection_mode: ProviderBotConnectionMode,
}

#[async_trait]
pub trait ProviderCoreService: Send + Sync {
    /// Unauthenticated discovery, including disabled Providers. None means absent.
    async fn get_provider_by_slug(&self, slug: &str) -> ServiceResult<Option<ProviderBasicInfo>> {
        let _ = slug;
        Err(ServiceError::InvalidOperation {
            message: "provider slug lookup is not configured".to_string(),
            request_id: None,
        })
    }

    async fn register_provider_with_policy(
        &self, name: String, webhook_url: Option<String>, auth_mode: ProviderAuthMode,
        created_by: String, protocol_version: Option<String>, coordination: Option<ProviderCoordinationConfig>,
        slug: Option<String>, admin_callback_url: Option<String>, scope: Option<ProviderCollaborationScope>,
    ) -> ServiceResult<RegisteredProvider> {
        if scope.is_some() { return Err(ServiceError::InternalError("Provider policy registration is not configured".into())); }
        self.register_provider_with_slug(name, webhook_url, auth_mode, created_by, protocol_version, coordination, slug, admin_callback_url).await
    }

    async fn update_provider_with_policy(
        &self, provider_id: &str, provider_admin_token: &str, authenticated_staff_id: &str,
        name: Option<String>, webhook_url: Option<String>, protocol_version: Option<String>, coordination: Option<ProviderCoordinationConfig>,
        organization_management: Option<ProviderOrganizationManagementConfig>, slug: Option<String>, admin_callback_url: Option<String>,
        scope: Option<ProviderCollaborationScope>,
    ) -> ServiceResult<ProviderRecord> {
        if scope.is_some() { return Err(ServiceError::InternalError("Provider policy update is not configured".into())); }
        self.update_provider_with_slug(provider_id, provider_admin_token, authenticated_staff_id, name, webhook_url, protocol_version, coordination, organization_management, slug, admin_callback_url).await
    }

    /// Additive registration entry point preserving callers of register_provider.
    async fn register_provider_with_slug(
        &self, name: String, webhook_url: Option<String>, auth_mode: ProviderAuthMode,
        created_by: String, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>, slug: Option<String>,
        admin_callback_url: Option<String>,
    ) -> ServiceResult<RegisteredProvider> {
        if slug.is_some() || admin_callback_url.is_some() {
            return Err(ServiceError::InvalidOperation {
                message: "provider slug registration is not configured".to_string(),
                request_id: None,
            });
        }
        self.register_provider(name, webhook_url, auth_mode, created_by, protocol_version, coordination).await
    }

    /// Slug omission preserves it; all supplied metadata is updated atomically.
    async fn update_provider_with_slug(
        &self, provider_id: &str, provider_admin_token: &str, authenticated_staff_id: &str,
        name: Option<String>, webhook_url: Option<String>, protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
        organization_management: Option<ProviderOrganizationManagementConfig>, slug: Option<String>,
        admin_callback_url: Option<String>,
    ) -> ServiceResult<ProviderRecord> {
        if slug.is_some() || admin_callback_url.is_some() {
            return Err(ServiceError::InvalidOperation {
                message: "provider slug updates are not configured".to_string(),
                request_id: None,
            });
        }
        self.update_provider(provider_id, provider_admin_token, authenticated_staff_id, name,
            webhook_url, protocol_version, coordination, organization_management).await
    }

    async fn register_provider(
        &self,
        name: String,
        webhook_url: Option<String>,
        auth_mode: ProviderAuthMode,
        created_by: String,
        protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
    ) -> ServiceResult<RegisteredProvider>;

    async fn authenticate_provider_admin(&self, token: &str) -> ServiceResult<ProviderRecord>;
    async fn get_downlink_credential(&self, provider_id: &str)
    -> ServiceResult<ProviderCredential>;
    async fn get_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<ProviderRecord>;
    async fn update_provider(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        name: Option<String>,
        webhook_url: Option<String>,
        protocol_version: Option<String>,
        coordination: Option<ProviderCoordinationConfig>,
        organization_management: Option<ProviderOrganizationManagementConfig>,
    ) -> ServiceResult<ProviderRecord>;

    async fn update_provider_admin_callback_url(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        admin_callback_url: String,
    ) -> ServiceResult<ProviderRecord> {
        let _ = (
            provider_id,
            provider_admin_token,
            authenticated_staff_id,
            admin_callback_url,
        );
        Err(ServiceError::InvalidOperation {
            message: "provider admin callback updates are not configured".to_string(),
            request_id: None,
        })
    }
    async fn set_provider_disabled(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        authenticated_staff_id: &str,
        disabled: bool,
    ) -> ServiceResult<ProviderRecord>;
}

/// Bot-authority retirement branch (spec §7.1): the durable
/// identity location the Provider Core resolves the retiring
/// Bot from. The store re-derives the branch under the lock;
/// a mismatch is an error, never a silent reinterpretation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderRetireBranch {
    /// Provider Bot metadata exists (the accepted Provider
    /// membership record routes the retirement).
    Metadata,
    /// A gateway binding routes the deletion and the Bot
    /// carries no provider metadata (legacy gateway before
    /// metadata backfill).
    BindingFallback,
    /// An owner-suffixed legacy Bot with no binding and no
    /// provider metadata. The allow flag only relaxes the
    /// LOOKUP — the existing admission conditions stay intact.
    LegacyUnbound,
}

/// Application command for the Provider retirement (bot-authority
/// plan §3.1 frozen contract, spec §7.1). `provider_admin_token`
/// is verified ONLY by the Core; it never enters the mutation,
/// audit rows or any return value.
#[derive(Debug, Clone)]
pub struct RetireProviderBotParams {
    pub provider_id: String,
    pub provider_admin_token: String,
    pub provider_bot_ref: String,
    /// `true` only relaxes the LegacyUnbound lookup to the
    /// existing owner-suffixed admission; it never grants
    /// deletion power by itself.
    pub allow_unbound_owner_suffixed_bot: bool,
}

/// Already-generated command from the Provider Core to the
/// BotProviderRepo after the Provider credential verification.
/// It carries the verified Provider id (never the token) and
/// the expected branch the store must re-verify under the lock.
#[derive(Debug, Clone)]
pub struct ProviderRetire {
    pub provider_id: String,
    pub bot_id: String,
    pub provider_bot_ref: String,
    pub mutation: crate::types::bot_authority::AuthorityMutation,
    pub expected_branch: ProviderRetireBranch,
}

/// Committed retirement result. `bot_id`/`provider_id`/
/// `provider_bot_ref`/`branch` are the non-credential locating
/// information the existing external cleanup needs, and they
/// ARE retrievable from the retained tombstone/metadata/binding
/// on a retry. `changed` is false when the retirement was
/// already tombstoned (idempotent re-execution).
#[derive(Debug, Clone)]
pub struct ProviderRetirement {
    pub bot_id: String,
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub changed: bool,
    pub branch: ProviderRetireBranch,
}

#[async_trait]
pub trait ProviderBotCoreService: Send + Sync {
    /// Retire a Provider Bot atomically (bot-authority spec §7.1).
    ///
    /// Verifies the Provider credential (the token is used for
    /// verification only), resolves the Bot within the existing
    /// authorization limits on the three durable identity
    /// branches, and issues one atomic retirement command: the
    /// Bot tombstone, the Provider/ref tombstone, the necessary
    /// binding disable, owner/manager edge revocation, pending
    /// transfer closure (fixed system marker) and the retire
    /// audit commit in ONE store transaction — never followed by
    /// a second ordinary delete. The verified Provider actor
    /// (`Provider { provider_id }`) is the audit/mutation
    /// initiator; the token is never recorded.
    async fn retire_registered_bot(
        &self, params: RetireProviderBotParams,
    ) -> ServiceResult<ProviderRetirement> {
        let _ = params;
        Err(ServiceError::InvalidOperation {
            message: "provider bot retirement is not configured".to_string(),
            request_id: None,
        })
    }

    /// Update only the routing override; identities and capabilities remain unchanged.
    async fn update_provider_bot_webhook(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        provider_bot_ref: &str,
        webhook_url: Option<String>,
    ) -> ServiceResult<UpdateProviderBotCoreResult> {
        let _ = (provider_id, provider_admin_token, provider_bot_ref, webhook_url);
        Err(ServiceError::InvalidOperation {
            message: "provider bot endpoint updates are not configured".to_string(),
            request_id: None,
        })
    }
    async fn register_provider_bot_with_bot_uuid(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        params: RegisterProviderBotParams,
    ) -> ServiceResult<(ProviderBotBinding, Option<String>)>;

    async fn list_provider_bots(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
    ) -> ServiceResult<Vec<ProviderBotBinding>>;

    async fn get_provider_bot_binding_by_ref(
        &self,
        provider_id: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let _ = (provider_id, provider_bot_ref);
        Err(ServiceError::InvalidOperation {
            message: "provider bot binding lookup is not configured".to_string(),
            request_id: None,
        })
    }

    async fn get_provider_bot_binding_by_bot_uuid(
        &self,
        bot_uuid: &str,
    ) -> ServiceResult<Option<ProviderBotBinding>> {
        let _ = bot_uuid;
        Err(ServiceError::InvalidOperation {
            message: "provider bot binding lookup is not configured".to_string(),
            request_id: None,
        })
    }

    async fn authenticate_static_bearer_event(
        &self,
        provider_id: &str,
        bot_runtime_token: &str,
    ) -> ServiceResult<RuntimeBotIdentity>;

    async fn authenticate_agentpass_event(
        &self,
        provider_id: &str,
        agent_code: &str,
    ) -> ServiceResult<RuntimeBotIdentity>;

    async fn authenticate_agentpass_actor_event(&self, provider_id: &str, agent_code: &str, actor: &str) -> ServiceResult<RuntimeBotIdentity> {
        let bot = self.authenticate_agentpass_event(provider_id, agent_code).await?;
        if bot.bot_uuid != actor { return Err(ServiceError::Unauthorized("Actor does not match AgentPass Bot".into())); }
        Ok(bot)
    }

    async fn authenticate_provider_admin_event(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        provider_bot_ref: &str,
    ) -> ServiceResult<RuntimeBotIdentity>;

    async fn get_provider_coordination_config(
        &self,
        provider_id: &str,
    ) -> ServiceResult<ProviderCoordinationConfig> {
        let _ = provider_id;
        Err(ServiceError::InvalidOperation {
            message: "provider coordination config lookup is not configured".to_string(),
            request_id: None,
        })
    }

    async fn set_provider_bot_disabled(
        &self,
        provider_id: &str,
        bot_uuid: &str,
        provider_admin_token: &str,
        disabled: bool,
    ) -> ServiceResult<ProviderBotBinding>;

    /// Update a provider-managed bot's capabilities in place.
    ///
    /// Each `Option` field is a PATCH: `Some` replaces the value (an empty
    /// `Vec` clears the array), `None` leaves it unchanged. The binding
    /// identity (`provider_bot_ref`, `bot_uuid`) is never changed. The
    /// Stored `agent_code` is preserved; ordinary updates cannot change identity.
    async fn update_provider_bot(
        &self,
        provider_id: &str,
        provider_admin_token: &str,
        provider_bot_ref: &str,
        name: Option<String>,
        summary: Option<String>,
        domains: Option<Vec<String>>,
        skills: Option<Vec<Skill>>,
        scopes: Option<Vec<String>>,
        visibility: Option<String>,
    ) -> ServiceResult<UpdateProviderBotCoreResult> {
        let _ = (
            provider_id,
            provider_admin_token,
            provider_bot_ref,
            name,
            summary,
            domains,
            skills,
            scopes,
            visibility,
        );
        Err(ServiceError::InvalidOperation {
            message: "provider bot updates are not configured".to_string(),
            request_id: None,
        })
    }
}

/// The owner-suffixed legacy Bot shape
/// (`{prefix}:{owner}`) — the single Home of the
/// LegacyUnbound admission rule. The
/// `allow_unbound_owner_suffixed_bot` flag only
/// relaxes the LOOKUP; this rule keeps the
/// existing admission (spec §7.1) and every
/// caller (Core, stores) evaluates the SAME
/// spelling here.
pub fn is_owner_suffixed_bot_id(bot_id: &str) -> bool {
    bot_id.rsplit_once(':').is_some_and(|(prefix, owner)| {
        !prefix.trim().is_empty() && !owner.trim().is_empty()
    })
}

/// Library-level command, independent of JSON PATCH representation.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub enum BotWebhookChange {
    #[default]
    Unchanged,
    Inherit,
    Set(String),
}
