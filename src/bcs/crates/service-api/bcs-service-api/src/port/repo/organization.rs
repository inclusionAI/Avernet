use async_trait::async_trait;
use bcs_domain::{Organization, OrganizationMember};

use crate::ServiceResult;

#[derive(Debug, Clone)]
pub struct CreateOrganizationRecord {
    pub env: String,
    pub code: String,
    pub name: String,
    pub description: Option<String>,
    pub managing_provider_id: String,
}

#[derive(Debug, Clone, Default)]
pub struct UpdateOrganizationRecord {
    pub env: String,
    pub code: String,
    pub name: Option<String>,
    pub description: Option<Option<String>>,
    pub disabled: Option<bool>,
}

#[derive(Debug, Clone)]
pub struct ListOrganizationsQuery {
    pub env: String,
    pub managing_provider_id: String,
    pub include_disabled: bool,
}

#[derive(Debug, Clone)]
pub struct UpsertOrganizationMemberRecord {
    pub env: String,
    pub organization_code: String,
    pub bot_uuid: String,
    pub role: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ListOrganizationMembersQuery {
    pub env: String,
    pub organization_code: String,
    pub include_disabled: bool,
    pub role: Option<String>,
}

#[async_trait]
pub trait OrganizationRepoPort: Send + Sync {
    async fn create_organization(
        &self,
        input: CreateOrganizationRecord,
    ) -> ServiceResult<Organization>;
    async fn get_organization(
        &self,
        env: &str,
        code: &str,
    ) -> ServiceResult<Option<Organization>>;
    async fn update_organization(
        &self,
        input: UpdateOrganizationRecord,
    ) -> ServiceResult<Option<Organization>>;
    async fn list_organizations(
        &self,
        query: ListOrganizationsQuery,
    ) -> ServiceResult<Vec<Organization>>;
    async fn upsert_member(
        &self,
        input: UpsertOrganizationMemberRecord,
    ) -> ServiceResult<OrganizationMember>;
    async fn get_member(
        &self,
        env: &str,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>>;
    async fn set_member_disabled(
        &self,
        env: &str,
        organization_code: &str,
        bot_uuid: &str,
        disabled: bool,
    ) -> ServiceResult<Option<OrganizationMember>>;
    async fn list_members(
        &self,
        query: ListOrganizationMembersQuery,
    ) -> ServiceResult<Vec<OrganizationMember>>;
}
