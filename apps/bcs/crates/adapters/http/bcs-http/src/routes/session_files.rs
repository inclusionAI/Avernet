//! Session file workspace HTTP routes.
//!
//! Surface: 11 handlers covering three-stage upload (prepare / upload /
//! complete), delete, list, get, capabilities, download (302/stream), share
//! mint, shared-file meta/content. Mutate authz (delete/share) is resolved in
//! the HTTP layer (`session_creator` + `driver_bot` + `caller_identities`) and
//! fed to the service via commands; the HTTP layer never judges ownership.
//!
//! Route-registration ordering note: `/files/capabilities` is registered before
//! `/files/{file_id}` — axum matchit is static-first by default, and an
//! explicit regression test guards it.

use axum::{
    Json,
    body::{Body, BodyDataStream},
    extract::{Path, Query, State},
    http::{header, HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Redirect, Response},
};
use bytes::Bytes;
use futures::Stream;
use std::pin::Pin;
use std::task::{Context, Poll};
use bcs_domain::{ActorKind, ActorRef, FileStatus, SessionFile};
use bcs_service_api::application::session_files::{
    CapabilitiesView, DeleteFileCommand, PrepareUploadCommand, SessionFileUseCaseError,
    ShareMintCommand,
};
use bcs_service_api::application::v1::{
    ApplicationError, CompleteSessionFile, SessionFileActorKind, SessionFileStatus, SessionFileView,
};
use bcs_service_api::port::repo::SessionFileListParams;
use serde::Deserialize;
use serde_json::json;

use bcs_storage_api::{ByteStream, ByteStreamTrait};

use crate::routes::group_messages::{
    GroupChatCaller, application_caller, resolve_group_chat_caller,
};
use crate::state::HttpAppState;

/// Adapts an axum `Body` data stream into a `ByteStream` for proxy upload
/// ingestion, without buffering the whole request body into memory. This lets
/// the `PUT .../content` route accept large single-part uploads (bytes stream
/// straight to the storage backend) and sidesteps axum's default 2 MiB body
/// limit that a buffering `Bytes` extractor would impose (the route also sets
/// `DefaultBodyLimit::disable`). When a client sends an oversize body, the
/// backend's per-chunk cap rejects it mid-stream rather than after buffering.
struct RequestBodyStream(BodyDataStream);

impl Stream for RequestBodyStream {
    type Item = Result<Bytes, std::io::Error>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        match Pin::new(&mut self.0).poll_next(cx) {
            Poll::Ready(Some(Ok(b))) => Poll::Ready(Some(Ok(b))),
            Poll::Ready(Some(Err(e))) => Poll::Ready(Some(Err(std::io::Error::other(e)))),
            Poll::Ready(None) => Poll::Ready(None),
            Poll::Pending => Poll::Pending,
        }
    }
}
impl ByteStreamTrait for RequestBodyStream {}

// ---------------------------------------------------------------
// DTOs
// ---------------------------------------------------------------

#[derive(serde::Serialize)]
struct ActorRefDto {
    actor_kind: String,
    actor_id: String,
}

#[derive(serde::Serialize)]
struct SessionFileDto {
    file_id: String,
    session_id: String,
    file_name: String,
    mime_type: String,
    size: u64,
    sha256: Option<String>,
    owner: ActorRefDto,
    storage_backend: String,
    status: String,
    created_at: u64,
    updated_at: u64,
    // NOTE: `object_handle` intentionally omitted — internal only, never leaked.
}

fn status_slug(status: &FileStatus) -> &'static str {
    use bcs_domain::FileStatus::*;
    match status {
        Pending => "Pending",
        Ready => "Ready",
        Deleting => "Deleting",
        Failed => "Failed",
    }
}

/// Wire DTO for the shared-file meta endpoint — omits `session_id` so the
/// share consumer never learns which session the file belongs to.
#[derive(serde::Serialize)]
struct SharedFileMetaDto {
    file_id: String,
    file_name: String,
    mime_type: String,
    size: u64,
    sha256: Option<String>,
    owner: ActorRefDto,
    storage_backend: String,
    status: String,
    created_at: u64,
    updated_at: u64,
}

