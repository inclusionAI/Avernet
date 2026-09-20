//! Channel(IM bridge) application service implementation.

mod commands;
mod queue_commands;
mod runtime_inbound;
mod human_input;
mod service;
mod bindings;
mod session_outbound;
mod human_input_notification;
mod terminal_notification;
pub mod visibility;

use std::collections::{HashSet, VecDeque};
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::Mutex;
use tracing::{info, warn};

use bcs_channel_api::{ChannelInboundSink, ChannelProvider, ChannelProviderRegistry};
use bcs_domain::{
    ActorKind, Attachment, AttachmentType, BindingStatus, BindingTarget, ChannelBinding,
    ChannelType, ConversationSessionMap, Group, GroupChatScope, GroupKind, GroupStrategy,
    HumanInputNotificationMode, HumanInputRequest, HumanInputRequestStatus, ImParticipantMap,
    Participant, ParticipantMode, ParticipantRole, Session, SessionKind, SessionScope,
    SessionStatus, SystemMessageEvent, Visibility, channel_group_id,
};
use bcs_service_api::application::channel::{
    ChannelInboundError, ChannelInboundFailureKind, ChannelService, ChannelUseCaseError,
    CreateBindingCommand, InboundMessage, OutboundMessage,
};
use bcs_service_api::application::collaboration_runtime::{
    AuthenticatedHumanCaller, StartStateMachineRunCommand,
};
use bcs_service_api::application::message_flow::{ChannelSenderIdentity, WebSendCommand};
use bcs_service_api::application::principal::{CallerContext, HumanActor};
use bcs_service_api::core::DmActorSpec;
use bcs_service_api::port::ChannelBindingCleanupPort;
use bcs_service_api::port::channel_delivery::{ChannelBindingRef, ChannelDeliveryPort, ChannelOutboundEvent};
use bcs_service_api::port::repo::{
    ChannelBindingRepoPort, ConversationSessionRepoPort, HumanInputEnqueueDisposition,
    HumanInputRequestRepoPort, ImParticipantRepoPort, NewSessionParams, SessionRepoPort,
};
use bcs_service_api::{
    BotRegistryCoreService, ChannelOutboundEventKind, ChannelOutboundPurpose, ChannelRenderHint,
    CollaborationRuntimeService, GroupCoreService, HumanInputReadyEvent, HumanResponseSource,
    MessageFlowService, RespondHumanNodeCommand, ServiceError, ServiceResult,
    SessionChannelDeliveryOutcome, SessionChannelOutboundPort, StateMachineTerminalEvent,
    StateMachineTerminalStatus, SystemMessageService,
};

pub use visibility::visibility_allows;

const DEFAULT_INBOUND_DEDUP_LIMIT: usize = 4096;
const CHANNEL_START_STALE_MS: u64 = 30_000;
const GROUP_CHAT_NEW_SESSION_CONFIG: &str = "group_chat_new_session_per_message";
const FORWARD_SENDER_IDENTITY_CONFIG: &str = "forward_sender_identity";

enum HumanInputActivation {
    Active,
    Unchanged,
    DeliveryFailed(String),
}

/// Channel application service implementation.
pub struct BcsChannelService {
    bindings: Arc<dyn ChannelBindingRepoPort>,
    conversations: Arc<dyn ConversationSessionRepoPort>,
    im_participants: Arc<dyn ImParticipantRepoPort>,
    human_input_requests: Arc<dyn HumanInputRequestRepoPort>,
    sessions: Arc<dyn SessionRepoPort>,
    message_flow: Arc<dyn MessageFlowService>,
    system_message: Arc<dyn SystemMessageService>,
    collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    groups: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    providers: Arc<ChannelProviderRegistry>,
    env: String,
    now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
    new_id: Arc<dyn Fn() -> String + Send + Sync>,
    inbound_dedup: InboundDedupGuard,
    binding_admin_lock: Mutex<()>,
    state_machine_session_resolution_lock: Mutex<()>,
    chat_session_resolution_lock: Mutex<()>,
    session_reset_tracker: commands::SessionResetTracker,
}

struct ResolvedInboundContext {
    binding_id: String,
    group_id: String,
    session_scope: SessionScope,
    im_user_id: Option<String>,
    caller_principal: String,
    context_projection: &'static str,
    state_machine_trigger: bool,
    new_session_per_message: bool,
}

