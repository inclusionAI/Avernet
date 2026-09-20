use super::*;

#[derive(Default)]
pub(super) struct RecordingSessionRepo {
    pub(super) next: AtomicU64,
    pub(super) sessions: Mutex<HashMap<String, Session>>,
    pub(super) added_participants: Mutex<Vec<(String, Participant)>>,
    pub(super) fail_create: Mutex<Option<String>>,
    pub(super) fail_get: Mutex<Option<String>>,
    pub(super) fail_add_participant: Mutex<Option<String>>,
}

#[async_trait]
impl SessionRepoPort for RecordingSessionRepo {
    async fn create(&self, group_id: &str, params: NewSessionParams) -> ServiceResult<Session> {
        if let Some(error) = self.fail_create.lock().await.clone() {
            return Err(ServiceError::InternalError(error));
        }
        let n = self.next.fetch_add(1, Ordering::SeqCst) + 1;
        let id = match params.id {
            Some(id) => id,
            None => format!("{group_id}:{n:08x}"),
        };
        let session = Session {
            id: id.clone(),
            group_id: group_id.to_string(),
            session_title: params.session_title,
            env: None,
            status: SessionStatus::Running,
            session_kind: params.session_kind,
            participants: params.participants,
            group_version: params.group_version,
            caller_id: params.caller_id,
            input: params.input,
            output: None,
            error_message: None,
            callback_status: None,
            activation_count: 1,
            message_visibility_version: params.message_visibility_version,
            caller_principal: params.caller_principal,
            created_by: params.created_by,
            current_msg_seq: 0,
            participant_join_seq: None,
            created_at: 1,
            updated_at: 1,
            completed_at: None,
            meta: params.meta,
            collected_at: None,
        };
        self.sessions.lock().await.insert(id, session.clone());
        Ok(session)
    }

    async fn get(&self, session_id: &str) -> Option<Session> {
        self.sessions.lock().await.get(session_id).cloned()
    }

    async fn try_get(&self, session_id: &str) -> ServiceResult<Option<Session>> {
        if let Some(error) = self.fail_get.lock().await.clone() {
            return Err(ServiceError::InternalError(error));
        }
        Ok(self.get(session_id).await)
    }

    async fn belongs_to_group(&self, session_id: &str, group_id: &str) -> bool {
        self.get(session_id)
            .await
            .is_some_and(|session| session.group_id == group_id)
    }

