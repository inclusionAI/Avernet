use axum::Router;
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, patch, post};
use bcs_service_api::application::v1::{
    AddSessionParticipant, AuthenticatedCaller, CompleteSession, DeleteSession,
    DeleteSessionParticipant, GetSession, ListSessionMessages, ListSessions,
    UpdateSessionParticipant,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::session::{
    CreateSessionRequest, ListSessionMessagesQuery, ListSessionsQuery, SessionParticipantInput,
    UpdateSessionRequest, UpdateSessionParticipantRequest,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route(
            "/openapi/v1/groups/{group_id}/sessions",
            get(list_sessions).post(create_session),
        )
        .route(
            "/openapi/v1/sessions/{session_id}",
            get(get_session)
                .patch(update_session)
                .delete(delete_session),
        )
        .route(
            "/openapi/v1/sessions/{session_id}/completion",
            post(complete_session),
        )
        .route(
            "/openapi/v1/sessions/{session_id}/messages",
            get(list_session_messages),
        )
        .route(
            "/openapi/v1/sessions/{session_id}/participants",
            post(add_session_participant),
        )
        .route(
            "/openapi/v1/sessions/{session_id}/participants/{bot_uuid}",
            patch(update_session_participant).delete(remove_session_participant),
        )
}

async fn create_session(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<CreateSessionRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(group_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .create(body.into_command(caller, group_id))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    let (status, code, message) = if result.created {
        (StatusCode::CREATED, 20_100, "Created")
    } else {
        (StatusCode::OK, 20_000, "OK")
    };
    Ok((
        status,
        Json(Envelope::success(code, message, result.session, request_id.0)),
    )
        .into_response())
}

async fn list_sessions(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<ListSessionsQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(group_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .list(ListSessions {
            caller,
            group_id,
            view_bot_id: query.view_bot_id,
            offset: query.offset,
            limit: query.limit,
            status: query.status,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn get_session(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .get(GetSession {
            caller,
            session_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn update_session(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<UpdateSessionRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .update(body.into_command(caller, session_id))
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn delete_session(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .delete(DeleteSession {
            caller,
            session_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn complete_session(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .complete(CompleteSession {
            caller,
            session_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn list_session_messages(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<ListSessionMessagesQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .message_service
        .list(ListSessionMessages {
            caller,
            session_id,
            before: query.before,
            limit: query.limit,
            view_bot_id: query.view_bot_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn add_session_participant(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<SessionParticipantInput>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .add_participant(AddSessionParticipant {
            caller,
            session_id,
            bot_uuid: body.bot_uuid,
            mode: body.mode,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn update_session_participant(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
    body: Result<Json<UpdateSessionParticipantRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((session_id, bot_uuid)) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .update_participant(UpdateSessionParticipant {
            caller,
            session_id,
            bot_uuid,
            mode: body.mode,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn remove_session_participant(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<(String, String)>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path((session_id, bot_uuid)) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .delete_participant(DeleteSessionParticipant {
            caller,
            session_id,
            bot_uuid,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}