impl BcsChannelService {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        bindings: Arc<dyn ChannelBindingRepoPort>,
        conversations: Arc<dyn ConversationSessionRepoPort>,
        im_participants: Arc<dyn ImParticipantRepoPort>,
        human_input_requests: Arc<dyn HumanInputRequestRepoPort>,
        sessions: Arc<dyn SessionRepoPort>,
        message_flow: Arc<dyn MessageFlowService>,
        system_message: Arc<dyn SystemMessageService>,
        collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
        groups: Arc<dyn GroupCoreService>,
        registry: Arc<dyn BotRegistryCoreService>,
        providers: Arc<ChannelProviderRegistry>,
        env: impl Into<String>,
        now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
        new_id: Arc<dyn Fn() -> String + Send + Sync>,
    ) -> Self {
        let env = env.into();
        Self {
            bindings,
            conversations,
            im_participants,
            human_input_requests,
            sessions,
            message_flow,
            system_message,
            collaboration_runtime,
            groups,
            registry,
            providers,
            env: env.trim().to_string(),
            now_ms,
            new_id,
            inbound_dedup: InboundDedupGuard::new(DEFAULT_INBOUND_DEDUP_LIMIT),
            binding_admin_lock: Mutex::new(()),
            state_machine_session_resolution_lock: Mutex::new(()),
            chat_session_resolution_lock: Mutex::new(()),
            session_reset_tracker: commands::SessionResetTracker::new(DEFAULT_INBOUND_DEDUP_LIMIT),
        }
    }

    async fn ensure_im_human_actor(
        &self,
        msg: &InboundMessage,
    ) -> Result<String, ChannelUseCaseError> {
        let staff_no = normalize_required(&msg.im_user_id, "im_user_id")?;
        let actor_id = human_actor_id(staff_no);
        let display_name = msg
            .im_user_nick
            .as_deref()
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .unwrap_or(staff_no);

        self.registry
            .ensure_human_actor(staff_no, display_name)
            .await?;
        self.im_participants
            .upsert(ImParticipantMap {
                channel_type: msg.channel_type.clone(),
                account_ref: msg.account_ref.trim().to_string(),
                im_user_id: staff_no.to_string(),
                actor_id: actor_id.clone(),
                display_name: Some(display_name.to_string()),
            })
            .await?;

        Ok(actor_id)
    }

    async fn resolve_inbound_context(
        &self,
        binding: &ChannelBinding,
        msg: &InboundMessage,
        actor_id: &str,
    ) -> Result<ResolvedInboundContext, ChannelUseCaseError> {
        let (group_id, is_bot_target) = match &binding.target {
            BindingTarget::Group { group_id } => (group_id.clone(), false),
            BindingTarget::Bot { bot_id } if msg.conversation_type == "1" => (
                self.ensure_dm_group(binding, bot_id, msg, actor_id).await?,
                true,
            ),
            BindingTarget::Bot { bot_id } => (
                self.ensure_managed_single_bot_group(binding, bot_id)
                    .await?,
                true,
            ),
        };
        let group = self
            .groups
            .get(&group_id)
            .await
            .ok_or_else(|| ChannelUseCaseError::NotFound(group_id.clone()))?;
        let per_sender = msg.conversation_type == "2"
            && match binding.group_chat_scope {
                Some(GroupChatScope::PerSender) => true,
                Some(GroupChatScope::ConversationShared) => false,
                None => is_bot_target,
            };
        let staff_no = normalize_required(&msg.im_user_id, "im_user_id")?;
        let session_scope = if per_sender {
            SessionScope::PerSender
        } else {
            SessionScope::Conversation
        };
        let tracks_sender = per_sender || msg.conversation_type == "1";
        let im_user_id = if tracks_sender {
            Some(staff_no.to_string())
        } else {
            None
        };
        let caller_principal = match im_user_id.as_deref() {
            Some(user_id) => format!(
                "{}:{}:{}",
                msg.channel_type, msg.im_conversation_id, user_id
            ),
            None => format!("{}:{}", msg.channel_type, msg.im_conversation_id),
        };

        Ok(ResolvedInboundContext {
            binding_id: binding.id.clone(),
            group_id,
            session_scope,
            im_user_id,
            caller_principal,
            context_projection: if is_bot_target { "direct_bot" } else { "group" },
            new_session_per_message: msg.conversation_type == "2"
                && binding.config.get(GROUP_CHAT_NEW_SESSION_CONFIG)
                    .and_then(serde_json::Value::as_bool) == Some(true),
            state_machine_trigger: !is_bot_target
                && group.group_strategy == GroupStrategy::StateMachine,
        })
    }

    async fn ensure_dm_group(
        &self,
        binding: &ChannelBinding,
        bot_id: &str,
        msg: &InboundMessage,
        actor_id: &str,
    ) -> Result<String, ChannelUseCaseError> {
        let group_id =
            channel_owned_group_id(&binding.channel_type, GroupKind::Dm, &(self.new_id)())?;
        let label = msg
            .im_user_nick
            .as_ref()
            .map(|name| format!("{} / {}", name.trim(), bot_id));
        let (group, _) = self
            .groups
            .create_or_reuse_actor_dm_group(
                &group_id,
                DmActorSpec {
                    actor_id: actor_id.to_string(),
                    actor_kind: ActorKind::Human,
                    display_name: msg.im_user_nick.clone(),
                },
                DmActorSpec {
                    actor_id: bot_id.to_string(),
                    actor_kind: ActorKind::Bot,
                    display_name: None,
                },
                bot_id,
                actor_id,
                label,
                Some("channel direct bot conversation".to_string()),
            )
            .await?;
        Ok(group.id)
    }

    async fn ensure_managed_single_bot_group(
        &self,
        binding: &ChannelBinding,
        bot_id: &str,
    ) -> Result<String, ChannelUseCaseError> {
        let group_id =
            channel_owned_group_id(&binding.channel_type, GroupKind::Normal, &binding.id)?;
        if self.groups.get(&group_id).await.is_some() {
            return Ok(group_id);
        }
        let legacy_group_id = legacy_channel_owned_group_id(&binding.channel_type, &binding.id);
        if self.groups.get(&legacy_group_id).await.is_some() {
            return Ok(legacy_group_id);
        }
        let mut group = Group::new(
            group_id.clone(),
            bot_id.to_string(),
            vec![Participant::bot(
                bot_id.to_string(),
                ParticipantRole::Driver,
            )],
        );
        group.group_strategy = GroupStrategy::Chat;
        group.label = Some(format!("Channel {}", binding.account_ref));
        self.groups.upsert(group).await?;
        Ok(group_id)
    }

    async fn resolve_or_create_chat_session(
        &self,
        ctx: &ResolvedInboundContext,
        msg: &InboundMessage,
    ) -> Result<(String, bool), ChannelUseCaseError> {
        if ctx.new_session_per_message {
            return Ok((self.create_chat_session(ctx, msg).await?, false));
        }
        let current = self
            .conversations
            .get(
                &ctx.binding_id,
                &msg.im_conversation_id,
                ctx.session_scope,
                ctx.im_user_id.as_deref(),
            )
            .await?;
        if let Some(map) = current {
            if self
                .sessions
                .get(&map.bcs_session_id)
                .await
                .is_some_and(|session| {
                    session.status == SessionStatus::Running && session.group_id == ctx.group_id
                })
            {
                return Ok((map.bcs_session_id, true));
            }
        }

        let session_id = self.create_chat_session(ctx, msg).await?;
        Ok((session_id, false))
    }

    async fn create_chat_session(
        &self,
        ctx: &ResolvedInboundContext,
        msg: &InboundMessage,
    ) -> Result<String, ChannelUseCaseError> {
        let group = self
            .groups
            .get(&ctx.group_id)
            .await
            .ok_or_else(|| ChannelUseCaseError::NotFound(ctx.group_id.clone()))?;
        let participants = group
            .participants
            .into_iter()
            .filter(|participant| participant.is_bot())
            .collect();
        let session = self
            .sessions
            .create_channel(
                &ctx.group_id,
                &msg.channel_type,
                NewSessionParams {
                    session_kind: SessionKind::Chat,
                    caller_principal: Some(ctx.caller_principal.clone()),
                    session_title: if msg.conversation_type == "1" {
                        msg.im_user_nick.clone()
                    } else {
                        None
                    },
                    meta: Some(channel_meta(ctx, msg)),
                    participants,
                    ..Default::default()
                },
            )
            .await?;
        if ctx.context_projection == "group" {
            // bcs-channel's session reason is the group's label (purpose); its
            // `session.input` is the inbound chat message and `group.context`
            // is not used as the reason here, so input/context are passed as
            // None. Empty label → no `目标` line.
            let reason = bcs_service_api::resolve_session_topic(None, None, group.label.as_deref())
                .unwrap_or_default();
            match self
                .system_message
                .notify(
                    &ctx.group_id,
                    SystemMessageEvent::SessionContext {
                        group_id: ctx.group_id.clone(),
                        session_id: session.id.clone(),
                        reason,
                        session_input: session.input.clone(),
                        task_ledger: None,
                        driver_delivery: None,
                    },
                    &session.id,
                    &session.participants,
                )
                .await
            {
                Ok(recipient_count) => info!(
                    binding_id = %ctx.binding_id,
                    group_id = %ctx.group_id,
                    bcs_session_id = %session.id,
                    recipient_count,
                    "channel inbound: initial group context injected"
                ),
                Err(error) => warn!(
                    binding_id = %ctx.binding_id,
                    group_id = %ctx.group_id,
                    bcs_session_id = %session.id,
                    error = %error,
                    "channel inbound: initial group context injection failed"
                ),
            }
        }
        Ok(session.id)
    }

    fn channel_route_from_session_meta(&self, session: &Session) -> Option<ConversationSessionMap> {
        let Some(channel) = session.meta.as_ref().and_then(|meta| meta.get("channel")) else {
            return None;
        };
        let Some(binding_id) = channel.get("binding_id").and_then(|value| value.as_str()) else {
            return None;
        };
        if binding_id.is_empty() {
            return None;
        }
        let Some(conversation_id) = channel
            .get("conversation_id")
            .and_then(|value| value.as_str())
        else {
            return None;
        };
        let session_scope = match channel
            .get("session_scope")
            .and_then(|value| value.as_str())
        {
            Some("per_sender") | Some("PerSender") => SessionScope::PerSender,
            _ => SessionScope::Conversation,
        };
        let im_user_id = channel
            .get("im_user_id")
            .and_then(|value| value.as_str())
            .filter(|value| !value.is_empty())
            .map(str::to_string);

        Some(ConversationSessionMap {
            binding_id: binding_id.to_string(),
            im_conversation_id: conversation_id.to_string(),
            im_conversation_type: channel
                .get("conversation_type")
                .and_then(|value| value.as_str())
                .unwrap_or("2")
                .to_string(),
            session_scope,
            im_user_id,
            bcs_session_id: session.id.clone(),
            last_active_at: session.updated_at,
        })
    }
}

