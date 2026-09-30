use std::sync::Arc;

use axum::Router;
use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Json, Path, Query, State};
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, CreateEventSubscription, CreateEventSubscriptionRequest,
    DeleteEventSubscription, EventSubscriptionService, GetEventDelivery, GetEventSubscription,
    ListEventDeliveries, ListEventSubscriptions, PatchEventSubscription, ReplayEventDelivery,
    SkipEventDelivery, TestEventSubscription,
};

use crate::v1::common::{
    ApiState, Envelope, ErrorResponse, RequestId, application_error_response, invalid_request,
};
use crate::v1::openapi::dto::event_subscription::{
    DeleteEventSubscriptionQuery, ListEventDeliveriesQuery, ListEventSubscriptionsQuery,
    PatchEventSubscriptionBody, ReplayEventDeliveryBody, SkipEventDeliveryBody,
};

pub fn router() -> Router<ApiState> {
    // Axum parameters must occupy a complete path segment. The POST handlers
    // therefore validate and strip the contract's `:test`, `:replay`, and
    // `:skip` action suffixes after matching the dynamic segment.
    Router::new()
        .route(
            "/event-subscriptions",
            get(list_subscriptions).post(create_subscription),
        )
        .route(
            "/event-subscriptions/{subscription_id}",
            get(get_subscription)
                .post(test_subscription)
                .patch(patch_subscription)
                .delete(delete_subscription),
        )
        .route(
            "/event-subscriptions/{subscription_id}/deliveries",
            get(list_deliveries),
        )
        .route(
            "/event-deliveries/{delivery_id}",
            get(get_delivery).post(delivery_action),
        )
}

fn service(
    state: &ApiState,
    request_id: &RequestId,
) -> Result<Arc<dyn EventSubscriptionService>, ErrorResponse> {
    state.event_subscription_service.clone().ok_or_else(|| {
        application_error_response(
            request_id,
            ApplicationError::internal("Event Subscription service is not configured"),
        )
    })
}

fn if_match_revision(headers: &HeaderMap) -> Result<Option<u64>, &'static str> {
    let Some(value) = headers.get(header::IF_MATCH) else {
        return Ok(None);
    };
    let value = value.to_str().map_err(|_| "If-Match must be UTF-8")?.trim();
    if value == "*" || value.starts_with("W/") {
        return Err("If-Match must contain one strong numeric revision");
    }
    let value = value
        .strip_prefix('"')
        .and_then(|value| value.strip_suffix('"'))
        .unwrap_or(value);
    if value.is_empty() || value.contains(',') {
        return Err("If-Match must contain one strong numeric revision");
    }
    value
        .parse::<u64>()
        .map(Some)
        .map_err(|_| "If-Match must contain one strong numeric revision")
}

fn expected_revision(
    request_id: &RequestId,
    headers: &HeaderMap,
    field_revision: Option<u64>,
) -> Result<u64, ErrorResponse> {
    let header_revision =
        if_match_revision(headers).map_err(|message| invalid_request(request_id, message))?;
    match (field_revision, header_revision) {
        (Some(field), Some(header)) if field != header => Err(invalid_request(
            request_id,
            "revision and If-Match must identify the same revision",
        )),
        (Some(0), _) | (_, Some(0)) => Err(invalid_request(
            request_id,
            "revision must be greater than zero",
        )),
        (Some(revision), _) | (_, Some(revision)) => Ok(revision),
        (None, None) => Err(invalid_request(
            request_id,
            "revision or If-Match is required",
        )),
    }
}

async fn create_subscription(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    body: Result<Json<CreateEventSubscriptionRequest>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Json(request) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .create(CreateEventSubscription { caller, request })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::CREATED,
        Json(Envelope::success(20_100, "Created", result, request_id.0)),
    )
        .into_response())
}

