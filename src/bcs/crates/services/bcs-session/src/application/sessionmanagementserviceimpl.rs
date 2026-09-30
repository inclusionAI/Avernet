//! application implementation.
use super::*;


pub struct SessionManagementServiceImpl {
    pub(super) repo: Arc<dyn SessionRepoPort>,
    pub(super) group_repo: Arc<dyn GroupRepoPort>,
    pub(super) bot_runtime: Option<Arc<dyn BotRuntimeConnectionService>>,
    pub(super) event_record_factory: Option<Arc<dyn EventRecordFactoryPort>>,
    pub(super) message_repo: Option<Arc<dyn MessageRepoPort>>,
    pub(super) frontend_delivery: Option<Arc<dyn FrontendDeliveryPort>>,
}



pub struct SessionManagementWithRuntimeCleanup {
    pub(super) inner: Arc<dyn SessionManagementService>,
    pub(super) collaboration_runtime: Arc<dyn CollaborationRuntimeService>,

}

impl SessionManagementWithRuntimeCleanup {

pub fn new(
        inner: Arc<dyn SessionManagementService>,
        collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    ) -> Self {
        Self {
            inner,
            collaboration_runtime,
        }
    }
}



#[async_trait]
impl SessionManagementService for SessionManagementWithRuntimeCleanup {
    async fn validate_session_registry(&self) -> Result<(), SessionUseCaseError> { self.inner.validate_session_registry().await }
    async fn ensure_direct_session(&self, id: &str) -> Result<bcs_service_api::port::repo::session_registry::SessionRegistration, SessionUseCaseError> {
        self.inner.ensure_direct_session(id).await.map_err(Into::into)
    }
    async fn session_registration(&self, id: &str) -> Result<Option<bcs_service_api::port::repo::session_registry::SessionRegistration>, SessionUseCaseError> {
        self.inner.session_registration(id).await.map_err(Into::into)
    }

    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        self.inner.create_or_reactivate(cmd).await
    }

    async fn get(&self, session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        self.inner.get(session_id).await
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        self.inner.belongs_to_group(session_id, group_id).await
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
        self.inner
            .list_by_group(
                group_id,
                status,
                offset,
                limit,
                title_contains,
                participant_id,
            )
            .await
    }

    async fn count_running_service(&self, group_id: &str) -> Result<u64, SessionUseCaseError> {
        self.inner.count_running_service(group_id).await
    }

    async fn list_running_service(
        &self,
        offset: u64,
        limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        self.inner.list_running_service(offset, limit).await
    }

    async fn list_running_service_after(
        &self, cursor: Option<&str>, limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        self.inner.list_running_service_after(cursor, limit).await
    }

    async fn list_recoverable_callbacks(
        &self,
        now_ms: u64,
        after_session_id: Option<&str>,
        limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        self.inner
            .list_recoverable_callbacks(now_ms, after_session_id, limit)
            .await
    }

    async fn update_callback_status(
        &self,
        session_id: &str,
        status: &str,
    ) -> Result<(), SessionUseCaseError> {
        self.inner.update_callback_status(session_id, status).await
    }

    async fn claim_callback(
        &self,
        command: ClaimSessionCallbackCommand,
    ) -> Result<Option<ClaimSessionCallbackOutcome>, SessionUseCaseError> {
        self.inner.claim_callback(command).await
    }

    async fn complete_callback(
        &self,
        command: CompleteSessionCallbackCommand,
    ) -> Result<bool, SessionUseCaseError> {
        self.inner.complete_callback(command).await
    }

    async fn complete_if_running(
        &self,
        session_id: &str,
        output: Option<Value>,
        error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        self.inner
            .complete_if_running(session_id, output, error)
            .await
    }

    async fn complete_running_service_activation(
        &self, session_id: &str, activation: i32, output: Option<Value>, error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        self.inner.complete_running_service_activation(session_id, activation, output, error).await
    }

    async fn add_participant(
        &self,
        session_id: &str,
        participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        self.inner.add_participant(session_id, participant).await
    }

    async fn remove_participant(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        self.inner.remove_participant(session_id, bot_uuid).await
    }

    async fn update_participant_mode(
        &self,
        session_id: &str,
        bot_uuid: &str,
        mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        self.inner
            .update_participant_mode(session_id, bot_uuid, mode)
            .await
    }

    async fn update_participant_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        message_view_scope: MessageViewScope,
    ) -> Result<Session, SessionUseCaseError> {
        self.inner
            .update_participant_message_view_scope(session_id, actor_id, message_view_scope)
            .await
    }

    async fn update_participant_mode_and_message_view_scope(
        &self,
        session_id: &str,
        actor_id: &str,
        mode: Option<ParticipantMode>,
        message_view_scope: MessageViewScope,
    ) -> Result<Session, SessionUseCaseError> {
        self.inner
            .update_participant_mode_and_message_view_scope(
                session_id,
                actor_id,
                mode,
                message_view_scope,
            )
            .await
    }

    async fn update_title(
        &self,
        session_id: &str,
        title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        self.inner.update_title(session_id, title).await
    }

    async fn list_group_ids_by_session_participant(
        &self,
        bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        self.inner
            .list_group_ids_by_session_participant(bot_uuid)
            .await
    }

    async fn delete(&self, session_id: &str) -> Result<bool, SessionUseCaseError> {
        self.collaboration_runtime
            .cancel_session_runs(session_id, "session_deleted")
            .await
            .map_err(|error| {
                SessionUseCaseError::Internal(ServiceError::InternalError(format!(
                    "Failed to cancel active state-machine runs for deleted session '{session_id}': {error}"
                )))
            })?;
        self.inner.delete(session_id).await
    }

    async fn collect(&self, session_id: &str, bot_uuid: &str) -> Result<(), SessionUseCaseError> {
        self.inner.collect(session_id, bot_uuid).await
    }

    async fn uncollect(&self, session_id: &str, bot_uuid: &str) -> Result<(), SessionUseCaseError> {
        self.inner.uncollect(session_id, bot_uuid).await
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
        self.inner
            .list_collected_by_group(group_id, bot_uuid, status, title_contains, offset, limit)
            .await
    }

    async fn collected_at_map(
        &self,
        session_ids: &[&str],
        bot_uuid: &str,
    ) -> Result<Vec<(String, u64)>, SessionUseCaseError> {
        self.inner.collected_at_map(session_ids, bot_uuid).await
    }

}

