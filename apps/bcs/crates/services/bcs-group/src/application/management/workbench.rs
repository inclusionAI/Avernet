//! Workbench session/chat authorization for the legacy Group service.

use super::*;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct WorkbenchAuthorizedHuman {
    pub(crate) actor_id: String,
    pub(crate) staff_no: String,
}

#[async_trait]
impl WorkbenchSessionService for GroupManagement {
    async fn connect(
        &self,
        command: WorkbenchConnectCommand,
    ) -> Result<WorkbenchConnectOutcome, WorkbenchUseCaseError> {
        let group = self
            .group
            .get(&command.group_id)
            .await
            .ok_or_else(|| WorkbenchUseCaseError::GroupNotFound(command.group_id.clone()))?;

        let (authenticated, legacy_participants) = match self
            .authorize_workbench_group_access(&group, command.bound_actor_id.as_deref())
            .await
        {
            Ok(authenticated) => (authenticated, workbench_participants(&group)),
            Err(WorkbenchUseCaseError::ForbiddenGroupAccess) => {
                let actor_id = command
                    .bound_actor_id
                    .as_deref()
                    .ok_or(WorkbenchUseCaseError::Unauthorized)?;
                let Some(session_id) = command.session_id.as_deref() else {
                    return Err(WorkbenchUseCaseError::ForbiddenGroupAccess);
                };
                let session = self
                    .session_management
                    .get(session_id)
                    .await
                    .map_err(|error| {
                        WorkbenchUseCaseError::Service(ServiceError::InternalError(
                            error.to_string(),
                        ))
                    })?
                    .filter(|session| session.group_id == command.group_id)
                    .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
                let participant = find_session_participant(&session, actor_id)
                    .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
                if participant.mode == Some(ParticipantMode::Absent) {
                    return Err(WorkbenchUseCaseError::ParticipantAbsent);
                }
                (
                    WorkbenchAuthorizedHuman {
                        actor_id: actor_id.to_string(),
                        staff_no: staff_no_from_bound_actor(Some(actor_id))?.to_string(),
                    },
                    workbench_participants_from_slice(&session.participants),
                )
            }
            Err(error) => return Err(error),
        };

        let Some(view_actor_id) = command.view_actor_id.as_deref() else {
            // Compatibility path for Workbench clients that predate explicit
            // participant views. Its authorization and participant list are
            // the values produced by the original full-view connection.
            return Ok(WorkbenchConnectOutcome {
                group_id: command.group_id,
                participants: legacy_participants,
            });
        };

        self.authorize_workbench_sender(&group, view_actor_id, &authenticated)
            .await
            .map_err(|error| match error {
                WorkbenchUseCaseError::ForbiddenSender => WorkbenchUseCaseError::ForbiddenViewActor,
                other => other,
            })?;

        let participants = if let Some(session_id) = command.session_id.as_deref() {
            let session = self
                .session_management
                .get(session_id)
                .await
                .map_err(|error| {
                    WorkbenchUseCaseError::Service(ServiceError::InternalError(error.to_string()))
                })?
                .filter(|session| session.group_id == command.group_id)
                .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
            let participant = find_session_participant(&session, view_actor_id)
                .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
            if participant.mode == Some(ParticipantMode::Absent) {
                return Err(WorkbenchUseCaseError::ParticipantAbsent);
            }
            workbench_participants_from_slice(&session.participants)
        } else {
            let participant = group
                .get_participant(view_actor_id)
                .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
            if participant.mode == Some(ParticipantMode::Absent) {
                return Err(WorkbenchUseCaseError::ParticipantAbsent);
            }
            workbench_participants(&group)
        };

        Ok(WorkbenchConnectOutcome {
            group_id: command.group_id,
            participants,
        })
    }

