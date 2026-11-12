//! Edge branches of the Session history and chat routes:
//!   GET  /sessions/{sid}/messages
//!   POST /sessions/{sid}/chat
//!
//! Both routes read a Session and its parent Group before delegating to the
//! history / message-flow services, so the fixture injects those services to
//! pin the error translation and the caller-visibility checks.

use std::sync::Arc;

use axum::http::StatusCode;
use bcs_service_api::{
    BotEventCommand, BotRegistryCoreService, BotEventOutcome, ChatAbortCommand, ChatAbortOutcome, GroupChatCommand,
    GroupCallbackCommand, GroupCallbackOutcome, GroupChatOutcome, GroupHistoryCommand,
    GroupHistoryResult, GroupMessageHistoryService, GroupStrategy, GroupUseCaseError,
    MessageDeliveryResult, MessageFlowService, MessageHistoryOptions, ServiceError,
    ServiceResult, SessionHistoryCommand, SessionHistoryResult, TaskCompleteCommand,
    TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome, TaskRunAliasRegistration,
};
use serde_json::json;
use tokio::sync::Mutex;

use super::{builder, call, get_as, json_as, Fail};

const SESSION: &str = "/sessions/group-1:00000001";

// ---------------------------------------------------------------
// Scripted history service
// ---------------------------------------------------------------

#[derive(Clone, Copy)]
pub(super) enum HistoryFailure {
    Forbidden,
    Unauthorized,
    InvalidLimit,
    Service,
    ActorMissing,
}

fn history_error(failure: HistoryFailure) -> GroupUseCaseError {
    match failure {
        HistoryFailure::Forbidden => GroupUseCaseError::Forbidden("no history for you".to_string()),
        HistoryFailure::Unauthorized => {
            GroupUseCaseError::Unauthorized("missing identity".to_string())
        }
        HistoryFailure::InvalidLimit => GroupUseCaseError::InvalidHistoryLimit(0),
        HistoryFailure::Service => {
            GroupUseCaseError::Service(ServiceError::InvalidOperation {
                message: "source bot rejected history".to_string(),
                request_id: None,
            })
        }
        HistoryFailure::ActorMissing => GroupUseCaseError::ActorNotFound("ghost".to_string()),
    }
}

pub(super) struct ScriptedHistory {
    failure: Mutex<Option<HistoryFailure>>,
}

impl ScriptedHistory {
    pub(super) fn ok() -> Arc<Self> {
        Arc::new(Self {
            failure: Mutex::new(None),
        })
    }

    pub(super) fn failing(failure: HistoryFailure) -> Arc<Self> {
        Arc::new(Self {
            failure: Mutex::new(Some(failure)),
        })
    }
}

#[async_trait::async_trait]
impl GroupMessageHistoryService for ScriptedHistory {
    async fn get_history(
        &self,
        _cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        unimplemented!("the session history route never reads group history")
    }

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        if let Some(failure) = *self.failure.lock().await {
            return Err(history_error(failure));
        }
        Ok(SessionHistoryResult {
            session_id: cmd.session_id,
            messages: Vec::new(),
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }

    async fn get_session_history_with_options(
        &self,
        cmd: SessionHistoryCommand,
        _options: MessageHistoryOptions,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        self.get_session_history(cmd).await
    }
}

// ---------------------------------------------------------------
// Scripted message flow
// ---------------------------------------------------------------

#[derive(Clone, Copy)]
pub(super) enum FlowFailure {
    GroupMissing,
    Unauthorized,
    Forbidden,
    InvalidOperation,
    SessionMissing,
    Internal,
}

pub(super) struct ScriptedFlow {
    failure: Mutex<Option<FlowFailure>>,
    commands: Mutex<Vec<GroupChatCommand>>,
}

impl ScriptedFlow {
    pub(super) fn ok() -> Arc<Self> {
        Arc::new(Self {
            failure: Mutex::new(None),
            commands: Mutex::new(Vec::new()),
        })
    }

