//! SessionManagementService 实现：薄编排，逻辑下沉到 core + repo。

use std::collections::BTreeMap;
use std::sync::Arc;

use async_trait::async_trait;

use bcs_service_api::application::session::{
    CreateOrReactivateCommand, CreateOrReactivateOutcome, SessionManagementService,
    SessionUseCaseError,
};
use bcs_service_api::core::session::new_session_id;
use bcs_service_api::port::repo::{
    AddSessionParticipantWithEvent, CompleteSessionWithEvent, CreateSessionWithEvent,
    GroupRepoPort, RemoveSessionParticipantWithEvent, SessionRepoPort,
};
use bcs_service_api::port::{EventRecordFactoryPort, NewEvent};
use bcs_service_api::types::{EVENT_SCHEMA_VERSION_V1, EventScope, EventSubject};
use bcs_service_api::{
    ActorKind, BotRuntimeConnectionService, CollaborationRuntimeService, GroupStrategy,
    Participant, ParticipantMode, ParticipantRole, ServiceError, Session, SessionKind,
    SessionStatus,
};
use chrono::{SecondsFormat, Utc};
use serde_json::{Value, json};

pub struct SessionManagementServiceImpl {
    repo: Arc<dyn SessionRepoPort>,
    group_repo: Arc<dyn GroupRepoPort>,
    bot_runtime: Option<Arc<dyn BotRuntimeConnectionService>>,
    event_record_factory: Option<Arc<dyn EventRecordFactoryPort>>,
}

pub struct SessionManagementWithRuntimeCleanup {
    inner: Arc<dyn SessionManagementService>,
    collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
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

    async fn count_running_service(
        &self,
        group_id: &str,
    ) -> Result<u64, SessionUseCaseError> {
        self.inner.count_running_service(group_id).await
    }

    async fn list_running_service(
        &self,
        offset: u64,
        limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        self.inner.list_running_service(offset, limit).await
    }

    async fn update_callback_status(
        &self,
        session_id: &str,
        status: &str,
    ) -> Result<(), SessionUseCaseError> {
        self.inner.update_callback_status(session_id, status).await
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

    async fn collect(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> Result<(), SessionUseCaseError> {
        self.inner.collect(session_id, bot_uuid).await
    }

    async fn uncollect(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> Result<(), SessionUseCaseError> {
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
            .list_collected_by_group(
                group_id,
                bot_uuid,
                status,
                title_contains,
                offset,
                limit,
            )
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
    pub fn new(repo: Arc<dyn SessionRepoPort>, group_repo: Arc<dyn GroupRepoPort>) -> Self {
        Self {
            repo,
            group_repo,
            bot_runtime: None,
            event_record_factory: None,
        }
    }

    pub fn with_bot_runtime(
        mut self,
        bot_runtime: Arc<dyn BotRuntimeConnectionService>,
    ) -> Self {
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

    fn prepare_event(
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

    async fn ensure_manager_worker_accepts_participants(
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

#[async_trait]
impl SessionManagementService for SessionManagementServiceImpl {
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
            Ok(CreateOrReactivateOutcome { session, created: false })
        } else {
            self.ensure_manager_worker_accepts_participants(
                &cmd.group_id,
                &cmd.params.participants,
            )
            .await?;
            let group_is_provisioning = self
                .group_repo
                .try_get(&cmd.group_id)
                .await?
                .is_some_and(|group| group.record_status == "provisioning");
            let mut params = cmd.params;
            let session = if group_is_provisioning || self.event_record_factory.is_none() {
                self.repo.create(&cmd.group_id, params).await?
            } else {
                let session_id = match params.id.clone() {
                    Some(session_id) => session_id,
                    None => new_session_id(&cmd.group_id).map_err(|error| {
                        SessionUseCaseError::InvalidParams(error.to_string())
                    })?,
                };
                params.id = Some(session_id.clone());
                let mut data = BTreeMap::new();
                data.insert(
                    "session_kind".to_string(),
                    json!(session_kind_name(params.session_kind)),
                );
                data.insert("status".to_string(), json!("running"));
                data.insert("initial".to_string(), json!(false));
                if let Some(created_by) = params
                    .created_by
                    .as_deref()
                    .or(params.caller_id.as_deref())
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

    async fn count_running_service(
        &self,
        group_id: &str,
    ) -> Result<u64, SessionUseCaseError> {
        Ok(self.repo.count_running_service(group_id).await)
    }

    async fn list_running_service(
        &self,
        offset: u64,
        limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(self.repo.list_running_service(offset, limit).await)
    }

    async fn update_callback_status(
        &self,
        session_id: &str,
        status: &str,
    ) -> Result<(), SessionUseCaseError> {
        Ok(self.repo.update_callback_status(session_id, status).await?)
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
            json!(if error.is_some() { "failed" } else { "completed" }),
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
            None => Ok(self
                .repo
                .complete_if_running(session_id, output, error)
                .await?),
        }
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
                if let Some(manager) = group.participants.iter().find(|p| p.role == ParticipantRole::Manager) {
                    if bot_uuid == manager.bot_uuid {
                        return Err(SessionUseCaseError::InvalidParams(
                            "Cannot remove the Manager bot from a ManagerWorker session".to_string(),
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
        Ok(self.repo.update_participant_mode(session_id, bot_uuid, mode).await?)
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

    async fn collect(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> Result<(), SessionUseCaseError> {
        if self.repo.get(session_id).await.is_none() {
            return Err(SessionUseCaseError::NotFound(session_id.to_string()));
        }
        self.repo.collect(session_id, bot_uuid).await?;
        Ok(())
    }

    async fn uncollect(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> Result<(), SessionUseCaseError> {
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

fn session_kind_name(kind: SessionKind) -> &'static str {
    match kind {
        SessionKind::Chat => "chat",
        SessionKind::ServiceInvocation => "service_invocation",
    }
}

fn actor_kind_name(kind: ActorKind) -> &'static str {
    match kind {
        ActorKind::Bot => "bot",
        ActorKind::Human => "human",
    }
}

fn participant_role_name(role: ParticipantRole) -> &'static str {
    match role {
        ParticipantRole::Driver => "driver",
        ParticipantRole::Consultant => "consultant",
        ParticipantRole::Manager => "manager",
        ParticipantRole::Worker => "worker",
        ParticipantRole::Observer => "observer",
    }
}

fn participant_mode_name(mode: ParticipantMode) -> &'static str {
    match mode {
        ParticipantMode::Auto => "auto",
        ParticipantMode::Muted => "muted",
        ParticipantMode::Present => "present",
        ParticipantMode::Absent => "absent",
    }
}
