//! `SessionFileServiceImpl` — the core application service for the BCS session
//! shared file workspace.
//!
//! Owns capability routing (presign vs proxy), mutate authz (delete / share),
//! the three-stage upload pipeline (prepare → stream → complete), delete
//! routing (Ready → `delete`, Pending/Failed → `abort_upload`), share-token
//! mint/consume, the Pending sweep, and `delete_all_for_session`.
//!
//! See `.superpowers/sdd/task-6-brief.md` for the full per-method spec.

use std::sync::Arc;

use async_trait::async_trait;
use tracing::warn;

use bcs_domain::{
    FileStatus, SessionFile, ShareTokenPayload, new_file_id,
    share_token_decode_and_verify, share_token_encode,
};
use bcs_service_api::application::session_files::{
    CapabilitiesView, DeleteFileCommand, DownloadRoute, PrepareUploadCommand,
    PrepareUploadResult, SessionFileService, SessionFileUseCaseError, ShareConsumeResult,
    ShareMintCommand, ShareMintResult,
};
use bcs_service_api::port::repo::{
    NewSessionFileParams, SessionFileListParams, SessionFileRepoPort, SessionRepoPort,
};
use bcs_storage_api::{
    ByteStream, ClientUploadTarget, PresignGetTicket, PreparedUpload, StorageCapabilities,
    StorageError, StorageHandle, UploadHandle, UploadMode, UploadPrepareRequest,
};

use crate::authz::{can_mutate, derive_key, validate_file_name};

/// Fixed part size used by the local-proxy (`ProxyViaBcs`) multipart branch.
///
/// Shared by [`SessionFileServiceImpl::prepare_upload`] (the preflight
/// part-count guard) and [`SessionFileServiceImpl::wire_client_target`] (URL
/// synthesis) so the bound and the synthesis agree on a single constant.
const PROXY_PART_SIZE: u64 = 10 * 1024 * 1024;

/// The preflight part-count ceiling (because `part_number` is a `u16`).
const MAX_PART_COUNT: u64 = 65535;

/// TTL bounds for share-token expiry (seconds). Clamped on mint so a single
/// misconfigured request cannot mint a 1-second or 10-year link.
const SHARE_TTL_MIN: u64 = 60;
const SHARE_TTL_MAX: u64 = 604_800;

/// Configuration for [`SessionFileServiceImpl`]. Built by bootstrap; the
/// service clones fields it needs from this struct in [`SessionFileServiceImpl::new`].
pub struct SessionFileServiceConfig {
    pub storage: Arc<dyn bcs_storage_api::StoragePlugin>,
    pub repo: Arc<dyn SessionFileRepoPort>,
    pub session_repo: Arc<dyn SessionRepoPort>,
    /// Server environment tag; MUST match the `env` column the repo writes
    /// (`MySqlSessionFileStore.env`). Part of the object key via
    /// [`crate::authz::derive_key`] so prod/gray/pre/dev objects stay isolated.
    pub env: String,
    pub max_size: u64,
    pub multipart_threshold: u64,
    pub bcs_base_url: String,
    pub share_secret: Vec<u8>,
    pub share_default_ttl: u64,
    pub share_link_ttl: u64,
    pub share_base_url: Option<String>,
}

pub struct SessionFileServiceImpl {
    cfg: SessionFileServiceConfig,
    /// Precomputed at construction; `capabilities()`/`max_size()` read this
    /// instead of re-asking the plugin so `capabilities()` is cheap, sync, no IO.
    caps: StorageCapabilities,
}

impl SessionFileServiceImpl {
    pub fn new(cfg: SessionFileServiceConfig) -> Self {
        // capabilities() precomputed at construction (cheap, no IO) — spec mandate.
        let caps = cfg.storage.capabilities();
        Self { cfg, caps }
    }

    /// Effective max object size = configured cap ∩ backend max object size.
    fn max_size(&self) -> u64 {
        self.cfg.max_size.min(self.caps.max_object_size)
    }

    fn bcs_proxy_upload_url(&self, sid: &str, file_id: &str) -> String {
        format!(
            "{}/sessions/{}/files/{}/content",
            self.cfg.bcs_base_url,
            urlencoding::encode(sid),
            file_id,
        )
    }

    fn bcs_proxy_upload_url_part(&self, sid: &str, file_id: &str, part: u16) -> String {
        format!("{}?part={}", self.bcs_proxy_upload_url(sid, file_id), part)
    }

    /// Translate [`PreparedUpload.client_target`] into the JSON the wire
    /// response carries (§1.2.a single / §1.2.b multipart).
    ///
    /// For the **local proxy** branch (`ProxyViaBcs`) the service decides
    /// single-vs-multipart by `size >= multipart_threshold` and synthesizes
    /// the BCS proxy URLs (`{bcs_base_url}/sessions/{encode(sid)}/files/{file_id}/content`).
    /// For the **presign** branch (`Direct`) the URLs come verbatim from the
    /// backend's `client_target`.
    fn wire_client_target(
        &self,
        sid: &str,
        file_id: &str,
        size: u64,
        prepared: &PreparedUpload,
    ) -> serde_json::Value {
        match &prepared.client_target {
            ClientUploadTarget::Direct { mode, url, parts, part_size, part_count } => match mode {
                UploadMode::Single => serde_json::json!({
                    "mode": "single",
                    "upload_url": url.clone().unwrap_or_default(),
                    "method": "PUT",
                    "expires_at": prepared.expires_at,
                }),
                UploadMode::Multipart => serde_json::json!({
                    "mode": "multipart",
                    "method": "PUT",
                    "part_size": part_size.unwrap_or(0),
                    "part_count": part_count.unwrap_or(0),
                    "expires_at": prepared.expires_at,
                    "parts": parts.clone().unwrap_or_default().iter().map(|p|
                        serde_json::json!({ "part_number": p.part_number, "upload_url": p.url })
                    ).collect::<Vec<_>>(),
                }),
            },
            ClientUploadTarget::ProxyViaBcs => {
                // Local proxy: no direct URL from the backend. Decide single vs
                // multipart by size threshold and synthesize BCS proxy URLs.
                let part_size: u64 = PROXY_PART_SIZE;
                if size >= self.cfg.multipart_threshold {
                    // part_count ≤ 65535 was enforced by prepare_upload before
                    // we got here; the debug_assert is a defensive invariant.
                    let part_count: u32 = ((size + part_size - 1) / part_size) as u32;
                    debug_assert!(
                        part_count as u64 <= MAX_PART_COUNT,
                        "part_count overflow — prepare_upload should have rejected",
                    );
                    let parts: Vec<_> = (1..=part_count as u16)
                        .map(|n| {
                            serde_json::json!({
                                "part_number": n,
                                "upload_url": self.bcs_proxy_upload_url_part(sid, file_id, n),
                            })
                        })
                        .collect();
                    serde_json::json!({
                        "mode": "multipart",
                        "method": "PUT",
                        "part_size": part_size,
                        "part_count": part_count,
                        "expires_at": prepared.expires_at,
                        "parts": parts,
                    })
                } else {
                    serde_json::json!({
                        "mode": "single",
                        "upload_url": self.bcs_proxy_upload_url(sid, file_id),
                        "method": "PUT",
                        "expires_at": prepared.expires_at,
                    })
                }
            }
        }
    }
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn map_storage_err(e: StorageError) -> SessionFileUseCaseError {
    match e {
        StorageError::NotFound => SessionFileUseCaseError::NotFound("object".into()),
        StorageError::Conflict(m) => SessionFileUseCaseError::Conflict(m),
        StorageError::InvalidInput(m) => SessionFileUseCaseError::InvalidInput(m),
        StorageError::Unsupported(_) => SessionFileUseCaseError::Backend,
        StorageError::Backend(_) => SessionFileUseCaseError::Backend,
    }
}

#[async_trait]
impl SessionFileService for SessionFileServiceImpl {
    async fn capabilities(&self) -> CapabilitiesView {
        CapabilitiesView {
            storage: self.cfg.storage.backend_name().to_string(),
            presign_upload: self.caps.supports_presign_put,
            presign_download: self.caps.supports_presign_download,
            max_size: self.max_size(),
        }
    }