fn to_shared_dto(f: &SessionFile) -> SharedFileMetaDto {
    SharedFileMetaDto {
        file_id: f.file_id.clone(),
        file_name: f.file_name.clone(),
        mime_type: f.mime_type.clone(),
        size: f.size,
        sha256: f.sha256.clone(),
        owner: ActorRefDto {
            actor_kind: match f.owner.actor_kind {
                ActorKind::Bot => "Bot".to_string(),
                ActorKind::Human => "Human".to_string(),
            },
            actor_id: f.owner.actor_id.clone(),
        },
        storage_backend: f.storage_backend.clone(),
        status: status_slug(&f.status).to_string(),
        created_at: f.created_at,
        updated_at: f.updated_at,
    }
}

fn to_dto(f: &SessionFile) -> SessionFileDto {
    SessionFileDto {
        file_id: f.file_id.clone(),
        session_id: f.session_id.clone(),
        file_name: f.file_name.clone(),
        mime_type: f.mime_type.clone(),
        size: f.size,
        sha256: f.sha256.clone(),
        owner: ActorRefDto {
            actor_kind: match f.owner.actor_kind {
                ActorKind::Bot => "Bot".to_string(),
                ActorKind::Human => "Human".to_string(),
            },
            actor_id: f.owner.actor_id.clone(),
        },
        storage_backend: f.storage_backend.clone(),
        status: status_slug(&f.status).to_string(),
        created_at: f.created_at,
        updated_at: f.updated_at,
    }
}

fn application_file_to_dto(file: &SessionFileView) -> SessionFileDto {
    SessionFileDto {
        file_id: file.file_id.clone(),
        session_id: file.session_id.clone(),
        file_name: file.file_name.clone(),
        mime_type: file.mime_type.clone(),
        size: file.size,
        sha256: file.sha256.clone(),
        owner: ActorRefDto {
            actor_kind: match file.owner.actor_kind {
                SessionFileActorKind::Bot => "Bot".to_string(),
                SessionFileActorKind::Human => "Human".to_string(),
            },
            actor_id: file.owner.actor_id.clone(),
        },
        storage_backend: file.storage_backend.clone(),
        status: match file.status {
            SessionFileStatus::Pending => "Pending",
            SessionFileStatus::Ready => "Ready",
            SessionFileStatus::Deleting => "Deleting",
            SessionFileStatus::Failed => "Failed",
        }
        .to_string(),
        created_at: file.created_at,
        updated_at: file.updated_at,
    }
}

#[derive(Debug, Deserialize)]
pub struct PrepareRequest {
    pub file_name: String,
    pub size: u64,
    pub mime_type: String,
}

#[derive(Debug, Deserialize, Default)]
pub struct ListQuery {
    #[serde(default)]
    pub prefix: Option<String>,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub limit: Option<u32>,
    #[serde(default)]
    pub offset: Option<u32>,
}

#[derive(Debug, Deserialize, Default)]
pub struct ShareRequest {
    #[serde(default)]
    pub ttl_seconds: Option<u64>,
}

#[derive(Debug, Deserialize, Default)]
pub struct DownloadQuery {
    #[serde(default)]
    pub ttl: Option<u64>,
    #[serde(default)]
    pub token: Option<String>,
    #[serde(default)]
    pub show: bool,
}

// ---------------------------------------------------------------
// Error mapping
// ---------------------------------------------------------------
//
// Spec error-code table:
//   NotFound            -> 404 FILE_NOT_FOUND
//   Forbidden           -> 403 FORBIDDEN
//   PayloadTooLarge     -> 413 PAYLOAD_TOO_LARGE
//   InvalidInput        -> 400 INVALID_INPUT
//   Conflict            -> 409 INVALID_TRANSITION
//   InvalidState        -> 422 INVALID_STATE
//   Backend             -> 502 STORAGE_BACKEND
//   Internal            -> 500 INTERNAL

