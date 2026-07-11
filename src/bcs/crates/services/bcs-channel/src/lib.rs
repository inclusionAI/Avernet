//! Channel(IM bridge) application service implementation.

pub mod visibility;

use std::collections::{HashSet, VecDeque};
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::Mutex;
use tracing::{info, warn};

use bcs_channel_api::{
    ChannelInboundSink, ChannelProvider, ChannelProviderRegistry,
};
use bcs_domain::{
    ActorKind, BindingStatus, BindingTarget, ChannelBinding, ConversationSessionMap,
    Group, GroupChatScope, GroupStrategy, ImParticipantMap, Participant, ParticipantMode,
    ParticipantRole, Session, SessionKind, SessionScope, SessionStatus, Visibility,
};
use bcs_service_api::application::channel::{
    ChannelInboundError, ChannelInboundFailureKind, ChannelService, ChannelUseCaseError,
    CreateBindingCommand, InboundMessage, OutboundMessage,
};
use bcs_service_api::application::collaboration_runtime::StartStateMachineRunCommand;
use bcs_service_api::application::message_flow::WebSendCommand;
use bcs_service_api::application::principal::{CallerContext, HumanActor};
use bcs_service_api::core::DmActorSpec;
use bcs_service_api::port::channel_delivery::{
    ChannelBindingRef, ChannelOutboundEvent,
};
use bcs_service_api::port::repo::{
    ChannelBindingRepoPort, ConversationSessionRepoPort, ImParticipantRepoPort, NewSessionParams,
    SessionRepoPort,
};
use bcs_service_api::{
    BotRegistryCoreService, CollaborationRuntimeService, GroupCoreService, MessageFlowService,
    ServiceError,
};

pub use visibility::visibility_allows;

const DEFAULT_INBOUND_DEDUP_LIMIT: usize = 4096;
const MAX_CHANNEL_SESSION_ID_CHARS: usize = 64;
const GENERATED_SESSION_ID_SUFFIX_CHARS: usize = 9;

/// Channel application service implementation.
pub struct BcsChannelService {
    bindings: Arc<dyn ChannelBindingRepoPort>,
    conversations: Arc<dyn ConversationSessionRepoPort>,
    im_participants: Arc<dyn ImParticipantRepoPort>,
    sessions: Arc<dyn SessionRepoPort>,
    message_flow: Arc<dyn MessageFlowService>,
    collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    groups: Arc<dyn GroupCoreService>,
    registry: Arc<dyn BotRegistryCoreService>,
    providers: Arc<ChannelProviderRegistry>,
    env: String,
    now_ms: Arc<dyn Fn() -> u64 + Send + Sync>,
    new_id: Arc<dyn Fn() -> String + Send + Sync>,
    inbound_dedup: InboundDedupGuard,
    binding_admin_lock: Mutex<()>,
}

struct ResolvedInboundContext {
    binding_id: String,
    group_id: String,
    session_scope: SessionScope,
    im_user_id: Option<String>,
    caller_principal: String,
    context_projection: &'static str,
    state_machine_trigger: bool,
}

