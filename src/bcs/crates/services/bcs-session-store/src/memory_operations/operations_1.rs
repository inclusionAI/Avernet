//! Persistence operations separated from the repository port forwarding.
use super::*;

impl MemorySessionRepo {
    pub(super) async fn repo_create(&self, group_id: &str, params: NewSessionParams) -> ServiceResult<Session> {
        let mut registry = self.registry.entries.lock().await;
        let now = now_ms();

        // Explicit id path: used for legacy sessions ({group_id}:00000000) etc.
        if let Some(ref id) = params.id {
            if !validate_session_id(id, group_id) {
                return Err(ServiceError::SessionInvalidParams(format!(
                    "session_id {id} not valid for group {group_id}"
                )));
            }
            let mut st = self.state.write().await;
            if st.sessions.contains_key(id) {
                return Err(ServiceError::SessionInvalidParams(format!(
                    "session {id} already exists"
                )));
            }
            check_group_claim(&registry, &id)?;
            let sess = session_from_params(id.clone(), group_id, &params, now);
            commit_group_claim(&mut registry, &id);
            insert_session(&mut st, sess.clone());
            return Ok(sess);
        }

        // Auto-generated id: retry 3 times to handle the ~0 probability collision.
        for _attempt in 0..3 {
            let id = new_session_id(group_id)
                .map_err(|error| ServiceError::SessionInvalidParams(error.to_string()))?;
            let mut st = self.state.write().await;
            if st.sessions.contains_key(&id) {
                continue;
            }
            check_group_claim(&registry, &id)?;
            let sess = session_from_params(id.clone(), group_id, &params, now);
            commit_group_claim(&mut registry, &id);
            insert_session(&mut st, sess.clone());
            return Ok(sess);
        }

        Err(ServiceError::SessionInvalidParams(
            "session_id collision retry exhausted (3 attempts)".to_string(),
        ))
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_create_with_event(&self, command: CreateSessionWithEvent) -> ServiceResult<Session> {
        let event_store = self.event_store.as_ref().ok_or_else(|| {
            ServiceError::InternalError(
                "Eventful Memory Session creation requires the shared Memory Event Store"
                    .to_string(),
            )
        })?;
        let session_id = command.params.id.clone().ok_or_else(|| {
            ServiceError::SessionInvalidParams(
                "Eventful Session creation requires a preallocated session_id".to_string(),
            )
        })?;
        if !validate_session_id(&session_id, &command.group_id) {
            return Err(ServiceError::SessionInvalidParams(format!(
                "session_id {session_id} not valid for group {}",
                command.group_id
            )));
        }
        let candidate = session_from_params(
            session_id.clone(),
            &command.group_id,
            &command.params,
            now_ms(),
        );
        validate_session_event_scope(&candidate, &command.event)?;
        let mut registry = self.registry.entries.lock().await;
        check_group_claim(&registry, &session_id)?;
        let mut state = self.state.write().await;
        if state.sessions.contains_key(&session_id) {
            return Err(ServiceError::SessionInvalidParams(format!(
                "session {session_id} already exists"
            )));
        }
        event_store
            .commit_business_mutation(&command.event, || {
                commit_group_claim(&mut registry, &session_id);
                insert_session(&mut state, candidate.clone());
                Ok(())
            })
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        Ok(candidate)
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        offset: u64,
        limit: u64,
        title_contains: Option<&str>,
        participant_id: Option<&str>,
    ) -> Vec<Session> {
        let st = self.state.read().await;
        let mut v: Vec<_> = st
            .sessions
            .values()
            .filter(|s| s.group_id == group_id)
            .filter(|s| status.map(|want| s.status == want).unwrap_or(true))
            .filter(|s| {
                title_contains.map_or(true, |q| {
                    s.session_title
                        .as_deref()
                        .unwrap_or("")
                        .to_lowercase()
                        .contains(&q.to_lowercase())
                })
            })
            .filter(|s| {
                participant_id.map_or(true, |pid| s.participants.iter().any(|p| p.bot_uuid == pid))
            })
            .cloned()
            .collect();
        // VSN7M: order by created_at DESC with session_id DESC tie-breaker
        // BEFORE pagination so same-timestamp sessions do not skip/duplicate
        // across pages. The repo owns the deterministic order; the facade's
        // post-pagination sort is now a no-op safety net. DESC tie-break keeps
        // memory consistent with the MySQL ORDER BY s.id DESC tie-break
        // (later-created rows sort first on timestamp ties).
        v.sort_by(|a, b| b.created_at.cmp(&a.created_at).then(b.id.cmp(&a.id)));
        v.into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_complete_if_running_with_event(
        &self,
        command: CompleteSessionWithEvent,
    ) -> ServiceResult<Option<Session>> {
        let event_store = self.event_store.as_ref().ok_or_else(|| {
            ServiceError::InternalError(
                "Eventful Memory Session completion requires the shared Memory Event Store"
                    .to_string(),
            )
        })?;
        let mut state = self.state.write().await;
        let current = state
            .sessions
            .get(&command.session_id)
            .cloned()
            .ok_or_else(|| ServiceError::SessionNotFound(command.session_id.clone()))?;
        if current.status == SessionStatus::Completed {
            return Ok(None);
        }
        if current.activation_count != command.expected_activation_count {
            return Err(ServiceError::Conflict(format!(
                "Session '{}' activation changed during completion",
                command.session_id
            )));
        }
        let mut candidate = current;
        let now = now_ms();
        candidate.status = SessionStatus::Completed;
        candidate.output = command.output;
        candidate.error_message = command.error;
        candidate.updated_at = now;
        candidate.completed_at = Some(now);
        validate_session_event_scope(&candidate, &command.event)?;
        event_store
            .commit_business_mutation(&command.event, || {
                state
                    .sessions
                    .insert(command.session_id.clone(), candidate.clone());
                Ok(())
            })
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        Ok(Some(candidate))
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_reactivate(
        &self,
        session_id: &str,
        new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Session> {
        let now = now_ms();
        let mut st = self.state.write().await;
        let sess = st
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;

        can_reactivate(
            sess.status,
            sess.session_kind,
            sess.callback_status.as_deref(),
        )
        .map_err(|msg| {
            if msg == "callback is still pending" {
                ServiceError::SessionCallbackPending(session_id.to_string())
            } else {
                ServiceError::SessionInvalidParams(format!("{session_id}: {msg}"))
            }
        })?;

        sess.status = SessionStatus::Running;
        sess.output = None;
        sess.error_message = None;
        sess.callback_status = Some("pending".to_string());
        if let Some(i) = new_input {
            sess.input = Some(i);
        }
        sess.activation_count += 1;
        sess.updated_at = now;
        sess.completed_at = None;
        let result = sess.clone();
        let activation_count = sess.activation_count;
        st.callback_leases.insert(
            session_id.to_string(),
            CallbackLeaseState::for_session_kind(SessionKind::ServiceInvocation),
        );
        debug!(session_id = %session_id, activation_count, "Session reactivated");
        Ok(result)
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_reactivate_if_completed_activation(
        &self,
        session_id: &str,
        expected_activation_count: i32,
        new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Option<Session>> {
        let now = now_ms();
        let mut state = self.state.write().await;
        let session = state
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(session_id.to_string()))?;
        if session.status != SessionStatus::Completed
            || session.session_kind != SessionKind::ServiceInvocation
            || session.activation_count != expected_activation_count
        {
            return Ok(None);
        }
        can_reactivate(
            session.status,
            session.session_kind,
            session.callback_status.as_deref(),
        )
        .map_err(|message| {
            if message == "callback is still pending" {
                ServiceError::SessionCallbackPending(session_id.to_string())
            } else {
                ServiceError::SessionInvalidParams(format!("{session_id}: {message}"))
            }
        })?;

        session.status = SessionStatus::Running;
        session.output = None;
        session.error_message = None;
        session.callback_status = Some("pending".to_string());
        if let Some(input) = new_input {
            session.input = Some(input);
        }
        session.activation_count += 1;
        session.updated_at = now;
        session.completed_at = None;
        let result = session.clone();
        let activation_count = session.activation_count;
        state.callback_leases.insert(
            session_id.to_string(),
            CallbackLeaseState::for_session_kind(SessionKind::ServiceInvocation),
        );
        debug!(session_id = %session_id, activation_count, "Session reactivated with CAS");
        Ok(Some(result))
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_add_participant_with_event(
        &self,
        command: AddSessionParticipantWithEvent,
    ) -> ServiceResult<Session> {
        let event_store = self.event_store.as_ref().ok_or_else(|| {
            ServiceError::InternalError(
                "Eventful Memory Session participant addition requires the shared Memory Event Store"
                    .to_string(),
            )
        })?;
        let mut state = self.state.write().await;
        let current = state
            .sessions
            .get(&command.session_id)
            .cloned()
            .ok_or_else(|| ServiceError::SessionNotFound(command.session_id.clone()))?;
        if current
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == command.participant.bot_uuid)
        {
            return Ok(current);
        }
        if serde_json::to_value(&current.participants).ok()
            != serde_json::to_value(&command.expected_participants).ok()
        {
            return Err(ServiceError::Conflict(format!(
                "Session '{}' participants changed during addition",
                command.session_id
            )));
        }
        let mut candidate = current;
        let bot_uuid = command.participant.bot_uuid.clone();
        candidate.participants.push(command.participant);
        candidate.updated_at = now_ms();
        let mut join_map = candidate
            .participant_join_seq
            .as_ref()
            .and_then(serde_json::Value::as_object)
            .cloned()
            .unwrap_or_default();
        join_map.insert(bot_uuid, serde_json::json!(candidate.current_msg_seq));
        candidate.participant_join_seq = Some(serde_json::Value::Object(join_map));
        validate_session_event_scope(&candidate, &command.event)?;
        event_store
            .commit_business_mutation(&command.event, || {
                state
                    .sessions
                    .insert(command.session_id.clone(), candidate.clone());
                Ok(())
            })
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        Ok(candidate)
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_remove_participant_with_event(
        &self,
        command: RemoveSessionParticipantWithEvent,
    ) -> ServiceResult<Session> {
        let event_store = self.event_store.as_ref().ok_or_else(|| {
            ServiceError::InternalError(
                "Eventful Memory Session participant removal requires the shared Memory Event Store"
                    .to_string(),
            )
        })?;
        let mut state = self.state.write().await;
        let current = state
            .sessions
            .get(&command.session_id)
            .cloned()
            .ok_or_else(|| ServiceError::SessionNotFound(command.session_id.clone()))?;
        if serde_json::to_value(&current.participants).ok()
            != serde_json::to_value(&command.expected_participants).ok()
        {
            return Err(ServiceError::Conflict(format!(
                "Session '{}' participants changed during removal",
                command.session_id
            )));
        }
        let mut candidate = current;
        let previous_len = candidate.participants.len();
        candidate
            .participants
            .retain(|participant| participant.bot_uuid != command.bot_uuid);
        if candidate.participants.len() == previous_len {
            return Err(ServiceError::SessionInvalidParams(format!(
                "participant {} not in session {}",
                command.bot_uuid, command.session_id
            )));
        }
        candidate.updated_at = now_ms();
        validate_session_event_scope(&candidate, &command.event)?;
        event_store
            .commit_business_mutation(&command.event, || {
                state
                    .sessions
                    .insert(command.session_id.clone(), candidate.clone());
                state.collected.retain(|key, _| {
                    !(key.0 == command.session_id && key.1 == command.bot_uuid)
                });
                Ok(())
            })
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        Ok(candidate)
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_update_participant_message_view_scope_with_event(
        &self,
        command: UpdateSessionParticipantMessageViewScopeWithEvent,
    ) -> ServiceResult<Session> {
        let event_store = self.event_store.as_ref().ok_or_else(|| {
            ServiceError::InternalError(
                "Eventful Memory Session participant scope update requires the shared Memory Event Store"
                    .to_string(),
            )
        })?;
        let mut state = self.state.write().await;
        let current = state
            .sessions
            .get(&command.session_id)
            .cloned()
            .ok_or_else(|| ServiceError::SessionNotFound(command.session_id.clone()))?;
        if serde_json::to_value(&current.participants).ok()
            != serde_json::to_value(&command.expected_participants).ok()
        {
            return Err(ServiceError::Conflict(format!(
                "Session '{}' participants changed during scope update",
                command.session_id
            )));
        }
        let mut candidate = current;
        let participant = candidate
            .participants
            .iter_mut()
            .find(|participant| participant.bot_uuid == command.actor_id)
            .ok_or_else(|| {
                ServiceError::SessionInvalidParams(format!(
                    "participant {} not in session {}",
                    command.actor_id, command.session_id
                ))
            })?;
        if !command
            .message_view_scope
            .is_valid_for(participant.actor_kind)
        {
            return Err(ServiceError::SessionInvalidParams(
                "Bot participants must use full message_view_scope".to_string(),
            ));
        }
        participant.message_view_scope = command.message_view_scope;
        if let Some(mode) = command.mode {
            if !mode.is_valid_for(participant.actor_kind) {
                return Err(ServiceError::SessionInvalidParams(
                    "Participant mode is invalid for the actor kind".to_string(),
                ));
            }
            participant.mode = Some(mode);
        }
        candidate.updated_at = now_ms();
        validate_session_event_scope(&candidate, &command.event)?;
        event_store
            .commit_business_mutation(&command.event, || {
                state
                    .sessions
                    .insert(command.session_id.clone(), candidate.clone());
                Ok(())
            })
            .await
            .map_err(|error| ServiceError::InternalError(error.to_string()))?;
        Ok(candidate)
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_claim_callback(
        &self,
        command: ClaimSessionCallback,
    ) -> ServiceResult<Option<SessionCallbackClaim>> {
        let mut state = self.state.write().await;
        let session = state
            .sessions
            .get(&command.session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(command.session_id.clone()))?;
        if session.status != SessionStatus::Completed
            || session.session_kind != SessionKind::ServiceInvocation
            || session.activation_count != command.expected_activation_count
            || session.callback_status.as_deref() != Some("pending")
        {
            return Ok(None);
        }
        let Some(lease) = state.callback_leases.get_mut(&command.session_id) else {
            return Ok(None);
        };
        let Some(current_token) = lease.token else {
            return Ok(None);
        };
        if lease
            .until_ms
            .is_some_and(|until_ms| until_ms > command.now_ms)
        {
            return Ok(None);
        }
        let next_token = current_token.checked_add(1).ok_or_else(|| {
            ServiceError::InternalError("Session callback lease token overflow".to_string())
        })?;
        lease.token = Some(next_token);
        lease.owner = Some(command.lease_owner);
        lease.until_ms = Some(command.lease_until_ms);
        Ok(Some(SessionCallbackClaim {
            lease_token: next_token,
        }))
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_complete_callback(&self, command: CompleteSessionCallback) -> ServiceResult<bool> {
        if !matches!(
            command.terminal_status.as_str(),
            "succeeded" | "partial_failed" | "failed" | "not_applicable"
        ) {
            return Err(ServiceError::SessionInvalidParams(format!(
                "invalid terminal callback status: {}",
                command.terminal_status
            )));
        }
        let mut state = self.state.write().await;
        let session = state
            .sessions
            .get(&command.session_id)
            .ok_or_else(|| ServiceError::SessionNotFound(command.session_id.clone()))?;
        if session.status != SessionStatus::Completed
            || session.session_kind != SessionKind::ServiceInvocation
            || session.activation_count != command.expected_activation_count
            || session.callback_status.as_deref() != Some("pending")
        {
            return Ok(false);
        }
        let Some(lease) = state.callback_leases.get(&command.session_id) else {
            return Ok(false);
        };
        if lease.token != Some(command.lease_token)
            || lease.owner.as_deref() != Some(command.lease_owner.as_str())
        {
            return Ok(false);
        }
        let session = state
            .sessions
            .get_mut(&command.session_id)
            .expect("Session existence checked above");
        session.callback_status = Some(command.terminal_status);
        session.updated_at = now_ms();
        let lease = state
            .callback_leases
            .get_mut(&command.session_id)
            .expect("Callback lease existence checked above");
        lease.owner = None;
        lease.until_ms = None;
        Ok(true)
    }
}

impl MemorySessionRepo {
    pub(super) async fn repo_list_collected_by_group(
        &self,
        group_id: &str,
        bot_uuid: &str,
        status: Option<SessionStatus>,
        title_contains: Option<&str>,
        offset: u64,
        limit: u64,
    ) -> Vec<Session> {
        let st = self.state.read().await;
        let q = title_contains.map(|s| s.to_ascii_lowercase());
        let mut v: Vec<_> = st
            .sessions
            .values()
            .filter(|s| s.group_id == group_id)
            .filter(|s| {
                st.collected
                    .contains_key(&(s.id.clone(), bot_uuid.to_string()))
            })
            .filter(|s| status.map(|want| s.status == want).unwrap_or(true))
            .filter(|s| {
                q.as_ref().map_or(true, |q| {
                    s.session_title
                        .as_deref()
                        .unwrap_or("")
                        .to_ascii_lowercase()
                        .contains(q)
                })
            })
            .cloned()
            .map(|mut s| {
                // Surface the collect-event time on the returned session; fall
                // back to created_at (COALESCE semantics) for Ordering safety.
                s.collected_at = st
                    .collected
                    .get(&(s.id.clone(), bot_uuid.to_string()))
                    .copied()
                    .or(Some(s.created_at));
                s
            })
            .collect();

        v.sort_by(|a, b| {
            let ka = a.collected_at.unwrap_or(a.created_at);
            let kb = b.collected_at.unwrap_or(b.created_at);
            kb.cmp(&ka).then(b.id.cmp(&a.id))
        });
        v.into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect()
    }
}
