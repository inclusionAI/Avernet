//! OpenAPI V1 session-file application facade.

use std::collections::HashSet;
use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{
    ActorKind, ActorRef, FileStatus, Participant, Session, SessionFile, SystemMessageEvent,
};
use bcs_service_api::application::session::SessionManagementService;
use bcs_service_api::application::session_files::{
    DeleteFileCommand, PrepareUploadCommand, SessionFileService as LegacySessionFileService,
    SessionFileUseCaseError, ShareMintCommand,
};
use bcs_service_api::application::v1::{
    ApplicationError, CompleteSessionFile, DeleteResult, DeleteSessionFile,
    DownloadSessionFile, DownloadSharedSessionFile, GetSessionFile, IdentityPolicy,
    ListSessionFiles, PrepareSessionFile, PrepareSessionFileResult, Principal,
    SessionFileActor, SessionFileActorKind, SessionFileApplicationService, SessionFileContent,
    SessionFilePage, SessionFileStatus, SessionFileView, ShareSessionFile,
    ShareSessionFileResult, UploadSessionFileContent, UploadSessionFileResult, select_principal,
};
use bcs_service_api::{
    BotRegistryCoreService, GroupCoreService, ServiceError, SystemMessageService,
};

pub struct SessionFileApplicationServiceImpl {
    files: Arc<dyn LegacySessionFileService>,
    sessions: Arc<dyn SessionManagementService>,
    groups: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    system_message: Arc<dyn SystemMessageService>,
}

impl SessionFileApplicationServiceImpl {
    pub fn new(
        files: Arc<dyn LegacySessionFileService>,
        sessions: Arc<dyn SessionManagementService>,
        groups: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        system_message: Arc<dyn SystemMessageService>,
    ) -> Self {
        Self {
            files,
            sessions,
            groups,
            registry,
            system_message,
        }
    }

