#[path = "support/session.rs"]
mod session_support;

use std::collections::BTreeMap;

use std::sync::atomic::{AtomicBool, Ordering};

use std::sync::Arc;

use bcs_domain::{MessageAudience, MessageVisibilityDomain};

use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore};

use bcs_message_flow::task_store::{new_task_entry, TaskLedgerStatus, TASK_TTL_MS};

use bcs_protocol::{BcsFrame, stream::TASK_INTENT_ELIGIBLE_KEY};

use bcs_service_api::{
    ActorKind, BotDeliveryKind, BotDeliveryTarget, BotEventCommand, BotRegistryCoreService,
    BotRunContextPort, BotTerminalEvent, BotTerminalObserverPort, BotTerminalState,
    ChannelOutboundEventKind, ChannelRenderHint, ChatEventState,
    ChatResponseMode, DefaultDelivery,
    FrontendDeliveryTarget, GroupCoreService, GroupKind, GroupStatus, GroupStrategy,
    CoordinationMode, CoordinationSurface, MessageFlowService, Participant, ParticipantMode,
    ParticipantRole,
    ProviderStreamGrayList,
    RoutingMode, RoutingPolicy, ServiceError, Session, SessionKind,
    SessionManagementService, SessionStatus, SessionUseCaseError, SystemMessageEvent,
    SystemMessageService, TaskCompleteCommand, TaskDispatchCommand, TaskMessageCommand,
    TaskRunAliasRegistration, ChannelInboundError, ChannelService, ChannelUseCaseError,
    interceptor::{BlockReason, InterceptorDecision, MessageInterceptor, OutboundMessage},
};

use serde_json::{Value, json};

use tokio::sync::{Barrier, Mutex, Notify, RwLock};

use tokio::time::{Duration, timeout};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

#[path = "contract_bot_event/support.rs"]
mod fixtures;
use fixtures::{BlockingInterceptor, BlockingMessageRepo, CONFIGURED_TASK_RUN_TIMEOUT_MS, RecordingBotTerminalObserver, RecordingChannelService, RecordingMessageRepo, RecordingSessionManagement, RecordingSystemMessage, agent_tool_result_payload, assert_frame_tags, assert_ledger_notification, configure_provider_v2_target, coordination_echo, manager_worker_flow_with_blocking_repo, manager_worker_flow_with_provider_stream, manager_worker_flow_with_repo, record_matching_tool_start, register_manager_worker_task, request_id, request_params, state_payload_name, test_session};

#[path = "contract_bot_event/publication.rs"]
mod publication;

#[path = "contract_bot_event/im_reaction.rs"]
mod im_reaction;

#[path = "contract_bot_event/channel_delivery.rs"]
mod channel_delivery;

#[path = "contract_bot_event/thinking_stream.rs"]
mod thinking_stream;

#[path = "contract_bot_event/group_relay.rs"]
mod group_relay;

#[path = "contract_bot_event/task_lifecycle.rs"]
mod task_lifecycle;

#[path = "contract_bot_event/coordination_echo.rs"]
mod coordination_echo;

#[path = "contract_bot_event/task_routing.rs"]
mod task_routing;

#[path = "contract_bot_event/task_result.rs"]
mod task_result;

#[path = "contract_bot_event/task_stream.rs"]
mod task_stream;

#[path = "contract_bot_event/task_completion.rs"]
mod task_completion;

#[path = "contract_bot_event/task_history.rs"]
mod task_history;

#[path = "contract_bot_event/history_persistence.rs"]
mod history_persistence;

#[path = "contract_bot_event/human_notify.rs"]
mod human_notify;