fn validate_inbound_content(msg: &InboundMessage) -> Result<(), ChannelInboundError> {
    let attachments = msg.attachments.as_deref().unwrap_or_default();
    if attachments
        .iter()
        .any(|attachment| !valid_attachment(attachment))
    {
        return Err(ChannelInboundError::new(
            ChannelInboundFailureKind::InvalidInbound,
            false,
            "attachment metadata is incomplete",
        ));
    }
    if msg.text.trim().is_empty() && attachments.is_empty() {
        return Err(ChannelInboundError::new(
            ChannelInboundFailureKind::InvalidInbound,
            false,
            "message text and attachments are both empty",
        ));
    }
    Ok(())
}

fn valid_attachment(attachment: &Attachment) -> bool {
    !attachment.attachment_id.trim().is_empty()
        && !attachment.file_name.trim().is_empty()
        && !attachment.url.trim().is_empty()
}

fn has_temporary_file_attachment(attachments: Option<&[Attachment]>) -> bool {
    attachments.is_some_and(|items| {
        items
            .iter()
            .any(|attachment| attachment.attachment_type == AttachmentType::File)
    })
}


pub struct ChannelServiceInboundSink {
    service: Arc<dyn ChannelService>,
}

impl ChannelServiceInboundSink {
    pub fn new(service: Arc<dyn ChannelService>) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ChannelInboundSink for ChannelServiceInboundSink {
    async fn submit(&self, msg: InboundMessage) -> Result<(), ChannelInboundError> {
        self.service.handle_inbound(msg).await
    }
}

fn invalid_inbound(error: ChannelUseCaseError) -> ChannelInboundError {
    inbound_failure(ChannelInboundFailureKind::InvalidInbound, false, error)
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    let mut chars = value.chars();
    let truncated = chars.by_ref().take(max_chars).collect::<String>();
    if chars.next().is_some() {
        format!("{truncated}…")
    } else {
        truncated
    }
}

fn inbound_failure(
    kind: ChannelInboundFailureKind,
    retryable: bool,
    error: impl std::fmt::Display,
) -> ChannelInboundError {
    ChannelInboundError::new(kind, retryable, error.to_string())
}

async fn validate_target(
    groups: &dyn GroupCoreService,
    registry: &dyn BotRegistryCoreService,
    cmd: &CreateBindingCommand,
) -> Result<BindingTarget, ChannelUseCaseError> {
    match &cmd.target {
        BindingTarget::Group { group_id } => {
            let group_id = normalize_required(group_id, "group_id")?;
            let group = groups
                .get(group_id)
                .await
                .ok_or_else(|| ChannelUseCaseError::NotFound(group_id.to_string()))?;
            if group.group_strategy == GroupStrategy::StateMachine
                && cmd.outbound_visibility == Visibility::LeadOnly
            {
                return Err(ChannelUseCaseError::InvalidParams(
                    "state_machine group does not support lead_only visibility".to_string(),
                ));
            }
            Ok(BindingTarget::Group {
                group_id: group_id.to_string(),
            })
        }
        BindingTarget::Bot { bot_id } => {
            let bot_id = normalize_required(bot_id, "bot_id")?;
            if registry.get(bot_id).await.is_none() {
                return Err(ChannelUseCaseError::NotFound(bot_id.to_string()));
            }
            Ok(BindingTarget::Bot {
                bot_id: bot_id.to_string(),
            })
        }
    }
}

fn provider_error(error: bcs_channel_api::ChannelProviderError) -> ChannelUseCaseError {
    ChannelUseCaseError::InvalidParams(error.to_string())
}

fn channel_meta(ctx: &ResolvedInboundContext, msg: &InboundMessage) -> serde_json::Value {
    serde_json::json!({
        "channel": {
            "source": msg.channel_type,
            "binding_id": ctx.binding_id,
            "conversation_id": msg.im_conversation_id,
            "conversation_type": msg.conversation_type,
            "session_scope": match ctx.session_scope {
                SessionScope::Conversation => "conversation",
                SessionScope::PerSender => "per_sender",
            },
            "im_user_id": ctx.im_user_id,
            "context_projection": ctx.context_projection,
        }
    })
}

fn session_scope_label(scope: SessionScope) -> &'static str {
    match scope {
        SessionScope::Conversation => "conversation",
        SessionScope::PerSender => "per_sender",
    }
}

