//! Message history application service.
//!
//! Implements `GroupMessageHistoryService` with cutoff-based routing:
//! new groups (created_at >= cutoff) → `MessageRepoPort`, old groups → fallback.

use std::sync::Arc;

use async_trait::async_trait;
use tracing::info;

#[cfg(test)]
use bcs_domain::{BCS_STATE_MACHINE_MESSAGE_SENDER_NAME, STATE_MACHINE_HUMAN_INPUT_PROMPT_MESSAGE_TYPE};
use bcs_domain::{
    ActorKind, BCS_SESSION_OPENING_MESSAGE_SENDER_NAME,
    HumanMessageView, MessageAttachment, MessageOwnerFilter, MessageQuery, MessageViewScope,
    SESSION_OPENING_MESSAGE_TYPE,
    STATE_MACHINE_PANEL_MESSAGE_TYPE, STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE, Session,
};
use bcs_service_api::{
    BotRegistryCoreService, CallerContext, Group, GroupCoreService, GroupHistoryCommand,
    GroupHistoryResult, GroupMessage, GroupMessageHistoryService, GroupMessageType, GroupStrategy,
    GroupUseCaseError, MessageHistoryOptions, MessageRole, ParticipantRole, PendingGroupMessage,
    PendingGroupMessageKind, PendingGroupMessagePort, ServiceError, SessionHistoryCommand,
    SessionHistoryResult,
    application::session_files::SessionFileService,
    port::repo::{MessageRepoPort, SessionRepoPort},
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
    pending_messages: Arc<dyn PendingGroupMessagePort>,
    cutoff_timestamp: u64,
    manager_worker_cutoff_timestamp: u64,
    new_participant_visible_limit: u64,
    default_page_limit: u32,
    max_page_limit: u32,
    history_attachment_ttl: u64,
    persisted_state_machine_history: bool,
}

pub enum ManagerWorkerHistoryView {
    Public,
    Worker(String),
}

impl MessageService {
    fn human_message_view(
        group: &Group,
        session: Option<&Session>,
        view_actor_id: Option<&str>,
    ) -> Option<HumanMessageView> {
        let actor_id = view_actor_id?;
        let participant = session
            .and_then(|session| {
                session
                    .participants
                    .iter()
                    .find(|participant| participant.bot_uuid == actor_id)
            })
            .or_else(|| group.get_participant(actor_id))?;
        (participant.actor_kind == ActorKind::Human).then(|| HumanMessageView {
            actor_id: actor_id.to_string(),
            scope: participant.message_view_scope,
            allow_legacy_unclassified_chat: group.group_strategy == GroupStrategy::Chat,
        })
    }

