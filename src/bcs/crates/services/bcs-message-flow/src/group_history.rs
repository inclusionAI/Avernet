use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use futures::stream::FuturesOrdered;
use futures::StreamExt;

use bcs_service_api::{
    ActorKind, BCS_SYSTEM_MESSAGE, BotDeliveryPort, BotRegistryCoreService,
    CallerContext, Group, GroupHistoryBotRequestPort, GroupHistoryCommand, GroupHistoryResult, GroupMessage,
    GroupMessageHistoryService, GroupMessageType, GroupCoreService, GroupUseCaseError, HumanActor,
    MessageRole, Participant, ServiceError,
    SessionHistoryCommand, SessionHistoryResult,
    backfill_bot_names,
};
use serde_json::Value;

const HISTORY_TIMEOUT_MS: u64 = 30_000;
const BOT_HISTORY_LIMIT_CAP: u64 = 1_000;
const UNBOUNDED_HISTORY_LIMIT: u64 = u64::MAX;
const OPENCLAW_NO_REPLY_TOKEN: &str = "NO_REPLY";

pub struct BcsGroupMessageHistory {
    group: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    bot_delivery: Arc<dyn BotDeliveryPort>,
    bot_request: Arc<dyn GroupHistoryBotRequestPort>,
}

impl BcsGroupMessageHistory {
    pub fn new(
        group: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        bot_delivery: Arc<dyn BotDeliveryPort>,
        bot_request: Arc<dyn GroupHistoryBotRequestPort>,
    ) -> Self {
        Self {
            group,
            registry,
            bot_delivery,
            bot_request,
        }
    }
}

#[async_trait]
impl GroupMessageHistoryService for BcsGroupMessageHistory {
    async fn get_history(
        &self,
        cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        if cmd.limit == 0 {
            return Err(GroupUseCaseError::InvalidHistoryLimit(cmd.limit));
        }

        let mut group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        let viewer_human_uuid = match &cmd.caller {
            CallerContext::Human(human) => {
                self.verify_human_group_access(&group, human).await?;
                if let Some(view_bot_id) = cmd.view_bot_id.as_deref() {
                    self.verify_view_actor_ownership(view_bot_id, human)
                        .await?;
                }
                Some(human.actor_id.as_str())
            }
            CallerContext::Bot(bot_actor) => {
                if !group.participants.iter().any(|p| p.bot_uuid == bot_actor.bot_uuid) {
                    return Err(GroupUseCaseError::Forbidden(format!(
                        "bot '{}' is not a participant in group '{}'",
                        bot_actor.bot_uuid, group.id
                    )));
                }
                None
            }
            _ => {
                return Err(GroupUseCaseError::Unauthorized(
                    "valid Human or Bot caller is required for this group message request"
                        .to_string(),
                ));
            }
        };

        backfill_bot_names(self.registry.as_ref(), &mut group).await;
        let source_bots = resolve_history_source_bots(&group, cmd.view_bot_id.as_deref())?;

        let mut stream = FuturesOrdered::new();
        let view_bot_id = cmd.view_bot_id.clone();
        for (idx, &source_bot) in source_bots.iter().enumerate() {
            let group_clone = group.clone();
            let vid = view_bot_id.clone();
            stream.push_back(async move {
                let result = self.fetch_history_from_bot(
                    &group_clone,
                    source_bot,
                    cmd.limit,
                    cmd.before,
                    vid.as_deref(),
                    viewer_human_uuid,
                ).await;
                (idx, source_bot, result)
            });
        }

        while let Some((_idx, source_bot, res)) = stream.next().await {
            match res {
                Ok(messages) => {
                    let messages = apply_window(messages, cmd.limit, cmd.before);
                    if messages.is_empty() && cmd.before.is_some() {
                        tracing::warn!(
                            group_id = %group.id,
                            requested_view_bot = ?cmd.view_bot_id,
                            selected_source_bot = %source_bot,
                            before = ?cmd.before,
                            "group_history: selected source had no messages before cursor"
                        );
                        continue;
                    }
                    let next_before = next_before_from_messages(&messages);
                    return Ok(GroupHistoryResult {
                        group_id: cmd.group_id,
                        messages,
                        limit: cmd.limit,
                        before: cmd.before,
                        next_before,
                    });
                }
                Err(reason) => {
                    tracing::warn!(
                        group_id = %group.id,
                        requested_view_bot = ?cmd.view_bot_id,
                        selected_source_bot = %source_bot,
                        fallback_reason = %reason,
                        "group_history: falling back to next history source"
                    );
                }
            }
        }
        drop(stream);

        let messages = apply_window(
            normalize_group_store_messages(&group.participants, group.messages),
            cmd.limit,
            cmd.before,
        );
        let next_before = next_before_from_messages(&messages);
        Ok(GroupHistoryResult {
            group_id: cmd.group_id,
            messages,
            limit: cmd.limit,
            before: cmd.before,
            next_before,
        })
    }

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        if cmd.limit == 0 {
            return Err(GroupUseCaseError::InvalidHistoryLimit(cmd.limit));
        }

