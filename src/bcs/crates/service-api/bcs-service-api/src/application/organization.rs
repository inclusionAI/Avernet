use async_trait::async_trait;
use bcs_domain::{Organization, OrganizationMember};

use crate::{OrganizationCandidateBot, OrganizationCandidateQuery, ServiceResult};

#[derive(Debug, Clone)]
pub struct OrganizationAuth {
    pub provider_id: String,
    pub provider_admin_token: String,
}

#[derive(Debug, Clone)]
pub struct CreateOrganizationCommand {
    pub auth: OrganizationAuth,
    pub organization_code: String,
    pub name: String,
    pub description: Option<String>,
}

#[derive(Debug, Clone)]
pub struct UpdateOrganizationCommand {
    pub auth: OrganizationAuth,
    pub organization_code: String,
    pub name: Option<String>,
    pub description: Option<Option<String>>,
    pub disabled: Option<bool>,
}

#[derive(Debug, Clone)]
pub struct PutOrganizationMemberCommand {
    pub auth: OrganizationAuth,
    pub organization_code: String,
    pub bot_uuid: String,
    pub role: Option<String>,
}

#[async_trait]
pub trait OrganizationManagementService: Send + Sync {
    async fn create(&self, command: CreateOrganizationCommand) -> ServiceResult<Organization>;
    async fn get(&self, auth: OrganizationAuth, code: &str) -> ServiceResult<Organization>;
    async fn list(
        &self,
        auth: OrganizationAuth,
        include_disabled: bool,
    ) -> ServiceResult<Vec<Organization>>;
    async fn update(&self, command: UpdateOrganizationCommand) -> ServiceResult<Organization>;
    async fn put_member(
        &self,
        command: PutOrganizationMemberCommand,
    ) -> ServiceResult<OrganizationMember>;
    async fn delete_member(
        &self,
        auth: OrganizationAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<()>;
    async fn get_member(
        &self,
        auth: OrganizationAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>>;
    async fn list_members(
        &self,
        auth: OrganizationAuth,
        organization_code: &str,
        include_disabled: bool,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>>;
    async fn candidate_bots(
        &self,
        auth: OrganizationAuth,
        query: OrganizationCandidateQuery,
    ) -> ServiceResult<Vec<OrganizationCandidateBot>>;
}