fn binding_target_kind(target: &BindingTarget) -> &'static str {
    match target {
        BindingTarget::Group { .. } => "group",
        BindingTarget::Bot { .. } => "bot",
    }
}

fn validate_group_chat_session_config(config: &serde_json::Value) -> Result<(), ChannelUseCaseError> {
    if config.get(GROUP_CHAT_NEW_SESSION_CONFIG).is_some_and(|value| !value.is_boolean()) {
        return Err(ChannelUseCaseError::InvalidParams(format!(
            "{GROUP_CHAT_NEW_SESSION_CONFIG} must be a boolean"
        )));
    }
    Ok(())
}

fn validate_forward_sender_identity_config(
    target: &BindingTarget,
    config: &serde_json::Value,
) -> Result<(), ChannelUseCaseError> {
    let enabled = match config.get(FORWARD_SENDER_IDENTITY_CONFIG) {
        None => false,
        Some(value) => value.as_bool().ok_or_else(|| {
            ChannelUseCaseError::InvalidParams(format!(
                "{FORWARD_SENDER_IDENTITY_CONFIG} must be a boolean"
            ))
        })?,
    };
    if enabled && !matches!(target, BindingTarget::Bot { .. }) {
        return Err(ChannelUseCaseError::InvalidParams(format!(
            "{FORWARD_SENDER_IDENTITY_CONFIG} can only be enabled for a Bot binding"
        )));
    }
    Ok(())
}