    async fn list_by_group(
        &self,
        group_id: &str,
        status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Vec<Session> {
        self.sessions
            .lock()
            .await
            .values()
            .filter(|session| session.group_id == group_id)
            .filter(|session| status.is_none_or(|status| session.status == status))
            .cloned()
            .collect()
    }

    async fn latest_running(&self, group_id: &str) -> Option<Session> {
        self.list_by_group(group_id, Some(SessionStatus::Running), 0, 1, None, None)
            .await
            .into_iter()
            .next()
    }

    async fn count_running_service(&self, group_id: &str) -> u64 {
        self.list_by_group(
            group_id,
            Some(SessionStatus::Running),
            0,
            u64::MAX,
            None,
            None,
        )
        .await
        .into_iter()
        .filter(|session| session.session_kind == SessionKind::ServiceInvocation)
        .count() as u64
    }

    async fn list_running_service(&self, _offset: u64, _limit: u64) -> Vec<Session> {
        self.sessions
            .lock()
            .await
            .values()
            .filter(|session| {
                session.status == SessionStatus::Running
                    && session.session_kind == SessionKind::ServiceInvocation
            })
            .cloned()
            .collect()
    }

    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> ServiceResult<Option<Session>> {
        let mut sessions = self.sessions.lock().await;
        let Some(session) = sessions.get_mut(session_id) else {
            return Err(ServiceError::SessionNotFound(session_id.to_string()));
        };
        if session.status == SessionStatus::Completed {
            return Ok(None);
        }
        session.status = SessionStatus::Completed;
        session.output = output;
        session.error_message = error;
        Ok(Some(session.clone()))
    }

    async fn reactivate(
        &self,
        session_id: &str,
        new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Session> {
        let mut sessions = self.sessions.lock().await;
        let Some(session) = sessions.get_mut(session_id) else {
            return Err(ServiceError::SessionNotFound(session_id.to_string()));
        };
        session.status = SessionStatus::Running;
        session.input = new_input;
        Ok(session.clone())
    }

    async fn add_participant(
        &self,
        session_id: &str,
        participant: Participant,
    ) -> ServiceResult<Session> {
        if let Some(error) = self.fail_add_participant.lock().await.clone() {
            return Err(ServiceError::InternalError(error));
        }
        self.added_participants
            .lock()
            .await
            .push((session_id.to_string(), participant.clone()));
        let mut sessions = self.sessions.lock().await;
        let Some(session) = sessions.get_mut(session_id) else {
            return Err(ServiceError::SessionNotFound(session_id.to_string()));
        };
        if !session
            .participants
            .iter()
            .any(|existing| existing.bot_uuid == participant.bot_uuid)
        {
            session.participants.push(participant);
        }
        Ok(session.clone())
    }

    async fn remove_participant(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> ServiceResult<Session> {
        let mut sessions = self.sessions.lock().await;
        let Some(session) = sessions.get_mut(session_id) else {
            return Err(ServiceError::SessionNotFound(session_id.to_string()));
        };
        session
            .participants
            .retain(|participant| participant.bot_uuid != bot_uuid);
        Ok(session.clone())
    }

    async fn update_participant_mode(
        &self,
        session_id: &str,
        bot_uuid: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<Session> {
        let mut sessions = self.sessions.lock().await;
        let Some(session) = sessions.get_mut(session_id) else {
            return Err(ServiceError::SessionNotFound(session_id.to_string()));
        };
        for participant in &mut session.participants {
            if participant.bot_uuid == bot_uuid {
                participant.mode = Some(mode);
            }
        }
        Ok(session.clone())
    }

    async fn update_callback_status(
        &self,
        session_id: &str,
        status: &str,
    ) -> ServiceResult<()> {
        let mut sessions = self.sessions.lock().await;
        let Some(session) = sessions.get_mut(session_id) else {
            return Err(ServiceError::SessionNotFound(session_id.to_string()));
        };
        session.callback_status = Some(status.to_string());
        Ok(())
    }

    async fn update_title(
        &self,
        session_id: &str,
        title: Option<String>,
    ) -> ServiceResult<Session> {
        let mut sessions = self.sessions.lock().await;
        let Some(session) = sessions.get_mut(session_id) else {
            return Err(ServiceError::SessionNotFound(session_id.to_string()));
        };
        session.session_title = title;
        Ok(session.clone())
    }

    async fn list_group_ids_by_session_participant(&self, bot_uuid: &str) -> Vec<String> {
        self.sessions
            .lock()
            .await
            .values()
            .filter(|session| {
                session
                    .participants
                    .iter()
                    .any(|participant| participant.bot_uuid == bot_uuid)
            })
            .map(|session| session.group_id.clone())
            .collect()
    }

    async fn delete(&self, session_id: &str) -> ServiceResult<bool> {
        Ok(self.sessions.lock().await.remove(session_id).is_some())
    }
}

#[derive(Default)]
pub(super) struct RecordingMessageFlow {
    pub(super) web_sends: Mutex<Vec<WebSendCommand>>,
    pub(super) failed_dispatch_count: Mutex<usize>,
    pub(super) active_run_ids: Mutex<Vec<String>>,
}

#[async_trait]
impl MessageFlowService for RecordingMessageFlow {
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        self.web_sends.lock().await.push(cmd);
        let failed_count = *self.failed_dispatch_count.lock().await;
        let active_run_ids = self.active_run_ids.lock().await.clone();
    Ok(WebSendOutcome {
        queue_admission: None,
            primary_run_id: "run_1".to_string(),
            status: "accepted".to_string(),
            active_run_ids,
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            mentions: Vec::new(),
            hidden_mentions: Vec::new(),
            delivered_count: 0,
            failed_count,
            delivery_results: Vec::<MessageDeliveryResult>::new(),
        })
    }

    async fn handle_group_chat(
        &self,
        _cmd: GroupChatCommand,
    ) -> ServiceResult<GroupChatOutcome> {
        Err(not_configured("group chat"))
    }

    async fn handle_persistent_group_send(
        &self,
        _cmd: PersistentGroupSendCommand,
    ) -> ServiceResult<PersistentGroupSendOutcome> {
        Err(not_configured("persistent group send"))
    }

    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        Err(not_configured("bot event"))
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        Err(not_configured("group callback"))
    }

    async fn handle_chat_abort(
        &self,
        _cmd: ChatAbortCommand,
    ) -> ServiceResult<ChatAbortOutcome> {
        Err(not_configured("chat abort"))
    }

    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        Ok(TaskRunAliasRegistration::NotTask)
    }

    async fn handle_task_dispatch(
        &self,
        _cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        Err(not_configured("task dispatch"))
    }

    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        Err(not_configured("task complete"))
    }
}

