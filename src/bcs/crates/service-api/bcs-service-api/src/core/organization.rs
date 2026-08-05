use async_trait::async_trait;
use bcs_domain::{ActorKind, Organization, OrganizationMember};

use crate::{BotCapabilities, OrganizationMemberPage, ServiceError, ServiceResult, Skill};
use crate::port::repo::OrganizationDiscoveryBot;

#[derive(Debug, Clone)]
pub struct AuthorizedOrganizationPair {
    pub organization: Organization,
    pub sender: OrganizationMember,
    pub target: OrganizationMember,
}

#[derive(Debug, Clone)]
pub struct OrganizationCandidateBot {
    pub bot_uuid: String,
    pub provider_id: String,
    pub capabilities: BotCapabilities,
}

#[derive(Debug, Clone, Default)]
pub struct OrganizationCandidateQuery {
    pub organization_code: String,
    pub q: Option<String>,
    pub provider_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct OrganizationCandidatePageQuery {
    pub candidate: OrganizationCandidateQuery,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone)]
pub struct OrganizationCandidateBotPage {
    pub bots: Vec<OrganizationCandidateBot>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone)]
pub struct OrganizationMemberBotDetail {
    pub provider_id: String,
    pub provider_bot_ref: String,
    pub agent_code: Option<String>,
    pub capabilities: BotCapabilities,
    pub created_by: Option<String>,
    pub actor_kind: ActorKind,
    pub env: Option<String>,
}

#[derive(Debug, Clone)]
pub struct OrganizationCandidateBotDetail {
    pub organization_code: String,
    pub bot_uuid: String,
    pub is_member: bool,
    pub bot: OrganizationMemberBotDetail,
}

#[derive(Debug, Clone)]
pub struct OrganizationMemberDetail {
    pub member: OrganizationMember,
    pub bot: Option<OrganizationMemberBotDetail>,
}

#[derive(Debug, Clone)]
pub struct OrganizationMemberPageQuery {
    pub include_disabled: bool,
    pub role: Option<String>,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone, Default)]
pub struct OrganizationMemberProfilePatch {
    pub name: Option<String>,
    pub summary: Option<String>,
    pub domains: Option<Vec<String>>,
    pub skills: Option<Vec<Skill>>,
    pub scopes: Option<Vec<String>>,
}

#[derive(Debug, Clone)]
pub struct OrganizationMemberProfile {
    pub organization_code: String,
    pub bot_uuid: String,
    pub provider_id: String,
    pub capabilities: BotCapabilities,
}

#[async_trait]
pub trait OrganizationCoreService: Send + Sync {
    async fn create(
        &self,
        managing_provider_id: &str,
        code: &str,
        name: &str,
        description: Option<&str>,
    ) -> ServiceResult<Organization>;
    async fn get_for_manager(
        &self,
        managing_provider_id: &str,
        code: &str,
    ) -> ServiceResult<Organization>;
    async fn list_for_manager(
        &self,
        managing_provider_id: &str,
        include_disabled: bool,
    ) -> ServiceResult<Vec<Organization>>;
    async fn update_for_manager(
        &self,
        managing_provider_id: &str,
        code: &str,
        name: Option<&str>,
        description: Option<Option<&str>>,
        disabled: Option<bool>,
    ) -> ServiceResult<Organization>;
    async fn put_member(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        bot_uuid: &str,
        role: Option<&str>,
    ) -> ServiceResult<OrganizationMember>;
    async fn delete_member(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<()>;
    async fn get_member_for_manager(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>>;
    async fn get_member_detail_for_manager(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMemberDetail>> {
        Ok(self
            .get_member_for_manager(managing_provider_id, organization_code, bot_uuid)
            .await?
            .map(|member| OrganizationMemberDetail { member, bot: None }))
    }
    async fn list_members_for_manager(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        include_disabled: bool,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>>;
    async fn list_members_page_for_manager(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        query: OrganizationMemberPageQuery,
    ) -> ServiceResult<OrganizationMemberPage> {
        let members = self
            .list_members_for_manager(
                managing_provider_id,
                organization_code,
                query.include_disabled,
                query.role.as_deref(),
            )
            .await?;
        let total = members.len() as u64;
        let members = members
            .into_iter()
            .skip(query.offset as usize)
            .take(query.limit as usize)
            .collect();
        Ok(OrganizationMemberPage {
            members,
            total,
            offset: query.offset,
            limit: query.limit,
        })
    }
    async fn update_member_profile(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        bot_uuid: &str,
        patch: OrganizationMemberProfilePatch,
    ) -> ServiceResult<OrganizationMemberProfile> {
        let _ = (managing_provider_id, organization_code, bot_uuid, patch);
        Err(ServiceError::InvalidOperation {
            message: "organization member profile update is not configured".to_string(),
            request_id: None,
        })
    }
    async fn candidate_bots(
        &self,
        managing_provider_id: &str,
        query: OrganizationCandidateQuery,
    ) -> ServiceResult<Vec<OrganizationCandidateBot>>;
    async fn candidate_bot_detail_for_manager(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationCandidateBotDetail>> {
        let _ = (managing_provider_id, organization_code, bot_uuid);
        Ok(None)
    }
    async fn candidate_bots_page(
        &self,
        managing_provider_id: &str,
        query: OrganizationCandidatePageQuery,
    ) -> ServiceResult<OrganizationCandidateBotPage> {
        let bots = self.candidate_bots(managing_provider_id, query.candidate).await?;
        let total = bots.len() as u64;
        let bots = usize::try_from(query.offset)
            .ok()
            .and_then(|offset| usize::try_from(query.limit).ok().map(|limit| (offset, limit)))
            .map(|(offset, limit)| bots.into_iter().skip(offset).take(limit).collect())
            .unwrap_or_default();
        Ok(OrganizationCandidateBotPage { bots, total, offset: query.offset, limit: query.limit })
    }
    /// Require membership plus the current provider/binding/delegation eligibility policy.
    async fn require_effective_member(
        &self,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<OrganizationMember>;
    /// List members that satisfy the current provider/binding/delegation eligibility policy.
    async fn list_effective_members(
        &self,
        organization_code: &str,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>>;
    /// Require only active organization and member lifecycle state.
    ///
    /// This does not confer authority to access another bot. Authorization callers must use
    /// `require_effective_member` or a use-case-specific application service instead.
    async fn require_runtime_member(
        &self,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<OrganizationMember>;
    /// List members by active organization/member lifecycle state only.
    async fn list_runtime_members(
        &self,
        organization_code: &str,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>>;
    /// Return a store-optimized discovery snapshot when the repository supports it.
    ///
    /// Consumers remain responsible for applying the authorization semantics of their use case.
    async fn list_runtime_discovery_bots(
        &self,
        _organization_code: &str,
        _role: Option<&str>,
    ) -> ServiceResult<Option<Vec<OrganizationDiscoveryBot>>> {
        Ok(None)
    }
    /// Authorize both participants for an organization-scoped A2A interaction.
    async fn authorize_pair(
        &self,
        organization_code: &str,
        sender_bot_uuid: &str,
        target_bot_uuid: &str,
    ) -> ServiceResult<AuthorizedOrganizationPair>;
}
