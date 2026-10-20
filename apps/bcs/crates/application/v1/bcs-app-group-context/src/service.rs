use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{GroupContextEntry, GroupContextOwners, GroupContextPolicy, ScopeLevel};
use bcs_group_context_store::{GroupContextRepo, GroupContextStoreError, NewGroupContextEntry};
use bcs_service_api::application::v1::{
    AddGroupContext, ApplicationError, GetGroupContext, GroupContextAck,
    GroupContextApplicationService, GroupContextGetItem, GroupContextGetResponse,
    GroupContextListItem, GroupContextListResponse, ListGroupContexts, SystemSetGroupContext,
    UpdateGroupContext,
};
use chrono::Utc;
use ulid::Ulid;

use crate::permissions::{actor_can_read, actor_can_write, actor_is_owner, permission_for};
use crate::scope::{ResolvedScope, resolve_scope, resolve_scopes};

pub const MAX_CONTENT_BYTES: usize = 4 * 1024;
pub const MAX_NAME_CHARS: usize = 255;
pub const MAX_POLICY_BYTES: usize = 4096;
pub const MAX_OWNERS_BYTES: usize = 4096;
pub const MAX_CREATOR_CHARS: usize = 255;

const INVALID_PARAM: &str = "invalid_param";
const PERMISSION_DENIED: &str = "permission_denied";
const NOT_FOUND: &str = "not_found";
const CONFLICT: &str = "conflict";
const PAYLOAD_TOO_LARGE: &str = "payload_too_large";

const SYSTEM_CREATOR: &str = "system";

pub struct GroupContextApplicationServiceImpl {
    repo: Arc<dyn GroupContextRepo>,
}

impl GroupContextApplicationServiceImpl {
    pub fn new(repo: Arc<dyn GroupContextRepo>) -> Self {
        Self { repo }
    }

    async fn create(
        &self,
        scope: ResolvedScope,
        name: String,
        content: String,
        creator: String,
        visible_to: GroupContextPolicy,
        collect_from: GroupContextPolicy,
        owners: GroupContextOwners,
        change_reason: Option<String>,
        valid_from: Option<bcs_domain::GroupContextTime>,
        valid_to: Option<bcs_domain::GroupContextTime>,
    ) -> Result<GroupContextEntry, ApplicationError> {
        validate_name(&name)?;
        validate_content(&content)?;
        validate_policies(&visible_to, &collect_from)?;
        validate_owners(&owners)?;
        let now = Utc::now();
        self.repo
            .create_active(NewGroupContextEntry {
                context_id: new_context_id(),
                name,
                scope_level: scope.scope_level,
                scope: scope.scope,
                content,
                creator,
                visible_to,
                collect_from,
                owners,
                supersedes: None,
                change_reason,
                valid_from: valid_from.unwrap_or(now),
                valid_to,
                tx_time: now,
            })
            .await
            .map_err(map_store_error)
    }

    async fn supersede(
        &self,
        expected_active_id: i64,
        old: &GroupContextEntry,
        scope: ResolvedScope,
        name: String,
        content: String,
        creator: String,
        visible_to: GroupContextPolicy,
        collect_from: GroupContextPolicy,
        owners: GroupContextOwners,
        change_reason: Option<String>,
        valid_from: bcs_domain::GroupContextTime,
        valid_to: Option<bcs_domain::GroupContextTime>,
    ) -> Result<GroupContextEntry, ApplicationError> {
        validate_name(&name)?;
        validate_content(&content)?;
        validate_policies(&visible_to, &collect_from)?;
        validate_owners(&owners)?;
        let now = Utc::now();
        self.repo
            .supersede_active(
                &old.name,
                &old.scope,
                expected_active_id,
                NewGroupContextEntry {
                    context_id: old.context_id.clone(),
                    name,
                    scope_level: scope.scope_level,
                    scope: scope.scope,
                    content,
                    creator,
                    visible_to,
                    collect_from,
                    owners,
                    supersedes: Some(old.id),
                    change_reason,
                    valid_from,
                    valid_to,
                    tx_time: now,
                },
            )
            .await
            .map_err(map_store_error)
    }
}

