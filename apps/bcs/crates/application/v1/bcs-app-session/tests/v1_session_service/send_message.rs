use super::*;

use bcs_service_api::{GroupChatCommand, GroupChatOutcome, MessageFlowService, ServiceResult};

#[derive(Default)]
struct RecordingMessageFlow {
    calls: Mutex<Vec<GroupChatCommand>>,
}

#[async_trait]
impl MessageFlowService for RecordingMessageFlow {
    async fn handle_web_send(
        &self,
        _cmd: bcs_service_api::WebSendCommand,
    ) -> ServiceResult<bcs_service_api::WebSendOutcome> {
        unimplemented!("web send is not used by send-into-Session tests")
    }

    async fn handle_group_chat(
        &self,
        cmd: GroupChatCommand,
    ) -> ServiceResult<GroupChatOutcome> {
        self.calls.lock().expect("message flow lock").push(cmd.clone());
        Ok(GroupChatOutcome {
            queue_admission: None,
            group_id: cmd.group_id.clone(),
            driver_bot_id: "driver".into(),
            delivered_count: 2,
            failed_count: 0,
            delivery_results: Vec::new(),
            mentions: Vec::new(),
            hidden_mentions: Vec::new(),
        })
    }

    async fn handle_bot_event(
        &self,
        _cmd: bcs_service_api::BotEventCommand,
    ) -> ServiceResult<bcs_service_api::BotEventOutcome> {
        unimplemented!("bot events are not used by send-into-Session tests")
    }

    async fn handle_group_callback(
        &self,
        _cmd: bcs_service_api::GroupCallbackCommand,
    ) -> ServiceResult<bcs_service_api::GroupCallbackOutcome> {
        unimplemented!("group callbacks are not used by send-into-Session tests")
    }

    async fn handle_chat_abort(
        &self,
        _cmd: bcs_service_api::ChatAbortCommand,
    ) -> ServiceResult<bcs_service_api::ChatAbortOutcome> {
        unimplemented!("chat aborts are not used by send-into-Session tests")
    }

    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<bcs_service_api::TaskRunAliasRegistration> {
        unimplemented!("task aliases are not used by send-into-Session tests")
    }

    async fn handle_task_dispatch(
        &self,
        _cmd: bcs_service_api::TaskDispatchCommand,
    ) -> ServiceResult<bcs_service_api::TaskDispatchOutcome> {
        unimplemented!("task dispatch is not used by send-into-Session tests")
    }

    async fn handle_task_complete(
        &self,
        _cmd: bcs_service_api::TaskCompleteCommand,
    ) -> ServiceResult<bcs_service_api::TaskCompleteOutcome> {
        unimplemented!("task completion is not used by send-into-Session tests")
    }
}

impl Fixture {
    fn with_message_flow(
        mut self,
        message_flow: Arc<dyn MessageFlowService>,
    ) -> Self {
        self.service = self.service.with_message_flow(message_flow);
        self
    }
}

async fn seed_session_with_human(fixture: &Fixture) -> String {
    fixture.add_bot("driver").await;
    fixture.store_group("g1", "driver", None).await;
    let group = fixture.groups.get("g1").await.expect("group exists");
    let session = fixture
        .session_repo
        .create(
            "g1",
            NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: vec![
                    Participant::bot("driver", ParticipantRole::Driver),
                    Participant::human("human_staff-1", ParticipantRole::Observer),
                ],
                group_version: Some(group.version),
                ..Default::default()
            },
        )
        .await
        .expect("seed session");
    session.id
}

#[tokio::test]
async fn send_message_binds_the_authenticated_human_and_reports_delivery() {
    let flow = Arc::new(RecordingMessageFlow::default());
    let fixture = Fixture::new().await.with_message_flow(flow.clone());
    let session_id = seed_session_with_human(&fixture).await;

    let outcome = SessionMessageService::send(
        &fixture.service,
        SendSessionMessage {
            caller: human_principal("staff-1"),
            session_id: session_id.clone(),
            message: "hello into the session".into(),
        },
    )
    .await
    .expect("send message");

    assert_eq!(outcome.session_id, session_id);
    assert_eq!(outcome.group_id, "g1");
    assert_eq!(outcome.sender, "human_staff-1");
    assert_eq!(outcome.delivered_count, 2);

    let human_inputs = fixture
        .runtime
        .human_input_calls
        .lock()
        .expect("runtime human input lock");
    let human_input = human_inputs.last().expect("runtime human input call");
    assert_eq!(human_input.session_id.as_deref(), Some(session_id.as_str()));
    assert_eq!(human_input.caller_actor_id, "human_staff-1");
    assert_eq!(human_input.content, "hello into the session");
    drop(human_inputs);

    let calls = flow.calls.lock().expect("message flow lock");
    let call = calls.last().expect("group chat call");
    assert_eq!(call.group_id, "g1");
    assert_eq!(call.session_id.as_deref(), Some(session_id.as_str()));
    assert_eq!(call.message, "hello into the session");
    assert_eq!(call.requested_sender_id.as_deref(), Some("human_staff-1"));
    assert_eq!(
        call.caller,
        CallerContext::Human(HumanActor {
            actor_id: "human_staff-1".into(),
            staff_no: "staff-1".into(),
        })
    );
}

#[tokio::test]
async fn send_message_rejects_non_participants_before_the_message_flow() {
    let flow = Arc::new(RecordingMessageFlow::default());
    let fixture = Fixture::new().await.with_message_flow(flow.clone());
    let session_id = seed_session_with_human(&fixture).await;

    let error = SessionMessageService::send(
        &fixture.service,
        SendSessionMessage {
            caller: human_principal("staff-2"),
            session_id,
            message: "should not be delivered".into(),
        },
    )
    .await
    .expect_err("non-participant must be refused");

    assert!(matches!(error, V1ApplicationError::Forbidden(_)));
    assert!(flow.calls.lock().expect("message flow lock").is_empty());
    assert!(
        fixture
            .runtime
            .human_input_calls
            .lock()
            .expect("runtime human input lock")
            .is_empty()
    );
}

#[tokio::test]
async fn send_message_rejects_blank_message_before_lookup() {
    let flow = Arc::new(RecordingMessageFlow::default());
    let fixture = Fixture::new().await.with_message_flow(flow.clone());

    let error = SessionMessageService::send(
        &fixture.service,
        SendSessionMessage {
            caller: human_principal("staff-1"),
            session_id: "missing-session".into(),
            message: "   \n\t ".into(),
        },
    )
    .await
    .expect_err("blank message must be refused");

    assert!(matches!(
        error,
        V1ApplicationError::InvalidInput { ref code, .. } if code == "invalid_request"
    ));
    assert!(flow.calls.lock().expect("message flow lock").is_empty());
    assert!(
        fixture
            .runtime
            .human_input_calls
            .lock()
            .expect("runtime human input lock")
            .is_empty()
    );
}

#[tokio::test]
async fn send_message_fails_closed_without_a_wired_message_flow() {
    let fixture = Fixture::new().await;
    let session_id = seed_session_with_human(&fixture).await;

    let error = SessionMessageService::send(
        &fixture.service,
        SendSessionMessage {
            caller: human_principal("staff-1"),
            session_id,
            message: "no flow".into(),
        },
    )
    .await
    .expect_err("missing message flow must fail closed");

    assert!(matches!(error, V1ApplicationError::Internal(_)));
}
