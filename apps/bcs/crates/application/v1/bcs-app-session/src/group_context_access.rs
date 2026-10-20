//! Add-only access guard for the public Group Context application service.
//!
//! The guard resolves the resource identity from the current Group/Session and
//! returns before the Group Context service can create a write. All other
//! operations are plain delegation; this is deliberately not a global
//! interceptor or generic authorization framework.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::ScopeLevel;
use bcs_service_api::application::v1::{
    AddGroupContext, ApplicationError, GetGroupContext, GroupContextAck,
    GroupContextApplicationService, GroupContextGetResponse, GroupContextListResponse,
    GroupContextRequestContext, ListGroupContexts, SystemSetGroupContext, UpdateGroupContext,
};
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_service_api::{Group, GroupCoreService, Participant, Session};

pub struct GroupContextAddAccessGuard {
    inner: Arc<dyn GroupContextApplicationService>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionRepoPort>,
}

impl GroupContextAddAccessGuard {
    pub fn new(
        inner: Arc<dyn GroupContextApplicationService>,
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionRepoPort>,
    ) -> Self {
        Self {
            inner,
            groups,
            sessions,
        }
    }

    async fn authorize_add(&self, command: &mut AddGroupContext) -> Result<(), ApplicationError> {
        match command.scope_level {
            ScopeLevel::Group => {
                let group_id = required_value(
                    "groupId",
                    command.request_context.group_id.as_deref(),
                    scope_level_name(command.scope_level),
                )?;
                let group = self.load_group(group_id).await?;
                ensure_participant(&group.participants, &command.actor_id, &group.id)?;
                sync_group_id(
                    &mut command.request_context,
                    &group,
                    scope_level_name(command.scope_level),
                )?;
            }
            ScopeLevel::Session => {
                let session_id = required_value(
                    "sessionId",
                    command.request_context.session_id.as_deref(),
                    scope_level_name(command.scope_level),
                )?;
                let session = self.load_session(session_id).await?;
                let group = self
                    .authorize_session_and_load_group(&session, &command.actor_id)
                    .await?;
                sync_group_id(
                    &mut command.request_context,
                    &group,
                    scope_level_name(command.scope_level),
                )?;
            }
            ScopeLevel::Round => {
                let round_id = required_value(
                    "roundId",
                    command.request_context.round_id.as_deref(),
                    scope_level_name(command.scope_level),
                )?;
                if round_id
                    .chars()
                    .any(|character| character.is_whitespace() || character.is_control())
                {
                    return Err(invalid_param(
                        "param roundId must not contain whitespace or control characters when scope_level=round",
                    ));
                }
                let explicit_session_id = optional_value(
                    "sessionId",
                    command.request_context.session_id.as_deref(),
                    scope_level_name(command.scope_level),
                )?;
                let session_id = match explicit_session_id {
                    Some(session_id) => session_id.to_string(),
                    None => session_id_from_round_id(round_id)?,
                };
                let session = self.load_session(&session_id).await?;
                if !round_id_is_for_session(round_id, &session.id) {
                    return Err(invalid_param(
                        "param roundId must start with \"{sessionId}:\" and contain a round \
suffix when scope_level=round",
                    ));
                }
                let group = self
                    .authorize_session_and_load_group(&session, &command.actor_id)
                    .await?;
                command.request_context.session_id = Some(session.id.clone());
                sync_group_id(
                    &mut command.request_context,
                    &group,
                    scope_level_name(command.scope_level),
                )?;
            }
        }
        Ok(())
    }

    async fn authorize_session_and_load_group(
        &self,
        session: &Session,
        actor_id: &str,
    ) -> Result<Group, ApplicationError> {
        ensure_participant(&session.participants, actor_id, "the current Session")?;
        self.load_group(&session.group_id).await
    }

    async fn load_group(&self, group_id: &str) -> Result<Group, ApplicationError> {
        self.groups
            .try_get(group_id)
            .await
            .map_err(|error| ApplicationError::internal(format!("Group lookup failed: {error}")))?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })
    }

    async fn load_session(&self, session_id: &str) -> Result<Session, ApplicationError> {
        self.sessions
            .try_get(session_id)
            .await
            .map_err(|error| ApplicationError::internal(format!("Session lookup failed: {error}")))?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "session_not_found",
                    format!("Session '{session_id}' was not found"),
                )
            })
    }
}

