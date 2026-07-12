use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{Organization, OrganizationMember, ProviderOrganizationManagementConfig};
use bcs_service_api::{
    AuthorizedOrganizationPair, BotCapabilities, BotRegistryCoreService, CreateOrganizationRecord,
    ListOrganizationMembersQuery, ListOrganizationsQuery, OrganizationCandidateBot,
    OrganizationCandidateQuery, OrganizationCoreService, OrganizationRepoPort,
    ProviderBotBindingRepoPort, ProviderRecord, ProviderRepoPort, ServiceError, ServiceResult,
    UpdateOrganizationRecord, UpsertOrganizationMemberRecord,
};

#[derive(Clone)]
pub struct OrganizationCore {
    env: String,
    organizations: Arc<dyn OrganizationRepoPort>,
    providers: Arc<dyn ProviderRepoPort>,
    provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
    registry: Arc<dyn BotRegistryCoreService>,
}

impl OrganizationCore {
    pub fn new(
        env: String,
        organizations: Arc<dyn OrganizationRepoPort>,
        providers: Arc<dyn ProviderRepoPort>,
        provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
        registry: Arc<dyn BotRegistryCoreService>,
    ) -> Self {
        Self {
            env,
            organizations,
            providers,
            provider_bindings,
            registry,
        }
    }

    async fn require_managed_organization(
        &self,
        manager: &str,
        code: &str,
    ) -> ServiceResult<Organization> {
        validate_external_id("organization_code", code)?;
        let organization = self
            .organizations
            .get_organization(&self.env, code)
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("organization '{}' not found", code),
                request_id: None,
            })?;
        if organization.managing_provider_id != manager {
            return Err(ServiceError::Forbidden(
                "organization_manager_required".to_string(),
            ));
        }
        Ok(organization)
    }

    async fn require_member_authorized(
        &self,
        manager: &str,
        code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<()> {
        let organization = self.require_managed_organization(manager, code).await?;
        if organization.disabled {
            return Err(ServiceError::Forbidden("organization_disabled".to_string()));
        }
        let _bot = self
            .registry
            .get(bot_uuid)
            .await
            .ok_or_else(|| ServiceError::BotNotFound(bot_uuid.to_string()))?;
        let binding = self
            .provider_bindings
            .get_binding_by_bot_uuid(bot_uuid)
            .await?
            .ok_or_else(|| {
                ServiceError::Forbidden("provider_managed_bot_required".to_string())
            })?;
        if binding.disabled {
            return Err(ServiceError::Forbidden("provider_bot_disabled".to_string()));
        }
        if binding.provider_id != manager
            && !self
                .provider_grants_manager(&binding.provider_id, manager)
                .await?
        {
            return Err(ServiceError::Forbidden(
                "organization_manager_not_authorized".to_string(),
            ));
        }
        Ok(())
    }

    async fn provider_grants_manager(
        &self,
        resource_provider_id: &str,
        manager_provider_id: &str,
    ) -> ServiceResult<bool> {
        if resource_provider_id == manager_provider_id {
            return Ok(true);
        }
        let Some(resource) = self.providers.get_provider(resource_provider_id).await? else {
            return Ok(false);
        };
        let Some(manager) = self.providers.get_provider(manager_provider_id).await? else {
            return Ok(false);
        };
        if resource.disabled || manager.disabled {
            return Ok(false);
        }
        let Ok(config) = ProviderOrganizationManagementConfig::from_provider_config(
            &resource.config,
        ) else {
            return Ok(false);
        };
        Ok(config
            .authorized_manager_provider_ids
            .iter()
            .any(|provider_id| provider_id == manager_provider_id))
    }



    async fn require_organization_for_runtime(
        &self,
        code: &str,
    ) -> ServiceResult<Organization> {
        validate_external_id("organization_code", code)?;
        let organization = self
            .organizations
            .get_organization(&self.env, code)
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("organization '{}' not found", code),
                request_id: None,
            })?;
        if organization.disabled {
            return Err(ServiceError::Forbidden("organization_disabled".to_string()));
        }
        Ok(organization)
    }

    async fn effective_member_in(
        &self,
        organization: &Organization,
        bot_uuid: &str,
    ) -> ServiceResult<OrganizationMember> {
        validate_external_id("bot_uuid", bot_uuid)?;
        let member = self
            .organizations
            .get_member(&self.env, &organization.code, bot_uuid)
            .await?
            .ok_or_else(|| ServiceError::Forbidden("organization_member_required".to_string()))?;
        self.ensure_member_effective(organization, member).await
    }

    async fn ensure_member_effective(
        &self,
        organization: &Organization,
        member: OrganizationMember,
    ) -> ServiceResult<OrganizationMember> {
        let manager_provider = self.manager_provider(organization).await?;
        self.ensure_member_effective_with(organization, member, &manager_provider)
            .await
    }

    async fn manager_provider(
        &self,
        organization: &Organization,
    ) -> ServiceResult<ProviderRecord> {
        self.providers
            .get_provider(&organization.managing_provider_id)
            .await?
            .ok_or_else(|| ServiceError::Forbidden("organization_provider_grant_required".to_string()))
    }

    async fn ensure_member_effective_with(
        &self,
        organization: &Organization,
        member: OrganizationMember,
        manager_provider: &ProviderRecord,
    ) -> ServiceResult<OrganizationMember> {
        if member.disabled {
            return Err(ServiceError::Forbidden("organization_member_disabled".to_string()));
        }
        let binding = self
            .provider_bindings
            .get_binding_by_bot_uuid(&member.bot_uuid)
            .await?
            .ok_or_else(|| ServiceError::Forbidden("provider_managed_bot_required".to_string()))?;
        if binding.disabled {
            return Err(ServiceError::Forbidden("provider_bot_disabled".to_string()));
        }
        let Some(resource_provider) = self.providers.get_provider(&binding.provider_id).await? else {
            return Err(ServiceError::Forbidden("organization_provider_grant_required".to_string()));
        };
        if resource_provider.disabled || manager_provider.disabled {
            return Err(ServiceError::Forbidden("organization_provider_grant_required".to_string()));
        }
        let config = ProviderOrganizationManagementConfig::from_provider_config(&resource_provider.config)
            .map_err(|_| ServiceError::Forbidden("organization_provider_grant_required".to_string()))?;
        if provider_scope_allows(
            &organization.managing_provider_id,
            &binding.provider_id,
            &config.authorized_manager_provider_ids,
        ) {
            Ok(member)
        } else {
            Err(ServiceError::Forbidden("organization_provider_grant_required".to_string()))
        }
    }

    async fn member_is_effective_with(
        &self,
        organization: &Organization,
        member: OrganizationMember,
        manager_provider: &ProviderRecord,
    ) -> ServiceResult<Option<OrganizationMember>> {
        match self
            .ensure_member_effective_with(organization, member, manager_provider)
            .await
        {
            Ok(member) => Ok(Some(member)),
            Err(ServiceError::Forbidden(_)) | Err(ServiceError::BotNotFound(_)) => Ok(None),
            Err(err) => Err(err),
        }
    }

    async fn allowed_provider_ids(&self, manager: &str) -> ServiceResult<HashSet<String>> {
        let providers = self.providers.list_providers().await?;
        let manager_is_active = providers
            .iter()
            .any(|provider| provider.provider_id == manager && !provider.disabled);
        if !manager_is_active {
            return Ok(HashSet::new());
        }
        let mut allowed = HashSet::from([manager.to_string()]);
        for provider in providers {
            if provider.disabled || provider.provider_id == manager {
                continue;
            }
            let Ok(config) =
                ProviderOrganizationManagementConfig::from_provider_config(&provider.config)
            else {
                continue;
            };
            if config
                .authorized_manager_provider_ids
                .iter()
                .any(|provider_id| provider_id == manager)
            {
                allowed.insert(provider.provider_id);
            }
        }
        Ok(allowed)
    }
}

