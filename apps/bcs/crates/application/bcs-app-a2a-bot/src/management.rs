//! Token-scoped Card management by stable Bot ID.
use super::*;
use serde_json::Value;
use bcs_service_api::application::a2a_bot::{ManagedA2aCard, RegisterAgentCommand};
use bcs_service_api::types::a2a_bot::a2a_management::{
    self as policy, ManagementCode as Code, ManagementError,
};

fn failure(code: Code, field: &str, reason: &'static str) -> ServiceError {
    ManagementError::new(code, field, reason).into()
}
fn view(provider: &str, record: A2aBotRecord) -> ServiceResult<ManagedA2aCard> {
    Ok(ManagedA2aCard {
        catalog_labels: record.catalog_labels,
        provider_name: provider.into(),
        agent_name: bcs_service_api::types::a2a_bot::card_bot_name(&record.agent_card)
            .map_err(|_| failure(Code::InvalidCard, "agent_card.name", "INVALID_NAME"))?
            .into(),
        bot_id: record.bot_uuid,
        provider_bot_ref: record.provider_bot_ref,
        agent_card_url: record.agent_card_url,
        agent_card: record.agent_card,
        agent_id: record.agent_id,
        visibility: record.visibility,
        status: record.status,
        user_visibility: record.user_visibility,
    })
}

fn identity_conflict() -> ServiceError {
    ServiceError::A2aManagement(ManagementError::new(
        Code::AgentIdentityConflict, "agent_id", "IDENTITY_CONFLICT"))
}