fn channel_sender_identity(
    binding: &ChannelBinding,
    msg: &InboundMessage,
    caller: &CallerContext,
) -> Option<ChannelSenderIdentity> {
    // COSEC: external Human attribution is disclosed only after the resolved
    // Bot binding explicitly opts in; message text never controls this gate.
    if !matches!(binding.target, BindingTarget::Bot { .. })
        || binding
            .config
            .get(FORWARD_SENDER_IDENTITY_CONFIG)
            .and_then(serde_json::Value::as_bool)
            != Some(true)
    {
        return None;
    }
    let CallerContext::Human(human) = caller else {
        return None;
    };
    let user_id = human.staff_no.trim();
    if user_id.is_empty() {
        return None;
    }
    let display_name = msg
        .im_user_nick
        .as_deref()
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .unwrap_or(user_id);
    Some(ChannelSenderIdentity {
        channel_type: msg.channel_type.clone(),
        user_id: user_id.to_string(),
        actor_id: human.actor_id.clone(),
        display_name: display_name.to_string(),
    })
}

fn binding_relevant_to_group(binding: &ChannelBinding, group_id: &str, session: &Session) -> bool {
    match &binding.target {
        BindingTarget::Group {
            group_id: target_group_id,
        } => target_group_id == group_id,
        BindingTarget::Bot { .. } => session.group_id == group_id,
    }
}

