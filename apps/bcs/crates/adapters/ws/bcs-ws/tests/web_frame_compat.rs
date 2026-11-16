mod support;

#[path = "web_frame_compat/diagnostics.rs"]
mod diagnostics;
#[path = "web_frame_compat/subscriptions.rs"]
mod subscriptions;
#[path = "web_frame_compat/chat_send.rs"]
mod chat_send;
#[path = "web_frame_compat/abort.rs"]
mod abort;
#[path = "web_frame_compat/interactions.rs"]
mod interactions;
#[path = "web_frame_compat/bound_connection.rs"]
mod bound_connection;
#[path = "web_frame_compat/bound_messages.rs"]
mod bound_messages;
#[path = "web_frame_compat/ingress_identity.rs"]
mod ingress_identity;

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{
    CollaborationDefinition, HumanMessageView, MessageAudience, MessageViewScope,
    MessageVisibilityDomain, StateMachineDeliveryCorrelation, StateMachineNodeRun,
    StateMachineNodeStatus, StateMachineRun, StateMachineRunStatus,
};
use bcs_protocol::{BcsFrame, RequestFrame, ResponseFrame};
use bcs_service_api::application::v1::{
    ActorKind, ApplicationError, AuthorizeGroupSessionConnection, AuthorizedGroupSessionConnection,
    GroupSessionConnectionBinding, GroupSessionConnectionError, GroupSessionConnectionService,
    IssueGroupSessionConnectionToken, IssuedGroupSessionConnectionToken, ParticipantRole,
    SessionParticipant, VerifyGroupSessionConnectionToken,
};
use bcs_service_api::{
    BotEventCommand, BotEventOutcome, CancelStateMachineRunCommand, ChatAbortCommand,
    ChatAbortFailure, ChatAbortOutcome, ChatAbortScope, CollaborationRuntimeError,
    CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome, GroupCallbackCommand,
    GroupCallbackOutcome, HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome,
    HandleSessionHumanInputCommand, HandleSessionHumanInputOutcome, MessageFlowService,
    ParticipantKind, ParticipantMode, RespondHumanNodeOutcome, ServiceResult, SessionHistoryResult,
    StartStateMachineRunCommand, StartStateMachineRunOutcome, StateMachineRunView,
    TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome,
    TaskRunAliasRegistration, WebSendCommand, WebSendOutcome,
    WorkbenchChatAbortAuthorizationCommand, WorkbenchChatAuthorizationCommand,
    WorkbenchConnectCommand, WorkbenchConnectOutcome, WorkbenchParticipantView,
    WorkbenchSessionService, WorkbenchSpeakingModeCommand, WorkbenchUseCaseError,
};
use bcs_service_api::{
    InteractionFrontendEvent, InteractionRequestedOutcome, InteractionService,
    InteractionServiceError, InteractionStatus, ProviderInteractionRequestedCommand,
    ProviderInteractionResolvedCommand, ResolveInteractionCommand, ResolveInteractionResult,
};
use bcs_test_support::NoopCollaborationRuntimeService;
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::{
    WebClientConnectionState, WebDispatchOutcome, WebDispatchState, WorkbenchConnectionAuth,
    WorkbenchConnectionRegistry, dispatch_client_frame,
};
use tokio::sync::{Mutex, mpsc};
use tokio::time::{Duration, timeout};

#[derive(Clone, Copy)]
enum SessionHumanInputBehavior {
    Consumed,
    Conflict,
}

struct RecordingCollaborationRuntime {
    behavior: SessionHumanInputBehavior,
    commands: Mutex<Vec<HandleSessionHumanInputCommand>>,
}

impl RecordingCollaborationRuntime {
    fn new(behavior: SessionHumanInputBehavior) -> Self {
        Self {
            behavior,
            commands: Mutex::new(Vec::new()),
        }
    }
}