#[async_trait]
impl GroupContextApplicationService for GroupContextApplicationServiceImpl {
    async fn add(&self, command: AddGroupContext) -> Result<GroupContextAck, ApplicationError> {
        let scope = resolve_scope(&command.request_context, command.scope_level)?;
        validate_actor(&command.actor_id)?;
        validate_name(&command.name)?;
        validate_content(&command.content)?;
        validate_policies(&command.visible_to, &command.collect_from)?;
        if !command.collect_from.contains_user(&command.actor_id) {
            return Err(permission_denied(format!(
                "actor_id={} is not allowed to write {} in {}",
                command.actor_id, command.name, scope.scope
            )));
        }
        self.create(
            scope,
            command.name,
            command.content,
            command.actor_id.clone(),
            command.visible_to,
            command.collect_from,
            GroupContextOwners::new([command.actor_id.clone()]),
            None,
            command.valid_from,
            command.valid_to,
        )
        .await?;
        Ok(GroupContextAck::ok())
    }

    async fn update(
        &self,
        command: UpdateGroupContext,
    ) -> Result<GroupContextAck, ApplicationError> {
        let scope = resolve_scope(&command.request_context, command.scope_level)?;
        validate_actor(&command.actor_id)?;
        validate_name(&command.name)?;
        if let Some(content) = command.content.as_ref() {
            validate_content(content)?;
        }
        if let Some(visible_to) = command.visible_to.as_ref() {
            validate_policy("visible_to", visible_to)?;
        }
        if let Some(collect_from) = command.collect_from.as_ref() {
            validate_collect_from(collect_from)?;
        }
        let old = self
            .repo
            .get_active(&command.name, &scope.scope)
            .await
            .map_err(map_store_error)?
            .ok_or_else(|| not_found(&command.name, &scope.scope))?;
        // Authorization uses only the active row read for this request. A new
        // owners list is data, never evidence for the request that carries it.
        let is_owner = actor_is_owner(&old, &command.actor_id);
        if !is_owner {
            if command.visible_to.is_some()
                || command.collect_from.is_some()
                || command.owners.is_some()
            {
                return Err(permission_denied(format!(
                    "actor_id={} is not an owner and may only update content and validity for {} in {}",
                    command.actor_id, command.name, scope.scope
                )));
            }
            if !actor_can_write(&old, &command.actor_id) {
                return Err(permission_denied(format!(
                    "actor_id={} is not allowed to update {} in {}",
                    command.actor_id, command.name, scope.scope
                )));
            }
        }
        let owners = match command.owners {
            Some(owners) => owners,
            None => old.owners.clone(),
        };
        validate_owners(&owners)?;
        let content = command.content.unwrap_or_else(|| old.content.clone());
        let visible_to = command.visible_to.unwrap_or_else(|| old.visible_to.clone());
        let collect_from = command
            .collect_from
            .unwrap_or_else(|| old.collect_from.clone());
        let valid_from = command.valid_from.unwrap_or_else(Utc::now);
        let valid_to = command.valid_to;
        // Pass old.id as the CAS token: if the row has been superseded by
        // another concurrent transaction, supersede_active will return Conflict.
        self.supersede(
            old.id,
            &old,
            scope,
            command.name,
            content,
            command.actor_id.clone(),
            visible_to,
            collect_from,
            owners,
            command.change_reason,
            valid_from,
            valid_to,
        )
        .await?;
        Ok(GroupContextAck::ok())
    }

    async fn list(
        &self,
        query: ListGroupContexts,
    ) -> Result<GroupContextListResponse, ApplicationError> {
        validate_actor(&query.actor_id)?;
        let resolved = resolve_scopes(&query.request_context, &query.effective_scope_levels())?;
        let scopes = resolved
            .iter()
            .map(|scope| scope.scope.clone())
            .collect::<Vec<_>>();
        let rows = self
            .repo
            .list_active_meta_by_scopes(&scopes)
            .await
            .map_err(map_store_error)?;
        let contexts = rows
            .into_iter()
            .filter_map(|entry| {
                permission_for(&entry, &query.actor_id).map(|permission| GroupContextListItem {
                    name: entry.name,
                    scope_level: entry.scope_level,
                    scope: entry.scope,
                    permission,
                    gmt_create: entry.gmt_create,
                    gmt_modified: entry.gmt_modified,
                })
            })
            .collect();
        Ok(GroupContextListResponse { contexts })
    }

