use async_trait::async_trait;
use bcs_domain::{Organization, OrganizationMember};

use crate::{BotCapabilities, OrganizationMemberPage, ServiceResult};

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
    async fn authorize_pair(
        &self,
        organization_code: &str,
        sender_bot_uuid: &str,
        target_bot_uuid: &str,
    ) -> ServiceResult<AuthorizedOrganizationPair>;
}