impl SessionManagementServiceImpl {

pub(super) async fn complete_session(
        &self,
        current: Session,
        output: Option<Value>,
        error: Option<String>,
        guard_activation: bool,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        let session_id = current.id.as_str();
        let summary_value = output.clone().unwrap_or(Value::Null);
        let summary_bytes = serde_json::to_vec(&summary_value).map_err(|serialize_error| {
            SessionUseCaseError::InvalidParams(format!(
                "Session output cannot be serialized: {serialize_error}"
            ))
        })?;
        let mut data = BTreeMap::new();
        data.insert("completed_by".to_string(), json!("bcs-system"));
        data.insert(
            "reason".to_string(),
            json!(if error.is_some() {
                "failed"
            } else {
                "completed"
            }),
        );
        data.insert(
            "summary".to_string(),
            json!({
                "content_type": "application/json",
                "size_bytes": summary_bytes.len(),
                "json": summary_value,
                "truncated": false
            }),
        );
        match self.prepare_event(
            "session.completed",
            &current.group_id,
            session_id,
            "session",
            session_id,
            data,
        )? {
            Some(event) => Ok(self
                .repo
                .complete_if_running_with_event(CompleteSessionWithEvent {
                    session_id: session_id.to_string(),
                    expected_activation_count: current.activation_count,
                    output,
                    error,
                    event,
                })
                .await?),
            None if guard_activation => Ok(self.repo.complete_running_service_activation(
                session_id, current.activation_count, output, error,
            ).await?),
            None => Ok(self.repo.complete_if_running(session_id, output, error).await?),
        }
    }

pub fn new(repo: Arc<dyn SessionRepoPort>, group_repo: Arc<dyn GroupRepoPort>) -> Self {
        Self {
            repo,
            group_repo,
            bot_runtime: None,
            event_record_factory: None,
            message_repo: None,
            frontend_delivery: None,
        }
    }

pub fn with_bot_runtime(mut self, bot_runtime: Arc<dyn BotRuntimeConnectionService>) -> Self {
        self.bot_runtime = Some(bot_runtime);
        self
    }

pub fn with_event_record_factory(
        mut self,
        event_record_factory: Arc<dyn EventRecordFactoryPort>,
    ) -> Self {
        self.event_record_factory = Some(event_record_factory);
        self
    }

pub fn with_opening_message_delivery(
        mut self,
        message_repo: Arc<dyn MessageRepoPort>,
        frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    ) -> Self {
        self.message_repo = Some(message_repo);
        self.frontend_delivery = Some(frontend_delivery);
        self
    }

pub(super) async fn persist_session_opening_message(
        &self,
        group: &bcs_service_api::Group,
        session: &Session,
    ) -> Result<(), SessionUseCaseError> {
        if !matches!(
            group.group_strategy,
            GroupStrategy::Chat | GroupStrategy::ManagerWorker
        ) {
            return Ok(());
        }
        let Some(opening_message) = group.opening_message.as_ref() else {
            return Ok(());
        };
        let Some(message_repo) = self.message_repo.as_ref() else {
            return Err(SessionUseCaseError::Internal(ServiceError::InternalError(
                "Session opening-message persistence is not configured".to_string(),
            )));
        };
        let rendered = opening_message
            .render(OpeningMessageRenderContext::Session {
                group_id: &group.id,
                session_id: &session.id,
                group_name: group.label.as_deref(),
                session_name: session.session_title.as_deref(),
            })
            .map_err(|error| {
                SessionUseCaseError::Internal(ServiceError::InternalError(format!(
                    "Failed to render opening_message for session '{}': {error}",
                    session.id
                )))
            })?;
        let strategy = match group.group_strategy {
            GroupStrategy::Chat => "chat",
            GroupStrategy::ManagerWorker => "manager_worker",
            GroupStrategy::StateMachine => unreachable!("filtered above"),
        };
        let visibility_domain = match group.group_strategy {
            GroupStrategy::Chat => MessageVisibilityDomain::Chat,
            GroupStrategy::ManagerWorker => MessageVisibilityDomain::ManagerWorker,
            GroupStrategy::StateMachine => unreachable!("filtered above"),
        };
        let audience =
            (visibility_domain != MessageVisibilityDomain::Chat).then_some(MessageAudience::Public);
        let mut opening_metadata = serde_json::json!({
            "scope": "session",
            "strategy": strategy,
        });
        if let Some(component) = rendered.component.as_ref() {
            opening_metadata["component"] = Value::String(component.clone());
        }
        let metadata = serde_json::json!({ "opening_message": opening_metadata });
        let client_msg_id = format!("{}:000-opening", session.id);
        let run_id = format!("{}:opening", session.id);
        let persisted = message_repo
            .append_message(NewMessage {
                group_id: group.id.clone(),
                session_id: session.id.clone(),
                sender_id: BCS_SESSION_OPENING_MESSAGE_SENDER.to_string(),
                sender_type: SenderType::Bot,
                message_type: SESSION_OPENING_MESSAGE_TYPE.to_string(),
                content: serde_json::json!({
                    "text": rendered.content,
                    "bot_name": BCS_SESSION_OPENING_MESSAGE_SENDER_NAME,
                    "metadata": metadata,
                }),
                client_msg_id: Some(client_msg_id),
                owner_bot_id: None,
                created_at: session.created_at,
                run_id: run_id.clone(),
                visibility_domain,
                audience,
            })
            .await
            .map_err(|error| {
                SessionUseCaseError::Internal(ServiceError::InternalError(format!(
                    "Failed to persist opening_message for session '{}': {error}",
                    session.id
                )))
            })?;

        let Some(frontend_delivery) = self.frontend_delivery.as_ref() else {
            return Ok(());
        };
        let content = persisted
            .content
            .get("text")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let metadata = persisted
            .content
            .get("metadata")
            .cloned()
            .unwrap_or(Value::Null);
        let payload = serde_json::json!({
            "run_id": persisted.run_id,
            "bcs_group_id": persisted.group_id,
            "bcs_session_id": persisted.session_id,
            "state": "final",
            "role": "assistant",
            "sender": BCS_SESSION_OPENING_MESSAGE_SENDER,
            "content": content.clone(),
            "message_type": "bot",
            "bot_name": BCS_SESSION_OPENING_MESSAGE_SENDER_NAME,
            "metadata": metadata,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": content}],
                "timestamp": persisted.created_at,
            },
        });
        let frame = serde_json::json!({
            "type": "event",
            "event": "chat",
            "payload": payload,
            "group_id": group.id,
            "bot_uuid": BCS_SESSION_OPENING_MESSAGE_SENDER,
        });
        let visibility_domain = match group.group_strategy {
            GroupStrategy::Chat => MessageVisibilityDomain::Chat,
            GroupStrategy::ManagerWorker => MessageVisibilityDomain::ManagerWorker,
            GroupStrategy::StateMachine => MessageVisibilityDomain::StateMachine,
        };
        let audience =
            (visibility_domain != MessageVisibilityDomain::Chat).then_some(MessageAudience::Public);
        match tokio::time::timeout(
            Duration::from_millis(500),
            frontend_delivery.publish(FrontendDeliveryCommand {
                target: FrontendDeliveryTarget::Session {
                    session_id: session.id.clone(),
                },
                event_json: frame.to_string(),
                delivery_kind: FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: None,
                exclude_conn_id: None,
                visibility_domain,
                audience,
            }),
        )
        .await
        {
            Ok(Ok(_)) => {}
            Ok(Err(error)) => tracing::warn!(
                session_id = %session.id,
                error = %error,
                "failed to publish persisted session opening message"
            ),
            Err(_) => tracing::warn!(
                session_id = %session.id,
                "timed out publishing persisted session opening message"
            ),
        }
        Ok(())
    }