        let mut group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| ServiceError::GroupNotFound(cmd.group_id.clone()))?;

        let viewer_human_uuid = match &cmd.caller {
            CallerContext::Human(human) => {
                self.verify_human_session_access(&group, &cmd.session_participants, human).await?;
                if let Some(view_bot_id) = cmd.view_bot_id.as_deref() {
                    self.verify_view_actor_ownership(view_bot_id, human)
                        .await?;
                }
                Some(human.actor_id.as_str())
            }
            CallerContext::Bot(bot_actor) => {
                if !group.participants.iter().any(|p| p.bot_uuid == bot_actor.bot_uuid)
                    && !cmd
                        .session_participants
                        .iter()
                        .any(|p| p.bot_uuid == bot_actor.bot_uuid)
                {
                    return Err(GroupUseCaseError::Forbidden(format!(
                        "bot '{}' is not a participant in group '{}'",
                        bot_actor.bot_uuid, cmd.group_id
                    )));
                }
                None
            }
            CallerContext::Public | CallerContext::Integration(_) | CallerContext::Admin(_) => {
                // Public/integration/admin callers: allow access without ownership verification
                None
            }
        };

        backfill_bot_names(self.registry.as_ref(), &mut group).await;
        let mut session_group = group.clone();
        session_group.participants = cmd.session_participants.clone();
        backfill_bot_names(self.registry.as_ref(), &mut session_group).await;
        let session_participants = session_group.participants;

        let source_bots = resolve_session_history_source_bots(
            &group,
            cmd.view_bot_id.as_deref(),
            &session_participants,
        )?;

        for source_bot in &source_bots {
            match self
                .fetch_session_history_from_bot(
                    &group,
                    &cmd.session_id,
                    source_bot,
                    &session_participants,
                    cmd.limit,
                    cmd.before,
                    cmd.view_bot_id.as_deref(),
                    viewer_human_uuid,
                )
                .await
            {
                Ok(messages) => {
                    let messages = apply_window(messages, cmd.limit, cmd.before);
                    if messages.is_empty() && cmd.before.is_some() {
                        continue;
                    }
                    let next_before = next_before_from_messages(&messages);
                    tracing::info!(
                        session_id = %cmd.session_id,
                        group_id = %cmd.group_id,
                        requested_view_bot = ?cmd.view_bot_id,
                        selected_source_bot = %source_bot,
                        "session_history: returning bot session history"
                    );
                    return Ok(SessionHistoryResult {
                        session_id: cmd.session_id,
                        messages,
                        limit: cmd.limit,
                        before: cmd.before,
                        next_before,
                    });
                }
                Err(reason) => {
                    tracing::warn!(
                        session_id = %cmd.session_id,
                        group_id = %cmd.group_id,
                        requested_view_bot = ?cmd.view_bot_id,
                        selected_source_bot = %source_bot,
                        fallback_reason = %reason,
                        "session_history: falling back to next history source"
                    );
                }
            }
        }

        Ok(SessionHistoryResult {
            session_id: cmd.session_id,
            messages: vec![],
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }
}

impl BcsGroupMessageHistory {
    async fn verify_human_group_access(
        &self,
        group: &Group,
        caller: &HumanActor,
    ) -> Result<(), GroupUseCaseError> {
        if group
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == caller.actor_id)
        {
            return Ok(());
        }

        for participant in group
            .participants
            .iter()
            .filter(|participant| participant.is_bot())
        {
            let Some(bot) = self.registry.get(&participant.bot_uuid).await else {
                continue;
            };
            if bot_belongs_to_staff(
                &participant.bot_uuid,
                bot.created_by.as_deref(),
                &caller.staff_no,
            ) {
                return Ok(());
            }
        }