async fn list_subscriptions(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    query: Result<Query<ListEventSubscriptionsQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let scope = query
        .scope()
        .map_err(|message| invalid_request(&request_id, message))?;
    let result = service(&state, &request_id)?
        .list(ListEventSubscriptions {
            caller,
            scope,
            status: query.status,
            cursor: query.cursor,
            limit: query.limit,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn get_subscription(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(subscription_id) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .get(GetEventSubscription {
            caller,
            subscription_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn patch_subscription(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
    body: Result<Json<PatchEventSubscriptionBody>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(subscription_id) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let revision = expected_revision(&request_id, &headers, body.revision)?;
    let result = service(&state, &request_id)?
        .patch(PatchEventSubscription {
            caller,
            subscription_id,
            expected_revision: revision,
            patch: body.into_patch(),
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn delete_subscription(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    headers: HeaderMap,
    query: Result<Query<DeleteEventSubscriptionQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(subscription_id) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let revision = expected_revision(&request_id, &headers, query.revision)?;
    let result = service(&state, &request_id)?
        .delete(DeleteEventSubscription {
            caller,
            subscription_id,
            expected_revision: revision,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn test_subscription(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(subscription_action) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let subscription_id = subscription_action
        .strip_suffix(":test")
        .filter(|value| !value.is_empty())
        .ok_or_else(|| invalid_request(&request_id, "Unknown Event Subscription action"))?
        .to_string();
    let result = service(&state, &request_id)?
        .test(TestEventSubscription {
            caller,
            subscription_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn list_deliveries(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<ListEventDeliveriesQuery>, QueryRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(subscription_id) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Query(query) = query.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .list_deliveries(ListEventDeliveries {
            caller,
            subscription_id,
            status: query.status,
            cursor: query.cursor,
            limit: query.limit,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn get_delivery(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(delivery_id) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let result = service(&state, &request_id)?
        .get_delivery(GetEventDelivery {
            caller,
            delivery_id,
        })
        .await
        .map_err(|error| application_error_response(&request_id, error))?;
    Ok((
        StatusCode::OK,
        Json(Envelope::success(20_000, "OK", result, request_id.0)),
    )
        .into_response())
}

async fn delivery_action(
    State(state): State<ApiState>,
    Extension(caller): Extension<AuthenticatedCaller>,
    Extension(request_id): Extension<RequestId>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<serde_json::Value>, JsonRejection>,
) -> Result<Response, ErrorResponse> {
    let Path(delivery_action) =
        path.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    let Json(body) = body.map_err(|error| invalid_request(&request_id, error.body_text()))?;
    if let Some(delivery_id) = delivery_action
        .strip_suffix(":replay")
        .filter(|value| !value.is_empty())
    {
        let body: ReplayEventDeliveryBody = serde_json::from_value(body)
            .map_err(|error| invalid_request(&request_id, error.to_string()))?;
        let result = service(&state, &request_id)?
            .replay_delivery(ReplayEventDelivery {
                caller,
                delivery_id: delivery_id.to_string(),
                replay_request_id: body.replay_request_id,
                expected_subscription_revision: body.expected_subscription_revision,
            })
            .await
            .map_err(|error| application_error_response(&request_id, error))?;
        return Ok((
            StatusCode::ACCEPTED,
            Json(Envelope::success(20_200, "Accepted", result, request_id.0)),
        )
            .into_response());
    }
    if let Some(delivery_id) = delivery_action
        .strip_suffix(":skip")
        .filter(|value| !value.is_empty())
    {
        let body: SkipEventDeliveryBody = serde_json::from_value(body)
            .map_err(|error| invalid_request(&request_id, error.to_string()))?;
        let result = service(&state, &request_id)?
            .skip_delivery(SkipEventDelivery {
                caller,
                delivery_id: delivery_id.to_string(),
                reason: body.reason,
            })
            .await
            .map_err(|error| application_error_response(&request_id, error))?;
        return Ok((
            StatusCode::OK,
            Json(Envelope::success(20_000, "OK", result, request_id.0)),
        )
            .into_response());
    }
    Err(invalid_request(
        &request_id,
        "Unknown Event Delivery action",
    ))
}
