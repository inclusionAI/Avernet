use async_trait::async_trait;
use bcs_domain::{Organization, OrganizationMember};

use crate::{
    OrganizationCandidateBot, OrganizationCandidateBotPage, OrganizationCandidatePageQuery,
    OrganizationCandidateQuery, OrganizationMemberDetail, OrganizationMemberPage,
    OrganizationMemberProfile, OrganizationMemberProfilePatch, ServiceResult,
};
use crate::core::OrganizationMemberPageQuery;

#[derive(Debug, Clone)]
pub struct OrganizationAuth {
    pub provider_id: String,
    pub provider_admin_token: String,
}

#[derive(Debug, Clone)]
pub struct OrganizationMemberAuth {
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
    pub auth: OrganizationMemberAuth,
    pub organization_code: String,
    pub name: Option<String>,
    pub description: Option<Option<String>>,
    pub disabled: Option<bool>,
}

#[derive(Debug, Clone)]
pub struct PutOrganizationMemberCommand {
    pub auth: OrganizationMemberAuth,
    pub organization_code: String,
    pub bot_uuid: String,
    pub role: Option<String>,
}

#[derive(Debug, Clone)]
pub struct UpdateOrganizationMemberProfileCommand {
    pub auth: OrganizationMemberAuth,
    pub organization_code: String,
    pub bot_uuid: String,
    pub patch: OrganizationMemberProfilePatch,
}

#[async_trait]
pub trait OrganizationManagementService: Send + Sync {
    async fn create(&self, command: CreateOrganizationCommand) -> ServiceResult<Organization>;
    async fn get(
        &self,
        auth: OrganizationMemberAuth,
        code: &str,
    ) -> ServiceResult<Organization>;
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
        auth: OrganizationMemberAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<()>;
    async fn get_member(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>>;
    async fn get_member_detail(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMemberDetail>> {
        Ok(self
            .get_member(auth, organization_code, bot_uuid)
            .await?
            .map(|member| OrganizationMemberDetail { member, bot: None }))
    }

    /// Authorize an organization owner to invoke an active, effective member.
    async fn require_invocable_member(
        &self,
        auth: OrganizationAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<OrganizationMember>;
    async fn list_members(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        include_disabled: bool,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>>;
    async fn list_members_page(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        query: OrganizationMemberPageQuery,
    ) -> ServiceResult<OrganizationMemberPage> {
        let members = self
            .list_members(
                auth,
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
        command: UpdateOrganizationMemberProfileCommand,
    ) -> ServiceResult<OrganizationMemberProfile>;
    async fn candidate_bots(
        &self,
        auth: OrganizationAuth,
        query: OrganizationCandidateQuery,
    ) -> ServiceResult<Vec<OrganizationCandidateBot>>;
    async fn candidate_bots_page(
        &self,
        auth: OrganizationAuth,
        query: OrganizationCandidatePageQuery,
    ) -> ServiceResult<OrganizationCandidateBotPage> {
        let bots = self.candidate_bots(auth, query.candidate).await?;
        let total = bots.len() as u64;
        let bots = usize::try_from(query.offset)
            .ok()
            .and_then(|offset| usize::try_from(query.limit).ok().map(|limit| (offset, limit)))
            .map(|(offset, limit)| bots.into_iter().skip(offset).take(limit).collect())
            .unwrap_or_default();
        Ok(OrganizationCandidateBotPage { bots, total, offset: query.offset, limit: query.limit })
    }
}