    async fn prepare_upload(
        &self,
        cmd: PrepareUploadCommand,
    ) -> Result<PrepareUploadResult, SessionFileUseCaseError> {
        if cmd.size > self.max_size() {
            return Err(SessionFileUseCaseError::PayloadTooLarge(format!(
                "size {} exceeds max {}",
                cmd.size,
                self.max_size()
            )));
        }
        // part_count guard: `part_number` is `u16`, so local-proxy multipart
        // (10 MiB part size) must not exceed 65535 parts. Presign backends
        // self-limit via their own `part_count` in `client_target`; we enforce
        // the proxy bound here uniformly so `wire_client_target`'s `as u16`
        // cannot overflow.
        let part_count_needed = cmd.size.div_ceil(PROXY_PART_SIZE);
        if part_count_needed > MAX_PART_COUNT {
            return Err(SessionFileUseCaseError::PayloadTooLarge(format!(
                "size {} would produce {} parts, max {}",
                cmd.size, part_count_needed, MAX_PART_COUNT,
            )));
        }
        // Session-existence re-check (member/participant gate is HTTP's job via
        // `ensure_session_member`; service only re-validates existence so a
        // prepare against a deleted session still 404s).
        let _sess = self
            .cfg
            .session_repo
            .get(&cmd.session_id)
            .await
            .ok_or_else(|| SessionFileUseCaseError::NotFound(format!("session {}", cmd.session_id)))?;

        // File names are interpolated into the storage key as a path component
        // (see `derive_key`); reject path-traversal metacharacters up front so
        // the local backend's `data_dir.join(key)` cannot resolve outside the
        // session-files root. This is opaque-metadata safety, not a whitelist.
        validate_file_name(&cmd.file_name).map_err(|reason| {
            SessionFileUseCaseError::InvalidInput(format!("invalid file_name: {reason}"))
        })?;

        let file_id = new_file_id();
        let key = derive_key(&self.cfg.env, &cmd.session_id, &file_id, &cmd.file_name);
        let req = UploadPrepareRequest {
            key: key.clone(),
            file_name: cmd.file_name.clone(),
            mime_type: cmd.mime_type.clone(),
            size: cmd.size,
            ttl_secs: 300,
        };
        let prepared: PreparedUpload = self
            .cfg
            .storage
            .prepare_upload(req)
            .await
            .map_err(map_storage_err)?;
        let handle_json = serde_json::to_string(&prepared.handle)
            .map_err(|e| SessionFileUseCaseError::Internal(bcs_service_api::ServiceError::InternalError(e.to_string())))?;
        let row = self
            .cfg
            .repo
            .insert(NewSessionFileParams {
                file_id: file_id.clone(),
                session_id: cmd.session_id.clone(),
                file_name: cmd.file_name.clone(),
                mime_type: cmd.mime_type.clone(),
                size: cmd.size,
                owner: cmd.caller.clone(),
                storage_backend: self.cfg.storage.backend_name().to_string(),
                object_handle: handle_json,
                expires_at: prepared.expires_at,
            })
            .await
            .map_err(SessionFileUseCaseError::Internal)?;

        let client_target_json = self.wire_client_target(&cmd.session_id, &file_id, cmd.size, &prepared);
        Ok(PrepareUploadResult {
            file: row,
            client_target_json,
            expires_at: prepared.expires_at,
        })
    }

    async fn stream_upload(
        &self,
        session_id: &str,
        file_id: &str,
        part_number: Option<u16>,
        body: ByteStream,
        content_length: u64,
    ) -> Result<(), SessionFileUseCaseError> {
        let row = self
            .cfg
            .repo
            .get(session_id, file_id)
            .await
            .map_err(SessionFileUseCaseError::Internal)?
            .ok_or_else(|| SessionFileUseCaseError::NotFound(format!("file {}", file_id)))?;
        if row.status != FileStatus::Pending {
            return Err(SessionFileUseCaseError::Conflict(format!(
                "file status {:?} not Pending — cannot stream",
                row.status,
            )));
        }
        // `content_length == 0` means "unknown": the client streamed the body
        // with chunked transfer encoding (no Content-Length header), which the
        // CLI does for `Body::wrap_stream` uploads. In that case we cannot
        // pre-validate size, so skip the upfront guards and rely on the
        // backend's per-chunk cap (rejects bytes beyond the prepared size)
        // plus `complete_upload`'s cumulative-size check. When the client
        // declares a length, enforce it eagerly as a fast-fail.
        if content_length != 0 {
            if content_length > self.max_size() {
                return Err(SessionFileUseCaseError::InvalidInput(format!(
                    "content_length {} exceeds max {}",
                    content_length,
                    self.max_size()
                )));
            }
            if content_length != row.size {
                // Multipart: per-part size may legitimately be < row.size, but
                // single-part uploads must match exactly. Reject only the
                // mismatched single-part case to avoid InvalidInput on a final
                // short part of a multipart upload.
                if part_number.is_none() {
                    return Err(SessionFileUseCaseError::InvalidInput(format!(
                        "content_length {} != prepared size {}",
                        content_length, row.size,
                    )));
                }
                // For multipart, a part must not exceed the prepared part size.
                // The accumulated total is verified by the backend at complete_upload.
                if content_length > PROXY_PART_SIZE {
                    return Err(SessionFileUseCaseError::InvalidInput(format!(
                        "part content_length {} exceeds part_size {}",
                        content_length, PROXY_PART_SIZE,
                    )));
                }
            }
        }
        let handle: UploadHandle = serde_json::from_str(&row.object_handle).map_err(|e| {
            SessionFileUseCaseError::Internal(bcs_service_api::ServiceError::InternalError(
                format!("decode upload handle: {e}"),
            ))
        })?;
        self.cfg
            .storage
            .stream_upload(&handle, part_number, body)
            .await
            .map_err(map_storage_err)?;
        Ok(())
    }

    async fn complete_upload(
        &self,
        session_id: &str,
        file_id: &str,
    ) -> Result<SessionFile, SessionFileUseCaseError> {
        let row = self
            .cfg
            .repo
            .get(session_id, file_id)
            .await
            .map_err(SessionFileUseCaseError::Internal)?
            .ok_or_else(|| SessionFileUseCaseError::NotFound(format!("file {}", file_id)))?;
        if row.status != FileStatus::Pending {
            return Err(SessionFileUseCaseError::Conflict(format!(
                "file status {:?} not Pending — cannot complete",
                row.status,
            )));
        }
        let upload_handle: UploadHandle = serde_json::from_str(&row.object_handle).map_err(|e| {
            SessionFileUseCaseError::Internal(bcs_service_api::ServiceError::InternalError(
                format!("decode upload handle: {e}"),
            ))
        })?;
        let meta = self
            .cfg
            .storage
            .complete_upload(&upload_handle)
            .await
            .map_err(map_storage_err)?;
        // Defensive size check for non-presign single uploads (local): the backend
        // returns the actual written size, which must match what we prepared.
        // Presign_put backends (baas/OSS) do NOT return size on complete (OSS object
        // existence is verified server-side, not byte-counted for the client); skip
        // the check for them to avoid spurious Conflict (P1-A).
        if !self.caps.supports_presign_put
            && meta.size != row.size
            && upload_handle.backend_handle.get("parts").is_none()
        {
            // Backend gave a different size than we prepared for. The metadata
            // row tracks the prepared size, so refuse to flip to Ready.
            return Err(SessionFileUseCaseError::Conflict(format!(
                "completed size {} != prepared size {}",
                meta.size, row.size,
            )));
        }
        let storage_handle = StorageHandle {
            backend: upload_handle.backend,
            key: upload_handle.key,
            backend_handle: upload_handle.backend_handle,
        };
        let handle_json = serde_json::to_string(&storage_handle).map_err(|e| {
            SessionFileUseCaseError::Internal(bcs_service_api::ServiceError::InternalError(
                e.to_string(),
            ))
        })?;
        let updated = self
            .cfg
            .repo
            .update_object_handle_and_status(
                session_id,
                file_id,
                &handle_json,
                FileStatus::Ready,
                meta.size,
            )
            .await
            .map_err(SessionFileUseCaseError::Internal)?
            .ok_or_else(|| SessionFileUseCaseError::NotFound(format!("file {} vanished", file_id)))?;
        Ok(updated)
    }

