use super::*;

pub(crate) async fn try_persist_group_message(
    flow: &BcsMessageFlow,
    group_id: &str,
    session_id: Option<&str>,
    sender_id: &str,
    sender_type: SenderType,
    message_type: &str,
    content: Value,
    client_msg_id: Option<&str>,
    owner_bot_id: Option<String>,
    run_id: &str,
) -> ServiceResult<Option<bcs_domain::PersistedMessage>> {
    let Some(ref repo) = flow.message_repo else {
        return Ok(None);
    };
    let created_at = now_ms();
    let group = flow.group.try_get(group_id).await?;
    // When the group cannot be resolved, default the strategy to `ManagerWorker`
    // rather than `Chat`: `Chat` maps to `MessageVisibilityDomain::Chat`, which is
    // always visible to Participant-scoped Humans regardless of the real audience.
    // `ManagerWorker` keeps the message hidden from Participant views unless the
    // audience computation below explicitly grants visibility, matching the same
    // fail-closed default used by `frontend_domain_for_group` and
    // `publish_web_user_message` for the identical "group lookup missed" case.
    let group_strategy = group
        .as_ref()
        .map(|group| group.group_strategy)
        .unwrap_or(GroupStrategy::ManagerWorker);
    let strategy_supports_message_event = flow.event_record_factory.is_some()
        && message_type == "chat"
        && matches!(
            group_strategy,
            GroupStrategy::Chat | GroupStrategy::ManagerWorker
        );
    let effective_session_id = if strategy_supports_message_event || message_type == bcs_domain::CHAT_ERROR_MESSAGE_TYPE {
        match session_id.filter(|session_id| !session_id.is_empty()) {
            Some(session_id) => session_id.to_string(),
            None => {
                let session_management = flow.session_management.as_ref().ok_or_else(|| {
                    ServiceError::InternalError(
                        "Eventing message persistence requires session management".to_string(),
                    )
                })?;
                session_management
                    .list_by_group(
                        group_id,
                        Some(SessionStatus::Running),
                        0,
                        1,
                        None,
                        None,
                    )
                    .await
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?
                    .into_iter()
                    .next()
                    .map(|session| session.id)
                    .ok_or_else(|| ServiceError::InvalidOperation {
                        message: format!(
                            "cannot persist message.created for Group '{group_id}' without a running Session"
                        ),
                        request_id: None,
                    })?
            }
        }
    } else {
        session_id.unwrap_or_default().to_string()
    };
    let visibility_type = if message_type == bcs_domain::CHAT_ERROR_MESSAGE_TYPE { "chat" } else { message_type };
    let (visibility_domain, audience) = persisted_message_visibility(group.as_ref(), sender_id, sender_type, visibility_type, owner_bot_id.as_deref())?;
    let msg = NewMessage {
        group_id: group_id.to_string(),
        session_id: effective_session_id.clone(),
        sender_id: sender_id.to_string(),
        sender_type,
        message_type: message_type.to_string(),
        content,
        client_msg_id: client_msg_id.map(str::to_string),
        owner_bot_id,
        created_at,
        run_id: run_id.to_string(),
        visibility_domain,
        audience,
    };
    let persisted = if let Some(factory) = flow.event_record_factory.as_ref() {
        if strategy_supports_message_event {
            let session_id = effective_session_id.as_str();
            let message_id = uuid::Uuid::new_v4().to_string();
            let content_size = serde_json::to_vec(&msg.content)
                .map_err(|error| ServiceError::InternalError(error.to_string()))?
                .len();
            let sender_type_name = match msg.sender_type {
                SenderType::Bot => "bot",
                SenderType::Human => "human",
                SenderType::System => "system",
            };
            let mut data = BTreeMap::from([
                (
                    "logical_message_id".to_string(),
                    serde_json::json!(message_id.clone()),
                ),
                ("message_type".to_string(), serde_json::json!(message_type)),
                (
                    "sender".to_string(),
                    serde_json::json!({"id": sender_id, "type": sender_type_name}),
                ),
                (
                    "content".to_string(),
                    serde_json::json!({
                        "content_type": "application/json",
                        "size_bytes": content_size,
                        "json": msg.content.clone(),
                        "truncated": false
                    }),
                ),
                ("attachments".to_string(), serde_json::json!([])),
            ]);
            if !run_id.is_empty() {
                data.insert("run_id".to_string(), serde_json::json!(run_id));
            }
            let occurred_at = Utc
                .timestamp_millis_opt(i64::try_from(created_at).map_err(|_| {
                    ServiceError::InternalError("message timestamp is out of range".to_string())
                })?)
                .single()
                .ok_or_else(|| {
                    ServiceError::InternalError("message timestamp is invalid".to_string())
                })?
                .to_rfc3339_opts(SecondsFormat::Millis, true);
            let event = factory
                .prepare(NewEvent {
                    event_id: format!("evt_{}", uuid::Uuid::new_v4()),
                    event_type: "message.created".to_string(),
                    schema_version: EVENT_SCHEMA_VERSION_V1.to_string(),
                    producer: "bcs-message-flow".to_string(),
                    producer_key: format!("message.created:{message_id}"),
                    occurred_at,
                    subject: EventSubject {
                        subject_type: "message".to_string(),
                        id: message_id.clone(),
                    },
                    scope: EventScope {
                        group_id: Some(group_id.to_string()),
                        session_id: Some(session_id.to_string()),
                        ..EventScope::default()
                    },
                    stream_key: format!("session:{session_id}"),
                    actor: Some(EventActor {
                        actor_type: match msg.sender_type {
                            SenderType::Bot => EventActorType::Bot,
                            SenderType::Human => EventActorType::Human,
                            SenderType::System => EventActorType::System,
                        },
                        id: sender_id.to_string(),
                        display_name: None,
                    }),
                    correlation_id: if run_id.is_empty() {
                        None
                    } else {
                        Some(run_id.to_string())
                    },
                    causation_event_id: None,
                    trace_id: None,
                    data,
                })
                .map_err(|error| ServiceError::InternalError(error.to_string()))?;
            match event {
                Some(event) => {
                    repo.append_message_with_event(AppendMessageWithEvent {
                        message_id,
                        message: msg,
                        event,
                    })
                    .await
                }
                None => repo.append_message(msg).await,
            }
        } else {
            repo.append_message(msg).await
        }
    } else {
        repo.append_message(msg).await
    }
    .map_err(|error| {
        if message_type == bcs_domain::CHAT_ERROR_MESSAGE_TYPE {
            tracing::warn!(group_id, session_id = %effective_session_id, run_id, bot_id = sender_id,
                error_code = "chat_error_persistence_failed", "terminal error projection was not persisted");
        }
        ServiceError::InternalError(format!(
            "failed to persist group message for Group '{group_id}': {error}"
        ))
    })?;
    info!(
        group_id = %group_id,
        sender_id = %sender_id,
        message_type,
        message_id = %persisted.message_id,
        "group message persisted"
    );
    Ok(Some(persisted))
}