        Err(GroupUseCaseError::Forbidden(format!(
            "current Human '{}' is not a participant and owns no Bot in group '{}'",
            caller.actor_id, group.id
        )))
    }

    async fn verify_human_session_access(
        &self,
        group: &Group,
        session_participants: &[Participant],
        caller: &HumanActor,
    ) -> Result<(), GroupUseCaseError> {
        if session_participants
            .iter()
            .any(|participant| participant.bot_uuid == caller.actor_id)
        {
            return Ok(());
        }

        // A human may own a Bot that was pulled into this session without being
        // a group participant (e.g. a driver Bot dispatching to the human's Bot).
        // Such a session is visible in the Bot tab, so reading its messages must
        // be allowed — same ownership rule as the group-level check below.
        for participant in session_participants
            .iter()
            .filter(|participant| participant.is_bot())
        {
            let Some(bot) = self.registry.get(&participant.bot_uuid).await else {
                continue;
            };
            if bot_belongs_to_staff(
                &participant.bot_uuid,
                bot.created_by.as_deref(),
                &caller.staff_no,
            ) {
                return Ok(());
            }
        }

        self.verify_human_group_access(group, caller).await
    }

    async fn verify_view_actor_ownership(
        &self,
        view_bot_id: &str,
        caller: &HumanActor,
    ) -> Result<(), GroupUseCaseError> {
        if view_bot_id == caller.actor_id {
            return Ok(());
        }

        if let Some(actor) = self.registry.get(view_bot_id).await {
            if actor.actor_kind == ActorKind::Bot
                && bot_belongs_to_staff(view_bot_id, actor.created_by.as_deref(), &caller.staff_no)
            {
                return Ok(());
            }
        }

        Err(GroupUseCaseError::Forbidden(format!(
            "view_bot_id '{}' must be the current Human '{}' or a Bot owned by them",
            view_bot_id, caller.actor_id
        )))
    }

    async fn fetch_history_from_bot(
        &self,
        group: &Group,
        source_bot: &str,
        limit: u64,
        before: Option<u64>,
        requested_view_bot: Option<&str>,
        viewer_human_uuid: Option<&str>,
    ) -> Result<Vec<GroupMessage>, String> {
        let target = self
            .registry
            .resolve_delivery_target(source_bot)
            .await
            .map_err(|error| format!("target_resolution_failed: {error}"))?;
        if !self.bot_delivery.is_available(&target).await {
            return Err("bot_target_unavailable".to_string());
        }

        let payload = self
            .bot_request
            .send_history_request(
                target,
                "chat.history",
                history_request_params(group, limit, before),
                HISTORY_TIMEOUT_MS,
            )
            .await
            .map_err(|error| format!("request_failed: {error}"))?;

        let raw_messages = payload
            .get("messages")
            .and_then(|messages| messages.as_array())
            .ok_or_else(|| "invalid_payload_missing_messages".to_string())?;
        if raw_messages.is_empty() {
            return Err("empty_messages".to_string());
        }

        Ok(convert_bot_history_messages(
            group,
            &[],
            source_bot,
            raw_messages,
            requested_view_bot,
            viewer_human_uuid,
        ))
    }

    async fn fetch_session_history_from_bot(
        &self,
        group: &Group,
        session_id: &str,
        source_bot: &str,
        session_participants: &[Participant],
        limit: u64,
        before: Option<u64>,
        requested_view_bot: Option<&str>,
        viewer_human_uuid: Option<&str>,
    ) -> Result<Vec<GroupMessage>, String> {
        let target = self
            .registry
            .resolve_delivery_target(source_bot)
            .await
            .map_err(|error| format!("target_resolution_failed: {error}"))?;
        if !self.bot_delivery.is_available(&target).await {
            return Err("bot_target_unavailable".to_string());
        }

        let is_legacy_session = session_id.ends_with(":00000000");
        let protocol_version = self.registry.get_protocol_version(source_bot).await;

        let params_list = session_history_request_params(
            session_id,
            &group.id,
            is_legacy_session,
            protocol_version,
            limit,
            before,
        );

        for params in params_list {
            let payload = match self
                .bot_request
                .send_history_request(target.clone(), "chat.history", params, HISTORY_TIMEOUT_MS)
                .await
            {
                Ok(payload) => payload,
                Err(error) => {
                    tracing::warn!(
                        group_id = %group.id,
                        session_id = %session_id,
                        source_bot = %source_bot,
                        error = %error,
                        "session_history: chat.history request failed, trying next params"
                    );
                    continue;
                }
            };

            let raw_messages = payload
                .get("messages")
                .and_then(|messages| messages.as_array())
                .ok_or_else(|| "invalid_payload_missing_messages".to_string())?;
            if raw_messages.is_empty() {
                continue;
            }

            return Ok(convert_bot_history_messages(
                group,
                session_participants,
                source_bot,
                raw_messages,
                requested_view_bot,
                viewer_human_uuid,
            ));
        }

        Err("empty_messages".to_string())
    }
}

fn history_request_params(
    group: &Group,
    limit: u64,
    before: Option<u64>,
) -> Value {
    let mut params = serde_json::Map::new();
    params.insert("session_key".to_string(), Value::String(group.id.clone()));
    params.insert("bcs_group_id".to_string(), Value::String(group.id.clone()));
    let outbound_limit = if limit == UNBOUNDED_HISTORY_LIMIT {
        BOT_HISTORY_LIMIT_CAP
    } else {
        limit.min(BOT_HISTORY_LIMIT_CAP)
    };
    params.insert(
        "limit".to_string(),
        Value::Number(serde_json::Number::from(outbound_limit)),
    );
    if let Some(before) = before {
        params.insert(
            "before".to_string(),
            Value::Number(serde_json::Number::from(before)),
        );
    }
    Value::Object(params)
}

fn resolve_history_source_bots<'a>(
    group: &'a Group,
    requested_view_bot: Option<&'a str>,
) -> Result<Vec<&'a str>, GroupUseCaseError> {
    let Some(view_bot_id) = requested_view_bot else {
        return Ok(vec![group.driver_bot.as_str()]);
    };

    let Some(participant) = group.get_participant(view_bot_id) else {
        return Err(GroupUseCaseError::Service(ServiceError::InvalidOperation {
            message: format!(
                "view_bot_id '{}' is not a participant in group '{}'",
                view_bot_id, group.id
            ),
            request_id: None,
        }));
    };

    if !participant.is_bot() {
        return Ok(vec![group.driver_bot.as_str()]);
    }

    let mut sources = vec![view_bot_id];
    if view_bot_id != group.driver_bot {
        sources.push(group.driver_bot.as_str());
    }
    Ok(sources)
}