    async fn authorize_chat_send(
        &self,
        command: WorkbenchChatAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        let group = self
            .group
            .get(&command.group_id)
            .await
            .ok_or_else(|| WorkbenchUseCaseError::GroupNotFound(command.group_id.clone()))?;
        let auth = match self
            .authorize_workbench_group_access(&group, command.bound_actor_id.as_deref())
            .await
        {
            Ok(auth) => auth,
            Err(WorkbenchUseCaseError::ForbiddenGroupAccess) => {
                let actor_id = command
                    .bound_actor_id
                    .as_deref()
                    .ok_or(WorkbenchUseCaseError::Unauthorized)?;
                let staff_no = staff_no_from_bound_actor(Some(actor_id))?;
                if self
                    .session_participant(command.session_id.as_deref(), &command.group_id, actor_id)
                    .await?
                    .is_some()
                {
                    WorkbenchAuthorizedHuman {
                        actor_id: actor_id.to_string(),
                        staff_no: staff_no.to_string(),
                    }
                } else {
                    return Err(WorkbenchUseCaseError::ForbiddenGroupAccess);
                }
            }
            Err(error) => return Err(error),
        };

        self.authorize_workbench_sender(&group, &command.from_actor_id, &auth)
            .await?;

        let group_participant = group.get_participant(&command.from_actor_id).cloned();
        let session_participant = self
            .session_participant(
                command.session_id.as_deref(),
                &command.group_id,
                &command.from_actor_id,
            )
            .await?;

        match session_participant.or(group_participant) {
            Some(participant) if participant.mode == Some(ParticipantMode::Absent) => {
                Err(WorkbenchUseCaseError::ParticipantAbsent)
            }
            Some(_) => Ok(()),
            None => Err(WorkbenchUseCaseError::SenderNotInGroup),
        }
    }

    async fn authorize_chat_abort(
        &self,
        command: WorkbenchChatAbortAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        let actor_id = command
            .bound_actor_id
            .as_deref()
            .ok_or(WorkbenchUseCaseError::Unauthorized)?;
        let staff_no = staff_no_from_bound_actor(Some(actor_id))?;
        let session = self
            .session_management
            .get(&command.session_id)
            .await
            .map_err(|error| {
                WorkbenchUseCaseError::Service(ServiceError::InternalError(error.to_string()))
            })?
            .filter(|session| session.group_id == command.group_id)
            .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;

        let direct_humans = session
            .participants
            .iter()
            .filter(|participant| participant.is_human() && participant.bot_uuid == actor_id)
            .collect::<Vec<_>>();
        if direct_humans
            .iter()
            .any(|participant| participant.effective_mode() == ParticipantMode::Absent)
        {
            return Err(WorkbenchUseCaseError::ParticipantAbsent);
        }

        // COSEC: abort authorization is recalculated from the current Session
        // on every request. A stale WebSocket subscription never grants access.
        let mut may_operate_session = !direct_humans.is_empty();
        if !may_operate_session {
            for participant in session
                .participants
                .iter()
                .filter(|participant| participant.is_bot())
            {
                let Some(bot) = self.registry.get(&participant.bot_uuid).await else {
                    continue;
                };
                if bot_belongs_to_staff(&participant.bot_uuid, bot.created_by.as_deref(), staff_no)
                {
                    may_operate_session = true;
                    break;
                }
            }
        }
        if !may_operate_session {
            return Err(WorkbenchUseCaseError::ForbiddenGroupAccess);
        }

        let target = session
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == command.target_bot_id);
        if !target.is_some_and(|participant| participant.is_bot()) {
            return Err(WorkbenchUseCaseError::TargetBotNotInSession);
        }

        Ok(())
    }
}

#[async_trait]
impl CanResolveInteraction for GroupManagement {
    async fn can_resolve(&self, command: CanResolveInteractionCommand) -> ServiceResult<bool> {
        let Some(staff_no) = command
            .actor_id
            .strip_prefix("human_")
            .filter(|staff_no| !staff_no.is_empty())
        else {
            return Ok(false);
        };
        let Some(session) = self
            .session_management
            .get(&command.bcs_session_id)
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?
            .filter(|session| session.group_id == command.group_id)
        else {
            return Ok(false);
        };

        for participant in &session.participants {
            if participant.effective_mode() == ParticipantMode::Absent {
                continue;
            }
            if participant.is_human() && participant.bot_uuid == command.actor_id {
                return Ok(true);
            }
            if !participant.is_bot() {
                continue;
            }
            let Some(bot) = self.registry.get(&participant.bot_uuid).await else {
                continue;
            };
            if bot_belongs_to_staff(&participant.bot_uuid, bot.created_by.as_deref(), staff_no) {
                return Ok(true);
            }
        }
        Ok(false)
    }
}