    async fn delete_file(&self, cmd: DeleteFileCommand) -> Result<(), SessionFileUseCaseError> {
        let row = match self
            .cfg
            .repo
            .get(&cmd.session_id, &cmd.file_id)
            .await
            .map_err(SessionFileUseCaseError::Internal)?
        {
            Some(r) => r,
            None => return Ok(()), // metadata-layer idempotent: no row → 204, no backend probe.
        };
        if !can_mutate(
            &cmd.caller_identities,
            &row.owner,
            cmd.session_creator.as_deref(),
            cmd.driver_bot.as_deref(),
        ) {
            return Err(SessionFileUseCaseError::Forbidden(format!(
                "caller not authorized to delete file {}",
                cmd.file_id,
            )));
        }
        let result = match row.status {
            FileStatus::Ready => {
                let handle: StorageHandle = serde_json::from_str(&row.object_handle).map_err(|e| {
                    SessionFileUseCaseError::Internal(bcs_service_api::ServiceError::InternalError(
                        format!("decode storage handle: {e}"),
                    ))
                })?;
                self.cfg.storage.delete(&handle).await
            }
            FileStatus::Pending | FileStatus::Failed => {
                let handle: UploadHandle = serde_json::from_str(&row.object_handle).map_err(|e| {
                    SessionFileUseCaseError::Internal(bcs_service_api::ServiceError::InternalError(
                        format!("decode upload handle: {e}"),
                    ))
                })?;
                self.cfg.storage.abort_upload(&handle).await
            }
            FileStatus::Deleting => {
                // Should not normally occur in v1; treat as a no-op backend call.
                return self
                    .cfg
                    .repo
                    .delete(&cmd.session_id, &cmd.file_id)
                    .await
                    .map(|_| ())
                    .map_err(SessionFileUseCaseError::Internal);
            }
        };
        match result {
            Ok(()) => {
                self.cfg
                    .repo
                    .delete(&cmd.session_id, &cmd.file_id)
                    .await
                    .map_err(SessionFileUseCaseError::Internal)?;
                Ok(())
            }
            Err(StorageError::NotFound) => {
                // Backend NotFound = idempotent. Drop the metadata row and return Ok.
                self.cfg
                    .repo
                    .delete(&cmd.session_id, &cmd.file_id)
                    .await
                    .map_err(SessionFileUseCaseError::Internal)?;
                Ok(())
            }
            Err(e) => Err(map_storage_err(e)), // Backend failure: leave row for sweep.
        }
    }

    async fn get(
        &self,
        session_id: &str,
        file_id: &str,
    ) -> Result<SessionFile, SessionFileUseCaseError> {
        self.cfg
            .repo
            .get(session_id, file_id)
            .await
            .map_err(SessionFileUseCaseError::Internal)?
            .ok_or_else(|| SessionFileUseCaseError::NotFound(format!("file {}", file_id)))
    }

    async fn list(
        &self,
        session_id: &str,
        params: SessionFileListParams,
    ) -> Result<bcs_service_api::port::repo::SessionFileListPage, SessionFileUseCaseError> {
        self.cfg
            .repo
            .list(session_id, params)
            .await
            .map_err(SessionFileUseCaseError::Internal)
    }

    async fn download_route(
        &self,
        session_id: &str,
        file_id: &str,
        ttl_secs: Option<u64>,
    ) -> Result<(SessionFile, DownloadRoute), SessionFileUseCaseError> {
        let row = self
            .cfg
            .repo
            .get(session_id, file_id)
            .await
            .map_err(SessionFileUseCaseError::Internal)?
            .ok_or_else(|| SessionFileUseCaseError::NotFound(format!("file {}", file_id)))?;
        if row.status != FileStatus::Ready {
            return Err(SessionFileUseCaseError::InvalidState(format!(
                "file status {:?} not Ready — cannot download",
                row.status,
            )));
        }
        if self.caps.supports_presign_download {
            let handle: StorageHandle = serde_json::from_str(&row.object_handle).map_err(|e| {
                SessionFileUseCaseError::Internal(bcs_service_api::ServiceError::InternalError(
                    format!("decode storage handle: {e}"),
                ))
            })?;
            let ttl = ttl_secs.unwrap_or(self.cfg.share_link_ttl);
            let ticket: PresignGetTicket = self
                .cfg
                .storage
                .presign_get(&handle, ttl)
                .await
                .map_err(map_storage_err)?;
            Ok((row, DownloadRoute { presign: Some(ticket) }))
        } else {
            // Local backend: no presigned redirect — HTTP streams via get_stream.
            Ok((row, DownloadRoute { presign: None }))
        }
    }

    async fn share_mint(
        &self,
        cmd: ShareMintCommand,
    ) -> Result<ShareMintResult, SessionFileUseCaseError> {
        let row = self
            .cfg
            .repo
            .get(&cmd.session_id, &cmd.file_id)
            .await
            .map_err(SessionFileUseCaseError::Internal)?
            .ok_or_else(|| SessionFileUseCaseError::NotFound(format!("file {}", cmd.file_id)))?;
        if row.status != FileStatus::Ready {
            return Err(SessionFileUseCaseError::InvalidState(format!(
                "file status {:?} not Ready — cannot share",
                row.status,
            )));
        }
        if !can_mutate(
            &cmd.caller_identities,
            &row.owner,
            cmd.session_creator.as_deref(),
            cmd.driver_bot.as_deref(),
        ) {
            return Err(SessionFileUseCaseError::Forbidden(format!(
                "caller not authorized to share file {}",
                cmd.file_id,
            )));
        }
        let ttl = cmd
            .ttl_seconds
            .unwrap_or(self.cfg.share_default_ttl)
            .clamp(SHARE_TTL_MIN, SHARE_TTL_MAX);
        let exp = now_secs() + ttl;
        let token = share_token_encode(
            &ShareTokenPayload {
                v: 1,
                file_id: cmd.file_id.clone(),
                exp,
            },
            &self.cfg.share_secret,
        );
        let base = self
            .cfg
            .share_base_url
            .clone()
            .unwrap_or_else(|| self.cfg.bcs_base_url.clone());
        let share_url = format!(
            "{}/sessions/shared-file/content?token={}",
            base,
            token,
        );
        Ok(ShareMintResult {
            share_url,
            share_token: token,
            expires_at: exp,
        })
    }

    async fn share_consume(
        &self,
        token: &str,
    ) -> Result<ShareConsumeResult, SessionFileUseCaseError> {
        let payload = share_token_decode_and_verify(token, &self.cfg.share_secret)
            .map_err(SessionFileUseCaseError::from)?;
        let row = self
            .cfg
            .repo
            .get_by_file_id(&payload.file_id)
            .await
            .map_err(SessionFileUseCaseError::Internal)?
            .ok_or_else(|| SessionFileUseCaseError::NotFound(format!("file {}", payload.file_id)))?;
        if row.status != FileStatus::Ready {
            return Err(SessionFileUseCaseError::InvalidState(format!(
                "file status {:?} not Ready — cannot consume share",
                row.status,
            )));
        }
        // NOTE: returns the full SessionFile row. The HTTP layer is responsible
        // for not leaking internal/out-of-scope fields: the shared-file *meta*
        // handler serializes via `to_shared_dto` (strips `object_handle` AND
        // `session_id`), and the *content* handler streams bytes (no JSON body).
        // Do NOT call a `redacted()` helper — there isn't one.
        Ok(ShareConsumeResult { file: row })
    }

    async fn get_stream(
        &self,
        session_id: &str,
        file_id: &str,
    ) -> Result<(SessionFile, ByteStream), SessionFileUseCaseError> {
        let row = self
            .cfg
            .repo
            .get(session_id, file_id)
            .await
            .map_err(SessionFileUseCaseError::Internal)?
            .ok_or_else(|| SessionFileUseCaseError::NotFound(format!("file {}", file_id)))?;
        if row.status != FileStatus::Ready {
            return Err(SessionFileUseCaseError::InvalidState(format!(
                "file status {:?} not Ready — cannot stream",
                row.status,
            )));
        }
        let handle: StorageHandle = serde_json::from_str(&row.object_handle).map_err(|e| {
            SessionFileUseCaseError::Internal(bcs_service_api::ServiceError::InternalError(
                format!("decode storage handle: {e}"),
            ))
        })?;
        let stream = self
            .cfg
            .storage
            .get_stream(&handle)
            .await
            .map_err(map_storage_err)?;
        Ok((row, stream))
    }