#[async_trait]
impl CollaborationRuntimeService for RecordingCollaborationRuntime {
    async fn start_state_machine_run(
        &self,
        _cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        unreachable!("run start is not used by web ws compat tests")
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn handle_session_human_input(
        &self,
        cmd: HandleSessionHumanInputCommand,
    ) -> Result<HandleSessionHumanInputOutcome, CollaborationRuntimeError> {
        self.commands.lock().await.push(cmd.clone());
        match self.behavior {
            SessionHumanInputBehavior::Consumed => Ok(HandleSessionHumanInputOutcome::Consumed {
                response: RespondHumanNodeOutcome {
                    node: StateMachineNodeRun {
                        run_id: "state-run-1".to_string(),
                        node_id: "human-review".to_string(),
                        status: StateMachineNodeStatus::Completed,
                        attempt: 0,
                        node_timeout_ms: Some(60_000),
                        timeout_deadline_ms: None,
                        max_attempts: 1,
                        assignee_bot_id: None,
                        outcome: Some("complete".to_string()),
                        responded_by: Some(cmd.caller_actor_id),
                        delivery_request_id: None,
                        bot_delivery_run_id: None,
                        artifact_text: Some(cmd.content),
                        error: None,
                        started_at: Some(1),
                        completed_at: Some(2),
                    },
                    run: StateMachineRun {
                        run_id: "state-run-1".to_string(),
                        root_run_id: Some("state-run-1".to_string()),
                        rerun_of: None,
                        definition_id: "definition-1".to_string(),
                        definition_version: 1,
                        group_id: cmd.group_id,
                        group_version: 1,
                        session_id: cmd.session_id.expect("session id"),
                        session_activation_count: None,
                        created_by: None,
                        status: StateMachineRunStatus::Running,
                        input: serde_json::Value::Null,
                        opening_message_override: None,
                        output: None,
                        error: None,
                        created_at: 1,
                        updated_at: 2,
                        completed_at: None,
                    },
                },
            }),
            SessionHumanInputBehavior::Conflict => Err(CollaborationRuntimeError::Conflict(
                "state machine is not waiting for Human input".to_string(),
            )),
        }
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
    ) -> Result<Option<StateMachineDeliveryCorrelation>, CollaborationRuntimeError> {
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
        _definition: CollaborationDefinition,
    ) -> Result<(), CollaborationRuntimeError> {
        Ok(())
    }

    async fn configure_group_runtime(
        &self,
        _cmd: ConfigureGroupRuntimeCommand,
    ) -> Result<ConfigureGroupRuntimeOutcome, CollaborationRuntimeError> {
        unreachable!("runtime configuration is not used by web ws compat tests")
    }
}

#[derive(Default)]
struct RecordingMessageFlow {
    web_sends: Mutex<Vec<WebSendCommand>>,
    aborts: Mutex<Vec<ChatAbortCommand>>,
    abort_outcome: Mutex<Option<ChatAbortOutcome>>,
    output_admission: Mutex<Option<Arc<dyn bcs_service_api::OutputAdmissionPort>>>,
}

#[derive(Default)]
struct RecordingWorkbenchSessions {
    connects: Mutex<Vec<WorkbenchConnectCommand>>,
    authorizations: Mutex<Vec<WorkbenchChatAuthorizationCommand>>,
    abort_authorizations: Mutex<Vec<WorkbenchChatAbortAuthorizationCommand>>,
    connect_error: Mutex<Option<WorkbenchUseCaseError>>,
    connect_scope: Mutex<Option<MessageViewScope>>,
    connect_participants: Mutex<Option<Vec<WorkbenchParticipantView>>>,
    speaking_mode_commands: Mutex<Vec<WorkbenchSpeakingModeCommand>>,
    speaking_mode_projection:
        Mutex<Option<bcs_domain::speaking_lease::SessionSpeakingMode>>,
}

#[derive(Default)]
struct RecordingGroupSessionConnections {
    authorizations: Mutex<Vec<AuthorizeGroupSessionConnection>>,
    reject_connect: Mutex<bool>,
}

#[async_trait]
impl GroupSessionConnectionService for RecordingGroupSessionConnections {
    async fn issue_token(
        &self,
        _command: IssueGroupSessionConnectionToken,
    ) -> Result<IssuedGroupSessionConnectionToken, GroupSessionConnectionError> {
        unreachable!("token issuance is not used by WebSocket frame tests")
    }