    async fn load_member(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        session_id: &str,
    ) -> Result<(Principal, Session), ApplicationError> {
        let principal = select_principal(caller, IdentityPolicy::HumanOrOwnedBot)?;
        let session = self
            .sessions
            .get(session_id)
            .await
            .map_err(map_session_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "session_not_found",
                    format!("Session '{session_id}' was not found"),
                )
            })?;
        self.groups
            .try_get(&session.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{}' was not found", session.group_id),
                )
            })?;
        if !self.is_member(&principal, &session).await? {
            return Err(ApplicationError::forbidden(
                "The effective Actor is not a Session Participant",
            ));
        }
        Ok((principal, session))
    }

    async fn is_member(
        &self,
        principal: &Principal,
        session: &Session,
    ) -> Result<bool, ApplicationError> {
        let actor_id = principal.actor_id();
        if session
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == actor_id)
        {
            return Ok(true);
        }
        let Principal::Human(human) = principal else {
            return Ok(false);
        };
        let owned = self
            .registry
            .try_list_bots_by_creator(&human.subject.id)
            .await
            .map_err(map_service_error)?
            .into_iter()
            .map(|bot| bot.bot_uuid)
            .collect::<HashSet<_>>();
        Ok(session
            .participants
            .iter()
            .any(|participant| owned.contains(&participant.bot_uuid)))
    }

    async fn caller_identities(
        &self,
        principal: &Principal,
    ) -> Result<Vec<String>, ApplicationError> {
        match principal {
            Principal::Bot(bot) => Ok(vec![bot.bot_uuid.clone()]),
            Principal::Human(human) => {
                let mut identities = vec![principal.actor_id()];
                identities.extend(
                    self.registry
                        .try_list_bots_by_creator(&human.subject.id)
                        .await
                        .map_err(map_service_error)?
                        .into_iter()
                        .map(|bot| bot.bot_uuid),
                );
                Ok(identities)
            }
        }
    }

    async fn ensure_upload_mutation(
        &self,
        principal: &Principal,
        file: &SessionFile,
    ) -> Result<(), ApplicationError> {
        if file.owner.actor_id == principal.actor_id() {
            return Ok(());
        }
        let Principal::Human(human) = principal else {
            return Err(upload_owner_mismatch());
        };
        if file.owner.actor_kind != ActorKind::Bot {
            return Err(upload_owner_mismatch());
        }
        let owned = self
            .registry
            .try_get(&file.owner.actor_id)
            .await
            .map_err(map_service_error)?
            .is_some_and(|bot| bot.created_by.as_deref() == Some(human.subject.id.as_str()));
        if owned {
            Ok(())
        } else {
            Err(upload_owner_mismatch())
        }
    }

    async fn authorized_file(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        session_id: &str,
        file_id: &str,
        mutation: bool,
    ) -> Result<(Principal, Session, SessionFile), ApplicationError> {
        let (principal, session) = self.load_member(caller, session_id).await?;
        let file = self
            .files
            .get(session_id, file_id)
            .await
            .map_err(map_file_error)?;
        if mutation {
            self.ensure_upload_mutation(&principal, &file).await?;
        }
        Ok((principal, session, file))
    }

    async fn content(
        &self,
        session_id: &str,
        file_id: &str,
        show: bool,
    ) -> Result<SessionFileContent, ApplicationError> {
        let (file, route) = self
            .files
            .download_route(session_id, file_id, None, show)
            .await
            .map_err(map_file_error)?;
        if let Some(ticket) = route.presign {
            return Ok(SessionFileContent::Redirect {
                download_url: ticket.download_url,
                expires_at: ticket.expires_at,
            });
        }
        let (_, body) = self
            .files
            .get_stream(session_id, file_id)
            .await
            .map_err(map_file_error)?;
        Ok(SessionFileContent::Stream {
            file: project_file(file),
            body,
        })
    }

    async fn notify_completed(
        &self,
        principal: &Principal,
        session: &Session,
        file: &SessionFile,
        content_url: &str,
    ) {
        let (prefix, name) = match principal {
            Principal::Bot(bot) => {
                let name = self
                    .registry
                    .get(&bot.bot_uuid)
                    .await
                    .and_then(|registered| registered.capabilities.name)
                    .unwrap_or_else(|| bot.bot_uuid.clone());
                ("Bot", name)
            }
            Principal::Human(human) => (
                "用户",
                human
                    .subject
                    .display_name
                    .clone()
                    .or_else(|| human.subject.full_name.clone())
                    .unwrap_or_else(|| human.subject.username.clone()),
            ),
        };
        let message = format!(
            "{} {} 上传了一个文件 {} ({}，{})，下载链接：{}",
            prefix,
            name,
            file.file_name,
            file.file_id,
            human_readable_size(file.size),
            content_url,
        );
        let uploader = principal.actor_id();
        let receivers = session
            .participants
            .iter()
            .filter(|participant| participant.is_bot() && participant.bot_uuid != uploader)
            .cloned()
            .collect::<Vec<Participant>>();
        if receivers.is_empty() {
            return;
        }
        let event = SystemMessageEvent::GenericNotification {
            group_id: session.group_id.clone(),
            message,
            receivers,
        };
        if let Err(error) = self
            .system_message
            .notify(
                &session.group_id,
                event,
                &session.id,
                &session.participants,
            )
            .await
        {
            tracing::warn!(
                session_id = %session.id,
                file_id = %file.file_id,
                error = %error,
                "failed to send best-effort session file completion notification"
            );
        }
    }
}

#[async_trait]
impl SessionFileApplicationService for SessionFileApplicationServiceImpl {
    async fn prepare(
        &self,
        command: PrepareSessionFile,
    ) -> Result<PrepareSessionFileResult, ApplicationError> {
        let (principal, _) = self.load_member(&command.caller, &command.session_id).await?;
        let result = self
            .files
            .prepare_upload(PrepareUploadCommand {
                session_id: command.session_id,
                file_name: command.file_name,
                size: command.size,
                mime_type: command.mime_type,
                caller: actor_ref(&principal),
            })
            .await
            .map_err(map_file_error)?;
        Ok(PrepareSessionFileResult {
            file: project_file(result.file),
            upload_target: result.client_target_json,
            expires_at: result.expires_at,
        })
    }

    async fn upload_content(
        &self,
        command: UploadSessionFileContent,
    ) -> Result<UploadSessionFileResult, ApplicationError> {
        self.authorized_file(
            &command.caller,
            &command.session_id,
            &command.file_id,
            true,
        )
        .await?;
        self.files
            .stream_upload(
                &command.session_id,
                &command.file_id,
                command.part_number,
                command.body,
                command.content_length.unwrap_or(0),
            )
            .await
            .map_err(map_file_error)?;
        Ok(UploadSessionFileResult {
            file_id: command.file_id,
            status: SessionFileStatus::Pending,
        })
    }

