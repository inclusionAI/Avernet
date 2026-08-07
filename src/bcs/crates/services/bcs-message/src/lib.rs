//! Message history application service.
//!
//! Implements `GroupMessageHistoryService` with cutoff-based routing:
//! new groups (created_at >= cutoff) → `MessageRepoPort`, old groups → fallback.

use std::sync::Arc;

use async_trait::async_trait;
use tracing::info;

use bcs_domain::{
    BCS_STATE_MACHINE_MESSAGE_SENDER_NAME, MessageAttachment, MessageOwnerFilter, MessageQuery,
    STATE_MACHINE_PANEL_MESSAGE_TYPE, Session,
};
use bcs_service_api::{
    application::session_files::SessionFileService, BotRegistryCoreService, GroupCoreService,
    GroupHistoryCommand, GroupHistoryResult, GroupMessageHistoryService, GroupUseCaseError,
    SessionHistoryCommand, SessionHistoryResult, Group, GroupMessage, GroupMessageType,
    GroupStrategy, MessageRole, ParticipantRole, port::repo::{MessageRepoPort, SessionRepoPort},
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
    session_file: Arc<dyn SessionFileService>,
    cutoff_timestamp: u64,
    manager_worker_cutoff_timestamp: u64,
    new_participant_visible_limit: u64,
    default_page_limit: u32,
    max_page_limit: u32,
    history_attachment_ttl: u64,
}