    async fn verify_token(
        &self,
        _command: VerifyGroupSessionConnectionToken,
    ) -> Result<GroupSessionConnectionBinding, GroupSessionConnectionError> {
        unreachable!("token verification is not used by WebSocket frame tests")
    }

    async fn authorize_connect(
        &self,
        command: AuthorizeGroupSessionConnection,
    ) -> Result<AuthorizedGroupSessionConnection, GroupSessionConnectionError> {
        self.authorizations.lock().await.push(command);
        if *self.reject_connect.lock().await {
            return Err(ApplicationError::forbidden("session access revoked").into());
        }
        Ok(AuthorizedGroupSessionConnection {
            participants: vec![SessionParticipant {
                actor_id: "human_100001".to_string(),
                actor_kind: ActorKind::Human,
                name: Some("Test Human".to_string()),
                avatar_url: None,
                role: ParticipantRole::Observer,
                tags: Vec::new(),
                mode: ParticipantMode::Present,
                message_view_scope: MessageViewScope::Full,
                joined_at: None,
            }],
            view: bcs_service_api::application::v1::ConnectionView::HumanSelf,
        })
    }
}

#[async_trait]
impl WorkbenchSessionService for RecordingWorkbenchSessions {
    async fn connect(
        &self,
        command: WorkbenchConnectCommand,
    ) -> Result<WorkbenchConnectOutcome, WorkbenchUseCaseError> {
        self.connects.lock().await.push(command.clone());
        if let Some(error) = self.connect_error.lock().await.take() {
            return Err(error);
        }
        let participants = if let Some(participants) =
            self.connect_participants.lock().await.clone()
        {
            participants
        } else {
            vec![WorkbenchParticipantView {
                bot_uuid: "human_100001".to_string(),
                role: "observer".to_string(),
                kind: ParticipantKind::Bot,
                mode: Some(ParticipantMode::Present),
                message_view_scope: self
                    .connect_scope
                    .lock()
                    .await
                    .unwrap_or(MessageViewScope::Full),
            }]
        };
        Ok(WorkbenchConnectOutcome {
            group_id: command.group_id,
            participants,
            speaking_mode: bcs_domain::speaking_lease::SessionSpeakingMode::Plain,
        })
    }

    async fn resolve_speaking_mode_projection(
        &self,
        command: WorkbenchSpeakingModeCommand,
    ) -> bcs_domain::speaking_lease::SessionSpeakingMode {
        self.speaking_mode_commands.lock().await.push(command);
        self.speaking_mode_projection
            .lock()
            .await
            .unwrap_or(bcs_domain::speaking_lease::SessionSpeakingMode::Plain)
    }

    async fn authorize_chat_send(
        &self,
        command: WorkbenchChatAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        self.authorizations.lock().await.push(command);
        Ok(())
    }

