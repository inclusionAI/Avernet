use bcs_message_flow::{BcsGroupMessageHistory, BcsMessageFlow, MemoryBotRunContextStore};
use bcs_protocol::BcsFrame;
use bcs_service_api::{
    ActorKind, BotActor, BotDeliveryKind, BotDeliveryTarget, BotEventCommand, BotRegistryCoreService,
    BotRunContextPort,
    CallerContext, ChatAbortCommand, ChatEventState, GroupCallbackCommand, GroupChatCommand, GroupHistoryBotRequestPort,
    FrontendDeliveryTarget, Group, GroupHistoryCommand, GroupKind, GroupMessage, GroupMessageHistoryService, GroupMessageType,
    GroupCoreService, GroupStatus, GroupStrategy, HumanActor, MessageFlowService, MessageRole, Participant,
    ParticipantMode, ParticipantRole, PersistentGroupSendCommand, ProviderStreamGrayList,
    ProviderTransportPreference, RedactedToken, ServiceError, WebSendCommand,
    DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS,
    ServiceResult, Session, SessionHistoryCommand, SessionKind, SessionManagementService,
    SessionStatus, SessionUseCaseError,
    interceptor::{BlockReason, InterceptorDecision, MessageInterceptor, OutboundMessage},
};
use serde_json::json;
use std::{collections::HashMap, sync::Arc};
use tokio::sync::RwLock;

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

struct BlockingInterceptor;

#[async_trait::async_trait]
impl MessageInterceptor for BlockingInterceptor {
    async fn on_outbound(&self, _msg: &mut OutboundMessage) -> InterceptorDecision {
        InterceptorDecision::Block(BlockReason {
            interceptor_id: "test-block".to_string(),
            code: "blocked".to_string(),
            message: "blocked by test".to_string(),
            user_visible: true,
        })
    }
}

#[derive(Default)]
struct RecordingMessageRepo {
    appended: RwLock<Vec<bcs_domain::NewMessage>>,
}

impl RecordingMessageRepo {
    async fn appended(&self) -> Vec<bcs_domain::NewMessage> {
        self.appended.read().await.clone()
    }
}

#[async_trait::async_trait]
impl bcs_service_api::port::repo::MessageRepoPort for RecordingMessageRepo {
    async fn append_message(
        &self,
        msg: bcs_domain::NewMessage,
    ) -> Result<bcs_domain::PersistedMessage, bcs_service_api::port::repo::MessageRepoError> {
        let seq = self.appended.read().await.len() as i64 + 1;
        let persisted = bcs_domain::PersistedMessage {
            message_id: format!("msg-{seq}"),
            group_id: msg.group_id.clone(),
            session_id: msg.session_id.clone(),
            session_seq: seq,
            sender_id: msg.sender_id.clone(),
            sender_type: msg.sender_type,
            message_type: msg.message_type.clone(),
            content: msg.content.clone(),
            client_msg_id: msg.client_msg_id.clone(),
            owner_bot_id: msg.owner_bot_id.clone(),
            status: bcs_domain::PersistedMessageStatus::Normal,
            created_at: msg.created_at,
            run_id: msg.run_id.clone(),
        };
        self.appended.write().await.push(msg);
        Ok(persisted)
    }

    async fn query_messages(
        &self,
        _query: bcs_domain::MessageQuery,
    ) -> Result<bcs_domain::MessagePage, bcs_service_api::port::repo::MessageRepoError> {
        Ok(bcs_domain::MessagePage {
            messages: Vec::new(),
            next_cursor: None,
            has_more: false,
        })
    }

    async fn get_message_by_id(
        &self,
        _session_id: &str,
        _message_id: &str,
    ) -> Result<Option<bcs_domain::PersistedMessage>, bcs_service_api::port::repo::MessageRepoError>
    {
        Ok(None)
    }

    async fn get_current_seq(
        &self,
        _session_id: &str,
    ) -> Result<i64, bcs_service_api::port::repo::MessageRepoError> {
        Ok(self.appended.read().await.len() as i64)
    }
}

#[derive(Default)]
struct RecordingHistoryBotRequest {
    calls: RwLock<Vec<(String, String, serde_json::Value, u64)>>,
}

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for RecordingHistoryBotRequest {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        let bot_uuid = target.bot_id().to_string();
        self.calls.write().await.push((
            bot_uuid.clone(),
            method.to_string(),
            params,
            timeout_ms,
        ));
        if bot_uuid == "bot-observer" {
            return Err("request_failed: disconnected".to_string());
        }
        Ok(json!({
            "messages": [
                {
                    "id": "hist-context",
                    "role": "assistant",
                    "content": "[BCS Context]\n- 消息来自: Observer(bot-observer)\n\n[消息内容]\ncontext body",
                    "timestamp": 7
                },
                {
                    "id": "hist-tool",
                    "role": "tool_result",
                    "content": [{"type": "text", "text": "tool output"}],
                    "timestamp": 8,
                    "toolName": "lookup",
                    "toolCallId": "call-1",
                    "isError": false
                },
                {
                    "id": "hist-call",
                    "role": "assistant",
                    "content": [{"type": "toolCall", "name": "search", "id": "call-2", "arguments": {"q": "bcs"}}],
                    "timestamp": 9,
                    "stopReason": "toolUse",
                    "historyMeta": {"source": "openclaw"}
                }
            ]
        }))
    }
}

struct NewerOnlyHistoryBotRequest;

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for NewerOnlyHistoryBotRequest {
    async fn send_history_request(
        &self,
        _target: BotDeliveryTarget,
        _method: &str,
        _params: serde_json::Value,
        _timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        Ok(json!({
            "messages": [
                {
                    "id": "hist-newer",
                    "role": "assistant",
                    "content": "newer than cursor",
                    "timestamp": 20
                }
            ]
        }))
    }
}

async fn make_human_bot_dm(support: &support::FlowTestSupport) {
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_kind = GroupKind::Dm;
    group.driver_bot = "bot-driver".to_string();
    group.dm_pair_key = Some("bot-driver|human_1".to_string());
    group.participants = vec![
        Participant {
            bot_uuid: "human_1".to_string(),
            bot_name: Some("Human One".to_string()),
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
        },
        Participant {
            bot_uuid: "bot-driver".to_string(),
            bot_name: Some("Driver".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        },
    ];
    support.group.upsert(group).await.unwrap();
}

struct StaticSessionManagement {
    session: Session,
}

impl StaticSessionManagement {
    fn new(session: Session) -> Self {
        Self { session }
    }
}

#[async_trait::async_trait]
impl SessionManagementService for StaticSessionManagement {
    async fn create_or_reactivate(
        &self,
        _cmd: bcs_service_api::CreateOrReactivateCommand,
    ) -> Result<bcs_service_api::CreateOrReactivateOutcome, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn get(&self, session_id: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok((self.session.id == session_id).then(|| self.session.clone()))
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self.session.id == session_id && self.session.group_id == group_id)
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn count_running_service(&self, _group_id: &str) -> Result<u64, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_running_service(
        &self,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> Result<(), SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        unimplemented!("not needed by this test")
    }
    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> { Ok(false) }
}

fn test_session(session_id: &str, group_id: &str, participants: Vec<Participant>) -> Session {
    Session {
        id: session_id.to_string(),
        group_id: group_id.to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants,
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        callback_status: None,
        activation_count: 1,
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        collected_at: None,
    }
}

struct ControllableHistoryBotRequest {
    delays_ms: HashMap<String, u64>,
    responses: HashMap<String, Result<serde_json::Value, String>>,
}

impl ControllableHistoryBotRequest {
    fn with_delays(
        delays: HashMap<String, u64>,
        responses: HashMap<String, Result<serde_json::Value, String>>,
    ) -> Self {
        Self {
            delays_ms: delays,
            responses,
        }
    }
}

#[derive(Default)]
struct FallbackParamHistoryBotRequest {
    calls: RwLock<Vec<(String, serde_json::Value)>>,
}

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for FallbackParamHistoryBotRequest {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        _method: &str,
        params: serde_json::Value,
        _timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        let bot_uuid = target.bot_id().to_string();
        self.calls
            .write()
            .await
            .push((bot_uuid.clone(), params.clone()));
        if bot_uuid != "bot-driver" {
            return Err("unexpected_source".to_string());
        }
        if params.get("bcs_session_id").is_some() {
            return Err("explicit_session_not_supported".to_string());
        }
        Ok(json!({
            "messages": [
                {"id": "fallback-1", "role": "assistant", "content": "fallback session history", "timestamp": 1}
            ]
        }))
    }
}

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for ControllableHistoryBotRequest {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        _method: &str,
        _params: serde_json::Value,
        _timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        let bot_uuid = target.bot_id();
        if let Some(&delay) = self.delays_ms.get(bot_uuid) {
            tokio::time::sleep(std::time::Duration::from_millis(delay)).await;
        }
        self.responses
            .get(bot_uuid)
            .cloned()
            .unwrap_or_else(|| Err("not configured".to_string()))
    }
}

