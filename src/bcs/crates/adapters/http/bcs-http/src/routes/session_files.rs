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
    body::Body,
    extract::{Path, Query, State},
    http::{header, HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Redirect, Response},
};
use bcs_domain::{ActorKind, ActorRef, SessionFile};
use bcs_service_api::application::session_files::{
    CapabilitiesView, DeleteFileCommand, PrepareUploadCommand, SessionFileUseCaseError,
    ShareMintCommand,
};
use bcs_service_api::port::repo::SessionFileListParams;
use serde::Deserialize;
use serde_json::json;

use crate::routes::group_messages::{resolve_group_chat_caller, GroupChatCaller};
use crate::state::HttpAppState;

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

fn status_slug(status: &bcs_domain::FileStatus) -> &'static str {
    use bcs_domain::FileStatus::*;
    match status {
        Pending => "Pending",
        Ready => "Ready",
        Deleting => "Deleting",
        Failed => "Failed",
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
    pub limit: Option<u32>,
    #[serde(default)]
    pub marker: Option<String>,
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

/// Collect the caller actor_id plus any bots owned by that human (for Humans),
/// or just the bot_uuid (for Bots). Used to feed mutate-authz into the service.
async fn caller_identities(state: &HttpAppState, caller: &GroupChatCaller) -> Vec<String> {
    match caller {
        GroupChatCaller::Bot { bot_uuid } => vec![bot_uuid.clone()],
        GroupChatCaller::Human(h) => {
            let mut ids = vec![h.actor_id.clone()];
            for b in state
                .services
                .registry
                .list_bots_by_creator(&h.staff_no)
                .await
            {
                ids.push(b.bot_uuid);
            }
            ids
        }
    }
}

/// Verify the caller is a member of the session's group: the session must
/// exist, the group must exist, and the caller (bot or human) must be a
/// participant or own a participating bot.
async fn ensure_session_member(
    state: &HttpAppState,
    sid: &str,
    caller: &GroupChatCaller,
) -> bool {
    let sess = match state.services.session_management.get(sid).await {
        Ok(Some(s)) => s,
        _ => return false,
    };
    let group = match state.services.group.get(&sess.group_id).await {
        Some(g) => g,
        None => return false,
    };
    match caller {
        GroupChatCaller::Bot { bot_uuid } => group
            .participants
            .iter()
            .any(|p| &p.bot_uuid == bot_uuid),
        GroupChatCaller::Human(h) => {
            crate::routes::sessions::human_has_group_access(
                state,
                &group,
                &h.actor_id,
                &h.staff_no,
            )
            .await
        }
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
    if !ensure_session_member(&state, &sid, &caller).await {
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
    body: axum::body::Bytes,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(c) => c,
        Err(_) => return unauthorized(),
    };
    if !ensure_session_member(&state, &sid, &caller).await {
        return forbidden_not_participant();
    }
    let part = uri
        .query()
        .and_then(|q| q.split('&').find(|p| p.starts_with("part=")))
        .and_then(|p| p["part=".len()..].parse().ok());
    let content_length = body.len() as u64;
    let stream = bcs_storage_api::byte_stream_from_bytes(body.into());
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
    if !ensure_session_member(&state, &sid, &caller).await {
        return forbidden_not_participant();
    }
    match state
        .services
        .session_files
        .complete_upload(&sid, &file_id)
        .await
    {
        Ok(f) => (StatusCode::OK, Json(json!(to_dto(&f)))).into_response(),
        Err(e) => err_to_response(e),
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
    if !ensure_session_member(&state, &sid, &caller).await {
        return forbidden_not_participant();
    }
    let (session_creator, driver_bot) = resolve_mutate_authz(&state, &sid).await;
    let cmd = DeleteFileCommand {
        session_id: sid,
        file_id,
        caller: caller_to_actor_ref(&caller),
        caller_identities: caller_identities(&state, &caller).await,
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
    if !ensure_session_member(&state, &sid, &caller).await {
        return forbidden_not_participant();
    }
    let params = SessionFileListParams {
        prefix: q.prefix,
        limit: q.limit.unwrap_or(100),
        marker: q.marker,
    };
    match state.services.session_files.list(&sid, params).await {
        Ok(page) => Json(json!({
            "items": page.items.iter().map(to_dto).collect::<Vec<_>>(),
            "truncated": page.truncated,
            "next_marker": page.next_marker,
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
    if !ensure_session_member(&state, &sid, &caller).await {
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
    if !ensure_session_member(&state, &sid, &caller).await {
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
    if !ensure_session_member(&state, &sid, &caller).await {
        return forbidden_not_participant();
    }
    download_file_by_id(&state, &sid, &file_id, q.ttl).await
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
) -> Response {
    match state
        .services
        .session_files
        .download_route(sid, file_id, ttl)
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
                    if let Ok(v) = format!(
                        "attachment; filename=\"{}\"",
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
    if !ensure_session_member(&state, &sid, &caller).await {
        return forbidden_not_participant();
    }
    let (session_creator, driver_bot) = resolve_mutate_authz(&state, &sid).await;
    let cmd = ShareMintCommand {
        session_id: sid,
        file_id,
        caller: caller_to_actor_ref(&caller),
        ttl_seconds: body.ttl_seconds,
        caller_identities: caller_identities(&state, &caller).await,
        session_creator,
        driver_bot,
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
    Path(sid): Path<String>,
    Query(q): Query<DownloadQuery>,
) -> Response {
    let Some(token) = q.token else {
        return unauthorized();
    };
    match state.services.session_files.share_consume(&sid, &token).await {
        Ok(r) => (StatusCode::OK, Json(json!(to_dto(&r.file)))).into_response(),
        Err(_) => share_consume_err_to_response(),
    }
}

pub async fn shared_file_content(
    State(state): State<HttpAppState>,
    Path(sid): Path<String>,
    Query(q): Query<DownloadQuery>,
) -> Response {
    let Some(token) = q.token else {
        return unauthorized();
    };
    match state.services.session_files.share_consume(&sid, &token).await {
        Ok(r) => {
            let sid_owned = r.file.session_id.clone();
            let fid = r.file.file_id.clone();
            download_file_by_id(&state, &sid_owned, &fid, q.ttl).await
        }
        Err(_) => share_consume_err_to_response(),
    }
}

// ---------------------------------------------------------------
// Tests
// ---------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::router::build_api_router;
    use crate::state::HttpAppState;
    use axum::body::to_bytes;
    use axum::http::{header, HeaderValue, Method, Request, StatusCode};
    use bcs_bot::BotCore;
    use bcs_bot_store::MemoryBotRepo;
    use bcs_domain::{ActorKind as DomainActorKind, FileStatus, ShareTokenPayload, share_token_encode};
    use bcs_group::GroupCore;
    use bcs_group_store::MemoryGroupRepo;
    use bcs_service_api::application::session_files::SessionFileService;
    use bcs_service_api::port::repo::{
        GroupRepoPort, SessionFileRepoPort, SessionRepoPort,
    };
    use bcs_service_api::{
        BotCapabilities, BotRegistryCoreService, Group, GroupKind, GroupStatus, Participant,
        ParticipantRole, SessionKind, Workspace,
    };
    use bcs_services_container::Services;
    use bcs_session::SessionManagementServiceImpl;
    use bcs_session_file_store::MemorySessionFileRepo;
    use bcs_session_file::{SessionFileServiceConfig, SessionFileServiceImpl};
    use bcs_session_store::MemorySessionRepo;
    use bcs_storage_api::fake::FakeStoragePlugin;
    use bcs_storage_api::StorageCapabilities;
    use std::path::PathBuf;
    use std::sync::Arc;
    use tower::ServiceExt;

    // ------------------- Helpers -------------------

    fn local_caps() -> StorageCapabilities {
        StorageCapabilities {
            supports_presign_put: false,
            supports_presign_download: false,
            supports_stream_put: true,
            supports_stream_get: true,
            max_object_size: 1024 * 1024 * 1024,
        }
    }

    fn empty_caps() -> BotCapabilities {
        BotCapabilities {
            name: Some("test-bot".into()),
            summary: None,
            domains: Vec::new(),
            skills: Vec::new(),
            scopes: Vec::new(),
            binding_channels: None,
            hidden: false,
            visibility: String::new(),
            agent_code: None,
            agent_token: None,
        }
    }

    fn group_participant(bot_uuid: &str) -> Participant {
        Participant {
            bot_uuid: bot_uuid.to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::default(),
            actor_kind: DomainActorKind::Bot,
            mode: None,
        }
    }

    struct TestApp {
        state: HttpAppState,
        bot_a_token: String,
        bot_b_token: String,
        #[allow(dead_code)]
        bot_a: String,
        #[allow(dead_code)]
        bot_b: String,
        sid: String,
    }

    async fn build_test_app() -> TestApp {
        // Registry: two bots registered with stable tokens.
        let bot_dir = tempfile::tempdir().expect("temp dir for bot registry");
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(PathBuf::from(
            bot_dir.path(),
        )));
        let registry = BotCore::with_repo(bot_repo.clone());
        registry
            .register_with_owner_and_token(
                "bot-a".into(),
                empty_caps(),
                "alice",
                "token-a",
            )
            .await
            .expect("register bot-a");
        registry
            .register_with_owner_and_token(
                "bot-b".into(),
                empty_caps(),
                "bob",
                "token-b",
            )
            .await
            .expect("register bot-b");

        // Group: g1 with bot_a and bot_b as participants, bot_a as driver.
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let group_core = GroupCore::with_repo(group_repo.clone());
        let group = Group {
            id: "g1".into(),
            label: None,
            status: GroupStatus::default(),
            driver_bot: "bot-a".into(),
            originator: Some("bot-a".into()),
            routing_policy: None,
            context: None,
            participants: vec![group_participant("bot-a"), group_participant("bot-b")],
            messages: Vec::new(),
            workspace: Workspace::default(),
            service_group_uuid: None,
            service_mode: None,
            created_at: 0,
            updated_at: 0,
            group_kind: GroupKind::default(),
            dm_pair_key: None,
            group_strategy: bcs_service_api::GroupStrategy::default(),
            service_spec: None,
            version: 1,
            record_status: "active".to_string(),
            visibility: "protected".to_string(),
        };
        group_repo.upsert(group).await.expect("upsert group");

        // Session id MUST follow `{group_id}:{8_hex}` per
        // `bcs_service_api::core::session::validate_session_id`.
        let sid = "g1:abcd1234".to_string();

        // Session: g1:abcd1234 -> g1, participants [bot-a, bot-b], created_by
        // "human_alice". Seed via the repo directly to bypass application-layer
        // validation that would otherwise require a fully wired group store.
        let session_repo = Arc::new(MemorySessionRepo::new());
        session_repo
            .create(
                "g1",
                bcs_service_api::port::repo::NewSessionParams {
                    session_kind: SessionKind::Chat,
                    participants: vec![group_participant("bot-a"), group_participant("bot-b")],
                    group_version: Some(1),
                    caller_id: Some("bot-a".into()),
                    caller_principal: None,
                    input: None,
                    created_by: Some("human_alice".into()),
                    session_title: None,
                    id: Some(sid.clone()),
                    meta: None,
                },
            )
            .await
            .expect("create session");

        let session_management =
            SessionManagementServiceImpl::new(session_repo.clone(), group_repo.clone());

        // SessionFileService with FakeStoragePlugin (local caps) + in-memory repo.
        let storage: Arc<dyn bcs_storage_api::StoragePlugin> =
            Arc::new(FakeStoragePlugin::new(local_caps()));
        let file_repo: Arc<dyn SessionFileRepoPort> = Arc::new(MemorySessionFileRepo::new());
        let session_repo_dyn: Arc<dyn SessionRepoPort> = session_repo.clone();
        let file_cfg = SessionFileServiceConfig {
            storage,
            repo: file_repo,
            session_repo: session_repo_dyn,
            env: "local".into(),
            max_size: 1024 * 1024,
            // effectively never multipart for tests
            multipart_threshold: 1024 * 1024 * 1024,
            bcs_base_url: "http://test.local".into(),
            share_secret: b"test-secret-32-bytes-0123456789".to_vec(),
            share_default_ttl: 3600,
            share_base_url: Some("http://test.local".into()),
        };
        let session_files: Arc<dyn SessionFileService> =
            Arc::new(SessionFileServiceImpl::new(file_cfg));

        let services = Services::builder()
            .registry(Arc::new(registry))
            .group(Arc::new(group_core))
            .session_management(Arc::new(session_management))
            .session_files(session_files)
            .build_for_test();

        let state = HttpAppState::new(services);

        // Sanity: the session exists and the bots resolve.
        assert!(
            state
                .services
                .session_management
                .get(&sid)
                .await
                .unwrap()
                .is_some(),
            "seed session not found"
        );

        TestApp {
            state,
            bot_a_token: "token-a".into(),
            bot_b_token: "token-b".into(),
            bot_a: "bot-a".into(),
            bot_b: "bot-b".into(),
            sid,
        }
    }

    fn auth_request(
        method: Method,
        uri: &str,
        token: &str,
        body: Option<Vec<u8>>,
    ) -> Request<Body> {
        let builder = Request::builder()
            .method(method)
            .uri(uri)
            .header(header::AUTHORIZATION, format!("Bearer {token}"));
        if let Some(b) = body {
            builder
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(b))
                .unwrap()
        } else {
            builder.body(Body::empty()).unwrap()
        }
    }

    async fn send(
        app: &TestApp,
        req: Request<Body>,
    ) -> (StatusCode, serde_json::Value, Option<HeaderValue>) {
        let router = build_api_router(app.state.clone());
        let resp = router.oneshot(req).await.expect("router oneshot");
        let status = resp.status();
        let cd = resp
            .headers()
            .get(header::CONTENT_DISPOSITION)
            .cloned();
        let bytes = to_bytes(resp.into_body(), usize::MAX).await.expect("body bytes");
        let json: serde_json::Value = if bytes.is_empty() {
            serde_json::Value::Null
        } else {
            serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null)
        };
        (status, json, cd)
    }

    fn post_json(
        app: &TestApp,
        uri: &str,
        token: &str,
        body: serde_json::Value,
    ) -> Request<Body> {
        let _ = app;
        auth_request(
            Method::POST,
            uri,
            token,
            Some(serde_json::to_vec(&body).unwrap()),
        )
    }

    fn put_bytes(app: &TestApp, uri: &str, token: &str, body: Vec<u8>) -> Request<Body> {
        let _ = app;
        let builder = Request::builder()
            .method(Method::PUT)
            .uri(uri)
            .header(header::AUTHORIZATION, format!("Bearer {token}"))
            .header(header::CONTENT_TYPE, "application/octet-stream")
            .header(header::CONTENT_LENGTH, body.len().to_string());
        builder.body(Body::from(body)).unwrap()
    }

    // ------------------- Tests -------------------

    #[tokio::test]
    async fn three_stage_upload_complete_download_roundtrip() {
        let app = build_test_app().await;

        // 1. Prepare
        let prepare_uri = format!("/sessions/{}/files", app.sid);
        let req = post_json(
            &app,
            &prepare_uri,
            &app.bot_a_token,
            json!({
                "file_name": "hello.txt",
                "size": 5u64,
                "mime_type": "text/plain",
            }),
        );
        let (status, body, _) = send(&app, req).await;
        assert_eq!(status, StatusCode::CREATED, "prepare body: {body:?}");
        let file_id = body
            .get("file_id")
            .and_then(|v| v.as_str())
            .expect("file_id in prepare response")
            .to_string();
        assert!(!file_id.is_empty());

        // For the local proxy backend, FakeStoragePlugin returns ProxyViaBcs,
        // so the service synthesizes a single-part URL (mode=single).
        assert_eq!(
            body.get("mode").and_then(|v| v.as_str()),
            Some("single")
        );

        // 2. Upload (single part)
        let upload_uri = format!(
            "/sessions/{}/files/{}/content",
            app.sid, file_id
        );
        let req = put_bytes(&app, &upload_uri, &app.bot_a_token, b"hello".to_vec());
        let (status, body, _) = send(&app, req).await;
        assert_eq!(status, StatusCode::ACCEPTED, "upload body: {body:?}");

        // 3. Complete
        let complete_uri = format!(
            "/sessions/{}/files/{}/complete",
            app.sid, file_id
        );
        let req = post_json(&app, &complete_uri, &app.bot_a_token, json!({}));
        let (status, body, _) = send(&app, req).await;
        assert_eq!(status, StatusCode::OK, "complete body: {body:?}");
        assert_eq!(
            body.get("status").and_then(|v| v.as_str()),
            Some("Ready")
        );
        assert_eq!(
            body.get("file_id").and_then(|v| v.as_str()),
            Some(file_id.as_str())
        );
        assert_eq!(
            body.get("size").and_then(|v| v.as_u64()),
            Some(5)
        );

        // 4. Download — local backend streams via get_stream, no 302.
        let download_uri = format!(
            "/sessions/{}/files/{}/content",
            app.sid, file_id
        );
        let req = auth_request(
            Method::GET,
            &download_uri,
            &app.bot_a_token,
            None,
        );
        let (status, _body, cd) = send(&app, req).await;
        assert_eq!(status, StatusCode::OK);
        assert!(
            cd.is_some(),
            "expected Content-Disposition on streamed download"
        );
        let cd = cd.unwrap().to_str().unwrap().to_string();
        assert!(cd.contains("attachment"), "cd: {cd}");
        assert!(cd.contains("hello.txt"), "cd: {cd}");
    }

    #[tokio::test]
    async fn capabilities_route_not_shadowed_by_file_id() {
        // Build the router then call GET /sessions/s1/files/capabilities with a
        // known bot member. The static segment MUST resolve to the
        // `capabilities` handler returning a CapabilitiesView JSON body — not
        // be treated as `file_id = "capabilities"`.
        let app = build_test_app().await;
        let uri = format!("/sessions/{}/files/capabilities", app.sid);
        let req = auth_request(Method::GET, &uri, &app.bot_a_token, None);
        let (status, body, _) = send(&app, req).await;
        assert_eq!(status, StatusCode::OK, "capabilities body: {body:?}");
        assert!(
            body.get("storage").is_some(),
            "expected CapabilitiesView JSON: {body:?}"
        );
        assert_eq!(
            body.get("presign_upload").and_then(|v| v.as_bool()),
            Some(false)
        );
    }

    #[tokio::test]
    async fn delete_ready_is_204_and_idempotent() {
        let app = build_test_app().await;

        // Prepare + upload + complete a file owned by bot-a.
        let (file_id, _) = upload_complete(&app, "del.txt", b"bye").await;

        // First delete -> 204.
        let uri = format!("/sessions/{}/files/{}", app.sid, file_id);
        let req = auth_request(Method::DELETE, &uri, &app.bot_a_token, None);
        let (status, body, _) = send(&app, req).await;
        assert_eq!(status, StatusCode::NO_CONTENT, "delete body: {body:?}");

        // Repeat delete (idempotent) -> 204. Even though the row is gone, the
        // service's `delete_file` returns Ok(()) for absent rows.
        let req = auth_request(Method::DELETE, &uri, &app.bot_a_token, None);
        let (status, body, _) = send(&app, req).await;
        assert_eq!(status, StatusCode::NO_CONTENT, "repeat delete body: {body:?}");
    }

    #[tokio::test]
    async fn delete_someone_else_file_is_403() {
        // bot-a uploads, bot-b attempts to delete — bot-b is a participant but
        // does not own the file and is neither session_creator nor driver_bot.
        let app = build_test_app().await;
        let (file_id, _) = upload_complete(&app, "owned.txt", b"x").await;

        let uri = format!("/sessions/{}/files/{}", app.sid, file_id);
        let req = auth_request(Method::DELETE, &uri, &app.bot_b_token, None);
        let (status, body, _) = send(&app, req).await;
        assert_eq!(
            status,
            StatusCode::FORBIDDEN,
            "expected 403 forbidding bot-b delete, body: {body:?}"
        );
    }

    #[tokio::test]
    async fn share_mint_then_consume() {
        let app = build_test_app().await;
        let (file_id, _) = upload_complete(&app, "share.txt", b"s").await;

        // Mint share token.
        let mint_uri = format!("/sessions/{}/files/{}/share", app.sid, file_id);
        let req = post_json(&app, &mint_uri, &app.bot_a_token, json!({}));
        let (status, body, _) = send(&app, req).await;
        assert_eq!(status, StatusCode::CREATED, "share mint body: {body:?}");
        let token = body
            .get("share_token")
            .and_then(|v| v.as_str())
            .expect("share_token in mint response")
            .to_string();
        assert!(!token.is_empty());

        // Consume via shared_file_meta — 200 with file DTO.
        let meta_uri = format!(
            "/sessions/{}/shared-file?token={}",
            app.sid, token
        );
        let req = auth_request(Method::GET, &meta_uri, &app.bot_a_token, None);
        let (status, body, _) = send(&app, req).await;
        assert_eq!(status, StatusCode::OK, "share consume body: {body:?}");
        assert_eq!(
            body.get("file_id").and_then(|v| v.as_str()),
            Some(file_id.as_str())
        );
        assert_eq!(
            body.get("status").and_then(|v| v.as_str()),
            Some("Ready")
        );
    }

    #[tokio::test]
    async fn share_consume_sid_mismatch_is_404() {
        // Mint on sid=s1; consume on a different sid → service returns NotFound.
        let app = build_test_app().await;
        let (file_id, _) = upload_complete(&app, "share2.txt", b"y").await;

        let mint_uri = format!("/sessions/{}/files/{}/share", app.sid, file_id);
        let req = post_json(&app, &mint_uri, &app.bot_a_token, json!({}));
        let (status, body, _) = send(&app, req).await;
        assert_eq!(status, StatusCode::CREATED, "share mint body: {body:?}");
        let token = body
            .get("share_token")
            .and_then(|v| v.as_str())
            .unwrap()
            .to_string();

        // Different session id.
        let wrong_uri = format!(
            "/sessions/{}/shared-file?token={}",
            "s-other", token
        );
        let req = auth_request(Method::GET, &wrong_uri, &app.bot_a_token, None);
        let (status, body, _) = send(&app, req).await;
        assert_eq!(
            status,
            StatusCode::NOT_FOUND,
            "expected 404 for sid-mismatch, body: {body:?}"
        );
        assert_eq!(
            body.get("error").and_then(|v| v.as_str()),
            Some("NOT_FOUND")
        );
    }

    #[tokio::test]
    async fn share_consume_expired_token_is_404() {
        // Mint a valid token, then construct a clone with `exp` in the past via
        // the domain share encode function. Consume must return uniform 404 —
        // no 422/410 leakage that reveals the token was expired.
        let app = build_test_app().await;
        let (file_id, _) = upload_complete(&app, "share-exp.txt", b"e").await;

        let mint_uri = format!("/sessions/{}/files/{}/share", app.sid, file_id);
        let req = post_json(&app, &mint_uri, &app.bot_a_token, json!({}));
        let (_status, body, _) = send(&app, req).await;
        let valid_token = body
            .get("share_token")
            .and_then(|v| v.as_str())
            .unwrap()
            .to_string();

        // Re-encode an expired token with the same file_id using the app's share secret.
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let expired_token = bcs_domain::share_token_encode(
            &bcs_domain::ShareTokenPayload {
                v: 1,
                file_id: file_id.clone(),
                exp: now.saturating_sub(10),
            },
            b"test-secret-32-bytes-0123456789",
        );
        assert_ne!(valid_token, expired_token);

        let meta_uri = format!(
            "/sessions/{}/shared-file?token={}",
            app.sid, expired_token
        );
        let req = auth_request(Method::GET, &meta_uri, &app.bot_a_token, None);
        let (status, body, _) = send(&app, req).await;
        assert_eq!(
            status,
            StatusCode::NOT_FOUND,
            "expected uniform 404 for expired share token, got {status}; body: {body:?}",
        );
        assert_eq!(
            body.get("error").and_then(|v| v.as_str()),
            Some("NOT_FOUND")
        );
    }

    #[tokio::test]
    async fn share_consume_tampered_token_is_404() {
        // Mint a valid token, tamper one character, consume must return uniform
        // 404 — no 400/401 leakage that reveals the token was tampered.
        let app = build_test_app().await;
        let (file_id, _) = upload_complete(&app, "share-tamper.txt", b"t").await;

        let mint_uri = format!("/sessions/{}/files/{}/share", app.sid, file_id);
        let req = post_json(&app, &mint_uri, &app.bot_a_token, json!({}));
        let (_status, body, _) = send(&app, req).await;
        let token = body
            .get("share_token")
            .and_then(|v| v.as_str())
            .unwrap()
            .to_string();

        let mut chars: Vec<char> = token.chars().collect();
        let idx = chars.len() - 1;
        let last = chars[idx];
        chars[idx] = if last == 'a' { 'b' } else { 'a' };
        let tampered: String = chars.into_iter().collect();

        let meta_uri = format!(
            "/sessions/{}/shared-file?token={}",
            app.sid, tampered
        );
        let req = auth_request(Method::GET, &meta_uri, &app.bot_a_token, None);
        let (status, body, _) = send(&app, req).await;
        assert_eq!(
            status,
            StatusCode::NOT_FOUND,
            "expected uniform 404 for tampered share token, got {status}; body: {body:?}",
        );
        assert_eq!(
            body.get("error").and_then(|v| v.as_str()),
            Some("NOT_FOUND")
        );
    }

    #[tokio::test]
    async fn local_download_streams_with_content_disposition() {
        // Build a non-multipart upload, complete, and verify the GET response
        // carries Content-Disposition: attachment; filename="...".
        let app = build_test_app().await;
        let (file_id, _) = upload_complete(&app, "disp.txt", b"abc").await;

        let uri = format!(
            "/sessions/{}/files/{}/content",
            app.sid, file_id
        );
        let req = auth_request(Method::GET, &uri, &app.bot_a_token, None);
        let (status, _body, cd) = send(&app, req).await;
        assert_eq!(status, StatusCode::OK);
        let cd = cd.expect("content-disposition header");
        let s = cd.to_str().unwrap();
        assert!(s.contains("attachment"), "cd: {s}");
        assert!(s.contains("disp.txt"), "cd: {s}");
    }

    // ------------------- Internal test helpers -------------------

    /// Run the full prepare→upload→complete sequence and return the file_id
    /// plus the file's status slug. Uploads as bot-a (the session's driver).
    async fn upload_complete(app: &TestApp, name: &str, bytes: &[u8]) -> (String, FileStatus) {
        // Prepare
        let prepare_uri = format!("/sessions/{}/files", app.sid);
        let req = post_json(
            &app,
            &prepare_uri,
            &app.bot_a_token,
            json!({
                "file_name": name,
                "size": bytes.len() as u64,
                "mime_type": "text/plain",
            }),
        );
        let (status, body, _) = send(app, req).await;
        assert_eq!(status, StatusCode::CREATED, "prepare body: {body:?}");
        let file_id = body
            .get("file_id")
            .and_then(|v| v.as_str())
            .unwrap()
            .to_string();

        // Upload (single part)
        let upload_uri = format!(
            "/sessions/{}/files/{}/content",
            app.sid, file_id
        );
        let req = put_bytes(app, &upload_uri, &app.bot_a_token, bytes.to_vec());
        let (status, body, _) = send(app, req).await;
        assert_eq!(status, StatusCode::ACCEPTED, "upload body: {body:?}");

        // Complete
        let complete_uri = format!(
            "/sessions/{}/files/{}/complete",
            app.sid, file_id
        );
        let req = post_json(&app, &complete_uri, &app.bot_a_token, json!({}));
        let (status, body, _) = send(app, req).await;
        assert_eq!(status, StatusCode::OK, "complete body: {body:?}");

        let status_slug = body
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap()
            .to_string();
        let file_status = match status_slug.as_str() {
            "Ready" => FileStatus::Ready,
            "Pending" => FileStatus::Pending,
            "Deleting" => FileStatus::Deleting,
            _ => FileStatus::Failed,
        };
        (file_id, file_status)
    }
}