/// Resolve which bots to query for session message history.
/// Unlike group history, session history considers session participants
/// in addition to group participants for source bot selection.
fn resolve_session_history_source_bots<'a>(
    group: &'a Group,
    requested_view_bot: Option<&'a str>,
    session_participants: &'a [Participant],
) -> Result<Vec<&'a str>, GroupUseCaseError> {
    fn push_unique<'a>(sources: &mut Vec<&'a str>, bot_id: &'a str) {
        if !sources.contains(&bot_id) {
            sources.push(bot_id);
        }
    }

    fn append_bot_sources<'a>(sources: &mut Vec<&'a str>, participants: &'a [Participant]) {
        for p in participants {
            if p.is_bot() {
                push_unique(sources, &p.bot_uuid);
            }
        }
    }

    let lead = session_participants
        .iter()
        .find(|p| p.is_bot() && p.role == group.group_strategy.lead_role())
        .or_else(|| session_participants.iter().find(|p| p.is_bot()))
        .or_else(|| {
            group
                .participants
                .iter()
                .find(|p| p.is_bot() && p.role == group.group_strategy.lead_role())
        })
        .map(|p| p.bot_uuid.as_str())
        .unwrap_or(group.driver_bot.as_str());

    let Some(view_bot_id) = requested_view_bot else {
        let mut sources = Vec::new();
        push_unique(&mut sources, lead);
        append_bot_sources(&mut sources, session_participants);
        append_bot_sources(&mut sources, &group.participants);
        return Ok(sources);
    };

    // View bot must be a participant in the group or session
    let participant = group.get_participant(view_bot_id).or_else(|| {
        session_participants
            .iter()
            .find(|p| p.bot_uuid == view_bot_id)
    });

    let Some(participant) = participant else {
        // Human viewers may not be listed as participants; fall back to all bot sources.
        if view_bot_id.starts_with("human_") {
            let mut sources = Vec::new();
            push_unique(&mut sources, lead);
            append_bot_sources(&mut sources, session_participants);
            append_bot_sources(&mut sources, &group.participants);
            return Ok(sources);
        }
        return Err(GroupUseCaseError::Service(ServiceError::InvalidOperation {
            message: format!(
                "view_bot_id '{}' is not a participant in group '{}'",
                view_bot_id, group.id
            ),
            request_id: None,
        }));
    };

    if !participant.is_bot() {
        let mut sources = Vec::new();
        push_unique(&mut sources, lead);
        append_bot_sources(&mut sources, session_participants);
        append_bot_sources(&mut sources, &group.participants);
        return Ok(sources);
    }

    let mut sources = Vec::new();
    push_unique(&mut sources, view_bot_id);
    if view_bot_id != lead {
        push_unique(&mut sources, lead);
    }
    append_bot_sources(&mut sources, session_participants);
    append_bot_sources(&mut sources, &group.participants);
    Ok(sources)
}

/// Build session-specific request params for `chat.history`.
/// Unlike group history (which always uses group_id as session_key),
/// session history uses protocol-version-aware wire keys:
/// - Legacy sessions (`{group_id}:00000000`): use `group_id` as session_key
/// - Protocol v3+: try `group_id` + `bcs_session_id` first, then fall back to `session_id`
/// - Protocol v2: use `session_id` as session_key
fn session_history_request_params(
    session_id: &str,
    group_id: &str,
    is_legacy_session: bool,
    protocol_version: u32,
    limit: u64,
    before: Option<u64>,
) -> Vec<Value> {
    let outbound_limit = if limit == UNBOUNDED_HISTORY_LIMIT {
        BOT_HISTORY_LIMIT_CAP
    } else {
        limit.min(BOT_HISTORY_LIMIT_CAP)
    };

    let build_params = |session_key: &str, bcs_session_id: Option<&str>| -> Value {
        let mut map = serde_json::Map::new();
        map.insert("session_key".to_string(), Value::String(session_key.to_string()));
        map.insert("bcs_group_id".to_string(), Value::String(group_id.to_string()));
        if let Some(sid) = bcs_session_id {
            map.insert("bcs_session_id".to_string(), Value::String(sid.to_string()));
        }
        map.insert(
            "limit".to_string(),
            Value::Number(serde_json::Number::from(outbound_limit)),
        );
        if let Some(before) = before {
            map.insert(
                "before".to_string(),
                Value::Number(serde_json::Number::from(before)),
            );
        }
        Value::Object(map)
    };

    if is_legacy_session {
        return vec![build_params(group_id, None)];
    }

    if protocol_version >= 3 {
        return vec![
            build_params(group_id, Some(session_id)),
            build_params(session_id, None),
        ];
    }

    vec![build_params(session_id, None)]
}