#[derive(Default)]
struct RecordingTargetHistoryBotRequest {
    calls: RwLock<Vec<(BotDeliveryTarget, String)>>,
}

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for RecordingTargetHistoryBotRequest {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        _params: serde_json::Value,
        _timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        self.calls
            .write()
            .await
            .push((target, method.to_string()));
        Ok(json!({
            "messages": [
                {"id": "provider-hist-1", "role": "assistant", "content": "provider history", "timestamp": 1}
            ]
        }))
    }
}

#[tokio::test]
async fn session_history_allows_human_who_is_only_session_participant() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.participants.retain(|participant| participant.is_bot());
    support.group.upsert(group).await.unwrap();

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Ok(json!({
            "messages": [
                {"id": "session-human-1", "role": "assistant", "content": "session visible", "timestamp": 1}
            ]
        })),
    );
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let result = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![
                Participant::bot("bot-observer", ParticipantRole::Observer),
                {
                    let mut human = Participant::human("human_1", ParticipantRole::Observer);
                    human.mode = Some(ParticipantMode::Present);
                    human
                },
            ],
            view_bot_id: Some("human_1".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "session visible");
}

#[tokio::test]
async fn session_history_allows_human_whose_owned_bot_is_session_participant() {
    // Reproduces: driver bot pulled the human's bot into the session, but the
    // human's bot is NOT a group participant and the human is not directly in
    // the group. The session still shows in the bot tab (filtered by session
    // participation), so reading its messages must be allowed.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;

    // Group only has the driver bot — no human, no human-owned bot.
    let mut group = support.group.get("group-1").await.unwrap();
    group
        .participants
        .retain(|participant| participant.bot_uuid == "bot-driver");
    support.group.upsert(group).await.unwrap();

    // The human owns bot-observer (the pulled-in bot).
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Ok(json!({
            "messages": [
                {"id": "pulled-1", "role": "assistant", "content": "pulled bot history", "timestamp": 1}
            ]
        })),
    );
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let result = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![
                Participant::bot("bot-driver", ParticipantRole::Driver),
                Participant::bot("bot-observer", ParticipantRole::Observer),
            ],
            view_bot_id: Some("bot-observer".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "pulled bot history");
}

#[tokio::test]
async fn session_history_denies_human_with_no_session_or_group_stake() {
    // Boundary check: a human who is neither a session participant, nor owns a
    // session-participant Bot, nor has any stake in the group must still be
    // denied — the ownership allowance above must not over-grant.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group
        .participants
        .retain(|participant| participant.bot_uuid == "bot-driver");
    support.group.upsert(group).await.unwrap();

    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        HashMap::new(),
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let err = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_2".to_string(),
                staff_no: "2".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![
                Participant::bot("bot-driver", ParticipantRole::Driver),
                Participant::bot("bot-observer", ParticipantRole::Observer),
            ],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .expect_err("unrelated human must be denied");

    assert!(
        matches!(err, bcs_service_api::GroupUseCaseError::Forbidden(_)),
        "expected Forbidden, got {err:?}"
    );
}

#[tokio::test]
async fn session_history_rejects_public_caller() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        HashMap::new(),
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let err = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Public,
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .expect_err("public session history reads must be rejected");

    assert!(
        matches!(err, bcs_service_api::GroupUseCaseError::Unauthorized(_)),
        "expected Unauthorized, got {err:?}"
    );
}

#[tokio::test]
async fn session_history_denies_group_owner_when_not_in_session_and_owns_no_session_bot() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();

    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        HashMap::new(),
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let err = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .expect_err("group ownership alone must not grant session history access");

    assert!(
        matches!(err, bcs_service_api::GroupUseCaseError::Forbidden(_)),
        "expected Forbidden, got {err:?}"
    );
}

#[tokio::test]
async fn session_history_denies_group_participant_bot_not_in_session() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        HashMap::new(),
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let err = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-observer".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .expect_err("group participant bot must be rejected when absent from the session");

    assert!(
        matches!(err, bcs_service_api::GroupUseCaseError::Forbidden(_)),
        "expected Forbidden, got {err:?}"
    );
}

#[tokio::test]
async fn session_history_denies_non_participant_bot() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        HashMap::new(),
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let err = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "intruder-bot".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .expect_err("non-participant bot must be rejected");

    assert!(
        matches!(err, bcs_service_api::GroupUseCaseError::Forbidden(_)),
        "expected Forbidden, got {err:?}"
    );
}

#[tokio::test]
async fn session_history_resolves_from_prefix_using_session_participants() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.participants.retain(|participant| participant.is_bot());
    support.group.upsert(group).await.unwrap();

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Ok(json!({
            "messages": [
                {
                    "id": "session-human-injected",
                    "role": "assistant",
                    "content": "[from:Human One]hello from session human",
                    "timestamp": 1
                }
            ]
        })),
    );
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let result = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![
                Participant::bot("bot-observer", ParticipantRole::Observer),
                {
                    let mut human = Participant::human("human_1", ParticipantRole::Observer);
                    human.mode = Some(ParticipantMode::Present);
                    human
                },
            ],
            view_bot_id: Some("human_1".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].sender, "human_1");
    assert_eq!(result.messages[0].content, "hello from session human");
    assert_eq!(result.messages[0].role, MessageRole::User);
}

#[tokio::test]
async fn session_history_human_view_prefers_session_bot_before_group_lead() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    if let Some(human) = group
        .participants
        .iter_mut()
        .find(|participant| participant.bot_uuid == "human_1")
    {
        human.mode = Some(ParticipantMode::Present);
    }
    support.group.upsert(group).await.unwrap();

    let mut responses = HashMap::new();
    responses.insert(
        "bot-driver".to_string(),
        Ok(json!({
            "messages": [
                {"id": "driver-1", "role": "assistant", "content": "group lead history", "timestamp": 1}
            ]
        })),
    );
    responses.insert(
        "bot-observer".to_string(),
        Ok(json!({
            "messages": [
                {"id": "observer-1", "role": "assistant", "content": "session bot history", "timestamp": 1}
            ]
        })),
    );
    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(
        HashMap::new(),
        responses,
    ));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request,
    );

    let result = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![
                Participant::bot("bot-observer", ParticipantRole::Observer),
                {
                    let mut human = Participant::human("human_1", ParticipantRole::Observer);
                    human.mode = Some(ParticipantMode::Present);
                    human
                },
            ],
            view_bot_id: Some("human_1".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].sender, "bot-observer");
    assert_eq!(result.messages[0].content, "session bot history");
}