pub(super) fn prepare_event(
        &self,
        event_type: &str,
        group_id: &str,
        session_id: &str,
        subject_type: &str,
        subject_id: &str,
        data: BTreeMap<String, Value>,
    ) -> Result<Option<bcs_service_api::port::repo::AppendEventRecord>, SessionUseCaseError> {
        let Some(factory) = self.event_record_factory.as_ref() else {
            return Ok(None);
        };
        let operation_id = uuid::Uuid::new_v4();
        factory
            .prepare(NewEvent {
                event_id: format!("evt_{operation_id}"),
                event_type: event_type.to_string(),
                schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
                producer: "bcs-session".to_string(),
                producer_key: format!("{event_type}:{subject_id}:{operation_id}"),
                occurred_at: Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true),
                subject: EventSubject {
                    subject_type: subject_type.to_string(),
                    id: subject_id.to_string(),
                },
                scope: EventScope {
                    group_id: Some(group_id.to_string()),
                    session_id: Some(session_id.to_string()),
                    ..EventScope::default()
                },
                stream_key: format!("session:{session_id}"),
                actor: None,
                correlation_id: Some(format!("session:{session_id}")),
                causation_event_id: None,
                trace_id: None,
                data,
            })
            .map_err(|error| {
                SessionUseCaseError::Internal(ServiceError::InternalError(error.to_string()))
            })
    }

pub(super) async fn ensure_manager_worker_accepts_participants(
        &self,
        group_id: &str,
        _participants: &[Participant],
    ) -> Result<(), SessionUseCaseError> {
        let Some(group) = self.group_repo.get(group_id).await else {
            return Ok(());
        };
        if group.group_strategy != GroupStrategy::ManagerWorker {
            return Ok(());
        }
        Ok(())
    }
}