fn err_to_response(err: SessionFileUseCaseError) -> Response {
    use SessionFileUseCaseError::*;
    let (code, status) = match &err {
        NotFound(_) => ("FILE_NOT_FOUND", StatusCode::NOT_FOUND),
        Forbidden(_) => ("FORBIDDEN", StatusCode::FORBIDDEN),
        PayloadTooLarge(_) => ("PAYLOAD_TOO_LARGE", StatusCode::PAYLOAD_TOO_LARGE),
        InvalidInput(_) => ("INVALID_INPUT", StatusCode::BAD_REQUEST),
        Conflict(_) => ("INVALID_TRANSITION", StatusCode::CONFLICT),
        InvalidState(_) => ("INVALID_STATE", StatusCode::UNPROCESSABLE_ENTITY),
        Backend => ("STORAGE_BACKEND", StatusCode::BAD_GATEWAY),
        Internal(_) => ("INTERNAL", StatusCode::INTERNAL_SERVER_ERROR),
    };
    (
        status,
        Json(json!({ "error": code, "message": err.to_string() })),
    )
        .into_response()
}

fn application_err_to_response(err: ApplicationError) -> Response {
    use ApplicationError::*;
    let (code, status) = match &err {
        InvalidInput { .. } => ("INVALID_INPUT", StatusCode::BAD_REQUEST),
        Unauthenticated => ("UNAUTHORIZED", StatusCode::UNAUTHORIZED),
        Forbidden(_) | ForbiddenCode { .. } => ("FORBIDDEN", StatusCode::FORBIDDEN),
        NotFound { .. } => ("FILE_NOT_FOUND", StatusCode::NOT_FOUND),
        Conflict { .. } => ("INVALID_TRANSITION", StatusCode::CONFLICT),
        Gone { .. } => ("GONE", StatusCode::GONE),
        QuotaExceeded { .. } => ("QUOTA_EXCEEDED", StatusCode::TOO_MANY_REQUESTS),
        PayloadTooLarge { .. } => ("PAYLOAD_TOO_LARGE", StatusCode::PAYLOAD_TOO_LARGE),
        Unprocessable { .. } => ("INVALID_STATE", StatusCode::UNPROCESSABLE_ENTITY),
        BadGateway { .. } => ("STORAGE_BACKEND", StatusCode::BAD_GATEWAY),
        Unavailable { .. } => ("SERVICE_UNAVAILABLE", StatusCode::SERVICE_UNAVAILABLE),
        Internal(_) => ("INTERNAL", StatusCode::INTERNAL_SERVER_ERROR),
    };
    (
        status,
        Json(json!({ "error": code, "message": err.to_string() })),
    )
        .into_response()
}

fn unauthorized() -> Response {
    (
        StatusCode::UNAUTHORIZED,
        Json(json!({ "error": "UNAUTHORIZED" })),
    )
        .into_response()
}

fn forbidden_not_participant() -> Response {
    (
        StatusCode::FORBIDDEN,
        Json(json!({ "error": "FORBIDDEN", "message": "not a session participant" })),
    )
        .into_response()
}

/// Uniform 404 for all `share_consume` failures — closes the token-validity /
/// file-existence oracle. The underlying `SessionFileUseCaseError` variants
/// (InvalidInput / InvalidState / NotFound / …) stay distinct at the service
/// layer for tests; the HTTP surface never distinguishes them.
/// A failing authority read is an internal error (HTTP 500),
/// never a silent deny-as-forbidden.
fn authority_internal_error(error: impl std::fmt::Display) -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(json!({
            "error": "INTERNAL_SERVER_ERROR",
            "message": format!("bot authority lookup failed: {error}")
        })),
    )
        .into_response()
}

fn share_consume_err_to_response() -> Response {
    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": "NOT_FOUND", "message": "shared file not found" })),
    )
        .into_response()
}

// ---------------------------------------------------------------
// Caller helpers
// ---------------------------------------------------------------

fn caller_to_actor_ref(caller: &GroupChatCaller) -> ActorRef {
    match caller {
        GroupChatCaller::Bot { bot_uuid } => ActorRef {
            actor_kind: ActorKind::Bot,
            actor_id: bot_uuid.clone(),
        },
        GroupChatCaller::Human(h) => ActorRef {
            actor_kind: ActorKind::Human,
            actor_id: h.actor_id.clone(),
        },
    }
}