#[tokio::test]
async fn session_history_v3_falls_back_to_session_key_when_explicit_session_request_errors() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.set_protocol_version("bot-driver", 3).await;
    let bot_request = Arc::new(FallbackParamHistoryBotRequest::default());
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request.clone(),
    );

    let result = history
        .get_session_history(SessionHistoryCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-driver".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: "group-1:abcdef12".to_string(),
            session_participants: vec![Participant::bot("bot-driver", ParticipantRole::Driver)],
            view_bot_id: None,
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "fallback session history");
    let calls = bot_request.calls.read().await;
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[0].1["session_key"], "group-1");
    assert_eq!(calls[0].1["bcs_session_id"], "group-1:abcdef12");
    assert_eq!(calls[1].1["session_key"], "group-1:abcdef12");
}

#[tokio::test]
async fn group_history_falls_back_to_driver_and_normalizes_bot_history() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();
    let history_request = Arc::new(RecordingHistoryBotRequest::default());
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        history_request.clone(),
    );

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: Some("bot-observer".to_string()),
            limit: 500,
            before: Some(10),
        })
        .await
        .unwrap();

    assert_eq!(result.group_id, "group-1");
    assert_eq!(result.limit, 500);
    assert_eq!(result.messages.len(), 3);
    assert_eq!(result.messages[0].id, "hist-context");
    assert_eq!(result.messages[0].sender, "bot-observer");
    assert_eq!(result.messages[0].bot_name.as_deref(), Some("Observer"));
    assert_eq!(result.messages[0].content, "context body");
    assert_eq!(result.messages[0].role, MessageRole::User);
    assert_eq!(
        result.messages[1].metadata,
        Some(json!({
            "tool_name": "lookup",
            "tool_call_id": "call-1",
            "is_error": false,
            "result": "tool output"
        }))
    );
    assert_eq!(
        result.messages[2].metadata,
        Some(json!({
            "stop_reason": "toolUse",
            "tool_calls": [{"tool_name": "search", "tool_call_id": "call-2", "arguments": {"q": "bcs"}}]
        }))
    );
    assert_eq!(
        result.messages[2].history_meta,
        Some(json!({"source": "openclaw"}))
    );

    let calls = history_request.calls.read().await.clone();
    assert_eq!(calls.len(), 2);

    let observer_call = calls.iter().find(|c| c.0 == "bot-observer").expect("observer call");
    assert_eq!(observer_call.1, "chat.history");
    assert_eq!(
        observer_call.2,
        json!({"session_key": "group-1", "bcs_group_id": "group-1", "limit": 500, "before": 10})
    );
    assert_eq!(observer_call.3, 30_000);

    let driver_call = calls.iter().find(|c| c.0 == "bot-driver").expect("driver call");
    assert_eq!(driver_call.1, "chat.history");
    assert_eq!(
        driver_call.2,
        json!({"session_key": "group-1", "bcs_group_id": "group-1", "limit": 500, "before": 10})
    );
    assert_eq!(driver_call.3, 30_000);
}

#[tokio::test]
async fn history_requests_provider_target_without_ws_connection() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .insert_named_actor("bot-provider", "Provider")
        .await;
    support
        .registry
        .save_created_by("bot-provider", "1", true)
        .await
        .unwrap();
    support
        .registry
        .set_delivery_target(
            "bot-provider",
            support::FakeRegistryService::provider_target("bot-provider"),
        )
        .await;
    let mut provider = Participant::bot("bot-provider", ParticipantRole::Driver);
    provider.bot_name = Some("Provider".to_string());
    let group = Group::new(
        "group-provider",
        "bot-provider",
        vec![
            provider,
            Participant {
                bot_uuid: "human_1".to_string(),
                bot_name: Some("Human One".to_string()),
                kind: None,
                role: ParticipantRole::Observer,
                actor_kind: ActorKind::Human,
                mode: Some(ParticipantMode::Present),
            },
        ],
    );
    support.group.upsert(group).await.unwrap();

    let history_request = Arc::new(RecordingTargetHistoryBotRequest::default());
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        history_request.clone(),
    );

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-provider".to_string(),
            view_bot_id: Some("bot-provider".to_string()),
            limit: 20,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages[0].sender, "bot-provider");
    assert_eq!(result.messages[0].content, "provider history");
    let calls = history_request.calls.read().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].1, "chat.history");
    assert!(calls[0].0.is_http_provider());
}

#[tokio::test]
async fn group_history_falls_back_to_store_when_bot_window_has_no_older_messages() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .group
        .add_message(
            "group-1",
            GroupMessage {
                id: "stored-old".to_string(),
                timestamp: 10,
                sender: "bot-driver".to_string(),
                content: "[from:Driver]stored old".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: None,
                role: MessageRole::Assistant,
                history_meta: None,
                metadata: None,
                run_id: String::new(),
            },
        )
        .await
        .unwrap();
    support
        .group
        .add_message(
            "group-1",
            GroupMessage {
                id: "stored-new".to_string(),
                timestamp: 20,
                sender: "bot-observer".to_string(),
                content: "[from:Observer]stored new".to_string(),
                message_type: GroupMessageType::Bot,
                bot_name: None,
                role: MessageRole::Assistant,
                history_meta: None,
                metadata: None,
                run_id: String::new(),
            },
        )
        .await
        .unwrap();

    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        Arc::new(NewerOnlyHistoryBotRequest),
    );

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: None,
            limit: 1,
            before: Some(20),
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].id, "stored-old");
    assert_eq!(result.messages[0].content, "stored old");
    assert_eq!(result.messages[0].bot_name.as_deref(), Some("Driver"));
    assert_eq!(result.before, Some(20));
    assert_eq!(result.next_before, Some(10));
}

#[tokio::test]
async fn group_history_expands_legacy_unbounded_limit_for_bot_request() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let history_request = Arc::new(RecordingHistoryBotRequest::default());
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        history_request.clone(),
    );

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: None,
            limit: u64::MAX,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.limit, u64::MAX);
    let calls = history_request.calls.read().await.clone();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].0, "bot-driver");
    assert_eq!(
        calls[0].2,
        json!({"session_key": "group-1", "bcs_group_id": "group-1", "limit": 1000})
    );
}

#[tokio::test]
async fn web_send_resets_message_count_routes_and_delivers() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "hello".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
        sender_conn_id: None,
        })
        .await
        .unwrap();

    assert_eq!(support.group.message_count("group-1").await.unwrap(), 0);
    assert_eq!(outcome.status, "started");
    assert!(
        outcome
            .bot_deliveries
            .iter()
            .any(|delivery| delivery.delivered)
    );
    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert!(
        support
            .bot_delivery
            .kinds()
            .await
            .contains(&BotDeliveryKind::Send)
    );
    assert_eq!(
        support.routing.route_calls().await,
        vec![("group-1".to_string(), "hello".to_string(), None)]
    );
}