    pub(super) fn failing(failure: FlowFailure) -> Arc<Self> {
        Arc::new(Self {
            failure: Mutex::new(Some(failure)),
            commands: Mutex::new(Vec::new()),
        })
    }
}

#[async_trait::async_trait]
impl MessageFlowService for ScriptedFlow {
    async fn handle_web_send(
        &self,
        _cmd: bcs_service_api::WebSendCommand,
    ) -> ServiceResult<bcs_service_api::WebSendOutcome> {
        unimplemented!("the session chat route never sends web messages")
    }

    async fn handle_group_chat(
        &self,
        cmd: GroupChatCommand,
    ) -> ServiceResult<GroupChatOutcome> {
        self.commands.lock().await.push(cmd);
        match *self.failure.lock().await {
            Some(FlowFailure::GroupMissing) => {
                Err(ServiceError::GroupNotFound("group-missing".to_string()))
            }
            Some(FlowFailure::Unauthorized) => {
                Err(ServiceError::Unauthorized("no identity".to_string()))
            }
            Some(FlowFailure::Forbidden) => {
                Err(ServiceError::Forbidden("muted by policy".to_string()))
            }
            Some(FlowFailure::InvalidOperation) => Err(ServiceError::InvalidOperation {
                message: "session is completed".to_string(),
                request_id: None,
            }),
            Some(FlowFailure::SessionMissing) => {
                Err(ServiceError::SessionNotFound("group-1:missing".to_string()))
            }
            Some(FlowFailure::Internal) => {
                Err(ServiceError::InternalError("delivery store down".to_string()))
            }
            None => Ok(GroupChatOutcome {
                queue_admission: None,
                group_id: "group-1".to_string(),
                driver_bot_id: "driver-bot".to_string(),
                mentions: vec!["worker-bot".to_string()],
                hidden_mentions: Vec::new(),
                delivered_count: 1,
                failed_count: 0,
                delivery_results: vec![MessageDeliveryResult {
                    bot_uuid: "worker-bot".to_string(),
                    delivery_type: bcs_domain::DeliveryType::Send,
                    success: true,
                    error: None,
                }],
            }),
        }
    }

    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        unimplemented!("the session chat route never sends bot events")
    }

    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        unimplemented!("the session chat route never aborts chats")
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        unimplemented!("the session chat route never sends group callbacks")
    }

    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        unimplemented!("the session chat route never registers task aliases")
    }

    async fn handle_task_dispatch(
        &self,
        _cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        unimplemented!("the session chat route never dispatches tasks")
    }

    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        unimplemented!("the session chat route never completes tasks")
    }
}

// ----- GET /sessions/{sid}/messages -------------------------------------

#[tokio::test]
async fn session_history_returns_not_found_for_missing_session() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .history(ScriptedHistory::ok())
        .build();

    let (status, body) = call(
        &env.app,
        get_as("/sessions/group-1:deadbeef/messages", "bot-token"),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "session not found");
}

#[tokio::test]
async fn session_history_propagates_session_store_failure() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .history(ScriptedHistory::ok())
        .build();
    *env.sessions.get_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(&env.app, get_as(&format!("{SESSION}/messages"), "bot-token")).await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn session_history_returns_not_found_for_missing_group() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .history(ScriptedHistory::ok())
        .build();
    env.sessions.sessions.lock().await[0].group_id = "group-missing".to_string();

    let (status, body) = call(&env.app, get_as(&format!("{SESSION}/messages"), "bot-token")).await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "group not found");
}

#[tokio::test]
async fn session_history_requires_an_identity() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .history(ScriptedHistory::ok())
        .build();

    let (status, body) = call(&env.app, super::get(&format!("{SESSION}/messages"))).await;

    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"], "unauthorized");
}

#[tokio::test]
async fn session_history_returns_the_projected_messages() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .history(ScriptedHistory::ok())
        .build();

    let (status, body) = call(&env.app, get_as(&format!("{SESSION}/messages"), "bot-token")).await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!([]));
}