    async fn authorize_chat_abort(
        &self,
        command: WorkbenchChatAbortAuthorizationCommand,
    ) -> Result<(), WorkbenchUseCaseError> {
        self.abort_authorizations.lock().await.push(command);
        Ok(())
    }
}

#[async_trait]
impl MessageFlowService for RecordingMessageFlow {
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        let operation_id = format!("op-{}", self.web_sends.lock().await.len() + 1);
        if let Some(port) = self.output_admission.lock().await.clone() {
            port.admit_output_operation(
                bcs_service_api::OutputOperationAdmission {
                    operation_id: operation_id.clone(),
                    group_id: cmd.group_id.clone(),
                    session_id: cmd.session_id.clone(),
                },
            )
            .await?;
        }
        self.web_sends.lock().await.push(cmd);
        Ok(WebSendOutcome {
            operation_id,
            queue_admission: None,
            primary_run_id: "run-web-1".to_string(),
            active_run_ids: vec!["run-web-1".to_string()],
            status: "accepted".to_string(),
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
            mentions: vec![],
            hidden_mentions: vec![],
            delivered_count: 0,
            failed_count: 0,
            delivery_results: vec![],
        })
    }

    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        unreachable!("bot event is not used by web ws compat tests")
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        unreachable!("group callback is not used by web ws compat tests")
    }

    async fn handle_chat_abort(&self, cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        self.aborts.lock().await.push(cmd);
        if let Some(outcome) = self.abort_outcome.lock().await.take() {
            return Ok(outcome);
        }
        Ok(ChatAbortOutcome {
            aborted: true,
            aborted_run_ids: vec!["run-web-1".to_string()],
            bot_deliveries: vec![],
            frontend_deliveries: vec![],
            failures: vec![],
        })
    }

    async fn resolve_chat_abort_scope(
        &self,
        group_id: &str,
        run_id: &str,
    ) -> ServiceResult<Option<ChatAbortScope>> {
        Ok(
            (group_id == "group-web-1" && run_id == "run-legacy").then(|| ChatAbortScope {
                group_id: group_id.to_string(),
                session_id: "session-web-1".to_string(),
                bot_id: "bot-target".to_string(),
            }),
        )
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
        unreachable!("task dispatch is not used by web ws compat tests")
    }

    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        unreachable!("task complete is not used by web ws compat tests")
    }
}

struct TestState {
    workbench_sessions: Arc<RecordingWorkbenchSessions>,
    group_session_connections: Arc<RecordingGroupSessionConnections>,
    message_flow: Arc<RecordingMessageFlow>,
    interactions: Arc<RecordingInteractions>,
    dispatch_state: Arc<WebDispatchState>,
    delivery_admission: Arc<support::RecordingAdmission>,
}

#[derive(Default)]
struct RecordingInteractions {
    resolves: Mutex<Vec<ResolveInteractionCommand>>,
    next_result: Mutex<Option<Result<ResolveInteractionResult, InteractionServiceError>>>,
    pending: Mutex<Vec<InteractionFrontendEvent>>,
    pending_error: Mutex<Option<bcs_service_api::ServiceError>>,
    invalidations: Mutex<Vec<(String, String)>>,
}

#[async_trait]
impl InteractionService for RecordingInteractions {
    async fn on_provider_requested(
        &self,
        _command: ProviderInteractionRequestedCommand,
    ) -> ServiceResult<InteractionRequestedOutcome> {
        Ok(InteractionRequestedOutcome::Duplicate)
    }

