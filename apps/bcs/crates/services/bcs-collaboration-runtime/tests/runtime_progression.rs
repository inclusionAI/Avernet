use std::{
    collections::{BTreeMap, HashMap, VecDeque},
    future::Future,
    io::{self, Write},
    sync::{Arc, Mutex as StdMutex, OnceLock},
    time::Duration,
};

use async_trait::async_trait;

use bcs_collaboration_runtime::CollaborationRuntime;

use bcs_collaboration_store::MemoryCollaborationStore;

use bcs_domain::{
    ActorKind, CollaborationDefinition, CollaborationDefinitionRef, CollaborationRuntimeDefinition,
    Group, GroupMessageType, GroupStrategy, HumanMessageView, MessageAudience, MessagePage,
    MessageRole, MessageViewScope, MessageVisibilityDomain, NewMessage, OpeningMessage,
    Participant, ParticipantMode, ParticipantRole, PersistedMessage, ResolvedParticipantBinding,
    RuntimeParticipantBinding, SessionStatus, StateMachineNodeStatus, StateMachineRun,
    StateMachineRunStatus, StateMachineTransition,
};

use bcs_domain::{MessageOwnerFilter, MessageQuery, STATE_MACHINE_PANEL_MESSAGE_TYPE};

use bcs_group::{GroupManagement, GroupManagementWithRuntimeCleanup, GroupStore};

use bcs_group_store::MemoryGroupRepo;

use bcs_message_store::MemoryMessageRepo;

use bcs_protocol::{BcsFrame, ChatSendParams};

use bcs_service_api::port::repo::{AppendEventRecord, MessageRepoError, MessageRepoPort};

use bcs_service_api::port::{EventRecordError, EventRecordFactoryPort, NewEvent};

use bcs_service_api::{
    AuthenticatedHumanCaller, BotDeliveryCommand, BotDeliveryPort, BotDeliveryResult,
    BotDeliveryTarget, BotRunContext, BotRunContextPort, ChatEventState, CollaborationEventRepoPort, CollaborationRuntimeError,
    CollaborationRuntimeService, ConfigureGroupRuntimeCommand, CreateStateMachineRerun,
    CreateStateMachineRerunOutcome, DefinitionYamlSource, FrontendDeliveryCommand,
    FrontendDeliveryPort, FrontendDeliveryResult, FrontendDeliveryTarget, GroupCoreService,
    GroupDeleteCommand, GroupManagementService, GroupRuntimeBindingRepoPort,
    HandleSessionHumanInputCommand, HandleSessionHumanInputOutcome, HumanInputReadyEvent,
    HumanResponseSource, HumanRunAccessCommand, JudgeDecision, JudgeEvaluatorPort, JudgeRequest,
    ListPendingHumanNodesCommand, PatchGroupCollaborationDefinitionCommand, ProviderRunTransport,
    RerunStateMachineCommand, RespondHumanNodeCommand, RespondHumanNodeOutcome, ServiceError,
    ServiceResult, SessionChannelDeliveryOutcome, SessionChannelOutboundPort,
    SessionManagementService, SessionStateMachinePermissionCommand,
    StartSessionStateMachineRunCommand, StartStateMachineRunCommand,
    StateMachineDefinitionRepoPort, StateMachineNodeSubStatus, StateMachineResultPublishCommand,
    StateMachineResultPublisherPort, StateMachineRunAccessCommand, StateMachineRunRepoPort,
    ValidateCollaborationDefinitionYamlCommand,
};

use bcs_service_api::{CreateOrReactivateCommand, NewSessionParams, SessionKind};

use bcs_session::{SessionManagementServiceImpl, SessionManagementWithRuntimeCleanup};

use bcs_session_store::MemorySessionRepo;

use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};

use serde_json::{Value, json};

use tokio::sync::{Mutex, Notify};

macro_rules! test_runtime {
    ($($argument:expr),+ $(,)?) => {
        CollaborationRuntime::new($($argument),+)
            .with_message_repo(Arc::new(MemoryMessageRepo::new()))
    };
}

#[path = "runtime_progression/fixed_loop.rs"]
mod fixed_loop_tests;

#[path = "runtime_progression/history_identity.rs"]
mod history_identity;

#[path = "runtime_progression/history_persistence.rs"]
mod history_persistence;

#[path = "runtime_progression/support.rs"]
mod fixtures;
use fixtures::{BlockingJudge, CountingDefinitionRepo, FailingAppendMessageRepo, FailingResultPublisher, RecordingBotRunContext, RecordingDelivery, RecordingEventFactory, RecordingFrontendDelivery, RecordingJudge, RecordingResultPublisher, RecordingSessionChannelOutbound, RejectingDelivery, SequencedJudge, SharedLogBuffer, assert_inferred_default_requires, capture_tracing_logs, chat_send_params, complete_with_text, frontend_human_input_yaml, human_input_yaml, join_yaml, judge_branch_yaml, judge_shared_merge_yaml, judge_timeout_yaml, judged_human_input_yaml, noop_judge, one_shot_authoring_yaml, one_shot_human_input_authoring_yaml, session_collaboration_group, single_node_authoring_yaml, single_node_template_yaml, single_node_yaml, state_machine_test_group, test_group, test_sessions};

#[path = "runtime_progression/session_permission.rs"]
mod session_permission;

#[path = "runtime_progression/one_shot.rs"]
mod one_shot;

#[path = "runtime_progression/result_publication.rs"]
mod result_publication;

#[path = "runtime_progression/run_result.rs"]
mod run_result;

#[path = "runtime_progression/human_input_activation.rs"]
mod human_input_activation;

#[path = "runtime_progression/human_input_response.rs"]
mod human_input_response;

#[path = "runtime_progression/timeouts.rs"]
mod timeouts;

#[path = "runtime_progression/completion.rs"]
mod completion;

#[path = "runtime_progression/resource_cleanup.rs"]
mod resource_cleanup;

#[path = "runtime_progression/definition_binding.rs"]
mod definition_binding;

#[path = "runtime_progression/opening.rs"]
mod opening;

#[path = "runtime_progression/graph_and_judge.rs"]
mod graph_and_judge;