#[tokio::test]
async fn session_history_rejects_a_view_bot_outside_the_session() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .history(ScriptedHistory::ok())
        .build();

    let (status, body) = call(
        &env.app,
        super::get(&format!("{SESSION}/messages?view_bot_id=ghost-bot")),
    )
    .await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(
        body["message"],
        "the selected View Actor is not an authorized Session participant"
    );
}

#[tokio::test]
async fn session_history_rejects_an_uncontrolled_view_bot() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .history(ScriptedHistory::ok())
        .build();

    let (status, body) = call(
        &env.app,
        super::get(&format!("{SESSION}/messages?view_bot_id=worker-bot")),
    )
    .await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(
        body["message"],
        "the selected View Actor is not an authorized Session participant"
    );
}

#[tokio::test]
async fn session_history_surfaces_authority_failure_for_a_view_bot() {
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .history(ScriptedHistory::ok())
        .authority(bcs_test_support::failing_authority_hook())
        .build();

    let (status, _) = call(
        &env.app,
        super::get(&format!("{SESSION}/messages?view_bot_id=worker-bot")),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn session_history_maps_forbidden_history_error() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .history(ScriptedHistory::failing(HistoryFailure::Forbidden))
        .build();

    let (status, body) = call(&env.app, get_as(&format!("{SESSION}/messages"), "bot-token")).await;

    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"], "forbidden");
    assert_eq!(body["message"], "no history for you");
}

#[tokio::test]
async fn session_history_maps_unauthorized_history_error() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .history(ScriptedHistory::failing(HistoryFailure::Unauthorized))
        .build();

    let (status, body) = call(&env.app, get_as(&format!("{SESSION}/messages"), "bot-token")).await;

    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"], "unauthorized");
}

#[tokio::test]
async fn session_history_maps_invalid_limit() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .history(ScriptedHistory::failing(HistoryFailure::InvalidLimit))
        .build();

    let (status, body) = call(
        &env.app,
        get_as("/sessions/group-1:00000001/messages?limit=0", "bot-token"),
    )
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "invalid_limit");
    assert_eq!(body["message"], "limit must be > 0, got 0");
}

#[tokio::test]
async fn session_history_maps_service_error() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .history(ScriptedHistory::failing(HistoryFailure::Service))
        .build();

    let (status, body) = call(&env.app, get_as(&format!("{SESSION}/messages"), "bot-token")).await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body["error"], "bad_request");
}

#[tokio::test]
async fn session_history_maps_other_errors_to_internal() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .history(ScriptedHistory::failing(HistoryFailure::ActorMissing))
        .build();

    let (status, body) = call(&env.app, get_as(&format!("{SESSION}/messages"), "bot-token")).await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(body["error"], "internal_error");
}

#[tokio::test]
async fn session_history_returns_empty_for_state_machine_sessions() {
    let env = builder(GroupStrategy::StateMachine, None)
        .await
        .history(ScriptedHistory::ok())
        .build();

    let (status, body) = call(&env.app, get_as(&format!("{SESSION}/messages"), "bot-token")).await;

    // The Noop state-machine runtime has no snapshot: the route answers an
    // empty page rather than an error.
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!([]));
}

#[tokio::test]
async fn session_history_rejects_a_bot_that_is_not_a_participant() {
    let env = builder(GroupStrategy::StateMachine, None)
        .await
        .history(ScriptedHistory::ok())
        .build();

    let (status, body) = call(&env.app, get_as(&format!("{SESSION}/messages"), "driver-token"))
        .await;

    // driver-bot IS a participant; use a Bot token whose actor is not.
    assert_eq!(status, StatusCode::OK, "fixture guard: driver is a member");
    assert_eq!(body, json!([]));

    env.registry
        .store_token_mapping("sample".to_string(), "extra-bot".to_string())
        .await;
    let (status, body) = call(&env.app, get_as(&format!("{SESSION}/messages"), "sample")).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(
        body["message"],
        "caller is not a session participant and owns no Bot in this session"
    );
}

// ----- POST /sessions/{sid}/chat ---------------------------------------