/// Collect the caller actor_id plus any Bot the Human
/// CONTROLS among `candidates` (for Humans), or just the
/// bot_uuid (for Bots). Used to feed mutate-authz into the
/// service. The candidates are the relevant Bot actors of
/// the resource; the live authority hook decides per Bot
/// (bot-authority plan Task 6.5): the legacy
/// `list_bots_by_creator` expansion is gone. A failing
/// authority read is an internal error.
async fn caller_identities(
    state: &HttpAppState,
    caller: &GroupChatCaller,
    candidates: &[String],
) -> Result<Vec<String>, crate::error::HttpAdapterError> {
    match caller {
        GroupChatCaller::Bot { bot_uuid } => Ok(vec![bot_uuid.clone()]),
        GroupChatCaller::Human(h) => {
            let mut ids = vec![h.actor_id.clone()];
            for bot_id in candidates {
                if ids.contains(bot_id) {
                    continue;
                }
                if crate::routes::sessions::human_controls_bot(
                    state,
                    &h.staff_no,
                    bot_id,
                )
                .await?
                {
                    ids.push(bot_id.clone());
                }
            }
            Ok(ids)
        }
    }
}

/// Verify the caller is a member of the session and return the loaded session
/// for reuse. Returns `None` when the session or its parent group is missing,
/// or the caller is not a member (handler should 403).
///
/// Membership is judged against the session's *own* participants, not the
/// group's seed: session participants are seeded from the group at creation
/// and then evolve independently (see `Session::participants`). A participant
/// may be added to a session without joining the parent group
/// (`add_session_participant`), and the group's list may change without
/// affecting an in-flight session. Checking `group.participants` here would
/// wrongly deny a session-only participant or drift once the session diverges.
///
/// Returning the session lets mutate handlers (e.g. share) reuse the already
/// loaded `participants` for service-layer authz instead of fetching again.
async fn ensure_session_member(
    state: &HttpAppState,
    sid: &str,
    caller: &GroupChatCaller,
) -> Result<Option<bcs_service_api::Session>, crate::error::HttpAdapterError> {
    let sess = match state.services.session_management.get(sid).await {
        Ok(Some(s)) => s,
        _ => return Ok(None),
    };
    // The session is always scoped to a parent group; require it to still
    // exist. Membership, however, is judged against the session's own
    // participants — not the group's seed.
    if state.services.group.get(&sess.group_id).await.is_none() {
        return Ok(None);
    }
    let is_member = match caller {
        GroupChatCaller::Bot { bot_uuid } => sess
            .participants
            .iter()
            .any(|p| &p.bot_uuid == bot_uuid),
        GroupChatCaller::Human(h) => {
            crate::routes::sessions::human_has_session_access(
                state,
                &sess,
                &h.actor_id,
                &h.staff_no,
            )
            .await?
        }
    };
    if is_member {
        Ok(Some(sess))
    } else {
        Ok(None)
    }
}

/// Resolve mutate-authz inputs (session_creator + driver_bot) for delete/share
/// commands. Returns `(session_creator, driver_bot)`.
async fn resolve_mutate_authz(
    state: &HttpAppState,
    sid: &str,
) -> (Option<String>, Option<String>) {
    let sess = state
        .services
        .session_management
        .get(sid)
        .await
        .ok()
        .flatten();
    let group = match sess.as_ref() {
        Some(s) => state.services.group.get(&s.group_id).await,
        None => None,
    };
    let session_creator = sess.as_ref().and_then(|s| s.created_by.clone());
    let driver_bot = group.as_ref().map(|g| g.driver_bot.clone());
    (session_creator, driver_bot)
}

// ---------------------------------------------------------------
// Step 3: prepare_upload
// ---------------------------------------------------------------