pub enum ManagerWorkerHistoryView {
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
        session_file: Arc<dyn SessionFileService>,
        cutoff_timestamp: u64,
        manager_worker_cutoff_timestamp: u64,
        new_participant_visible_limit: u64,
        default_page_limit: u32,
        max_page_limit: u32,
        history_attachment_ttl: u64,
    ) -> Self {
        Self {
            message_repo,
            fallback,
            session_repo,
            group,
            registry,
            session_file,
            cutoff_timestamp,
            manager_worker_cutoff_timestamp,
            new_participant_visible_limit,
            default_page_limit,
            max_page_limit,
            history_attachment_ttl,
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
    pub fn compute_visible_from_seq(
        participant_join_seq: Option<&serde_json::Value>,
        current_msg_seq: i64,
        view_bot_id: &str,
        new_participant_visible_limit: u64,
    ) -> Option<i64> {
        let n = new_participant_visible_limit as i64;
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

    pub fn manager_worker_history_view(
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

    /// Compute the message-history visibility predicates for a viewer, mirroring
    /// the new-message path of `MessageService::get_session_history`. This is
    /// the single source of truth shared by the legacy group-history facade and
    /// the V1 `bcs-app-session` message-history facade so the two cannot drift.
    ///
    /// Returns:
    /// - `ManagerWorker` strategy + worker viewer → `(Eq(worker_id), None)`
    ///   (owner isolation: a worker only reads its own messages).
    /// - `ManagerWorker` strategy + non-worker bot manager viewer →
    ///   `(PublicOrOwner(view), None)`; none / human viewer →
    ///   `(IsNull, None)` (public-only, VUlai).
    /// - Chat / other strategies + bot viewer →
    ///   `(PublicOrOwner(view), visible_from_seq)`; none / human viewer →
    ///   `(IsNull, visible_from_seq)` where `visible_from_seq` is the spec
    ///   §5.2 new-participant cutoff for the viewer's `bot_uuid`, or `None`
    ///   when no viewer / no recorded messages.
    pub fn compute_session_history_query(
        group: &Group,
        session: &Session,
        view_bot_id: Option<&str>,
        new_participant_visible_limit: u64,
    ) -> Result<(MessageOwnerFilter, Option<i64>), GroupUseCaseError> {
        let is_manager_worker = group.group_strategy == GroupStrategy::ManagerWorker;
        if is_manager_worker {
            let view = Self::manager_worker_history_view(group, session, view_bot_id)?;
            let owner_filter = match view {
                ManagerWorkerHistoryView::Worker(worker_id) => MessageOwnerFilter::Eq(worker_id),
                ManagerWorkerHistoryView::Public => match view_bot_id {
                    // Non-worker bot viewer (the manager) reads public + own copies.
                    Some(v) if !v.is_empty() && !v.starts_with("human_") => {
                        MessageOwnerFilter::PublicOrOwner(v.to_string())
                    }
                    _ => MessageOwnerFilter::IsNull,
                },
            };
            Ok((owner_filter, None))
        } else {
            let visible_from_seq = match view_bot_id {
                Some(view_bot_id) => Self::compute_visible_from_seq(
                    session.participant_join_seq.as_ref(),
                    session.current_msg_seq,
                    view_bot_id,
                    new_participant_visible_limit,
                ),
                None => None,
            };
            Ok((Self::chat_owner_filter_for_view(view_bot_id), visible_from_seq))
        }
    }

    /// Chat/non-MW viewer → owner filter: a bot viewer reads public messages
    /// plus its own system-message copies (`PublicOrOwner`); no view_bot_id or
    /// a `human_*` viewer reads public-only (`IsNull`). Membership is NOT
    /// verified here (mirrors the existing chat-branch behavior).
    pub fn chat_owner_filter_for_view(view_bot_id: Option<&str>) -> MessageOwnerFilter {
        match view_bot_id {
            Some(v) if !v.is_empty() && !v.starts_with("human_") => {
                MessageOwnerFilter::PublicOrOwner(v.to_string())
            }
            _ => MessageOwnerFilter::IsNull,
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

/// Extract display text + attachment views (without url) from persisted content.
/// - `Value::String(s)`                         -> (s, None)
/// - `Value::Object{ text, attachments }`       -> (text, attachments mapped; url=None)
/// - other                                      -> (content.to_string(), None)
fn extract_text_and_attachments(
    content: &serde_json::Value,
) -> (String, Option<Vec<MessageAttachment>>) {
    if let Some(s) = content.as_str() {
        return (s.to_string(), None);
    }
    if let Some(obj) = content.as_object() {
        let text = obj
            .get("text")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_string();
        let attachments = obj
            .get("attachments")
            .and_then(serde_json::Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(parse_message_attachment)
                    .collect::<Vec<_>>()
            })
            .filter(|v| !v.is_empty());
        return (text, attachments);
    }
    (content.to_string(), None)
}

/// Map one stable_metadata JSON object to a `MessageAttachment` (url=None).
/// Returns None if `attachment_id` or `file_name` is missing.
fn parse_message_attachment(v: &serde_json::Value) -> Option<MessageAttachment> {
    let obj = v.as_object()?;
    Some(MessageAttachment {
        attachment_id: obj.get("attachment_id")?.as_str()?.to_string(),
        attachment_type: obj
            .get("type")
            .and_then(|t| serde_json::from_value(t.clone()).ok())
            .unwrap_or(bcs_domain::AttachmentType::Image),
        file_name: obj.get("file_name")?.as_str()?.to_string(),
        mime_type: obj.get("mime_type").and_then(|v| v.as_str()).map(String::from),
        size: obj.get("size").and_then(|v| v.as_u64()),
        sha256: obj.get("sha256").and_then(|v| v.as_str()).map(String::from),
        url: None,
        expires_at: None,
    })
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
    let (role, metadata, content_str, attachments) = match pm.message_type.as_str() {
        "chat" | "text" | "system" => {
            let role = match pm.sender_type {
                bcs_domain::SenderType::Human => MessageRole::User,
                bcs_domain::SenderType::Bot => MessageRole::Assistant,
                bcs_domain::SenderType::System => MessageRole::System,
            };
            let (text, attachments) = extract_text_and_attachments(&pm.content);
            (role, None, text, attachments)
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
            (MessageRole::Assistant, metadata, text, None)
        }
        "tool_call" => {
            let metadata = build_tool_call_metadata(&pm.content);
            let text = pm.content
                .get("result")
                .map(|r| extract_tool_result_text(r))
                .unwrap_or_else(|| pm.content.to_string());
            (MessageRole::ToolResult, metadata, text, None)
        }
        _ => {
            let role = match pm.sender_type {
                bcs_domain::SenderType::Human => MessageRole::User,
                bcs_domain::SenderType::Bot => MessageRole::Assistant,
                bcs_domain::SenderType::System => MessageRole::System,
            };
            let text = pm.content.as_str().unwrap_or("").to_string();
            (role, None, text, None)
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
        attachments,
    }
}

/// Mint a share_url into each attachment of a single message. Failures per
/// attachment (file deleted / not owned by session / storage error) leave
/// `url: None` and do NOT abort the batch or bubble up.
async fn enrich_message_attachments(
    svc: &Arc<dyn SessionFileService>,
    session_id: &str,
    ttl: u64,
    msg: &mut GroupMessage,
) {
    let Some(atts) = msg.attachments.as_mut() else {
        return;
    };
    for att in atts.iter_mut() {
        match svc.share_mint_for_history(session_id, &att.attachment_id, ttl).await {
            Ok(minted) => {
                att.url = Some(minted.share_url);
                att.expires_at = Some(minted.expires_at);
            }
            Err(_) => {
                att.url = None;
                att.expires_at = None;
            }
        }
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
            let owner_filter = Self::chat_owner_filter_for_view(cmd.view_bot_id.as_deref());
            let query = MessageQuery {
                group_id: cmd.group_id.clone(),
                session_id: String::new(),
                cursor: cmd.before,
                limit: self.effective_limit(cmd.limit),
                keyword: None,
                sender_id: None,
                message_type: None,
                owner_filter,
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
                    let session_id_for_pm = pm.session_id.clone();
                    let mut gm = persisted_to_group_message(pm, bot_name);
                    if gm.attachments.is_some() && !session_id_for_pm.is_empty() {
                        enrich_message_attachments(
                            &self.session_file,
                            &session_id_for_pm,
                            self.history_attachment_ttl,
                            &mut gm,
                        )
                        .await;
                    }
                    result.push(gm);
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
                next_before: page.next_cursor.map(|c| c.0),
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
            let (owner_filter, visible_from_seq) = Self::compute_session_history_query(
                group_opt.as_ref().unwrap(),
                sess,
                cmd.view_bot_id.as_deref(),
                self.new_participant_visible_limit,
            )?;

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
                    let session_id_for_pm = pm.session_id.clone();
                    let mut gm = persisted_to_group_message(pm, bot_name);
                    if gm.attachments.is_some() && !session_id_for_pm.is_empty() {
                        enrich_message_attachments(
                            &self.session_file,
                            &session_id_for_pm,
                            self.history_attachment_ttl,
                            &mut gm,
                        )
                        .await;
                    }
                    result.push(gm);
                }
                result
            };
            Ok(SessionHistoryResult {
                session_id,
                messages,
                limit: cmd.limit,
                before: cmd.before,
                next_before: page.next_cursor.map(|c| c.0),
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
                    let Ok(view) = Self::manager_worker_history_view(
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
    use bcs_domain::{MessagePage, NewMessage, PersistedMessage, SenderType, SystemMessageEvent};
    use bcs_group::GroupCore;
    use bcs_message_store::MemoryMessageRepo;
    use bcs_service_api::{
        application::session_files::{
            CapabilitiesView, DeleteFileCommand, DownloadRoute, PrepareUploadCommand,
            PrepareUploadResult, SessionFileService, SessionFileUseCaseError, ShareConsumeResult,
            ShareMintCommand, ShareMintResult,
        },
        CallerContext, Group, MessageRole, Participant, ParticipantRole, SessionKind,
        port::repo::{
            MessageRepoError, NewSessionParams, SessionFileListPage, SessionFileListParams,
            SessionRepoPort,
        },
    };
    use bcs_session_store::MemorySessionRepo;
    use bcs_storage_api::ByteStream;
    use tokio::sync::Mutex;

    // Minimal mock: everything errors except share_mint_for_history (configurable).
    struct MintMock {
        ok: bool,
    }

    #[async_trait]
    impl SessionFileService for MintMock {
        async fn capabilities(&self) -> CapabilitiesView {
            unimplemented!()
        }
        async fn prepare_upload(
            &self,
            _cmd: PrepareUploadCommand,
        ) -> Result<PrepareUploadResult, SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn stream_upload(
            &self,
            _session_id: &str,
            _file_id: &str,
            _part_number: Option<u16>,
            _body: ByteStream,
            _content_length: u64,
        ) -> Result<(), SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn complete_upload(
            &self,
            _session_id: &str,
            _file_id: &str,
        ) -> Result<bcs_domain::SessionFile, SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn delete_file(
            &self,
            _cmd: DeleteFileCommand,
        ) -> Result<(), SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn get(
            &self,
            _session_id: &str,
            _file_id: &str,
        ) -> Result<bcs_domain::SessionFile, SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn list(
            &self,
            _session_id: &str,
            _params: SessionFileListParams,
        ) -> Result<SessionFileListPage, SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn download_route(
            &self,
            _session_id: &str,
            _file_id: &str,
            _ttl_secs: Option<u64>,
            _show: bool,
        ) -> Result<(bcs_domain::SessionFile, DownloadRoute), SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn share_mint(
            &self,
            _cmd: ShareMintCommand,
        ) -> Result<ShareMintResult, SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn share_consume(
            &self,
            _token: &str,
        ) -> Result<ShareConsumeResult, SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn get_stream(
            &self,
            _session_id: &str,
            _file_id: &str,
        ) -> Result<(bcs_domain::SessionFile, ByteStream), SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn sweep_expired_pending(&self) -> Result<u64, SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn delete_all_for_session(
            &self,
            _session_id: &str,
        ) -> Result<u64, SessionFileUseCaseError> {
            unimplemented!()
        }
        async fn share_mint_for_history(
            &self,
            _session_id: &str,
            _file_id: &str,
            _ttl_seconds: u64,
        ) -> Result<ShareMintResult, SessionFileUseCaseError> {
            if self.ok {
                Ok(ShareMintResult {
                    share_url: "https://bcs/sessions/shared-file/content?token=x".into(),
                    share_token: "x".into(),
                    expires_at: 9999,
                })
            } else {
                Err(SessionFileUseCaseError::NotFound("nope".into()))
            }
        }
    }

    fn att_msg() -> GroupMessage {
        GroupMessage {
            id: "m".into(),
            timestamp: 1,
            sender: "s".into(),
            content: "t".into(),
            message_type: GroupMessageType::Bot,
            bot_name: None,
            role: MessageRole::User,
            run_id: String::new(),
            history_meta: None,
            metadata: None,
            attachments: Some(vec![MessageAttachment {
                attachment_id: "file_1".into(),
                attachment_type: bcs_domain::AttachmentType::Image,
                file_name: "f.png".into(),
                mime_type: None,
                size: None,
                sha256: None,
                url: None,
                expires_at: None,
            }]),
        }
    }

    #[tokio::test]
    async fn enrich_fills_url_on_success() {
        let svc: Arc<dyn SessionFileService> = Arc::new(MintMock { ok: true });
        let mut msg = att_msg();
        enrich_message_attachments(&svc, "sid", 3600, &mut msg).await;
        let att = &msg.attachments.as_ref().unwrap()[0];
        assert_eq!(att.url.as_deref(), Some("https://bcs/sessions/shared-file/content?token=x"));
        assert_eq!(att.expires_at, Some(9999));
    }

    #[tokio::test]
    async fn enrich_leaves_url_none_on_failure() {
        let svc: Arc<dyn SessionFileService> = Arc::new(MintMock { ok: false });
        let mut msg = att_msg();
        enrich_message_attachments(&svc, "sid", 3600, &mut msg).await;
        let att = &msg.attachments.as_ref().unwrap()[0];
        assert!(att.url.is_none());
        assert!(att.expires_at.is_none());
    }

    #[tokio::test]
    async fn enrich_no_op_when_no_attachments() {
        let svc: Arc<dyn SessionFileService> = Arc::new(MintMock { ok: true });
        let mut msg = att_msg();
        msg.attachments = None;
        enrich_message_attachments(&svc, "sid", 3600, &mut msg).await;
        assert!(msg.attachments.is_none());
    }

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
            attachments: None,
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
            Arc::new(MintMock { ok: true }),
            chat_cutoff,
            manager_worker_cutoff,
            100,
            50,
            100,
            3600,
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

    #[tokio::test]
    async fn chat_bot_viewer_sees_public_and_own_system_copies_not_others() {
        let (service, repo, _sessions, fallback, session_id) =
            service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
        append_history(&repo, "group-1", &session_id, "human_1", "public-human", None).await;
        append_history(&repo, "group-1", &session_id, "system", "sys-to-worker-a", Some("worker-a")).await;
        append_history(&repo, "group-1", &session_id, "system", "sys-to-worker-b", Some("worker-b")).await;

        // worker-a view: public + own system copy; NOT worker-b's copy.
        let res_a = service
            .get_session_history(session_cmd("group-1", &session_id, Some("worker-a")))
            .await
            .expect("worker-a chat history");
        let contents_a: Vec<&str> = res_a.messages.iter().map(|m| m.content.as_str()).collect();
        assert!(contents_a.contains(&"public-human"));
        assert!(contents_a.contains(&"sys-to-worker-a"));
        assert!(!contents_a.contains(&"sys-to-worker-b"),
            "other bot's system copy must be hidden under PublicOrOwner");

        // no view_bot_id: only public (IsNull).
        let res_none = service
            .get_session_history(session_cmd("group-1", &session_id, None))
            .await
            .expect("public chat history");
        let contents_none: Vec<&str> = res_none.messages.iter().map(|m| m.content.as_str()).collect();
        assert!(contents_none.contains(&"public-human"));
        assert!(!contents_none.contains(&"sys-to-worker-a"));
        assert!(!contents_none.contains(&"sys-to-worker-b"));
        let _ = fallback;
    }

    #[tokio::test]
    async fn mw_manager_viewer_sees_public_and_own_system_copies() {
        let (service, repo, _sessions, _fallback, session_id) =
            service_fixture(GroupStrategy::ManagerWorker, 0, 0, Vec::new()).await;
        append_history(&repo, "group-1", &session_id, "human_1", "public-human", None).await;
        append_history(&repo, "group-1", &session_id, "system", "sys-to-manager", Some("mgr")).await;
        append_history(&repo, "group-1", &session_id, "system", "sys-to-worker-a", Some("worker-a")).await;

        let res = service
            .get_session_history(session_cmd("group-1", &session_id, Some("mgr")))
            .await
            .expect("manager history");
        let contents: Vec<&str> = res.messages.iter().map(|m| m.content.as_str()).collect();
        assert!(contents.contains(&"public-human"));
        assert!(contents.contains(&"sys-to-manager"),
            "manager now sees own system copy under PublicOrOwner(mgr)");
        assert!(!contents.contains(&"sys-to-worker-a"));
    }

    #[tokio::test]
    async fn get_history_chat_view_bot_id_now_filters_by_public_or_owner() {
        let (service, repo, _sessions, _fallback, _session_id) =
            service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
        // get_history new-path hardcodes session_id "" (String::new()),
        // so seed with session_id "" to match the query.
        let gid = "group-1";
        append_history(&repo, gid, "", "human_1", "public-human", None).await;
        append_history(&repo, gid, "", "system", "sys-to-a", Some("worker-a")).await;
        append_history(&repo, gid, "", "system", "sys-to-b", Some("worker-b")).await;

        // Regression: view_bot_id was previously ignored (hardcoded Any).
        let res_a = service
            .get_history(group_cmd(gid, Some("worker-a")))
            .await
            .expect("worker-a group history");
        let contents_a: Vec<&str> = res_a.messages.iter().map(|m| m.content.as_str()).collect();
        assert!(contents_a.contains(&"public-human"));
        assert!(contents_a.contains(&"sys-to-a"));
        assert!(!contents_a.contains(&"sys-to-b"),
            "get_history must now honor view_bot_id (was hardcoded Any)");

        let res_none = service
            .get_history(group_cmd(gid, None))
            .await
            .expect("public group history");
        let contents_none: Vec<&str> = res_none.messages.iter().map(|m| m.content.as_str()).collect();
        assert!(contents_none.contains(&"public-human"));
        assert!(!contents_none.contains(&"sys-to-a"));
        assert!(!contents_none.contains(&"sys-to-b"));
    }

    /// End-to-end round-trip: `SystemMessageDispatcherImpl` persists system
    /// messages by `PersistMode` into a REAL `MemoryMessageRepo` — personalized
    /// copies with `owner_bot_id = Some(recipient)` and shared notices as a
    /// single public (`owner = None`) record — and
    /// `MessageService::get_session_history` with `view_bot_id=recipient`
    /// returns that recipient's own copy plus public records, hides other
    /// recipients' copies, and lets human viewers (no bot view) read the
    /// public notices. This locks the §数据流 bridge between the write side
    /// (PersistMode-driven ownership) and the query side (`PublicOrOwner`
    /// scoping).
    #[tokio::test]
    async fn system_message_dispatch_round_trips_through_message_service_view_scoping() {
        use bcs_system_message::SystemMessageDispatcherImpl;
        use bcs_system_message::producers::bot_joined::BotJoinedMessageProducer;
        use bcs_test_support::{
            NoopBotDeliveryPort, NoopBotRegistryCoreService, NoopFrontendDeliveryPort,
            NoopGroupMessageHistoryService,
        };
        use bcs_service_api::SystemMessageDispatcherService;

        let (service, repo, _sessions, _fallback, session_id) =
            service_fixture(GroupStrategy::Chat, 0, u64::MAX, Vec::new()).await;
        let group_id = "group-1";

        // A public (owner=None) anchor that must remain visible to every viewer.
        append_history(&repo, group_id, &session_id, "bot-anchor", "public-anchor", None).await;

        // Build a REAL dispatcher wired to the SAME MemoryMessageRepo. Delivery
        // ports are noops — persistence happens before delivery, so the
        // per-recipient records land in the repo regardless of delivery outcome.
        let dispatcher = SystemMessageDispatcherImpl::builder()
            .with_registry(Arc::new(NoopBotRegistryCoreService))
            .with_delivery(Arc::new(NoopBotDeliveryPort))
            .with_frontend_delivery(Arc::new(NoopFrontendDeliveryPort))
            .with_message_repo(repo.clone())
            .register(BotJoinedMessageProducer::new(Arc::new(
                NoopGroupMessageHistoryService,
            )))
            .build()
            .expect("build dispatcher");

        // BotJoined: new-bot joins a group that already has `mgr` (driver) and
        // two workers. The producer emits one context-injection message for
        // new-bot and one notification for each existing participant.
        let new_bot_id = "bot-new".to_string();
        let existing_id = "mgr".to_string();
        let participants = vec![
            Participant::bot(&existing_id, ParticipantRole::Manager),
            Participant::bot("worker-a", ParticipantRole::Worker),
            Participant::bot("worker-b", ParticipantRole::Worker),
            Participant::bot(&new_bot_id, ParticipantRole::Consultant),
        ];
        let event = SystemMessageEvent::BotJoined {
            group_id: group_id.to_string(),
            actor: Participant::bot(&new_bot_id, ParticipantRole::Consultant),
        };
        dispatcher
            .dispatch(event, &group_fixture(group_id, &existing_id), &session_id, &participants)
            .await
            .expect("dispatch succeeded");

        // Viewer = existing mgr: sees the public join notice (owner=None) +
        // the public anchor; must NOT see new-bot's context injection
        // (owner=new-bot).
        let res_existing = service
            .get_session_history(session_cmd(group_id, &session_id, Some(&existing_id)))
            .await
            .expect("existing view session history");
        let existing_contents: Vec<&str> =
            res_existing.messages.iter().map(|m| m.content.as_str()).collect();
        assert!(existing_contents.contains(&"public-anchor"),
            "public owner=None records still visible to mgr");
        assert!(existing_contents.iter().any(|c| c.contains("已加入协作群")),
            "public join notice (owner=None) is returned to mgr");
        assert_eq!(
            existing_contents.iter().filter(|c| c.contains("已加入协作群")).count(),
            1,
            "the shared notice is a single public record, not per-bot copies"
        );
        assert!(existing_contents.iter().all(|c| !c.contains("你加入了 BCS 协作群.")),
            "new-bot's context injection (owner=new-bot) is hidden from mgr");

        // Viewer = new-bot: sees its own context injection (owner=new-bot) +
        // the public anchor + the public join notice.
        let res_new = service
            .get_session_history(session_cmd(group_id, &session_id, Some(&new_bot_id)))
            .await
            .expect("new-bot view session history");
        let new_contents: Vec<&str> =
            res_new.messages.iter().map(|m| m.content.as_str()).collect();
        assert!(new_contents.contains(&"public-anchor"),
            "public owner=None records still visible to new-bot");
        assert!(new_contents.iter().any(|c| c.contains("你加入了 BCS 协作群.")),
            "new-bot's own context injection (owner=new-bot) is returned");
        assert!(new_contents.iter().any(|c| c.contains("已加入协作群")),
            "public join notice (owner=None) is returned to new-bot");

        // Viewer = human (no bot view): sees the public anchor + the public
        // join notice; must NOT see any per-bot owned copy. This is the
        // regression guard for system messages vanishing from human history.
        let res_human = service
            .get_session_history(session_cmd(group_id, &session_id, None))
            .await
            .expect("human view session history");
        let human_contents: Vec<&str> =
            res_human.messages.iter().map(|m| m.content.as_str()).collect();
        assert!(human_contents.contains(&"public-anchor"),
            "public owner=None records visible to human viewers");
        assert!(human_contents.iter().any(|c| c.contains("已加入协作群")),
            "public join notice is visible to human viewers");
        assert!(human_contents.iter().all(|c| !c.contains("你加入了 BCS 协作群.")),
            "per-bot owned copies stay hidden from human viewers");
    }

    fn group_fixture(group_id: &str, driver_bot_id: &str) -> Group {
        let mut group = Group::new(
            group_id.to_string(),
            driver_bot_id,
            vec![
                Participant::bot(driver_bot_id, ParticipantRole::Manager),
                Participant::bot("worker-a", ParticipantRole::Worker),
                Participant::bot("worker-b", ParticipantRole::Worker),
            ],
        );
        group.group_strategy = GroupStrategy::Chat;
        group
    }

    mod attachment_parse_tests {
        use super::*;
        use bcs_domain::{PersistedMessage, SenderType};

        fn pm(content: serde_json::Value) -> PersistedMessage {
            PersistedMessage {
                message_id: "m".into(),
                group_id: "g".into(),
                session_id: "sid".into(),
                session_seq: 1,
                sender_id: "human_x".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content,
                client_msg_id: None,
                owner_bot_id: None,
                status: bcs_domain::PersistedMessageStatus::Normal,
                created_at: 1,
                run_id: String::new(),
            }
        }

        #[test]
        fn plain_string_content_returns_text_no_attachments() {
            let (text, atts) = extract_text_and_attachments(&serde_json::json!("hello"));
            assert_eq!(text, "hello");
            assert!(atts.is_none());
        }

        #[test]
        fn object_content_extracts_text_and_attachments_without_url() {
            let content = serde_json::json!({
                "text": "描述一下图片",
                "attachments": [{
                    "attachment_id": "01KZ977A05N0TVGX8BKFA26T6D",
                    "type": "image",
                    "file_name": "109951168084935137.jpg",
                    "mime_type": "image/jpeg",
                    "sha256": null,
                    "size": 13276
                }]
            });
            let (text, atts) = extract_text_and_attachments(&content);
            assert_eq!(text, "描述一下图片");
            let atts = atts.expect("attachments present");
            assert_eq!(atts.len(), 1);
            assert_eq!(atts[0].attachment_id, "01KZ977A05N0TVGX8BKFA26T6D");
            assert_eq!(atts[0].file_name, "109951168084935137.jpg");
            assert_eq!(atts[0].mime_type.as_deref(), Some("image/jpeg"));
            assert_eq!(atts[0].size, Some(13276));
            assert!(atts[0].url.is_none(), "url must be None at parse stage");
            assert!(atts[0].expires_at.is_none());
        }

        #[test]
        fn object_without_attachments_returns_text_only() {
            let content = serde_json::json!({"text": "only text"});
            let (text, atts) = extract_text_and_attachments(&content);
            assert_eq!(text, "only text");
            assert!(atts.is_none());
        }

        #[test]
        fn object_without_text_returns_empty_text() {
            let content = serde_json::json!({"attachments": [{"attachment_id":"a","type":"image","file_name":"f"}]});
            let (text, atts) = extract_text_and_attachments(&content);
            assert_eq!(text, "");
            assert!(atts.is_some());
        }

        #[test]
        fn attachment_missing_required_fields_is_dropped() {
            let content = serde_json::json!({
                "text": "t",
                "attachments": [
                    {"type":"image","file_name":"no_id"},            // missing attachment_id -> dropped
                    {"attachment_id":"a","file_name":"f"}            // missing type -> defaults to Image, kept
                ]
            });
            let (_t, atts) = extract_text_and_attachments(&content);
            let atts = atts.expect("some kept");
            assert_eq!(atts.len(), 1, "only the second attachment survives");
            assert_eq!(atts[0].attachment_id, "a");
        }

        #[test]
        fn persisted_to_group_message_preserves_text_and_attachments() {
            let content = serde_json::json!({
                "text": "描述一下图片",
                "attachments": [{"attachment_id":"a","type":"image","file_name":"f","mime_type":"image/png","size":4,"sha256":null}]
            });
            let gm = persisted_to_group_message(pm(content), None);
            assert_eq!(gm.content, "描述一下图片");
            let atts = gm.attachments.expect("attachments");
            assert_eq!(atts[0].attachment_id, "a");
            assert!(atts[0].url.is_none());
        }
    }
}