fn normalize_required<'a>(
    value: &'a str,
    field_name: &str,
) -> Result<&'a str, ChannelUseCaseError> {
    let value = value.trim();
    if value.is_empty() {
        Err(ChannelUseCaseError::InvalidParams(format!(
            "{field_name} must not be empty"
        )))
    } else {
        Ok(value)
    }
}

fn channel_owned_group_id(
    channel_type: &str,
    group_kind: GroupKind,
    owner_id: &str,
) -> Result<String, ChannelUseCaseError> {
    let channel_type = normalize_required(channel_type, "channel_type")?;
    let owner_id = normalize_required(owner_id, "channel group owner id")?;
    channel_group_id(channel_type, group_kind, owner_id).map_err(|error| {
        ChannelUseCaseError::Internal(ServiceError::InternalError(error.to_string()))
    })
}

fn legacy_channel_owned_group_id(channel_type: &str, owner_id: &str) -> String {
    format!("{}_{}", channel_type.trim(), owner_id.trim())
}

fn human_actor_id(staff_no: &str) -> String {
    format!("human_{}", staff_no.trim())
}

fn fixed_group_reply_scope(binding_id: &str, conversation_id: &str, actor_id: &str) -> String {
    reply_scope_key("fixed_group", &[binding_id, conversation_id, actor_id])
}

fn direct_reply_scope(binding_id: &str, im_user_id: &str, actor_id: &str) -> String {
    reply_scope_key("direct_assignee", &[binding_id, im_user_id, actor_id])
}

fn reply_scope_key(kind: &str, components: &[&str]) -> String {
    let mut key = kind.to_string();
    for component in components {
        key.push('|');
        key.push_str(&component.len().to_string());
        key.push(':');
        key.push_str(component);
    }
    key
}

fn inbound_dedup_key(channel_type: &str, account_ref: &str, msg_id: &str) -> Option<String> {
    let msg_id = msg_id.trim();
    if msg_id.is_empty() {
        return None;
    }
    Some(format!("{channel_type}:{account_ref}:{msg_id}"))
}

struct InboundDedupGuard {
    state: Mutex<InboundDedupState>,
    limit: usize,
}

#[derive(Default)]
struct InboundDedupState {
    seen: HashSet<String>,
    order: VecDeque<String>,
}

impl InboundDedupGuard {
    fn new(limit: usize) -> Self {
        Self {
            state: Mutex::new(InboundDedupState::default()),
            limit,
        }
    }

    async fn claim(&self, key: &str) -> bool {
        let mut state = self.state.lock().await;
        if state.seen.contains(key) {
            return false;
        }
        state.seen.insert(key.to_string());
        state.order.push_back(key.to_string());
        while state.order.len() > self.limit {
            if let Some(oldest) = state.order.pop_front() {
                state.seen.remove(&oldest);
            }
        }
        true
    }

    async fn forget(&self, key: &str) {
        let mut state = self.state.lock().await;
        state.seen.remove(key);
        state.order.retain(|existing| existing != key);
    }
}


#[cfg(test)]
mod tests;