    async fn sweep_expired_pending(&self) -> Result<u64, SessionFileUseCaseError> {
        let now = now_secs();
        let rows = self
            .cfg
            .repo
            .list_expired_pending(now, 100)
            .await
            .map_err(SessionFileUseCaseError::Internal)?;
        let mut swept = 0u64;
        for row in rows {
            let handle: UploadHandle = match serde_json::from_str(&row.object_handle) {
                Ok(h) => h,
                Err(e) => {
                    warn!(
                        error = %e,
                        file_id = %row.file_id,
                        "sweep: decode upload handle failed; marking Failed without backend abort",
                    );
                    let _ = self
                        .cfg
                        .repo
                        .update_status(&row.session_id, &row.file_id, FileStatus::Failed)
                        .await;
                    swept += 1;
                    continue;
                }
            };
            if let Err(e) = self.cfg.storage.abort_upload(&handle).await {
                warn!(
                    error = ?e,
                    file_id = %row.file_id,
                    "sweep: abort_upload failed; still marking row Failed",
                );
            }
            let _ = self
                .cfg
                .repo
                .update_status(&row.session_id, &row.file_id, FileStatus::Failed)
                .await;
            swept += 1;
        }
        Ok(swept)
    }

    async fn delete_all_for_session(&self, session_id: &str) -> Result<u64, SessionFileUseCaseError> {
        // Collect every row for the session WITHOUT deleting yet, so backend
        // cleanup happens BEFORE the metadata row is dropped. The previous flow
        // deleted every row up front (atomic repo `delete_all_for_session`)
        // and only then attempted `storage.delete`; a backend failure there
        // orphaned the object with no metadata row left for any later retry or
        // the pending sweep to find — a silent false-success.
        //
        // Now: per row, attempt backend cleanup first ([`cleanup_backend_for_row`]);
        // only on success do we drop the metadata. On failure we KEEP the row
        // (row + object stay consistent, a retry can find them) and surface a
        // partial-failure error at the end. The session-delete caller logs that
        // error without failing the HTTP response (session is already gone).
        let mut rows = Vec::new();
        let mut offset = 0u32;
        loop {
            let page = self
                .cfg
                .repo
                .list(
                    session_id,
                    SessionFileListParams { prefix: None, status: None, limit: 1000, offset },
                )
                .await
                .map_err(SessionFileUseCaseError::Internal)?;
            let got = page.items.len() as u32;
            rows.extend(page.items);
            if got < 1000 {
                break;
            }
            offset += got;
        }

        let mut deleted = 0u64;
        let mut retained = 0u64;
        for row in rows {
            if self.cleanup_backend_for_row(&row).await {
                self.cfg
                    .repo
                    .delete(&row.session_id, &row.file_id)
                    .await
                    .map_err(SessionFileUseCaseError::Internal)?;
                deleted += 1;
            } else {
                // Backend cleanup failed: keep the metadata row so a later
                // retry / sweep can reconcile (both row and object persist).
                retained += 1;
            }
        }

        if retained > 0 {
            return Err(SessionFileUseCaseError::Internal(
                bcs_service_api::ServiceError::InternalError(format!(
                    "session file cleanup partial failure: {retained} object(s) retained for retry, {deleted} deleted"
                )),
            ));
        }
        Ok(deleted)
    }
}

impl SessionFileServiceImpl {
    /// Attempt backend cleanup for one row during session-delete.
    ///
    /// Returns `true` when the backend holds no remaining object for the row
    /// — either cleanup succeeded, or the status never had a backend object
    /// (`Failed` / `Deleting`), so the metadata row can be dropped. Returns
    /// `false` when cleanup failed; the caller keeps the row for retry.
    async fn cleanup_backend_for_row(&self, row: &SessionFile) -> bool {
        match row.status {
            FileStatus::Ready => {
                let handle: StorageHandle = match serde_json::from_str(&row.object_handle) {
                    Ok(h) => h,
                    Err(e) => {
                        warn!(
                            error = %e,
                            file_id = %row.file_id,
                            "delete_all_for_session: decode storage handle failed; retaining row",
                        );
                        return false;
                    }
                };
                if let Err(e) = self.cfg.storage.delete(&handle).await {
                    warn!(
                        error = ?e,
                        file_id = %row.file_id,
                        "delete_all_for_session: backend delete failed; retaining row + object for retry",
                    );
                    return false;
                }
                true
            }
            FileStatus::Pending => {
                // Pending rows hold an UploadHandle with staged temp parts;
                // abort them so the backend is not left with orphaned multipart
                // data. If abort fails, keep the row so the pending sweep can
                // retry (it re-runs abort_upload before marking Failed).
                let handle: UploadHandle = match serde_json::from_str(&row.object_handle) {
                    Ok(h) => h,
                    Err(e) => {
                        warn!(
                            error = %e,
                            file_id = %row.file_id,
                            "delete_all_for_session: decode upload handle failed; retaining row",
                        );
                        return false;
                    }
                };
                if let Err(e) = self.cfg.storage.abort_upload(&handle).await {
                    warn!(
                        error = ?e,
                        file_id = %row.file_id,
                        "delete_all_for_session: backend abort_upload failed; retaining row for sweep retry",
                    );
                    return false;
                }
                true
            }
            // No final backend object to remove for these states; dropping the
            // metadata row is the only cleanup needed.
            FileStatus::Failed | FileStatus::Deleting => true,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::{ActorKind, ActorRef, Session, SessionKind, SessionStatus};
    use bcs_service_api::port::repo::SessionFileListParams;
    use bcs_session_file_store::MemorySessionFileRepo;
    use bcs_storage_api::{
        ByteStream, StorageCapabilities, StorageHealth, StorageObjectMeta, StoragePlugin,
    };
    use bcs_storage_api::fake::FakeStoragePlugin;
    use futures::StreamExt;

    // ---- Minimal `SessionRepoPort` stub for service tests ------------------
    //
    // The trait surface is large but the service only ever exercises `get`.
    // The remaining methods return empty/Err stubs so the impl compiles
    // without dragging a real session store into this crate.

    #[derive(Default)]
    struct FakeSessionRepo {
        sessions: tokio::sync::RwLock<std::collections::HashMap<String, Session>>,
    }

    impl FakeSessionRepo {
        fn with_session(sid: &str) -> Self {
            Self::with_sessions(&[sid])
        }

        fn with_sessions(sids: &[&str]) -> Self {
            let repo = Self::default();
            // Insert synchronously — fine because we are outside any async
            // runtime at this point (constructor runs before the test body).
            {
                let mut map = repo.sessions.try_write().expect("try_write at construction");
                for sid in sids {
                    let s = Session {
                        id: (*sid).to_string(),
                        group_id: "g1".into(),
                        session_title: None,
                        env: Some("test".into()),
                        status: SessionStatus::Running,
                        session_kind: SessionKind::Chat,
                        participants: vec![],
                        group_version: Some(1),
                        caller_id: None,
                        input: None,
                        output: None,
                        error_message: None,
                        callback_status: None,
                        activation_count: 1,
                        caller_principal: None,
                        created_by: Some("creator_1".into()),
                        current_msg_seq: 0,
                        participant_join_seq: None,
                        created_at: now_secs(),
                        updated_at: now_secs(),
                        completed_at: None,
                        collected_at: None,
                        meta: None,
                    };
                    map.insert((*sid).to_string(), s);
                }
            }
            repo
        }
    }

    #[async_trait]
    impl SessionRepoPort for FakeSessionRepo {
        async fn get(&self, session_id: &str) -> Option<Session> {
            self.sessions.read().await.get(session_id).cloned()
        }
        async fn belongs_to_group(&self, _session_id: &str, _group_id: &str) -> bool { true }
        async fn list_running_service(&self, _offset: u64, _limit: u64) -> Vec<Session> { vec![] }
        async fn count_running_service(&self, _group_id: &str) -> u64 { 0 }

        async fn create(
            &self,
            _group_id: &str,
            _params: bcs_service_api::port::repo::NewSessionParams,
        ) -> bcs_service_api::ServiceResult<Session> {
            Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
        }
        async fn list_by_group(
            &self,
            _group_id: &str,
            _status: Option<SessionStatus>,
            _offset: u64,
            _limit: u64,
            _title_contains: Option<&str>,
            _participant_id: Option<&str>,
        ) -> Vec<Session> { vec![] }
        async fn latest_running(&self, _group_id: &str) -> Option<Session> { None }
        async fn complete_if_running(
            &self,
            _session_id: &str,
            _output: Option<serde_json::Value>,
            _error: Option<String>,
        ) -> bcs_service_api::ServiceResult<Option<Session>> {
            Ok(None)
        }
        async fn reactivate(
            &self,
            _session_id: &str,
            _new_input: Option<serde_json::Value>,
        ) -> bcs_service_api::ServiceResult<Session> {
            Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
        }
        async fn add_participant(
            &self,
            _session_id: &str,
            _participant: bcs_service_api::types::Participant,
        ) -> bcs_service_api::ServiceResult<Session> {
            Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
        }
        async fn remove_participant(
            &self,
            _session_id: &str,
            _bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<Session> {
            Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
        }
        async fn update_participant_mode(
            &self,
            _session_id: &str,
            _bot_uuid: &str,
            _mode: bcs_service_api::types::ParticipantMode,
        ) -> bcs_service_api::ServiceResult<Session> {
            Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
        }
        async fn update_callback_status(
            &self,
            _session_id: &str,
            _status: &str,
        ) -> bcs_service_api::ServiceResult<()> {
            Ok(())
        }
        async fn update_title(
            &self,
            _session_id: &str,
            _title: Option<String>,
        ) -> bcs_service_api::ServiceResult<Session> {
            Err(bcs_service_api::ServiceError::InternalError("unsupported".into()))
        }
        async fn list_group_ids_by_session_participant(&self, _bot_uuid: &str) -> Vec<String> {
            vec![]
        }
        async fn delete(&self, _session_id: &str) -> bcs_service_api::ServiceResult<bool> {
            Ok(false)
        }
    }

    // ---- Test scaffolding ---------------------------------------------------

    fn local_caps() -> StorageCapabilities {
        StorageCapabilities {
            supports_presign_put: false,
            supports_presign_download: false,
            supports_stream_put: true,
            supports_stream_get: true,
            max_object_size: 5_000_000_000,
        }
    }

    fn presign_caps() -> StorageCapabilities {
        StorageCapabilities {
            supports_presign_put: true,
            supports_presign_download: true,
            supports_stream_put: true,
            supports_stream_get: true,
            max_object_size: 5_000_000_000,
        }
    }

    fn actor(id: &str) -> ActorRef {
        ActorRef { actor_kind: ActorKind::Human, actor_id: id.into() }
    }

    /// A presign-capable fake plugin for `download_route` coverage. Reuses
    /// `FakeStoragePlugin` storage mechanics but flips the caps so the service
    /// routes `download_route` through `presign_get`.
    fn build_svc(
        caps: StorageCapabilities,
    ) -> (SessionFileServiceImpl, Arc<dyn StoragePlugin>, Arc<dyn SessionFileRepoPort>) {
        let storage: Arc<dyn StoragePlugin> = Arc::new(FakeStoragePlugin::new(caps));
        let repo: Arc<dyn SessionFileRepoPort> = Arc::new(MemorySessionFileRepo::new());
        let session_repo: Arc<dyn SessionRepoPort> =
            Arc::new(FakeSessionRepo::with_sessions(&["g1:abcd1234", "g1:other"]));
        let cfg = SessionFileServiceConfig {
            storage: storage.clone(),
            repo: repo.clone(),
            session_repo,
            env: "test".into(),
            max_size: 5_000_000_000,
            multipart_threshold: 100 * 1024 * 1024,
            bcs_base_url: "http://bcs:21000".into(),
            share_secret: b"k".to_vec(),
            share_default_ttl: 3600,
            share_link_ttl: 7777,
            share_base_url: None,
        };
        (SessionFileServiceImpl::new(cfg), storage, repo)
    }

    async fn collect_stream(mut s: ByteStream) -> Vec<u8> {
        let mut out = Vec::new();
        while let Some(chunk) = s.next().await {
            out.extend_from_slice(&chunk.unwrap());
        }
        out
    }

    fn sample_prepare(size: u64) -> PrepareUploadCommand {
        PrepareUploadCommand {
            session_id: "g1:abcd1234".into(),
            file_name: "x.txt".into(),
            size,
            mime_type: "text/plain".into(),
            caller: actor("human_1"),
        }
    }

    // ---- Step 3: prepare_upload ---------------------------------------------

    #[tokio::test]
    async fn prepare_single_returns_proxy_url_and_pending_row() {
        let (s, _, repo) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(100)).await.unwrap();
        // row is Pending
        let row = repo.get("g1:abcd1234", &r.file.file_id).await.unwrap().unwrap();
        assert_eq!(row.status, FileStatus::Pending);
        // wire payload shape (§1.2.a)
        assert_eq!(r.client_target_json["mode"], "single");
        assert!(r.client_target_json["upload_url"]
            .as_str()
            .unwrap()
            .starts_with("http://bcs:21000/sessions/g1%3Aabcd1234/files/"));
        assert_eq!(r.client_target_json["method"], "PUT");
        assert!(r.client_target_json["expires_at"].is_u64());
        // expires_at echoes at outer envelope
        assert!(r.expires_at > 0);
    }

    #[tokio::test]
    async fn prepare_multipart_returns_parts_and_aligned_part_count() {
        let (s, _, _) = build_svc(local_caps());
        // 150 MiB → multipart (threshold 100 MiB), part_size 10 MiB → 15 parts.
        let size: u64 = 150 * 1024 * 1024;
        let r = s.prepare_upload(sample_prepare(size)).await.unwrap();
        assert_eq!(r.client_target_json["mode"], "multipart");
        assert_eq!(r.client_target_json["part_size"], PROXY_PART_SIZE);
        assert_eq!(r.client_target_json["part_count"], 15u32);
        let parts = r.client_target_json["parts"].as_array().unwrap();
        assert_eq!(parts.len(), 15);
        // part 1 URL carries ?part=1 — assert via json
        assert_eq!(parts[0]["part_number"], 1u32);
        assert!(parts[0]["upload_url"].as_str().unwrap().ends_with("?part=1"));
        assert_eq!(parts[14]["part_number"], 15u32);
        assert!(r.client_target_json["expires_at"].is_u64());
    }

    #[tokio::test]
    async fn prepare_rejects_size_above_max() {
        let (s, _, _) = build_svc(local_caps());
        let cmd = PrepareUploadCommand {
            session_id: "g1:abcd1234".into(),
            size: 6_000_000_000, // > 5_000_000_000
            ..sample_prepare(0)
        };
        let err = s.prepare_upload(cmd).await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::PayloadTooLarge(_)));
    }

