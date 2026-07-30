//! SessionManagementService 实现：薄编排，逻辑下沉到 core + repo。

use std::sync::Arc;

use async_trait::async_trait;

use bcs_service_api::application::session::{
    CreateOrReactivateCommand, CreateOrReactivateOutcome, SessionManagementService,
    SessionUseCaseError,
};
use bcs_service_api::port::repo::{GroupRepoPort, SessionRepoPort};
use bcs_service_api::{
    BotRuntimeConnectionService, GroupStrategy, Participant, ParticipantMode, ParticipantRole,
    Session, SessionStatus,
};

pub struct SessionManagementServiceImpl {
    repo: Arc<dyn SessionRepoPort>,
    group_repo: Arc<dyn GroupRepoPort>,
    bot_runtime: Option<Arc<dyn BotRuntimeConnectionService>>,
}

impl SessionManagementServiceImpl {
    pub fn new(repo: Arc<dyn SessionRepoPort>, group_repo: Arc<dyn GroupRepoPort>) -> Self {
        Self {
            repo,
            group_repo,
            bot_runtime: None,
        }
    }

    pub fn with_bot_runtime(
        mut self,
        bot_runtime: Arc<dyn BotRuntimeConnectionService>,
    ) -> Self {
        self.bot_runtime = Some(bot_runtime);
        self
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
            let session = self.repo.create(&cmd.group_id, cmd.params).await?;
            Ok(CreateOrReactivateOutcome { session, created: true })
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
        output: Option<serde_json::Value>,
        error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self.repo.complete_if_running(session_id, output, error).await?)
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
        Ok(self.repo.add_participant(session_id, participant).await?)
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
            if bot_uuid == group.driver_bot || Some(bot_uuid.to_string()) == group.originator {
                return Err(SessionUseCaseError::InvalidParams(
                    "Cannot remove the group driver/coordinator from a session".to_string(),
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

        Ok(self.repo.remove_participant(session_id, bot_uuid).await?)
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