#[tokio::test]
async fn web_send_persists_public_human_owner_for_manager_worker() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "control-plane-owner".to_string();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.participants = vec![
        Participant::bot("bot-manager", ParticipantRole::Manager),
        Participant::bot("bot-worker", ParticipantRole::Worker),
        Participant::human("human_1", ParticipantRole::Observer),
    ];
    support.group.upsert(group).await.unwrap();
    let repo = Arc::new(RecordingMessageRepo::default());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_message_repo(repo.clone());

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "start service work".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        sender_conn_id: None,
    })
    .await
    .unwrap();

    let appended = repo.appended().await;
    assert_eq!(appended.len(), 1);
    assert_eq!(appended[0].sender_id, "human_1");
    assert_eq!(appended[0].message_type, "chat");
    assert_eq!(appended[0].owner_bot_id, None);

    let chat_support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let chat_repo = Arc::new(RecordingMessageRepo::default());
    let chat_flow = BcsMessageFlow::new(
        chat_support.group.clone(),
        chat_support.routing.clone(),
        chat_support.registry.clone(),
        chat_support.bot_delivery.clone(),
        chat_support.frontend_delivery.clone(),
    )
    .with_message_repo(chat_repo.clone());

    chat_flow
        .handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "regular chat".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            sender_conn_id: None,
        })
        .await
        .unwrap();

    let chat_appended = chat_repo.appended().await;
    assert_eq!(chat_appended.len(), 1);
    assert_eq!(chat_appended[0].owner_bot_id, None);
}

#[tokio::test]
async fn accepted_chat_send_records_run_context_for_callback() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context.clone());

    let before_send_ms = bcs_protocol::now_ms();
    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: Some("group-1:abcdef12".to_string()),
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "hello".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: Some("idempotency-1".to_string()),
            source_im_message_id: Some("source-msg-1".to_string()),
            sender_conn_id: None,
        })
        .await
        .unwrap();
    let after_send_ms = bcs_protocol::now_ms();

    let context = run_context
        .get_context(&outcome.primary_run_id)
        .await
        .expect("run context");
    assert_eq!(context.bot_id, "bot-driver");
    assert_eq!(context.group_id, "group-1");
    assert_eq!(context.bcs_session_id.as_deref(), Some("group-1:abcdef12"));
    assert!(!context.terminal);
    assert_eq!(
        flow.message_tracker
            .channel_source_message_id(&outcome.primary_run_id)
            .await
            .as_deref(),
        Some("source-msg-1")
    );
    assert!(
        context.deadline_ms
            >= before_send_ms.saturating_add(DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS)
    );
    assert!(
        context.deadline_ms
            <= after_send_ms.saturating_add(DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS)
    );
}

#[tokio::test]
async fn web_send_delivers_to_registered_provider_target_without_ws_connection() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .insert_named_actor("bot-provider", "Provider")
        .await;
    support
        .registry
        .set_delivery_target(
            "bot-provider",
            support::FakeRegistryService::provider_target("bot-provider"),
        )
        .await;
    let mut provider = Participant::bot("bot-provider", ParticipantRole::Driver);
    provider.bot_name = Some("Provider".to_string());
    let group = Group::new(
        "group-provider",
        "bot-provider",
        vec![
            provider,
            Participant {
                bot_uuid: "human_1".to_string(),
                bot_name: Some("Human One".to_string()),
                kind: None,
                role: ParticipantRole::Observer,
                actor_kind: ActorKind::Human,
                mode: Some(ParticipantMode::Present),
            },
        ],
    );
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-provider".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "hello provider".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            sender_conn_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.delivered_count, 1);
    let targets = support.bot_delivery.targets().await;
    assert_eq!(targets.len(), 1);
    assert!(targets[0].is_http_provider());
}

#[tokio::test]
async fn provider_stream_gray_created_by_enables_sse_for_provider_chat_send() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    install_provider_driver_group(&support, "gray-user").await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new(vec![
        "gray-user".to_string(),
    ])));

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-provider".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        sender_conn_id: None,
    })
    .await
    .unwrap();

    assert_eq!(
        support.bot_delivery.provider_transports().await,
        vec![ProviderTransportPreference::CallbackSse]
    );
}

#[tokio::test]
async fn provider_stream_gray_created_by_miss_keeps_provider_callback() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    install_provider_driver_group(&support, "other-user").await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new(vec![
        "gray-user".to_string(),
    ])));

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-provider".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        sender_conn_id: None,
    })
    .await
    .unwrap();

    assert_eq!(
        support.bot_delivery.provider_transports().await,
        vec![ProviderTransportPreference::Callback]
    );
}

#[tokio::test]
async fn provider_stream_gray_mode_disabled_sends_provider_chat_send_over_sse() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    install_provider_driver_group(&support, "gray-user").await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new_disabled(vec![
        "gray-user".to_string(),
    ])));

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-provider".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        sender_conn_id: None,
    })
    .await
    .unwrap();

    assert_eq!(
        support.bot_delivery.provider_transports().await,
        vec![ProviderTransportPreference::CallbackSse]
    );
}

#[tokio::test]
async fn provider_stream_gray_created_by_still_keeps_inject_on_callback() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "gray-user", true)
        .await
        .unwrap();
    support
        .registry
        .set_delivery_target(
            "bot-observer",
            BotDeliveryTarget::HttpProvider {
                bot_id: "bot-observer".to_string(),
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "bot-observer".to_string(),
                webhook_url: "https://provider.example.com/bcs/webhook".to_string(),
                bcs_to_provider_token: RedactedToken::new("secret-b2p"),
                protocol_version: "2.0".to_string(),
            },
        )
        .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_provider_stream_gray_list(Arc::new(ProviderStreamGrayList::new(vec![
        "gray-user".to_string(),
    ])));

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        sender_conn_id: None,
    })
    .await
    .unwrap();

    let kinds = support.bot_delivery.kinds().await;
    let targets = support.bot_delivery.targets().await;
    let transports = support.bot_delivery.provider_transports().await;
    assert_eq!(kinds, vec![BotDeliveryKind::Send, BotDeliveryKind::Inject]);
    assert!(targets[1].is_http_provider());
    assert_eq!(transports[1], ProviderTransportPreference::Callback);
}

async fn install_provider_driver_group(
    support: &support::FlowTestSupport,
    created_by: &str,
) {
    support
        .registry
        .insert_named_actor("bot-provider", "Provider")
        .await;
    support
        .registry
        .save_created_by("bot-provider", created_by, true)
        .await
        .unwrap();
    support
        .registry
        .set_delivery_target(
            "bot-provider",
            BotDeliveryTarget::HttpProvider {
                bot_id: "bot-provider".to_string(),
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "bot-provider".to_string(),
                webhook_url: "https://provider.example.com/bcs/webhook".to_string(),
                bcs_to_provider_token: RedactedToken::new("secret-b2p"),
                protocol_version: "2.0".to_string(),
            },
        )
        .await;
    let mut provider = Participant::bot("bot-provider", ParticipantRole::Driver);
    provider.bot_name = Some("Provider".to_string());
    support
        .group
        .upsert(Group::new(
            "group-provider",
            "bot-provider",
            vec![
                provider,
                Participant {
                    bot_uuid: "human_1".to_string(),
                    bot_name: Some("Human One".to_string()),
                    kind: None,
                    role: ParticipantRole::Observer,
                    actor_kind: ActorKind::Human,
                    mode: Some(ParticipantMode::Present),
                },
            ],
        ))
        .await
        .unwrap();
}

#[tokio::test]
async fn web_send_explicit_mentions_do_not_inject_manager_worker_workers() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.driver_bot = "bot-driver".to_string();
    group.participants = vec![
        Participant {
            bot_uuid: "bot-driver".to_string(),
            bot_name: Some("Manager".to_string()),
            kind: None,
            role: ParticipantRole::Manager,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        },
        Participant {
            bot_uuid: "bot-observer".to_string(),
            bot_name: Some("Worker".to_string()),
            kind: None,
            role: ParticipantRole::Worker,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
        },
        Participant {
            bot_uuid: "human_1".to_string(),
            bot_name: Some("Human One".to_string()),
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
        },
    ];
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: Some("group-1:s1".to_string()),
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "@Manager ask worker later".to_string(),
            mentions: vec!["bot-driver".to_string()],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            sender_conn_id: None,
        })
        .await
        .unwrap();

    let delivered_bots: Vec<String> = outcome
        .bot_deliveries
        .iter()
        .map(|delivery| delivery.target_bot_id.clone())
        .collect();
    assert_eq!(delivered_bots, vec!["bot-driver".to_string()]);
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::Send]
    );
}