impl BcsChannelService {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        bindings: Arc<dyn ChannelBindingRepoPort>,
        conversations: Arc<dyn ConversationSessionRepoPort>,
        im_participants: Arc<dyn ImParticipantRepoPort>,
        sessions: Arc<dyn SessionRepoPort>,
        message_flow: Arc<dyn MessageFlowService>,
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
            sessions,
            message_flow,
            collaboration_runtime,
            groups,
            registry,
            providers,
            env: env.trim().to_string(),
            now_ms,
            new_id,
            inbound_dedup: InboundDedupGuard::new(DEFAULT_INBOUND_DEDUP_LIMIT),
            binding_admin_lock: Mutex::new(()),
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
            BindingTarget::Bot { bot_id } if msg.conversation_type == "1" => {
                (self.ensure_dm_group(binding, bot_id, msg, actor_id).await?, true)
            }
            BindingTarget::Bot { bot_id } => {
                (self.ensure_managed_single_bot_group(binding, bot_id).await?, true)
            }
        };
        let group = self
            .groups
            .get(&group_id)
            .await
            .ok_or_else(|| ChannelUseCaseError::NotFound(group_id.clone()))?;
        if !is_bot_target && group.group_strategy == GroupStrategy::Chat {
            return Err(ChannelUseCaseError::InvalidParams(
                "free-chat groups are not exposed as channel Group targets".to_string(),
            ));
        }

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
            Some(user_id) => format!("{}:{}:{}", msg.channel_type, msg.im_conversation_id, user_id),
            None => format!("{}:{}", msg.channel_type, msg.im_conversation_id),
        };

        Ok(ResolvedInboundContext {
            binding_id: binding.id.clone(),
            group_id,
            session_scope,
            im_user_id,
            caller_principal,
            context_projection: if is_bot_target { "direct_bot" } else { "group" },
            state_machine_trigger: !is_bot_target && group.group_strategy == GroupStrategy::StateMachine,
        })
    }

    async fn ensure_dm_group(
        &self,
        binding: &ChannelBinding,
        bot_id: &str,
        msg: &InboundMessage,
        actor_id: &str,
    ) -> Result<String, ChannelUseCaseError> {
        let group_id = channel_owned_group_id(&binding.channel_type, &(self.new_id)())?;
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
        let group_id = channel_owned_group_id(&binding.channel_type, &binding.id)?;
        if self.groups.get(&group_id).await.is_some() {
            return Ok(group_id);
        }
        let mut group = Group::new(
            group_id.clone(),
            bot_id.to_string(),
            vec![Participant::bot(bot_id.to_string(), ParticipantRole::Driver)],
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
            .create(
                &ctx.group_id,
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
        Ok(session.id)
    }

    async fn start_state_machine_from_inbound(
        &self,
        ctx: &ResolvedInboundContext,
        msg: &InboundMessage,
        actor_id: &str,
    ) -> Result<(), ChannelUseCaseError> {
        let input = serde_json::json!({
            "source": msg.channel_type,
            "text": msg.text,
            "sender": {
                "staff_id": msg.im_user_id.trim(),
                "name": msg.im_user_nick,
                "actor_id": actor_id,
            },
            "conversation": {
                "id": msg.im_conversation_id,
                "type": msg.conversation_type,
            },
            "scope": match ctx.session_scope {
                SessionScope::Conversation => "conversation",
                SessionScope::PerSender => "per_sender",
            },
        });
        let session = self
            .sessions
            .create(
                &ctx.group_id,
                NewSessionParams {
                    session_kind: SessionKind::ServiceInvocation,
                    caller_id: Some(actor_id.to_string()),
                    caller_principal: Some(ctx.caller_principal.clone()),
                    input: Some(input.clone()),
                    session_title: msg.im_user_nick.clone(),
                    meta: Some(channel_meta(ctx, msg)),
                    ..Default::default()
                },
            )
            .await?;
        self.conversations
            .upsert(ConversationSessionMap {
                binding_id: ctx.binding_id.clone(),
                im_conversation_id: msg.im_conversation_id.clone(),
                im_conversation_type: msg.conversation_type.clone(),
                session_scope: ctx.session_scope,
                im_user_id: ctx.im_user_id.clone(),
                bcs_session_id: session.id.clone(),
                last_active_at: (self.now_ms)(),
            })
            .await?;
        self.collaboration_runtime
            .start_state_machine_run(StartStateMachineRunCommand {
                group_id: ctx.group_id.clone(),
                session_id: Some(session.id),
                definition_yaml: None,
                definition: None,
                definition_ref: None,
                input,
                caller_id: Some(actor_id.to_string()),
            })
            .await
            .map_err(|error| {
                ChannelUseCaseError::Internal(ServiceError::InternalError(error.to_string()))
            })?;
        Ok(())
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
        let Some(conversation_id) = channel.get("conversation_id").and_then(|value| value.as_str()) else {
            return None;
        };
        let session_scope = match channel.get("session_scope").and_then(|value| value.as_str()) {
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

#[async_trait]
impl ChannelService for BcsChannelService {
    async fn handle_inbound(
        &self,
        mut msg: InboundMessage,
    ) -> Result<(), ChannelInboundError> {
        if msg.conversation_type == "2" && !msg.is_at_bot {
            info!(
                channel_type = %msg.channel_type,
                account_ref = %msg.account_ref,
                msg_id = %msg.msg_id,
                im_conversation_id = %msg.im_conversation_id,
                conversation_type = %msg.conversation_type,
                reason = "group_message_without_at_bot",
                "channel inbound: ignored"
            );
            return Ok(());
        }
        if msg.msg_id.trim().is_empty() {
            info!(
                channel_type = %msg.channel_type,
                account_ref = %msg.account_ref,
                reason = "empty_msg_id",
                "channel inbound: ignored"
            );
            return Ok(());
        }
        msg.channel_type = normalize_required(&msg.channel_type, "channel_type")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        msg.account_ref = normalize_required(&msg.account_ref, "account_ref")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        msg.im_conversation_id = normalize_required(&msg.im_conversation_id, "im_conversation_id")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        msg.conversation_type = normalize_required(&msg.conversation_type, "conversation_type")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        msg.im_user_id = normalize_required(&msg.im_user_id, "im_user_id")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        msg.msg_id = normalize_required(&msg.msg_id, "msg_id")
            .map_err(|error| invalid_inbound(error))?
            .to_string();
        let account_ref = msg.account_ref.clone();
        info!(
            channel_type = %msg.channel_type,
            account_ref = %account_ref,
            msg_id = %msg.msg_id,
            im_conversation_id = %msg.im_conversation_id,
            conversation_type = %msg.conversation_type,
            im_user_id = %msg.im_user_id,
            is_at_bot = msg.is_at_bot,
            text_len = msg.text.chars().count(),
            "channel inbound: received"
        );
        let Some(binding) = self
            .bindings
            .find_active_by_account(msg.channel_type.clone(), &account_ref)
            .await
            .map_err(|error| {
                inbound_failure(
                    ChannelInboundFailureKind::BindingLookupFailed,
                    true,
                    error,
                )
            })?
        else {
            return Err(ChannelInboundError::new(
                ChannelInboundFailureKind::BindingNotFound,
                false,
                format!(
                    "active binding not found for channel {} account {}",
                    msg.channel_type, account_ref
                ),
            ));
        };
        info!(
            channel_type = %binding.channel_type,
            account_ref = %binding.account_ref,
            binding_id = %binding.id,
            msg_id = %msg.msg_id,
            target_kind = binding_target_kind(&binding.target),
            "channel inbound: binding resolved"
        );
        let dedup_key = inbound_dedup_key(&msg.channel_type, &account_ref, &msg.msg_id);
        if let Some(key) = dedup_key.as_deref() {
            if !self.inbound_dedup.claim(key).await {
                info!(
                    channel_type = %msg.channel_type,
                    account_ref = %account_ref,
                    msg_id = %msg.msg_id,
                    reason = "duplicate_msg_id",
                    "channel inbound: ignored"
                );
                return Ok(());
            }
        }

        let result = async {
            let actor_id = self.ensure_im_human_actor(&msg).await.map_err(|error| {
                inbound_failure(
                    ChannelInboundFailureKind::ActorResolutionFailed,
                    true,
                    error,
                )
            })?;
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                msg_id = %msg.msg_id,
                im_user_id = %msg.im_user_id,
                actor_id = %actor_id,
                "channel inbound: actor resolved"
            );
            let ctx = self
                .resolve_inbound_context(&binding, &msg, &actor_id)
                .await
                .map_err(|error| {
                    inbound_failure(
                        ChannelInboundFailureKind::ContextResolutionFailed,
                        true,
                        error,
                    )
                })?;
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                binding_id = %ctx.binding_id,
                msg_id = %msg.msg_id,
                group_id = %ctx.group_id,
                session_scope = session_scope_label(ctx.session_scope),
                context_projection = ctx.context_projection,
                state_machine_trigger = ctx.state_machine_trigger,
                "channel inbound: context resolved"
            );

            if ctx.state_machine_trigger {
                return self
                    .start_state_machine_from_inbound(&ctx, &msg, &actor_id)
                    .await
                    .map_err(|error| {
                        inbound_failure(ChannelInboundFailureKind::DispatchFailed, true, error)
                    });
            }

            let (session_id, reused_session) = self
                .resolve_or_create_chat_session(&ctx, &msg)
                .await
                .map_err(|error| {
                    inbound_failure(
                        ChannelInboundFailureKind::SessionResolutionFailed,
                        true,
                        error,
                    )
                })?;
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                binding_id = %ctx.binding_id,
                msg_id = %msg.msg_id,
                group_id = %ctx.group_id,
                bcs_session_id = %session_id,
                reused = reused_session,
                session_scope = session_scope_label(ctx.session_scope),
                "channel inbound: session resolved"
            );
            self.conversations
                .upsert(ConversationSessionMap {
                    binding_id: binding.id,
                    im_conversation_id: msg.im_conversation_id.clone(),
                    im_conversation_type: msg.conversation_type.clone(),
                    session_scope: ctx.session_scope,
                    im_user_id: ctx.im_user_id.clone(),
                    bcs_session_id: session_id.clone(),
                    last_active_at: (self.now_ms)(),
                })
                .await
                .map_err(|error| {
                    inbound_failure(
                        ChannelInboundFailureKind::SessionResolutionFailed,
                        true,
                        error,
                    )
                })?;
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                binding_id = %ctx.binding_id,
                msg_id = %msg.msg_id,
                bcs_session_id = %session_id,
                im_conversation_id = %msg.im_conversation_id,
                "channel inbound: conversation mapped"
            );

            let mut participant = Participant::human(actor_id.clone(), ParticipantRole::Consultant);
            participant.mode = Some(ParticipantMode::Present);
            participant.bot_name = msg.im_user_nick.clone();
            self.sessions
                .add_participant(&session_id, participant)
                .await
                .map_err(|error| {
                    inbound_failure(
                        ChannelInboundFailureKind::SessionResolutionFailed,
                        true,
                        error,
                    )
                })?;
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                msg_id = %msg.msg_id,
                bcs_session_id = %session_id,
                actor_id = %actor_id,
                "channel inbound: participant added"
            );

            let dispatch_group_id = ctx.group_id.clone();
            let dispatch_session_id = session_id.clone();
            let dispatch_actor_id = actor_id.clone();
            let dispatch_msg_id = msg.msg_id.clone();
            let outcome = self
                .message_flow
                .handle_web_send(WebSendCommand {
                    caller: CallerContext::Human(HumanActor {
                        actor_id: actor_id.clone(),
                        staff_no: msg.im_user_id.trim().to_string(),
                    }),
                    group_id: dispatch_group_id.clone(),
                    session_id: Some(dispatch_session_id.clone()),
                    from_actor_id: dispatch_actor_id.clone(),
                    from_name: msg.im_user_nick,
                    message: msg.text,
                    mentions: Vec::new(),
                    attachments: None,
                    thinking: None,
                    idempotency_key: Some(dispatch_msg_id.clone()),
                    sender_conn_id: None,
                })
                .await
                .map_err(|error| {
                    inbound_failure(ChannelInboundFailureKind::DispatchFailed, true, error)
                })?;
            if outcome.active_run_ids.is_empty() && outcome.failed_count > 0 {
                return Err(ChannelInboundError::new(
                    ChannelInboundFailureKind::DispatchFailed,
                    true,
                    format!(
                        "message flow reported {} failed deliveries without an active run",
                        outcome.failed_count
                    ),
                ));
            }
            info!(
                channel_type = %msg.channel_type,
                account_ref = %account_ref,
                binding_id = %ctx.binding_id,
                msg_id = %dispatch_msg_id,
                group_id = %dispatch_group_id,
                bcs_session_id = %dispatch_session_id,
                actor_id = %dispatch_actor_id,
                "channel inbound: dispatched"
            );
            Ok(())
        }
        .await;
        if result.is_err() {
            if let Some(key) = dedup_key.as_deref() {
                self.inbound_dedup.forget(key).await;
            }
        }
        result
    }

    async fn try_outbound(&self, msg: OutboundMessage) -> Result<(), ChannelUseCaseError> {
        if msg.source_is_channel {
            return Ok(());
        }
        let Some(group) = self.groups.get(&msg.group_id).await else {
            return Ok(());
        };
        let Some(session) = self.sessions.get(&msg.bcs_session_id).await else {
            return Ok(());
        };
        if session.group_id != msg.group_id {
            return Ok(());
        }
        let mut conversations = self
            .conversations
            .list_by_bcs_session(&msg.bcs_session_id)
            .await?;
        if let Some(conv) = self.channel_route_from_session_meta(&session) {
            if !conversations
                .iter()
                .any(|existing| existing.binding_id == conv.binding_id)
            {
                conversations.push(conv);
            }
        }
        let mut seen_bindings = HashSet::new();
        for conv in conversations {
            if !seen_bindings.insert(conv.binding_id.clone()) {
                continue;
            }
            let Some(binding) = self.bindings.get(&conv.binding_id).await? else {
                continue;
            };
            if binding.status != BindingStatus::Active {
                continue;
            }
            if !binding_relevant_to_group(&binding, &msg.group_id, &session) {
                continue;
            }
            if !visibility_allows(group.group_strategy, binding.outbound_visibility, msg.sender_role) {
                continue;
            }
            let binding_ref = ChannelBindingRef {
                channel_type: binding.channel_type.clone(),
                account_ref: binding.account_ref.clone(),
            };
            let Some(provider) = self.providers.get(&binding.channel_type) else {
                warn!(
                    channel_type = %binding.channel_type,
                    binding_id = %binding.id,
                    "channel outbound: provider is not registered"
                );
                continue;
            };
            let delivery = provider.delivery();
            if !delivery.is_available(&binding_ref).await {
                info!(
                    binding_id = %binding.id,
                    channel_type = %binding.channel_type,
                    account_ref = %binding.account_ref,
                    bcs_session_id = %msg.bcs_session_id,
                    run_id = %msg.run_id,
                    reason = "delivery_unavailable",
                    "channel outbound: skipped"
                );
                continue;
            }
            let im_user_display_name = match conv.im_user_id.as_deref() {
                Some(user_id) => self
                    .im_participants
                    .get(binding.channel_type.clone(), &binding.account_ref, user_id)
                    .await?
                    .and_then(|participant| participant.display_name),
                None => None,
            };
            let im_conversation_id = conv.im_conversation_id.clone();
            let im_conversation_type = conv.im_conversation_type.clone();
            let im_user_id = conv.im_user_id.clone();
            info!(
                binding_id = %binding.id,
                channel_type = %binding.channel_type,
                account_ref = %binding.account_ref,
                bcs_session_id = %msg.bcs_session_id,
                run_id = %msg.run_id,
                im_conversation_id = %im_conversation_id,
                conversation_type = %im_conversation_type,
                im_user_id = im_user_id.as_deref().unwrap_or(""),
                event_kind = ?msg.kind,
                text_len = msg.text.as_deref().map(|text| text.chars().count()).unwrap_or(0),
                "channel outbound: selected"
            );
            let result = match delivery
                .deliver_event(ChannelOutboundEvent {
                    binding_ref,
                    im_conversation_id,
                    im_conversation_type,
                    im_user_id,
                    im_user_display_name,
                    bcs_session_id: msg.bcs_session_id.clone(),
                    run_id: msg.run_id.clone(),
                    sender_actor_id: msg.sender_actor_id.clone(),
                    sender_label: msg.sender_label.clone(),
                    render_sender_label: matches!(binding.target, BindingTarget::Group { .. })
                        && binding.outbound_visibility == Visibility::FullTranscript,
                    sender_role: msg.sender_role,
                    kind: msg.kind,
                    text: msg.text.clone(),
                    raw_payload: msg.raw_payload.clone(),
                    render_hint: msg.render_hint,
                })
                .await
            {
                Ok(result) => result,
                Err(error) => {
                    warn!(
                        binding_id = %binding.id,
                        channel_type = %binding.channel_type,
                        account_ref = %binding.account_ref,
                        bcs_session_id = %msg.bcs_session_id,
                        run_id = %msg.run_id,
                        error = %error,
                        "channel outbound: delivery call failed"
                    );
                    continue;
                }
            };
            if !result.delivered {
                let delivery_error = result.error.as_ref().map(ToString::to_string);
                warn!(
                    binding_id = %binding.id,
                    channel_type = %binding.channel_type,
                    account_ref = %binding.account_ref,
                    bcs_session_id = %msg.bcs_session_id,
                    run_id = %msg.run_id,
                    error = delivery_error.as_deref().unwrap_or(""),
                    "channel outbound: delivery not confirmed"
                );
            } else {
                info!(
                    binding_id = %binding.id,
                    channel_type = %binding.channel_type,
                    account_ref = %binding.account_ref,
                    bcs_session_id = %msg.bcs_session_id,
                    run_id = %msg.run_id,
                    "channel outbound: delivered"
                );
            }
        }
        Ok(())
    }

    async fn create_binding(
        &self,
        cmd: CreateBindingCommand,
    ) -> Result<ChannelBinding, ChannelUseCaseError> {
        let _guard = self.binding_admin_lock.lock().await;
        let account_ref = normalize_required(&cmd.account_ref, "account_ref")?.to_string();
        if self.env.is_empty() {
            return Err(ChannelUseCaseError::Internal(ServiceError::InternalError(
                "server environment configuration 'env' is empty".to_string(),
            )));
        }
        let env = self.env.clone();
        let target = validate_target(&*self.groups, &*self.registry, &cmd).await?;
        let group_chat_scope = match (&target, cmd.group_chat_scope) {
            (BindingTarget::Bot { .. }, None) => Some(GroupChatScope::PerSender),
            (_, scope) => scope,
        };
        let provider = self.provider_for(&cmd.channel_type)?;
        provider.validate_config(&cmd.config).map_err(provider_error)?;
        let binding_id = (self.new_id)();
        if matches!(&target, BindingTarget::Bot { .. }) {
            channel_owned_group_id(&cmd.channel_type, &binding_id)?;
        }
        if self
            .bindings
            .find_active_by_account(cmd.channel_type.clone(), &account_ref)
            .await?
            .is_some()
        {
            return Err(ChannelUseCaseError::InvalidParams(format!(
                "active binding already exists for account_ref {account_ref}"
            )));
        }

        let binding = ChannelBinding {
            id: binding_id,
            channel_type: cmd.channel_type,
            account_ref,
            target,
            group_chat_scope,
            outbound_visibility: cmd.outbound_visibility,
            env,
            status: BindingStatus::Active,
            created_by: cmd.created_by,
            config: cmd.config,
        };
        self.bindings.create(binding.clone()).await?;
        let mut redacted = binding;
        redacted.config = provider.redact_config(&redacted.config);
        Ok(redacted)
    }

    async fn list_bindings(&self) -> Result<Vec<ChannelBinding>, ChannelUseCaseError> {
        let bindings = self.bindings.list().await?;
        bindings
            .into_iter()
            .map(|mut binding| {
                let provider = self.provider_for(&binding.channel_type)?;
                binding.config = provider.redact_config(&binding.config);
                Ok(binding)
            })
            .collect()
    }

    async fn set_binding_status(
        &self,
        id: &str,
        active: bool,
    ) -> Result<(), ChannelUseCaseError> {
        let _guard = self.binding_admin_lock.lock().await;
        let Some(binding) = self.bindings.get(id).await? else {
            return Err(ChannelUseCaseError::NotFound(id.to_string()));
        };
        if active {
            if let Some(existing) = self
                .bindings
                .find_active_by_account(binding.channel_type.clone(), &binding.account_ref)
                .await?
            {
                if existing.id != id {
                    return Err(ChannelUseCaseError::InvalidParams(format!(
                        "active binding already exists for account_ref {}",
                        binding.account_ref
                    )));
                }
            }
        }
        self.bindings.set_status(id, active).await?;
        Ok(())
    }

    async fn update_binding_config(
        &self,
        id: &str,
        config: serde_json::Value,
    ) -> Result<(), ChannelUseCaseError> {
        let _guard = self.binding_admin_lock.lock().await;
        let Some(binding) = self.bindings.get(id).await? else {
            return Err(ChannelUseCaseError::NotFound(id.to_string()));
        };
        let provider = self.provider_for(&binding.channel_type)?;
        provider.validate_config(&config).map_err(provider_error)?;
        self.bindings.set_config(id, config).await?;
        Ok(())
    }

    async fn delete_binding(&self, id: &str) -> Result<(), ChannelUseCaseError> {
        if self.bindings.get(id).await?.is_none() {
            return Err(ChannelUseCaseError::NotFound(id.to_string()));
        }
        self.bindings.delete(id).await?;
        Ok(())
    }
}

