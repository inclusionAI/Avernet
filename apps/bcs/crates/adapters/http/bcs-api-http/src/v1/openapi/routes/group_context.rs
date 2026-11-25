use axum::Router;
use axum::extract::rejection::{JsonRejection, QueryRejection};
use axum::extract::{Extension, Json, Query, State};
use axum::routing::post;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, GroupContextApplicationService,
};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};

pub(crate) fn router() -> Router<ApiState> {
    Router::new()
        .route("/groupcontext/shared-info", post(shared_info))
        .route("/groupcontext/list-preset", post(list_presets))
        .route(
            "/groupcontext/manage/list-preset",
            post(list_managed_presets),
        )
        .route("/groupcontext/manage/add-preset", post(add_preset))
        .route("/groupcontext/manage/update-preset", post(update_preset))
        .route("/groupcontext/manage/delete-preset", post(delete_preset))
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ContextQuery {
    group_id: String,
    session_id: Option<String>,
    #[serde(rename = "view_bot_id")]
    view_bot_id: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ManagementQuery {
    group_id: String,
}

fn human_actor(
    caller: &AuthenticatedCaller,
    request_id: &RequestId,
) -> Result<String, ErrorResponse> {
    match (&caller.user, &caller.bot) {
        (Some(user), None) => Ok(format!("human_{}", user.id)),
        _ => Err(application_error_response(
            request_id,
            ApplicationError::forbidden_code(
                "permission_denied",
                "Group Context browser access requires a Human caller",
            ),
        )),
    }
}

fn service<'a>(
    state: &'a ApiState,
    request_id: &RequestId,
) -> Result<&'a dyn GroupContextApplicationService, ErrorResponse> {
    state.group_context.as_deref().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal("Group Context service unavailable"),
        )
    })
}

fn success(data: Value, request_id: RequestId) -> Json<Envelope<Value>> {
    Json(Envelope::success(20_000, "OK", data, request_id.0))
}

#[derive(Deserialize)]
struct AddPreset {
    name: String,
    #[serde(default)]
    description: String,
}

#[derive(Deserialize)]
struct UpdatePreset {
    name: String,
    new_name: Option<String>,
    description: Option<String>,
}

#[derive(Deserialize)]
struct DeletePreset {
    name: String,
}

async fn shared_info(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ContextQuery>, QueryRejection>,
) -> Result<Json<Envelope<Value>>, ErrorResponse> {
    human_actor(&caller, &request_id)?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let service = service(&state, &request_id)?;
    let items = service
        .shared_info(
            &query.group_id,
            query.session_id.as_deref(),
            query.view_bot_id.as_deref(),
            &caller,
        )
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok(success(json!({ "items": items }), request_id))
}

async fn list_presets(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ContextQuery>, QueryRejection>,
) -> Result<Json<Envelope<Value>>, ErrorResponse> {
    human_actor(&caller, &request_id)?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let service = service(&state, &request_id)?;
    let presets = service
        .list_presets(&query.group_id, &caller, query.session_id.as_deref())
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok(success(json!({ "presets": presets }), request_id))
}

async fn list_managed_presets(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ManagementQuery>, QueryRejection>,
) -> Result<Json<Envelope<Value>>, ErrorResponse> {
    let actor = human_actor(&caller, &request_id)?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let service = service(&state, &request_id)?;
    let presets = service
        .list_presets(&query.group_id, &caller, None)
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    let can_manage = service
        .can_manage_presets(&query.group_id, &actor)
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok(success(
        json!({ "presets": presets, "can_manage": can_manage }),
        request_id,
    ))
}

async fn add_preset(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ManagementQuery>, QueryRejection>,
    body: Result<Json<AddPreset>, JsonRejection>,
) -> Result<Json<Envelope<Value>>, ErrorResponse> {
    let actor = human_actor(&caller, &request_id)?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let service = service(&state, &request_id)?;
    service
        .add_preset(&query.group_id, &actor, body.name, body.description)
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok(success(json!({}), request_id))
}

async fn update_preset(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ManagementQuery>, QueryRejection>,
    body: Result<Json<UpdatePreset>, JsonRejection>,
) -> Result<Json<Envelope<Value>>, ErrorResponse> {
    let actor = human_actor(&caller, &request_id)?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let service = service(&state, &request_id)?;
    service
        .update_preset(
            &query.group_id,
            &actor,
            body.name,
            body.new_name,
            body.description,
        )
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok(success(json!({}), request_id))
}

async fn delete_preset(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ManagementQuery>, QueryRejection>,
    body: Result<Json<DeletePreset>, JsonRejection>,
) -> Result<Json<Envelope<Value>>, ErrorResponse> {
    let actor = human_actor(&caller, &request_id)?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let service = service(&state, &request_id)?;
    service
        .delete_preset(&query.group_id, &actor, body.name)
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok(success(json!({}), request_id))
}
