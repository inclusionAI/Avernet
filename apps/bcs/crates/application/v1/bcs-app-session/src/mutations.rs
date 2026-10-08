//! V1 Session facade: write use cases (plan Task 11).
//!
//! Every mutation builds the REQUIRED [`BotOperationContext`] from the
//! verified caller plus the authorized effective Principal and propagates it
//! application → Core → owning Repo, where the store commits the business
//! change and its `applied` audit row in ONE transaction (spec §12.5).

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    require_human, resolve_authorized_principal, ApplicationError, DeleteResult, Principal,
};
use bcs_service_api::application::v1::AuthenticatedCaller;
use bcs_service_api::application::v1::session::{
    AddSessionParticipant, CollectSession, CompleteSession, CreateSession, CreateSessionOutcome,
    DeleteSession, DeleteSessionParticipant, GetSession, ListSessions, SessionCollectionResult,
    SessionCompletionResult, SessionDetail, SessionParticipant, SessionService,
    SessionStatus as V1SessionStatus, UncollectSession, UpdateSession, UpdateSessionParticipant,
};
use bcs_service_api::types::{BotOperationContext, MessageViewScope};
use bcs_service_api::{
    ActorKind, CreateSessionLaunch, GroupStrategy, Participant, ParticipantMode, ParticipantRole,
    RegisteredBot, RequestedSessionRole, SessionKind, SessionLaunchRequest,
    SessionStatus as DomainSessionStatus, SystemMessageEvent,
};

use crate::{map_launch_error, map_service_error, map_session_error, SessionServiceImpl};

impl SessionServiceImpl {
    /// REQUIRED audit identity for a lane whose selector is the synchronous
    /// `require_human` (the Human acts as themselves; there is no
    /// managed-bot delegation to record).
    pub(crate) fn self_context(
        caller: &AuthenticatedCaller,
    ) -> Result<BotOperationContext, ApplicationError> {
        let user = bcs_service_api::application::v1::authorization::require_authenticated_user(caller)?;
        Ok(BotOperationContext {
            operation_id: uuid::Uuid::new_v4().to_string(),
            actor: bcs_service_api::types::BotOperationActor::Human {
                user_id: user.id.clone(),
                effective_actor_id: format!("human_{}", user.id),
            },
        })
    }

    /// Audit identity for the mixed-identity lanes: the verified Human
    /// operator (when present) plus the effective actor resolved by the
    /// async authority selection (spec §12.1(6)).
    pub(crate) async fn authorized_context(
        &self,
        caller: &AuthenticatedCaller,
    ) -> Result<(Principal, BotOperationContext), ApplicationError> {
        let principal = resolve_authorized_principal(caller, self.authority.as_ref()).await?;
        let context = match (&caller.user, &principal) {
            (Some(user), Principal::Bot(bot)) => BotOperationContext {
                operation_id: uuid::Uuid::new_v4().to_string(),
                actor: bcs_service_api::types::BotOperationActor::Human {
                    user_id: user.id.clone(),
                    effective_actor_id: bot.bot_uuid.clone(),
                },
            },
            (Some(user), _) => BotOperationContext {
                operation_id: uuid::Uuid::new_v4().to_string(),
                actor: bcs_service_api::types::BotOperationActor::Human {
                    user_id: user.id.clone(),
                    effective_actor_id: principal.actor_id().to_string(),
                },
            },
            (None, Principal::Bot(bot)) => BotOperationContext {
                operation_id: uuid::Uuid::new_v4().to_string(),
                actor: bcs_service_api::types::BotOperationActor::Bot {
                    bot_id: bot.bot_uuid.clone(),
                },
            },
            (None, Principal::Human(_)) => {
                return Err(ApplicationError::Unauthenticated);
            }
        };
        Ok((principal, context))
    }
}

#[async_trait]
impl SessionService for SessionServiceImpl {
    async fn list(
        &self,
        command: ListSessions,
    ) -> Result<bcs_service_api::application::v1::Page<bcs_service_api::application::v1::session::SessionSummary>, ApplicationError> {
        self.list_sessions(command).await
    }