impl BcsChannelService {
    fn provider_for(
        &self,
        channel_type: &str,
    ) -> Result<Arc<dyn ChannelProvider>, ChannelUseCaseError> {
        self.providers.get(channel_type).ok_or_else(|| {
            ChannelUseCaseError::InvalidParams(format!(
                "channel provider '{channel_type}' is not available"
            ))
        })
    }
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
            if group.group_strategy == GroupStrategy::Chat {
                return Err(ChannelUseCaseError::InvalidParams(
                    "free-chat groups are not exposed as channel Group targets".to_string(),
                ));
            }
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

fn channel_meta(
    ctx: &ResolvedInboundContext,
    msg: &InboundMessage,
) -> serde_json::Value {
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

fn binding_relevant_to_group(binding: &ChannelBinding, group_id: &str, session: &Session) -> bool {
    match &binding.target {
        BindingTarget::Group { group_id: target_group_id } => target_group_id == group_id,
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
    owner_id: &str,
) -> Result<String, ChannelUseCaseError> {
    let channel_type = normalize_required(channel_type, "channel_type")?;
    let owner_id = normalize_required(owner_id, "channel group owner id")?;
    let group_id = format!("{channel_type}_{owner_id}");
    if group_id.chars().count() + GENERATED_SESSION_ID_SUFFIX_CHARS
        > MAX_CHANNEL_SESSION_ID_CHARS
    {
        return Err(ChannelUseCaseError::Internal(ServiceError::InternalError(
            format!(
                "generated channel group id cannot produce a session id within {MAX_CHANNEL_SESSION_ID_CHARS} characters"
            ),
        )));
    }
    Ok(group_id)
}

fn human_actor_id(staff_no: &str) -> String {
    format!("human_{}", staff_no.trim())
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
mod tests {
    use std::collections::HashMap;
    use std::future::Future;
    use std::io::{self, Write};
    use std::sync::{Arc, Once, OnceLock};
    use std::sync::atomic::{AtomicU64, Ordering};

    use async_trait::async_trait;
    use tokio::sync::Mutex;

    use bcs_channel_store::{
        MemoryChannelBindingRepo, MemoryConversationSessionRepo, MemoryImParticipantRepo,
    };
    use bcs_channel_api::{
        ChannelInboundSink, ChannelProvider, ChannelProviderError, ChannelProviderRegistry,
        ChannelProviderResult,
    };
    use bcs_domain::{
        ActorKind, BindingStatus, BindingTarget, BotCapabilities, BotDynamicStatus,
        ChannelBinding, ChannelConfig, ChannelType, Group, GroupChatScope, Participant,
        ParticipantMode, ParticipantRole, RegisteredBot, Session, SessionKind, SessionScope,
        SessionStatus, Skill, StateMachineRun, StateMachineRunStatus, Visibility,
    };
    use bcs_service_api::lifecycle::ServiceLifecycle;
    use bcs_service_api::application::channel::{
        ChannelInboundError, ChannelInboundFailureKind, ChannelService, ChannelUseCaseError,
        CreateBindingCommand, InboundMessage, OutboundMessage,
    };
    use bcs_service_api::application::collaboration_runtime::{
        CancelStateMachineRunCommand, CollaborationRuntimeError, ConfigureGroupRuntimeCommand,
        ConfigureGroupRuntimeOutcome, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome,
        StartStateMachineRunCommand, StartStateMachineRunOutcome, StateMachineRunView,
    };
    use bcs_service_api::application::group_message::SessionHistoryResult;
    use bcs_service_api::application::message_flow::{
        BotEventCommand, BotEventOutcome, ChatAbortCommand, ChatAbortOutcome, GroupCallbackCommand,
        GroupCallbackOutcome, GroupChatCommand, GroupChatOutcome, MessageDeliveryResult,
        MessageFlowService, PersistentGroupSendCommand, PersistentGroupSendOutcome,
        TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome,
        TaskRunAliasRegistration, WebSendCommand, WebSendOutcome,
    };
    use bcs_service_api::core::{
        AgentCredentials, BotDeliveryTarget, BotRegistryCoreService, EnsureHumanResult,
        GroupCoreService, ServiceError, ServiceResult,
    };
    use bcs_service_api::CollaborationRuntimeService;
    use bcs_service_api::port::channel_delivery::{
        ChannelBindingRef, ChannelDeliveryPort, ChannelDeliveryResult, ChannelOutboundEvent,
        ChannelOutboundEventKind, ChannelRenderHint,
    };
    use bcs_service_api::port::repo::{
        ChannelBindingRepoPort, ConversationSessionRepoPort, ImParticipantRepoPort,
        NewSessionParams, SessionRepoPort,
    };

    use crate::{
        BcsChannelService, ResolvedInboundContext, channel_meta, channel_owned_group_id,
    };

    type TestResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;

    struct PanicOnListBindingRepo {
        inner: Arc<MemoryChannelBindingRepo>,
    }

    impl PanicOnListBindingRepo {
        fn new(inner: Arc<MemoryChannelBindingRepo>) -> Self {
            Self { inner }
        }
    }

    #[async_trait]
    impl ChannelBindingRepoPort for PanicOnListBindingRepo {
        async fn create(&self, binding: ChannelBinding) -> ServiceResult<()> {
            self.inner.create(binding).await
        }

        async fn get(&self, id: &str) -> ServiceResult<Option<ChannelBinding>> {
            self.inner.get(id).await
        }

        async fn find_active_by_account(
            &self,
            channel_type: ChannelType,
            account_ref: &str,
        ) -> ServiceResult<Option<ChannelBinding>> {
            self.inner
                .find_active_by_account(channel_type, account_ref)
                .await
        }

        async fn list(&self) -> ServiceResult<Vec<ChannelBinding>> {
            panic!("outbound delivery must not scan all channel bindings")
        }

        async fn set_status(&self, id: &str, active: bool) -> ServiceResult<()> {
            self.inner.set_status(id, active).await
        }

        async fn set_config(&self, id: &str, config: serde_json::Value) -> ServiceResult<()> {
            self.inner.set_config(id, config).await
        }

        async fn delete(&self, id: &str) -> ServiceResult<()> {
            self.inner.delete(id).await
        }
    }

    struct FailingBindingLookupRepo;

    #[async_trait]
    impl ChannelBindingRepoPort for FailingBindingLookupRepo {
        async fn create(&self, _binding: ChannelBinding) -> ServiceResult<()> {
            unreachable!("inbound binding lookup test only calls find_active_by_account")
        }

        async fn get(&self, _id: &str) -> ServiceResult<Option<ChannelBinding>> {
            unreachable!("inbound binding lookup test only calls find_active_by_account")
        }

        async fn find_active_by_account(
            &self,
            _channel_type: ChannelType,
            _account_ref: &str,
        ) -> ServiceResult<Option<ChannelBinding>> {
            Err(ServiceError::InternalError("binding lookup failed".to_string()))
        }

        async fn list(&self) -> ServiceResult<Vec<ChannelBinding>> {
            unreachable!("inbound binding lookup test only calls find_active_by_account")
        }

        async fn set_status(&self, _id: &str, _active: bool) -> ServiceResult<()> {
            unreachable!("inbound binding lookup test only calls find_active_by_account")
        }

        async fn set_config(&self, _id: &str, _config: serde_json::Value) -> ServiceResult<()> {
            unreachable!("inbound binding lookup test only calls find_active_by_account")
        }

        async fn delete(&self, _id: &str) -> ServiceResult<()> {
            unreachable!("inbound binding lookup test only calls find_active_by_account")
        }
    }

    struct FailingParticipantRepo;

    #[async_trait]
    impl ImParticipantRepoPort for FailingParticipantRepo {
        async fn get(
            &self,
            _channel_type: ChannelType,
            _account_ref: &str,
            _im_user_id: &str,
        ) -> ServiceResult<Option<bcs_domain::ImParticipantMap>> {
            unreachable!("inbound actor test only writes the participant mapping")
        }

        async fn upsert(&self, _map: bcs_domain::ImParticipantMap) -> ServiceResult<()> {
            Err(ServiceError::InternalError("actor write failed".to_string()))
        }
    }

    #[derive(Clone, Default)]
    struct SharedLogBuffer(Arc<std::sync::Mutex<Vec<u8>>>);

    impl SharedLogBuffer {
        fn clear(&self) {
            self.0.lock().unwrap().clear();
        }

        fn contents(&self) -> String {
            String::from_utf8(self.0.lock().unwrap().clone()).unwrap()
        }
    }

    struct SharedLogWriter {
        buffer: Arc<std::sync::Mutex<Vec<u8>>>,
    }

    impl Write for SharedLogWriter {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            self.buffer.lock().unwrap().extend_from_slice(buf);
            Ok(buf.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedLogBuffer {
        type Writer = SharedLogWriter;

        fn make_writer(&'a self) -> Self::Writer {
            SharedLogWriter {
                buffer: self.0.clone(),
            }
        }
    }

    fn tracing_capture_lock() -> &'static Mutex<()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
    }

    fn tracing_log_buffer() -> &'static SharedLogBuffer {
        static BUFFER: OnceLock<SharedLogBuffer> = OnceLock::new();
        BUFFER.get_or_init(SharedLogBuffer::default)
    }

    fn ensure_tracing_subscriber() {
        static INIT: Once = Once::new();
        INIT.call_once(|| {
            let subscriber = tracing_subscriber::fmt()
                .with_ansi(false)
                .with_level(false)
                .with_target(true)
                .with_writer(tracing_log_buffer().clone())
                .finish();
            let _ = tracing::subscriber::set_global_default(subscriber);
        });
    }

    async fn capture_tracing_logs<Fut, T>(future: Fut) -> (T, String)
    where
        Fut: Future<Output = T>,
    {
        let _capture_guard = tracing_capture_lock().lock().await;
        ensure_tracing_subscriber();
        let buffer = tracing_log_buffer();
        buffer.clear();
        let output = future.await;
        let logs = buffer.contents();
        (output, logs)
    }

    struct TestHarness {
        service: BcsChannelService,
        binding_repo: Arc<MemoryChannelBindingRepo>,
        conversation_repo: Arc<MemoryConversationSessionRepo>,
        participant_repo: Arc<MemoryImParticipantRepo>,
        session_repo: Arc<RecordingSessionRepo>,
        registry: Arc<RecordingRegistry>,
        message_flow: Arc<RecordingMessageFlow>,
        provider: Arc<RecordingProvider>,
        delivery: Arc<RecordingDelivery>,
        collaboration_runtime: Arc<RecordingCollaborationRuntime>,
    }

    impl TestHarness {
        async fn new(group: Group) -> ServiceResult<Self> {
            Self::new_with_env(group, "pre").await
        }

        async fn new_with_env(group: Group, env: &str) -> ServiceResult<Self> {
            Self::new_with_env_and_id(
                group,
                env,
                Arc::new(|| "generated_id".to_string()),
            )
            .await
        }

        async fn new_with_generated_id(
            group: Group,
            generated_id: String,
        ) -> ServiceResult<Self> {
            Self::new_with_env_and_id(
                group,
                "pre",
                Arc::new(move || generated_id.clone()),
            )
            .await
        }

        async fn new_with_env_and_id(
            group: Group,
            env: &str,
            new_id: Arc<dyn Fn() -> String + Send + Sync>,
        ) -> ServiceResult<Self> {
            let binding_repo = Arc::new(MemoryChannelBindingRepo::new());
            let conversation_repo = Arc::new(MemoryConversationSessionRepo::new());
            let participant_repo = Arc::new(MemoryImParticipantRepo::new());
            let session_repo = Arc::new(RecordingSessionRepo::default());
            let groups = Arc::new(bcs_group::GroupCore::memory());
            groups.upsert(group).await?;
            let registry = Arc::new(RecordingRegistry::default());
            let message_flow = Arc::new(RecordingMessageFlow::default());
            let delivery = Arc::new(RecordingDelivery::default());
            let provider = Arc::new(RecordingProvider::new(delivery.clone()));
            let providers = Arc::new(
                ChannelProviderRegistry::new(vec![provider.clone()])
                    .expect("test provider registry"),
            );
            let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::default());
            let service = BcsChannelService::new(
                binding_repo.clone(),
                conversation_repo.clone(),
                participant_repo.clone(),
                session_repo.clone(),
                message_flow.clone(),
                collaboration_runtime.clone(),
                groups,
                registry.clone(),
                providers,
                env,
                Arc::new(|| 42),
                new_id,
            );

            Ok(Self {
                service,
                binding_repo,
                conversation_repo,
                participant_repo,
                session_repo,
                registry,
                message_flow,
                provider,
                delivery,
                collaboration_runtime,
            })
        }

        async fn new_without_binding_list(group: Group) -> ServiceResult<Self> {
            let binding_repo = Arc::new(MemoryChannelBindingRepo::new());
            let conversation_repo = Arc::new(MemoryConversationSessionRepo::new());
            let participant_repo = Arc::new(MemoryImParticipantRepo::new());
            let session_repo = Arc::new(RecordingSessionRepo::default());
            let groups = Arc::new(bcs_group::GroupCore::memory());
            groups.upsert(group).await?;
            let registry = Arc::new(RecordingRegistry::default());
            let message_flow = Arc::new(RecordingMessageFlow::default());
            let delivery = Arc::new(RecordingDelivery::default());
            let provider = Arc::new(RecordingProvider::new(delivery.clone()));
            let providers = Arc::new(
                ChannelProviderRegistry::new(vec![provider.clone()])
                    .expect("test provider registry"),
            );
            let collaboration_runtime = Arc::new(RecordingCollaborationRuntime::default());
            let service = BcsChannelService::new(
                Arc::new(PanicOnListBindingRepo::new(binding_repo.clone())),
                conversation_repo.clone(),
                participant_repo.clone(),
                session_repo.clone(),
                message_flow.clone(),
                collaboration_runtime.clone(),
                groups,
                registry.clone(),
                providers,
                "pre",
                Arc::new(|| 42),
                Arc::new(|| "generated_id".to_string()),
            );

            Ok(Self {
                service,
                binding_repo,
                conversation_repo,
                participant_repo,
                session_repo,
                registry,
                message_flow,
                provider,
                delivery,
                collaboration_runtime,
            })
        }
    }

    async fn inbound_service(
        bindings: Arc<dyn ChannelBindingRepoPort>,
        im_participants: Arc<dyn ImParticipantRepoPort>,
        sessions: Arc<dyn SessionRepoPort>,
        message_flow: Arc<dyn MessageFlowService>,
        registry: Arc<dyn BotRegistryCoreService>,
    ) -> BcsChannelService {
        let groups = Arc::new(bcs_group::GroupCore::memory());
        groups
            .upsert(manager_group("group_1"))
            .await
            .expect("inbound test group");
        BcsChannelService::new(
            bindings,
            Arc::new(MemoryConversationSessionRepo::new()),
            im_participants,
            sessions,
            message_flow,
            Arc::new(RecordingCollaborationRuntime::default()),
            groups,
            registry,
            Arc::new(ChannelProviderRegistry::empty()),
            "pre",
            Arc::new(|| 42),
            Arc::new(|| "generated_id".to_string()),
        )
    }

    async fn active_inbound_binding_repo() -> Arc<MemoryChannelBindingRepo> {
        let bindings = Arc::new(MemoryChannelBindingRepo::new());
        bindings
            .create(active_binding(
                "binding_1",
                "robot_1",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await
            .expect("active inbound binding");
        bindings
    }

    fn assert_inbound_error(
        error: ChannelInboundError,
        kind: ChannelInboundFailureKind,
        retryable: bool,
        diagnostic: &str,
    ) {
        assert_eq!(error.kind, kind);
        assert_eq!(error.retryable, retryable);
        assert!(error.diagnostic.contains(diagnostic));
    }

    #[tokio::test]
    async fn create_binding_rejects_empty_account_and_target_refs() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;

        let empty_account = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: " ".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await;
        assert!(empty_account.is_err());

        let ignored_client_env = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: " ".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await;
        assert!(ignored_client_env.is_ok());

        let empty_group = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: " ".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await;
        assert!(empty_group.is_err());

        Ok(())
    }

    #[tokio::test]
    async fn create_binding_uses_service_runtime_env() -> TestResult {
        let harness = TestHarness::new_with_env(manager_group("group_1"), "pre").await?;

        let binding = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "local".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;

        assert_eq!(binding.env, "pre");
        assert_eq!(
            harness.binding_repo.get("generated_id").await?.unwrap().env,
            "pre"
        );

        Ok(())
    }

    #[tokio::test]
    async fn create_direct_bot_binding_defaults_group_scope_to_per_sender() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;

        let binding = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Bot {
                    bot_id: "target_bot".to_string(),
                },
                group_chat_scope: None,
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;

        assert_eq!(binding.group_chat_scope, Some(GroupChatScope::PerSender));

        Ok(())
    }

    #[tokio::test]
    async fn create_direct_bot_binding_rejects_overlong_generated_session_id_before_persisting(
    ) -> TestResult {
        let harness = TestHarness::new_with_generated_id(
            manager_group("group_1"),
            "x".repeat(55),
        )
        .await?;

        let result = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Bot {
                    bot_id: "target_bot".to_string(),
                },
                group_chat_scope: None,
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await;

        assert!(matches!(
            result,
            Err(ChannelUseCaseError::Internal(ServiceError::InternalError(_)))
        ));
        assert!(harness.binding_repo.list().await?.is_empty());

        Ok(())
    }

    #[test]
    fn channel_owned_group_id_rejects_overlong_session_id() {
        let result = channel_owned_group_id("dingtalk", &"x".repeat(55));

        assert!(result.is_err());
    }

    #[tokio::test]
    async fn create_binding_reports_empty_service_env_as_internal_error() -> TestResult {
        let harness = TestHarness::new_with_env(manager_group("group_1"), " ").await?;

        let error = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "local".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await
            .expect_err("empty service env should fail");

        match error {
            ChannelUseCaseError::Internal(ServiceError::InternalError(message)) => {
                assert!(message.contains("server environment"));
            }
            other => panic!("expected internal service env error, got {other:?}"),
        }

        Ok(())
    }

    #[tokio::test]
    async fn create_binding_rejects_duplicate_active_account_and_provider_invalid_config(
    ) -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;

        harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;

        let duplicate = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await;
        assert!(duplicate.is_err());

        let invalid_config = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_2".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: invalid_provider_config("robot_2"),
            })
            .await;
        assert!(invalid_config.is_err());
        assert_eq!(harness.provider.validate_call_count(), 3);

        let unknown_provider = harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: "missing".to_string(),
                account_ref: "robot_4".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("different_robot"),
            })
            .await;
        assert!(unknown_provider.is_err());

        Ok(())
    }

    #[tokio::test]
    async fn enabling_binding_rejects_duplicate_active_account() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .binding_repo
            .create(active_binding(
                "binding_active",
                "robot_1",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;
        let mut disabled = active_binding(
            "binding_disabled",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        );
        disabled.status = BindingStatus::Disabled;
        harness.binding_repo.create(disabled).await?;

        let enable = harness
            .service
            .set_binding_status("binding_disabled", true)
            .await;
        assert!(enable.is_err());

        Ok(())
    }

    #[tokio::test]
    async fn list_bindings_redacts_provider_config() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .binding_repo
            .create(active_binding(
                "binding_1",
                "robot_1",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;

        let bindings = harness.service.list_bindings().await?;
        assert_eq!(bindings.len(), 1);
        assert_eq!(bindings[0].config["client_secret"], "<redacted>");

        let stored = harness
            .binding_repo
            .get("binding_1")
            .await?
            .expect("stored binding");
        assert_eq!(stored.config["client_secret"], "secret");

        Ok(())
    }

    #[tokio::test]
    async fn update_binding_config_replaces_existing_provider_config() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .binding_repo
            .create(active_binding(
                "binding_1",
                "robot_1",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;

        let next_config = serde_json::json!({
            "robot_code": "robot_1",
            "client_id": "client_id",
            "client_secret": "secret",
            "valid": true,
            "send_mode": {
                "mode": "streaming_card",
                "card_template_id": "card_tpl_123"
            }
        });

        harness
            .service
            .update_binding_config("binding_1", next_config)
            .await?;

        let binding = harness
            .binding_repo
            .get("binding_1")
            .await?
            .expect("binding exists");
        assert_eq!(binding.config["send_mode"]["mode"], "streaming_card");
        assert_eq!(
            binding.config["send_mode"]["card_template_id"],
            "card_tpl_123"
        );

        Ok(())
    }

    #[tokio::test]
    async fn update_binding_config_rejects_provider_invalid_config() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .binding_repo
            .create(active_binding(
                "binding_1",
                "robot_1",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;

        let result = harness
            .service
            .update_binding_config("binding_1", invalid_provider_config("robot_1"))
            .await;

        assert!(result.is_err());

        Ok(())
    }

    #[tokio::test]
    async fn inbound_manager_worker_materializes_human_and_isolates_dm_conversations(
    ) -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;

        harness
            .service
            .handle_inbound(inbound("conv_a", "u1", Some("张三"), "msg_a"))
            .await?;
        harness
            .service
            .handle_inbound(inbound("conv_b", "u2", None, "msg_b"))
            .await?;

        let ensured = harness.registry.ensured.lock().await.clone();
        assert_eq!(
            ensured,
            vec![
                ("u1".to_string(), "张三".to_string()),
                ("u2".to_string(), "u2".to_string()),
            ]
        );

        let u1 = harness
            .participant_repo
            .get(channel_type(), "robot_1", "u1")
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing u1 participant".to_string()))?;
        assert_eq!(u1.actor_id, "human_u1");
        assert_eq!(u1.display_name.as_deref(), Some("张三"));

        let conv_a = harness
            .conversation_repo
            .get("generated_id", "conv_a", SessionScope::Conversation, Some("u1"))
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing conv_a".to_string()))?;
        let conv_b = harness
            .conversation_repo
            .get("generated_id", "conv_b", SessionScope::Conversation, Some("u2"))
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing conv_b".to_string()))?;
        assert_ne!(conv_a.bcs_session_id, conv_b.bcs_session_id);

        let web_sends = harness.message_flow.web_sends.lock().await;
        assert_eq!(web_sends.len(), 2);
        assert_eq!(web_sends[0].from_actor_id, "human_u1");
        assert_eq!(web_sends[0].session_id.as_deref(), Some(conv_a.bcs_session_id.as_str()));

        let added = harness.session_repo.added_participants.lock().await;
        assert_eq!(added.len(), 2);
        assert_eq!(added[0].1.bot_uuid, "human_u1");
        assert_eq!(added[0].1.mode, Some(ParticipantMode::Present));

        Ok(())
    }

    #[tokio::test(flavor = "current_thread")]
    async fn inbound_chat_logs_binding_actor_session_and_dispatch() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;

        let (result, logs) = capture_tracing_logs(async {
            harness
                .service
                .handle_inbound(inbound("conv_a", "u1", Some("张三"), "msg_a"))
                .await
        })
        .await;
        result?;

        for expected in [
            "channel inbound: received",
            "channel inbound: binding resolved",
            "channel inbound: actor resolved",
            "channel inbound: session resolved",
            "channel inbound: dispatched",
            "msg_id=msg_a",
            "binding_id=generated_id",
            "actor_id=human_u1",
            "bcs_session_id=group_1:00000001",
        ] {
            assert!(
                logs.contains(expected),
                "expected log fragment {expected:?}, got:\n{logs}"
            );
        }

        Ok(())
    }

    #[tokio::test]
    async fn single_chat_outbound_preserves_im_user_for_delivery() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;
        harness
            .service
            .handle_inbound(inbound("conv_a", "u1", Some("张三"), "msg_a"))
            .await?;
        let conv = harness
            .conversation_repo
            .get("generated_id", "conv_a", SessionScope::Conversation, Some("u1"))
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing conv_a".to_string()))?;

        harness
            .service
            .try_outbound(outbound(
                &conv.bcs_session_id,
                ParticipantRole::Worker,
                false,
            ))
            .await?;

        let events = harness.delivery.events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].im_conversation_id, "conv_a");
        assert_eq!(events[0].im_conversation_type, "1");
        assert_eq!(events[0].im_user_id.as_deref(), Some("u1"));
        assert_eq!(events[0].im_user_display_name.as_deref(), Some("张三"));

        Ok(())
    }

    #[tokio::test]
    async fn direct_bot_single_chat_session_includes_target_bot_and_human() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Bot {
                    bot_id: "target_bot".to_string(),
                },
                group_chat_scope: None,
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;

        harness
            .service
            .handle_inbound(inbound("conv_direct", "u1", Some("张三"), "msg_direct"))
            .await?;

        let conv = harness
            .conversation_repo
            .get("generated_id", "conv_direct", SessionScope::Conversation, Some("u1"))
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing direct conversation".to_string()))?;
        let session = harness
            .session_repo
            .get(&conv.bcs_session_id)
            .await
            .ok_or_else(|| ServiceError::InternalError("missing direct session".to_string()))?;

        assert_eq!(session.participants.len(), 2);
        assert!(session.participants.iter().any(|participant| {
            participant.bot_uuid == "target_bot"
                && participant.actor_kind == ActorKind::Bot
                && participant.mode == Some(ParticipantMode::Auto)
        }));
        assert!(session.participants.iter().any(|participant| {
            participant.bot_uuid == "human_u1"
                && participant.actor_kind == ActorKind::Human
                && participant.mode == Some(ParticipantMode::Present)
        }));

        Ok(())
    }

    #[tokio::test]
    async fn inbound_ignores_unmentioned_group_messages_and_duplicate_msg_ids() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;

        harness
            .service
            .handle_inbound(group_inbound("conv_group", "u1", Some("张三"), "msg_ignored", false))
            .await?;
        assert!(harness.registry.ensured.lock().await.is_empty());
        assert!(harness.message_flow.web_sends.lock().await.is_empty());

        harness
            .service
            .handle_inbound(group_inbound("conv_group", "u1", Some("张三"), " ", true))
            .await?;
        assert!(harness.registry.ensured.lock().await.is_empty());
        assert!(harness.message_flow.web_sends.lock().await.is_empty());

        let first = group_inbound("conv_group", "u1", Some("张三"), "msg_once", true);
        harness.service.handle_inbound(first.clone()).await?;
        harness.service.handle_inbound(first).await?;

        assert_eq!(harness.registry.ensured.lock().await.len(), 1);
        assert_eq!(harness.message_flow.web_sends.lock().await.len(), 1);

        Ok(())
    }

    #[tokio::test]
    async fn inbound_classifies_missing_binding_and_lookup_failure() -> TestResult {
        let missing_binding = inbound_service(
            Arc::new(MemoryChannelBindingRepo::new()),
            Arc::new(MemoryImParticipantRepo::new()),
            Arc::new(RecordingSessionRepo::default()),
            Arc::new(RecordingMessageFlow::default()),
            Arc::new(RecordingRegistry::default()),
        )
        .await;

        let error = missing_binding
            .handle_inbound(inbound("conv_missing", "u1", Some("张三"), "msg_missing"))
            .await
            .expect_err("missing binding must be reported");
        assert_inbound_error(
            error,
            ChannelInboundFailureKind::BindingNotFound,
            false,
            "active binding",
        );

        let lookup_failure = inbound_service(
            Arc::new(FailingBindingLookupRepo),
            Arc::new(MemoryImParticipantRepo::new()),
            Arc::new(RecordingSessionRepo::default()),
            Arc::new(RecordingMessageFlow::default()),
            Arc::new(RecordingRegistry::default()),
        )
        .await;

        let error = lookup_failure
            .handle_inbound(inbound("conv_lookup", "u1", Some("张三"), "msg_lookup"))
            .await
            .expect_err("binding lookup failure must be reported");
        assert_inbound_error(
            error,
            ChannelInboundFailureKind::BindingLookupFailed,
            true,
            "binding lookup failed",
        );

        Ok(())
    }

    #[tokio::test]
    async fn inbound_classifies_actor_participant_session_and_dispatch_failures() -> TestResult {
        let bindings = active_inbound_binding_repo().await;
        let registry = Arc::new(RecordingRegistry::default());
        *registry.fail_ensure_human.lock().await = Some("actor write failed".to_string());
        let actor_failure = inbound_service(
            bindings,
            Arc::new(MemoryImParticipantRepo::new()),
            Arc::new(RecordingSessionRepo::default()),
            Arc::new(RecordingMessageFlow::default()),
            registry,
        )
        .await;

        let error = actor_failure
            .handle_inbound(inbound("conv_actor", "u1", Some("张三"), "msg_actor"))
            .await
            .expect_err("actor write failure must be reported");
        assert_inbound_error(
            error,
            ChannelInboundFailureKind::ActorResolutionFailed,
            true,
            "actor write failed",
        );

        let participant_failure = inbound_service(
            active_inbound_binding_repo().await,
            Arc::new(FailingParticipantRepo),
            Arc::new(RecordingSessionRepo::default()),
            Arc::new(RecordingMessageFlow::default()),
            Arc::new(RecordingRegistry::default()),
        )
        .await;

        let error = participant_failure
            .handle_inbound(inbound("conv_participant", "u1", Some("张三"), "msg_participant"))
            .await
            .expect_err("participant mapping failure must be reported");
        assert_inbound_error(
            error,
            ChannelInboundFailureKind::ActorResolutionFailed,
            true,
            "actor write failed",
        );

        let session_repo = Arc::new(RecordingSessionRepo::default());
        *session_repo.fail_create.lock().await = Some("session create failed".to_string());
        let session_failure = inbound_service(
            active_inbound_binding_repo().await,
            Arc::new(MemoryImParticipantRepo::new()),
            session_repo,
            Arc::new(RecordingMessageFlow::default()),
            Arc::new(RecordingRegistry::default()),
        )
        .await;

        let error = session_failure
            .handle_inbound(inbound("conv_session", "u1", Some("张三"), "msg_session"))
            .await
            .expect_err("session failure must be reported");
        assert_inbound_error(
            error,
            ChannelInboundFailureKind::SessionResolutionFailed,
            true,
            "session create failed",
        );

        let message_flow = Arc::new(RecordingMessageFlow::default());
        *message_flow.failed_dispatch_count.lock().await = 1;
        let dispatch_failure = inbound_service(
            active_inbound_binding_repo().await,
            Arc::new(MemoryImParticipantRepo::new()),
            Arc::new(RecordingSessionRepo::default()),
            message_flow,
            Arc::new(RecordingRegistry::default()),
        )
        .await;

        let error = dispatch_failure
            .handle_inbound(inbound("conv_dispatch", "u1", Some("张三"), "msg_dispatch"))
            .await
            .expect_err("failed message flow dispatch must be reported");
        assert_inbound_error(
            error,
            ChannelInboundFailureKind::DispatchFailed,
            true,
            "failed deliveries",
        );

        Ok(())
    }

    #[tokio::test]
    async fn inbound_returns_error_when_session_participant_write_fails() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;
        *harness.session_repo.fail_add_participant.lock().await =
            Some("participant write failed".to_string());

        let result = harness
            .service
            .handle_inbound(group_inbound("conv_group", "u1", Some("张三"), "msg_fail", true))
            .await;

        assert_inbound_error(
            result.expect_err("participant write failure must be reported"),
            ChannelInboundFailureKind::SessionResolutionFailed,
            true,
            "participant write failed",
        );
        assert!(harness.message_flow.web_sends.lock().await.is_empty());

        Ok(())
    }

    #[tokio::test]
    async fn inbound_group_per_sender_scope_isolates_same_conversation() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::PerSender),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;

        harness
            .service
            .handle_inbound(group_inbound(
                "conv_group",
                "u1",
                Some("张三"),
                "msg_u1",
                true,
            ))
            .await?;
        harness
            .service
            .handle_inbound(group_inbound("conv_group", "u2", Some("李四"), "msg_u2", true))
            .await?;

        let u1 = harness
            .conversation_repo
            .get("generated_id", "conv_group", SessionScope::PerSender, Some("u1"))
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing u1 conversation".to_string()))?;
        let u2 = harness
            .conversation_repo
            .get("generated_id", "conv_group", SessionScope::PerSender, Some("u2"))
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing u2 conversation".to_string()))?;

        assert_ne!(u1.bcs_session_id, u2.bcs_session_id);
        assert_eq!(harness.message_flow.web_sends.lock().await.len(), 2);

        Ok(())
    }

    #[tokio::test]
    async fn direct_bot_group_chat_uses_bounded_channel_session_id() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        let binding_id = "0d86bd1b-6efd-4b5c-8906-1be1b5717c74";
        let mut binding = active_binding(
            binding_id,
            "robot_1",
            BindingTarget::Bot {
                bot_id: "20260625_fkxorj0t:410025".to_string(),
            },
            Visibility::FullTranscript,
        );
        binding.group_chat_scope = Some(GroupChatScope::PerSender);
        harness.binding_repo.create(binding).await?;

        harness
            .service
            .handle_inbound(group_inbound("conv_group", "u1", Some("张三"), "msg_u1", true))
            .await?;

        let mapped = harness
            .conversation_repo
            .get(binding_id, "conv_group", SessionScope::PerSender, Some("u1"))
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing conversation".to_string()))?;
        assert!(mapped.bcs_session_id.starts_with("dingtalk_"));
        assert!(mapped.bcs_session_id.len() <= 64);

        Ok(())
    }

    #[tokio::test]
    async fn direct_bot_group_chat_defaults_to_per_sender_scope() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        let binding_id = "binding_bot_default_scope";
        let mut binding = active_binding(
            binding_id,
            "robot_1",
            BindingTarget::Bot {
                bot_id: "target_bot".to_string(),
            },
            Visibility::FullTranscript,
        );
        binding.group_chat_scope = None;
        harness.binding_repo.create(binding).await?;

        harness
            .service
            .handle_inbound(group_inbound(
                "conv_group",
                "u1",
                Some("张三"),
                "msg_u1",
                true,
            ))
            .await?;
        harness
            .service
            .handle_inbound(group_inbound(
                "conv_group",
                "u2",
                Some("李四"),
                "msg_u2",
                true,
            ))
            .await?;

        let u1 = harness
            .conversation_repo
            .get(binding_id, "conv_group", SessionScope::PerSender, Some("u1"))
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing u1 conversation".to_string()))?;
        let u2 = harness
            .conversation_repo
            .get(binding_id, "conv_group", SessionScope::PerSender, Some("u2"))
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing u2 conversation".to_string()))?;
        assert_ne!(u1.bcs_session_id, u2.bcs_session_id);

        Ok(())
    }

    #[tokio::test]
    async fn inbound_group_shared_scope_reuses_conversation_session_for_different_senders(
    ) -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;

        harness
            .service
            .handle_inbound(group_inbound("conv_group", "u1", Some("张三"), "msg_u1", true))
            .await?;
        harness
            .service
            .handle_inbound(group_inbound("conv_group", "u2", Some("李四"), "msg_u2", true))
            .await?;

        let shared = harness
            .conversation_repo
            .get("generated_id", "conv_group", SessionScope::Conversation, None)
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing shared conversation".to_string()))?;
        let per_sender_u1 = harness
            .conversation_repo
            .get("generated_id", "conv_group", SessionScope::PerSender, Some("u1"))
            .await?;
        assert_eq!(per_sender_u1, None);

        let web_sends = harness.message_flow.web_sends.lock().await;
        assert_eq!(web_sends.len(), 2);
        assert_eq!(
            web_sends[0].session_id.as_deref(),
            Some(shared.bcs_session_id.as_str())
        );
        assert_eq!(
            web_sends[1].session_id.as_deref(),
            Some(shared.bcs_session_id.as_str())
        );
        drop(web_sends);

        let ensured = harness.registry.ensured.lock().await.clone();
        assert_eq!(
            ensured,
            vec![
                ("u1".to_string(), "张三".to_string()),
                ("u2".to_string(), "李四".to_string()),
            ]
        );

        Ok(())
    }

    #[tokio::test]
    async fn inbound_state_machine_group_creates_service_invocation_and_starts_runtime(
    ) -> TestResult {
        let harness = TestHarness::new(state_machine_group("group_sm")).await?;
        harness
            .service
            .create_binding(CreateBindingCommand {
                channel_type: channel_type(),
                account_ref: "robot_1".to_string(),
                target: BindingTarget::Group {
                    group_id: "group_sm".to_string(),
                },
                group_chat_scope: Some(GroupChatScope::ConversationShared),
                outbound_visibility: Visibility::FullTranscript,
                env: "dev".to_string(),
                created_by: Some("creator".to_string()),
                config: dingtalk_config("robot_1"),
            })
            .await?;

        harness
            .service
            .handle_inbound(group_inbound("conv_sm", "u1", Some("张三"), "msg_sm", true))
            .await?;

        let starts = harness.collaboration_runtime.starts.lock().await.clone();
        assert_eq!(starts.len(), 1);
        assert_eq!(starts[0].group_id, "group_sm");
        assert_eq!(starts[0].caller_id.as_deref(), Some("human_u1"));
        assert_eq!(starts[0].input["source"], "dingtalk");
        assert_eq!(starts[0].input["sender"]["actor_id"], "human_u1");
        assert_eq!(starts[0].input["conversation"]["id"], "conv_sm");

        let session_id = starts[0]
            .session_id
            .as_deref()
            .ok_or_else(|| ServiceError::InternalError("missing runtime session_id".to_string()))?;
        let session = harness
            .session_repo
            .get(session_id)
            .await
            .ok_or_else(|| ServiceError::InternalError("missing service session".to_string()))?;
        assert_eq!(session.session_kind, SessionKind::ServiceInvocation);
        assert_eq!(session.caller_id.as_deref(), Some("human_u1"));
        assert_eq!(session.caller_principal.as_deref(), Some("dingtalk:conv_sm"));

        let channel = session
            .meta
            .as_ref()
            .and_then(|meta| meta.get("channel"))
            .ok_or_else(|| ServiceError::InternalError("missing channel meta".to_string()))?;
        assert_eq!(channel["binding_id"], "generated_id");
        assert_eq!(channel["conversation_id"], "conv_sm");
        assert_eq!(channel["session_scope"], "conversation");
        assert_eq!(channel["context_projection"], "group");
        assert_eq!(channel.get("im_user_id").and_then(|value| value.as_str()), None);

        let mapped = harness
            .conversation_repo
            .get("generated_id", "conv_sm", SessionScope::Conversation, None)
            .await?
            .ok_or_else(|| ServiceError::InternalError("missing state machine conversation".to_string()))?;
        assert_eq!(mapped.bcs_session_id, session_id);
        assert!(harness.message_flow.web_sends.lock().await.is_empty());
        assert!(harness.session_repo.added_participants.lock().await.is_empty());

        Ok(())
    }

    #[test]
    fn channel_meta_uses_inbound_channel_type_as_source() {
        let msg = InboundMessage {
            channel_type: "test-im".to_string(),
            account_ref: "robot_1".to_string(),
            im_conversation_id: "conv_meta".to_string(),
            conversation_type: "2".to_string(),
            im_user_id: "u1".to_string(),
            im_user_nick: Some("张三".to_string()),
            text: "hello".to_string(),
            is_at_bot: true,
            msg_id: "msg_meta".to_string(),
        };
        let meta = channel_meta(
            &ResolvedInboundContext {
                binding_id: "binding_1".to_string(),
                group_id: "group_1".to_string(),
                session_scope: SessionScope::Conversation,
                im_user_id: None,
                caller_principal: "test-im:conv_meta".to_string(),
                context_projection: "group",
                state_machine_trigger: false,
            },
            &msg,
        );

        assert_eq!(meta["channel"]["source"], "test-im");
    }

    #[tokio::test]
    async fn try_outbound_filters_visibility_and_sets_render_hint() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        let binding = active_binding(
            "binding_1",
            "robot_1",
            BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            Visibility::FullTranscript,
        );
        harness.binding_repo.create(binding).await?;
        harness
            .session_repo
            .create(
                "group_1",
                NewSessionParams {
                    id: Some("group_1:00000001".to_string()),
                    session_kind: SessionKind::Chat,
                    ..Default::default()
                },
            )
            .await?;
        harness
            .conversation_repo
            .upsert(bcs_domain::ConversationSessionMap {
                binding_id: "binding_1".to_string(),
                im_conversation_id: "conv_a".to_string(),
                im_conversation_type: "2".to_string(),
                session_scope: SessionScope::Conversation,
                im_user_id: None,
                bcs_session_id: "group_1:00000001".to_string(),
                last_active_at: 1,
            })
            .await?;

        harness
            .service
            .try_outbound(outbound(
                "group_1:00000001",
                ParticipantRole::Worker,
                false,
            ))
            .await?;
        let events = harness.delivery.events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].im_conversation_id, "conv_a");
        assert!(events[0].render_sender_label);
        drop(events);

        harness
            .service
            .try_outbound(outbound(
                "group_1:00000001",
                ParticipantRole::Manager,
                true,
            ))
            .await?;
        assert_eq!(harness.delivery.events.lock().await.len(), 1);

        Ok(())
    }

    #[tokio::test(flavor = "current_thread")]
    async fn try_outbound_logs_selected_binding_and_delivery_result() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .binding_repo
            .create(active_binding(
                "binding_1",
                "robot_1",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;
        harness
            .session_repo
            .create(
                "group_1",
                NewSessionParams {
                    id: Some("group_1:00000001".to_string()),
                    session_kind: SessionKind::Chat,
                    ..Default::default()
                },
            )
            .await?;
        harness
            .conversation_repo
            .upsert(bcs_domain::ConversationSessionMap {
                binding_id: "binding_1".to_string(),
                im_conversation_id: "conv_a".to_string(),
                im_conversation_type: "2".to_string(),
                session_scope: SessionScope::Conversation,
                im_user_id: None,
                bcs_session_id: "group_1:00000001".to_string(),
                last_active_at: 1,
            })
            .await?;

        let (result, logs) = capture_tracing_logs(async {
            harness
                .service
                .try_outbound(outbound(
                    "group_1:00000001",
                    ParticipantRole::Worker,
                    false,
                ))
                .await
        })
        .await;
        result?;

        for expected in [
            "channel outbound: selected",
            "channel outbound: delivered",
            "binding_id=binding_1",
            "bcs_session_id=group_1:00000001",
            "run_id=run_1",
            "im_conversation_id=conv_a",
        ] {
            assert!(
                logs.contains(expected),
                "expected log fragment {expected:?}, got:\n{logs}"
            );
        }

        Ok(())
    }

    #[tokio::test]
    async fn try_outbound_uses_session_mapping_instead_of_listing_bindings() -> TestResult {
        let harness = TestHarness::new_without_binding_list(manager_group("group_1")).await?;
        harness
            .binding_repo
            .create(active_binding(
                "binding_1",
                "robot_1",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;
        harness
            .session_repo
            .create(
                "group_1",
                NewSessionParams {
                    id: Some("group_1:00000001".to_string()),
                    session_kind: SessionKind::Chat,
                    ..Default::default()
                },
            )
            .await?;
        harness
            .conversation_repo
            .upsert(bcs_domain::ConversationSessionMap {
                binding_id: "binding_1".to_string(),
                im_conversation_id: "conv_a".to_string(),
                im_conversation_type: "2".to_string(),
                session_scope: SessionScope::Conversation,
                im_user_id: None,
                bcs_session_id: "group_1:00000001".to_string(),
                last_active_at: 1,
            })
            .await?;

        harness
            .service
            .try_outbound(outbound(
                "group_1:00000001",
                ParticipantRole::Worker,
                false,
            ))
            .await?;

        let events = harness.delivery.events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].im_conversation_id, "conv_a");

        Ok(())
    }

    #[tokio::test(flavor = "current_thread")]
    async fn try_outbound_logs_delivery_error_detail_when_not_confirmed() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .binding_repo
            .create(active_binding(
                "binding_1",
                "robot_1",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;
        harness
            .session_repo
            .create(
                "group_1",
                NewSessionParams {
                    id: Some("group_1:00000001".to_string()),
                    session_kind: SessionKind::Chat,
                    ..Default::default()
                },
            )
            .await?;
        harness
            .conversation_repo
            .upsert(bcs_domain::ConversationSessionMap {
                binding_id: "binding_1".to_string(),
                im_conversation_id: "conv_a".to_string(),
                im_conversation_type: "2".to_string(),
                session_scope: SessionScope::Conversation,
                im_user_id: None,
                bcs_session_id: "group_1:00000001".to_string(),
                last_active_at: 1,
            })
            .await?;
        *harness.delivery.fail_error.lock().await =
            Some("dingtalk normal message send returned status 400 code=invalidConversation".to_string());

        let (result, logs) = capture_tracing_logs(async {
            harness
                .service
                .try_outbound(outbound(
                    "group_1:00000001",
                    ParticipantRole::Worker,
                    false,
                ))
                .await
        })
        .await;
        result?;

        assert!(
            logs.contains("channel outbound: delivery not confirmed"),
            "expected delivery failure log, got:\n{logs}"
        );
        assert!(
            logs.contains("invalidConversation"),
            "expected delivery error detail in log, got:\n{logs}"
        );

        Ok(())
    }

    #[tokio::test]
    async fn try_outbound_continues_after_delivery_call_error() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .binding_repo
            .create(active_binding(
                "binding_failed",
                "robot_failed",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;
        harness
            .binding_repo
            .create(active_binding(
                "binding_healthy",
                "robot_healthy",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;
        harness
            .session_repo
            .create(
                "group_1",
                NewSessionParams {
                    id: Some("group_1:00000001".to_string()),
                    session_kind: SessionKind::Chat,
                    ..Default::default()
                },
            )
            .await?;
        for (binding_id, conversation_id) in [
            ("binding_failed", "conv_failed"),
            ("binding_healthy", "conv_healthy"),
        ] {
            harness
                .conversation_repo
                .upsert(bcs_domain::ConversationSessionMap {
                    binding_id: binding_id.to_string(),
                    im_conversation_id: conversation_id.to_string(),
                    im_conversation_type: "2".to_string(),
                    session_scope: SessionScope::Conversation,
                    im_user_id: None,
                    bcs_session_id: "group_1:00000001".to_string(),
                    last_active_at: 1,
                })
                .await?;
        }
        *harness.delivery.call_error_account_ref.lock().await =
            Some("robot_failed".to_string());

        harness
            .service
            .try_outbound(outbound(
                "group_1:00000001",
                ParticipantRole::Worker,
                false,
            ))
            .await?;

        let events = harness.delivery.events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].binding_ref.account_ref, "robot_healthy");

        Ok(())
    }

    #[tokio::test]
    async fn try_outbound_skips_when_session_does_not_belong_to_group() -> TestResult {
        let harness = TestHarness::new(manager_group("group_1")).await?;
        harness
            .binding_repo
            .create(active_binding(
                "binding_1",
                "robot_1",
                BindingTarget::Group {
                    group_id: "group_1".to_string(),
                },
                Visibility::FullTranscript,
            ))
            .await?;
        harness
            .session_repo
            .create(
                "other_group",
                NewSessionParams {
                    id: Some("other_group:00000001".to_string()),
                    session_kind: SessionKind::Chat,
                    ..Default::default()
                },
            )
            .await?;
        harness
            .conversation_repo
            .upsert(bcs_domain::ConversationSessionMap {
                binding_id: "binding_1".to_string(),
                im_conversation_id: "conv_a".to_string(),
                im_conversation_type: "2".to_string(),
                session_scope: SessionScope::Conversation,
                im_user_id: None,
                bcs_session_id: "other_group:00000001".to_string(),
                last_active_at: 1,
            })
            .await?;

        harness
            .service
            .try_outbound(outbound(
                "other_group:00000001",
                ParticipantRole::Worker,
                false,
            ))
            .await?;

        assert!(harness.delivery.events.lock().await.is_empty());

        Ok(())
    }

    fn manager_group(id: &str) -> Group {
        let mut group = Group::new(
            id,
            "manager_bot",
            vec![
                Participant::bot("manager_bot", ParticipantRole::Manager),
                Participant::bot("worker_bot", ParticipantRole::Worker),
            ],
        );
        group.group_strategy = bcs_domain::GroupStrategy::ManagerWorker;
        group
    }

    fn state_machine_group(id: &str) -> Group {
        let mut group = manager_group(id);
        group.group_strategy = bcs_domain::GroupStrategy::StateMachine;
        group
    }

    fn dingtalk_config(account_ref: &str) -> ChannelConfig {
        serde_json::json!({
            "robot_code": account_ref,
            "client_id": "client_id",
            "client_secret": "secret",
            "valid": true,
            "send_mode": {
                "mode": "normal",
                "message_type": "markdown"
            }
        })
    }

    fn invalid_provider_config(account_ref: &str) -> ChannelConfig {
        let mut config = dingtalk_config(account_ref);
        config["valid"] = serde_json::json!(false);
        config
    }

    fn channel_type() -> ChannelType {
        "dingtalk".to_string()
    }

    fn active_binding(
        id: &str,
        account_ref: &str,
        target: BindingTarget,
        visibility: Visibility,
    ) -> ChannelBinding {
        ChannelBinding {
            id: id.to_string(),
            channel_type: channel_type(),
            account_ref: account_ref.to_string(),
            target,
            group_chat_scope: Some(GroupChatScope::ConversationShared),
            outbound_visibility: visibility,
            env: "dev".to_string(),
            status: BindingStatus::Active,
            created_by: Some("creator".to_string()),
            config: dingtalk_config(account_ref),
        }
    }

    fn inbound(
        conversation_id: &str,
        user_id: &str,
        user_nick: Option<&str>,
        msg_id: &str,
    ) -> InboundMessage {
        InboundMessage {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            im_conversation_id: conversation_id.to_string(),
            conversation_type: "1".to_string(),
            im_user_id: user_id.to_string(),
            im_user_nick: user_nick.map(str::to_string),
            text: "hello".to_string(),
            is_at_bot: true,
            msg_id: msg_id.to_string(),
        }
    }

    fn group_inbound(
        conversation_id: &str,
        user_id: &str,
        user_nick: Option<&str>,
        msg_id: &str,
        is_at_bot: bool,
    ) -> InboundMessage {
        InboundMessage {
            channel_type: channel_type(),
            account_ref: "robot_1".to_string(),
            im_conversation_id: conversation_id.to_string(),
            conversation_type: "2".to_string(),
            im_user_id: user_id.to_string(),
            im_user_nick: user_nick.map(str::to_string),
            text: "hello".to_string(),
            is_at_bot,
            msg_id: msg_id.to_string(),
        }
    }

    fn outbound(
        session_id: &str,
        sender_role: ParticipantRole,
        source_is_channel: bool,
    ) -> OutboundMessage {
        OutboundMessage {
            group_id: "group_1".to_string(),
            bcs_session_id: session_id.to_string(),
            run_id: "run_1".to_string(),
            sender_actor_id: "worker_bot".to_string(),
            sender_role,
            sender_label: "Worker".to_string(),
            kind: ChannelOutboundEventKind::ChatFinal,
            text: Some("done".to_string()),
            raw_payload: serde_json::json!({"type": "chat.final"}),
            render_hint: ChannelRenderHint::Render,
            source_is_channel,
        }
    }

    #[derive(Default)]
    struct RecordingSessionRepo {
        next: AtomicU64,
        sessions: Mutex<HashMap<String, Session>>,
        added_participants: Mutex<Vec<(String, Participant)>>,
        fail_create: Mutex<Option<String>>,
        fail_add_participant: Mutex<Option<String>>,
    }

    #[async_trait]
    impl SessionRepoPort for RecordingSessionRepo {
        async fn create(
            &self,
            group_id: &str,
            params: NewSessionParams,
        ) -> ServiceResult<Session> {
            if let Some(error) = self.fail_create.lock().await.clone() {
                return Err(ServiceError::InternalError(error));
            }
            let n = self.next.fetch_add(1, Ordering::SeqCst) + 1;
            let id = match params.id {
                Some(id) => id,
                None => format!("{group_id}:{n:08x}"),
            };
            let session = Session {
                id: id.clone(),
                group_id: group_id.to_string(),
                session_title: params.session_title,
                env: None,
                status: SessionStatus::Running,
                session_kind: params.session_kind,
                participants: params.participants,
                group_version: params.group_version,
                caller_id: params.caller_id,
                input: params.input,
                output: None,
                error_message: None,
                callback_status: None,
                activation_count: 1,
                caller_principal: params.caller_principal,
                created_by: params.created_by,
                current_msg_seq: 0,
                participant_join_seq: None,
                created_at: 1,
                updated_at: 1,
                completed_at: None,
                meta: params.meta,
            };
            self.sessions.lock().await.insert(id, session.clone());
            Ok(session)
        }

        async fn get(&self, session_id: &str) -> Option<Session> {
            self.sessions.lock().await.get(session_id).cloned()
        }

        async fn belongs_to_group(&self, session_id: &str, group_id: &str) -> bool {
            self.get(session_id)
                .await
                .is_some_and(|session| session.group_id == group_id)
        }

        async fn list_by_group(
            &self,
            group_id: &str,
            status: Option<SessionStatus>,
            _offset: u64,
            _limit: u64,
            _title_contains: Option<&str>,
            _participant_id: Option<&str>,
        ) -> Vec<Session> {
            self.sessions
                .lock()
                .await
                .values()
                .filter(|session| session.group_id == group_id)
                .filter(|session| status.is_none_or(|status| session.status == status))
                .cloned()
                .collect()
        }

        async fn latest_running(&self, group_id: &str) -> Option<Session> {
            self.list_by_group(group_id, Some(SessionStatus::Running), 0, 1, None, None)
                .await
                .into_iter()
                .next()
        }

        async fn count_running_service(&self, group_id: &str) -> u64 {
            self.list_by_group(group_id, Some(SessionStatus::Running), 0, u64::MAX, None, None)
                .await
                .into_iter()
                .filter(|session| session.session_kind == SessionKind::ServiceInvocation)
                .count() as u64
        }

        async fn list_running_service(&self, _offset: u64, _limit: u64) -> Vec<Session> {
            self.sessions
                .lock()
                .await
                .values()
                .filter(|session| {
                    session.status == SessionStatus::Running
                        && session.session_kind == SessionKind::ServiceInvocation
                })
                .cloned()
                .collect()
        }

        async fn complete_if_running(
            &self,
            session_id: &str,
            output: Option<serde_json::Value>,
            error: Option<String>,
        ) -> ServiceResult<Option<Session>> {
            let mut sessions = self.sessions.lock().await;
            let Some(session) = sessions.get_mut(session_id) else {
                return Err(ServiceError::SessionNotFound(session_id.to_string()));
            };
            if session.status == SessionStatus::Completed {
                return Ok(None);
            }
            session.status = SessionStatus::Completed;
            session.output = output;
            session.error_message = error;
            Ok(Some(session.clone()))
        }

        async fn reactivate(
            &self,
            session_id: &str,
            new_input: Option<serde_json::Value>,
        ) -> ServiceResult<Session> {
            let mut sessions = self.sessions.lock().await;
            let Some(session) = sessions.get_mut(session_id) else {
                return Err(ServiceError::SessionNotFound(session_id.to_string()));
            };
            session.status = SessionStatus::Running;
            session.input = new_input;
            Ok(session.clone())
        }

        async fn add_participant(
            &self,
            session_id: &str,
            participant: Participant,
        ) -> ServiceResult<Session> {
            if let Some(error) = self.fail_add_participant.lock().await.clone() {
                return Err(ServiceError::InternalError(error));
            }
            self.added_participants
                .lock()
                .await
                .push((session_id.to_string(), participant.clone()));
            let mut sessions = self.sessions.lock().await;
            let Some(session) = sessions.get_mut(session_id) else {
                return Err(ServiceError::SessionNotFound(session_id.to_string()));
            };
            if !session
                .participants
                .iter()
                .any(|existing| existing.bot_uuid == participant.bot_uuid)
            {
                session.participants.push(participant);
            }
            Ok(session.clone())
        }

        async fn remove_participant(
            &self,
            session_id: &str,
            bot_uuid: &str,
        ) -> ServiceResult<Session> {
            let mut sessions = self.sessions.lock().await;
            let Some(session) = sessions.get_mut(session_id) else {
                return Err(ServiceError::SessionNotFound(session_id.to_string()));
            };
            session
                .participants
                .retain(|participant| participant.bot_uuid != bot_uuid);
            Ok(session.clone())
        }

        async fn update_participant_mode(
            &self,
            session_id: &str,
            bot_uuid: &str,
            mode: ParticipantMode,
        ) -> ServiceResult<Session> {
            let mut sessions = self.sessions.lock().await;
            let Some(session) = sessions.get_mut(session_id) else {
                return Err(ServiceError::SessionNotFound(session_id.to_string()));
            };
            for participant in &mut session.participants {
                if participant.bot_uuid == bot_uuid {
                    participant.mode = Some(mode);
                }
            }
            Ok(session.clone())
        }

        async fn update_callback_status(
            &self,
            session_id: &str,
            status: &str,
        ) -> ServiceResult<()> {
            let mut sessions = self.sessions.lock().await;
            let Some(session) = sessions.get_mut(session_id) else {
                return Err(ServiceError::SessionNotFound(session_id.to_string()));
            };
            session.callback_status = Some(status.to_string());
            Ok(())
        }

        async fn update_title(
            &self,
            session_id: &str,
            title: Option<String>,
        ) -> ServiceResult<Session> {
            let mut sessions = self.sessions.lock().await;
            let Some(session) = sessions.get_mut(session_id) else {
                return Err(ServiceError::SessionNotFound(session_id.to_string()));
            };
            session.session_title = title;
            Ok(session.clone())
        }

        async fn list_group_ids_by_session_participant(&self, bot_uuid: &str) -> Vec<String> {
            self.sessions
                .lock()
                .await
                .values()
                .filter(|session| {
                    session
                        .participants
                        .iter()
                        .any(|participant| participant.bot_uuid == bot_uuid)
                })
                .map(|session| session.group_id.clone())
                .collect()
        }

        async fn delete(&self, session_id: &str) -> ServiceResult<bool> {
            Ok(self.sessions.lock().await.remove(session_id).is_some())
        }
    }

    #[derive(Default)]
    struct RecordingMessageFlow {
        web_sends: Mutex<Vec<WebSendCommand>>,
        failed_dispatch_count: Mutex<usize>,
    }

    #[async_trait]
    impl MessageFlowService for RecordingMessageFlow {
        async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
            self.web_sends.lock().await.push(cmd);
            let failed_count = *self.failed_dispatch_count.lock().await;
            Ok(WebSendOutcome {
                primary_run_id: "run_1".to_string(),
                status: "accepted".to_string(),
                active_run_ids: Vec::new(),
                bot_deliveries: Vec::new(),
                frontend_deliveries: Vec::new(),
                mentions: Vec::new(),
                hidden_mentions: Vec::new(),
                delivered_count: 0,
                failed_count,
                delivery_results: Vec::<MessageDeliveryResult>::new(),
            })
        }

        async fn handle_group_chat(&self, _cmd: GroupChatCommand) -> ServiceResult<GroupChatOutcome> {
            Err(not_configured("group chat"))
        }

        async fn handle_persistent_group_send(
            &self,
            _cmd: PersistentGroupSendCommand,
        ) -> ServiceResult<PersistentGroupSendOutcome> {
            Err(not_configured("persistent group send"))
        }

        async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
            Err(not_configured("bot event"))
        }

        async fn handle_group_callback(
            &self,
            _cmd: GroupCallbackCommand,
        ) -> ServiceResult<GroupCallbackOutcome> {
            Err(not_configured("group callback"))
        }

        async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
            Err(not_configured("chat abort"))
        }

        async fn register_task_run_alias(
            &self,
            _task_id: &str,
            _run_id: &str,
            _bot_id: &str,
        ) -> ServiceResult<TaskRunAliasRegistration> {
            Ok(TaskRunAliasRegistration::NotTask)
        }

        async fn handle_task_dispatch(
            &self,
            _cmd: TaskDispatchCommand,
        ) -> ServiceResult<TaskDispatchOutcome> {
            Err(not_configured("task dispatch"))
        }

        async fn handle_task_complete(
            &self,
            _cmd: TaskCompleteCommand,
        ) -> ServiceResult<TaskCompleteOutcome> {
            Err(not_configured("task complete"))
        }
    }

    #[derive(Default)]
    struct RecordingDelivery {
        events: Mutex<Vec<ChannelOutboundEvent>>,
        fail_error: Mutex<Option<String>>,
        call_error_account_ref: Mutex<Option<String>>,
    }

    #[async_trait]
    impl ChannelDeliveryPort for RecordingDelivery {
        async fn is_available(&self, _binding: &ChannelBindingRef) -> bool {
            true
        }

        async fn deliver_event(
            &self,
            event: ChannelOutboundEvent,
        ) -> ServiceResult<ChannelDeliveryResult> {
            if self
                .call_error_account_ref
                .lock()
                .await
                .as_deref()
                .is_some_and(|account_ref| account_ref == event.binding_ref.account_ref)
            {
                return Err(ServiceError::InternalError(
                    "simulated channel delivery call failure".to_string(),
                ));
            }
            self.events.lock().await.push(event);
            if let Some(error) = self.fail_error.lock().await.clone() {
                return Ok(ChannelDeliveryResult {
                    delivered: false,
                    error: Some(ServiceError::InternalError(error)),
                });
            }
            Ok(ChannelDeliveryResult {
                delivered: true,
                error: None,
            })
        }
    }

    struct RecordingProvider {
        delivery: Arc<RecordingDelivery>,
        validate_calls: std::sync::Mutex<Vec<serde_json::Value>>,
    }

    impl RecordingProvider {
        fn new(delivery: Arc<RecordingDelivery>) -> Self {
            Self {
                delivery,
                validate_calls: std::sync::Mutex::new(Vec::new()),
            }
        }

        fn validate_call_count(&self) -> usize {
            self.validate_calls.lock().unwrap().len()
        }
    }

    #[async_trait]
    impl ChannelProvider for RecordingProvider {
        fn channel_type(&self) -> &'static str {
            "dingtalk"
        }

        fn validate_config(&self, config: &serde_json::Value) -> ChannelProviderResult<()> {
            self.validate_calls.lock().unwrap().push(config.clone());
            if config
                .get("valid")
                .and_then(serde_json::Value::as_bool)
                == Some(false)
            {
                return Err(ChannelProviderError::InvalidConfig(
                    "provider rejected config".to_string(),
                ));
            }
            Ok(())
        }

        fn redact_config(&self, config: &serde_json::Value) -> serde_json::Value {
            let mut redacted = config.clone();
            if let Some(object) = redacted.as_object_mut() {
                object.insert("client_secret".to_string(), serde_json::json!("<redacted>"));
            }
            redacted
        }

        fn delivery(&self) -> Arc<dyn ChannelDeliveryPort> {
            self.delivery.clone()
        }

        fn http_ingress(&self) -> Option<Arc<dyn bcs_channel_api::ChannelHttpIngressPort>> {
            None
        }

        fn stream_lifecycle(
            &self,
            _sink: Arc<dyn ChannelInboundSink>,
        ) -> Option<Arc<dyn ServiceLifecycle>> {
            None
        }
    }

    #[derive(Default)]
    struct RecordingCollaborationRuntime {
        starts: Mutex<Vec<StartStateMachineRunCommand>>,
    }

    #[async_trait]
    impl CollaborationRuntimeService for RecordingCollaborationRuntime {
        async fn start_state_machine_run(
            &self,
            cmd: StartStateMachineRunCommand,
        ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
            let session_id = match cmd.session_id.clone() {
                Some(session_id) => session_id,
                None => "state_session".to_string(),
            };
            self.starts.lock().await.push(cmd.clone());
            Ok(StartStateMachineRunOutcome {
                view: StateMachineRunView {
                    run: StateMachineRun {
                        run_id: "state_run_1".to_string(),
                        definition_id: "definition_1".to_string(),
                        definition_version: 1,
                        group_id: cmd.group_id,
                        group_version: 1,
                        session_id,
                        created_by: cmd.caller_id,
                        status: StateMachineRunStatus::Running,
                        input: cmd.input,
                        output: None,
                        error: None,
                        created_at: 1,
                        updated_at: 1,
                        completed_at: None,
                    },
                    nodes: Vec::new(),
                    judge_outputs: Vec::new(),
                },
            })
        }

        async fn get_state_machine_run(
            &self,
            _run_id: &str,
        ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
            Ok(None)
        }

        async fn get_state_machine_session_history(
            &self,
            _session_id: &str,
            _limit: u64,
            _before: Option<u64>,
        ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
            Ok(None)
        }

        async fn cancel_state_machine_run(
            &self,
            cmd: CancelStateMachineRunCommand,
        ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
            Err(CollaborationRuntimeError::RunNotFound(cmd.run_id))
        }

        async fn lookup_delivery_correlation(
            &self,
            _run_id: &str,
        ) -> Result<Option<bcs_domain::StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
            Ok(None)
        }

        async fn register_delivery_alias(
            &self,
            _delivery_request_id: &str,
            _bot_delivery_run_id: String,
        ) -> Result<(), CollaborationRuntimeError> {
            Ok(())
        }

        async fn handle_bot_terminal_event(
            &self,
            _cmd: HandleBotTerminalEventCommand,
        ) -> Result<HandleBotTerminalEventOutcome, CollaborationRuntimeError> {
            Ok(HandleBotTerminalEventOutcome {
                consumed: false,
                view: None,
            })
        }

        async fn upsert_definition(
            &self,
            _definition: bcs_domain::CollaborationDefinition,
        ) -> Result<(), CollaborationRuntimeError> {
            Ok(())
        }

        async fn configure_group_runtime(
            &self,
            _cmd: ConfigureGroupRuntimeCommand,
        ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
            Err(CollaborationRuntimeError::InvalidRequest(
                "Recording runtime does not configure groups".to_string(),
            ))
        }
    }

    #[derive(Default)]
    struct RecordingRegistry {
        ensured: Mutex<Vec<(String, String)>>,
        fail_ensure_human: Mutex<Option<String>>,
    }

    #[async_trait]
    impl BotRegistryCoreService for RecordingRegistry {
        async fn register(&self, _bot_id: String, _capabilities: BotCapabilities) -> ServiceResult<()> {
            Ok(())
        }

        async fn update_status(
            &self,
            _bot_id: &str,
            _status: BotDynamicStatus,
        ) -> bool {
            false
        }

        async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
            Some(registered_bot(bot_id))
        }

        async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
            None
        }

        async fn list_active(&self) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn find_by_scopes(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
            Vec::new()
        }

        async fn unregister(&self, _bot_id: &str) -> bool {
            false
        }

        async fn cleanup_expired(&self) {}

        async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
            None
        }

        async fn save_to_storage(&self, _bot_id: &str, _caps: &BotCapabilities) -> ServiceResult<()> {
            Ok(())
        }

        async fn update_visibility(&self, _bot_id: &str, _visibility: &str) -> ServiceResult<()> {
            Ok(())
        }

        #[allow(deprecated)]
        async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
            Ok(())
        }

        async fn ensure_human_actor(
            &self,
            staff_no: &str,
            nick_name: &str,
        ) -> ServiceResult<EnsureHumanResult> {
            if let Some(error) = self.fail_ensure_human.lock().await.clone() {
                return Err(ServiceError::InternalError(error));
            }
            self.ensured
                .lock()
                .await
                .push((staff_no.to_string(), nick_name.to_string()));
            Ok(EnsureHumanResult { created: true })
        }

        async fn has_been_onboarded(&self, _bot_id: &str) -> bool {
            true
        }

        async fn save_created_by(
            &self,
            _bot_id: &str,
            _created_by: &str,
            _overwrite: bool,
        ) -> ServiceResult<()> {
            Ok(())
        }

        async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
            Ok(())
        }

        async fn load_token(&self, _bot_id: &str) -> Option<String> {
            None
        }

        async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
            None
        }

        async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
            Err(())
        }

        async fn reconnect_streaming(
            &self,
            _existing_token: String,
        ) -> Result<(String, String), ()> {
            Err(())
        }

        async fn disconnect_streaming(&self, _bot_id: &str) {}

        async fn is_connected(&self, _bot_id: &str) -> bool {
            false
        }

        async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
            Err(())
        }

        async fn list_connected(&self) -> Vec<String> {
            Vec::new()
        }

        async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

        async fn register_http_connection(&self, _bot_id: String, token: String) -> String {
            token
        }

        async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
            Ok(BotDeliveryTarget::WebSocket {
                bot_id: bot_id.to_string(),
            })
        }
    }

    fn registered_bot(bot_id: &str) -> RegisteredBot {
        RegisteredBot {
            bot_uuid: bot_id.to_string(),
            capabilities: BotCapabilities {
                name: Some(bot_id.to_string()),
                summary: None,
                domains: Vec::new(),
                skills: Vec::<Skill>::new(),
                scopes: Vec::new(),
                binding_channels: None,
                hidden: false,
                visibility: "protected".to_string(),
                agent_code: None,
                agent_token: None,
            },
            dynamic_status: BotDynamicStatus::default(),
            env: Some("dev".to_string()),
            created_by: None,
            actor_kind: ActorKind::Bot,
            status: bcs_domain::ActorStatus::Online,
        }
    }

    fn not_configured(name: &str) -> ServiceError {
        ServiceError::InvalidOperation {
            message: format!("{name} is not configured"),
            request_id: None,
        }
    }
}
