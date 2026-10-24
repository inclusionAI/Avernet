//! Workbench session/chat authorization for the legacy Group service.

use super::*;
use bcs_service_api::ParticipantKind;
use bcs_service_api::types::authorization_snapshot::{
    GroupAuthorizationSnapshot, ParticipantAuthorizationSnapshot, SessionAuthorizationSnapshot,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct WorkbenchAuthorizedHuman {
    pub(crate) actor_id: String,
    pub(crate) staff_no: String,
}

/// One Workbench target's strict authorization facts (F4, spec
/// §8.3 权威资源读取): the Group snapshot is always read through
/// the strict Core (never the display cache; failures propagate);
/// an explicit `session_id` additionally reads the Session
/// snapshot the same way. The two reads are shared by the access
/// check, the sender check, the Provider-scope check and the
/// target-membership check inside one request.
pub(crate) struct WorkbenchTarget {
    group: GroupAuthorizationSnapshot,
    /// Present when an explicit Session target exists AND belongs
    /// to this Group. A missing or foreign explicit Session reads
    /// as `None` while `session_requested` keeps the intent — a
    /// Session target NEVER backstops through Group facts.
    session: Option<SessionAuthorizationSnapshot>,
    session_requested: bool,
}

impl WorkbenchTarget {
    /// The target-resource participant row of `actor_id`: an
    /// explicit Session target gates on the Session snapshot
    /// ONLY (Group membership never backstops a Session-targeted
    /// operation); a Group-level target gates on the Group
    /// snapshot.
    fn participant(&self, actor_id: &str) -> Option<&ParticipantAuthorizationSnapshot> {
        match (&self.session_requested, &self.session) {
            (true, Some(session)) => session
                .participants
                .iter()
                .find(|participant| participant.actor_id == actor_id),
            (true, None) => None,
            (false, _) => self
                .group
                .participants
                .iter()
                .find(|participant| participant.actor_id == actor_id),
        }
    }
}

impl GroupManagement {
    /// Resolve the strict authorization facts of one Workbench
    /// target (F4): the Group snapshot — and, for an explicit
    /// `session_id`, the Session snapshot — through their strict
    /// Cores. Read failures propagate (no display-cache
    /// fallback); an absent Group is `GroupNotFound`.
    async fn workbench_target(
        &self,
        group_id: &str,
        session_id: Option<&str>,
    ) -> Result<WorkbenchTarget, WorkbenchUseCaseError> {
        let group = self
            .group
            .read_authorization_snapshot(group_id)
            .await
            .map_err(WorkbenchUseCaseError::from_service_error)?
            .ok_or_else(|| WorkbenchUseCaseError::GroupNotFound(group_id.to_string()))?;
        let mut session = None;
        if let Some(session_id) = session_id {
            session = self
                .session_authorization()?
                .read_authorization_snapshot(session_id)
                .await
                .map_err(WorkbenchUseCaseError::from_service_error)?
                .filter(|snapshot| snapshot.group_id == group_id);
        }
        Ok(WorkbenchTarget {
            group,
            session,
            session_requested: session_id.is_some(),
        })
    }

    async fn authorize_workbench_provider_scope(
        &self, target: &WorkbenchTarget, actor_id: Option<&str>,
    ) -> Result<(), WorkbenchUseCaseError> {
        let actor_id = actor_id.ok_or(WorkbenchUseCaseError::Unauthorized)?;
        let mut actors = target.group.participants.iter().map(|p| p.actor_id.clone()).collect::<Vec<_>>();
        actors.push(actor_id.into());
        let restricted = self.registry.collaboration_owner(&actors).await.map_err(|error| match error {
            ServiceError::Forbidden(_) => WorkbenchUseCaseError::ForbiddenCollaborationScope,
            other => WorkbenchUseCaseError::Service(other),
        })?.is_some();
        if !restricted { return Ok(()); }
        if let Some(session) = &target.session {
            actors.extend(session.participants.iter().map(|p| p.actor_id.clone()));
        }
        self.registry.collaboration_owner(&actors).await.map_err(|error| match error {
            ServiceError::Forbidden(_) => WorkbenchUseCaseError::ForbiddenCollaborationScope,
            other => WorkbenchUseCaseError::Service(other),
        })?;
        Ok(())
    }
}

#[async_trait]
impl WorkbenchSessionService for GroupManagement {
    async fn connect(
        &self,
        command: WorkbenchConnectCommand,
    ) -> Result<WorkbenchConnectOutcome, WorkbenchUseCaseError> {
        let target = self
            .workbench_target(&command.group_id, command.session_id.as_deref())
            .await?;
        self.authorize_workbench_provider_scope(&target, command.bound_actor_id.as_deref()).await?;

        let (authenticated, legacy_participants) = match self
            .authorize_workbench_group_access(&target.group, command.bound_actor_id.as_deref())
            .await
        {
            Ok(authenticated) => (authenticated, workbench_participants_from_snapshot(&target.group.participants)),
            Err(WorkbenchUseCaseError::ForbiddenGroupAccess) => {
                let actor_id = command
                    .bound_actor_id
                    .as_deref()
                    .ok_or(WorkbenchUseCaseError::Unauthorized)?;
                // A Human without Group access may still connect
                // to an explicit Session they participate in —
                // Session facts only (F4); a missing or foreign
                // explicit Session never falls back to Group.
                let session = target
                    .session
                    .as_ref()
                    .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
                let participant = session
                    .participants
                    .iter()
                    .find(|participant| participant.actor_id == actor_id)
                    .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
                if participant.effective_mode == ParticipantMode::Absent {
                    return Err(WorkbenchUseCaseError::ParticipantAbsent);
                }
                (
                    WorkbenchAuthorizedHuman {
                        actor_id: actor_id.to_string(),
                        staff_no: staff_no_from_bound_actor(Some(actor_id))?.to_string(),
                    },
                    workbench_participants_from_snapshot(&session.participants),
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

        self.authorize_workbench_sender(&target.group, view_actor_id, &authenticated)
            .await
            .map_err(|error| match error {
                WorkbenchUseCaseError::ForbiddenSender => WorkbenchUseCaseError::ForbiddenViewActor,
                other => other,
            })?;

        // The explicit-view connection gates the view actor on the
        // ACTUAL target resource (F4): an explicit Session admits
        // its own participants only; a Group-level view admits the
        // Group's participants.
        let participant = target
            .participant(view_actor_id)
            .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
        if participant.effective_mode == ParticipantMode::Absent {
            return Err(WorkbenchUseCaseError::ParticipantAbsent);
        }
        let participants = match (&target.session_requested, &target.session) {
            (true, Some(session)) => workbench_participants_from_snapshot(&session.participants),
            _ => workbench_participants_from_snapshot(&target.group.participants),
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
        let target = self
            .workbench_target(&command.group_id, command.session_id.as_deref())
            .await?;
        self.authorize_workbench_provider_scope(&target, command.bound_actor_id.as_deref()).await?;
        let auth = match self
            .authorize_workbench_group_access(&target.group, command.bound_actor_id.as_deref())
            .await
        {
            Ok(auth) => auth,
            Err(WorkbenchUseCaseError::ForbiddenGroupAccess) => {
                // Session-only access: a Human outside the Group
                // may still send when they participate in the
                // explicit target Session (Session facts only).
                let actor_id = command
                    .bound_actor_id
                    .as_deref()
                    .ok_or(WorkbenchUseCaseError::Unauthorized)?;
                let staff_no = staff_no_from_bound_actor(Some(actor_id))?;
                let session_member = target.session.as_ref().is_some_and(|session| {
                    session
                        .participants
                        .iter()
                        .any(|participant| participant.actor_id == actor_id)
                });
                if session_member {
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

        self.authorize_workbench_sender(&target.group, &command.from_actor_id, &auth)
            .await?;

        // F4: the NEW message gates on the ACTUAL target resource
        // — an explicit Session requires the sender in the
        // SESSION participants (never backstopped by Group
        // membership); only a Group-level send checks Group
        // membership. A missing/foreign explicit Session can admit
        // nobody.
        match target.participant(&command.from_actor_id) {
            Some(participant) if participant.effective_mode == ParticipantMode::Absent => {
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
        // COSEC/F4: abort authorization is recalculated from the
        // STRICT current facts on every request. A stale WebSocket
        // subscription or display-cache row never grants access.
        let target = self
            .workbench_target(&command.group_id, Some(&command.session_id))
            .await?;
        let session = target
            .session
            .as_ref()
            .ok_or(WorkbenchUseCaseError::ForbiddenGroupAccess)?;
        self.authorize_workbench_provider_scope(&target, Some(actor_id)).await?;

        let direct_humans = session
            .participants
            .iter()
            .filter(|participant| {
                participant.actor_kind == ActorKind::Human && participant.actor_id == actor_id
            })
            .collect::<Vec<_>>();
        if direct_humans
            .iter()
            .any(|participant| participant.effective_mode == ParticipantMode::Absent)
        {
            return Err(WorkbenchUseCaseError::ParticipantAbsent);
        }

        let mut may_operate_session = !direct_humans.is_empty();
        if !may_operate_session {
            for participant in session
                .participants
                .iter()
                .filter(|participant| participant.actor_kind == ActorKind::Bot)
            {
                // Row 14: the current `can_manage` edge is the
                // only Human→Bot control leg. A read failure
                // propagates — never a deny-as-false.
                if self
                    .human_controls_bot(staff_no, &participant.actor_id)
                    .await
                    .map_err(WorkbenchUseCaseError::Group)?
                {
                    may_operate_session = true;
                    break;
                }
            }
        }
        if !may_operate_session {
            return Err(WorkbenchUseCaseError::ForbiddenGroupAccess);
        }

        let target_bot = session
            .participants
            .iter()
            .find(|participant| participant.actor_id == command.target_bot_id);
        if !target_bot.is_some_and(|participant| participant.actor_kind == ActorKind::Bot) {
            return Err(WorkbenchUseCaseError::TargetBotNotInSession);
        }

        Ok(())
    }
}

/// Project a strict snapshot participant list into the
/// Workbench wire view. The legacy V1 `ParticipantKind` has a
/// single variant so `effective_kind()` always resolves to `Bot`
/// exactly like the pre-snapshot projection; `effective_mode` is
/// the fully-resolved mode (the snapshot resolves a legacy `None`
/// mode to the per-actor-kind default — semantically the value
/// clients already assume for a missing mode).
fn workbench_participants_from_snapshot(
    participants: &[ParticipantAuthorizationSnapshot],
) -> Vec<WorkbenchParticipantView> {
    participants
        .iter()
        .map(|participant| WorkbenchParticipantView {
            bot_uuid: participant.actor_id.clone(),
            role: participant_role_to_wire(participant.role).to_string(),
            kind: ParticipantKind::Bot,
            mode: Some(participant.effective_mode),
            message_view_scope: participant.message_view_scope,
        })
        .collect()
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
            // Row 14: the current `can_manage` edge is the
            // only Human→Bot control leg.
            if self
                .human_controls_bot(staff_no, &participant.bot_uuid)
                .await
                .map_err(|e| ServiceError::InternalError(e.to_string()))?
            {
                return Ok(true);
            }
        }
        Ok(false)
    }
}