    async fn get(
        &self,
        query: GetSession,
    ) -> Result<SessionDetail, ApplicationError> {
        self.get_session(query).await
    }

    async fn create(
        &self,
        command: CreateSession,
    ) -> Result<CreateSessionOutcome, ApplicationError> {
        // Mixed Human+Bot identity: the async authority check verifies the
        // User may act as the authenticated Bot (live role facts, not the
        // signed owner_id claim); the resolved acting identity then drives
        // the launch, whose store audit row carries the authenticated
        // operator plus the resolved creator as the effective actor.
        let session_caller = self
            .resolve_launch_caller(&command.caller)
            .await?;
        // The VERIFIED Human operator rides along so the create-lane audit
        // keeps the dual identity (spec §12.1(6)): present for pure-Human
        // and mixed Human+Bot callers, None for Bot-only launches.
        let operator_user_id = command
            .caller
            .user
            .as_ref()
            .map(|user| user.id.clone());
        let outcome = self
            .launch
            .create(CreateSessionLaunch {
                request: SessionLaunchRequest {
                    caller: session_caller,
                    operator_user_id,
                    group_id: command.group_id,
                    requested_creator: command.acting_bot_id,
                    title: command.title,
                    kind: command.kind,
                    input: command.input,
                    meta: command.meta,
                    public_creator_role: command.creator_role.map(RequestedSessionRole::from),
                    human_message_view_scope: command.message_view_scope,
                    context_delivery: command.context_delivery,
                },
            })
            .await
            .map_err(map_launch_error)?;
        let detail = self
            .project_detail(&outcome.session, outcome.state_machine_run)
            .await?;
        Ok(CreateSessionOutcome {
            session: detail,
            created: outcome.created,
        })
    }