pub async fn prepare_upload(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<PrepareRequest>,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    let is_member = match ensure_session_member(&state, &sid, &caller).await {
        Ok(v) => v.is_some(),
        Err(error) => return authority_internal_error(error),
    };
    if !is_member {
        return forbidden_not_participant();
    }
    let cmd = PrepareUploadCommand {
        session_id: sid.clone(),
        file_name: body.file_name,
        size: body.size,
        mime_type: body.mime_type,
        caller: caller_to_actor_ref(&caller),
    };
    match state.services.session_files.prepare_upload(cmd).await {
        Ok(r) => {
            let mut v = r.client_target_json.clone();
            v["file_id"] = json!(r.file.file_id);
            (StatusCode::CREATED, Json(v)).into_response()
        }
        Err(e) => err_to_response(e),
    }
}

// ---------------------------------------------------------------
// Step 5: upload_bytes + complete_upload
// ---------------------------------------------------------------

pub async fn upload_bytes(
    State(state): State<HttpAppState>,
    Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
    body: Body,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    let is_member = match ensure_session_member(&state, &sid, &caller).await {
        Ok(v) => v.is_some(),
        Err(error) => return authority_internal_error(error),
    };
    if !is_member {
        return forbidden_not_participant();
    }
    let part = uri
        .query()
        .and_then(|q| q.split('&').find(|p| p.starts_with("part=")))
        .and_then(|p| p["part=".len()..].parse().ok());
    // Content-Length is absent for chunked (streamed) request bodies — e.g. the
    // CLI's `Body::wrap_stream` uploads. Pass 0 = "unknown" so the service
    // skips its size-equality guard and relies on the backend's per-chunk cap
    // plus `complete_upload`'s cumulative-size check.
    let content_length = headers
        .get(header::CONTENT_LENGTH)
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(0);
    let stream: ByteStream = Box::new(RequestBodyStream(body.into_data_stream()));
    match state
        .services
        .session_files
        .stream_upload(&sid, &file_id, part, stream, content_length)
        .await
    {
        Ok(()) => (
            StatusCode::ACCEPTED,
            Json(json!({ "file_id": file_id, "status": "Pending" })),
        )
            .into_response(),
        Err(e) => err_to_response(e),
    }
}

pub async fn complete_upload(
    State(state): State<HttpAppState>,
    Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    let is_member = match ensure_session_member(&state, &sid, &caller).await {
        Ok(v) => v.is_some(),
        Err(error) => return authority_internal_error(error),
    };
    if !is_member {
        return forbidden_not_participant();
    }
    let Some(application) = state.session_file_application.as_ref() else {
        return application_err_to_response(ApplicationError::internal(
            "session file application service is not configured",
        ));
    };
    let command = CompleteSessionFile {
        caller: application_caller(&caller),
        session_id: sid.clone(),
        file_id: file_id.clone(),
    };
    match application.complete(command).await {
        Ok(file) => (
            StatusCode::OK,
            Json(json!(application_file_to_dto(&file))),
        )
            .into_response(),
        Err(error) => application_err_to_response(error),
    }
}

// ---------------------------------------------------------------
// Step 6: delete_file + list_files + get_file + capabilities
// ---------------------------------------------------------------

pub async fn delete_file(
    State(state): State<HttpAppState>,
    Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    let is_member = match ensure_session_member(&state, &sid, &caller).await {
        Ok(v) => v.is_some(),
        Err(error) => return authority_internal_error(error),
    };
    if !is_member {
        return forbidden_not_participant();
    }
    let (session_creator, driver_bot) = resolve_mutate_authz(&state, &sid).await;
    // Identity expansion follows the LIVE authority
    // edge (bot-authority plan Task 6.5); a failing
    // read is an internal error.
    let mut candidate_actors: Vec<String> = Vec::new();
    if let Some(c) = &session_creator {
        candidate_actors.push(c.clone());
    }
    if let Some(d) = &driver_bot {
        if !d.is_empty() {
            candidate_actors.push(d.clone());
        }
    }
    // V1-facade parity: the FILE's owner is an
    // authorized-delete candidate — a Human DELETEing a
    // file a participant Bot uploaded must still be
    // admitted through its CURRENT control edge, not the
    // file's historical ownership facts. A missing file
    // keeps the service's own NotFound path.
    match state.services.session_files.get(&sid, &file_id).await {
        Ok(file) => {
            let owner = file.owner.actor_id;
            if !candidate_actors.contains(&owner) {
                candidate_actors.push(owner);
            }
        }
        Err(SessionFileUseCaseError::NotFound(_)) => {}
        Err(error) => return authority_internal_error(error),
    }
    let caller_identities =
        match caller_identities(&state, &caller, &candidate_actors).await {
            Ok(ids) => ids,
            Err(error) => return authority_internal_error(error),
        };
    let cmd = DeleteFileCommand {
        session_id: sid,
        file_id,
        caller: caller_to_actor_ref(&caller),
        caller_identities,
        session_creator,
        driver_bot,
    };
    match state.services.session_files.delete_file(cmd).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(e) => err_to_response(e),
    }
}