#[tokio::test]
async fn web_send_in_human_bot_dm_uses_dm_routing_and_keeps_frontend_echo() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    make_human_bot_dm(&support).await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "@bot-observer hello".to_string(),
            mentions: vec!["bot-driver".to_string()],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
        sender_conn_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert_eq!(outcome.delivered_count, 1);
    assert!(support.routing.route_calls().await.is_empty());
    assert_eq!(
        support.routing.dm_route_calls().await,
        vec![(
            "group-1".to_string(),
            "@bot-observer hello".to_string(),
            "human_1".to_string()
        )]
    );
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::Send]
    );
}

#[tokio::test]
async fn web_send_in_human_bot_dm_omits_group_context_by_default() -> ServiceResult<()> {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    make_human_bot_dm(&support).await;
    support.registry.set_protocol_version("bot-driver", 3).await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello direct".to_string(),
        mentions: vec!["bot-driver".to_string()],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        sender_conn_id: None,
    })
    .await?;

    let frames = support.bot_delivery.frames().await;
    let Some(BcsFrame::Request(req)) = frames.first() else {
        return Err(ServiceError::InternalError("expected request frame".to_string()));
    };
    let Some(params) = req.params.as_ref() else {
        return Err(ServiceError::InternalError("expected params".to_string()));
    };
    let Some(text) = params["message"]["content"][0]["text"].as_str() else {
        return Err(ServiceError::InternalError("expected text".to_string()));
    };
    assert_eq!(text, "hello direct");
    let Some(participants) = params["session_context"]["participants"].as_array() else {
        return Err(ServiceError::InternalError("expected participants".to_string()));
    };
    assert_eq!(participants.len(), 0);
    assert!(params["session_context"].get("group_type").is_none());
    assert!(params["session_context"].get("routing_mode").is_none());
    assert!(params["session_context"].get("recipient_role").is_none());
    Ok(())
}

#[tokio::test]
async fn web_send_blocking_interceptor_prevents_bot_delivery() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_interceptor(BlockingInterceptor);

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "hello".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
        sender_conn_id: None,
        })
        .await
        .unwrap();

    assert!(support.bot_delivery.frames().await.is_empty());
    assert_eq!(outcome.delivered_count, 0);
    assert_eq!(outcome.failed_count, 2);
    assert!(
        outcome
            .delivery_results
            .iter()
            .all(|result| result.error.as_deref() == Some("blocked by test"))
    );
}

#[tokio::test]
async fn web_send_delivery_frame_contains_recipient_group_context() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "@Driver hello".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
    sender_conn_id: None,
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let driver_frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("driver send frame");
    let BcsFrame::Request(req) = driver_frame else {
        panic!("expected request frame");
    };
    let context = req
        .params
        .as_ref()
        .and_then(|params| params.get("session_context"))
        .expect("session_context");
    assert_eq!(
        req.params
            .as_ref()
            .and_then(|params| params.get("timeout_ms")),
        None
    );
    assert_eq!(context["session_id"], "group-1");
    assert_eq!(context["recipient"], "bot-driver");
    assert_eq!(context["recipient_role"], "driver");
    assert_eq!(context["from_bot_id"], "human_1");
}

#[tokio::test]
async fn web_send_with_session_id_routes_v2_by_substituting_wire_group_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:abcdef12";
    let session = test_session(
        session_id,
        "group-1",
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            {
                let mut human = Participant::human("human_1", ParticipantRole::Observer);
                human.mode = Some(ParticipantMode::Present);
                human
            },
        ],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some(session_id.to_string()),
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello in session".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
    sender_conn_id: None,
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 1, "session participants should exclude bot-observer");
    let frontend_commands = support.frontend_delivery.commands().await;
    assert_eq!(
        frontend_commands[0].target,
        FrontendDeliveryTarget::Session {
            session_id: session_id.to_string()
        }
    );
    let BcsFrame::Request(req) = &frames[0] else {
        panic!("expected request frame");
    };
    assert_eq!(req.method, "chat.send");
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], session_id);
    assert_eq!(params["session_key"], "group:group-1:");
    assert!(
        params.get("bcs_session_id").is_none(),
        "legacy protocol clients should not receive bcs_session_id explicitly"
    );
}

#[tokio::test]
async fn web_send_with_legacy_session_id_routes_v2_with_group_wire_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:00000000";
    let session = test_session(
        session_id,
        "group-1",
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            {
                let mut human = Participant::human("human_1", ParticipantRole::Observer);
                human.mode = Some(ParticipantMode::Present);
                human
            },
        ],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some(session_id.to_string()),
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello in legacy session".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        sender_conn_id: None,
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 1);
    let BcsFrame::Request(req) = &frames[0] else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], "group-1");
    assert_eq!(params["session_key"], "group:group-1");
    assert!(params.get("bcs_session_id").is_none());
}

#[tokio::test]
async fn web_send_with_session_id_routes_v3_with_explicit_bcs_session_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:abcdef12";
    support.registry.set_protocol_version("bot-driver", 3).await;
    let session = test_session(
        session_id,
        "group-1",
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            {
                let mut human = Participant::human("human_1", ParticipantRole::Observer);
                human.mode = Some(ParticipantMode::Present);
                human
            },
        ],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some(session_id.to_string()),
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello in session".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
    sender_conn_id: None,
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 1);
    let BcsFrame::Request(req) = &frames[0] else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], "group-1");
    assert_eq!(params["bcs_session_id"], session_id);
    assert_eq!(params["session_key"], "group:group-1");
}

#[tokio::test]
async fn web_send_to_provider_with_session_id_uses_explicit_bcs_session_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:abcdef12";
    support
        .registry
        .set_delivery_target(
            "bot-driver",
            support::FakeRegistryService::provider_target("bot-driver"),
        )
        .await;
    let session = test_session(
        session_id,
        "group-1",
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            {
                let mut human = Participant::human("human_1", ParticipantRole::Observer);
                human.mode = Some(ParticipantMode::Present);
                human
            },
        ],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some(session_id.to_string()),
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "hello provider in session".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        sender_conn_id: None,
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    assert_eq!(frames.len(), 1);
    assert!(support.bot_delivery.targets().await[0].is_http_provider());
    let BcsFrame::Request(req) = &frames[0] else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], "group-1");
    assert_eq!(params["bcs_session_id"], session_id);
    assert_eq!(params["session_key"], "group:group-1");
}

#[tokio::test]
async fn web_send_direct_bot_projection_hides_bcs_group_context() -> ServiceResult<()> {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:abcdef12";
    support.registry.set_protocol_version("bot-driver", 3).await;
    let mut session = test_session(
        session_id,
        "group-1",
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            {
                let mut human = Participant::human("human_1", ParticipantRole::Observer);
                human.mode = Some(ParticipantMode::Present);
                human.bot_name = Some("张三".to_string());
                human
            },
        ],
    );
    session.meta = Some(json!({
        "channel": {
            "source": "dingtalk",
            "context_projection": "direct_bot"
        }
    }));
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some(session_id.to_string()),
        from_actor_id: "human_1".to_string(),
        from_name: Some("张三".to_string()),
        message: "帮我查一下".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        sender_conn_id: None,
    })
    .await?;

    let frames = support.bot_delivery.frames().await;
    let Some(BcsFrame::Request(req)) = frames.first() else {
        return Err(ServiceError::InternalError("expected request frame".to_string()));
    };
    let Some(params) = req.params.as_ref() else {
        return Err(ServiceError::InternalError("expected params".to_string()));
    };
    let Some(text) = params["message"]["content"][0]["text"].as_str() else {
        return Err(ServiceError::InternalError("expected text".to_string()));
    };
    assert_eq!(text, "帮我查一下");
    assert_eq!(params["session_context"]["session_id"], session_id);
    let Some(participants) = params["session_context"]["participants"].as_array() else {
        return Err(ServiceError::InternalError("expected participants".to_string()));
    };
    assert_eq!(participants.len(), 0);
    assert!(params["session_context"].get("group_type").is_none());
    assert!(params["session_context"].get("routing_mode").is_none());
    assert!(params["session_context"].get("recipient_role").is_none());
    Ok(())
}