#[async_trait]
impl GroupContextApplicationService for GroupContextAddAccessGuard {
    async fn add(&self, mut command: AddGroupContext) -> Result<GroupContextAck, ApplicationError> {
        self.authorize_add(&mut command).await?;
        self.inner.add(command).await
    }

    async fn update(
        &self,
        command: UpdateGroupContext,
    ) -> Result<GroupContextAck, ApplicationError> {
        self.inner.update(command).await
    }

    async fn list(
        &self,
        query: ListGroupContexts,
    ) -> Result<GroupContextListResponse, ApplicationError> {
        self.inner.list(query).await
    }

    async fn get(
        &self,
        query: GetGroupContext,
    ) -> Result<GroupContextGetResponse, ApplicationError> {
        self.inner.get(query).await
    }

    async fn systemset(
        &self,
        command: SystemSetGroupContext,
    ) -> Result<GroupContextAck, ApplicationError> {
        self.inner.systemset(command).await
    }
}

fn ensure_participant(
    participants: &[Participant],
    actor_id: &str,
    scope: &str,
) -> Result<(), ApplicationError> {
    if participants
        .iter()
        .any(|participant| participant.bot_uuid == actor_id)
    {
        return Ok(());
    }
    Err(permission_denied(actor_id, scope))
}

fn sync_group_id(
    request_context: &mut GroupContextRequestContext,
    group: &Group,
    scope_level: &str,
) -> Result<(), ApplicationError> {
    if let Some(group_id) = request_context
        .group_id
        .as_deref()
        .filter(|group_id| !group_id.trim().is_empty())
    {
        if group_id != group.id {
            return Err(invalid_param(format!(
                "param groupId must match Group '{}' when scope_level={scope_level}",
                group.id
            )));
        }
    }
    request_context.group_id = Some(group.id.clone());
    Ok(())
}

fn required_value<'a>(
    field: &str,
    value: Option<&'a str>,
    scope_level: &str,
) -> Result<&'a str, ApplicationError> {
    let Some(value) = value else {
        return Err(missing_scope_value(field, scope_level));
    };
    if value.trim().is_empty() {
        return Err(missing_scope_value(field, scope_level));
    }
    Ok(value)
}

fn optional_value<'a>(
    field: &str,
    value: Option<&'a str>,
    scope_level: &str,
) -> Result<Option<&'a str>, ApplicationError> {
    if value.is_some_and(|value| value.trim().is_empty()) {
        return Err(invalid_param(format!(
            "param {field} must not be empty when scope_level={scope_level}"
        )));
    }
    Ok(value)
}

fn session_id_from_round_id(round_id: &str) -> Result<String, ApplicationError> {
    let mut parts = round_id.splitn(3, ':');
    let group_part = parts.next().unwrap_or_default();
    let session_part = parts.next().unwrap_or_default();
    let round_part = parts.next().unwrap_or_default();
    if group_part.trim().is_empty()
        || session_part.trim().is_empty()
        || round_part.trim().is_empty()
    {
        return Err(invalid_param(
            "param roundId must have the form \"{sessionId}:{roundId}\" when scope_level=round",
        ));
    }
    Ok(format!("{group_part}:{session_part}"))
}

fn round_id_is_for_session(round_id: &str, session_id: &str) -> bool {
    let prefix = format!("{session_id}:");
    round_id
        .strip_prefix(&prefix)
        .is_some_and(|round_part| !round_part.trim().is_empty())
}

fn invalid_param(message: impl Into<String>) -> ApplicationError {
    ApplicationError::invalid("invalid_param", message)
}

fn permission_denied(actor_id: &str, scope: &str) -> ApplicationError {
    ApplicationError::forbidden_code(
        "permission_denied",
        format!("actor_id={actor_id} is not a member of {scope}"),
    )
}

fn scope_level_name(scope_level: ScopeLevel) -> &'static str {
    match scope_level {
        ScopeLevel::Group => "group",
        ScopeLevel::Session => "session",
        ScopeLevel::Round => "round",
    }
}

fn missing_scope_value(field: &str, scope_level: &str) -> ApplicationError {
    invalid_param(format!(
        "param {field} is required when scope_level={scope_level}"
    ))
}
