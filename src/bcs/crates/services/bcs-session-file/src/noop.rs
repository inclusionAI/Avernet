//! Noop `SessionFileService` for builder defaults / tests.

use async_trait::async_trait;
use bcs_domain::SessionFile;
use bcs_service_api::application::session_files::{
    CapabilitiesView, DeleteFileCommand, DownloadRoute, PrepareUploadCommand, PrepareUploadResult,
    SessionFileService, SessionFileUseCaseError,
    ShareConsumeResult, ShareMintCommand, ShareMintResult,
};
use bcs_service_api::port::repo::{SessionFileListPage, SessionFileListParams};
use bcs_service_api::ServiceError;
use bcs_storage_api::ByteStream;

const NOT_SUPPORTED: &str = "NoopSessionFileService";

#[derive(Default)]
pub struct NoopSessionFileService;

#[async_trait]
impl SessionFileService for NoopSessionFileService {
    async fn capabilities(&self) -> CapabilitiesView {
        CapabilitiesView {
            storage: "noop".to_string(),
            presign_upload: false,
            presign_download: false,
            inline_view: false,
            max_size: 0,
        }
    }

    async fn prepare_upload(
        &self,
        _cmd: PrepareUploadCommand,
    ) -> Result<PrepareUploadResult, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::Internal(ServiceError::InternalError(
            NOT_SUPPORTED.into(),
        )))
    }

    async fn stream_upload(
        &self,
        _session_id: &str,
        _file_id: &str,
        _part_number: Option<u16>,
        _body: ByteStream,
        _content_length: u64,
    ) -> Result<(), SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::Internal(ServiceError::InternalError(
            NOT_SUPPORTED.into(),
        )))
    }

    async fn complete_upload(
        &self,
        _session_id: &str,
        _file_id: &str,
    ) -> Result<SessionFile, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::Internal(ServiceError::InternalError(
            NOT_SUPPORTED.into(),
        )))
    }

    async fn delete_file(
        &self,
        _cmd: DeleteFileCommand,
    ) -> Result<(), SessionFileUseCaseError> {
        Ok(())
    }

    async fn get(
        &self,
        _session_id: &str,
        _file_id: &str,
    ) -> Result<SessionFile, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::NotFound(NOT_SUPPORTED.into()))
    }

    async fn list(
        &self,
        _session_id: &str,
        _params: SessionFileListParams,
    ) -> Result<SessionFileListPage, SessionFileUseCaseError> {
        Ok(SessionFileListPage {
            items: Vec::new(),
            total: 0,
        })
    }

    async fn download_route(
        &self,
        _session_id: &str,
        _file_id: &str,
        _ttl_secs: Option<u64>,
        _show: bool,
    ) -> Result<(SessionFile, DownloadRoute), SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::NotFound(NOT_SUPPORTED.into()))
    }

    async fn share_mint(
        &self,
        _cmd: ShareMintCommand,
    ) -> Result<ShareMintResult, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::Internal(ServiceError::InternalError(
            NOT_SUPPORTED.into(),
        )))
    }

    async fn share_mint_for_history(
        &self,
        _session_id: &str,
        _file_id: &str,
        _ttl_seconds: u64,
    ) -> Result<ShareMintResult, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::Internal(ServiceError::InternalError(
            NOT_SUPPORTED.into(),
        )))
    }

    async fn share_consume(
        &self,
        _token: &str,
    ) -> Result<ShareConsumeResult, SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::NotFound(NOT_SUPPORTED.into()))
    }

    async fn get_stream(
        &self,
        _session_id: &str,
        _file_id: &str,
    ) -> Result<(SessionFile, ByteStream), SessionFileUseCaseError> {
        Err(SessionFileUseCaseError::NotFound(NOT_SUPPORTED.into()))
    }

    async fn sweep_expired_pending(&self) -> Result<u64, SessionFileUseCaseError> {
        Ok(0)
    }

    async fn delete_all_for_session(&self, _session_id: &str) -> Result<u64, SessionFileUseCaseError> {
        Ok(0)
    }
}