pub async fn list_files(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Query(q): Query<ListQuery>,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    let is_member = match ensure_session_member(&state, &sid, &caller).await {
        Ok(v) => v.is_some(),
        Err(error) => return authority_internal_error(error),
    };
    if !is_member {
        return forbidden_not_participant();
    }
    let status: Option<FileStatus> = match q.status.as_deref() {
        Some("Pending") => Some(FileStatus::Pending),
        Some("Ready") => Some(FileStatus::Ready),
        Some("Deleting") => Some(FileStatus::Deleting),
        Some("Failed") => Some(FileStatus::Failed),
        Some(invalid) => {
            return (StatusCode::BAD_REQUEST, Json(json!({
                "error": "INVALID_INPUT",
                "message": format!("invalid status value: {invalid}"),
            }))).into_response();
        }
        None => None,
    };
    let params = SessionFileListParams {
        prefix: q.prefix,
        status,
        limit: q.limit.unwrap_or(100),
        offset: q.offset.unwrap_or(0),
    };
    match state.services.session_files.list(&sid, params).await {
        Ok(page) => Json(json!({
            "items": page.items.iter().map(to_dto).collect::<Vec<_>>(),
            "total": page.total,
        }))
        .into_response(),
        Err(e) => err_to_response(e),
    }
}

pub async fn get_file(
    State(state): State<HttpAppState>,
    Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    let is_member = match ensure_session_member(&state, &sid, &caller).await {
        Ok(v) => v.is_some(),
        Err(error) => return authority_internal_error(error),
    };
    if !is_member {
        return forbidden_not_participant();
    }
    match state.services.session_files.get(&sid, &file_id).await {
        Ok(f) => (StatusCode::OK, Json(json!(to_dto(&f)))).into_response(),
        Err(e) => err_to_response(e),
    }
}

pub async fn capabilities(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    headers: HeaderMap,
    uri: Uri,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    let is_member = match ensure_session_member(&state, &sid, &caller).await {
        Ok(v) => v.is_some(),
        Err(error) => return authority_internal_error(error),
    };
    if !is_member {
        return forbidden_not_participant();
    }
    let c: CapabilitiesView = state.services.session_files.capabilities().await;
    (StatusCode::OK, Json(json!(c))).into_response()
}

// ---------------------------------------------------------------
// Step 7: download_content (302 / streaming)
// ---------------------------------------------------------------

pub async fn download_content(
    State(state): State<HttpAppState>,
    Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
    Query(q): Query<DownloadQuery>,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    let is_member = match ensure_session_member(&state, &sid, &caller).await {
        Ok(v) => v.is_some(),
        Err(error) => return authority_internal_error(error),
    };
    if !is_member {
        return forbidden_not_participant();
    }
    // TTL hidden from frontend: always None → download_route uses share_link_ttl.
    download_file_by_id(&state, &sid, &file_id, None, q.show).await
}

