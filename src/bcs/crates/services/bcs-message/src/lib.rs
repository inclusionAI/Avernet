//! Message history application service.
//!
//! Implements `GroupMessageHistoryService` with cutoff-based routing:
//! new groups (created_at >= cutoff) → `MessageRepoPort`, old groups → fallback.

use std::sync::Arc;

use async_trait::async_trait;
use tracing::info;

use bcs_domain::{
    BCS_STATE_MACHINE_MESSAGE_SENDER_NAME, MessageOwnerFilter, MessageQuery,
    STATE_MACHINE_PANEL_MESSAGE_TYPE, Session,
};
use bcs_service_api::{
    BotRegistryCoreService, GroupCoreService, GroupHistoryCommand, GroupHistoryResult,
    GroupMessageHistoryService, GroupUseCaseError, SessionHistoryCommand, SessionHistoryResult,
    Group, GroupMessage, GroupMessageType, GroupStrategy, MessageRole, ParticipantRole,
    port::repo::{MessageRepoPort, SessionRepoPort},
    ServiceError,
};

/// Application service implementing [`GroupMessageHistoryService`].
///
/// Routes between new-group persistence (MessageRepoPort) and old-group
/// fallback based on group.created_at >= cutoff.
pub struct MessageService {
    message_repo: Arc<dyn MessageRepoPort>,
    fallback: Arc<dyn GroupMessageHistoryService>,
    session_repo: Arc<dyn SessionRepoPort>,
    group: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    cutoff_timestamp: u64,
    manager_worker_cutoff_timestamp: u64,
    new_participant_visible_limit: u64,
    default_page_limit: u32,
    max_page_limit: u32,
}

enum ManagerWorkerHistoryView {
    Public,
    Worker(String),
}

impl MessageService {
    pub fn new(
        message_repo: Arc<dyn MessageRepoPort>,
        fallback: Arc<dyn GroupMessageHistoryService>,
        session_repo: Arc<dyn SessionRepoPort>,
        group: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        cutoff_timestamp: u64,
        manager_worker_cutoff_timestamp: u64,
        new_participant_visible_limit: u64,
        default_page_limit: u32,
        max_page_limit: u32,
    ) -> Self {
        Self {
            message_repo,
            fallback,
            session_repo,
            group,
            registry,
            cutoff_timestamp,
            manager_worker_cutoff_timestamp,
            new_participant_visible_limit,
            default_page_limit,
            max_page_limit,
        }
    }

    /// Chat and ManagerWorker use independent cutoffs for the new message store path.
    /// Uses session.created_at when available, otherwise group.created_at.
    fn should_use_new_path(&self, group: &Group, session: Option<&Session>) -> bool {
        let created_at = session.map_or(group.created_at, |s| s.created_at);
        match group.group_strategy {
            GroupStrategy::Chat => created_at >= self.cutoff_timestamp,
            GroupStrategy::ManagerWorker => created_at >= self.manager_worker_cutoff_timestamp,
            _ => false,
        }
    }

    /// Compute the effective page limit: if caller passes 0, use default;
    /// otherwise clamp to max_page_limit.
    fn effective_limit(&self, raw: u64) -> u32 {
        if raw == 0 {
            self.default_page_limit
        } else {
            (raw as u32).min(self.max_page_limit)
        }
    }

    /// Compute `visible_from_seq` for a new participant.
    ///
    /// Spec §5.2: `visible_from = MAX(1, base_seq - N + 1)`, where:
    /// - If the participant has a recorded join_seq, base_seq = join_seq.
    /// - Otherwise (NULL join_seq), base_seq = current_msg_seq.
    /// - N = new_participant_visible_limit.
    fn compute_visible_from_seq(
        &self,
        participant_join_seq: Option<&serde_json::Value>,
        current_msg_seq: i64,
        view_bot_id: &str,
    ) -> Option<i64> {
        let n = self.new_participant_visible_limit as i64;
        let join_seq = participant_join_seq
            .and_then(|jm| jm.get(view_bot_id))
            .and_then(|v: &serde_json::Value| v.as_i64());
        let base_seq = match join_seq {
            Some(seq) => seq,
            None => {
                if current_msg_seq > 0 {
                    current_msg_seq
                } else {
                    return None;
                }
            }
        };
        Some((base_seq - n + 1).max(1))
    }

