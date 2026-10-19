use super::*;

impl SessionServiceImpl {
    pub(super) async fn update_participant_impl(
        &self,
        command: UpdateSessionParticipant,
    ) -> Result<SessionParticipant, ApplicationError> {
        if command.mode.is_none() && command.message_view_scope.is_none() {
            return Err(ApplicationError::invalid(
                "empty_participant_update",
                "At least one of mode or message_view_scope must be provided",
            ));
        }
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
                    .add_participant(&command.session_id, participant)
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
                    // Target ownership permits this mode change only, not
                    // Session management or message-view scope changes below.
                    if !self
                        .can_manage_session(&principal, &session, &group)
                        .await?
                        && !self.principal_can_act_as(&principal, &command.bot_uuid).await?
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
                .update_participant_mode(&command.session_id, &command.bot_uuid, mode)
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
}