fn convert_bot_history_messages(
    group: &Group,
    session_participants: &[Participant],
    source_bot: &str,
    raw_messages: &[Value],
    requested_view_bot: Option<&str>,
    viewer_human_uuid: Option<&str>,
) -> Vec<GroupMessage> {
    let participants: Vec<&Participant> = group
        .participants
        .iter()
        .chain(session_participants.iter())
        .collect();
    let name_to_uuid: HashMap<String, String> = participants
        .iter()
        .filter_map(|participant| {
            participant
                .bot_name
                .as_ref()
                .map(|name| (name.clone(), participant.bot_uuid.clone()))
        })
        .collect();
    let source_bot_name = participants
        .iter()
        .find(|participant| participant.bot_uuid == source_bot)
        .and_then(|participant| participant.bot_name.clone());

    let mut expanded_parts: Vec<(
        String,
        Option<String>,
        Option<String>,
        &Value,
        u64,
        String,
    )> = Vec::new();

    for message in raw_messages {
        let Some(role_str) = message.get("role").and_then(|r| r.as_str()) else {
            continue;
        };
        let raw_content = raw_message_content(message);
        let timestamp = message
            .get("timestamp")
            .and_then(|timestamp| timestamp.as_u64())
            .unwrap_or(0);

        for (stripped_content, from_bot_name, from_bot_uuid) in handle_queued_message(&raw_content) {
            if role_str.eq_ignore_ascii_case("assistant")
                && is_openclaw_no_reply_history_content(&stripped_content)
            {
                continue;
            }
            expanded_parts.push((
                stripped_content,
                from_bot_name,
                from_bot_uuid,
                message,
                timestamp,
                role_str.to_string(),
            ));
        }
    }

    expanded_parts
        .into_iter()
        .map(|(stripped_content, from_bot_name, from_bot_uuid, message, timestamp, role_str)| {
            let is_system = from_bot_name.as_deref() == Some(BCS_SYSTEM_MESSAGE);

            let mut role = if is_system {
                MessageRole::System
            } else {
                match role_str.as_str() {
                    "assistant" => MessageRole::Assistant,
                    "tool_result" | "toolResult" => MessageRole::ToolResult,
                    _ => MessageRole::User,
                }
            };

            if !is_system && (from_bot_name.is_some() || from_bot_uuid.is_some()) {
                let is_self_message = from_bot_uuid
                    .as_ref()
                    .map(|uuid| uuid == source_bot)
                    .unwrap_or(false)
                    || from_bot_name
                    .as_ref()
                    .and_then(|name| name_to_uuid.get(name))
                    .map(|uuid| uuid == source_bot)
                    .unwrap_or(false);
                let from_uuid_resolved = from_bot_uuid.clone().or_else(|| {
                    from_bot_name
                        .as_ref()
                        .and_then(|name| name_to_uuid.get(name).cloned())
                });
                let is_viewer_message = requested_view_bot.map_or(false, |view_bot| {
                    from_uuid_resolved
                        .as_deref()
                        .map_or(false, |uuid| uuid == view_bot)
                });
                let is_viewer_human_message = viewer_human_uuid.map_or(false, |human_uuid| {
                    from_uuid_resolved
                        .as_deref()
                        .map_or(false, |uuid| uuid == human_uuid)
                });
                // Only promote to Assistant when the sender is a known bot
                // participant. Messages from non-bot entities (e.g. human
                // senders whose UUID appears in a [from:] prefix) keep their
                // original transcript role to avoid mis-ordering.
                let is_from_known_bot = from_uuid_resolved.as_ref().map_or(false, |uuid| {
                    participants.iter().any(|p| p.is_bot() && p.bot_uuid == *uuid)
                });
                if is_viewer_message || is_viewer_human_message {
                    role = MessageRole::User;
                } else if !is_self_message && is_from_known_bot {
                    role = MessageRole::Assistant;
                }
            }

            let (sender, bot_name) = if let Some(ref name) = from_bot_name {
                let uuid = from_bot_uuid
                    .clone()
                    .or_else(|| name_to_uuid.get(name).cloned())
                    .unwrap_or_else(|| "user".to_string());
                (uuid, from_bot_name.clone())
            } else if let Some(ref uuid) = from_bot_uuid {
                let name = participants
                    .iter()
                    .find(|participant| participant.bot_uuid == *uuid)
                    .and_then(|participant| participant.bot_name.clone());
                (uuid.clone(), name)
            } else {
                match role {
                    MessageRole::Assistant | MessageRole::ToolResult => {
                        (source_bot.to_string(), source_bot_name.clone())
                    }
                    MessageRole::User => ("user".to_string(), None),
                    MessageRole::System => ("system".to_string(), None),
                }
            };

            let metadata = history_metadata_for_message(role, message, &stripped_content);
            let id = message
                .get("id")
                .and_then(|id| id.as_str())
                .map(str::to_string)
                .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

            let message_type = if from_bot_name.as_deref() == Some(BCS_SYSTEM_MESSAGE) {
                GroupMessageType::System
            } else {
                GroupMessageType::Bot
            };

            GroupMessage {
                id,
                timestamp,
                sender,
                content: stripped_content,
                message_type,
                bot_name,
                role,
                run_id: String::new(),
                history_meta: message.get("historyMeta").cloned(),
                metadata,
            }
        })
        .collect()
}

fn raw_message_content(message: &Value) -> String {
    message
        .get("content")
        .and_then(|content| content.as_str())
        .map(str::to_string)
        .or_else(|| {
            message.get("content")?.as_array().map(|blocks| {
                blocks
                    .iter()
                    .filter_map(|block| block.get("text")?.as_str())
                    .collect::<Vec<_>>()
                    .join("")
            })
        })
        .unwrap_or_default()
}

fn history_metadata_for_message(
    role: MessageRole,
    message: &Value,
    stripped_content: &str,
) -> Option<Value> {
    if role == MessageRole::ToolResult {
        let mut metadata = serde_json::Map::new();
        if let Some(value) = message.get("toolName") {
            metadata.insert("tool_name".to_string(), value.clone());
        }
        if let Some(value) = message.get("toolCallId") {
            metadata.insert("tool_call_id".to_string(), value.clone());
        }
        if let Some(value) = message.get("isError") {
            metadata.insert("is_error".to_string(), value.clone());
        }
        if !stripped_content.is_empty() {
            metadata.insert(
                "result".to_string(),
                Value::String(stripped_content.to_string()),
            );
        }
        return (!metadata.is_empty()).then_some(Value::Object(metadata));
    }

    if role == MessageRole::Assistant {
        let mut metadata = serde_json::Map::new();
        if message.get("stopReason").and_then(|value| value.as_str()) == Some("toolUse") {
            metadata.insert(
                "stop_reason".to_string(),
                Value::String("toolUse".to_string()),
            );
        }
        if let Some(blocks) = message
            .get("content")
            .and_then(|content| content.as_array())
        {
            let tool_calls: Vec<Value> = blocks
                .iter()
                .filter(|block| {
                    block.get("type").and_then(|value| value.as_str()) == Some("toolCall")
                })
                .map(|block| {
                    let mut tool_call = serde_json::Map::new();
                    if let Some(value) = block.get("name") {
                        tool_call.insert("tool_name".to_string(), value.clone());
                    }
                    if let Some(value) = block.get("id") {
                        tool_call.insert("tool_call_id".to_string(), value.clone());
                    }
                    if let Some(value) = block.get("arguments") {
                        tool_call.insert("arguments".to_string(), value.clone());
                    }
                    Value::Object(tool_call)
                })
                .collect();
            if !tool_calls.is_empty() {
                metadata.insert("tool_calls".to_string(), Value::Array(tool_calls));
            }
        }
        return (!metadata.is_empty()).then_some(Value::Object(metadata));
    }

    None
}