    async fn update(
        &self,
        command: UpdateSession,
    ) -> Result<SessionDetail, ApplicationError> {
        let (principal, context) = self.authorized_context(&command.caller).await?;
        // Only `title` is mutable in phase one; a request carrying no field is
        // rejected (mirrors the sibling Group V1 facade).
        if command.title.is_none() {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "at least one mutable field is required",
            ));
        }
        self.load_session_for_manage(&principal, &command.session_id)
            .await?;
        let session = self
            .sessions
            .update_title(&command.session_id, command.title, &context)
            .await
            .map_err(map_session_error)?;
        self.project_detail(&session, None).await
    }

    async fn complete(
        &self,
        command: CompleteSession,
    ) -> Result<SessionCompletionResult, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let context = Self::self_context(&command.caller)?;
        let (session, _) = self
            .load_session_for_manage(&principal, &command.session_id)
            .await?;
        // VaGQN: ServiceInvocation sessions have their own callback/output
        // lifecycle and must not be completed via this V1 endpoint.
        if session.session_kind == SessionKind::ServiceInvocation {
            return Err(ApplicationError::conflict(
                "conflict",
                "Service sessions cannot be completed via this endpoint",
            ));
        }
        // If already Completed, return the stable completed state idempotently
        // without invoking the CAS.
        let completed = if matches!(session.status, DomainSessionStatus::Completed) {
            session
        } else {
            match self
                .sessions
                .complete_if_running(&command.session_id, None, None, &context)
                .await
                .map_err(map_session_error)?
            {
                Some(session) => session,
                None => match self
                    .sessions
                    .get(&command.session_id)
                    .await
                    .map_err(map_session_error)?
                {
                    Some(session) => session,
                    None => {
                        return Err(ApplicationError::not_found(
                            "session_not_found",
                            format!("Session '{}' was not found", command.session_id),
                        ));
                    }
                },
            }
        };
        let completed_at = completed.completed_at.unwrap_or(completed.updated_at);
        Ok(SessionCompletionResult {
            session_id: completed.id,
            status: V1SessionStatus::Completed,
            completed_at,
        })
    }

    async fn collect(
        &self,
        command: CollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        // Spec §8.2/§12.5: the collection mark belongs to the SELECTED
        // participant (the Human's self actor or an owned/managed Bot) while
        // the audit row records the REAL Human operator alongside that
        // effective actor.
        self.load_session_for_collection(
            &command.caller,
            &command.session_id,
            &command.participant,
        )
        .await?;
        let context = Self::operation_context(&command.caller, &command.participant)?;
        self.sessions
            .collect(&command.session_id, &command.participant, &context)
            .await
            .map_err(map_session_error)?;
        Ok(SessionCollectionResult {
            session_id: command.session_id,
            participant: command.participant,
            collected: true,
        })
    }

    async fn uncollect(
        &self,
        command: UncollectSession,
    ) -> Result<SessionCollectionResult, ApplicationError> {
        self.load_session_for_collection(
            &command.caller,
            &command.session_id,
            &command.participant,
        )
        .await?;
        let context = Self::operation_context(&command.caller, &command.participant)?;
        self.sessions
            .uncollect(&command.session_id, &command.participant, &context)
            .await
            .map_err(map_session_error)?;
        Ok(SessionCollectionResult {
            session_id: command.session_id,
            participant: command.participant,
            collected: false,
        })
    }

    async fn add_participant(
        &self,
        command: AddSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        let context = Self::self_context(&command.caller)?;
        let principal = require_human(&command.caller)?;
        let (session, group) = self
            .load_session_for_manage(&principal, &command.session_id)
            .await?;
        // VSN7B: the added Bot must be collaboration-eligible from the caller
        // OR from the parent Group's driver/originator.
        self.ensure_collaboration_eligible(&principal, &command.bot_uuid, "bot_uuid", &group)
            .await?;
        // VfhG3: explicit 409 when the target Bot is already a participant.
        if session
            .participants
            .iter()
            .any(|p| p.bot_uuid == command.bot_uuid)
        {
            return Err(ApplicationError::conflict(
                "participant_already_exists",
                format!(
                    "Bot '{}' is already a participant of Session '{}'",
                    command.bot_uuid, command.session_id
                ),
            ));
        }
        let target = self
            .registry
            .try_get(&command.bot_uuid)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "actor_not_found",
                    format!("Actor '{}' was not found", command.bot_uuid),
                )
            })?;
        let mode = ParticipantMode::default_for(target.actor_kind);
        let group_participant = group
            .participants
            .iter()
            .find(|p| p.bot_uuid == command.bot_uuid);
        let role = group_participant.map(|p| p.role).unwrap_or_else(|| {
            if target.actor_kind == ActorKind::Human {
                ParticipantRole::Observer
            } else {
                match group.group_strategy {
                    GroupStrategy::ManagerWorker => ParticipantRole::Worker,
                    _ => ParticipantRole::Consultant,
                }
            }
        });
        let tags = group_participant
            .map(|participant| participant.tags.clone())
            .unwrap_or_default();
        let message_view_scope = command
            .message_view_scope
            .or_else(|| group_participant.map(|participant| participant.message_view_scope))
            .unwrap_or(MessageViewScope::Full);
        if !message_view_scope.is_valid_for(target.actor_kind) {
            return Err(ApplicationError::invalid(
                "invalid_message_view_scope",
                "Bot participants must use full message_view_scope",
            ));
        }
        let participant = Participant {
            bot_uuid: command.bot_uuid.clone(),
            bot_name: None,
            kind: None,
            role,
            actor_kind: target.actor_kind,
            mode: Some(mode),
            tags,
            message_view_scope,
        };
        let mut updated = self
            .sessions
            .add_participant(&command.session_id, participant, &context)
            .await
            .map_err(map_session_error)?;
        // Emit a `BotJoined` system message best-effort; a dispatch failure is
        // logged and never surfaces to the caller.
        if let Some(actor) = updated
            .participants
            .iter()
            .find(|p| p.bot_uuid == command.bot_uuid)
            .cloned()
        {
            let event = SystemMessageEvent::BotJoined {
                group_id: updated.group_id.clone(),
                actor,
                session_id: command.session_id.clone(),
                session_input: updated.input.clone(),
            };
            if let Err(error) = self
                .system_message
                .notify(
                    &updated.group_id,
                    event,
                    &command.session_id,
                    &updated.participants,
                )
                .await
            {
                tracing::warn!(
                    session_id = %command.session_id,
                    bot_uuid = %command.bot_uuid,
                    error = %error,
                    "notify bot joined failed"
                );
            }
        }
        self.backfill_and_project_participant(&mut updated.participants, &command.bot_uuid)
            .await
    }

    async fn update_participant(
        &self,
        command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        if command.mode.is_none() && command.message_view_scope.is_none() {
            return Err(ApplicationError::invalid(
                "empty_participant_update",
                "At least one of mode or message_view_scope must be provided",
            ));
        }
        let context = Self::self_context(&command.caller)?;
        let principal = require_human(&command.caller)?;
        let session = self.load_session(&command.session_id).await?;
        let group = self.load_group(&session.group_id).await?;
        let existing = session
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == command.bot_uuid)
            .cloned();
        if existing.is_none() {
            if command.bot_uuid.starts_with("human_") {
                self.authorize_human_self_mode_update(&principal, &session, &command.bot_uuid)
                    .await?;
                if let Some(mode) = command.mode {
                    Self::validate_participant_mode(mode, ActorKind::Human)?;
                }
                let group_scope = group
                    .participants
                    .iter()
                    .find(|participant| participant.bot_uuid == command.bot_uuid)
                    .map(|participant| participant.message_view_scope);
                let message_view_scope = command
                    .message_view_scope
                    .or(group_scope)
                    .unwrap_or(MessageViewScope::Full);
                let mut participant =
                    Participant::human(&command.bot_uuid, ParticipantRole::Observer);
                participant.mode = Some(
                    command
                        .mode
                        .unwrap_or_else(|| ParticipantMode::default_for(ActorKind::Human)),
                );
                participant.message_view_scope = message_view_scope;
                // Initial membership creation cannot have an existing
                // participant-bound Session connection to invalidate.
                let mut updated = self
                    .sessions
                    .add_participant(&command.session_id, participant, &context)
                    .await
                    .map_err(map_session_error)?;
                if let Some(mode) = command.mode {
                    let actor_name = self
                        .registry
                        .try_get(&command.bot_uuid)
                        .await
                        .map_err(map_service_error)?
                        .and_then(|bot| bot.capabilities.name)
                        .unwrap_or_else(|| command.bot_uuid.clone());
                    let event = SystemMessageEvent::ParticipantModeChanged {
                        group_id: updated.group_id.clone(),
                        actor_id: command.bot_uuid.clone(),
                        actor_name,
                        actor_kind: ActorKind::Human,
                        from: None,
                        to: mode,
                    };
                    if let Err(error) = self
                        .system_message
                        .notify(
                            &updated.group_id,
                            event,
                            &command.session_id,
                            &updated.participants,
                        )
                        .await
                    {
                        tracing::warn!(
                            session_id = %command.session_id,
                            bot_uuid = %command.bot_uuid,
                            error = %error,
                            "notify participant mode changed failed"
                        );
                    }
                }
                return self
                    .backfill_and_project_participant(&mut updated.participants, &command.bot_uuid)
                    .await;
            }
            if !self
                .can_manage_session(&principal, &session, &group)
                .await?
            {
                return Err(ApplicationError::forbidden(
                    "Principal may not manage this Session",
                ));
            }
            return Err(ApplicationError::not_found(
                "participant_not_found",
                format!(
                    "Participant '{}' not found in Session '{}'",
                    command.bot_uuid, command.session_id
                ),
            ));
        }
        let existing = existing.expect("checked above");
        let old_mode = existing.mode;
        let actor_kind = existing.actor_kind;
        let actor_name = self
            .registry
            .try_get(&command.bot_uuid)
            .await
            .map_err(map_service_error)?
            .and_then(|bot| bot.capabilities.name)
            .unwrap_or_else(|| command.bot_uuid.clone());
        if let Some(mode) = command.mode {
            match actor_kind {
                ActorKind::Human => {
                    self.authorize_human_self_mode_update(&principal, &session, &command.bot_uuid)
                        .await?;
                }
                ActorKind::Bot => {
                    if !self
                        .can_manage_session(&principal, &session, &group)
                        .await?
                    {
                        return Err(ApplicationError::forbidden(
                            "Principal may not manage this Session",
                        ));
                    }
                }
            }
            Self::validate_participant_mode(mode, actor_kind)?;
        }
        if let Some(message_view_scope) = command.message_view_scope {
            if !message_view_scope.is_valid_for(actor_kind) {
                return Err(ApplicationError::invalid(
                    "invalid_message_view_scope",
                    "Bot participants must use full message_view_scope",
                ));
            }
            match actor_kind {
                ActorKind::Human if principal.actor_id() == command.bot_uuid => {
                    self.authorize_human_self_mode_update(&principal, &session, &command.bot_uuid)
                        .await?;
                }
                _ if !self
                    .can_manage_session(&principal, &session, &group)
                    .await? =>
                {
                    return Err(ApplicationError::forbidden_code(
                        "message_view_scope_forbidden",
                        "A Human may only update its own scope unless it manages the Session",
                    ));
                }
                _ => {}
            }
        }
        let mut updated = session;
        if let Some(mode) = command.mode
            && command.message_view_scope.is_none()
        {
            updated = self
                .sessions
                .update_participant_mode(
                    &command.session_id,
                    &command.bot_uuid,
                    mode,
                    &context,
                )
                .await
                .map_err(map_session_error)?;
            if old_mode != Some(mode) {
                let event = SystemMessageEvent::ParticipantModeChanged {
                    group_id: updated.group_id.clone(),
                    actor_id: command.bot_uuid.clone(),
                    actor_name: actor_name.clone(),
                    actor_kind,
                    from: old_mode,
                    to: mode,
                };
                if let Err(error) = self
                    .system_message
                    .notify(
                        &updated.group_id,
                        event,
                        &command.session_id,
                        &updated.participants,
                    )
                    .await
                {
                    tracing::warn!(
                        session_id = %command.session_id,
                        bot_uuid = %command.bot_uuid,
                        error = %error,
                        "notify participant mode changed failed"
                    );
                }
            }
        }
        if let Some(message_view_scope) = command.message_view_scope {
            let lease = if actor_kind == ActorKind::Human
                && existing.message_view_scope != message_view_scope
            {
                Some(
                    self.participant_view_bindings
                        .begin_scope_change(&command.session_id, &command.bot_uuid)
                        .await
                        .map_err(map_service_error)?,
                )
            } else {
                None
            };
            let update_result = self
                .sessions
                .update_participant_mode_and_message_view_scope(
                    &command.session_id,
                    &command.bot_uuid,
                    command.mode,
                    message_view_scope,
                    &context,
                )
                .await
                .map_err(map_session_error);
            let release_result = if let Some(lease) = lease {
                self.participant_view_bindings
                    .finish_scope_change(lease)
                    .await
                    .map_err(map_service_error)
            } else {
                Ok(())
            };
            updated = update_result?;
            release_result?;
            if let Some(mode) = command.mode
                && old_mode != Some(mode)
            {
                let event = SystemMessageEvent::ParticipantModeChanged {
                    group_id: updated.group_id.clone(),
                    actor_id: command.bot_uuid.clone(),
                    actor_name,
                    actor_kind,
                    from: old_mode,
                    to: mode,
                };
                if let Err(error) = self
                    .system_message
                    .notify(
                        &updated.group_id,
                        event,
                        &command.session_id,
                        &updated.participants,
                    )
                    .await
                {
                    tracing::warn!(
                        session_id = %command.session_id,
                        bot_uuid = %command.bot_uuid,
                        error = %error,
                        "notify participant mode changed failed"
                    );
                }
            }
        }
        match self
            .backfill_and_project_participant(&mut updated.participants, &command.bot_uuid)
            .await
        {
            Ok(participant) => Ok(participant),
            Err(ApplicationError::Internal(_)) => Err(ApplicationError::not_found(
                "participant_not_found",
                format!(
                    "Participant '{}' not found in Session '{}'",
                    command.bot_uuid, command.session_id
                ),
            )),
            Err(other) => Err(other),
        }
    }

    async fn delete_participant(
        &self,
        command: DeleteSessionParticipant,
    ) -> Result<DeleteResult, ApplicationError> {
        let context = Self::self_context(&command.caller)?;
        let principal = require_human(&command.caller)?;
        // Design §8.7 parity with the Group facade: the target Actor may
        // leave the Session (self-service delete) without session-manage
        // authorization. The legacy removal path still rejects removing the
        // group driver or the ManagerWorker Manager.
        let is_self = self
            .principal_can_act_as(&principal, &command.bot_uuid)
            .await?;
        let session = if is_self {
            self.load_session(&command.session_id).await?
        } else {
            self.load_session_for_manage(&principal, &command.session_id)
                .await?
                .0
        };
        // Capture the participant being removed so the `BotLeft` event
        // carries its accurate identity.
        let removed = session
            .participants
            .iter()
            .find(|p| p.bot_uuid == command.bot_uuid)
            .cloned();
        // Idempotent: a missing participant returns `deleted: false`.
        if removed.is_none() {
            return Ok(DeleteResult { deleted: false });
        }
        let mut updated = self
            .sessions
            .remove_participant(&command.session_id, &command.bot_uuid, &context)
            .await
            .map_err(map_session_error)?;
        if let Some(actor) = removed {
            let event = SystemMessageEvent::BotLeft {
                group_id: updated.group_id.clone(),
                actor,
            };
            if let Err(error) = self
                .system_message
                .notify(
                    &updated.group_id,
                    event,
                    &command.session_id,
                    &updated.participants,
                )
                .await
            {
                tracing::warn!(
                    session_id = %command.session_id,
                    bot_uuid = %command.bot_uuid,
                    error = %error,
                    "notify bot left failed"
                );
            }
        }
        Ok(DeleteResult { deleted: true })
    }

    async fn delete(&self, command: DeleteSession) -> Result<DeleteResult, ApplicationError> {
        let (principal, _context) = self.authorized_context(&command.caller).await?;
        // Idempotent: a missing session yields `deleted: false`; non-managers
        // still get 403.
        let session = match self
            .sessions
            .get(&command.session_id)
            .await
            .map_err(map_session_error)?
        {
            Some(session) => session,
            None => return Ok(DeleteResult { deleted: false }),
        };
        let group = self.load_group(&session.group_id).await?;
        let can_delete = if let Some(requested) = command.acting_bot_id.as_deref() {
            let acting_actor_id = match &principal {
                Principal::Human(_) => {
                    self.resolve_view_actor(&command.caller, Some(requested))
                        .await?
                }
                Principal::Bot(bot) if requested == bot.bot_uuid => requested.to_string(),
                Principal::Bot(_) => {
                    return Err(ApplicationError::forbidden(
                        "The explicit acting Bot must identify the authenticated Bot",
                    ));
                }
            };
            let management_actor_ids = Self::group_management_actor_ids(&group);
            management_actor_ids
                .iter()
                .any(|actor_id| actor_id == &acting_actor_id)
                || session
                    .created_by
                    .as_deref()
                    .is_some_and(|creator| creator == acting_actor_id)
        } else {
            self.can_manage_session(&principal, &session, &group)
                .await?
        };
        if !can_delete {
            return Err(ApplicationError::forbidden(
                "Principal may not delete this Session",
            ));
        }
        let deleted = self
            .sessions
            .delete(&command.session_id)
            .await
            .map_err(map_session_error)?;
        Ok(DeleteResult { deleted })
    }
}