#[tokio::test]
async fn session_chat_returns_not_found_for_missing_session() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .message_flow(ScriptedFlow::ok())
        .build();

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:deadbeef/chat",
            "bot-token",
            json!({"message": "hi"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "session not found");
}

#[tokio::test]
async fn session_chat_propagates_session_store_failure() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .message_flow(ScriptedFlow::ok())
        .build();
    *env.sessions.get_error.lock().await = Some(Fail::Internal);

    let (status, _) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/chat",
            "bot-token",
            json!({"message": "hi"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
}

#[tokio::test]
async fn session_chat_rejects_an_unauthenticated_caller() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .message_flow(ScriptedFlow::ok())
        .build();

    let (status, body) = call(
        &env.app,
        super::json_body(
            "POST",
            "/sessions/group-1:00000001/chat",
            json!({"message": "hi"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert_eq!(body["error"], "unauthorized");
}

#[tokio::test]
async fn session_chat_self_enrolment_failure_is_reported() {
    // A Human not yet in the Session is inserted on first message; a store
    // failure there must not be swallowed.
    let env = builder(GroupStrategy::Chat, Some("alice"))
        .await
        .message_flow(ScriptedFlow::ok())
        .build();
    *env.sessions.add_error.lock().await = Some(Fail::Conflict);

    let (status, body) = call(
        &env.app,
        super::json_body(
            "POST",
            "/sessions/group-1:00000001/chat",
            json!({"message": "hi"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], "state conflict");
}

#[tokio::test]
async fn session_chat_routes_a_bot_caller_with_a_from_field() {
    let flow = ScriptedFlow::ok();
    let env = builder(GroupStrategy::Chat, None)
        .await
        .message_flow(flow.clone())
        .build();

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/chat",
            "bot-token",
            json!({"message": "hi", "from": "driver-bot"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["delivered"], true);
    let commands = flow.commands.lock().await;
    assert_eq!(commands[0].requested_sender_id.as_deref(), Some("driver-bot"));
    assert_eq!(commands[0].session_id.as_deref(), Some("group-1:00000001"));
}

#[tokio::test]
async fn session_chat_maps_group_missing_error() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .message_flow(ScriptedFlow::failing(FlowFailure::GroupMissing))
        .build();

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/chat",
            "bot-token",
            json!({"message": "hi"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body["error"], "group not found: group-missing");
}

#[tokio::test]
async fn session_chat_maps_unauthorized_and_forbidden_errors() {
    for (failure, expected) in [
        (FlowFailure::Unauthorized, "no identity"),
        (FlowFailure::Forbidden, "muted by policy"),
    ] {
        let env = builder(GroupStrategy::Chat, None)
            .await
            .message_flow(ScriptedFlow::failing(failure))
            .build();
        let (status, body) = call(
            &env.app,
            json_as(
                "POST",
                "/sessions/group-1:00000001/chat",
                "bot-token",
                json!({"message": "hi"}),
            ),
        )
        .await;
        assert_eq!(status, StatusCode::FORBIDDEN);
        assert_eq!(body["error"], expected);
    }
}

#[tokio::test]
async fn session_chat_maps_invalid_operation() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .message_flow(ScriptedFlow::failing(FlowFailure::InvalidOperation))
        .build();

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/chat",
            "bot-token",
            json!({"message": "hi"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert!(
        body["error"]
            .as_str()
            .unwrap()
            .contains("Invalid operation")
    );
}

#[tokio::test]
async fn session_chat_maps_session_missing_error() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .message_flow(ScriptedFlow::failing(FlowFailure::SessionMissing))
        .build();

    let (status, _) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/chat",
            "bot-token",
            json!({"message": "hi"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn session_chat_maps_internal_errors() {
    let env = builder(GroupStrategy::Chat, None)
        .await
        .message_flow(ScriptedFlow::failing(FlowFailure::Internal))
        .build();

    let (status, body) = call(
        &env.app,
        json_as(
            "POST",
            "/sessions/group-1:00000001/chat",
            "bot-token",
            json!({"message": "hi"}),
        ),
    )
    .await;

    assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(body["error"], "Internal error: delivery store down");
}
