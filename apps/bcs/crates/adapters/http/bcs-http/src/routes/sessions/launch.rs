use super::*;

fn session_to_json_with_state_machine_run(
    sess: &bcs_service_api::Session,
    run: Option<&StateMachineRunView>,
    initial_run: Option<&bcs_service_api::InitialSessionRun>,
) -> Value {
    let mut v = session_to_json(sess);
    if let (Some(obj), Some(run)) = (v.as_object_mut(), run) {
        obj.insert(
            "state_machine_run_id".into(),
            Value::String(run.run.run_id.clone()),
        );
        obj.insert(
            "state_machine_run".into(),
            serde_json::to_value(run).unwrap_or(Value::Null),
        );
    }
    if let (Some(obj), Some(initial_run)) = (v.as_object_mut(), initial_run) {
        obj.insert(
            "initial_run".into(),
            serde_json::to_value(initial_run).unwrap_or(Value::Null),
        );
    }
    v
}

// ---------------------------------------------------------------
// POST /groups/{id}/sessions
//
// Ports 6eb4b6384: resolve caller identity, verify created_by
// ownership (human must be self, bot must be self or owner),
// and enforce participant access to the group.
// ---------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct CreateSessionRequest {
    #[serde(default)]
    pub context_from_session_id: Option<String>,
    #[serde(default)]
    pub context_names: Option<Vec<String>>,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub session_title: Option<String>,
    #[serde(default)]
    pub input: Option<Value>,
    #[serde(default)]
    pub meta: Option<Value>,
    #[serde(default)]
    pub session_kind: Option<String>,
    #[serde(default)]
    pub created_by: Option<String>,
    #[serde(default)]
    pub caller_role: Option<String>,
    /// Optional Session-level message projection for the authenticated Human
    /// inserted or inherited during creation. Omission preserves the legacy
    /// `full` default for a session-only Human.
    #[serde(default)]
    pub message_view_scope: Option<MessageViewScope>,
    /// Optional delivery override for the driver bot's `<GroupContext>`
    /// message: `"send"` (default, driver is asked to respond) or
    /// `"inject"` (driver observes silently). Other participants always
    /// receive the context via `chat.inject`.
    #[serde(default)]
    pub group_context_delivery: Option<DeliveryType>,
}

fn legacy_creator_role(value: Option<String>) -> Option<RequestedSessionRole> {
    value.map(|value| match value.as_str() {
        "driver" => RequestedSessionRole::Known(DomainParticipantRole::Driver),
        "consultant" => RequestedSessionRole::Known(DomainParticipantRole::Consultant),
        "manager" => RequestedSessionRole::Known(DomainParticipantRole::Manager),
        "worker" => RequestedSessionRole::Known(DomainParticipantRole::Worker),
        "observer" => RequestedSessionRole::Known(DomainParticipantRole::Observer),
        _ => RequestedSessionRole::Unknown(value),
    })
}

fn legacy_session_kind(value: Option<&str>) -> Option<DomainSessionKind> {
    value.map(|value| match value {
        "service_invocation" => DomainSessionKind::ServiceInvocation,
        // Preserve legacy behavior: every other explicit value was treated as chat.
        _ => DomainSessionKind::Chat,
    })
}

fn session_launch_error_to_legacy(error: SessionLaunchError) -> Response {
    match error {
        SessionLaunchError::GroupNotFound(group_id) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "error": "not_found",
                "message": format!("group {group_id} not found"),
            })),
        )
            .into_response(),
        SessionLaunchError::SessionNotFound(session_id) => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({"error": session_id})),
        )
            .into_response(),
        SessionLaunchError::Forbidden(message) => (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({
                "error": "forbidden",
                "message": message,
            })),
        )
            .into_response(),
        SessionLaunchError::InvalidRole(message) => (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({
                "error": "invalid_role",
                "message": message,
            })),
        )
            .into_response(),
        SessionLaunchError::InvalidRequest(message) => (StatusCode::BAD_REQUEST, Json(serde_json::json!({"error": message}))).into_response(),
        SessionLaunchError::Conflict(message) => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({"error": message})),
        )
            .into_response(),
        SessionLaunchError::Runtime(error) => collaboration_error_to_response(error),
        SessionLaunchError::Internal(error) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": error.to_string()})),
        )
            .into_response(),
    }
}

pub async fn create_session_for_group(
    State(state): State<HttpAppState>,
    Path(group_id): Path<String>,
    headers: HeaderMap,
    uri: Uri,
    Json(body): Json<CreateSessionRequest>,
) -> impl IntoResponse {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(GroupChatCaller::Human(human)) => SessionCaller::Human {
            actor_id: human.actor_id,
            owner_id: human.staff_no,
            display_name: human.nick_name,
        },
        Ok(GroupChatCaller::Bot { bot_uuid }) => SessionCaller::Bot { bot_uuid },
        Err(_) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({"error": "unauthorized"})),
            )
                .into_response();
        }
    };

    let inherited_contexts = match (body.context_from_session_id, body.context_names) {
        (None, None) => Vec::new(),
        (Some(source), names) if body.session_id.is_none() => {
            match state.services.group_context.inheritance_sources(&group_id, &source, caller.actor_id(), names.as_deref()).await {
                Ok(contexts) => contexts,
                Err(error) => return crate::routes::group_context::GroupContextHttpError::from(error).into_response(),
            }
        }
        _ => return session_launch_error_to_legacy(SessionLaunchError::InvalidRequest(
            "context inheritance requires a new session, a source session id".into(),
        )),
    };
    let session_id = body.session_id;
    let request = SessionLaunchRequest {
        caller,
        group_id,
        requested_creator: body.created_by,
        title: body.session_title,
        kind: legacy_session_kind(body.session_kind.as_deref()),
        input: body.input,
        meta: body.meta,
        public_creator_role: legacy_creator_role(body.caller_role),
        human_message_view_scope: body.message_view_scope,
        context_delivery: body.group_context_delivery,
    };

    let result = match session_id {
        Some(session_id) => {
            state
                .services
                .session_launch
                .reactivate(ReactivateSessionLaunch {
                    session_id,
                    request,
                })
                .await
        }
        None if inherited_contexts.is_empty() => state.services.session_launch.create(CreateSessionLaunch { request }).await,
        None => {
            state
                .services
                .session_launch
                .create_with_contexts(CreateSessionLaunch { request }, inherited_contexts)
                .await
        }
    };

    match result {
        Ok(outcome) => (
            if outcome.created {
                StatusCode::CREATED
            } else {
                StatusCode::OK
            },
            Json(session_to_json_with_state_machine_run(
                &outcome.session,
                outcome.state_machine_run.as_ref(),
                outcome.initial_run.as_ref(),
            )),
        )
            .into_response(),
        Err(error) => session_launch_error_to_legacy(error),
    }
}
