use async_trait::async_trait;
use bcs_domain::{Organization, OrganizationMember};

use crate::{BotCapabilities, OrganizationMemberPage, ServiceResult};
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
pub struct OrganizationMemberPageQuery {
    pub include_disabled: bool,
    pub role: Option<String>,
    pub offset: u64,
    pub limit: u64,
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
    async fn candidate_bots(
        &self,
        managing_provider_id: &str,
        query: OrganizationCandidateQuery,
    ) -> ServiceResult<Vec<OrganizationCandidateBot>>;
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
    async fn require_effective_member(
        &self,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<OrganizationMember>;
    async fn list_effective_members(
        &self,
        organization_code: &str,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>>;
    async fn require_runtime_member(
        &self,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<OrganizationMember>;
    async fn list_runtime_members(
        &self,
        organization_code: &str,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>>;
    async fn list_runtime_discovery_bots(
        &self,
        _organization_code: &str,
        _role: Option<&str>,
    ) -> ServiceResult<Option<Vec<OrganizationDiscoveryBot>>> {
        Ok(None)
    }
    async fn authorize_pair(
        &self,
        organization_code: &str,
        sender_bot_uuid: &str,
        target_bot_uuid: &str,
    ) -> ServiceResult<AuthorizedOrganizationPair>;
}