    pub fn new(
        message_repo: Arc<dyn MessageRepoPort>,
        fallback: Arc<dyn GroupMessageHistoryService>,
        session_repo: Arc<dyn SessionRepoPort>,
        group: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        session_file: Arc<dyn SessionFileService>,
        pending_messages: Arc<dyn PendingGroupMessagePort>,
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
            pending_messages,
            cutoff_timestamp,
            manager_worker_cutoff_timestamp,
            new_participant_visible_limit,
            default_page_limit,
            max_page_limit,
            history_attachment_ttl,
            persisted_state_machine_history: false,
        }
    }

    pub fn with_persisted_state_machine_history(mut self, enabled: bool) -> Self {
        self.persisted_state_machine_history = enabled;
        self
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
        current_msg_seq: i64,
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
                    current_msg_seq,
                    view_bot_id,
                    new_participant_visible_limit,
                ),
                None => None,
            };
            Ok((
                Self::chat_owner_filter_for_view(view_bot_id),
                visible_from_seq,
            ))
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

    fn pending_owner(
        group: &Group,
        session: Option<&Session>,
        bot_id: &str,
    ) -> Option<Option<String>> {
        if group.group_strategy != GroupStrategy::ManagerWorker {
            return Some(None);
        }
        let participant = session
            .and_then(|session| {
                session
                    .participants
                    .iter()
                    .find(|participant| participant.bot_uuid == bot_id)
            })
            .or_else(|| group.get_participant(bot_id))?;
        Some((participant.role == ParticipantRole::Worker).then(|| bot_id.to_string()))
    }

    fn pending_is_visible(owner_filter: &MessageOwnerFilter, owner_bot_id: Option<&str>) -> bool {
        // COSEC: pending in-memory content must follow the same owner isolation
        // as the durable MessageRepo query; never expose another worker's run.
        match owner_filter {
            MessageOwnerFilter::Any => true,
            MessageOwnerFilter::IsNull => owner_bot_id.is_none(),
            MessageOwnerFilter::Eq(expected) => owner_bot_id == Some(expected.as_str()),
            MessageOwnerFilter::PublicOrOwner(expected) => {
                owner_bot_id.is_none() || owner_bot_id == Some(expected.as_str())
            }
        }
    }

    async fn pending_group_messages(
        &self,
        group: &Group,
        session: Option<&Session>,
        session_id: Option<&str>,
        owner_filter: &MessageOwnerFilter,
        human_view: Option<&HumanMessageView>,
        before: Option<u64>,
    ) -> Vec<GroupMessage> {
        let visibility_domain = match group.group_strategy {
            GroupStrategy::Chat => bcs_domain::MessageVisibilityDomain::Chat,
            GroupStrategy::ManagerWorker => bcs_domain::MessageVisibilityDomain::ManagerWorker,
            GroupStrategy::StateMachine => bcs_domain::MessageVisibilityDomain::StateMachine,
        };
        let audience = (visibility_domain != bcs_domain::MessageVisibilityDomain::Chat)
            .then_some(bcs_domain::MessageAudience::FullOnly);
        if human_view
            .is_some_and(|view| !view.allows_artifact(visibility_domain, audience.as_ref()))
        {
            return Vec::new();
        }
        let snapshots = self
            .pending_messages
            .list_pending(&group.id, session_id)
            .await;
        let mut bot_names: std::collections::HashMap<String, Option<String>> =
            std::collections::HashMap::new();
        let mut messages = Vec::with_capacity(snapshots.len());
        for snapshot in snapshots {
            if before.is_some_and(|cursor| snapshot.created_at_ms >= cursor) {
                continue;
            }
            let Some(owner_bot_id) = Self::pending_owner(group, session, &snapshot.bot_id) else {
                continue;
            };
            if !Self::pending_is_visible(owner_filter, owner_bot_id.as_deref()) {
                continue;
            }
            let bot_name = match bot_names.entry(snapshot.bot_id.clone()) {
                std::collections::hash_map::Entry::Occupied(entry) => entry.get().clone(),
                std::collections::hash_map::Entry::Vacant(entry) => {
                    let name = self
                        .registry
                        .get(&snapshot.bot_id)
                        .await
                        .and_then(|bot| bot.capabilities.name);
                    entry.insert(name.clone());
                    name
                }
            };
            messages.push(pending_to_group_message(snapshot, bot_name));
        }
        messages
    }
}

mod projection;
use projection::*;

#[async_trait]
impl GroupMessageHistoryService for MessageService {
    async fn get_history(
        &self,
        cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        self.get_history_with_options(cmd, MessageHistoryOptions::default())
            .await
    }