    fn manager_worker_history_view(
        &self,
        group: &Group,
        session: &Session,
        view_bot_id: Option<&str>,
    ) -> Result<ManagerWorkerHistoryView, GroupUseCaseError> {
        let Some(view_bot_id) = view_bot_id else {
            return Ok(ManagerWorkerHistoryView::Public);
        };
        if view_bot_id.starts_with("human_") {
            return Ok(ManagerWorkerHistoryView::Public);
        }
        let participant = session
            .participants
            .iter()
            .find(|participant| participant.bot_uuid == view_bot_id)
            .or_else(|| group.get_participant(view_bot_id));
        let Some(participant) = participant else {
            return Err(GroupUseCaseError::Service(ServiceError::InvalidOperation {
                message: format!(
                    "view_bot_id '{}' is not a participant in group '{}'",
                    view_bot_id, group.id
                ),
                request_id: None,
            }));
        };
        if participant.is_bot() && participant.role == ParticipantRole::Worker {
            Ok(ManagerWorkerHistoryView::Worker(view_bot_id.to_string()))
        } else {
            Ok(ManagerWorkerHistoryView::Public)
        }
    }
}

fn build_tool_call_metadata(content: &serde_json::Value) -> Option<serde_json::Value> {
    let obj = match content.as_object() {
        Some(o) => o,
        None => return None,
    };
    Some(serde_json::json!({
        "tool_call_id": obj.get("tool_call_id").cloned().unwrap_or(serde_json::Value::Null),
        "tool_name": obj.get("name").cloned().unwrap_or(serde_json::Value::Null),
        "arguments": obj.get("args").cloned().unwrap_or(serde_json::Value::Null),
        "is_error": obj.get("is_error").unwrap_or(&serde_json::Value::Bool(false)),
        "result": extract_tool_result_text(obj.get("result").unwrap_or(&serde_json::Value::Null)),
    }))
}

fn extract_tool_result_text(result: &serde_json::Value) -> String {
    if let Some(content) = result.get("content") {
        if let Some(arr) = content.as_array() {
            return arr
                .iter()
                .filter_map(|block| block.get("text").and_then(|t| t.as_str()))
                .collect::<Vec<_>>()
                .join("\n");
        }
        if let Some(text) = content.as_str() {
            return text.to_string();
        }
    }
    if let Some(text) = result.as_str() {
        return text.to_string();
    }
    result.to_string()
}

fn persisted_to_group_message(
    pm: bcs_domain::PersistedMessage,
    bot_name: Option<String>,
) -> GroupMessage {
    let is_state_machine_panel = pm.message_type == STATE_MACHINE_PANEL_MESSAGE_TYPE;
    let message_id = if is_state_machine_panel {
        pm.client_msg_id
            .clone()
            .unwrap_or_else(|| pm.message_id.clone())
    } else {
        pm.message_id.clone()
    };
    let panel_bot_name = is_state_machine_panel.then(|| {
        pm.content
            .get("bot_name")
            .and_then(serde_json::Value::as_str)
            .unwrap_or(BCS_STATE_MACHINE_MESSAGE_SENDER_NAME)
            .to_string()
    });
    let (role, metadata, content_str) = match pm.message_type.as_str() {
        "chat" | "text" | "system" => {
            let role = match pm.sender_type {
                bcs_domain::SenderType::Human => MessageRole::User,
                bcs_domain::SenderType::Bot => MessageRole::Assistant,
                bcs_domain::SenderType::System => MessageRole::System,
            };
            let text = pm.content.as_str().unwrap_or("").to_string();
            (role, None, text)
        }
        STATE_MACHINE_PANEL_MESSAGE_TYPE => {
            // TODO(sm-history-node-expansion): expand this persisted panel anchor
            // into node task/output messages after pagination and cursor semantics
            // for expanded state-machine history are defined.
            let text = pm
                .content
                .get("text")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .to_string();
            let metadata = pm.content.get("metadata").cloned();
            (MessageRole::Assistant, metadata, text)
        }
        "tool_call" => {
            let metadata = build_tool_call_metadata(&pm.content);
            let text = pm.content
                .get("result")
                .map(|r| extract_tool_result_text(r))
                .unwrap_or_else(|| pm.content.to_string());
            (MessageRole::ToolResult, metadata, text)
        }
        _ => {
            let role = match pm.sender_type {
                bcs_domain::SenderType::Human => MessageRole::User,
                bcs_domain::SenderType::Bot => MessageRole::Assistant,
                bcs_domain::SenderType::System => MessageRole::System,
            };
            let text = pm.content.as_str().unwrap_or("").to_string();
            (role, None, text)
        }
    };

    GroupMessage {
        id: message_id,
        timestamp: pm.created_at,
        sender: pm.sender_id,
        content: content_str,
        message_type: GroupMessageType::Bot,
        bot_name: panel_bot_name.or(bot_name),
        role,
        run_id: pm.run_id,
        history_meta: None,
        metadata,
    }
}