    async fn complete(
        &self,
        command: CompleteSessionFile,
    ) -> Result<SessionFileView, ApplicationError> {
        let (principal, session, _) = self
            .authorized_file(
                &command.caller,
                &command.session_id,
                &command.file_id,
                true,
            )
            .await?;
        let file = self
            .files
            .complete_upload(&command.session_id, &command.file_id)
            .await
            .map_err(map_file_error)?;
        self.notify_completed(
            &principal,
            &session,
            &file,
            &command.notification_content_url,
        )
        .await;
        Ok(project_file(file))
    }

    async fn delete(&self, command: DeleteSessionFile) -> Result<DeleteResult, ApplicationError> {
        let (principal, session) = self.load_member(&command.caller, &command.session_id).await?;
        let group = self
            .groups
            .try_get(&session.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{}' was not found", session.group_id),
                )
            })?;
        self.files
            .delete_file(DeleteFileCommand {
                session_id: command.session_id,
                file_id: command.file_id,
                caller: actor_ref(&principal),
                caller_identities: self.caller_identities(&principal).await?,
                session_creator: session.created_by,
                driver_bot: Some(group.driver_bot),
            })
            .await
            .map_err(map_file_error)?;
        Ok(DeleteResult { deleted: true })
    }

    async fn get(&self, command: GetSessionFile) -> Result<SessionFileView, ApplicationError> {
        self.load_member(&command.caller, &command.session_id).await?;
        self.files
            .get(&command.session_id, &command.file_id)
            .await
            .map(project_file)
            .map_err(map_file_error)
    }

    async fn list(&self, command: ListSessionFiles) -> Result<SessionFilePage, ApplicationError> {
        self.load_member(&command.caller, &command.session_id).await?;
        let page = self
            .files
            .list(
                &command.session_id,
                bcs_service_api::port::repo::SessionFileListParams {
                    prefix: command.prefix,
                    status: command.status.map(domain_status),
                    limit: command.limit,
                    offset: command.offset,
                },
            )
            .await
            .map_err(map_file_error)?;
        Ok(SessionFilePage {
            items: page.items.into_iter().map(project_file).collect(),
            total: page.total,
        })
    }

    async fn download(
        &self,
        command: DownloadSessionFile,
    ) -> Result<SessionFileContent, ApplicationError> {
        self.load_member(&command.caller, &command.session_id).await?;
        self.content(&command.session_id, &command.file_id, command.show)
            .await
    }

    async fn share(
        &self,
        command: ShareSessionFile,
    ) -> Result<ShareSessionFileResult, ApplicationError> {
        let (principal, session) = self.load_member(&command.caller, &command.session_id).await?;
        let result = self
            .files
            .share_mint(ShareMintCommand {
                session_id: command.session_id,
                file_id: command.file_id,
                caller: actor_ref(&principal),
                ttl_seconds: command.ttl_seconds,
                caller_identities: self.caller_identities(&principal).await?,
                session_participants: session
                    .participants
                    .iter()
                    .map(|participant| participant.bot_uuid.clone())
                    .collect(),
            })
            .await
            .map_err(map_file_error)?;
        Ok(ShareSessionFileResult {
            share_token: result.share_token,
            expires_at: result.expires_at,
        })
    }

    async fn download_shared(
        &self,
        command: DownloadSharedSessionFile,
    ) -> Result<SessionFileContent, ApplicationError> {
        let consumed = self
            .files
            .share_consume(&command.token)
            .await
            .map_err(|_| shared_file_not_found())?;
        self.content(
            &consumed.file.session_id,
            &consumed.file.file_id,
            command.show,
        )
        .await
    }
}

fn actor_ref(principal: &Principal) -> ActorRef {
    ActorRef {
        actor_kind: match principal {
            Principal::Human(_) => ActorKind::Human,
            Principal::Bot(_) => ActorKind::Bot,
        },
        actor_id: principal.actor_id(),
    }
}

fn project_file(file: SessionFile) -> SessionFileView {
    SessionFileView {
        file_id: file.file_id,
        session_id: file.session_id,
        file_name: file.file_name,
        mime_type: file.mime_type,
        size: file.size,
        sha256: file.sha256,
        owner: SessionFileActor {
            actor_kind: match file.owner.actor_kind {
                ActorKind::Human => SessionFileActorKind::Human,
                ActorKind::Bot => SessionFileActorKind::Bot,
            },
            actor_id: file.owner.actor_id,
        },
        storage_backend: file.storage_backend,
        status: project_status(file.status),
        created_at: file.created_at,
        updated_at: file.updated_at,
    }
}