    #[tokio::test]
    async fn prepare_rejects_part_count_overflow() {
        // 65536 parts of 10 MiB would be 65536 * 10 MiB > 5 GiB max — covered by
        // the size guard earlier. Use a deliberately oversized max_size to
        // isolate the part_count guard and confirm it triggers.
        let unbounded_caps = StorageCapabilities {
            supports_presign_put: false,
            supports_presign_download: false,
            supports_stream_put: true,
            supports_stream_get: true,
            max_object_size: u64::MAX,
        };
        let storage: Arc<dyn StoragePlugin> = Arc::new(FakeStoragePlugin::new(unbounded_caps));
        let repo: Arc<dyn SessionFileRepoPort> = Arc::new(MemorySessionFileRepo::new());
        let session_repo: Arc<dyn SessionRepoPort> =
            Arc::new(FakeSessionRepo::with_session("g1:abcd1234"));
        let cfg = SessionFileServiceConfig {
            storage: storage.clone(),
            repo: repo.clone(),
            session_repo,
            env: "test".into(),
            max_size: u64::MAX,
            multipart_threshold: 100 * 1024 * 1024,
            bcs_base_url: "http://bcs:21000".into(),
            share_secret: b"k".to_vec(),
            share_default_ttl: 3600,
            share_link_ttl: 7777,
            share_base_url: None,
        };
        let svc = SessionFileServiceImpl::new(cfg);
        // 65536 parts of 10 MiB
        let size = MAX_PART_COUNT * PROXY_PART_SIZE + PROXY_PART_SIZE;
        let err = svc.prepare_upload(sample_prepare(size)).await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::PayloadTooLarge(_)));
    }

    #[tokio::test]
    async fn prepare_returns_not_found_when_session_missing() {
        let (s, _, _) = build_svc(local_caps());
        let cmd = PrepareUploadCommand {
            session_id: "no-such-session".into(),
            ..sample_prepare(10)
        };
        let err = s.prepare_upload(cmd).await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::NotFound(_)));
    }

    #[tokio::test]
    async fn prepare_rejects_path_traversal_file_name() {
        // A file_name carrying path separators / `..` must be rejected before
        // it reaches `derive_key`, otherwise `data_dir.join(key)` in the local
        // backend resolves outside the session-files root.
        let (s, _, repo) = build_svc(local_caps());
        for bad in ["../../etc/passwd", "a/b", "a\\b", "..", ".", "evil\0.txt", ""] {
            let cmd = PrepareUploadCommand {
                file_name: bad.into(),
                ..sample_prepare(5)
            };
            let err = s.prepare_upload(cmd).await.unwrap_err();
            assert!(
                matches!(err, SessionFileUseCaseError::InvalidInput(_)),
                "expected InvalidInput for file_name {bad:?}, got {err:?}"
            );
        }
        // No row was created for any rejected name.
        let page = repo
            .list("g1:abcd1234", SessionFileListParams::default())
            .await
            .unwrap();
        assert_eq!(page.items.len(), 0);

        // A safe (including non-ASCII) name still succeeds.
        let r = s
            .prepare_upload(PrepareUploadCommand {
                file_name: "自由.txt".into(),
                ..sample_prepare(5)
            })
            .await
            .unwrap();
        assert_eq!(r.file.file_name, "自由.txt");
    }

    // ---- stream + complete roundtrip ----------------------------------------

    #[tokio::test]
    async fn stream_and_complete_single_roundtrip_flips_row_to_ready() {
        let (s, _, repo) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        s.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 5)
            .await
            .unwrap();
        let f = s.complete_upload("g1:abcd1234", &r.file.file_id).await.unwrap();
        assert_eq!(f.status, FileStatus::Ready);
        assert_eq!(f.size, 5);
        let row = repo.get("g1:abcd1234", &r.file.file_id).await.unwrap().unwrap();
        assert_eq!(row.status, FileStatus::Ready);
    }

    #[tokio::test]
    async fn stream_rejects_non_pending_row() {
        let (s, _, _) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        s.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 5).await.unwrap();
        s.complete_upload("g1:abcd1234", &r.file.file_id).await.unwrap();
        // Now Ready — a second stream_upload should Conflict.
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hi"));
        let err = s.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 2).await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::Conflict(_)));
    }

    #[tokio::test]
    async fn stream_rejects_size_mismatch_single_part() {
        let (s, _, _) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(10)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"abc"));
        let err = s.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 3).await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::InvalidInput(_)));
    }

    #[tokio::test]
    async fn complete_returns_not_found_for_unknown_file() {
        let (s, _, _) = build_svc(local_caps());
        let err = s.complete_upload("g1:abcd1234", "nope").await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::NotFound(_)));
    }

    // ---- delete routing -----------------------------------------------------

    fn delete_cmd(file_id: &str, caller_ids: &[&str]) -> DeleteFileCommand {
        let caller_identities = caller_ids.iter().map(|s| (*s).to_string()).collect();
        DeleteFileCommand {
            session_id: "g1:abcd1234".into(),
            file_id: file_id.into(),
            caller: actor("human_1"),
            caller_identities,
            session_creator: Some("creator_1".into()),
            driver_bot: None,
        }
    }

    #[tokio::test]
    async fn delete_ready_file_routes_to_backend_delete() {
        let (s, _, repo) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        s.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 5).await.unwrap();
        s.complete_upload("g1:abcd1234", &r.file.file_id).await.unwrap();
        s.delete_file(delete_cmd(&r.file.file_id, &["human_1"])).await.unwrap();
        assert!(repo.get("g1:abcd1234", &r.file.file_id).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn delete_pending_file_routes_to_abort_upload() {
        let (s, _, repo) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        // never uploaded — still Pending
        assert_eq!(r.file.status, FileStatus::Pending);
        s.delete_file(delete_cmd(&r.file.file_id, &["human_1"])).await.unwrap();
        assert!(repo.get("g1:abcd1234", &r.file.file_id).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn delete_non_owner_returns_forbidden() {
        let (s, _, repo) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let err = s.delete_file(delete_cmd(&r.file.file_id, &["someone_else"])).await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::Forbidden(_)));
        // Row still present
        assert!(repo.get("g1:abcd1234", &r.file.file_id).await.unwrap().is_some());
    }

    #[tokio::test]
    async fn delete_creator_can_delete_others_file() {
        let (s, _, repo) = build_svc(local_caps());
        // prepare with owner human_42; creator_1 should still be allowed via session_creator.
        let mut cmd = sample_prepare(5);
        cmd.caller = actor("human_42");
        let r = s.prepare_upload(cmd).await.unwrap();
        s.delete_file(delete_cmd(&r.file.file_id, &["creator_1"])).await.unwrap();
        assert!(repo.get("g1:abcd1234", &r.file.file_id).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn delete_driver_bot_can_delete() {
        let (s, _, repo) = build_svc(local_caps());
        let mut cmd = sample_prepare(5);
        cmd.caller = actor("human_42");
        let r = s.prepare_upload(cmd).await.unwrap();
        let mut dc = delete_cmd(&r.file.file_id, &["bot-driver"]);
        dc.session_creator = None;
        dc.driver_bot = Some("bot-driver".into());
        s.delete_file(dc).await.unwrap();
        assert!(repo.get("g1:abcd1234", &r.file.file_id).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn delete_missing_row_is_idempotent_204() {
        let (s, _, _) = build_svc(local_caps());
        // No row → Ok(()) — service does not probe backend.
        s.delete_file(delete_cmd("never-existed", &["human_1"])).await.unwrap();
    }

    // ---- download route -----------------------------------------------------

    #[tokio::test]
    async fn download_route_local_returns_no_presign() {
        let (s, _, _) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        s.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 5).await.unwrap();
        s.complete_upload("g1:abcd1234", &r.file.file_id).await.unwrap();
        let (_file, route) = s.download_route("g1:abcd1234", &r.file.file_id, None).await.unwrap();
        assert!(route.presign.is_none());
    }

    #[tokio::test]
    async fn download_route_presign_backend_returns_ticket() {
        let (s, _, _) = build_svc(presign_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        s.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 5).await.unwrap();
        s.complete_upload("g1:abcd1234", &r.file.file_id).await.unwrap();
        let (_file, route) = s.download_route("g1:abcd1234", &r.file.file_id, Some(60)).await.unwrap();
        let ticket = route.presign.unwrap();
        assert!(ticket.download_url.starts_with("fake://"));
    }

    #[tokio::test]
    async fn download_route_uses_share_link_ttl_when_no_query_ttl() {
        // presign-capable backend so download_route goes through presign_get.
        let (s, _storage, _repo) = build_svc(presign_caps());
        // share_link_ttl in build_svc is set to 7777 (asserted below via expires_at).
        let r1 = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        s.stream_upload("g1:abcd1234", &r1.file.file_id, None, body, 5).await.unwrap();
        s.complete_upload("g1:abcd1234", &r1.file.file_id).await.unwrap();

        // download_route(..., None) should pass share_link_ttl (7777) to presign_get.
        let (_row, route) = s.download_route("g1:abcd1234", &r1.file.file_id, None).await.unwrap();
        let ticket = route.presign.expect("presign backend returns a ticket");
        // FakeStoragePlugin.presign_get sets expires_at = ttl_secs.
        assert_eq!(ticket.expires_at, 7777,
            "expected share_link_ttl(7777) propagated to presign_get, got expires_at={}", ticket.expires_at);
    }

    #[tokio::test]
    async fn download_route_rejects_non_ready() {
        let (s, _, _) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let err = s.download_route("g1:abcd1234", &r.file.file_id, None).await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::InvalidState(_)));
    }

    // ---- get_stream ---------------------------------------------------------

    #[tokio::test]
    async fn get_stream_roundtrips_bytes() {
        let (s, _, _) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let payload = bytes::Bytes::from_static(b"hello");
        let body = bcs_storage_api::byte_stream_from_bytes(payload.clone());
        s.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 5).await.unwrap();
        s.complete_upload("g1:abcd1234", &r.file.file_id).await.unwrap();
        let (file, stream) = s.get_stream("g1:abcd1234", &r.file.file_id).await.unwrap();
        assert_eq!(file.size, 5);
        let got = collect_stream(stream).await;
        assert_eq!(got, payload.as_ref());
    }

    // ---- share mint + consume -----------------------------------------------

    fn share_cmd(file_id: &str, ids: &[&str]) -> ShareMintCommand {
        let caller_identities = ids.iter().map(|s| (*s).to_string()).collect();
        ShareMintCommand {
            session_id: "g1:abcd1234".into(),
            file_id: file_id.into(),
            caller: actor("human_1"),
            ttl_seconds: None,
            caller_identities,
            session_creator: Some("creator_1".into()),
            driver_bot: None,
        }
    }

    async fn prepare_complete(s: &SessionFileServiceImpl) -> String {
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        s.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 5).await.unwrap();
        s.complete_upload("g1:abcd1234", &r.file.file_id).await.unwrap();
        r.file.file_id
    }

    #[tokio::test]
    async fn share_mint_and_consume_roundtrip() {
        let (s, _, _) = build_svc(local_caps());
        let file_id = prepare_complete(&s).await;
        let r = s.share_mint(share_cmd(&file_id, &["human_1"])).await.unwrap();
        assert!(r.share_url.contains("/sessions/shared-file/content?token="));
        assert!(!r.share_token.is_empty());
        assert!(r.expires_at > now_secs());
        let consumed = s.share_consume(&r.share_token).await.unwrap();
        assert_eq!(consumed.file.file_id, file_id);
        assert_eq!(consumed.file.status, FileStatus::Ready);
    }

    #[tokio::test]
    async fn share_mint_non_owner_forbidden() {
        let (s, _, _) = build_svc(local_caps());
        let file_id = prepare_complete(&s).await;
        let err = s.share_mint(share_cmd(&file_id, &["someone_else"])).await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::Forbidden(_)));
    }

    #[tokio::test]
    async fn share_mint_non_ready_rejects() {
        let (s, _, _) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let err = s.share_mint(share_cmd(&r.file.file_id, &["human_1"])).await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::InvalidState(_)));
    }

    #[tokio::test]
    async fn share_consume_expired_token_rejected() {
        let (s, _, _) = build_svc(local_caps());
        let file_id = prepare_complete(&s).await;
        // Build an already-expired token directly from the share library to
        // exercise the expired-share branch.
        let exp = now_secs() - 10;
        let token = share_token_encode(
            &ShareTokenPayload { v: 1, file_id: file_id.clone(), exp },
            b"k",
        );
        let err = s.share_consume(&token).await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::InvalidState(_)));
    }

    #[tokio::test]
    async fn share_consume_tampered_token_rejected() {
        let (s, _, _) = build_svc(local_caps());
        let file_id = prepare_complete(&s).await;
        let r = s.share_mint(share_cmd(&file_id, &["human_1"])).await.unwrap();
        // Tamper: flip one character in the token.
        let mut chars: Vec<char> = r.share_token.chars().collect();
        let last_idx = chars.len() - 1;
        let last = chars[last_idx];
        chars[last_idx] = if last == 'a' { 'b' } else { 'a' };
        let tampered: String = chars.into_iter().collect();
        let err = s.share_consume(&tampered).await.unwrap_err();
        // Tampered token → InvalidInput (per `From<ShareTokenError>` mapping)
        // or InvalidSignature → InvalidInput.
        assert!(matches!(err, SessionFileUseCaseError::InvalidInput(_)));
    }

    #[tokio::test]
    async fn share_mint_uses_share_base_url_when_configured() {
        let (s, _, _) = build_svc_parts_for_share_base();
        let file_id = prepare_complete(&s).await;
        let r = s.share_mint(share_cmd(&file_id, &["human_1"])).await.unwrap();
        assert!(r.share_url.starts_with("https://share.example.com/sessions/shared-file/content?token="));
    }

    fn build_svc_parts_for_share_base() -> (SessionFileServiceImpl, Arc<dyn StoragePlugin>, Arc<dyn SessionFileRepoPort>) {
        let storage: Arc<dyn StoragePlugin> = Arc::new(FakeStoragePlugin::new(local_caps()));
        let repo: Arc<dyn SessionFileRepoPort> = Arc::new(MemorySessionFileRepo::new());
        let session_repo: Arc<dyn SessionRepoPort> =
            Arc::new(FakeSessionRepo::with_sessions(&["g1:abcd1234"]));
        let cfg = SessionFileServiceConfig {
            storage: storage.clone(),
            repo: repo.clone(),
            session_repo,
            env: "test".into(),
            max_size: 5_000_000_000,
            multipart_threshold: 100 * 1024 * 1024,
            bcs_base_url: "http://bcs:21000".into(),
            share_secret: b"k".to_vec(),
            share_default_ttl: 3600,
            share_link_ttl: 3600,
            share_base_url: Some("https://share.example.com".into()),
        };
        (SessionFileServiceImpl::new(cfg), storage, repo)
    }

    // ---- capabilities -------------------------------------------------------

    #[tokio::test]
    async fn capabilities_reports_backend_and_max_size() {
        let (s, _, _) = build_svc(local_caps());
        let c = s.capabilities().await;
        assert_eq!(c.storage, "fake");
        assert!(!c.presign_upload);
        assert!(!c.presign_download);
        assert_eq!(c.max_size, 5_000_000_000);
    }

    // ---- list ---------------------------------------------------------------

    #[tokio::test]
    async fn list_returns_rows_for_session() {
        let (s, _, _) = build_svc(local_caps());
        for i in 0..3 {
            let mut cmd = sample_prepare(5);
            cmd.file_name = format!("f{i}.txt");
            s.prepare_upload(cmd).await.unwrap();
        }
        let page = s.list(
            "g1:abcd1234",
            SessionFileListParams { prefix: None, status: None, limit: 100, offset: 0 },
        ).await.unwrap();
        assert_eq!(page.items.len(), 3);
        assert_eq!(page.total, 3);
    }

    // ---- sweep ---------------------------------------------------------------

    #[tokio::test]
    async fn sweep_marks_expired_pending_as_failed() {
        let (s, _, repo) = build_svc(local_caps());
        let r = s.prepare_upload(sample_prepare(5)).await.unwrap();
        // Force-expire: rewrite object_handle's expires_at to the past.
        let past = now_secs() - 100;
        let row = repo.get("g1:abcd1234", &r.file.file_id).await.unwrap().unwrap();
        let mut handle: serde_json::Value = serde_json::from_str(&row.object_handle).unwrap();
        handle["expires_at"] = serde_json::json!(past);
        repo.update_object_handle_and_status(
            "g1:abcd1234",
            &r.file.file_id,
            &handle.to_string(),
            FileStatus::Pending,
            row.size,
        ).await.unwrap();
        let swept = s.sweep_expired_pending().await.unwrap();
        assert_eq!(swept, 1);
        let after = repo.get("g1:abcd1234", &r.file.file_id).await.unwrap().unwrap();
        assert_eq!(after.status, FileStatus::Failed);
    }

    // ---- delete_all_for_session ----------------------------------------------

    #[tokio::test]
    async fn delete_all_for_session_removes_rows_and_calls_backend_delete_on_ready() {
        let (s, _, repo) = build_svc(local_caps());
        // Two files: one Ready, one Pending.
        let r1 = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        s.stream_upload("g1:abcd1234", &r1.file.file_id, None, body, 5).await.unwrap();
        s.complete_upload("g1:abcd1234", &r1.file.file_id).await.unwrap();

        let r2 = s.prepare_upload(sample_prepare(10)).await.unwrap(); // Pending

        // Add a second session to confirm we don't touch it.
        let r3 = s.prepare_upload(PrepareUploadCommand {
            session_id: "g1:other".into(),
            file_name: "y.txt".into(),
            size: 3,
            mime_type: "text/plain".into(),
            caller: actor("human_1"),
        }).await.unwrap();

        let deleted = s.delete_all_for_session("g1:abcd1234").await.unwrap();
        assert_eq!(deleted, 2);
        assert!(repo.get("g1:abcd1234", &r1.file.file_id).await.unwrap().is_none());
        assert!(repo.get("g1:abcd1234", &r2.file.file_id).await.unwrap().is_none());
        // Other session untouched.
        assert!(repo.get("g1:other", &r3.file.file_id).await.unwrap().is_some());
    }

    #[tokio::test]
    async fn delete_all_for_session_retains_row_when_backend_delete_fails() {
        // Regression for the orphan-on-failure bug: when `storage.delete` fails
        // for a Ready row, the metadata row MUST be retained (cleanup happens
        // before the row is dropped) so a later retry can find it, and the call
        // surfaces a partial-failure error instead of false success. A Pending
        // row whose abort succeeds is still cleaned — partial progress.
        let storage: Arc<dyn StoragePlugin> = Arc::new(FailingDeleteStorage {
            inner: FakeStoragePlugin::new(local_caps()),
        });
        let (s, repo) = build_svc_with_storage(storage);

        // Ready file (backend delete will fail).
        let r1 = s.prepare_upload(sample_prepare(5)).await.unwrap();
        let body = bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        s.stream_upload("g1:abcd1234", &r1.file.file_id, None, body, 5).await.unwrap();
        s.complete_upload("g1:abcd1234", &r1.file.file_id).await.unwrap();
        // Pending file (abort succeeds → cleaned).
        let r2 = s.prepare_upload(sample_prepare(10)).await.unwrap();

        let err = s.delete_all_for_session("g1:abcd1234").await.unwrap_err();
        assert!(
            matches!(err, SessionFileUseCaseError::Internal(_)),
            "expected partial-failure Internal error, got {err:?}"
        );
        // Ready row retained for retry (NOT orphaned — row + object both persist).
        let row = repo
            .get("g1:abcd1234", &r1.file.file_id)
            .await
            .unwrap()
            .expect("Ready row retained after failed backend delete");
        assert_eq!(row.status, FileStatus::Ready);
        // Pending row was cleaned (abort succeeded) despite the Ready failure.
        assert!(repo.get("g1:abcd1234", &r2.file.file_id).await.unwrap().is_none());
    }

    /// `build_svc` variant that takes a caller-supplied storage plugin (used to
    /// inject backend failures).
    fn build_svc_with_storage(
        storage: Arc<dyn StoragePlugin>,
    ) -> (SessionFileServiceImpl, Arc<dyn SessionFileRepoPort>) {
        let repo: Arc<dyn SessionFileRepoPort> = Arc::new(MemorySessionFileRepo::new());
        let session_repo: Arc<dyn SessionRepoPort> =
            Arc::new(FakeSessionRepo::with_sessions(&["g1:abcd1234", "g1:other"]));
        let cfg = SessionFileServiceConfig {
            storage: storage.clone(),
            repo: repo.clone(),
            session_repo,
            env: "test".into(),
            max_size: 5_000_000_000,
            multipart_threshold: 100 * 1024 * 1024,
            bcs_base_url: "http://bcs:21000".into(),
            share_secret: b"k".to_vec(),
            share_default_ttl: 3600,
            share_link_ttl: 3600,
            share_base_url: None,
        };
        (SessionFileServiceImpl::new(cfg), repo)
    }

    /// A `StoragePlugin` that delegates the upload lifecycle to
    /// `FakeStoragePlugin` but forces `delete` to fail — used to exercise the
    /// retain-on-failure path of `delete_all_for_session`.
    struct FailingDeleteStorage {
        inner: FakeStoragePlugin,
    }

    #[async_trait]
    impl StoragePlugin for FailingDeleteStorage {
        fn backend_name(&self) -> &'static str {
            self.inner.backend_name()
        }
        fn capabilities(&self) -> StorageCapabilities {
            self.inner.capabilities()
        }
        async fn prepare_upload(
            &self,
            req: UploadPrepareRequest,
        ) -> Result<PreparedUpload, StorageError> {
            self.inner.prepare_upload(req).await
        }
        async fn stream_upload(
            &self,
            handle: &UploadHandle,
            part_number: Option<u16>,
            body: ByteStream,
        ) -> Result<(), StorageError> {
            self.inner.stream_upload(handle, part_number, body).await
        }
        async fn complete_upload(
            &self,
            handle: &UploadHandle,
        ) -> Result<StorageObjectMeta, StorageError> {
            self.inner.complete_upload(handle).await
        }
        async fn abort_upload(&self, handle: &UploadHandle) -> Result<(), StorageError> {
            self.inner.abort_upload(handle).await
        }
        async fn get_stream(&self, handle: &StorageHandle) -> Result<ByteStream, StorageError> {
            self.inner.get_stream(handle).await
        }
        async fn presign_get(
            &self,
            handle: &StorageHandle,
            ttl_secs: u64,
        ) -> Result<PresignGetTicket, StorageError> {
            self.inner.presign_get(handle, ttl_secs).await
        }
        async fn delete(&self, _handle: &StorageHandle) -> Result<(), StorageError> {
            Err(StorageError::Backend(anyhow::anyhow!("forced delete failure")))
        }
        async fn health_check(&self) -> Result<StorageHealth, StorageError> {
            self.inner.health_check().await
        }
    }

    /// Minimal presign_put backend whose complete_upload returns size=0 and a
    /// parts-less backend_handle (mirrors baas single). Used to assert service
    /// skips the size-mismatch defense for presign_put backends.
    #[derive(Default)]
    struct PresignSizelessComplete {
        staging: Arc<tokio::sync::Mutex<bytes::Bytes>>,
    }

    #[async_trait]
    impl StoragePlugin for PresignSizelessComplete {
        fn backend_name(&self) -> &'static str {
            "sizeless"
        }
        fn capabilities(&self) -> StorageCapabilities {
            StorageCapabilities {
                supports_presign_put: true,
                supports_presign_download: false,
                supports_stream_put: true,
                supports_stream_get: true,
                max_object_size: u64::MAX,
            }
        }
        async fn prepare_upload(
            &self,
            req: UploadPrepareRequest,
        ) -> Result<PreparedUpload, StorageError> {
            Ok(PreparedUpload {
                handle: UploadHandle {
                    backend: "sizeless".into(),
                    key: req.key.clone(),
                    backend_handle: serde_json::json!({ "transfer_id": "t", "type": "SINGLE" }),
                    expires_at: req.ttl_secs,
                },
                client_target: ClientUploadTarget::ProxyViaBcs,
                expires_at: req.ttl_secs,
            })
        }
        async fn stream_upload(
            &self,
            _h: &UploadHandle,
            _p: Option<u16>,
            mut b: ByteStream,
        ) -> Result<(), StorageError> {
            let mut v = Vec::new();
            while let Some(c) = b.next().await {
                v.extend_from_slice(&c.unwrap());
            }
            *self.staging.lock().await = bytes::Bytes::from(v);
            Ok(())
        }
        async fn complete_upload(
            &self,
            _h: &UploadHandle,
        ) -> Result<StorageObjectMeta, StorageError> {
            Ok(StorageObjectMeta {
                key: "k".into(),
                size: 0,
                sha256: None,
            })
        }
        async fn abort_upload(&self, _: &UploadHandle) -> Result<(), StorageError> {
            Ok(())
        }
        async fn get_stream(&self, _: &StorageHandle) -> Result<ByteStream, StorageError> {
            unimplemented!()
        }
        async fn presign_get(
            &self,
            _: &StorageHandle,
            t: u64,
        ) -> Result<PresignGetTicket, StorageError> {
            Ok(PresignGetTicket {
                download_url: "x".into(),
                expires_at: t,
            })
        }
        async fn delete(&self, _: &StorageHandle) -> Result<(), StorageError> {
            Ok(())
        }
        async fn health_check(&self) -> Result<StorageHealth, StorageError> {
            Ok(StorageHealth {
                ok: true,
                detail: None,
            })
        }
    }

    #[tokio::test]
    async fn complete_presign_backend_single_skips_size_mismatch_check() {
        // presign_put backend whose complete_upload returns size=0 (no size in
        // response), single-part (backend_handle has no "parts"). service must
        // NOT reject this as Conflict (P1-A: baas complete response carries no
        // size).
        let storage: Arc<dyn StoragePlugin> = Arc::new(PresignSizelessComplete::default());
        let (svc, _repo) = build_svc_with_storage(storage);
        let r = svc.prepare_upload(sample_prepare(5)).await.unwrap();
        let body =
            bcs_storage_api::byte_stream_from_bytes(bytes::Bytes::from_static(b"hello"));
        svc.stream_upload("g1:abcd1234", &r.file.file_id, None, body, 5)
            .await
            .unwrap();
        let ready = svc
            .complete_upload("g1:abcd1234", &r.file.file_id)
            .await
            .unwrap();
        assert_eq!(ready.status, FileStatus::Ready); // not rejected as Conflict
    }

    // ---- misc / trait sanity ------------------------------------------------

    #[tokio::test]
    async fn get_returns_not_found_for_unknown() {
        let (s, _, _) = build_svc(local_caps());
        let err = s.get("g1:abcd1234", "nope").await.unwrap_err();
        assert!(matches!(err, SessionFileUseCaseError::NotFound(_)));
    }

    // Silence unused-import noise for the presign_caps helper in build modes
    // where the test using it is the only consumer.
    #[test]
    fn _presign_caps_referenced() {
        let _ = presign_caps();
    }

    // Reference the `Storage` type so unused warnings stay quiet (import is
    // declared but only used in type signature of `with_session`).
    #[test]
    fn _session_repo_compiles() {
        let _ = FakeSessionRepo::default();
    }
}