#[async_trait]
impl OrganizationCoreService for OrganizationCore {
    async fn create(
        &self,
        managing_provider_id: &str,
        code: &str,
        name: &str,
        description: Option<&str>,
    ) -> ServiceResult<Organization> {
        validate_external_id("organization_code", code)?;
        validate_required_text("name", name, 256)?;
        self.organizations
            .create_organization(CreateOrganizationRecord {
                env: self.env.clone(),
                code: code.to_string(),
                name: name.trim().to_string(),
                description: description.map(str::to_string),
                managing_provider_id: managing_provider_id.to_string(),
            })
            .await
    }

    async fn get_for_manager(
        &self,
        managing_provider_id: &str,
        code: &str,
    ) -> ServiceResult<Organization> {
        self.require_managed_organization(managing_provider_id, code)
            .await
    }

    async fn list_for_manager(
        &self,
        managing_provider_id: &str,
        include_disabled: bool,
    ) -> ServiceResult<Vec<Organization>> {
        self.organizations
            .list_organizations(ListOrganizationsQuery {
                env: self.env.clone(),
                managing_provider_id: managing_provider_id.to_string(),
                include_disabled,
            })
            .await
    }

    async fn update_for_manager(
        &self,
        managing_provider_id: &str,
        code: &str,
        name: Option<&str>,
        description: Option<Option<&str>>,
        disabled: Option<bool>,
    ) -> ServiceResult<Organization> {
        if name.is_none() && description.is_none() && disabled.is_none() {
            return Err(ServiceError::InvalidOperation {
                message: "no organization fields to update".to_string(),
                request_id: None,
            });
        }
        if let Some(name) = name {
            validate_required_text("name", name, 256)?;
        }
        self.require_managed_organization(managing_provider_id, code)
            .await?;
        self.organizations
            .update_organization(UpdateOrganizationRecord {
                env: self.env.clone(),
                code: code.to_string(),
                name: name.map(|value| value.trim().to_string()),
                description: description
                    .map(|value| value.map(str::to_string)),
                disabled,
            })
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("organization '{}' not found", code),
                request_id: None,
            })
    }

    async fn put_member(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        bot_uuid: &str,
        role: Option<&str>,
    ) -> ServiceResult<OrganizationMember> {
        validate_external_id("bot_uuid", bot_uuid)?;
        if let Some(role) = role {
            validate_external_id("role", role)?;
        }
        self.require_member_authorized(managing_provider_id, organization_code, bot_uuid)
            .await?;
        self.organizations
            .upsert_member(UpsertOrganizationMemberRecord {
                env: self.env.clone(),
                organization_code: organization_code.to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: role.map(str::to_string),
            })
            .await
    }

    async fn delete_member(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<()> {
        validate_external_id("bot_uuid", bot_uuid)?;
        let organization = self
            .require_managed_organization(managing_provider_id, organization_code)
            .await?;
        if organization.disabled {
            return Err(ServiceError::Forbidden("organization_disabled".to_string()));
        }
        self.organizations
            .set_member_disabled(&self.env, organization_code, bot_uuid, true)
            .await?;
        Ok(())
    }

    async fn get_member_for_manager(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Option<OrganizationMember>> {
        validate_external_id("bot_uuid", bot_uuid)?;
        self.require_managed_organization(managing_provider_id, organization_code)
            .await?;
        self.organizations
            .get_member(&self.env, organization_code, bot_uuid)
            .await
    }

    async fn list_members_for_manager(
        &self,
        managing_provider_id: &str,
        organization_code: &str,
        include_disabled: bool,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>> {
        if let Some(role) = role {
            validate_external_id("role", role)?;
        }
        self.require_managed_organization(managing_provider_id, organization_code)
            .await?;
        self.organizations
            .list_members(ListOrganizationMembersQuery {
                env: self.env.clone(),
                organization_code: organization_code.to_string(),
                include_disabled,
                role: role.map(str::to_string),
            })
            .await
    }



    async fn require_effective_member(
        &self,
        organization_code: &str,
        bot_uuid: &str,
    ) -> ServiceResult<OrganizationMember> {
        let organization = self.require_organization_for_runtime(organization_code).await?;
        self.effective_member_in(&organization, bot_uuid).await
    }

    async fn list_effective_members(
        &self,
        organization_code: &str,
        role: Option<&str>,
    ) -> ServiceResult<Vec<OrganizationMember>> {
        if let Some(role) = role {
            validate_external_id("role", role)?;
        }
        let organization = self.require_organization_for_runtime(organization_code).await?;
        let members = self
            .organizations
            .list_members(ListOrganizationMembersQuery {
                env: self.env.clone(),
                organization_code: organization.code.clone(),
                include_disabled: false,
                role: role.map(str::to_string),
            })
            .await?;
        let manager_provider = self.manager_provider(&organization).await?;
        let mut effective = Vec::new();
        for member in members {
            if let Some(member) = self
                .member_is_effective_with(&organization, member, &manager_provider)
                .await?
            {
                effective.push(member);
            }
        }
        effective.sort_by(|left, right| left.bot_uuid.cmp(&right.bot_uuid));
        Ok(effective)
    }

    async fn authorize_pair(
        &self,
        organization_code: &str,
        sender_bot_uuid: &str,
        target_bot_uuid: &str,
    ) -> ServiceResult<AuthorizedOrganizationPair> {
        let organization = self.require_organization_for_runtime(organization_code).await?;
        let sender = self.effective_member_in(&organization, sender_bot_uuid).await?;
        let target = self.effective_member_in(&organization, target_bot_uuid).await?;
        Ok(AuthorizedOrganizationPair {
            organization,
            sender,
            target,
        })
    }

    async fn candidate_bots(
        &self,
        managing_provider_id: &str,
        query: OrganizationCandidateQuery,
    ) -> ServiceResult<Vec<OrganizationCandidateBot>> {
        let allowed = self.allowed_provider_ids(managing_provider_id).await?;
        if allowed.is_empty() {
            return Ok(Vec::new());
        }

        let mut bindings = Vec::new();
        for provider_id in &allowed {
            bindings.extend(
                self.provider_bindings
                    .list_bindings_by_provider(provider_id)
                    .await?
                    .into_iter()
                    .filter(|binding| !binding.disabled),
            );
        }
        let bot_ids = bindings
            .iter()
            .map(|binding| binding.bot_uuid.clone())
            .collect::<Vec<_>>();
        let bots = self
            .registry
            .get_by_ids(&bot_ids)
            .await
            .into_iter()
            .map(|bot| (bot.bot_uuid.clone(), bot))
            .collect::<HashMap<_, _>>();

        let mut candidates = Vec::new();
        for binding in bindings {
            if !allowed.contains(&binding.provider_id) {
                continue;
            }
            let Some(bot) = bots.get(&binding.bot_uuid) else {
                continue;
            };
            if !matches_query(&bot.bot_uuid, &bot.capabilities, &query) {
                continue;
            }
            candidates.push(OrganizationCandidateBot {
                bot_uuid: bot.bot_uuid.clone(),
                provider_id: binding.provider_id,
                capabilities: bot.capabilities.clone(),
            });
        }
        candidates.sort_by(|left, right| left.bot_uuid.cmp(&right.bot_uuid));
        candidates.dedup_by(|left, right| left.bot_uuid == right.bot_uuid);
        Ok(candidates)
    }
}


fn provider_scope_allows(
    managing_provider_id: &str,
    resource_provider_id: &str,
    authorized_manager_provider_ids: &[String],
) -> bool {
    resource_provider_id == managing_provider_id
        || authorized_manager_provider_ids
            .iter()
            .any(|provider_id| provider_id == managing_provider_id)
}

fn validate_required_text(kind: &str, value: &str, max_len: usize) -> ServiceResult<()> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(ServiceError::InvalidOperation {
            message: format!("{kind} is required"),
            request_id: None,
        });
    }
    if trimmed.len() > max_len {
        return Err(ServiceError::InvalidOperation {
            message: format!("{kind} cannot exceed {max_len} characters"),
            request_id: None,
        });
    }
    Ok(())
}