    async fn get_history_with_options(
        &self,
        cmd: GroupHistoryCommand,
        options: MessageHistoryOptions,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        let hide_opening_message = matches!(&cmd.caller, CallerContext::Bot(_));
        let group = self.group.get(&cmd.group_id).await.ok_or_else(|| {
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
            let human_view = Self::human_message_view(&group, None, cmd.view_bot_id.as_deref());
            let query = MessageQuery {
                group_id: cmd.group_id.clone(),
                session_id: String::new(),
                cursor: cmd.before,
                limit: self.effective_limit(cmd.limit),
                keyword: None,
                sender_id: None,
                message_type: None,
                owner_filter: owner_filter.clone(),
                time_range: None,
                visible_from_seq: None,
                human_view: human_view.clone(),
            };
            let page = self.message_repo.query_messages(query).await.map_err(|e| {
                GroupUseCaseError::Service(ServiceError::InternalError(format!(
                    "message repo error: {}",
                    e
                )))
            })?;
            let mut bot_names: std::collections::HashMap<String, Option<String>> =
                std::collections::HashMap::new();
            let mut messages: Vec<GroupMessage> = {
                let mut result = Vec::with_capacity(page.messages.len());
                for pm in page.messages.into_iter().filter(|message| {
                    !hide_opening_message || !matches!(message.message_type.as_str(), SESSION_OPENING_MESSAGE_TYPE | bcs_domain::CHAT_ERROR_MESSAGE_TYPE)
                }) {
                    if let Some(message) = bcs_domain::state_machine_history::project_state_machine_message(&pm) {
                        result.push(message);
                        continue;
                    }
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
            let mut durable_next_before = page.next_cursor.map(|cursor| cursor.0);
            // The message repo only holds messages persisted by BCS. A
            // provider-backed bot keeps its own transcript, so when a specific
            // bot view is requested and the repo has nothing for it, first
            // resolve the durable history through the legacy provider. Pending
            // tracker content is merged afterwards so an active run does not
            // hide the provider's older transcript.
            if messages.is_empty() && cmd.view_bot_id.is_some() {
                let fallback_result = self.fallback.get_history(cmd.clone()).await?;
                if !options.include_pending {
                    return Ok(fallback_result);
                }
                messages = fallback_result.messages;
                durable_next_before = fallback_result.next_before;
            }
            if !options.include_pending {
                return Ok(GroupHistoryResult {
                    group_id: cmd.group_id,
                    messages,
                    limit: cmd.limit,
                    before: cmd.before,
                    next_before: durable_next_before,
                });
            }
            let pending = self
                .pending_group_messages(
                    &group,
                    None,
                    None,
                    &owner_filter,
                    human_view.as_ref(),
                    cmd.before,
                )
                .await;
            let (messages, next_before) =
                merge_history_window(messages, pending, limit, durable_next_before);
            Ok(GroupHistoryResult {
                group_id: cmd.group_id,
                messages,
                limit: cmd.limit,
                before: cmd.before,
                next_before,
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
        self.get_session_history_with_options(cmd, MessageHistoryOptions::default())
            .await
    }

    async fn get_session_history_with_options(
        &self,
        cmd: SessionHistoryCommand,
        options: MessageHistoryOptions,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        let hide_opening_message = matches!(&cmd.caller, CallerContext::Bot(_));
        let session_id = cmd.session_id.clone();
        let session = self.session_repo.get(&session_id).await;

        // Chat and ManagerWorker use independent cutoffs for the new message store path.
        let group_opt = self.group.get(&cmd.group_id).await;
        let human_view = group_opt.as_ref().and_then(|group| {
            Self::human_message_view(group, session.as_ref(), cmd.view_bot_id.as_deref())
        });
        // Legacy provider transcripts do not carry visibility domain/audience
        // metadata and therefore cannot be projected safely. Participant views
        // always read the classified durable store, while Full keeps the exact
        // pre-feature cutoff/fallback behavior.
        let requires_participant_projection = human_view
            .as_ref()
            .is_some_and(|view| view.scope == MessageViewScope::Participant);
        if requires_participant_projection && session.is_none() {
            info!(
                session_id = %session_id,
                "get_session_history: participant view has no classified session; returning empty history"
            );
            return Ok(SessionHistoryResult {
                session_id,
                messages: Vec::new(),
                limit: cmd.limit,
                before: cmd.before,
                next_before: None,
            });
        }
        let use_new_path = match group_opt.as_ref() {
            Some(group) => {
                requires_participant_projection
                    || self.should_use_new_path(group, session.as_ref())
            }
            None => false,
        };
        if use_new_path {
            let sess = session.as_ref().unwrap();
            let limit = self.effective_limit(cmd.limit);
            let needs_current_window = cmd.view_bot_id.as_deref().is_some_and(|actor|
                sess.participant_join_seq.as_ref().and_then(|map| map.get(actor)).and_then(serde_json::Value::as_i64).is_none());
            let current_seq = if needs_current_window
                && group_opt.as_ref().unwrap().group_strategy != GroupStrategy::ManagerWorker {
                self.message_repo.get_current_seq(&sess.id).await
                    .map_err(|error| GroupUseCaseError::Service(ServiceError::InternalError(error.to_string())))?
            } else { 0 };
            let (legacy_owner_filter, mut visible_from_seq) = Self::compute_session_history_query(
                current_seq,
                group_opt.as_ref().unwrap(),
                sess,
                cmd.view_bot_id.as_deref(),
                self.new_participant_visible_limit,
            )?;
            if visible_from_seq.is_some() && group_opt.as_ref().unwrap().group_strategy == GroupStrategy::Chat {
                let anchor = cmd.view_bot_id.as_deref()
                    .and_then(|actor| sess.participant_join_seq.as_ref()?.get(actor)?.as_i64())
                    .unwrap_or(current_seq);
                visible_from_seq = Some(self.message_repo.resolve_history_window_start(
                    &sess.id, anchor, self.new_participant_visible_limit,
                ).await.map_err(|error| GroupUseCaseError::Service(ServiceError::InternalError(error.to_string())))?);
            }
            let owner_filter = if human_view
                .as_ref()
                .is_some_and(|view| view.scope == MessageViewScope::Participant)
                && group_opt.as_ref().unwrap().group_strategy != GroupStrategy::Chat
            {
                // Participant projections must inspect public and directed
                // collaboration records before applying Domain/Audience. Full
                // Human views keep the pre-feature owner filter unchanged.
                MessageOwnerFilter::Any
            } else {
                legacy_owner_filter
            };
            let merge_public_opening_message =
                !hide_opening_message && matches!(&owner_filter, MessageOwnerFilter::Eq(_));

            info!(
                session_id = %session_id,
                limit,
                visible_from_seq,
                human_view = ?human_view,
                owner_filter = ?owner_filter,
                "get_session_history: new session, querying MessageRepoPort"
            );

            let query = MessageQuery {
                group_id: cmd.group_id.clone(),
                session_id: session_id.clone(),
                cursor: cmd.before,
                limit: self.effective_limit(cmd.limit),
                keyword: None,
                sender_id: None,
                message_type: None,
                owner_filter: owner_filter.clone(),
                time_range: None,
                visible_from_seq,
                human_view: human_view.clone(),
            };
            let mut page = self.message_repo.query_messages(query).await.map_err(|e| {
                GroupUseCaseError::Service(ServiceError::InternalError(format!(
                    "message repo error: {}",
                    e
                )))
            })?;
            if merge_public_opening_message {
                let opening_page = self
                    .message_repo
                    .query_messages(MessageQuery {
                        group_id: cmd.group_id.clone(),
                        session_id: session_id.clone(),
                        cursor: cmd.before,
                        limit,
                        keyword: None,
                        sender_id: None,
                        message_type: Some(SESSION_OPENING_MESSAGE_TYPE.to_string()),
                        owner_filter: MessageOwnerFilter::IsNull,
                        time_range: None,
                        visible_from_seq: None,
                        human_view: human_view.clone(),
                    })
                    .await
                    .map_err(|error| {
                        GroupUseCaseError::Service(ServiceError::InternalError(format!(
                            "message repo opening-message error: {error}"
                        )))
                    })?;
                let source_has_more = page.has_more || opening_page.has_more;
                page.messages.extend(opening_page.messages);
                page.messages.sort_by(|left, right| {
                    right
                        .created_at
                        .cmp(&left.created_at)
                        .then_with(|| right.session_seq.cmp(&left.session_seq))
                });
                let combined_has_more = page.messages.len() > limit as usize;
                page.messages.truncate(limit as usize);
                page.has_more = source_has_more || combined_has_more;
                page.next_cursor = page
                    .has_more
                    .then(|| {
                        page.messages
                            .last()
                            .map(|message| (message.created_at, message.session_seq))
                    })
                    .flatten();
            }
            let mut bot_names: std::collections::HashMap<String, Option<String>> =
                std::collections::HashMap::new();
            let messages: Vec<GroupMessage> = {
                let mut result = Vec::with_capacity(page.messages.len());
                for pm in page.messages.into_iter().filter(|message| {
                    !hide_opening_message || !matches!(message.message_type.as_str(), SESSION_OPENING_MESSAGE_TYPE | bcs_domain::CHAT_ERROR_MESSAGE_TYPE)
                }) {
                    if let Some(message) = bcs_domain::state_machine_history::project_state_machine_message(&pm) {
                        result.push(message);
                        continue;
                    }
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
            let durable_next_before = page.next_cursor.map(|cursor| cursor.0);
            if !options.include_pending {
                return Ok(SessionHistoryResult {
                    session_id,
                    messages,
                    limit: cmd.limit,
                    before: cmd.before,
                    next_before: durable_next_before,
                });
            }
            let pending = self
                .pending_group_messages(
                    group_opt.as_ref().unwrap(),
                    Some(sess),
                    Some(&session_id),
                    &owner_filter,
                    human_view.as_ref(),
                    cmd.before,
                )
                .await;
            let (messages, next_before) =
                merge_history_window(messages, pending, limit, durable_next_before);
            Ok(SessionHistoryResult {
                session_id,
                messages,
                limit: cmd.limit,
                before: cmd.before,
                next_before,
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
                    group_id: cmd.group_id.clone(),
                    session_id: session_id.clone(),
                    cursor: cmd.before,
                    limit,
                    keyword: None,
                    sender_id: None,
                    message_type: Some(STATE_MACHINE_PANEL_MESSAGE_TYPE.to_string()),
                    owner_filter: owner_filter.clone(),
                    time_range: None,
                    visible_from_seq: None,
                    human_view: human_view.clone(),
                })
                .await
                .map_err(|error| {
                    GroupUseCaseError::Service(ServiceError::InternalError(format!(
                        "message repo panel-anchor error: {error}"
                    )))
                })?;
            let mut persisted_anchors = panel_page.messages;
            let mut persisted_anchors_have_more = panel_page.has_more;
            if self.persisted_state_machine_history {
                // Keep ordinary legacy transcripts, but StateMachine content
                // comes only from durable rows, including before the chat cutoff.
                fallback_result.messages.retain(|message| message.metadata.as_ref()
                    .and_then(bcs_domain::state_machine_history::StateMachineHistoryKey::from_metadata).is_none());
                for kind in [bcs_domain::STATE_MACHINE_OUTPUT_MESSAGE_TYPE,
                    STATE_MACHINE_HUMAN_INPUT_RESPONSE_MESSAGE_TYPE] {
                    let page = self.message_repo.query_messages(MessageQuery {
                        group_id: cmd.group_id.clone(), session_id: session_id.clone(),
                        cursor: cmd.before, limit, keyword: None, sender_id: None,
                        message_type: Some(kind.into()), owner_filter: owner_filter.clone(),
                        time_range: None, visible_from_seq: None, human_view: human_view.clone(),
                    }).await.map_err(|error| GroupUseCaseError::Service(
                        ServiceError::InternalError(format!("message repo StateMachine history error: {error}"))
                    ))?;
                    persisted_anchors_have_more |= page.has_more;
                    persisted_anchors.extend(page.messages);
                }
            }
            if !hide_opening_message {
                let opening_page = self
                    .message_repo
                    .query_messages(MessageQuery {
                        group_id: cmd.group_id,
                        session_id: session_id.clone(),
                        cursor: cmd.before,
                        limit,
                        keyword: None,
                        sender_id: None,
                        message_type: Some(SESSION_OPENING_MESSAGE_TYPE.to_string()),
                        owner_filter: MessageOwnerFilter::IsNull,
                        time_range: None,
                        visible_from_seq: None,
                        human_view: human_view.clone(),
                    })
                    .await
                    .map_err(|error| {
                        GroupUseCaseError::Service(ServiceError::InternalError(format!(
                            "message repo opening-message error: {error}"
                        )))
                    })?;
                persisted_anchors_have_more |= opening_page.has_more;
                persisted_anchors.extend(opening_page.messages);
            }
            if persisted_anchors.is_empty() {
                return Ok(fallback_result);
            }

            let source_has_more =
                fallback_result.next_before.is_some() || persisted_anchors_have_more;
            let mut seen_ids = fallback_result
                .messages
                .iter()
                .map(|message| message.id.clone())
                .collect::<std::collections::HashSet<_>>();
            fallback_result.messages.extend(
                persisted_anchors
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
mod tests;