    async fn get(
        &self,
        query: GetGroupContext,
    ) -> Result<GroupContextGetResponse, ApplicationError> {
        validate_actor(&query.actor_id)?;
        let resolved = resolve_scopes(&query.request_context, &query.effective_scope_levels())?;
        let scopes = resolved
            .iter()
            .map(|scope| scope.scope.clone())
            .collect::<Vec<_>>();
        let mut rows = self
            .repo
            .get_active_by_name_and_scopes(&query.name, &scopes)
            .await
            .map_err(map_store_error)?;
        if !rows.is_empty()
            && !rows
                .iter()
                .any(|entry| actor_can_read(entry, &query.actor_id))
        {
            return Err(permission_denied(format!(
                "actor_id={} is not allowed to read {}",
                query.actor_id, query.name
            )));
        }
        rows.retain(|entry| actor_can_read(entry, &query.actor_id));
        rows.sort_by_key(|entry| specificity_rank(entry.scope_level));
        let items = rows
            .into_iter()
            .map(|entry| GroupContextGetItem {
                name: entry.name,
                scope_level: entry.scope_level,
                content: entry.content,
                valid_from: entry.valid_from,
                valid_to: entry.valid_to,
                visible_to: None,
                collect_from: None,
            })
            .collect();
        Ok(GroupContextGetResponse { items })
    }

    async fn systemset(
        &self,
        command: SystemSetGroupContext,
    ) -> Result<GroupContextAck, ApplicationError> {
        let scope = resolve_scope(&command.request_context, command.scope_level)?;
        validate_name(&command.name)?;
        validate_content(&command.content)?;
        validate_policies(&command.visible_to, &command.collect_from)?;
        let existing = self
            .repo
            .get_active(&command.name, &scope.scope)
            .await
            .map_err(map_store_error)?;
        match existing {
            Some(old) => {
                // CAS guard: if old.id was already superseded, this returns Conflict.
                self.supersede(
                    old.id,
                    &old,
                    scope,
                    command.name,
                    command.content,
                    SYSTEM_CREATOR.to_string(),
                    command.visible_to,
                    command.collect_from,
                    old.owners.clone(),
                    None,
                    command.valid_from.unwrap_or_else(Utc::now),
                    command.valid_to,
                )
                .await?;
            }
            None => {
                self.create(
                    scope,
                    command.name,
                    command.content,
                    SYSTEM_CREATOR.to_string(),
                    command.visible_to,
                    command.collect_from,
                    GroupContextOwners::new([SYSTEM_CREATOR]),
                    None,
                    command.valid_from,
                    command.valid_to,
                )
                .await?;
            }
        }
        Ok(GroupContextAck::ok())
    }
}

fn validate_actor(actor_id: &str) -> Result<(), ApplicationError> {
    if actor_id.trim().is_empty() {
        return Err(ApplicationError::invalid(
            INVALID_PARAM,
            "actor_id is required",
        ));
    }
    if actor_id.chars().count() > MAX_CREATOR_CHARS {
        return Err(ApplicationError::payload_too_large(
            PAYLOAD_TOO_LARGE,
            format!("actor_id exceeds {MAX_CREATOR_CHARS} characters"),
        ));
    }
    Ok(())
}

fn validate_name(name: &str) -> Result<(), ApplicationError> {
    if name.trim().is_empty() {
        return Err(ApplicationError::invalid(INVALID_PARAM, "name is required"));
    }
    if name.chars().count() > MAX_NAME_CHARS {
        return Err(ApplicationError::payload_too_large(
            PAYLOAD_TOO_LARGE,
            format!("name exceeds {MAX_NAME_CHARS} characters"),
        ));
    }
    Ok(())
}

fn validate_policies(
    visible_to: &GroupContextPolicy,
    collect_from: &GroupContextPolicy,
) -> Result<(), ApplicationError> {
    validate_policy("visible_to", visible_to)?;
    validate_collect_from(collect_from)
}

