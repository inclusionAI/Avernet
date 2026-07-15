use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{Organization, OrganizationMember};
use bcs_service_api::{
    CreateOrganizationCommand, OrganizationAuth, OrganizationCandidateBot, OrganizationCandidateBotPage,
    OrganizationCandidatePageQuery, OrganizationCandidateQuery, OrganizationCoreService, OrganizationManagementService,
    OrganizationMemberAuth, OrganizationMemberDetail, OrganizationMemberPage, OrganizationMemberPageQuery,
    OrganizationMemberProfile, ProviderCoreService,
    PutOrganizationMemberCommand, ServiceResult, UpdateOrganizationCommand,
    UpdateOrganizationMemberProfileCommand,
};

#[derive(Clone)]
pub struct OrganizationManagement {
    providers: Arc<dyn ProviderCoreService>,
    core: Arc<dyn OrganizationCoreService>,
}

impl OrganizationManagement {
    pub fn new(
        providers: Arc<dyn ProviderCoreService>,
        core: Arc<dyn OrganizationCoreService>,
    ) -> Self {
        Self { providers, core }
    }

    async fn authenticate(&self, auth: &OrganizationAuth) -> ServiceResult<()> {
        self.providers
            .get_provider(&auth.provider_id, &auth.provider_admin_token)
            .await?;
        Ok(())
    }

    async fn authenticate_member(&self, auth: &OrganizationMemberAuth) -> ServiceResult<String> {
        let provider = self
            .providers
            .authenticate_provider_admin(&auth.provider_admin_token)
            .await?;
        if provider.disabled {
            return Err(bcs_service_api::ServiceError::Forbidden(
                "organization_manager_disabled".to_string(),
            ));
        }
        Ok(provider.provider_id)
    }
}

#[async_trait]
impl OrganizationManagementService for OrganizationManagement {
    async fn create(&self, command: CreateOrganizationCommand) -> ServiceResult<Organization> {
        self.authenticate(&command.auth).await?;
        self.core
            .create(
                &command.auth.provider_id,
                &command.organization_code,
                &command.name,
                command.description.as_deref(),
            )
            .await
    }

    async fn get(&self, auth: OrganizationAuth, code: &str) -> ServiceResult<Organization> {
        self.authenticate(&auth).await?;
        self.core.get_for_manager(&auth.provider_id, code).await
    }

    async fn list(
        &self,
        auth: OrganizationAuth,
        include_disabled: bool,
    ) -> ServiceResult<Vec<Organization>> {
        self.authenticate(&auth).await?;
        self.core
            .list_for_manager(&auth.provider_id, include_disabled)
            .await
    }

    async fn update(&self, command: UpdateOrganizationCommand) -> ServiceResult<Organization> {
        self.authenticate(&command.auth).await?;
        self.core
            .update_for_manager(
                &command.auth.provider_id,
                &command.organization_code,
                command.name.as_deref(),
                command
                    .description
                    .as_ref()
                    .map(|description| description.as_deref()),
                command.disabled,
            )
            .await
    }

    async fn put_member(
        &self,
        command: PutOrganizationMemberCommand,
    ) -> ServiceResult<OrganizationMember> {
        let provider_id = self.authenticate_member(&command.auth).await?;
        self.core
            .put_member(
                &provider_id,
                &command.organization_code,
                &command.bot_uuid,
                command.role.as_deref(),
            )
            .await
    }

    async fn delete_member(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<()> {
        let provider_id = self.authenticate_member(&auth).await?;
        self.core
            .delete_member(&provider_id, organization_code, bot_uuid)
            .await
    }

    async fn get_member(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>> {
        let provider_id = self.authenticate_member(&auth).await?;
        self.core
            .get_member_for_manager(&provider_id, organization_code, bot_uuid)
            .await
    }

    async fn get_member_detail(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMemberDetail>> {
        let provider_id = self.authenticate_member(&auth).await?;
        self.core
            .get_member_detail_for_manager(&provider_id, organization_code, bot_uuid)
            .await
    }

    async fn require_invocable_member(
        &self,
        auth: OrganizationAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<OrganizationMember> {
        self.authenticate(&auth).await?;
        let organization = self
            .core
            .get_for_manager(&auth.provider_id, organization_code)
            .await?;
        if organization.managing_provider_id != auth.provider_id {
            return Err(bcs_service_api::ServiceError::Forbidden(
                "organization_manager_required".to_string(),
            ));
        }
        self.core
            .require_effective_member(organization_code, bot_uuid)
            .await
    }

    async fn list_members(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        include_disabled: bool,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>> {
        let provider_id = self.authenticate_member(&auth).await?;
        self.core
            .list_members_for_manager(
                &provider_id,
                organization_code,
                include_disabled,
                role,
            )
            .await
    }

    async fn list_members_page(
        &self,
        auth: OrganizationMemberAuth,
        organization_code: &str,
        query: OrganizationMemberPageQuery,
    ) -> ServiceResult<OrganizationMemberPage> {
        let provider_id = self.authenticate_member(&auth).await?;
        self.core
            .list_members_page_for_manager(&provider_id, organization_code, query)
            .await
    }

    async fn update_member_profile(
        &self,
        command: UpdateOrganizationMemberProfileCommand,
    ) -> ServiceResult<OrganizationMemberProfile> {
        let provider_id = self.authenticate_member(&command.auth).await?;
        self.core
            .update_member_profile(
                &provider_id,
                &command.organization_code,
                &command.bot_uuid,
                command.patch,
            )
            .await
    }

    async fn candidate_bots(
        &self,
        auth: OrganizationAuth,
        query: OrganizationCandidateQuery,
    ) -> ServiceResult<Vec<OrganizationCandidateBot>> {
        self.authenticate(&auth).await?;
        self.core.candidate_bots(&auth.provider_id, query).await
    }

    async fn candidate_bots_page(
        &self,
        auth: OrganizationAuth,
        query: OrganizationCandidatePageQuery,
    ) -> ServiceResult<OrganizationCandidateBotPage> {
        self.authenticate(&auth).await?;
        self.core.candidate_bots_page(&auth.provider_id, query).await
    }
}
