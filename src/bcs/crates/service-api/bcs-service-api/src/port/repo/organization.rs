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

#[derive(Debug, Clone)]
pub struct ListOrganizationMembersPageQuery {
    pub env: String,
    pub organization_code: String,
    pub include_disabled: bool,
    pub role: Option<String>,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone)]
pub struct OrganizationMemberPage {
    pub members: Vec<OrganizationMember>,
    pub total: u64,
    pub offset: u64,
    pub limit: u64,
}

#[derive(Debug, Clone)]
pub struct OrganizationMemberStatus {
    pub bot_uuid: String,
    pub disabled: bool,
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
    async fn get_member_statuses(
        &self,
        env: &str,
        organization_code: &str,
        first_bot_uuid: &str,
        second_bot_uuid: &str,
    ) -> ServiceResult<Vec<OrganizationMemberStatus>>;
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
    async fn list_members_page(
        &self,
        query: ListOrganizationMembersPageQuery,
    ) -> ServiceResult<OrganizationMemberPage> {
        let members = self
            .list_members(ListOrganizationMembersQuery {
                env: query.env,
                organization_code: query.organization_code,
                include_disabled: query.include_disabled,
                role: query.role,
            })
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
}
