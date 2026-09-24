//! application implementation.
use super::*;


#[async_trait]
impl SessionManagementService for SessionManagementServiceImpl {
    async fn validate_session_registry(&self) -> Result<(), SessionUseCaseError> {
        self.repo.validate_session_registry().await.map_err(SessionUseCaseError::from)
    }

    async fn ensure_direct_session(&self, id: &str) -> Result<bcs_service_api::port::repo::session_registry::SessionRegistration, SessionUseCaseError> {
        self.repo.ensure_direct_session(id).await.map_err(Into::into)
    }
    async fn session_registration(&self, id: &str) -> Result<Option<bcs_service_api::port::repo::session_registry::SessionRegistration>, SessionUseCaseError> {
        self.repo.session_registration(id).await.map_err(Into::into)
    }

    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        if let Some(sid) = cmd.session_id.as_deref() {
            // Pre-check the existing session status so the HTTP layer can
            // return 409 (legacy `session_is_running_cannot_invoke`,
            // server.rs:12529-12535) for Running sessions, instead of the
            // generic 400 InvalidParams that the repo's `can_reactivate`
            // would surface.
            if let Some(existing) = self.repo.get(sid).await {
                if matches!(existing.status, SessionStatus::Running) {
                    return Err(SessionUseCaseError::Conflict(format!(
                        "session {sid} is running, cannot invoke"
                    )));
                }
            }
            let session = self.repo.reactivate(sid, cmd.params.input.clone()).await?;
            Ok(CreateOrReactivateOutcome {
                session,
                created: false,
            })
        } else {
            self.ensure_manager_worker_accepts_participants(
                &cmd.group_id,
                &cmd.params.participants,
            )
            .await?;
            let group = self.group_repo.try_get(&cmd.group_id).await?;
            let group_is_provisioning = group
                .as_ref()
                .is_some_and(|group| group.record_status == "provisioning");
            let mut params = cmd.params;
            let session = if group_is_provisioning || self.event_record_factory.is_none() {
                self.repo.create(&cmd.group_id, params).await?
            } else {
                let session_id = match params.id.clone() {
                    Some(session_id) => session_id,
                    None => new_session_id(&cmd.group_id)
                        .map_err(|error| SessionUseCaseError::InvalidParams(error.to_string()))?,
                };
                params.id = Some(session_id.clone());
                let mut data = BTreeMap::new();
                data.insert(
                    "session_kind".to_string(),
                    json!(session_kind_name(params.session_kind)),
                );
                data.insert("status".to_string(), json!("running"));
                data.insert("initial".to_string(), json!(false));
                if let Some(created_by) =
                    params.created_by.as_deref().or(params.caller_id.as_deref())
                {
                    data.insert("created_by".to_string(), json!(created_by));
                }
                match self.prepare_event(
                    "session.created",
                    &cmd.group_id,
                    &session_id,
                    "session",
                    &session_id,
                    data,
                )? {
                    Some(event) => {
                        self.repo
                            .create_with_event(CreateSessionWithEvent {
                                group_id: cmd.group_id.clone(),
                                params,
                                event,
                            })
                            .await?
                    }
                    None => self.repo.create(&cmd.group_id, params).await?,
                }
            };
            if let Some(group) = group.as_ref() {
                if let Err(opening_error) =
                    self.persist_session_opening_message(group, &session).await
                {
                    let opening_error_message = opening_error.to_string();
                    let compensation_reason =
                        Some("opening_message_persistence_failed".to_string());
                    let compensation_result = if group_is_provisioning {
                        self.repo
                            .complete_if_running(&session.id, None, compensation_reason)
                            .await
                            .map_err(SessionUseCaseError::from)
                    } else {
                        SessionManagementService::complete_if_running(
                            self,
                            &session.id,
                            None,
                            compensation_reason,
                        )
                        .await
                    };
                    if let Err(compensation_error) = compensation_result {
                        return Err(SessionUseCaseError::Internal(ServiceError::InternalError(
                            format!(
                                "{opening_error_message}; failed to compensate session '{}': {compensation_error}",
                                session.id
                            ),
                        )));
                    }
                    return Err(opening_error);
                }
            }
            Ok(CreateOrReactivateOutcome {
                session,
                created: true,
            })
        }
    }

    async fn get(&self, session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self.repo.get(session_id).await)
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self.repo.belongs_to_group(session_id, group_id).await)
    }

    async fn list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(self
            .repo
            .try_list_by_group(
                group_id,
                status,
                offset,
                limit,
                title_contains,
                participant_id,
            )
            .await?)
    }

    async fn count_running_service(&self, group_id: &str) -> Result<u64, SessionUseCaseError> {
        Ok(self.repo.count_running_service(group_id).await)
    }

    async fn list_running_service(
        &self,
        offset: u64,
        limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(self.repo.list_running_service(offset, limit).await)
    }

    async fn list_running_service_after(
        &self, cursor: Option<&str>, limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(self.repo.list_running_service_after(cursor, limit).await?)
    }

    async fn list_recoverable_callbacks(
        &self,
        now_ms: u64,
        after_session_id: Option<&str>,
        limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(self
            .repo
            .list_recoverable_callbacks(now_ms, after_session_id, limit)
            .await?)
    }

    async fn update_callback_status(
        &self,
        session_id: &str,
        status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(self.repo.update_callback_status(session_id, status).await?)
    }

    async fn claim_callback(
        &self,
        command: ClaimSessionCallbackCommand,
    ) -> Result<Option<ClaimSessionCallbackOutcome>, SessionUseCaseError> {
        if command.expected_activation_count < 1
            || command.lease_owner.is_empty()
            || command.lease_until_ms <= command.now_ms
        {
            return Err(SessionUseCaseError::InvalidParams(
                "callback claim requires a positive activation, non-empty owner, and future lease"
                    .to_string(),
            ));
        }
        Ok(self
            .repo
            .claim_callback(ClaimSessionCallback {
                session_id: command.session_id,
                expected_activation_count: command.expected_activation_count,
                lease_owner: command.lease_owner,
                now_ms: command.now_ms,
                lease_until_ms: command.lease_until_ms,
            })
            .await?
            .map(|claim| ClaimSessionCallbackOutcome {
                lease_token: claim.lease_token,
            }))
    }

    async fn complete_callback(
        &self,
        command: CompleteSessionCallbackCommand,
    ) -> Result<bool, SessionUseCaseError> {
        if !matches!(
            command.terminal_status.as_str(),
            "succeeded" | "partial_failed" | "failed" | "not_applicable"
        ) {
            return Err(SessionUseCaseError::InvalidParams(format!(
                "invalid terminal callback status: {}",
                command.terminal_status
            )));
        }
        Ok(self
            .repo
            .complete_callback(CompleteSessionCallback {
                session_id: command.session_id,
                expected_activation_count: command.expected_activation_count,
                lease_owner: command.lease_owner,
                lease_token: command.lease_token,
                terminal_status: command.terminal_status,
            })
            .await?)
    }

    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<Value>,
        error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        let Some(current) = self.repo.get(session_id).await else {
            return Err(SessionUseCaseError::NotFound(session_id.to_string()));
        };
        if current.status == SessionStatus::Completed {
            return Ok(None);
        }
        self.complete_session(current, output, error, false).await
    }

    async fn complete_running_service_activation(
        &self,
        session_id: &str,
        expected_activation_count: i32,
        output: Option<Value>,
        error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        let Some(current) = self.repo.try_get(session_id).await? else { return Ok(None); };
        if current.status != SessionStatus::Running
            || current.session_kind != SessionKind::ServiceInvocation
            || current.activation_count != expected_activation_count
        {
            return Ok(None);
        }
        self.complete_session(current, output, error, true).await
    }

    async fn add_participant(
        &self,
        session_id: &str,
        participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        let session = self
            .repo
            .get(session_id)
            .await
            .ok_or_else(|| SessionUseCaseError::NotFound(session_id.to_string()))?;
        self.ensure_manager_worker_accepts_participants(
            &session.group_id,
            std::slice::from_ref(&participant),
        )
        .await?;
        if session
            .participants
            .iter()
            .any(|existing| existing.bot_uuid == participant.bot_uuid)
        {
            return Ok(session);
        }
        let mut data = BTreeMap::new();
        data.insert("actor_id".to_string(), json!(participant.bot_uuid));
        data.insert(
            "actor_type".to_string(),
            json!(actor_kind_name(participant.actor_kind)),
        );
        data.insert(
            "role".to_string(),
            json!(participant_role_name(participant.role)),
        );
        data.insert(
            "mode".to_string(),
            json!(participant_mode_name(participant.effective_mode())),
        );
        data.insert(
            "visible_from_seq".to_string(),
            json!(session.current_msg_seq),
        );
        match self.prepare_event(
            "session.participant.added",
            &session.group_id,
            session_id,
            "participant",
            &participant.bot_uuid,
            data,
        )? {
            Some(event) => Ok(self
                .repo
                .add_participant_with_event(AddSessionParticipantWithEvent {
                    session_id: session_id.to_string(),
                    expected_participants: session.participants,
                    participant,
                    event,
                })
                .await?),
            None => Ok(self.repo.add_participant(session_id, participant).await?),
        }
    }

    async fn remove_participant(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        let session = self
            .repo
            .get(session_id)
            .await
            .ok_or_else(|| SessionUseCaseError::NotFound(session_id.to_string()))?;

        if let Some(group) = self.group_repo.get(&session.group_id).await {
            // The group driver (and, for ManagerWorker, the Manager) is
            // structurally required and cannot be removed from a session. The
            // group originator/coordinator, however, may leave a session —
            // session membership is session-scoped and does not affect the
            // group's coordination structure.
            if bot_uuid == group.driver_bot {
                return Err(SessionUseCaseError::InvalidParams(
                    "Cannot remove the group driver from a session".to_string(),
                ));
            }

            if group.group_strategy == GroupStrategy::ManagerWorker {
                if let Some(manager) = group
                    .participants
                    .iter()
                    .find(|p| p.role == ParticipantRole::Manager)
                {
                    if bot_uuid == manager.bot_uuid {
                        return Err(SessionUseCaseError::InvalidParams(
                            "Cannot remove the Manager bot from a ManagerWorker session"
                                .to_string(),
                        ));
                    }
                }
            }
        }

        let removed = session
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == bot_uuid)
            .cloned()
            .ok_or_else(|| {
                SessionUseCaseError::InvalidParams(format!(
                    "participant {bot_uuid} not in session {session_id}"
                ))
            })?;
        let mut data = BTreeMap::new();
        data.insert("actor_id".to_string(), json!(bot_uuid));
        data.insert(
            "actor_type".to_string(),
            json!(actor_kind_name(removed.actor_kind)),
        );
        data.insert(
            "previous_role".to_string(),
            json!(participant_role_name(removed.role)),
        );
        data.insert("reason".to_string(), json!("removed"));
        match self.prepare_event(
            "session.participant.removed",
            &session.group_id,
            session_id,
            "participant",
            bot_uuid,
            data,
        )? {
            Some(event) => Ok(self
                .repo
                .remove_participant_with_event(RemoveSessionParticipantWithEvent {
                    session_id: session_id.to_string(),
                    expected_participants: session.participants,
                    bot_uuid: bot_uuid.to_string(),
                    event,
                })
                .await?),
            None => Ok(self.repo.remove_participant(session_id, bot_uuid).await?),
        }
    }

    async fn update_participant_mode(
        &self,
        session_id: &str,
        bot_uuid: &str,
        mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        Ok(self
            .repo
            .update_participant_mode(session_id, bot_uuid, mode)
            .await?)
    }

    async fn update_participant_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        message_view_scope: MessageViewScope,
    ) -> Result<Session, SessionUseCaseError> {
        self.update_participant_mode_and_message_view_scope(
            session_id,
            actor_id,
            None,
            message_view_scope,
        )
        .await
    }

    async fn update_participant_mode_and_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        mode: Option<ParticipantMode>,
        message_view_scope: MessageViewScope,
    ) -> Result<Session, SessionUseCaseError> {
        let session = self
            .repo
            .get(session_id)
            .await
            .ok_or_else(|| SessionUseCaseError::NotFound(session_id.to_string()))?;
        let participant = session
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == actor_id)
            .ok_or_else(|| {
                SessionUseCaseError::InvalidParams(format!(
                    "participant {actor_id} not in session {session_id}"
                ))
            })?;
        if !message_view_scope.is_valid_for(participant.actor_kind) {
            return Err(SessionUseCaseError::InvalidParams(
                "Bot participants must use full message_view_scope".to_string(),
            ));
        }
        if mode.is_some_and(|mode| !mode.is_valid_for(participant.actor_kind)) {
            return Err(SessionUseCaseError::InvalidParams(
                "Participant mode is invalid for the actor kind".to_string(),
            ));
        }
        let previous_scope = participant.message_view_scope;
        let scope_changed = previous_scope != message_view_scope;
        let mode_changed = mode.is_some_and(|mode| participant.effective_mode() != mode);
        if !scope_changed && !mode_changed {
            return Ok(session);
        }
        if !scope_changed {
            return Ok(self
                .repo
                .update_participant_mode(
                    session_id,
                    actor_id,
                    mode.expect("mode differs when scope is unchanged"),
                )
                .await?);
        }
        let mut data = BTreeMap::new();
        data.insert("actor_id".to_string(), json!(actor_id));
        data.insert("from_scope".to_string(), json!(previous_scope));
        data.insert("to_scope".to_string(), json!(message_view_scope));
        match self.prepare_event(
            "session.participant.message_view_scope_changed",
            &session.group_id,
            session_id,
            "participant",
            actor_id,
            data,
        )? {
            Some(event) => Ok(self
                .repo
                .update_participant_message_view_scope_with_event(
                    UpdateSessionParticipantMessageViewScopeWithEvent {
                        session_id: session_id.to_string(),
                        expected_participants: session.participants,
                        actor_id: actor_id.to_string(),
                        message_view_scope,
                        mode,
                        event,
                    },
                )
                .await?),
            None => Ok(self
                .repo
                .update_participant_mode_and_message_view_scope(
                    session_id,
                    actor_id,
                    mode,
                    message_view_scope,
                )
                .await?),
        }
    }

    async fn update_title(
        &self,
        session_id: &str,
        title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        Ok(self.repo.update_title(session_id, title).await?)
    }

    async fn list_group_ids_by_session_participant(
        &self,
        bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(self
            .repo
            .try_list_group_ids_by_session_participant(bot_uuid)
            .await?)
    }

    async fn delete(&self, session_id: &str) -> Result<bool, SessionUseCaseError> {
        Ok(self.repo.delete(session_id).await?)
    }

    async fn collect(&self, session_id: &str, bot_uuid: &str) -> Result<(), SessionUseCaseError> {
        if self.repo.get(session_id).await.is_none() {
            return Err(SessionUseCaseError::NotFound(session_id.to_string()));
        }
        self.repo.collect(session_id, bot_uuid).await?;
        Ok(())
    }

    async fn uncollect(&self, session_id: &str, bot_uuid: &str) -> Result<(), SessionUseCaseError> {
        if self.repo.get(session_id).await.is_none() {
            return Err(SessionUseCaseError::NotFound(session_id.to_string()));
        }
        self.repo.uncollect(session_id, bot_uuid).await?;
        Ok(())
    }

    async fn list_collected_by_group(
        &self,
        group_id: &str,
        bot_uuid: &str,
        status: Option<SessionStatus>,
        title_contains: Option<&str>,
        offset: u64,
        limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(self
            .repo
            .list_collected_by_group(group_id, bot_uuid, status, title_contains, offset, limit)
            .await)
    }

    async fn collected_at_map(
        &self,
        session_ids: &[&str],
        bot_uuid: &str,
    ) -> Result<Vec<(String, u64)>, SessionUseCaseError> {
        Ok(self.repo.collected_at_map(session_ids, bot_uuid).await)
    }
}



pub(super) fn session_kind_name(kind: SessionKind) -> &'static str {
    match kind {
        SessionKind::Chat => "chat",
        SessionKind::ServiceInvocation => "service_invocation",
    }
}



pub(super) fn actor_kind_name(kind: ActorKind) -> &'static str {
    match kind {
        ActorKind::Bot => "bot",
        ActorKind::Human => "human",
    }
}



pub(super) fn participant_role_name(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}



pub(super) fn participant_mode_name(mode: ParticipantMode) -> &'static str {
    match mode {
        ParticipantMode::Auto => "auto",
        ParticipantMode::Muted => "muted",
        ParticipantMode::Present => "present",
        ParticipantMode::Absent => "absent",
    }
}