#[async_trait]
impl GroupMessageHistoryService for MessageService {
    async fn get_history(
        &self,
        cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        let group = self
            .group
            .get(&cmd.group_id)
            .await
            .ok_or_else(|| {
                GroupUseCaseError::Service(ServiceError::GroupNotFound(cmd.group_id.clone()))
            })?;

        if group.group_strategy == GroupStrategy::ManagerWorker {
            return Err(GroupUseCaseError::Service(ServiceError::InvalidOperation {
                message: "manager-worker group history requires session_id".to_string(),
                request_id: None,
            }));
        }

        if self.should_use_new_path(&group, None) {
            let limit = self.effective_limit(cmd.limit);
            info!(
                group_id = %cmd.group_id,
                limit,
                "get_history: new Chat group, querying MessageRepoPort"
            );
            let query = MessageQuery {
                group_id: cmd.group_id.clone(),
                session_id: String::new(),
                cursor: cmd.before,
                limit: self.effective_limit(cmd.limit),
                keyword: None,
                sender_id: None,
                message_type: None,
                owner_filter: MessageOwnerFilter::Any,
                time_range: None,
                visible_from_seq: None,
            };
            let page = self.message_repo.query_messages(query).await.map_err(|e| {
                GroupUseCaseError::Service(ServiceError::InternalError(format!(
                    "message repo error: {}",
                    e
                )))
            })?;
            let mut bot_names: std::collections::HashMap<String, Option<String>> =
                std::collections::HashMap::new();
            let messages: Vec<GroupMessage> = {
                let mut result = Vec::with_capacity(page.messages.len());
                for pm in page.messages {
                    let bot_name = match bot_names.entry(pm.sender_id.clone()) {
                        std::collections::hash_map::Entry::Occupied(e) => e.get().clone(),
                        std::collections::hash_map::Entry::Vacant(e) => {
                            let name = self
                                .registry
                                .get(&pm.sender_id)
                                .await
                                .and_then(|bot| bot.capabilities.name);
                            e.insert(name.clone());
                            name
                        }
                    };
                    result.push(persisted_to_group_message(pm, bot_name));
                }
                result
            };
            // The message repo only holds messages persisted by BCS. A
            // provider-backed bot keeps its own transcript, so when a specific
            // bot view is requested and the repo has nothing for it, fall back
            // to the legacy path that fetches history directly from that bot.
            if messages.is_empty() && cmd.view_bot_id.is_some() {
                return self.fallback.get_history(cmd).await;
            }
            Ok(GroupHistoryResult {
                group_id: cmd.group_id,
                messages,
                limit: cmd.limit,
                before: cmd.before,
                next_before: page.next_cursor,
            })
        } else {
            info!(
                group_id = %cmd.group_id,
                "get_history: old group, falling back to legacy path"
            );
            self.fallback.get_history(cmd).await
        }
    }

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        let session_id = cmd.session_id.clone();
        let session = self.session_repo.get(&session_id).await;

        // Chat and ManagerWorker use independent cutoffs for the new message store path.
        let group_opt = self.group.get(&cmd.group_id).await;
        let use_new_path = match group_opt.as_ref() {
            Some(group) => self.should_use_new_path(group, session.as_ref()),
            None => false,
        };