/// Shared streaming/redirect logic for both authenticated download_content
/// and shared_file_content (post share-consume). Resolves the route and either
/// 302-redirects to the presigned URL or streams the bytes locally with
/// Content-Disposition / Content-Type / Content-Length.
async fn download_file_by_id(
    state: &HttpAppState,
    sid: &str,
    file_id: &str,
    ttl: Option<u64>,
    show: bool,
) -> Response {
    match state
        .services
        .session_files
        .download_route(sid, file_id, ttl, show)
        .await
    {
        Ok((file, route)) => match route.presign {
            Some(ticket) => Redirect::to(&ticket.download_url).into_response(),
            None => match state.services.session_files.get_stream(sid, file_id).await {
                Ok((_f, stream)) => {
                    let mut h = HeaderMap::new();
                    if let Ok(v) = file.mime_type.parse() {
                        h.insert(header::CONTENT_TYPE, v);
                    }
                    if let Ok(v) = file.size.to_string().parse() {
                        h.insert(header::CONTENT_LENGTH, v);
                    }
                    let disposition = if show { "inline" } else { "attachment" };
                    if let Ok(v) = format!(
                        "{}; filename=\"{}\"",
                        disposition,
                        file.file_name.replace('"', "\\\"")
                    )
                    .parse()
                    {
                        h.insert(header::CONTENT_DISPOSITION, v);
                    }
                    (h, Body::from_stream(stream)).into_response()
                }
                Err(e) => err_to_response(e),
            },
        },
        Err(e) => err_to_response(e),
    }
}

// ---------------------------------------------------------------
// Step 8: share_mint + shared_file_meta + shared_file_content (no auth)
// ---------------------------------------------------------------

pub async fn share_mint(
    State(state): State<HttpAppState>,
    Path((sid, file_id)): Path<(String, String)>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<ShareRequest>,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    let sess = match ensure_session_member(&state, &sid, &caller).await {
        Ok(Some(sess)) => sess,
        Ok(None) => return forbidden_not_participant(),
        Err(error) => return authority_internal_error(error),
    };
    // Identity expansion follows the LIVE authority
    // edge (bot-authority plan Task 6.5); a failing
    // read is an internal error.
    let share_candidates = sess
        .participants
        .iter()
        .map(|p| p.bot_uuid.clone())
        .collect::<Vec<_>>();
    let caller_identities =
        match caller_identities(&state, &caller, &share_candidates).await {
            Ok(ids) => ids,
            Err(error) => return authority_internal_error(error),
        };
    let cmd = ShareMintCommand {
        session_id: sid,
        file_id,
        caller: caller_to_actor_ref(&caller),
        ttl_seconds: body.ttl_seconds,
        caller_identities,
        session_participants: sess
            .participants
            .iter()
            .map(|p| p.bot_uuid.clone())
            .collect(),
    };
    match state.services.session_files.share_mint(cmd).await {
        Ok(r) => (
            StatusCode::CREATED,
            Json(json!({
                "share_url": r.share_url,
                "share_token": r.share_token,
                "expires_at": r.expires_at,
            })),
        )
            .into_response(),
        Err(e) => err_to_response(e),
    }
}

pub async fn shared_file_meta(
    State(state): State<HttpAppState>,
    Query(q): Query<DownloadQuery>,
) -> Response {
    let Some(token) = q.token else {
        return unauthorized();
    };
    match state.services.session_files.share_consume(&token).await {
        Ok(r) => (StatusCode::OK, Json(json!(to_shared_dto(&r.file)))).into_response(),
        Err(_) => share_consume_err_to_response(),
    }
}

pub async fn shared_file_content(
    State(state): State<HttpAppState>,
    Query(q): Query<DownloadQuery>,
) -> Response {
    let Some(token) = q.token else {
        return unauthorized();
    };
    match state.services.session_files.share_consume(&token).await {
        Ok(r) => {
            let sid_owned = r.file.session_id.clone();
            let fid = r.file.file_id.clone();
            // TTL hidden from frontend: always None so download_route
            // uses share_link_ttl. q.ttl is accepted-but-ignored.
            download_file_by_id(&state, &sid_owned, &fid, None, q.show).await
        }
        Err(_) => share_consume_err_to_response(),
    }
}

// ---------------------------------------------------------------
// Tests
// ---------------------------------------------------------------

#[cfg(test)]
mod tests;
