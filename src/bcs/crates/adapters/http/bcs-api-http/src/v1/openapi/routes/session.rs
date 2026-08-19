use axum::Router;
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, patch, post};
use bcs_service_api::application::v1::{
    AddSessionParticipant, AuthenticatedCaller, CollectSession, DeleteSession,
    DeleteSessionParticipant, GetSession, ListSessionMessages, ListSessions, UncollectSession,
    UpdateSessionParticipant,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::session::{
    AddSessionParticipantRequest, CollectSessionRequest, CreateSessionRequest, DeleteSessionQuery,
    ListSessionMessagesQuery, ListSessionsQuery, UncollectSessionQuery,
    UpdateSessionParticipantRequest, UpdateSessionRequest,
};

pub fn router() -> Router<ApiState> {
    Router::new()
        .route(
            "/groups/{group_id}/sessions",
            get(list_sessions).post(create_session),
        )
        .route(
            "/sessions/{session_id}",
            get(get_session)
                .patch(update_session)
                .delete(delete_session),
        )
        .route(
            "/sessions/{session_id}/messages",
            get(list_session_messages),
        )
        .route(
            "/sessions/{session_id}/collect",
            post(collect_session).delete(uncollect_session),
        )
        .route(
            "/sessions/{session_id}/participants",
            post(add_session_participant),
        )
        .route(
            "/sessions/{session_id}/participants/{bot_uuid}",
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
    let command = body
        .into_command(&caller, group_id)
        .map_err(|error| application_error_response(&request_id, error))?;
    let result = state
        .session_service
        .create(command)
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(
            20_100,
            "Created",
            result.session,
            request_id.0,
        )),
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
        .get(GetSession { caller, session_id })
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
    query: Result<Query<DeleteSessionQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .delete(DeleteSession {
            caller,
            session_id,
            acting_bot_id: query.acting_bot_id,
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

async fn collect_session(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<CollectSessionRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    if body.participant.trim().is_empty() {
        return Err(invalid_request(
            &request_id,
            "participant must not be empty",
        ));
    }
    let result = state
        .session_service
        .collect(CollectSession {
            caller,
            session_id,
            participant: body.participant,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn uncollect_session(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<UncollectSessionQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    if query.participant.trim().is_empty() {
        return Err(invalid_request(
            &request_id,
            "participant must not be empty",
        ));
    }
    let result = state
        .session_service
        .uncollect(UncollectSession {
            caller,
            session_id,
            participant: query.participant,
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
    body: Result<Json<AddSessionParticipantRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(session_id) = path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = state
        .session_service
        .add_participant(AddSessionParticipant {
            caller,
            session_id,
            bot_uuid: body.bot_uuid,
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
