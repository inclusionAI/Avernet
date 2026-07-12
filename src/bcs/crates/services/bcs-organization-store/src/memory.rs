use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_domain::{Organization, OrganizationMember};
use bcs_service_api::port::repo::{
    CreateOrganizationRecord, ListOrganizationMembersQuery, ListOrganizationsQuery,
    OrganizationRepoPort, UpdateOrganizationRecord, UpsertOrganizationMemberRecord,
};
use bcs_service_api::{ServiceError, ServiceResult};
use tokio::sync::RwLock;

#[derive(Debug, Default)]
pub struct MemoryOrganizationRepo {
    organizations: RwLock<HashMap<(String, String), Organization>>,
    members: RwLock<HashMap<(String, String, String), OrganizationMember>>,
}

impl MemoryOrganizationRepo {
    pub fn new() -> Self {
        Self::default()
    }
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}

#[async_trait]
impl OrganizationRepoPort for MemoryOrganizationRepo {
    async fn create_organization(
        &self,
        input: CreateOrganizationRecord,
    ) -> ServiceResult<Organization> {
        let mut organizations = self.organizations.write().await;
        let key = (input.env.clone(), input.code.clone());
        if organizations.contains_key(&key) {
            return Err(ServiceError::Conflict(
                "organization code already exists".to_string(),
            ));
        }
        let now = now_millis();
        let organization = Organization {
            env: input.env,
            code: input.code,
            name: input.name,
            description: input.description,
            managing_provider_id: input.managing_provider_id,
            disabled: false,
            created_at: now,
            updated_at: now,
        };
        organizations.insert(key, organization.clone());
        Ok(organization)
    }

    async fn get_organization(
        &self,
        env: &str,
        code: &str,
    ) -> ServiceResult<Option<Organization>> {
        let organizations = self.organizations.read().await;
        Ok(organizations
            .get(&(env.to_string(), code.to_string()))
            .cloned())
    }

    async fn update_organization(
        &self,
        input: UpdateOrganizationRecord,
    ) -> ServiceResult<Option<Organization>> {
        let mut organizations = self.organizations.write().await;
        let Some(organization) = organizations.get_mut(&(input.env, input.code)) else {
            return Ok(None);
        };
        if let Some(name) = input.name {
            organization.name = name;
        }
        if let Some(description) = input.description {
            organization.description = description;
        }
        if let Some(disabled) = input.disabled {
            organization.disabled = disabled;
        }
        organization.updated_at = now_millis();
        Ok(Some(organization.clone()))
    }

    async fn list_organizations(
        &self,
        query: ListOrganizationsQuery,
    ) -> ServiceResult<Vec<Organization>> {
        let organizations = self.organizations.read().await;
        Ok(organizations
            .values()
            .filter(|organization| {
                organization.env == query.env
                    && organization.managing_provider_id == query.managing_provider_id
                    && (query.include_disabled || !organization.disabled)
            })
            .cloned()
            .collect())
    }

    async fn upsert_member(
        &self,
        input: UpsertOrganizationMemberRecord,
    ) -> ServiceResult<OrganizationMember> {
        let mut members = self.members.write().await;
        let key = (
            input.env.clone(),
            input.organization_code.clone(),
            input.bot_uuid.clone(),
        );
        let now = now_millis();
        let member = members.entry(key).or_insert_with(|| OrganizationMember {
            env: input.env,
            organization_code: input.organization_code,
            bot_uuid: input.bot_uuid,
            role: None,
            disabled: false,
            created_at: now,
            updated_at: now,
        });
        member.role = input.role;
        member.disabled = false;
        member.updated_at = now;
        Ok(member.clone())
    }

    async fn get_member(
        &self,
        env: &str,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>> {
        let members = self.members.read().await;
        Ok(members
            .get(&(
                env.to_string(),
                organization_code.to_string(),
                bot_uuid.to_string(),
            ))
            .cloned())
    }

    async fn set_member_disabled(
        &self,
        env: &str,
        organization_code: &str,
        bot_uuid: &str,
        disabled: bool,
    ) -> ServiceResult<Option<OrganizationMember>> {
        let mut members = self.members.write().await;
        let Some(member) = members.get_mut(&(
            env.to_string(),
            organization_code.to_string(),
            bot_uuid.to_string(),
        )) else {
            return Ok(None);
        };
        member.disabled = disabled;
        member.updated_at = now_millis();
        Ok(Some(member.clone()))
    }

    async fn list_members(
        &self,
        query: ListOrganizationMembersQuery,
    ) -> ServiceResult<Vec<OrganizationMember>> {
        let members = self.members.read().await;
        Ok(members
            .values()
            .filter(|member| {
                member.env == query.env
                    && member.organization_code == query.organization_code
                    && (query.include_disabled || !member.disabled)
                    && query
                        .role
                        .as_ref()
                        .map(|role| member.role.as_ref() == Some(role))
                        .unwrap_or(true)
            })
            .cloned()
            .collect())
    }
}
