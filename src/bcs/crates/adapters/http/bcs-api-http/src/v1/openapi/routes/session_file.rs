use std::pin::Pin;
use std::task::{Context, Poll};

use axum::body::{Body, BodyDataStream};
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{DefaultBodyLimit, Extension, Json, Path, Query, State};
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, CompleteSessionFile, DeleteSessionFile,
    DownloadSessionFile, DownloadSharedSessionFile, GetSessionFile, IdentityPolicy,
    ListSessionFiles, PrepareSessionFile, SessionFileApplicationService, SessionFileContent,
    ShareSessionFile, UploadSessionFileContent, select_principal,
};
use bcs_storage_api::{ByteStream, ByteStreamTrait};
use bytes::Bytes;
use futures::Stream;
use serde_json::{Value, json};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, IdentityPolicyMethodRouterExt, RequestId,
    RouteIdentityPolicy, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::session_file::{
    ListSessionFilesQuery, PrepareSessionFileRequest, ProtectedFileContentQuery,
    ShareSessionFileRequest, SharedFileContentQuery, UploadSessionFileQuery,
};

use super::super::SessionFileUrlProjector;

struct RequestBodyStream(BodyDataStream);

impl Stream for RequestBodyStream {
    type Item = Result<Bytes, std::io::Error>;

    fn poll_next(mut self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        match Pin::new(&mut self.0).poll_next(context) {
            Poll::Ready(Some(Ok(bytes))) => Poll::Ready(Some(Ok(bytes))),
            Poll::Ready(Some(Err(error))) => {
                Poll::Ready(Some(Err(std::io::Error::other(error))))
            }
            Poll::Ready(None) => Poll::Ready(None),
            Poll::Pending => Poll::Pending,
        }
    }
}

impl ByteStreamTrait for RequestBodyStream {}

pub fn protected_router() -> Router<ApiState> {
    let policy = IdentityPolicy::HumanOrOwnedBot;
    Router::new()
        .route(
            "/sessions/{session_id}/files",
            get(list_files)
                .post(prepare_file)
                .identity_policy(policy),
        )
        .route(
            "/sessions/{session_id}/files/{file_id}",
            get(get_file)
                .delete(delete_file)
                .identity_policy(policy),
        )
        .route(
            "/sessions/{session_id}/files/{file_id}/content",
            get(download_file)
                .put(upload_content)
                .layer(DefaultBodyLimit::disable())
                .identity_policy(policy),
        )
        .route(
            "/sessions/{session_id}/files/{file_id}/complete",
            post(complete_file).identity_policy(policy),
        )
        .route(
            "/sessions/{session_id}/files/{file_id}/share",
            post(share_file).identity_policy(policy),
        )
}

pub fn public_router() -> Router<ApiState> {
    Router::new().route("/sessions/shared-file/content", get(download_shared_file))
}

fn service(state: &ApiState) -> Result<&dyn SessionFileApplicationService, ApplicationError> {
    state
        .session_file_service
        .as_deref()
        .ok_or_else(|| ApplicationError::internal("V1 Session File service is not configured"))
}

fn projector(state: &ApiState) -> Result<&SessionFileUrlProjector, ApplicationError> {
    state
        .session_file_url_projector
        .as_ref()
        .ok_or_else(|| ApplicationError::internal("V1 Session File URL projector is not configured"))
}

fn authorize_identity(
    caller: &AuthenticatedCaller,
    policy: IdentityPolicy,
) -> Result<(), ApplicationError> {
    select_principal(caller, policy).map(|_| ())
}

fn error(request_id: &RequestId, error: ApplicationError) -> ErrorResponse {
    application_error_response(request_id, error)
}

async fn prepare_file(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    RouteIdentityPolicy(policy): RouteIdentityPolicy,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<PrepareSessionFileRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    authorize_identity(&caller, policy).map_err(|e| error(&request_id, e))?;
    let Path(session_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Json(body) = body.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let result = service(&state)
        .map_err(|e| error(&request_id, e))?
        .prepare(PrepareSessionFile {
            caller,
            session_id: session_id.clone(),
            file_name: body.file_name,
            size: body.size,
            mime_type: body.mime_type,
        })
        .await
        .map_err(|e| error(&request_id, e))?;
    let mut data = projector(&state)
        .map_err(|e| error(&request_id, e))?
        .project_upload_target(
            result.upload_target,
            result.proxy_upload,
            &session_id,
            &result.file.file_id,
        );
    if let Some(object) = data.as_object_mut() {
        object.insert("file_id".to_string(), Value::String(result.file.file_id));
    }
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(20_100, "Created", data, request_id.0)),
    )
        .into_response())
}