        if use_new_path {
            let sess = session.as_ref().unwrap();
            let limit = self.effective_limit(cmd.limit);
            let is_manager_worker = group_opt
                .as_ref()
                .map(|group| group.group_strategy == GroupStrategy::ManagerWorker)
                .unwrap_or(false);
            let (owner_filter, visible_from_seq) = if is_manager_worker {
                let view = self.manager_worker_history_view(
                    group_opt.as_ref().unwrap(),
                    sess,
                    cmd.view_bot_id.as_deref(),
                )?;
                let owner_filter = match view {
                    ManagerWorkerHistoryView::Public => MessageOwnerFilter::IsNull,
                    ManagerWorkerHistoryView::Worker(worker_id) => MessageOwnerFilter::Eq(worker_id),
                };
                (owner_filter, None)
            } else {
                let visible_from_seq = if let Some(ref view_bot_id) = cmd.view_bot_id {
                    self.compute_visible_from_seq(
                        sess.participant_join_seq.as_ref(),
                        sess.current_msg_seq,
                        view_bot_id,
                    )
                } else {
                    None
                };
                (MessageOwnerFilter::Any, visible_from_seq)
            };

            info!(
                session_id = %session_id,
                limit,
                visible_from_seq,
                owner_filter = ?owner_filter,
                "get_session_history: new session, querying MessageRepoPort"
            );

            let query = MessageQuery {
                group_id: cmd.group_id,
                session_id: session_id.clone(),
                cursor: cmd.before,
                limit: self.effective_limit(cmd.limit),
                keyword: None,
                sender_id: None,
                message_type: None,
                owner_filter,
                time_range: None,
                visible_from_seq,
            };
            let page = self.message_repo.query_messages(query).await.map_err(|e| {
                GroupUseCaseError::Service(ServiceError::InternalError(format!(
                    "message repo error: {}",
                    e
                )))
            })?;
            let mut bot_names: std::collections::HashMap<String, Option<String>> =
                std::collections::HashMap::new();
            let messages: Vec<GroupMessage> = {
                let mut result = Vec::with_capacity(page.messages.len());
                for pm in page.messages {
                    let bot_name = match bot_names.entry(pm.sender_id.clone()) {
                        std::collections::hash_map::Entry::Occupied(e) => e.get().clone(),
                        std::collections::hash_map::Entry::Vacant(e) => {
                            let name = self
                                .registry
                                .get(&pm.sender_id)
                                .await
                                .and_then(|bot| bot.capabilities.name);
                            e.insert(name.clone());
                            name
                        }
                    };
                    result.push(persisted_to_group_message(pm, bot_name));
                }
                result
            };
            Ok(SessionHistoryResult {
                session_id,
                messages,
                limit: cmd.limit,
                before: cmd.before,
                next_before: page.next_cursor,
            })
        } else {
            info!(
                session_id = %cmd.session_id,
                "get_session_history: old session, merging legacy history with persisted panel anchors"
            );
            let mut fallback_result = self.fallback.get_session_history(cmd.clone()).await?;
            let (Some(group), Some(session)) = (group_opt.as_ref(), session.as_ref()) else {
                return Ok(fallback_result);
            };
            let owner_filter = match group.group_strategy {
                GroupStrategy::Chat => MessageOwnerFilter::Any,
                GroupStrategy::ManagerWorker => {
                    let Ok(view) = self.manager_worker_history_view(
                        group,
                        session,
                        cmd.view_bot_id.as_deref(),
                    ) else {
                        return Ok(fallback_result);
                    };
                    match view {
                        ManagerWorkerHistoryView::Public => MessageOwnerFilter::IsNull,
                        ManagerWorkerHistoryView::Worker(worker_id) => {
                            MessageOwnerFilter::Eq(worker_id)
                        }
                    }
                }
                _ => return Ok(fallback_result),
            };
            let limit = self.effective_limit(cmd.limit);
            let panel_page = self
                .message_repo
                .query_messages(MessageQuery {
                    group_id: cmd.group_id,
                    session_id: session_id.clone(),
                    cursor: cmd.before,
                    limit,
                    keyword: None,
                    sender_id: None,
                    message_type: Some(STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string()),
                    owner_filter,
                    time_range: None,
                    visible_from_seq: None,
                })
                .await
                .map_err(|error| {
                    GroupUseCaseError::Service(ServiceError::InternalError(format!(
                        "message repo panel-anchor error: {error}"
                    )))
                })?;
            if panel_page.messages.is_empty() {
                return Ok(fallback_result);
            }

            let source_has_more =
                fallback_result.next_before.is_some() || panel_page.has_more;
            let mut seen_ids = fallback_result
                .messages
                .iter()
                .map(|message| message.id.clone())
                .collect::<std::collections::HashSet<_>>();
            fallback_result.messages.extend(
                panel_page
                    .messages
                    .into_iter()
                    .map(|message| persisted_to_group_message(message, None))
                    .filter(|message| seen_ids.insert(message.id.clone())),
            );
            fallback_result.messages.sort_by(|left, right| {
                right
                    .timestamp
                    .cmp(&left.timestamp)
                    .then_with(|| right.id.cmp(&left.id))
            });
            let combined_has_more = fallback_result.messages.len() > limit as usize;
            fallback_result.messages.truncate(limit as usize);
            fallback_result.next_before = if source_has_more || combined_has_more {
                fallback_result
                    .messages
                    .last()
                    .map(|message| message.timestamp)
            } else {
                None
            };
            Ok(fallback_result)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use bcs_bot::BotCore;
    use bcs_domain::{MessagePage, NewMessage, PersistedMessage, SenderType};
    use bcs_group::GroupCore;
    use bcs_message_store::MemoryMessageRepo;
    use bcs_service_api::{
        CallerContext, Group, MessageRole, Participant, ParticipantRole, SessionKind,
        port::repo::{MessageRepoError, NewSessionParams, SessionRepoPort},
    };
    use bcs_session_store::MemorySessionRepo;
    use tokio::sync::Mutex;

    struct FallbackHistory {
        messages: Mutex<Vec<GroupMessage>>,
        group_calls: Mutex<usize>,
        session_calls: Mutex<usize>,
    }

    struct CountingMessageRepo {
        inner: Arc<MemoryMessageRepo>,
        query_calls: Mutex<usize>,
    }

    impl CountingMessageRepo {
        fn new(inner: Arc<MemoryMessageRepo>) -> Self {
            Self {
                inner,
                query_calls: Mutex::new(0),
            }
        }

        async fn query_calls(&self) -> usize {
            *self.query_calls.lock().await
        }
    }

    #[async_trait]
    impl MessageRepoPort for CountingMessageRepo {
        async fn append_message(
            &self,
            msg: NewMessage,
        ) -> Result<PersistedMessage, MessageRepoError> {
            self.inner.append_message(msg).await
        }

        async fn query_messages(
            &self,
            query: MessageQuery,
        ) -> Result<MessagePage, MessageRepoError> {
            *self.query_calls.lock().await += 1;
            self.inner.query_messages(query).await
        }

        async fn get_message_by_id(
            &self,
            session_id: &str,
            message_id: &str,
        ) -> Result<Option<PersistedMessage>, MessageRepoError> {
            self.inner.get_message_by_id(session_id, message_id).await
        }

        async fn get_current_seq(&self, session_id: &str) -> Result<i64, MessageRepoError> {
            self.inner.get_current_seq(session_id).await
        }
    }

    impl FallbackHistory {
        fn new(messages: Vec<GroupMessage>) -> Self {
            Self {
                messages: Mutex::new(messages),
                group_calls: Mutex::new(0),
                session_calls: Mutex::new(0),
            }
        }

        async fn group_calls(&self) -> usize {
            *self.group_calls.lock().await
        }

        async fn session_calls(&self) -> usize {
            *self.session_calls.lock().await
        }
    }

    #[async_trait]
    impl GroupMessageHistoryService for FallbackHistory {
        async fn get_history(
            &self,
            cmd: GroupHistoryCommand,
        ) -> Result<GroupHistoryResult, GroupUseCaseError> {
            *self.group_calls.lock().await += 1;
            Ok(GroupHistoryResult {
                group_id: cmd.group_id,
                messages: self.messages.lock().await.clone(),
                limit: cmd.limit,
                before: cmd.before,
                next_before: None,
            })
        }

        async fn get_session_history(
            &self,
            cmd: SessionHistoryCommand,
        ) -> Result<SessionHistoryResult, GroupUseCaseError> {
            *self.session_calls.lock().await += 1;
            Ok(SessionHistoryResult {
                session_id: cmd.session_id,
                messages: self.messages.lock().await.clone(),
                limit: cmd.limit,
                before: cmd.before,
                next_before: None,
            })
        }
    }

    fn fallback_message(content: &str) -> GroupMessage {
        GroupMessage {
            id: "fallback-msg".to_string(),
            timestamp: 1,
            sender: "legacy-bot".to_string(),
            content: content.to_string(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::Assistant,
            run_id: String::new(),
            history_meta: None,
            metadata: None,
        }
    }

    fn session_cmd(group_id: &str, session_id: &str, view_bot_id: Option<&str>) -> SessionHistoryCommand {
        SessionHistoryCommand {
            caller: CallerContext::Public,
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            session_participants: Vec::new(),
            view_bot_id: view_bot_id.map(str::to_string),
            limit: 50,
            before: None,
        }
    }

    fn group_cmd(group_id: &str, view_bot_id: Option<&str>) -> GroupHistoryCommand {
        GroupHistoryCommand {
            caller: CallerContext::Public,
            group_id: group_id.to_string(),
            view_bot_id: view_bot_id.map(str::to_string),
            limit: 50,
            before: None,
        }
    }

    async fn service_fixture(
        strategy: GroupStrategy,
        chat_cutoff: u64,
        manager_worker_cutoff: u64,
        fallback_messages: Vec<GroupMessage>,
    ) -> (
        MessageService,
        Arc<MemoryMessageRepo>,
        Arc<MemorySessionRepo>,
        Arc<FallbackHistory>,
        String,
    ) {
        let group_id = "group-1".to_string();
        let session_id = "group-1:abcdef12".to_string();
        let group = Arc::new(GroupCore::memory());
        let session_repo = Arc::new(MemorySessionRepo::new());
        let message_repo = Arc::new(MemoryMessageRepo::new());
        let fallback = Arc::new(FallbackHistory::new(fallback_messages));

        let mut domain_group = Group::new(
            group_id.clone(),
            "mgr",
            vec![
                Participant::bot("mgr", ParticipantRole::Manager),
                Participant::bot("worker-a", ParticipantRole::Worker),
                Participant::bot("worker-b", ParticipantRole::Worker),
            ],
        );
        domain_group.group_strategy = strategy;
        group.upsert(domain_group).await.expect("upsert group");
        session_repo
            .create(
                &group_id,
                NewSessionParams {
                    id: Some(session_id.clone()),
                    session_kind: SessionKind::Chat,
                    participants: Vec::new(),
                    ..Default::default()
                },
            )
            .await
            .expect("create session");

        let service = MessageService::new(
            message_repo.clone(),
            fallback.clone(),
            session_repo.clone(),
            group,
            Arc::new(BotCore::memory()),
            chat_cutoff,
            manager_worker_cutoff,
            100,
            50,
            100,
        );

        (service, message_repo, session_repo, fallback, session_id)
    }

    async fn append_history(
        repo: &MemoryMessageRepo,
        group_id: &str,
        session_id: &str,
        sender_id: &str,
        content: &str,
        owner_bot_id: Option<&str>,
    ) {
        repo.append_message(NewMessage {
            group_id: group_id.to_string(),
            session_id: session_id.to_string(),
            sender_id: sender_id.to_string(),
            sender_type: SenderType::Bot,
            message_type: "chat".to_string(),
            content: serde_json::Value::String(content.to_string()),
            client_msg_id: None,
            created_at: 1,
            run_id: String::new(),
            owner_bot_id: owner_bot_id.map(str::to_string),
        })
        .await
        .expect("append history");
    }

    #[tokio::test]
    async fn chat_history_uses_chat_cutoff_and_keeps_owner_filter_disabled() {
        let (service, repo, _sessions, fallback, session_id) =
            service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
        append_history(&repo, "group-1", &session_id, "bot-a", "visible", Some("worker-a")).await;

        let result = service
            .get_session_history(session_cmd("group-1", &session_id, Some("worker-a")))
            .await
            .expect("chat history");

        assert_eq!(fallback.session_calls().await, 0);
        assert_eq!(result.messages.len(), 1);
        assert_eq!(result.messages[0].content, "visible");
    }

    #[tokio::test]
    async fn state_machine_panel_round_trips_through_chat_session_history() {
        let (service, repo, _sessions, fallback, session_id) =
            service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
        let run_id = "sm-run-1";
        let stable_message_id = format!("{run_id}:000-panel");
        let panel_content =
            "<AixUI type=\"panel\" component=\"bcsPanel.StateMachineRunView\" />";
        repo.append_message(NewMessage {
            group_id: "group-1".to_string(),
            session_id: session_id.clone(),
            sender_id: bcs_domain::BCS_STATE_MACHINE_MESSAGE_SENDER.to_string(),
            sender_type: SenderType::Bot,
            message_type: STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string(),
            content: serde_json::json!({
                "text": panel_content,
                "bot_name": BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
                "metadata": {
                    "state_machine": {
                        "event": "panel",
                        "run_id": run_id,
                        "component": "bcsPanel.StateMachineRunView",
                    }
                }
            }),
            client_msg_id: Some(stable_message_id.clone()),
            created_at: 2,
            run_id: run_id.to_string(),
            owner_bot_id: None,
        })
        .await
        .expect("append state-machine panel");

        let result = service
            .get_session_history(session_cmd("group-1", &session_id, None))
            .await
            .expect("state-machine panel history");

        assert_eq!(fallback.session_calls().await, 0);
        assert_eq!(result.messages.len(), 1);
        let panel = &result.messages[0];
        assert_eq!(panel.id, stable_message_id);
        assert_eq!(panel.content, panel_content);
        assert_eq!(
            panel.bot_name.as_deref(),
            Some(BCS_STATE_MACHINE_MESSAGE_SENDER_NAME)
        );
        assert_eq!(panel.role, MessageRole::Assistant);
        assert_eq!(panel.run_id, run_id);
        assert_eq!(
            panel
                .metadata
                .as_ref()
                .and_then(|metadata| metadata["state_machine"]["event"].as_str()),
            Some("panel")
        );
    }

    #[tokio::test]
    async fn chat_session_history_interleaves_every_state_machine_panel() {
        let (service, repo, _sessions, fallback, session_id) =
            service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;

        for (created_at, run_id) in [(2, "sm-run-1"), (4, "sm-run-2")] {
            repo.append_message(NewMessage {
                group_id: "group-1".to_string(),
                session_id: session_id.clone(),
                sender_id: bcs_domain::BCS_STATE_MACHINE_MESSAGE_SENDER.to_string(),
                sender_type: SenderType::Bot,
                message_type: STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string(),
                content: serde_json::json!({
                    "text": format!(
                        "<AixUI type=\"panel\" component=\"bcsPanel.StateMachineRunView\" params='{{\"runId\":\"{run_id}\"}}' />"
                    ),
                    "bot_name": BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
                    "metadata": {
                        "state_machine": {
                            "event": "panel",
                            "run_id": run_id,
                            "component": "bcsPanel.StateMachineRunView",
                        }
                    }
                }),
                client_msg_id: Some(format!("{run_id}:000-panel")),
                created_at,
                run_id: run_id.to_string(),
                owner_bot_id: None,
            })
            .await
            .expect("append state-machine panel");
        }
        repo.append_message(NewMessage {
            group_id: "group-1".to_string(),
            session_id: session_id.clone(),
            sender_id: "human-1".to_string(),
            sender_type: SenderType::Human,
            message_type: "chat".to_string(),
            content: serde_json::Value::String("ordinary message".to_string()),
            client_msg_id: None,
            created_at: 3,
            run_id: String::new(),
            owner_bot_id: None,
        })
        .await
        .expect("append ordinary message");

        let result = service
            .get_session_history(session_cmd("group-1", &session_id, None))
            .await
            .expect("state-machine panel history");

        assert_eq!(fallback.session_calls().await, 0);
        assert_eq!(result.messages.len(), 3);
        assert_eq!(result.messages[0].id, "sm-run-2:000-panel");
        assert_eq!(result.messages[1].content, "ordinary message");
        assert_eq!(result.messages[2].id, "sm-run-1:000-panel");
    }

    #[tokio::test]
    async fn pre_cutoff_manager_worker_falls_back_to_legacy_history() {
        let (service, _repo, _sessions, fallback, session_id) = service_fixture(
            GroupStrategy::ManagerWorker,
            0,
            u64::MAX,
            vec![fallback_message("legacy")],
        )
        .await;

        let result = service
            .get_session_history(session_cmd("group-1", &session_id, Some("worker-a")))
            .await
            .expect("manager worker fallback history");

        assert_eq!(fallback.session_calls().await, 1);
        assert_eq!(result.messages.len(), 1);
        assert_eq!(result.messages[0].content, "legacy");
    }

    #[tokio::test]
    async fn pre_cutoff_chat_history_merges_persisted_state_machine_panel_anchor() {
        let (service, repo, _sessions, fallback, session_id) = service_fixture(
            GroupStrategy::Chat,
            u64::MAX,
            u64::MAX,
            vec![fallback_message("legacy")],
        )
        .await;
        let run_id = "sm-old-session-run";
        repo.append_message(NewMessage {
            group_id: "group-1".to_string(),
            session_id: session_id.clone(),
            sender_id: bcs_domain::BCS_STATE_MACHINE_MESSAGE_SENDER.to_string(),
            sender_type: SenderType::Bot,
            message_type: STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string(),
            content: serde_json::json!({
                "text": format!(
                    "<AixUI type=\"panel\" component=\"bcsPanel.StateMachineRunView\" params='{{\"runId\":\"{run_id}\"}}' />"
                ),
                "bot_name": BCS_STATE_MACHINE_MESSAGE_SENDER_NAME,
                "metadata": {
                    "state_machine": {
                        "event": "panel",
                        "run_id": run_id,
                        "component": "bcsPanel.StateMachineRunView",
                    }
                }
            }),
            client_msg_id: Some(format!("{run_id}:000-panel")),
            created_at: 2,
            run_id: run_id.to_string(),
            owner_bot_id: None,
        })
        .await
        .expect("append state-machine panel");

        let result = service
            .get_session_history(session_cmd("group-1", &session_id, None))
            .await
            .expect("legacy history with state-machine panel");

        assert_eq!(fallback.session_calls().await, 1);
        assert_eq!(result.messages.len(), 2);
        assert_eq!(result.messages[0].id, format!("{run_id}:000-panel"));
        assert_eq!(result.messages[1].content, "legacy");
    }

    #[tokio::test]
    async fn manager_worker_group_history_is_rejected_without_fallback() {
        let (mut service, repo, _sessions, fallback, _session_id) =
            service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
        let counting_repo = Arc::new(CountingMessageRepo::new(repo));
        service.message_repo = counting_repo.clone();

        for view_bot_id in [None, Some("worker-a")] {
            let err = service
                .get_history(group_cmd("group-1", view_bot_id))
                .await
                .expect_err("manager worker group history should be rejected");

            assert!(
                matches!(
                    err,
                    GroupUseCaseError::Service(ServiceError::InvalidOperation { .. })
                ),
                "expected InvalidOperation, got {err:?}"
            );
        }

        assert_eq!(counting_repo.query_calls().await, 0);
        assert_eq!(fallback.group_calls().await, 0);
        assert_eq!(fallback.session_calls().await, 0);
    }

    #[tokio::test]
    async fn manager_worker_worker_view_filters_by_worker_owner_after_cutoff() {
        let (service, repo, _sessions, fallback, session_id) =
            service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
        append_history(&repo, "group-1", &session_id, "human_1", "public-human", None).await;
        append_history(&repo, "group-1", &session_id, "worker-a", "a-only", Some("worker-a")).await;
        append_history(&repo, "group-1", &session_id, "worker-b", "b-only", Some("worker-b")).await;

        let result = service
            .get_session_history(session_cmd("group-1", &session_id, Some("worker-a")))
            .await
            .expect("manager worker db history");

        assert_eq!(fallback.session_calls().await, 0);
        assert_eq!(result.messages.len(), 1);
        assert_eq!(result.messages[0].content, "a-only");
    }

    #[tokio::test]
    async fn manager_worker_manager_view_reads_public_rows_after_cutoff() {
        let (service, repo, _sessions, fallback, session_id) =
            service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
        append_history(&repo, "group-1", &session_id, "human_1", "public-human", None).await;
        append_history(&repo, "group-1", &session_id, "mgr", "public-manager", None).await;
        append_history(&repo, "group-1", &session_id, "worker-a", "a-only", Some("worker-a")).await;
        append_history(&repo, "group-1", &session_id, "worker-b", "b-only", Some("worker-b")).await;

        let result = service
            .get_session_history(session_cmd("group-1", &session_id, Some("mgr")))
            .await
            .expect("manager worker manager view history");

        assert_eq!(fallback.session_calls().await, 0);
        assert_eq!(result.messages.len(), 2);
        let contents: Vec<_> = result.messages.iter().map(|m| m.content.as_str()).collect();
        assert_eq!(contents, vec!["public-manager", "public-human"]);
    }

    #[tokio::test]
    async fn manager_worker_human_view_reads_public_rows_after_cutoff() {
        let (service, repo, _sessions, fallback, session_id) =
            service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
        append_history(&repo, "group-1", &session_id, "human_1", "public-human", None).await;
        append_history(&repo, "group-1", &session_id, "mgr", "public-manager", None).await;
        append_history(&repo, "group-1", &session_id, "worker-a", "a-only", Some("worker-a")).await;

        let result = service
            .get_session_history(session_cmd("group-1", &session_id, Some("human_1")))
            .await
            .expect("manager worker human view history");

        assert_eq!(fallback.session_calls().await, 0);
        assert_eq!(result.messages.len(), 2);
        let contents: Vec<_> = result.messages.iter().map(|m| m.content.as_str()).collect();
        assert_eq!(contents, vec!["public-manager", "public-human"]);
    }

    #[tokio::test]
    async fn manager_worker_unknown_view_bot_is_rejected_after_cutoff() {
        let (service, repo, _sessions, _fallback, session_id) =
            service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
        append_history(&repo, "group-1", &session_id, "human_1", "public-human", None).await;

        let err = service
            .get_session_history(session_cmd("group-1", &session_id, Some("not-a-participant")))
            .await
            .expect_err("unknown view bot should not read public history");

        assert!(
            matches!(
                err,
                GroupUseCaseError::Service(ServiceError::InvalidOperation { .. })
            ),
            "expected InvalidOperation, got {err:?}"
        );
    }

    #[tokio::test]
    async fn manager_worker_history_without_view_owner_reads_public_rows_after_cutoff() {
        let (service, repo, _sessions, fallback, session_id) =
            service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
        append_history(&repo, "group-1", &session_id, "human_1", "public-human", None).await;
        append_history(&repo, "group-1", &session_id, "worker-a", "a-only", Some("worker-a")).await;

        let result = service
            .get_session_history(session_cmd("group-1", &session_id, None))
            .await
            .expect("manager worker default public history");

        assert_eq!(fallback.session_calls().await, 0);
        assert_eq!(result.messages.len(), 1);
        assert_eq!(result.messages[0].content, "public-human");
    }
}
