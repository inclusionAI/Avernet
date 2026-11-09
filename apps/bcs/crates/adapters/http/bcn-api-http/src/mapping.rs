//! Protocol models stop here; public tasks carry no SDK types.
use a2a::{A2AError, Part, PartContent};
use bcn_runtime_model::*;
use std::collections::HashMap;
pub const INTERACTION: &str = "urn:avernet:bcn:interaction-id";
pub const STREAM: &str = "urn:avernet:bcn:cursor";
pub fn error(e: RuntimeError) -> A2AError {
    match e {
        RuntimeError::NotFound => A2AError::task_not_found("requested task"),
        RuntimeError::AccessDenied => A2AError::new(-32000, "access denied"),
        RuntimeError::InvalidInput(_) => A2AError::invalid_params("invalid request parameters"),
        RuntimeError::Unsupported(_) => A2AError::unsupported_operation("unsupported operation"),
        RuntimeError::Conflict(_) | RuntimeError::ContextBlocked => A2AError::invalid_params("task state conflicts with request"),
        RuntimeError::CapacityExceeded => A2AError::new(-32000, "capacity exceeded"),
        RuntimeError::ExecutionUnknown => A2AError::new(-32000, "execution_unknown"),
        RuntimeError::CancellationUnknown => A2AError::new(-32000, "cancellation_unknown"),
        _ => A2AError::internal("runtime temporarily unavailable"),
    }
}
pub fn id(value: &str) -> Result<(), A2AError> {
    if value.trim().is_empty() || value.len() > 256 { Err(A2AError::invalid_params("invalid identity")) } else { Ok(()) }
}
pub fn submit(ctx: &CallerContext, target: ExecutionTarget, req: a2a::SendMessageRequest) -> Result<SubmitRequest, A2AError> {
    id(&req.message.message_id)?;
    if req.message.role != a2a::Role::User { return Err(A2AError::invalid_params("user message required")); }
    if let Some(c) = &req.configuration {
        if c.task_push_notification_config.is_some() { return Err(A2AError::push_notification_not_supported()); }
        if c.accepted_output_modes.as_ref().is_some_and(|m| !m.iter().any(|m| m == "text/plain")) { return Err(A2AError::content_type_not_supported()); }
        history(c.history_length)?;
    }
    let mut parts = Vec::new();
    for part in req.message.parts {
        if part.media_type.as_ref().is_some_and(|v| v != "text/plain") { return Err(A2AError::content_type_not_supported()); }
        let PartContent::Text(text) = part.content else { return Err(A2AError::content_type_not_supported()); };
        parts.push(MessagePart::Text { text });
    }
    let interaction_id = req.message.metadata.as_ref().and_then(|m|m.get(INTERACTION))
        .map(|v|serde_json::from_value::<InteractionId>(v.clone()).map_err(|_|A2AError::invalid_params("invalid scoped interaction"))).transpose()?;
    if let Some(i) = &interaction_id { id(&i.value)?; if i.scope != ctx.scope { return Err(error(RuntimeError::AccessDenied)); } }
    for value in [&req.message.context_id, &req.message.task_id].into_iter().flatten() { id(value)?; }
    Ok(SubmitRequest { target, interaction_id, message: Message {
        message_id: MessageId::new(ctx.scope.clone(), req.message.message_id),
        context_id: req.message.context_id.map(|v|SessionId::new(ctx.scope.clone(),v)),
        task_id: req.message.task_id.map(|v|TaskId::new(ctx.scope.clone(),v)), parts,
    } })
}
pub fn history(length: Option<i32>) -> Result<(), A2AError> {
    if length.is_some_and(|v|v != 0) { Err(A2AError::unsupported_operation("history is not available")) } else { Ok(()) }
}
pub fn parts(parts: &[MessagePart]) -> Vec<Part> {
    parts.iter().filter_map(|p|match p { MessagePart::Text { text } => Some(Part::text(text)), _=>None }).collect()
}
pub fn artifact(parts: &[MessagePart]) -> a2a::Artifact {
    a2a::Artifact { artifact_id: "output".into(), name: None, description: None, parts: self::parts(parts), metadata: None, extensions: None }
}
pub fn task(view: &TaskView) -> a2a::Task {
    let state = match view.status {
        TaskStatus::Submitted => a2a::TaskState::Submitted,
        TaskStatus::Working | TaskStatus::PendingResult => a2a::TaskState::Working,
        TaskStatus::InputRequired => a2a::TaskState::InputRequired,
        TaskStatus::AuthRequired => a2a::TaskState::AuthRequired,
        TaskStatus::Completed => a2a::TaskState::Completed,
        TaskStatus::Failed => a2a::TaskState::Failed,
        TaskStatus::Cancelled => a2a::TaskState::Canceled,
    };
    let mut prompts = vec![];
    for i in &view.active_interactions {
        let mut part = Part::text(&i.prompt);
        part.metadata = Some(HashMap::from([(INTERACTION.into(),serde_json::to_value(&i.interaction_id).expect("scoped ID serialization"))]));
        prompts.push(part);
    }
    let message = if prompts.is_empty() { None } else { Some(a2a::Message {
        message_id: format!("{}-questions",view.task_id.value), context_id: Some(view.session_id.value.clone()), task_id: Some(view.task_id.value.clone()),
        role: a2a::Role::Agent, parts: prompts, metadata: None, extensions: Some(vec![INTERACTION.into()]), reference_task_ids: None,
    }) };
    a2a::Task { id: view.task_id.value.clone(), context_id: view.session_id.value.clone(), status:a2a::TaskStatus { state, message, timestamp:i64::try_from(view.updated_at_ms).ok().and_then(chrono::DateTime::from_timestamp_millis) },
        artifacts: if view.output.is_empty(){None}else{Some(vec![artifact(&view.output)])}, history:None,
        metadata: view.error.clone().map(|e|HashMap::from([("urn:avernet:bcn:error".into(),serde_json::json!(error(e).message))])),
    }
}
