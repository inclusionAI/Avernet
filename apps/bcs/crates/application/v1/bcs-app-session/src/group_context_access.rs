//! Group and Session resource checks for public Group Context operations.
//!
//! The guard resolves the canonical Group for adds and preset definitions, and
//! checks Session membership when a Bot lists presets with a Session ID.
//! Entry-level permissions remain in the Group Context application service.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{ActorKind, ScopeLevel};
use std::collections::HashSet;
use bcs_service_api::application::v1::{
    AddGroupContext, ApplicationError, GetGroupContext, GroupContextAck,
    GroupContextApplicationService, GroupContextGetResponse, GroupContextListResponse,
    GroupContextRequestContext, ListGroupContexts, SystemSetGroupContext, UpdateGroupContext,
    AuthenticatedCaller, ListSharedGroupContexts, SharedGroupContextItem,
};
use bcs_service_api::port::{BotAuthorityPort, repo::SessionRepoPort};
use bcs_service_api::{Group, GroupCoreService, Participant, Session};

pub struct GroupContextAddAccessGuard {
    inner: Arc<dyn GroupContextApplicationService>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionRepoPort>,
    bot_authority: Arc<dyn BotAuthorityPort>,
}

impl GroupContextAddAccessGuard {
    pub fn new(
        inner: Arc<dyn GroupContextApplicationService>,
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionRepoPort>,
        bot_authority: Arc<dyn BotAuthorityPort>,
    ) -> Self {
        Self {
            inner,
            groups,
            sessions,
            bot_authority,
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

impl GroupContextAddAccessGuard {
    async fn authorize_preset_write(&self, group_id: &str, actor: &str) -> Result<Group, ApplicationError> {
        let group = self.load_group(group_id).await?;
        let user_id = actor.strip_prefix("human_").filter(|id| !id.is_empty())
            .ok_or_else(|| ApplicationError::forbidden_code("permission_denied", "preset management requires a Human caller"))?;
        let authorized = self.bot_authority.can_manage(user_id, &group.driver_bot).await
            .map_err(|error| ApplicationError::internal(format!("Bot authority lookup failed: {error}")))?;
        if !authorized {
            return Err(ApplicationError::forbidden_code(
                "permission_denied",
                "preset management requires owner or admin authority on the Group driver Bot",
            ));
        }
        Ok(group)
    }
}

#[async_trait]
impl GroupContextApplicationService for GroupContextAddAccessGuard {
    async fn add(&self, mut command: AddGroupContext) -> Result<GroupContextAck, ApplicationError> {
        self.authorize_add(&mut command).await?;
        self.inner.add(command).await
    }

    async fn shared_info(
        &self,
        group_id: &str,
        session_id: Option<&str>,
        view_bot_id: Option<&str>,
        caller: &AuthenticatedCaller,
    ) -> Result<Vec<SharedGroupContextItem>, ApplicationError> {
        let group = self.groups.read_authorization_snapshot(group_id).await
            .map_err(|error| ApplicationError::internal(format!("Group lookup failed: {error}")))?
            .filter(|group| group.live)
            .ok_or_else(|| ApplicationError::not_found("group_not_found", "Group was not found"))?;
        let session = if let Some(id) = session_id {
            let session = self.sessions.read_authorization_snapshot(id).await
                .map_err(|error| ApplicationError::internal(format!("Session lookup failed: {error}")))?
                .filter(|session| session.live)
                .ok_or_else(|| ApplicationError::not_found("session_not_found", "Session was not found"))?;
            if session.group_id != group.id {
                return Err(invalid_param("sessionId must identify a Session in the requested Group"));
            }
            Some(session)
        } else {
            None
        };
        let members: Vec<_> = group.participants.iter()
            .chain(session.iter().flat_map(|session| session.participants.iter()))
            .collect();
        let bots: HashSet<_> = members.iter()
            .filter(|member| member.actor_kind == ActorKind::Bot)
            .map(|member| member.actor_id.as_str())
            .collect();
        let session_has_bot = |bot_id: &str| session.as_ref().is_some_and(|session| {
            session.participants.iter().any(|member| {
                member.actor_kind == ActorKind::Bot && member.actor_id == bot_id
            })
        });
        let has_bot = |bot_id: &str| {
            if session.is_some() { session_has_bot(bot_id) } else { bots.contains(bot_id) }
        };
        let actor_ids = if let Some(id) = view_bot_id {
            let user = caller.user.as_ref().filter(|_| caller.bot.is_none())
                .ok_or_else(|| permission_denied(id, group_id))?;
            if !has_bot(id) || !self.bot_authority.can_manage(&user.id, id).await
                .map_err(|error| ApplicationError::internal(format!("Bot authority lookup failed: {error}")))?
            {
                return Err(permission_denied(id, group_id));
            }
            vec![id.to_string()]
        } else if let Some(bot) = &caller.bot {
            if !has_bot(&bot.bot_uuid) {
                return Err(permission_denied(&bot.bot_uuid, group_id));
            }
            vec![bot.bot_uuid.clone()]
        } else if let Some(user) = &caller.user {
            let human = format!("human_{}", user.id);
            let mut actor_ids = vec![human.clone()];
            let mut manages_member = false;
            let mut can_read_session = session.as_ref().is_some_and(|session| {
                session.participants.iter().any(|member| {
                    member.actor_kind == ActorKind::Human && member.actor_id == human
                })
            });
            if session.is_some() {
                use futures::{StreamExt, TryStreamExt};

                // Bound outstanding authority reads per request to protect the shared DB pool.
                let bot_ids: Vec<_> = bots.iter().map(|id| id.to_string()).collect();
                let managed: Vec<_> = futures::stream::iter(bot_ids)
                    .map(|id| async move {
                        self.bot_authority.can_manage(&user.id, &id).await
                            .map(|allowed| allowed.then_some(id))
                    })
                    .buffer_unordered(8)
                    .try_collect()
                    .await
                    .map_err(|error| ApplicationError::internal(format!("Bot authority lookup failed: {error}")))?;
                for id in managed.into_iter().flatten() {
                    can_read_session |= session_has_bot(&id);
                    actor_ids.push(id);
                }
            } else if group.visibility != "public" && !group.participants.iter().any(|member| {
                member.actor_kind == ActorKind::Human && member.actor_id == human
            }) {
                for id in bots {
                    if self.bot_authority.can_manage(&user.id, id).await
                        .map_err(|error| ApplicationError::internal(format!("Bot authority lookup failed: {error}")))?
                    {
                        manages_member = true;
                        break;
                    }
                }
            }
            let admitted = if session.is_some() {
                can_read_session
            } else {
                group.visibility == "public" || manages_member || group.participants.iter().any(|member| {
                    member.actor_kind == ActorKind::Human && member.actor_id == human
                })
            };
            if !admitted {
                return Err(permission_denied(&human, group_id));
            }
            actor_ids
        } else {
            return Err(ApplicationError::Unauthenticated);
        };
        self.inner.list_shared_contexts(ListSharedGroupContexts {
            group_id: group.id,
            session_id: session.map(|session| session.id),
            actor_ids,
        }).await
    }

    async fn can_manage_presets(&self, group_id: &str, actor: &str) -> Result<bool, ApplicationError> {
        match self.authorize_preset_write(group_id, actor).await {
            Ok(_) => Ok(true),
            Err(ApplicationError::Forbidden(_) | ApplicationError::ForbiddenCode { .. }) => Ok(false),
            Err(error) => Err(error),
        }
    }

    async fn list_presets(&self, group_id: &str, caller: &AuthenticatedCaller, session_id: Option<&str>) -> Result<Vec<bcs_service_api::application::v1::GroupContextItemPreset>, ApplicationError> {
        let group = self.load_group(group_id).await?;
        if let Some(bot) = &caller.bot
            && !group.participants.iter().any(|member| member.bot_uuid == bot.bot_uuid)
        {
            let session_id = session_id.filter(|id| !id.trim().is_empty()).ok_or_else(|| {
                ApplicationError::forbidden_code(
                    "permission_denied",
                    "This Bot is not a Group member. If it participates in a Session in this Group, pass sessionId (CLI: --session-id) to verify Session membership.",
                )
            })?;
            let session = self.load_session(session_id).await?;
            if session.group_id != group.id {
                return Err(invalid_param("sessionId must identify a Session in the requested Group"));
            }
            ensure_participant(&session.participants, &bot.bot_uuid, "the specified Session")?;
        }
        self.inner.list_presets(&group.id, caller, session_id).await
    }

    async fn add_preset(&self, group_id: &str, actor: &str, name: String, description: String) -> Result<(), ApplicationError> {
        let group = self.authorize_preset_write(group_id, actor).await?;
        self.inner.add_preset(&group.id, actor, name, description).await
    }

    async fn update_preset(&self, group_id: &str, actor: &str, name: String, new_name: Option<String>, description: Option<String>) -> Result<(), ApplicationError> {
        let group = self.authorize_preset_write(group_id, actor).await?;
        self.inner.update_preset(&group.id, actor, name, new_name, description).await
    }

    async fn delete_preset(&self, group_id: &str, actor: &str, name: String) -> Result<(), ApplicationError> {
        let group = self.authorize_preset_write(group_id, actor).await?;
        self.inner.delete_preset(&group.id, actor, name).await
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
