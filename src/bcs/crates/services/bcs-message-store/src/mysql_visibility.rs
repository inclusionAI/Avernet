use super::*;

fn visibility_domain_name(domain: MessageVisibilityDomain) -> &'static str {
    match domain {
        MessageVisibilityDomain::Chat => "chat",
        MessageVisibilityDomain::ManagerWorker => "manager_worker",
        MessageVisibilityDomain::StateMachine => "state_machine",
    }
}

pub(crate) fn serialize_visibility(
    msg: &NewMessage,
) -> Result<(&'static str, Option<&'static str>, Option<String>), MessageRepoError> {
    if matches!(
        msg.visibility_domain,
        MessageVisibilityDomain::ManagerWorker | MessageVisibilityDomain::StateMachine
    ) && msg.audience.is_none()
    {
        return Err(MessageRepoError::StorageError(
            "ManagerWorker/StateMachine messages require an audience".to_string(),
        ));
    }
    if let (Some(owner_bot_id), Some(audience)) = (msg.owner_bot_id.as_deref(), msg.audience.as_ref())
    {
        if !matches!(audience, MessageAudience::Directed { .. }) {
            return Err(MessageRepoError::StorageError(
                "owner_bot_id requires a directed audience on classified messages".to_string(),
            ));
        }
        if !audience.contains(owner_bot_id) {
            return Err(MessageRepoError::StorageError(
                "owner_bot_id must be included in the directed audience".to_string(),
            ));
        }
    }
    let (audience_kind, audience_actor_ids_json) = match &msg.audience {
        None => (None, None),
        Some(MessageAudience::Public) => (Some("public"), None),
        Some(MessageAudience::FullOnly) => (Some("full_only"), None),
        Some(MessageAudience::Directed { actor_ids }) => {
            msg.audience
                .as_ref()
                .expect("audience is present")
                .validate()
                .map_err(|error| MessageRepoError::StorageError(error.to_string()))?;
            let json = serde_json::to_string(actor_ids).map_err(|error| {
                MessageRepoError::StorageError(format!("serialize directed audience: {error}"))
            })?;
            (Some("directed"), Some(json))
        }
    };
    Ok((
        visibility_domain_name(msg.visibility_domain),
        audience_kind,
        audience_actor_ids_json,
    ))
}

fn parse_visibility_domain(
    raw: Option<&str>,
) -> Result<Option<MessageVisibilityDomain>, MessageRepoError> {
    match raw {
        None | Some("") => Ok(None),
        Some("chat") => Ok(Some(MessageVisibilityDomain::Chat)),
        Some("manager_worker") => Ok(Some(MessageVisibilityDomain::ManagerWorker)),
        Some("state_machine") => Ok(Some(MessageVisibilityDomain::StateMachine)),
        Some(other) => Err(MessageRepoError::StorageError(format!(
            "unknown visibility_domain: {other}"
        ))),
    }
}

fn parse_audience(
    kind: Option<&str>,
    actor_ids_json: Option<&str>,
) -> Result<Option<MessageAudience>, MessageRepoError> {
    match (kind, actor_ids_json.filter(|value| !value.is_empty())) {
        (None | Some(""), None) => Ok(None),
        (None | Some(""), Some(_)) => Err(MessageRepoError::StorageError(
            "audience actor ids exist without audience_kind".to_string(),
        )),
        (Some("public"), None) => Ok(Some(MessageAudience::Public)),
        (Some("full_only"), None) => Ok(Some(MessageAudience::FullOnly)),
        (Some("public" | "full_only"), Some(_)) => Err(MessageRepoError::StorageError(
            "non-directed audience must not contain actor ids".to_string(),
        )),
        (Some("directed"), Some(raw)) => {
            let actor_ids = serde_json::from_str::<Vec<String>>(raw).map_err(|error| {
                MessageRepoError::StorageError(format!("invalid directed audience JSON: {error}"))
            })?;
            MessageAudience::directed(actor_ids)
                .map(Some)
                .map_err(|error| MessageRepoError::StorageError(error.to_string()))
        }
        (Some("directed"), None) => Err(MessageRepoError::StorageError(
            "directed audience requires actor ids".to_string(),
        )),
        (Some(other), _) => Err(MessageRepoError::StorageError(format!(
            "unknown audience_kind: {other}"
        ))),
    }
}

