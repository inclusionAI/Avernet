use bcs_message_flow::{BcsGroupMessageHistory, BcsMessageFlow, MemoryBotRunContextStore};
use bcs_protocol::BcsFrame;
use bcs_service_api::{
    ActiveBotRunContext, ActorKind, BotActor, BotDeliveryKind, BotDeliveryTarget, BotEventCommand,
    BotRegistryCoreService, BotRunContext, BotRunContextPort, BotRunScope, BotRunTransportOwner,
    CallerContext, ChannelSenderIdentity, ChatAbortCommand, ChatEventState, FrontendDeliveryTarget,
    Group, GroupCallbackCommand, GroupChatCommand, GroupCoreService, GroupHistoryBotRequestPort,
    GroupHistoryCommand, GroupKind, GroupMessage, GroupMessageHistoryService, GroupMessageType,
    GroupStatus, GroupStrategy, HumanActor, MessageFlowService, MessageRole, Participant,
    ParticipantMode, ParticipantRole, PersistentGroupSendCommand, ProviderStreamGrayList,
    RedactedToken, ServiceError, ServiceResult, SessionHistoryCommand, SystemMessageEvent,
    SystemMessageService, WebSendCommand,
    interceptor::{BlockReason, InterceptorDecision, MessageInterceptor, OutboundMessage},
    port::{EventRecordFactoryPort, NewEvent},
};
use serde_json::json;
use std::{collections::HashMap, sync::Arc};
use tokio::sync::{Mutex, RwLock};

use crate::flow_support as support;

use bcs_test_support::LazySeedAuthorityHook;

use crate::session_support;
use session_support::{StaticSessionManagement, test_session};

#[path = "contract_message_flow/support/authority.rs"]
mod authority;
#[path = "contract_message_flow/support/group_setup.rs"]
mod group_setup;
#[path = "contract_message_flow/support/history.rs"]
mod history_support;
#[path = "contract_message_flow/support/message_repo.rs"]
mod message_repo;
#[path = "contract_message_flow/support/policy.rs"]
mod policy;
#[path = "contract_message_flow/support/runs.rs"]
mod runs;
use authority::{DRIVER_OWNER_1, staff_authority, with_hook};
use group_setup::make_human_bot_dm;
use history_support::ControllableHistoryBotRequest;
use message_repo::RecordingMessageRepo;
use policy::BlockingInterceptor;
use runs::{register_active_run, register_active_run_with_downstream};

#[path = "contract_message_flow/session_history.rs"]
mod session_history;
#[path = "contract_message_flow/group_history.rs"]
mod group_history;

#[path = "contract_message_flow/web_send_routing.rs"]
mod web_send_routing;
#[path = "contract_message_flow/web_send_projection.rs"]
mod web_send_projection;
#[path = "contract_message_flow/web_send_provider.rs"]
mod web_send_provider;
#[path = "contract_message_flow/web_send_sessions.rs"]
mod web_send_sessions;
#[path = "contract_message_flow/group_chat.rs"]
mod group_chat;

#[path = "contract_message_flow/persistent_send.rs"]
mod persistent_send;
#[path = "contract_message_flow/bot_event_callback.rs"]
mod bot_event_callback;
#[path = "contract_message_flow/abort.rs"]
mod abort;

#[path = "contract_message_flow/attachments.rs"]
mod attachments;
#[path = "contract_message_flow/human_mentions.rs"]
mod human_mentions;