fn is_openclaw_no_reply_history_content(content: &str) -> bool {
    let trimmed = content.trim();
    if trimmed.eq_ignore_ascii_case(OPENCLAW_NO_REPLY_TOKEN) {
        return true;
    }
    if !trimmed.starts_with('{')
        || !trimmed.ends_with('}')
        || !trimmed.contains(OPENCLAW_NO_REPLY_TOKEN)
    {
        return false;
    }
    let Ok(parsed) = serde_json::from_str::<Value>(trimmed) else {
        return false;
    };
    let Some(object) = parsed.as_object() else {
        return false;
    };
    if object.len() != 1 {
        return false;
    }
    object
        .get("action")
        .and_then(|value| value.as_str())
        .map(|action| action.trim().eq_ignore_ascii_case(OPENCLAW_NO_REPLY_TOKEN))
        .unwrap_or(false)
}

fn normalize_group_store_messages(
    participants: &[Participant],
    messages: Vec<GroupMessage>,
) -> Vec<GroupMessage> {
    let name_to_uuid: HashMap<String, String> = participants
        .iter()
        .filter_map(|p| p.bot_name.as_ref().map(|name| (name.clone(), p.bot_uuid.clone())))
        .collect();

    messages
        .into_iter()
        .flat_map(|message| {
            let parts = handle_queued_message(&message.content);
            if parts.is_empty() {
                return Vec::new();
            }
            let split = parts.len() > 1;
            parts
                .into_iter()
                .map(|(content, bot_name, bot_uuid)| {
                    let sender = bot_uuid
                        .clone()
                        .or_else(|| {
                            bot_name
                                .as_ref()
                                .and_then(|name| name_to_uuid.get(name).cloned())
                        })
                        .unwrap_or_else(|| message.sender.clone());
                    let id = if split {
                        uuid::Uuid::new_v4().to_string()
                    } else {
                        message.id.clone()
                    };
                    let message_type = if bot_name.as_deref() == Some(BCS_SYSTEM_MESSAGE) {
                        GroupMessageType::System
                    } else {
                        GroupMessageType::Bot
                    };

                    GroupMessage {
                        id,
                        timestamp: message.timestamp,
                        sender,
                        content,
                        message_type,
                        bot_name,
                        role: message.role,
                        run_id: String::new(),
                        history_meta: message.history_meta.clone(),
                        metadata: message.metadata.clone(),
                    }
                })
                .collect::<Vec<_>>()
        })
        .collect()
}

fn strip_from_prefix(content: &str) -> (String, Option<String>, Option<String>) {
    let (content, header_name, header_uuid) = strip_bcs_context_header(content);
    if let Some(rest) = content.strip_prefix("[from:") {
        if let Some(end) = rest.find(']') {
            let bot_name = rest[..end].to_string();
            let body = rest[end + 1..].to_string();
            return (body, Some(bot_name), None);
        }
    }
    (content.to_string(), header_name, header_uuid)
}

fn strip_bcs_context_header(content: &str) -> (String, Option<String>, Option<String>) {
    let trimmed = content.trim_start();
    if !trimmed.starts_with("[BCS Context]")
        && !trimmed.starts_with("[BCS Group Context]")
        && !trimmed.starts_with("[BCS Message]")
    {
        return (content.to_string(), None, None);
    }

    let (sender_name, sender_uuid) = trimmed
        .lines()
        .find_map(|line| {
            let line = line.trim();
            let rest = line
                .strip_prefix("- 消息来自:")
                .or_else(|| line.strip_prefix("- 消息来自："))?;
            let sender = rest.trim();
            if sender.is_empty() || sender == "system" {
                return None;
            }
            if let Some(paren_pos) = sender.find(')') {
                let open_pos = sender[..paren_pos].rfind('(')?;
                if open_pos > 0 {
                    let name = sender[..open_pos].to_string();
                    let uuid = sender[open_pos + 1..paren_pos].to_string();
                    return Some((Some(name), Some(uuid)));
                }
            }
            Some((None, Some(sender.to_string())))
        })
        .unwrap_or((None, None));

    if let Some(marker_pos) = trimmed.find("[消息内容]") {
        let after_marker = &trimmed[marker_pos + "[消息内容]".len()..];
        let body = after_marker.strip_prefix('\n').unwrap_or(after_marker);
        return (body.to_string(), sender_name, sender_uuid);
    }
    if let Some(pos) = trimmed.find("\n\n") {
        return (trimmed[pos + 2..].to_string(), sender_name, sender_uuid);
    }
    (String::new(), sender_name, sender_uuid)
}