/// Resolve a Card PUT AgentId against the latest dedicated column. Omission
/// preserves the stored value; null and an empty value preserve an empty
/// representation but cannot clear a nonempty identity.
fn resolve_update_identity(
    declaration: &bcs_service_api::types::a2a_bot::A2aAgentIdentity,
    current: &Option<String>,
) -> ServiceResult<Option<String>> {
    use bcs_service_api::types::a2a_bot::A2aAgentIdentity;
    let empty = current.as_deref().unwrap_or("").is_empty();
    match declaration {
        A2aAgentIdentity::Omitted => Ok(current.clone()),
        A2aAgentIdentity::Null if empty => Ok(None),
        A2aAgentIdentity::Text(value) if empty => Ok(Some(value.clone())),
        A2aAgentIdentity::Text(value)
            if current.as_deref().is_some_and(|current| current == value.as_str()) =>
        {
            Ok(current.clone())
        }
        _ => Err(identity_conflict()),
    }
}
impl A2aBotManagement {
    async fn authenticated_public_provider(&self, token: &str) -> ServiceResult<(String, String, String)> {
        let provider = self.providers.authenticate_provider_admin(token).await?;
        if provider.disabled {
            return Err(failure(Code::PermissionDenied, "", "PROVIDER_DISABLED"));
        }
        let name = provider
            .slug
            .filter(|s| bcs_service_api::types::a2a_bot::valid_provider_name(s))
            .ok_or_else(|| failure(Code::ServiceUnavailable, "", "PROVIDER_NAME_NOT_CONFIGURED"))?;
        Ok((provider.provider_id, name, provider.config))
    }
    async fn public_record_by_id(&self, provider: &str, bot_id: &str) -> ServiceResult<A2aBotRecord> {
        if bot_id.is_empty() || bot_id != bot_id.trim() || bot_id.chars().any(char::is_control) {
            return Err(failure(Code::InvalidRequest, "bot_id", "INVALID_BOT_ID"));
        }
        let record = self.core.get_card(bot_id).await?
            .filter(|record| record.provider_id == provider)
            .ok_or_else(|| failure(Code::BotNotFound, "bot_id", "NOT_FOUND"))?;
        let binding = self.core.provider_binding(provider, bot_id).await.map_err(|error| match error {
            ServiceError::Forbidden(_) | ServiceError::BotNotFound(_) | ServiceError::BotNotRegistered(_) =>
                failure(Code::BotNotFound, "bot_id", "NOT_FOUND"),
            other => other,
        })?;
        if binding.provider_bot_ref != record.provider_bot_ref {
            return Err(failure(Code::TargetChanged, "bot_id", "TARGET_CHANGED"));
        }
        Ok(record)
    }
    async fn public_load(
        &self,
        source: A2aCardSource,
    ) -> ServiceResult<(
        Option<String>,
        bcs_service_api::port::a2a_card::ValidatedA2aCard,
    )> {
        let (url, card) = match source {
            A2aCardSource::Inline(raw) => {
                policy::validate_management_card(&raw)?;
                (None, self.fetcher.validate(raw, false).await?)
            }
            A2aCardSource::Url(url) => {
                policy::validate_card_url(&url)?;
                let card = self.fetcher.fetch(&url, false).await?;
                policy::validate_management_card(&card.raw)?;
                (Some(url), card)
            }
        };
        bcs_service_api::types::a2a_card_projection::project_agent_card_capabilities(Some(&card.raw))?;
        Ok((url, card))
    }
    pub(super) async fn public_register(
        &self,
        command: RegisterAgentCommand,
    ) -> ServiceResult<ManagedA2aCard> {
        let (provider_id, provider_name, provider_config) =
            self.authenticated_public_provider(&command.provider_admin_token).await?;
        let labels=self.policy.normalize(command.catalog_labels)?;
        if command.now_ms < 0 {
            return Err(failure(Code::InvalidRequest, "", "INVALID_REGISTRATION_TIME"));
        }
        if self.registration.is_none() {
            return Err(failure(Code::ServiceUnavailable, "", "REGISTRATION_NOT_CONFIGURED"));
        }
        let provider = self
            .providers
            .get_provider_by_slug(&provider_name)
            .await?
            .ok_or_else(|| failure(Code::ServiceUnavailable, "", "PROVIDER_UNAVAILABLE"))?;
        if provider.auth_mode != bcs_service_api::ProviderAuthMode::StaticBearer {
            return Err(failure(
                Code::PermissionDenied,
                "",
                "IDENTITY_AWARE_ADMISSION_REQUIRED",
            ));
        }
        let owners = match command.owners.filter(|owners| !owners.is_empty()) {
            Some(owners) => owners,
            None => {
                let config: Value = serde_json::from_str(&provider_config)
                    .map_err(|_| failure(Code::InvalidOwner, "owners", "DEFAULT_OWNER_CONFIG_INVALID"))?;
                let owner = config.get("a2a").and_then(|a2a| a2a.get("default_owner"))
                    .and_then(Value::as_str).map(str::to_owned)
                    .ok_or_else(|| failure(Code::InvalidOwner, "owners", "DEFAULT_OWNER_NOT_CONFIGURED"))?;
                vec![owner]
            }
        };
        if owners.len() != 1 || !bcs_service_api::valid_default_owner(&owners[0])
        { return Err(failure(Code::InvalidOwner, "owners", "ONE_OWNER_REQUIRED")); }
        policy::validate_provider_bot_ref(&command.provider_bot_ref)?;
        let (url, card) = self.public_load(command.source).await?;
        policy::validate_summary(command.summary.as_deref(), &card.raw)?;
        self.authorize(&provider_id, &command.provider_admin_token).await?;
        self.providers.pin_a2a_provider_name(&provider_id, &provider_name).await?;
        let outcome = self
            .register_loaded_with_visibility(
                bcs_service_api::RegisterProviderBotCommand {
                    avatar: bcs_service_api::types::bot_profile::AvatarChange::Keep,
                    provider_id,
                    provider_admin_token: command.provider_admin_token,
                    name: String::new(),
                    provider_bot_ref: command.provider_bot_ref,
                    owners,
                    summary: command.summary,
                    webhook_url: None,
                    domains: vec![],
                    skills: vec![],
                    scopes: vec![],
                    bot_uuid: None,
                    reject_existing_bot_uuid: true,
                    connection_mode: bcs_service_api::ProviderBotConnectionMode::Gateway,
                },
                url,
                card,
                command.now_ms,
                command.visibility,
                command.agent_id,
                labels,
            )
            .await?;
        let record = self
            .core
            .get_card(&outcome.bot_uuid)
            .await?
            .ok_or_else(|| failure(Code::InternalError, "", "REGISTERED_CARD_MISSING"))?;
        view(&provider_name, record)
    }
    pub(super) async fn public_find(&self, token:&str, provider_bot_ref:&str) -> ServiceResult<Vec<ManagedA2aCard>> {
        let (provider,label,_)=self.authenticated_public_provider(token).await?;
        policy::validate_provider_bot_ref(provider_bot_ref)?;
        let Some(record)=self.core.get_by_provider_ref(&provider,provider_bot_ref).await? else {return Ok(vec![])};
        let record=self.public_record_by_id(&provider,&record.bot_uuid).await?;
        // Re-read may race a deletion/reassignment; never return an unrelated source identity.
        if record.provider_bot_ref!=provider_bot_ref {
            return Err(failure(Code::TargetChanged,"provider_bot_ref","TARGET_CHANGED"));
        }
        let result=view(&label,record)?;
        self.providers.pin_a2a_provider_name(&provider, &label).await?;
        Ok(vec![result])
    }
    pub(super) async fn public_get(
        &self,
        token: &str,
        bot_id: &str,
    ) -> ServiceResult<ManagedA2aCard> {
        let (provider, label, _) = self.authenticated_public_provider(token).await?;
        let result=view(&label, self.public_record_by_id(&provider, bot_id).await?)?;
        self.providers.pin_a2a_provider_name(&provider, &label).await?;
        Ok(result)
    }
    pub(super) async fn public_update(
        &self,
        token: &str,
        bot_id: &str,
        source: Option<A2aCardSource>,
        visibility: Option<bcs_service_api::types::a2a_bot::A2aRegistrationVisibility>,
        agent_id: bcs_service_api::types::a2a_bot::A2aAgentIdentity,
        labels: Option<bcs_service_api::types::catalog_labels::CatalogLabelsInput>,
    ) -> ServiceResult<ManagedA2aCard> {
        let (provider, label, _) = self.authenticated_public_provider(token).await?;
        if source.is_none() && visibility.is_none() && labels.is_none() && agent_id==bcs_service_api::types::a2a_bot::A2aAgentIdentity::Omitted {return Err(failure(Code::InvalidRequest,"","UPDATE_REQUIRED"));}
        let labels=labels.map(|input|self.policy.normalize(input)).transpose()?;
        let target = self.public_record_by_id(&provider, bot_id).await?;
        let loaded = match source {Some(source)=>Some(self.public_load(source).await?),None=>None};
        // Retry only local CAS races, never the outbound fetch. Each attempt
        // rechecks the original identity and binding, so renames cannot retarget.
        let mut provider_name_pinned = false;
        for _ in 0..3 {
            self.authorize(&provider, token).await?;
            let current = self
                .core
                .get_card(&target.bot_uuid)
                .await?
                .filter(|v| {
                    v.provider_id == provider
                        && v.provider_bot_ref == target.provider_bot_ref
                })
                .ok_or_else(|| failure(Code::TargetChanged, "bot_id", "TARGET_CHANGED"))?;
            let (url,raw)=match &loaded {Some((url,card))=>(url.clone(),card.raw.clone()),None=>(current.agent_card_url.clone(),current.agent_card.clone())};
            policy::validate_source_transition(&current.agent_card, &raw)?;
            if loaded.is_none() {
                bcs_service_api::types::a2a_card_projection::project_agent_card_capabilities(Some(&raw))?;
            }
            let revision = current
                .revision
                .checked_add(1)
                .ok_or_else(|| failure(Code::InternalError, "", "REVISION_EXHAUSTED"))?;
            // Reserve the name only after auth, label, target and Card validation.
            // Pin at most once per request; the store still fences concurrent slug changes.
            if !provider_name_pinned {
                self.providers.pin_a2a_provider_name(&provider, &label).await?;
                provider_name_pinned = true;
            }
            let next = A2aBotRecord {
                // Omission preserves this CAS attempt's latest labels, not the pre-fetch snapshot.
                catalog_labels: labels.clone().unwrap_or_else(||current.catalog_labels.clone()),
                agent_card_url: url.clone(),
                agent_card: raw,
                agent_id: resolve_update_identity(&agent_id,&current.agent_id)?,
                visibility: visibility.clone().unwrap_or(current.visibility),
                status: current.status,
                user_visibility: current.user_visibility,
                revision,
                ..current
            };
            match self.core.commit_card_with_visibility(next, revision - 1, visibility).await {
                Ok(record) => return view(&label, record),
                Err(ServiceError::Conflict(_)) => continue,
                Err(ServiceError::Forbidden(_)) => {
                    return Err(failure(Code::TargetChanged, "bot_id", "TARGET_CHANGED"));
                }
                Err(error) => return Err(error),
            }
        }
        Err(failure(
            Code::TargetChanged,
            "bot_id",
            "CONCURRENT_UPDATE",
        ))
    }
    pub(super) async fn public_update_metadata(
        &self,
        command: bcs_service_api::application::a2a_bot::UpdateAgentCardMetadataCommand,
    ) -> ServiceResult<ManagedA2aCard> {
        let (provider, label, _) = self.authenticated_public_provider(&command.provider_admin_token).await?;
        if command.catalog_labels.is_none() && command.status.is_none()
            && command.visibility.is_none() && command.user_visibility.is_none() {
            return Err(failure(Code::InvalidRequest, "", "METADATA_UPDATE_REQUIRED"));
        }
        let labels=command.catalog_labels.map(|input|self.policy.normalize(input)).transpose()?;
        for _ in 0..3 {
            self.authorize(&provider, &command.provider_admin_token).await?;
            let current=self.public_record_by_id(&provider, &command.bot_id).await?;
            let revision=current.revision.checked_add(1)
                .ok_or_else(|| failure(Code::InternalError, "", "REVISION_EXHAUSTED"))?;
            let mut next=current.clone();
            next.revision=revision;
            if let Some(labels)=&labels { next.catalog_labels=labels.clone(); }
            if let Some(visibility)=command.visibility { next.visibility=visibility.clone(); }
            if let Some(status)=command.status { next.status=status; }
            if let Some(user_visibility)=command.user_visibility { next.user_visibility=user_visibility; }
            match self.core.commit_card_with_visibility(next, revision-1, command.visibility).await {
                Ok(record) => {
                    self.providers.pin_a2a_provider_name(&provider, &label).await?;
                    return view(&label, record);
                }
                Err(ServiceError::Conflict(_)) => continue,
                Err(ServiceError::Forbidden(_)) => {
                    return Err(failure(Code::TargetChanged, "bot_id", "TARGET_CHANGED"));
                }
                Err(error) => return Err(error),
            }
        }
        Err(failure(Code::TargetChanged, "bot_id", "CONCURRENT_UPDATE"))
    }
    pub(super) async fn public_refresh(
        &self,
        token: &str,
        bot_id: &str,
    ) -> ServiceResult<ManagedA2aCard> {
        let (provider, label, _) = self.authenticated_public_provider(token).await?;
        let old = self.public_record_by_id(&provider, bot_id).await?;
        let url = old
            .agent_card_url
            .clone()
            .ok_or_else(|| failure(Code::CardUrlRequired, "agent_card_url", "URL_REQUIRED"))?;
        let (_, card) = self.public_load(A2aCardSource::Url(url)).await?;
        self.authorize(&provider, token).await?;
        policy::validate_source_transition(&old.agent_card, &card.raw)?;
        let revision = old
            .revision
            .checked_add(1)
            .ok_or_else(|| failure(Code::InternalError, "", "REVISION_EXHAUSTED"))?;
        self.providers.pin_a2a_provider_name(&provider, &label).await?;
        let next = A2aBotRecord {
            agent_card: card.raw,
            revision,
            ..old
        };
        let record =
            self.core
                .commit_card(next, revision - 1)
                .await
                .map_err(|error| match error {
                    ServiceError::Conflict(_) => {
                        failure(Code::RefreshSuperseded, "agent_card", "CONCURRENT_UPDATE")
                    }
                    ServiceError::Forbidden(_) => {
                        failure(Code::TargetChanged, "bot_id", "TARGET_CHANGED")
                    }
                    other => other,
                })?;
        view(&label, record)
    }
}