fn project_status(status: FileStatus) -> SessionFileStatus {
    match status {
        FileStatus::Pending => SessionFileStatus::Pending,
        FileStatus::Ready => SessionFileStatus::Ready,
        FileStatus::Deleting => SessionFileStatus::Deleting,
        FileStatus::Failed => SessionFileStatus::Failed,
    }
}

fn domain_status(status: SessionFileStatus) -> FileStatus {
    match status {
        SessionFileStatus::Pending => FileStatus::Pending,
        SessionFileStatus::Ready => FileStatus::Ready,
        SessionFileStatus::Deleting => FileStatus::Deleting,
        SessionFileStatus::Failed => FileStatus::Failed,
    }
}

fn upload_owner_mismatch() -> ApplicationError {
    ApplicationError::forbidden_code(
        "file_upload_owner_mismatch",
        "The effective Actor may not modify this file upload",
    )
}

fn shared_file_not_found() -> ApplicationError {
    ApplicationError::not_found("shared_file_not_found", "Shared file was not found")
}

fn map_file_error(error: SessionFileUseCaseError) -> ApplicationError {
    match error {
        SessionFileUseCaseError::NotFound(message) => {
            ApplicationError::not_found("session_file_not_found", message)
        }
        SessionFileUseCaseError::Forbidden(message) => ApplicationError::forbidden(message),
        SessionFileUseCaseError::InvalidInput(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        SessionFileUseCaseError::PayloadTooLarge(message) => {
            ApplicationError::payload_too_large("file_too_large", message)
        }
        SessionFileUseCaseError::Conflict(message) => {
            ApplicationError::conflict("file_upload_not_pending", message)
        }
        SessionFileUseCaseError::InvalidState(message) => {
            ApplicationError::unprocessable("file_upload_incomplete", message)
        }
        SessionFileUseCaseError::Backend => ApplicationError::bad_gateway(
            "storage_backend_unavailable",
            "Storage backend is unavailable",
        ),
        SessionFileUseCaseError::Internal(error) => ApplicationError::internal(error.to_string()),
    }
}

fn map_session_error(
    error: bcs_service_api::application::session::SessionUseCaseError,
) -> ApplicationError {
    use bcs_service_api::application::session::SessionUseCaseError;
    match error {
        SessionUseCaseError::NotFound(session_id) => ApplicationError::not_found(
            "session_not_found",
            format!("Session '{session_id}' was not found"),
        ),
        SessionUseCaseError::InvalidParams(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        SessionUseCaseError::CallbackPending(message)
        | SessionUseCaseError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        SessionUseCaseError::Internal(error) => map_service_error(error),
    }
}

fn map_service_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::SessionNotFound(id) => {
            ApplicationError::not_found("session_not_found", format!("Session '{id}' was not found"))
        }
        ServiceError::GroupNotFound(id) => {
            ApplicationError::not_found("group_not_found", format!("Group '{id}' was not found"))
        }
        ServiceError::BotNotFound(id) | ServiceError::BotNotRegistered(id) => {
            ApplicationError::not_found("bot_not_found", format!("Bot '{id}' was not found"))
        }
        ServiceError::Unauthorized(_) => ApplicationError::Unauthenticated,
        ServiceError::Forbidden(message) => ApplicationError::forbidden(message),
        ServiceError::Conflict(message) => ApplicationError::conflict("conflict", message),
        other => ApplicationError::internal(other.to_string()),
    }
}

fn human_readable_size(bytes: u64) -> String {
    const UNITS: [&str; 5] = ["B", "KB", "MB", "GB", "TB"];
    if bytes < 1024 {
        return format!("{bytes} B");
    }
    let mut size = bytes as f64;
    let mut unit = 0usize;
    while size >= 1024.0 && unit < UNITS.len() - 1 {
        size /= 1024.0;
        unit += 1;
    }
    if size.fract() == 0.0 {
        format!("{:.0} {}", size, UNITS[unit])
    } else {
        format!("{:.1} {}", size, UNITS[unit])
    }
}