pub(crate) fn persisted_message_visibility(group: Option<&Group>, sender_id: &str, sender_type: SenderType, message_type: &str, owner_bot_id: Option<&str>) -> ServiceResult<(MessageVisibilityDomain, Option<MessageAudience>)> {
    let visibility_domain = match group.map(|g| g.group_strategy).unwrap_or(GroupStrategy::ManagerWorker) {
        GroupStrategy::Chat => MessageVisibilityDomain::Chat,
        GroupStrategy::ManagerWorker => MessageVisibilityDomain::ManagerWorker,
        GroupStrategy::StateMachine => MessageVisibilityDomain::StateMachine,
    };
    let audience = match visibility_domain {
        MessageVisibilityDomain::Chat => None,
        MessageVisibilityDomain::ManagerWorker => Some(
            manager_worker_message_audience(
                group,
                sender_id,
                sender_type,
                message_type,
                owner_bot_id,
            )
            .map_err(|error| ServiceError::InternalError(error.to_string()))?,
        ),
        MessageVisibilityDomain::StateMachine => Some(
            if sender_type == SenderType::Human {
                MessageAudience::directed([sender_id.to_string()])
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?
            } else if message_type == "chat"
                && group
                    .and_then(|group| group.get_participant(sender_id))
                    .is_some_and(|participant| participant.role == ParticipantRole::Manager)
            {
                MessageAudience::Public
            } else {
                MessageAudience::FullOnly
            },
        ),
    };
    Ok((visibility_domain, audience))
}