#[tokio::test]
async fn web_send_prefers_human_from_name_in_delivered_frame() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .group
        .add_participant(
            "group-1",
            Participant::human("human_2", ParticipantRole::Observer),
        )
        .await
        .unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_2".to_string(),
            staff_no: "2".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_2".to_string(),
        from_name: Some("Alice Human".to_string()),
        message: "hello from a person".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
    sender_conn_id: None,
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let driver_frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("driver send frame");
    let BcsFrame::Request(req) = driver_frame else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["channel"]["user_id"], "Alice Human");
    assert_eq!(params["channel"]["actor_id"], "human_2");
    assert_eq!(params["channel"]["actor_name"], "Alice Human");
    assert_eq!(params["session_context"]["from"], "Alice Human(human_2)");
    assert!(
        params["message"]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("[from:Alice Human]hello from a person")
    );
}

#[tokio::test]
async fn web_send_inject_delivery_uses_event_frame() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_web_send(WebSendCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: None,
        from_actor_id: "human_1".to_string(),
        from_name: Some("Human One".to_string()),
        message: "observe this".to_string(),
        mentions: vec![],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
    sender_conn_id: None,
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let inject_frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.inject"))
        .expect("inject request frame");
    let BcsFrame::Request(req) = inject_frame else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], "group-1");
    assert_eq!(params["deliver"], false);
    assert!(
        frames
            .iter()
            .all(|frame| !matches!(frame, BcsFrame::Event(event) if event.event == "chat.inject"))
    );
}

#[tokio::test]
async fn web_send_delivers_to_private_group_targets() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .set_visibility("bot-observer", "private")
        .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "private targets remain in collaboration".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
        sender_conn_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.delivered_count, 2);
    assert_eq!(outcome.failed_count, 0);
    assert!(outcome
        .delivery_results
        .iter()
        .any(|result| result.bot_uuid == "bot-observer" && result.success));
    assert_eq!(support.bot_delivery.frames().await.len(), 2);
    assert!(support
        .bot_delivery
        .kinds()
        .await
        .contains(&BotDeliveryKind::Inject));
}

#[tokio::test]
async fn web_send_partial_delivery_failure_is_represented_in_outcome() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.bot_delivery.fail_for("bot-observer").await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: Some("Human One".to_string()),
            message: "partial".to_string(),
            mentions: vec![],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
        sender_conn_id: None,
        })
        .await
        .unwrap();

    assert_eq!(outcome.delivered_count, 1);
    assert_eq!(outcome.failed_count, 1);
    assert!(
        outcome
            .delivery_results
            .iter()
            .any(|result| result.bot_uuid == "bot-observer" && !result.success)
    );
}

#[tokio::test]
async fn group_chat_validates_sender_and_returns_legacy_delivery_projection() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-driver", "1", true)
        .await
        .unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_group_chat(GroupChatCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some("bot-driver".to_string()),
            message: "hello as my bot".to_string(),
            session_id: Some("session-1".to_string()),
        })
        .await
        .unwrap();

    assert_eq!(outcome.group_id, "group-1");
    assert_eq!(outcome.driver_bot_id, "bot-driver");
    assert_eq!(outcome.delivered_count, 2);
    assert_eq!(outcome.failed_count, 0);
    assert_eq!(
        support.routing.route_calls().await,
        vec![("group-1".to_string(), "hello as my bot".to_string(), None)]
    );
    let frames = support.bot_delivery.frames().await;
    assert!(
        frames
            .iter()
            .any(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
    );
}

#[tokio::test]
async fn group_chat_uses_session_participants_for_human_sender_validation() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:human-chat";
    let session = test_session(
        session_id,
        "group-1",
        vec![
            Participant::bot("bot-driver", ParticipantRole::Driver),
            Participant::human("human_2", ParticipantRole::Observer),
        ],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let outcome = flow
        .handle_group_chat(GroupChatCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_2".to_string(),
                staff_no: "2".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some("human_2".to_string()),
            message: "hello from the session Human".to_string(),
            session_id: Some(session_id.to_string()),
        })
        .await
        .unwrap();

    assert_eq!(outcome.delivered_count, 1);
    let frames = support.bot_delivery.frames().await;
    let send_frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("chat.send frame");
    let BcsFrame::Request(req) = send_frame else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["channel"]["actor_id"], "human_2");
    assert_eq!(params["session_context"]["from_bot_id"], "human_2");
}

#[tokio::test]
async fn group_chat_rejects_bot_that_is_not_a_session_participant() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:driver-only";
    let session = test_session(
        session_id,
        "group-1",
        vec![Participant::bot(
            "bot-driver",
            ParticipantRole::Driver,
        )],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let error = flow
        .handle_group_chat(GroupChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-observer".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some("bot-observer".to_string()),
            message: "should not be delivered".to_string(),
            session_id: Some(session_id.to_string()),
        })
        .await
        .unwrap_err();

    assert!(
        matches!(error, ServiceError::Unauthorized(message) if message.contains("not a participant"))
    );
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn group_chat_rejects_session_from_another_group() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "other-group:mismatched";
    let session = test_session(
        session_id,
        "other-group",
        vec![Participant::bot(
            "bot-driver",
            ParticipantRole::Driver,
        )],
    );
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let error = flow
        .handle_group_chat(GroupChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-driver".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some("bot-driver".to_string()),
            message: "should not be delivered".to_string(),
            session_id: Some(session_id.to_string()),
        })
        .await
        .unwrap_err();

    assert!(matches!(
        error,
        ServiceError::InvalidOperation { message, .. }
            if message == "session 'other-group:mismatched' does not belong to group 'group-1'"
    ));
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn group_chat_rejects_session_without_participants() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let session_id = "group-1:empty";
    let session = test_session(session_id, "group-1", Vec::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(StaticSessionManagement::new(session)));

    let error = flow
        .handle_group_chat(GroupChatCommand {
            caller: CallerContext::Bot(BotActor {
                bot_uuid: "bot-driver".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some("bot-driver".to_string()),
            message: "should not be delivered".to_string(),
            session_id: Some(session_id.to_string()),
        })
        .await
        .unwrap_err();

    assert!(matches!(
        error,
        ServiceError::InvalidOperation { message, .. }
            if message == "session 'group-1:empty' has no participants"
    ));
    assert!(support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn group_chat_treats_requested_sender_id_literally_without_trimming() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-driver", "1", true)
        .await
        .unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let error = flow
        .handle_group_chat(GroupChatCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            requested_sender_id: Some(" bot-driver ".to_string()),
            message: "hello as my bot".to_string(),
            session_id: Some("session-1".to_string()),
        })
        .await
        .unwrap_err();

    assert!(
        matches!(error, ServiceError::Unauthorized(message) if message.contains(" bot-driver "))
    );
}

#[tokio::test]
async fn group_chat_uses_human_staff_number_as_display_name_when_speaking_as_owned_bot() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-driver", "1", true)
        .await
        .unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_group_chat(GroupChatCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        requested_sender_id: Some("bot-driver".to_string()),
        message: "hello as my bot".to_string(),
        session_id: Some("session-1".to_string()),
    })
    .await
    .unwrap();

    let frames = support.bot_delivery.frames().await;
    let send_frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("chat.send frame");
    let BcsFrame::Request(req) = send_frame else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["channel"]["user_id"], "1");
    assert_eq!(params["channel"]["actor_id"], "bot-driver");
    assert_eq!(params["channel"]["actor_name"], "1");
    assert_eq!(params["session_context"]["from"], "1(bot-driver)");
}