async fn list_files(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    RouteIdentityPolicy(policy): RouteIdentityPolicy,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<ListSessionFilesQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    authorize_identity(&caller, policy).map_err(|e| error(&request_id, e))?;
    let Path(session_id) = path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Query(query) = query.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let result = service(&state)
        .map_err(|e| error(&request_id, e))?
        .list(ListSessionFiles {
            caller,
            session_id,
            prefix: query.prefix,
            status: query.status,
            limit: query.limit,
            offset: query.offset,
        })
        .await
        .map_err(|e| error(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn get_file(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    RouteIdentityPolicy(policy): RouteIdentityPolicy,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    authorize_identity(&caller, policy).map_err(|e| error(&request_id, e))?;
    let Path((session_id, file_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let result = service(&state)
        .map_err(|e| error(&request_id, e))?
        .get(GetSessionFile {
            caller,
            session_id,
            file_id,
        })
        .await
        .map_err(|e| error(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn delete_file(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    RouteIdentityPolicy(policy): RouteIdentityPolicy,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    authorize_identity(&caller, policy).map_err(|e| error(&request_id, e))?;
    let Path((session_id, file_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let result = service(&state)
        .map_err(|e| error(&request_id, e))?
        .delete(DeleteSessionFile {
            caller,
            session_id,
            file_id,
        })
        .await
        .map_err(|e| error(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn upload_content(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    RouteIdentityPolicy(policy): RouteIdentityPolicy,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
    query: Result<Query<UploadSessionFileQuery>, QueryRejection>,
    headers: HeaderMap,
    body: Body,
) -> Result<Response, ErrorResponse> {
    authorize_identity(&caller, policy).map_err(|e| error(&request_id, e))?;
    let Path((session_id, file_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Query(query) = query.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let content_length = headers
        .get(header::CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<u64>().ok());
    let stream: ByteStream = Box::new(RequestBodyStream(body.into_data_stream()));
    let result = service(&state)
        .map_err(|e| error(&request_id, e))?
        .upload_content(UploadSessionFileContent {
            caller,
            session_id,
            file_id,
            part_number: query.part,
            body: stream,
            content_length,
        })
        .await
        .map_err(|e| error(&request_id, e))?;
    Ok((
        StatusCode::ACCEPTED,
        Json(Envelope::success(20_000, "Accepted", result, request_id.0)),
    )
        .into_response())
}

async fn complete_file(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    RouteIdentityPolicy(policy): RouteIdentityPolicy,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    authorize_identity(&caller, policy).map_err(|e| error(&request_id, e))?;
    let Path((session_id, file_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let notification_content_url = projector(&state)
        .map_err(|e| error(&request_id, e))?
        .content_url(&session_id, &file_id);
    let result = service(&state)
        .map_err(|e| error(&request_id, e))?
        .complete(CompleteSessionFile {
            caller,
            session_id,
            file_id,
            notification_content_url,
        })
        .await
        .map_err(|e| error(&request_id, e))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn share_file(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    RouteIdentityPolicy(policy): RouteIdentityPolicy,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
    body: Result<Json<ShareSessionFileRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    authorize_identity(&caller, policy).map_err(|e| error(&request_id, e))?;
    let Path((session_id, file_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Json(body) = body.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let result = service(&state)
        .map_err(|e| error(&request_id, e))?
        .share(ShareSessionFile {
            caller,
            session_id,
            file_id,
            ttl_seconds: body.ttl_seconds,
        })
        .await
        .map_err(|e| error(&request_id, e))?;
    let data = json!({
        "share_url": projector(&state)
            .map_err(|e| error(&request_id, e))?
            .shared_content_url(&result.share_token),
        "share_token": result.share_token,
        "expires_at": result.expires_at,
    });
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(20_100, "Created", data, request_id.0)),
    )
        .into_response())
}

async fn download_file(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    RouteIdentityPolicy(policy): RouteIdentityPolicy,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
    query: Result<Query<ProtectedFileContentQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    authorize_identity(&caller, policy).map_err(|e| error(&request_id, e))?;
    let Path((session_id, file_id)) =
        path.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let Query(query) = query.map_err(|e| invalid_request(&request_id, e.body_text()))?;
    let content = service(&state)
        .map_err(|e| error(&request_id, e))?
        .download(DownloadSessionFile {
            caller,
            session_id,
            file_id,
            show: query.show,
        })
        .await
        .map_err(|e| error(&request_id, e))?;
    Ok(content_response(content))
}

async fn download_shared_file(
    State(state): State<ApiState>,
    headers: HeaderMap,
    query: Result<Query<SharedFileContentQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let request_id = RequestId::from_headers(&headers);
    let Query(query) = query.map_err(|_| {
        application_error_response(
            &request_id,
            ApplicationError::not_found("shared_file_not_found", "Shared file was not found"),
        )
    })?;
    let content = service(&state)
        .map_err(|e| error(&request_id, e))?
        .download_shared(DownloadSharedSessionFile {
            token: query.token,
            show: query.show,
        })
        .await
        .map_err(|e| error(&request_id, e))?;
    Ok(content_response(content))
}

fn content_response(content: SessionFileContent) -> Response {
    match content {
        SessionFileContent::Redirect { download_url, .. } => (
            StatusCode::FOUND,
            [(header::LOCATION, download_url)],
        )
            .into_response(),
        SessionFileContent::Stream { file, body, inline } => {
            let mut headers = HeaderMap::new();
            if let Ok(value) = file.mime_type.parse() {
                headers.insert(header::CONTENT_TYPE, value);
            }
            if let Ok(value) = file.size.to_string().parse() {
                headers.insert(header::CONTENT_LENGTH, value);
            }
            let disposition = format!(
                "{}; filename=\"{}\"",
                if inline { "inline" } else { "attachment" },
                file.file_name.replace('"', "\\\"")
            );
            if let Ok(value) = disposition.parse() {
                headers.insert(header::CONTENT_DISPOSITION, value);
            }
            (headers, Body::from_stream(body)).into_response()
        }
    }
}