fn manager_worker_message_audience(
    group: Option<&Group>,
    sender_id: &str,
    sender_type: SenderType,
    message_type: &str,
    owner_actor_id: Option<&str>,
) -> Result<MessageAudience, &'static str> {
    if sender_type == SenderType::Human {
        return MessageAudience::directed([sender_id.to_string()]);
    }
    if let Some(owner_actor_id) = owner_actor_id {
        return MessageAudience::directed([sender_id.to_string(), owner_actor_id.to_string()]);
    }
    if sender_type == SenderType::Bot
        && message_type == "chat"
        && group
            .and_then(|group| group.get_participant(sender_id))
            .is_some_and(|participant| participant.role == ParticipantRole::Manager)
    {
        return Ok(MessageAudience::Public);
    }
    Ok(MessageAudience::FullOnly)
}

/// Persist the sender's original text verbatim so human-facing history keeps
/// `@mention` markers visible (mention tokens are only stripped from bot-bound
/// deliveries, via `RoutingDecision::cleaned_message`). Non-empty `mentions`
/// ride along so frontends can render mention chips after a history reload.
pub(super) fn persisted_inbound_content(
    content: &str,
    attachments: Option<&[Attachment]>,
    mentions: &[String],
) -> Value {
    let attachments = attachments.filter(|items| !items.is_empty());
    if attachments.is_none() && mentions.is_empty() {
        return Value::String(content.to_string());
    }
    let mut stored = serde_json::json!({ "text": content });
    if let Some(attachments) = attachments {
        stored["attachments"] = attachments
            .iter()
            .map(Attachment::stable_metadata)
            .collect::<Vec<_>>()
            .into();
    }
    if !mentions.is_empty() {
        stored["mentions"] = mentions
            .iter()
            .map(|mention| Value::String(mention.clone()))
            .collect::<Vec<_>>()
            .into();
    }
    stored
}

#[cfg(test)]
mod attachment_persistence_tests {
    use bcs_domain::{Attachment, AttachmentType};

    use super::persisted_inbound_content;

    #[test]
    fn temporary_attachment_url_is_not_persisted_in_message_history() {
        let attachment = Attachment {
            attachment_id: "att-1".to_string(),
            attachment_type: AttachmentType::Image,
            file_name: "image".to_string(),
            mime_type: None,
            size: None,
            sha256: None,
            url: "https://download.example.com/image?token=temporary".to_string(),
            expires_at: None,
        };

        let persisted = persisted_inbound_content("look", Some(&[attachment]), &[]);

        assert_eq!(persisted["text"], "look");
        assert_eq!(persisted["attachments"][0]["attachment_id"], "att-1");
        assert!(persisted["attachments"][0].get("url").is_none());
        assert!(!persisted.to_string().contains("token=temporary"));
    }

    #[test]
    fn mention_text_is_persisted_verbatim_with_structured_mentions() {
        let persisted =
            persisted_inbound_content("@Driver please review", None, &["bot-driver".to_string()]);

        assert_eq!(persisted["text"], "@Driver please review");
        assert_eq!(persisted["mentions"][0], "bot-driver");
        assert!(persisted.get("attachments").is_none());
    }

    #[test]
    fn plain_text_without_attachments_or_mentions_stays_a_string() {
        let persisted = persisted_inbound_content("plain chat", None, &[]);

        assert_eq!(
            persisted,
            serde_json::Value::String("plain chat".to_string())
        );
    }
}

#[cfg(test)]
mod staged_construction_tests {
    use std::sync::Arc;

    use bcs_test_support::{
        NoopBotDeliveryPort, NoopBotRegistryCoreService, NoopFrontendDeliveryPort,
        NoopGroupCoreService, NoopRoutingCoreService,
    };

    use super::BcsMessageFlow;

    fn message_flow() -> BcsMessageFlow {
        BcsMessageFlow::new(
            Arc::new(NoopGroupCoreService),
            Arc::new(NoopRoutingCoreService),
            Arc::new(NoopBotRegistryCoreService),
            Arc::new(NoopBotDeliveryPort),
            Arc::new(NoopFrontendDeliveryPort),
        )
    }

    #[test]
    fn pending_reader_requires_run_context_before_finalize() {
        let missing = message_flow();
        assert!(missing.pending_message_port().is_err());

        let configured = message_flow()
            .with_bot_run_context(Arc::new(crate::run_context::MemoryBotRunContextStore::new()));
        assert!(configured.pending_message_port().is_ok());
    }
}