#[tokio::test]
async fn persistent_group_send_routes_stores_and_returns_legacy_fields() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_persistent_group_send(PersistentGroupSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            sender: "human_1".to_string(),
            content: "@Driver persistent hello".to_string(),
            message_type: GroupMessageType::Bot,
            role: MessageRole::User,
            max_group_messages: 10,
            store_messages: true,
        })
        .await
        .unwrap();

    assert!(!outcome.message_id.is_empty());
    assert_eq!(
        outcome.routed_to,
        vec!["bot-driver".to_string(), "bot-observer".to_string()]
    );
    assert_eq!(outcome.mentions, Vec::<String>::new());
    assert_eq!(
        support.routing.route_calls().await,
        vec![(
            "group-1".to_string(),
            "@Driver persistent hello".to_string(),
            Some("human_1".to_string())
        )]
    );
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::Send, BotDeliveryKind::Inject]
    );
    assert!(support.routing.send_calls().await.is_empty());
    assert_eq!(support.group.message_count("group-1").await.unwrap(), 2);
    let group = support.group.get("group-1").await.unwrap();
    assert_eq!(group.messages.len(), 1);
    assert_eq!(group.messages[0].id, outcome.message_id);
    assert_eq!(group.messages[0].sender, "human_1");
    assert_eq!(group.messages[0].content, "@Driver persistent hello");
}

#[tokio::test]
async fn persistent_group_send_delivers_to_registered_provider_target_without_ws_connection() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .insert_named_actor("bot-provider", "Provider")
        .await;
    support
        .registry
        .set_delivery_target(
            "bot-provider",
            support::FakeRegistryService::provider_target("bot-provider"),
        )
        .await;
    let mut provider = Participant::bot("bot-provider", ParticipantRole::Driver);
    provider.bot_name = Some("Provider".to_string());
    let group = Group::new(
        "group-provider",
        "bot-provider",
        vec![
            provider,
            Participant {
                bot_uuid: "human_1".to_string(),
                bot_name: Some("Human One".to_string()),
                kind: None,
                role: ParticipantRole::Observer,
                actor_kind: ActorKind::Human,
                mode: Some(ParticipantMode::Present),
            },
        ],
    );
    support.group.upsert(group).await.unwrap();
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_persistent_group_send(PersistentGroupSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-provider".to_string(),
            sender: "human_1".to_string(),
            content: "hello provider".to_string(),
            message_type: GroupMessageType::Bot,
            role: MessageRole::User,
            max_group_messages: 10,
            store_messages: false,
        })
        .await
        .unwrap();

    assert_eq!(outcome.routed_to, vec!["bot-provider".to_string()]);
    assert!(support.routing.send_calls().await.is_empty());
    assert!(support.bot_delivery.targets().await[0].is_http_provider());
}

#[tokio::test]
async fn persistent_group_send_in_human_bot_dm_uses_dm_routing() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    make_human_bot_dm(&support).await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_persistent_group_send(PersistentGroupSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            sender: "human_1".to_string(),
            content: "dm hello".to_string(),
            message_type: GroupMessageType::Bot,
            role: MessageRole::User,
            max_group_messages: 10,
            store_messages: true,
        })
        .await
        .unwrap();

    assert_eq!(outcome.routed_to, vec!["bot-driver".to_string()]);
    assert_eq!(
        support.routing.dm_route_calls().await,
        vec![(
            "group-1".to_string(),
            "dm hello".to_string(),
            "human_1".to_string()
        )]
    );
    assert_eq!(
        support.bot_delivery.kinds().await,
        vec![BotDeliveryKind::Send]
    );
    assert!(support.routing.send_calls().await.is_empty());
}

#[tokio::test]
async fn persistent_group_send_marks_group_inactive_when_legacy_message_cap_is_reached() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let error = flow
        .handle_persistent_group_send(PersistentGroupSendCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            sender: "human_1".to_string(),
            content: "over cap".to_string(),
            message_type: GroupMessageType::Bot,
            role: MessageRole::User,
            max_group_messages: 1,
            store_messages: true,
        })
        .await
        .unwrap_err();

    assert!(matches!(
        error,
        ServiceError::MessageLimitReached(ref message)
            if message == "Group 'group-1' already has 1 messages (max 1)"
    ));
    assert_eq!(
        support.group.get("group-1").await.unwrap().status,
        GroupStatus::Inactive
    );
}

#[tokio::test]
async fn bot_final_event_routes_and_publishes_frontend_through_message_flow() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context.clone());

    let outcome = flow
        .handle_bot_event(BotEventCommand {
            bot_id: "bot-observer".to_string(),
            run_id: "run-1".to_string(),
            group_id: "group-1".to_string(),
            event_type: "chat".to_string(),
            event_payload: json!({
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "callback done"}],
                },
            }),
            bcs_session_id: None,
            state: ChatEventState::Final,
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert!(!outcome.bot_deliveries.is_empty());
    assert_eq!(
        support.routing.route_calls().await,
        vec![(
            "group-1".to_string(),
            "callback done".to_string(),
            Some("bot-observer".to_string())
        )]
    );
    let frames = support.bot_delivery.frames().await;
    let run_id = request_id_for_method(&frames, "chat.send");
    let context = run_context
        .get_context(&run_id)
        .await
        .expect("relay run context");
    assert_eq!(context.bot_id, "bot-driver");
    assert_eq!(context.group_id, "group-1");
}

#[tokio::test]
async fn bot_final_event_relay_to_provider_uses_explicit_bcs_session_id() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .set_delivery_target(
            "bot-driver",
            support::FakeRegistryService::provider_target("bot-driver"),
        )
        .await;
    let session_id = "group-1:abcdef12";
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-observer".to_string(),
        run_id: "run-provider-relay".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat".to_string(),
        event_payload: json!({
            "state": "final",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "callback done"}],
            },
        }),
        bcs_session_id: Some(session_id.to_string()),
        state: ChatEventState::Final,
    })
    .await
    .unwrap();

    assert!(support.bot_delivery.targets().await[0].is_http_provider());
    let frames = support.bot_delivery.frames().await;
    let frame = frames
        .iter()
        .find(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.send"))
        .expect("chat.send frame");
    let BcsFrame::Request(req) = frame else {
        panic!("expected request frame");
    };
    let params = req.params.as_ref().expect("params");
    assert_eq!(params["bcs_group_id"], "group-1");
    assert_eq!(params["bcs_session_id"], session_id);
    assert_eq!(params["session_key"], "group:group-1");
}