    async fn on_provider_resolved(
        &self,
        _command: ProviderInteractionResolvedCommand,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn resolve(
        &self,
        command: ResolveInteractionCommand,
    ) -> Result<ResolveInteractionResult, InteractionServiceError> {
        self.resolves.lock().await.push(command.clone());
        self.next_result.lock().await.take().unwrap_or_else(|| {
            Ok(ResolveInteractionResult {
                accepted: true,
                interaction_id: command.interaction_id,
                status: InteractionStatus::Accepted,
                idempotency_key: command.idempotency_key,
            })
        })
    }

    async fn list_pending(
        &self,
        _bcs_session_id: &str,
    ) -> ServiceResult<Vec<InteractionFrontendEvent>> {
        if let Some(error) = self.pending_error.lock().await.take() {
            return Err(error);
        }
        Ok(self.pending.lock().await.clone())
    }

    async fn invalidate_run(
        &self,
        bcs_run_id: &str,
        reason: &str,
        _invalidated_at_ms: u64,
    ) -> ServiceResult<usize> {
        self.invalidations
            .lock()
            .await
            .push((bcs_run_id.to_string(), reason.to_string()));
        Ok(1)
    }

    async fn cleanup_terminal(&self, _terminal_before_ms: u64) -> ServiceResult<usize> {
        Ok(0)
    }
}

fn new_state() -> TestState {
    new_state_with_collaboration_runtime(Arc::new(NoopCollaborationRuntimeService))
}

fn new_state_with_collaboration_runtime(
    collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
) -> TestState {
    let workbench_sessions = Arc::new(RecordingWorkbenchSessions::default());
    let group_session_connections = Arc::new(RecordingGroupSessionConnections::default());
    let message_flow = Arc::new(RecordingMessageFlow::default());
    let interactions = Arc::new(RecordingInteractions::default());
    let output_permits = Arc::new(bcs_ws::shared::OutputPermitStore::new());
    let delivery_admission = Arc::new(support::RecordingAdmission::new());
    let frontend_connections = Arc::new(
        WorkbenchConnectionRegistry::new().with_output_permits(output_permits.clone()),
    );
    frontend_connections.set_delivery_admission(delivery_admission.clone());
    *message_flow
        .output_admission
        .try_lock()
        .expect("fresh flow lock") = Some(Arc::new(
        bcs_ws::web::WorkbenchOutputAdmission::new(
            frontend_connections.clone(),
            delivery_admission.clone(),
        ),
    ));
    let dispatch_state = Arc::new(WebDispatchState {
        message_flow: message_flow.clone(),
        collaboration_runtime,
        workbench_sessions: workbench_sessions.clone(),
        interactions: interactions.clone(),
        group_session_connections: Some(group_session_connections.clone()),
        frontend_connections,
        run_channels: Arc::new(
            RunChannelManager::new().with_output_permits(output_permits),
        ),
        delivery_admission: delivery_admission.clone(),
        speaking_leases: None,
        lease_idle_timeout_ms: 60_000,
    });

    TestState {
        workbench_sessions,
        group_session_connections,
        message_flow,
        interactions,
        dispatch_state,
        delivery_admission,
    }
}

async fn recv_response(rx: &mut mpsc::Receiver<String>) -> ResponseFrame {
    let raw = rx.recv().await.expect("expected ws response");
    match serde_json::from_str::<BcsFrame>(&raw).unwrap() {
        BcsFrame::Response(res) => res,
        other => panic!("expected response frame, got {other:?}"),
    }
}

fn session_bound_auth() -> WorkbenchConnectionAuth {
    WorkbenchConnectionAuth::SessionBound {
        tenant: Some("tenant-a".to_string()),
        actor_id: "human_100001".to_string(),
        group_id: "group-web-1".to_string(),
        session_id: "session-bound-1".to_string(),
        caller_ingress: None,
    }
}

async fn connect_session_bound(
    state: &TestState,
    tx: &mpsc::Sender<String>,
    rx: &mut mpsc::Receiver<String>,
    connection_state: &mut WebClientConnectionState,
) -> WebDispatchOutcome {
    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-bound",
        "connect",
        Some(serde_json::json!({
            "group_id": "group-web-1",
            "session_id": "session-bound-1"
        })),
    ));
    let outcome = dispatch_client_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        tx,
        connection_state,
        &session_bound_auth(),
    )
    .await
    .unwrap();
    assert!(recv_response(rx).await.ok);
    outcome
}

fn system_now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or_default()
}

fn upgrade_ingress(
    staff_no: &str,
    authenticated_ms: i64,
) -> bcs_service_api::application::caller_identity::AuthenticatedHumanCallerIngress {
    // Identity carries no window: the historical expiry argument is dropped.
    let principal = bcs_auth_api::AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        ..Default::default()
    };
    bcs_ws::web::ws_upgrade_caller_ingress(&principal, authenticated_ms).expect("ingress")
}
