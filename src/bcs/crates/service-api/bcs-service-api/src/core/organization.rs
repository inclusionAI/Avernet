use async_trait::async_trait;
use bcs_domain::{Organization, OrganizationMember};

use crate::{BotCapabilities, ServiceResult};

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
    pub domains: Option<String>,
    pub skills: Option<String>,
    pub scopes: Option<String>,
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