pub(super) fn row_to_message(row: &bcs_db_api::DbRow) -> Result<PersistedMessage, MessageRepoError> {
    let content_str: String = db_get_column(row, "content")
        .map_err(|e| MessageRepoError::StorageError(format!("content: {}", e)))?;
    let content: serde_json::Value =
        serde_json::from_str(&content_str).unwrap_or(serde_json::Value::String(content_str));

    let sender_type_str: String = db_get_column(row, "sender_type")
        .map_err(|e| MessageRepoError::StorageError(format!("sender_type: {}", e)))?;
    let sender_type = match sender_type_str.as_str() {
        "bot" => SenderType::Bot,
        "human" => SenderType::Human,
        "system" => SenderType::System,
        other => {
            return Err(MessageRepoError::StorageError(format!(
                "unknown sender_type: {}",
                other
            )));
        }
    };

    let status_str: String = db_get_column(row, "status")
        .map_err(|e| MessageRepoError::StorageError(format!("status: {}", e)))?;
    let status = match status_str.as_str() {
        "normal" => PersistedMessageStatus::Normal,
        "recalled" => PersistedMessageStatus::Recalled,
        "deleted" => PersistedMessageStatus::Deleted,
        other => {
            return Err(MessageRepoError::StorageError(format!(
                "unknown status: {}",
                other
            )));
        }
    };

    let client_msg_id: Option<String> = row
        .get_string("client_msg_id")
        .map_err(|e| MessageRepoError::StorageError(format!("client_msg_id: {}", e)))?;
    let owner_bot_id: Option<String> = row
        .get_string("owner_bot_id")
        .map_err(|e| MessageRepoError::StorageError(format!("owner_bot_id: {}", e)))?;
    let visibility_domain = parse_visibility_domain(
        row.get_string("visibility_domain")
            .map_err(|e| MessageRepoError::StorageError(format!("visibility_domain: {e}")))?
            .as_deref(),
    )?;
    let audience = parse_audience(
        row.get_string("audience_kind")
            .map_err(|e| MessageRepoError::StorageError(format!("audience_kind: {e}")))?
            .as_deref(),
        row.get_string("audience_actor_ids_json")
            .map_err(|e| {
                MessageRepoError::StorageError(format!("audience_actor_ids_json: {e}"))
            })?
            .as_deref(),
    )?;

    let created_at_i64: i64 = db_get_column(row, "created_at")
        .map_err(|e| MessageRepoError::StorageError(format!("created_at: {}", e)))?;

    let run_id: String = db_get_column(row, "run_id")
        .map_err(|e| MessageRepoError::StorageError(format!("run_id: {}", e)))?;

    Ok(PersistedMessage {
        message_id: db_get_column(row, "message_id")
            .map_err(|e| MessageRepoError::StorageError(format!("message_id: {}", e)))?,
        group_id: db_get_column(row, "group_id")
            .map_err(|e| MessageRepoError::StorageError(format!("group_id: {}", e)))?,
        session_id: db_get_column(row, "session_id")
            .map_err(|e| MessageRepoError::StorageError(format!("session_id: {}", e)))?,
        session_seq: db_get_column(row, "session_seq")
            .map_err(|e| MessageRepoError::StorageError(format!("session_seq: {}", e)))?,
        sender_id: db_get_column(row, "sender_id")
            .map_err(|e| MessageRepoError::StorageError(format!("sender_id: {}", e)))?,
        sender_type,
        message_type: db_get_column(row, "message_type")
            .map_err(|e| MessageRepoError::StorageError(format!("message_type: {}", e)))?,
        content,
        client_msg_id,
        owner_bot_id,
        visibility_domain,
        audience,
        status,
        created_at: created_at_i64 as u64,
        run_id,
    })
}
