use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{Organization, OrganizationMember};
use bcs_service_api::{
    CreateOrganizationCommand, OrganizationAuth, OrganizationCandidateBot,
    OrganizationCandidateQuery, OrganizationCoreService, OrganizationManagementService,
    ProviderCoreService, PutOrganizationMemberCommand, ServiceResult, UpdateOrganizationCommand,
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
        self.authenticate(&command.auth).await?;
        self.core
            .put_member(
                &command.auth.provider_id,
                &command.organization_code,
                &command.bot_uuid,
                command.role.as_deref(),
            )
            .await
    }

    async fn delete_member(
        &self,
        auth: OrganizationAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<()> {
        self.authenticate(&auth).await?;
        self.core
            .delete_member(&auth.provider_id, organization_code, bot_uuid)
            .await
    }

    async fn get_member(
        &self,
        auth: OrganizationAuth,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>> {
        self.authenticate(&auth).await?;
        self.core
            .get_member_for_manager(&auth.provider_id, organization_code, bot_uuid)
            .await
    }

    async fn list_members(
        &self,
        auth: OrganizationAuth,
        organization_code: &str,
        include_disabled: bool,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>> {
        self.authenticate(&auth).await?;
        self.core
            .list_members_for_manager(
                &auth.provider_id,
                organization_code,
                include_disabled,
                role,
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
}