#[tokio::test]
async fn group_callback_command_routes_and_publishes_frontend_through_message_flow() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context.clone());

    let outcome = flow
        .handle_group_callback(GroupCallbackCommand {
            group_id: "group-1".to_string(),
            message: "system callback".to_string(),
            mentions: vec!["bot-driver".to_string()],
            metadata: Some(json!({"source": "contract"})),
            store_message: true,
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    assert_eq!(outcome.mentions, vec!["bot-driver".to_string()]);
    assert_eq!(outcome.delivered_count, 2);
    assert_eq!(
        support.group.get("group-1").await.unwrap().messages.len(),
        1
    );
    let frames = support.bot_delivery.frames().await;
    let run_id = request_id_for_method(&frames, "chat.send");
    let context = run_context
        .get_context(&run_id)
        .await
        .expect("callback run context");
    assert_eq!(context.bot_id, "bot-driver");
    assert_eq!(context.group_id, "group-1");
}

#[tokio::test]
async fn group_callback_continues_when_persistence_and_frontend_publish_fail() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.group.fail_add_message().await;
    support.frontend_delivery.fail_publish().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_group_callback(GroupCallbackCommand {
            group_id: "group-1".to_string(),
            message: "side effects can fail".to_string(),
            mentions: vec!["bot-driver".to_string()],
            metadata: Some(json!({"source": "contract"})),
            store_message: true,
        })
        .await
        .unwrap();

    assert_eq!(outcome.delivered_count, 2);
    assert_eq!(outcome.failed_count, 0);
    assert!(outcome.frontend_deliveries.is_empty());
    assert!(
        support
            .group
            .get("group-1")
            .await
            .unwrap()
            .messages
            .is_empty()
    );
}

#[tokio::test]
async fn group_callback_all_mentions_use_routing_service() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_group_callback(GroupCallbackCommand {
        group_id: "group-1".to_string(),
        message: "broadcast".to_string(),
        mentions: vec!["all".to_string()],
        metadata: None,
        store_message: false,
    })
    .await
    .unwrap();

    assert_eq!(
        support.routing.route_calls().await,
        vec![("group-1".to_string(), "@all broadcast".to_string(), None)]
    );
}

#[tokio::test]
async fn chat_abort_delivers_abort_frame_to_bot_participants() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            run_id: Some("run-1".to_string()),
        })
        .await
        .unwrap();

    assert!(outcome.aborted);
    assert_eq!(outcome.aborted_run_ids, vec!["run-1".to_string()]);
    assert!(
        support
            .bot_delivery
            .kinds()
            .await
            .iter()
            .all(|kind| *kind == BotDeliveryKind::Abort)
    );
    assert!(
        support
            .bot_delivery
            .frames()
            .await
            .into_iter()
            .all(|frame| matches!(frame, BcsFrame::Request(req) if req.method == "chat.abort"))
    );
}

#[tokio::test]
async fn chat_abort_publishes_frontend_event_through_port() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    let outcome = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: None,
            run_id: Some("run-1".to_string()),
        })
        .await
        .unwrap();

    assert_eq!(outcome.frontend_deliveries.len(), 1);
    let events = support.frontend_delivery.events().await;
    assert_eq!(events.len(), 1);
    assert!(events[0].contains(r#""event":"chat.abort""#));
    assert!(events[0].contains(r#""run_id":"run-1""#));
}

#[tokio::test]
async fn chat_abort_with_session_rejects_a_run_from_another_session() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    run_context
        .put_context(bcs_service_api::BotRunContext {
            run_id: "run-other-session".to_string(),
            bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("session-other".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context);

    let outcome = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: Some("session-bound".to_string()),
            run_id: Some("run-other-session".to_string()),
        })
        .await
        .unwrap();

    assert!(!outcome.aborted);
    assert!(outcome.aborted_run_ids.is_empty());
    assert!(support.bot_delivery.frames().await.is_empty());
    assert!(support.frontend_delivery.events().await.is_empty());
}

#[tokio::test]
async fn chat_abort_with_session_accepts_a_run_from_the_same_session() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    run_context
        .put_context(bcs_service_api::BotRunContext {
            run_id: "run-bound".to_string(),
            bot_id: "bot-driver".to_string(),
            group_id: "group-1".to_string(),
            bcs_session_id: Some("session-bound".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
    .with_bot_run_context(run_context);

    let outcome = flow
        .handle_chat_abort(ChatAbortCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            session_id: Some("session-bound".to_string()),
            run_id: Some("run-bound".to_string()),
        })
        .await
        .unwrap();

    assert!(outcome.aborted);
    assert_eq!(outcome.aborted_run_ids, vec!["run-bound".to_string()]);
    assert!(!support.bot_delivery.frames().await.is_empty());
}

#[tokio::test]
async fn chat_abort_without_run_id_uses_only_the_bound_session_key() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let flow = BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    );

    flow.handle_chat_abort(ChatAbortCommand {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".to_string(),
            staff_no: "1".to_string(),
        }),
        group_id: "group-1".to_string(),
        session_id: Some("session-bound".to_string()),
        run_id: None,
    })
    .await
    .unwrap();

    assert!(
        support
            .bot_delivery
            .frames()
            .await
            .into_iter()
            .all(|frame| matches!(
                frame,
                BcsFrame::Request(req)
                    if req.params.as_ref().and_then(|params| params["session_key"].as_str())
                        == Some("session-bound")
                    && req.params.as_ref().and_then(|params| params.get("run_id")).is_none()
            ))
    );
}

#[tokio::test]
async fn group_history_prefers_high_priority_even_when_low_priority_finishes_first() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();

    let mut delays = HashMap::new();
    delays.insert("bot-observer".to_string(), 300); // high-priority, finishes later
    delays.insert("bot-driver".to_string(), 50); // low-priority, finishes first

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Ok(json!({
            "messages": [
                {"id": "obs-1", "role": "assistant", "content": "observer msg", "timestamp": 1}
            ]
        })),
    );
    responses.insert(
        "bot-driver".to_string(),
        Ok(json!({
            "messages": [
                {"id": "drv-1", "role": "assistant", "content": "driver msg", "timestamp": 2}
            ]
        })),
    );

    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(delays, responses));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request.clone(),
    );

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: Some("bot-observer".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "observer msg");
}

#[tokio::test]
async fn group_history_falls_back_to_driver_when_view_bot_fails() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();

    let mut delays = HashMap::new();
    delays.insert("bot-observer".to_string(), 50);
    delays.insert("bot-driver".to_string(), 100);

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Err("observer_timeout".to_string()),
    );
    responses.insert(
        "bot-driver".to_string(),
        Ok(json!({
            "messages": [
                {"id": "drv-1", "role": "assistant", "content": "driver fallback", "timestamp": 2}
            ]
        })),
    );

    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(delays, responses));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request.clone(),
    );

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: Some("bot-observer".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 1);
    assert_eq!(result.messages[0].content, "driver fallback");
}

#[tokio::test]
async fn group_history_falls_back_to_store_when_all_bots_fail() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-observer", "1", true)
        .await
        .unwrap();

    let mut delays = HashMap::new();
    delays.insert("bot-observer".to_string(), 50);
    delays.insert("bot-driver".to_string(), 50);

    let mut responses = HashMap::new();
    responses.insert(
        "bot-observer".to_string(),
        Err("observer_timeout".to_string()),
    );
    responses.insert(
        "bot-driver".to_string(),
        Err("driver_timeout".to_string()),
    );

    let bot_request = Arc::new(ControllableHistoryBotRequest::with_delays(delays, responses));
    let history = BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        bot_request.clone(),
    );

    let result = history
        .get_history(GroupHistoryCommand {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_1".to_string(),
                staff_no: "1".to_string(),
            }),
            group_id: "group-1".to_string(),
            view_bot_id: Some("bot-observer".to_string()),
            limit: 500,
            before: None,
        })
        .await
        .unwrap();

    assert_eq!(result.messages.len(), 0);
}

fn request_id_for_method(frames: &[BcsFrame], method: &str) -> String {
    frames
        .iter()
        .find_map(|frame| match frame {
            BcsFrame::Request(req) if req.method == method => Some(req.id.clone()),
            _ => None,
        })
        .unwrap_or_else(|| panic!("{method} request frame not found"))
}