fn validate_external_id(kind: &str, value: &str) -> ServiceResult<()> {
    let valid = !value.is_empty()
        && value.len() <= 256
        && value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.' | ':'));
    if valid {
        Ok(())
    } else {
        Err(ServiceError::InvalidOperation {
            message: format!("invalid {kind}: '{value}'"),
            request_id: None,
        })
    }
}

fn matches_query(
    bot_uuid: &str,
    capabilities: &BotCapabilities,
    query: &OrganizationCandidateQuery,
) -> bool {
    query
        .q
        .as_deref()
        .map(|q| contains_any_text(bot_uuid, capabilities, q))
        .unwrap_or(true)
        && query
            .domains
            .as_deref()
            .map(|domains| all_terms_match(domains, &capabilities.domains))
            .unwrap_or(true)
        && query
            .skills
            .as_deref()
            .map(|skills| {
                let names = capabilities
                    .skills
                    .iter()
                    .map(|skill| skill.name.as_str())
                    .collect::<Vec<_>>();
                all_terms_match(skills, &names)
            })
            .unwrap_or(true)
        && query
            .scopes
            .as_deref()
            .map(|scopes| all_terms_match(scopes, &capabilities.scopes))
            .unwrap_or(true)
}

fn contains_any_text(bot_uuid: &str, capabilities: &BotCapabilities, query: &str) -> bool {
    let query = query.trim().to_ascii_lowercase();
    if query.is_empty() {
        return true;
    }
    bot_uuid.to_ascii_lowercase().contains(&query)
        || capabilities
            .name
            .as_deref()
            .is_some_and(|name| name.to_ascii_lowercase().contains(&query))
        || capabilities
            .summary
            .as_deref()
            .is_some_and(|summary| summary.to_ascii_lowercase().contains(&query))
        || capabilities
            .domains
            .iter()
            .any(|domain| domain.to_ascii_lowercase().contains(&query))
        || capabilities
            .skills
            .iter()
            .any(|skill| skill.name.to_ascii_lowercase().contains(&query))
        || capabilities
            .scopes
            .iter()
            .any(|scope| scope.to_ascii_lowercase().contains(&query))
}

fn all_terms_match<T>(raw_terms: &str, values: &[T]) -> bool
where
    T: AsRef<str>,
{
    split_terms(raw_terms).into_iter().all(|term| {
        values
            .iter()
            .any(|value| value.as_ref().to_ascii_lowercase().contains(&term))
    })
}

fn split_terms(raw_terms: &str) -> Vec<String> {
    raw_terms
        .split(',')
        .map(str::trim)
        .filter(|term| !term.is_empty())
        .map(str::to_ascii_lowercase)
        .collect()
}