fn validate_policy(field: &str, policy: &GroupContextPolicy) -> Result<(), ApplicationError> {
    let encoded = serde_json::to_vec(policy)
        .map_err(|error| ApplicationError::internal(error.to_string()))?;
    if encoded.len() > MAX_POLICY_BYTES {
        return Err(ApplicationError::payload_too_large(
            PAYLOAD_TOO_LARGE,
            format!("{field} serialized JSON exceeds {MAX_POLICY_BYTES} bytes"),
        ));
    }
    Ok(())
}

fn validate_content(content: &str) -> Result<(), ApplicationError> {
    if content.as_bytes().len() > MAX_CONTENT_BYTES {
        Err(ApplicationError::payload_too_large(
            PAYLOAD_TOO_LARGE,
            format!("content exceeds {MAX_CONTENT_BYTES} bytes"),
        ))
    } else {
        Ok(())
    }
}

fn validate_owners(owners: &GroupContextOwners) -> Result<(), ApplicationError> {
    let encoded = serde_json::to_vec(owners)
        .map_err(|error| ApplicationError::internal(error.to_string()))?;
    if encoded.len() > MAX_OWNERS_BYTES {
        return Err(ApplicationError::payload_too_large(
            PAYLOAD_TOO_LARGE,
            format!("owners serialized JSON exceeds {MAX_OWNERS_BYTES} bytes"),
        ));
    }
    if owners.is_empty() {
        return Err(ApplicationError::invalid(
            INVALID_PARAM,
            "owners.userIds must not be empty",
        ));
    }
    if owners
        .user_ids
        .iter()
        .any(|user_id| user_id.trim().is_empty())
    {
        return Err(ApplicationError::invalid(
            INVALID_PARAM,
            "owners.userIds must not contain blank ids",
        ));
    }
    if owners
        .user_ids
        .iter()
        .any(|user_id| user_id.chars().count() > MAX_CREATOR_CHARS)
    {
        return Err(ApplicationError::payload_too_large(
            PAYLOAD_TOO_LARGE,
            format!("owners.userIds contains an id exceeding {MAX_CREATOR_CHARS} characters"),
        ));
    }
    Ok(())
}

fn validate_collect_from(policy: &GroupContextPolicy) -> Result<(), ApplicationError> {
    validate_policy("collect_from", policy)?;
    if policy.user_ids.is_empty() {
        Err(ApplicationError::invalid(
            INVALID_PARAM,
            "collect_from.userIds must not be empty",
        ))
    } else {
        Ok(())
    }
}

fn not_found(name: &str, scope: &str) -> ApplicationError {
    ApplicationError::not_found(
        NOT_FOUND,
        format!("name={name} and scope={scope}, group context not found"),
    )
}

fn permission_denied(message: impl Into<String>) -> ApplicationError {
    ApplicationError::forbidden_code(PERMISSION_DENIED, message)
}

fn map_store_error(error: GroupContextStoreError) -> ApplicationError {
    match error {
        GroupContextStoreError::DuplicateActive { name, scope } => {
            ApplicationError::conflict(CONFLICT, format!("{name} already exists in {scope}"))
        }
        GroupContextStoreError::NotFound { name, scope } => not_found(&name, &scope),
        GroupContextStoreError::Conflict { name, scope } => ApplicationError::conflict(
            CONFLICT,
            format!("concurrent update lost for name={name} scope={scope}"),
        ),
        GroupContextStoreError::Backend(message) => ApplicationError::internal(message),
    }
}

fn specificity_rank(scope_level: ScopeLevel) -> u8 {
    match scope_level {
        ScopeLevel::Round => 0,
        ScopeLevel::Session => 1,
        ScopeLevel::Group => 2,
    }
}

fn new_context_id() -> String {
    Ulid::new().to_string()
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn store_not_found_and_backend_errors_map_to_application_errors() {
        let not_found = map_store_error(GroupContextStoreError::NotFound {
            name: "player_state".into(),
            scope: "sess-001".into(),
        });
        assert_eq!(not_found.code(), NOT_FOUND);

        let internal = map_store_error(GroupContextStoreError::Backend("storage failed".into()));
        assert_eq!(internal.code(), "internal_error");
    }
}