#[derive(Default)]
pub(super) struct RecordingDelivery {
    pub(super) failures_remaining: AtomicU64,
    pub(super) events: Mutex<Vec<ChannelOutboundEvent>>,
    pub(super) fail_error: Mutex<Option<String>>,
    pub(super) call_error_account_ref: Mutex<Option<String>>,
}

#[async_trait]
impl ChannelDeliveryPort for RecordingDelivery {
    async fn is_available(&self, _binding: &ChannelBindingRef) -> bool {
        true
    }

    async fn deliver_event(
        &self,
        event: ChannelOutboundEvent,
    ) -> ServiceResult<ChannelDeliveryResult> {
        if self
            .call_error_account_ref
            .lock()
            .await
            .as_deref()
            .is_some_and(|account_ref| account_ref == event.binding_ref.account_ref)
        {
            return Err(ServiceError::InternalError(
                "simulated channel delivery call failure".to_string(),
            ));
        }
        self.events.lock().await.push(event);
        if self.failures_remaining.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |remaining| remaining.checked_sub(1)).is_ok() {
            return Ok(ChannelDeliveryResult {
                delivered: false,
                provider_message_ref: None,
                error: Some(ServiceError::InternalError("temporary delivery outage".into())),
            });
        }
        if let Some(error) = self.fail_error.lock().await.clone() {
            return Ok(ChannelDeliveryResult {
                delivered: false,
                provider_message_ref: None,
                error: Some(ServiceError::InternalError(error)),
            });
        }
        Ok(ChannelDeliveryResult {
            delivered: true,
            provider_message_ref: None,
            error: None,
        })
    }
}

pub(super) struct RecordingProvider {
    pub(super) delivery: Arc<RecordingDelivery>,
    pub(super) validate_calls: std::sync::Mutex<Vec<serde_json::Value>>,
}

impl RecordingProvider {
    pub(super) fn new(delivery: Arc<RecordingDelivery>) -> Self {
        Self {
            delivery,
            validate_calls: std::sync::Mutex::new(Vec::new()),
        }
    }

    pub(super) fn validate_call_count(&self) -> usize {
        self.validate_calls.lock().unwrap().len()
    }
}

#[async_trait]
impl ChannelProvider for RecordingProvider {
    fn channel_type(&self) -> &'static str {
        "dingtalk"
    }

    fn validate_config(&self, config: &serde_json::Value) -> ChannelProviderResult<()> {
        self.validate_calls.lock().unwrap().push(config.clone());
        if config.get("valid").and_then(serde_json::Value::as_bool) == Some(false) {
            return Err(ChannelProviderError::InvalidConfig(
                "provider rejected config".to_string(),
            ));
        }
        Ok(())
    }

    fn redact_config(&self, config: &serde_json::Value) -> serde_json::Value {
        let mut redacted = config.clone();
        if let Some(object) = redacted.as_object_mut() {
            object.insert("client_secret".to_string(), serde_json::json!("<redacted>"));
        }
        redacted
    }

    fn resolve_direct_recipient(
        &self,
        actor_id: &str,
    ) -> ChannelProviderResult<Option<String>> {
        let recipient = actor_id
            .strip_prefix("human_")
            .filter(|value| !value.is_empty() && value.trim().len() == value.len())
            .ok_or_else(|| {
                ChannelProviderError::Provider(format!(
                    "test provider cannot resolve direct recipient from actor {actor_id}"
                ))
            })?;
        Ok(Some(recipient.to_string()))
    }

    fn delivery(&self) -> Arc<dyn ChannelDeliveryPort> {
        self.delivery.clone()
    }

    fn http_ingress(&self) -> Option<Arc<dyn bcs_channel_api::ChannelHttpIngressPort>> {
        None
    }

    fn stream_lifecycle(
        &self,
        _sink: Arc<dyn ChannelInboundSink>,
    ) -> Option<Arc<dyn ServiceLifecycle>> {
        None
    }
}