fn apply_window(messages: Vec<GroupMessage>, limit: u64, before: Option<u64>) -> Vec<GroupMessage> {
    messages
        .into_iter()
        .filter(|message| before.map_or(true, |before| message.timestamp < before))
        .take(limit as usize)
        .collect()
}

fn next_before_from_messages(messages: &[GroupMessage]) -> Option<u64> {
    messages.iter().map(|message| message.timestamp).min()
}

fn bot_belongs_to_staff(bot_uuid: &str, created_by: Option<&str>, staff_no: &str) -> bool {
    if created_by == Some(staff_no) {
        return true;
    }
    if created_by.is_some() {
        return false;
    }
    bot_uuid
        .rsplit_once(':')
        .map(|(_, suffix)| suffix == staff_no)
        .unwrap_or(false)
}

fn handle_queued_message(content: &str) -> Vec<(String, Option<String>, Option<String>)> {
    let trimmed = content.trim_start();
    if !trimmed.starts_with("[Queued messages") {
        return vec![strip_from_prefix(content)];
    }

    let mut results = Vec::new();

    // Split by "\n---\nQueued #" to isolate individual queued messages.
    // The first chunk (before the first split) contains the header + optional dropped summary.
    let split_marker = "\n---\nQueued #";
    let split_point = content.find(split_marker);

    let (header_part, queued_rest) = match split_point {
        Some(pos) => {
            let after_marker = &content[pos + split_marker.len()..];
            (content[..pos].trim(), Some(format!("Queued #{}", after_marker)))
        }
        None => (content.trim(), None),
    };

    // --- Process header_part (drop [Queued messages...], extract dropped summary) ---
    let mut header_lines = header_part.lines().peekable();
    // Discard leading lines that start with "[Queued messages"
    while let Some(line) = header_lines.peek() {
        if line.trim_start().starts_with("[Queued messages") {
            header_lines.next();
        } else {
            break;
        }
    }

    let remaining_header: Vec<&str> = header_lines.collect();
    let remaining_str = remaining_header.join("\n").trim().to_string();

    if !remaining_str.is_empty() {
        // Check for [Queue overflow] Dropped N messages due to cap.
        let mut dropped_count: Option<usize> = None;
        let mut summary_start: Option<usize> = None;

        for (idx, line) in remaining_header.iter().enumerate() {
            let t = line.trim();
            if t.starts_with("[Queue overflow] Dropped ") {
                if let Some(start) = t.find("Dropped ") {
                    let after = &t[start + 8..];
                    if let Some(end) = after.find(" messages") {
                        if let Ok(n) = after[..end].parse::<usize>() {
                            dropped_count = Some(n);
                        }
                    }
                }
            }
            if t.eq_ignore_ascii_case("Summary:") {
                summary_start = Some(idx);
                break;
            }
        }

        if let Some(n) = dropped_count {
            let summary_lines: Vec<&str> = if let Some(start_idx) = summary_start {
                remaining_header.iter().skip(start_idx + 1).copied().collect()
            } else {
                // If no explicit "Summary:" line, take everything after the overflow line
                remaining_header.iter().skip_while(|l| !l.trim().starts_with("[Queue overflow]")).skip(1).copied().collect()
            };
            let dropped_body = summary_lines.join("\n");
            let content = if dropped_body.is_empty() {
                format!("Dropped {} messages:", n)
            } else {
                format!("Dropped {} messages:\n{}", n, dropped_body)
            };
            results.push((content, None, None));
        }
    }

    // --- Process queued_rest (each "Queued #N" block) ---
    if let Some(ref rest) = queued_rest {
        // Split rest by subsequent "\n---\nQueued #"
        let mut segments: Vec<&str> = rest.split("\n---\nQueued #").collect();
        if segments.is_empty() {
            segments.push(rest);
        }

        for segment in segments {
            let seg_trimmed = segment.trim_start();

            // Remove "Queued #N" prefix and trailing empty lines
            let body = if let Some(first_nl) = seg_trimmed.find('\n') {
                let after_queued_line = &seg_trimmed[first_nl..];
                // Strip 1-3 leading newlines (the empty lines after Queued #N)
                let mut body = after_queued_line;
                for _ in 0..3 {
                    if body.starts_with('\n') {
                        body = &body[1..];
                    } else {
                        break;
                    }
                }
                body
            } else {
                // No newline after Queued #N → empty segment
                ""
            };

            if body.is_empty() {
                continue;
            }

            results.push(strip_from_prefix(body));
        }
    }

    // Fallback: no Queued # markers found, but header content remains after stripping
    // [Queued messages...] prefix and no dropped summary was extracted.
    // Try splitting on bare \n---\n as a fallback delimiter.
    if queued_rest.is_none() && !remaining_str.is_empty() && results.is_empty() {
        if let Some(pos) = remaining_str.find("\n---\n") {
            let body = remaining_str[pos + 5..].trim_start();
            if !body.is_empty() {
                results.push(strip_from_prefix(body));
            }
        } else {
            results.push(strip_from_prefix(&remaining_str));
        }
    }

    results
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_handle_queued_message_standard_bundle_with_dropped() {
        let input = r#"[Queued messages while agent was busy]

[Queue overflow] Dropped 2 messages due to cap.
Summary:
- [from:国际DataBot]我是 **国际DataBot** ...
- [from:互联互通平台bot]哈哈，好嘞！...

---
Queued #1


[from:能力生产]哈哈，好的！...

---
Queued #2


[from:少灵个人助理（隐私）]热烈围观中...

---
Queued #3


[from:需求助手]哈哈，来了！..."#;

        let result = handle_queued_message(input);
        assert_eq!(result.len(), 4, "expected 1 dropped + 3 queued = 4");

        // Dropped summary (index 0)
        assert_eq!(
            result[0].0,
            "Dropped 2 messages:\n- [from:国际DataBot]我是 **国际DataBot** ...\n- [from:互联互通平台bot]哈哈，好嘞！..."
        );
        assert_eq!(result[0].1, None);
        assert_eq!(result[0].2, None);

        // Queued #1
        assert_eq!(result[1].0, "哈哈，好的！...\n");
        assert_eq!(result[1].1.as_deref(), Some("能力生产"));
        assert_eq!(result[1].2, None);

        // Queued #2
        assert_eq!(result[2].0, "热烈围观中...\n");
        assert_eq!(result[2].1.as_deref(), Some("少灵个人助理（隐私）"));

        // Queued #3
        assert_eq!(result[3].0, "哈哈，来了！...");
        assert_eq!(result[3].1.as_deref(), Some("需求助手"));
    }

    #[test]
    fn test_handle_queued_message_no_dropped() {
        let input = r#"[Queued messages while agent was busy]

---
Queued #1


[from:BotA]Hello

---
Queued #2


[from:BotB]World"#;

        let result = handle_queued_message(input);
        assert_eq!(result.len(), 2);
        assert_eq!(result[0].0, "Hello\n");
        assert_eq!(result[0].1.as_deref(), Some("BotA"));
        assert_eq!(result[1].0, "World");
        assert_eq!(result[1].1.as_deref(), Some("BotB"));
    }

    #[test]
    fn test_handle_queued_message_empty_queue() {
        let input = "[Queued messages while agent was busy]\n";
        let result = handle_queued_message(input);
        assert!(result.is_empty());
    }

    #[test]
    fn test_handle_queued_message_not_queue() {
        let input = "Normal message content";
        let result = handle_queued_message(input);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "Normal message content");
        assert_eq!(result[0].1, None);
        assert_eq!(result[0].2, None);
    }

    #[test]
    fn test_handle_queued_message_not_queue_with_from_prefix() {
        let input = "[from:BotA]Hello world";
        let result = handle_queued_message(input);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "Hello world");
        assert_eq!(result[0].1.as_deref(), Some("BotA"));
    }

    #[test]
    fn test_handle_queued_message_queued_with_double_newline_only() {
        let input = "[Queued messages while agent was busy]\n\n---\nQueued #1\n\n[from:BotA]hi\n";
        let result = handle_queued_message(input);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "hi\n");
        assert_eq!(result[0].1.as_deref(), Some("BotA"));
    }

    #[test]
    fn test_handle_queued_message_last_segment_no_trailing_newline() {
        let input = "[Queued messages while agent was busy]\n\n---\nQueued #1\n\n\n[from:BotA]bye";
        let result = handle_queued_message(input);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "bye");
        assert_eq!(result[0].1.as_deref(), Some("BotA"));
    }

    #[test]
    fn test_handle_queued_message_segment_without_queued_marker_falls_back() {
        // Bot message contains --- but no Queued # marker
        let input = "[Queued messages while agent was busy]\n\nSome description\n---\nNot a queued message\n";
        let result = handle_queued_message(input);
        // Header is dropped; the remaining part is treated as one fallback segment
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, "Not a queued message");
        assert_eq!(result[0].1, None);
    }

    // --- Session history request params tests ---

    #[test]
    fn session_history_request_params_for_v2_uses_session_id_as_wire_key() {
        let params = session_history_request_params(
            "group-1:abcdef12",
            "group-1",
            false,
            2,
            100,
            None,
        );
        assert_eq!(params.len(), 1);
        assert_eq!(params[0]["session_key"], "group-1:abcdef12");
    }

    #[test]
    fn session_history_request_params_for_v3_sends_explicit_session_and_fallback_key() {
        let params = session_history_request_params(
            "group-1:abcdef12",
            "group-1",
            false,
            3,
            100,
            None,
        );
        assert_eq!(params.len(), 2);
        assert_eq!(params[0]["session_key"], "group-1");
        assert_eq!(params[0]["bcs_session_id"], "group-1:abcdef12");
        assert_eq!(params[1]["session_key"], "group-1:abcdef12");
    }

    #[test]
    fn session_history_request_params_for_legacy_session_uses_group_id_only() {
        let params = session_history_request_params(
            "group-1:00000000",
            "group-1",
            true,
            3,
            100,
            None,
        );
        assert_eq!(params.len(), 1);
        assert_eq!(params[0]["session_key"], "group-1");
        assert!(params[0].get("bcs_session_id").is_none());
    }

    #[test]
    fn session_history_request_params_includes_limit_and_before() {
        let params = session_history_request_params(
            "group-1:abcdef12",
            "group-1",
            false,
            2,
            50,
            Some(12345),
        );
        assert_eq!(params[0]["limit"], 50);
        assert_eq!(params[0]["before"], 12345);
    }
}
