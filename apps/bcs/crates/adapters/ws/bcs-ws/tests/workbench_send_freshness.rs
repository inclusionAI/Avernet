//! F4 WS integration pin: `chat.send` is authorized per request
//! against the CURRENT facts — the transport connection staying
//! open grants nothing by itself. A Bot removed from the Session
//! while its Human's WebSocket stays connected cannot land a new
//! message afterwards: no persistence, no queue admission, no
//! Bot delivery, no HumanInput side effect.
//!
//! The Workbench seat runs the REAL `GroupManagement`
//! (memory repos + real strict Session authorization snapshot
//! core), so the removal between the two sends is exercised
//! through production code, not a fake flag.

mod support;

use std::sync::Arc;

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_friend::FriendCore;
use bcs_group::{GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo};
use bcs_protocol::{BcsFrame, RequestFrame, ResponseFrame};
use bcs_relation::RelationCore;
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    BotCapabilities, BotEventCommand, BotEventOutcome, BotRegistryCoreService, ChatAbortCommand,
    ChatAbortOutcome, Group, GroupCallbackCommand, GroupCallbackOutcome, GroupChatCommand,
    GroupChatOutcome, GroupCoreService, MessageFlowService, Participant, ParticipantRole,
    PersistentGroupSendCommand, PersistentGroupSendOutcome, ServiceError, ServiceResult,
    SessionKind, TaskCompleteCommand, TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome,
    TaskRunAliasRegistration, WebSendCommand, WebSendOutcome,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{NoopCollaborationRuntimeService, NoopInteractionService, NoopSystemMessageService};
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::{
    WebClientConnectionState, WebDispatchState, WorkbenchConnectionAuth,
    WorkbenchConnectionRegistry, dispatch_client_frame,
};
use tokio::sync::{Mutex, mpsc};

const GROUP: &str = "group-ws-f4";
const SESSION: &str = "group-ws-f4:abcd1234";

/// Records every `handle_web_send` (the single ingress to
/// persistence, queue admission and Bot delivery); the remaining
/// trait surface errors like the test-support noop if reached.
#[derive(Default)]
struct SpyMessageFlow {
    web_sends: Mutex<Vec<WebSendCommand>>,
}

#[async_trait]
impl MessageFlowService for SpyMessageFlow {
    async fn handle_web_send(&self, cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
        self.web_sends.lock().await.push(cmd);
        Ok(WebSendOutcome {
            operation_id: "op-test".to_string(),
            queue_admission: None,
            primary_run_id: "run-spy".into(),
            status: "started".into(),
            active_run_ids: Vec::new(),
            bot_deliveries: Vec::new(),
            frontend_deliveries: Vec::new(),
            mentions: Vec::new(),
            hidden_mentions: Vec::new(),
            delivered_count: 0,
            failed_count: 0,
            delivery_results: Vec::new(),
        })
    }

    async fn handle_group_chat(&self, _cmd: GroupChatCommand) -> ServiceResult<GroupChatOutcome> {
        Err(ServiceError::InternalError("not reached by this test".into()))
    }

    async fn handle_persistent_group_send(
        &self,
        _cmd: PersistentGroupSendCommand,
    ) -> ServiceResult<PersistentGroupSendOutcome> {
        Err(ServiceError::InternalError("not reached by this test".into()))
    }

    async fn handle_bot_event(&self, _cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
        Err(ServiceError::InternalError("not reached by this test".into()))
    }

    async fn handle_group_callback(
        &self,
        _cmd: GroupCallbackCommand,
    ) -> ServiceResult<GroupCallbackOutcome> {
        Err(ServiceError::InternalError("not reached by this test".into()))
    }

    async fn handle_chat_abort(&self, _cmd: ChatAbortCommand) -> ServiceResult<ChatAbortOutcome> {
        Err(ServiceError::InternalError("not reached by this test".into()))
    }

    async fn register_task_run_alias(
        &self,
        _task_id: &str,
        _run_id: &str,
        _bot_id: &str,
    ) -> ServiceResult<TaskRunAliasRegistration> {
        Err(ServiceError::InternalError("not reached by this test".into()))
    }

    async fn handle_task_dispatch(
        &self,
        _cmd: TaskDispatchCommand,
    ) -> ServiceResult<TaskDispatchOutcome> {
        Err(ServiceError::InternalError("not reached by this test".into()))
    }

    async fn handle_task_complete(
        &self,
        _cmd: TaskCompleteCommand,
    ) -> ServiceResult<TaskCompleteOutcome> {
        Err(ServiceError::InternalError("not reached by this test".into()))
    }
}

struct WsFixture {
    state: Arc<WebDispatchState>,
    flow: Arc<SpyMessageFlow>,
    session_repo: Arc<dyn SessionRepoPort>,
}

async fn ws_fixture() -> WsFixture {
    let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
    let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
    let session_repo: Arc<dyn SessionRepoPort> = Arc::new(MemorySessionRepo::new());
    let session_management = Arc::new(SessionManagementServiceImpl::new(
        session_repo.clone(),
        group_repo,
    ));
    let bots = Arc::new(BotCore::memory());
    let authority_repo = Arc::new(MemoryBotRepo::new());
    // alice CURRENTLY owns b2 (the sender bot); the group/session
    // below mirrors a real Workbench collaboration.
    bots.register("b2".into(), BotCapabilities::default())
        .await
        .expect("register b2");
    bcs_test_support::grant_owner_edge(&authority_repo, "b2", "alice").await;
    let group = Group::new(
        GROUP,
        "b1",
        vec![
            Participant::bot("b1", ParticipantRole::Driver),
            Participant::bot("b2", ParticipantRole::Consultant),
        ],
    );
    groups.upsert(group.clone()).await.expect("store group");
    session_repo
        .create(
            GROUP,
            NewSessionParams {
                id: Some(SESSION.into()),
                session_kind: SessionKind::Chat,
                participants: group.participants.clone(),
                created_by: Some("b1".into()),
                ..Default::default()
            },
        )
        .await
        .expect("store session");

    let workbench = Arc::new(
        GroupManagement::new(
            groups,
            bots,
            Arc::new(FriendCore::memory()),
            Arc::new(RelationCore::memory()),
            GroupConfig::default(),
            session_management,
            Arc::new(NoopSystemMessageService),
        )
        .with_bot_authority(bcs_test_support::authority_hook_for(authority_repo))
        .with_session_authorization(Arc::new(bcs_session::SessionAuthorizationCoreImpl::new(
            session_repo.clone(),
        ))),
    );

    let flow = Arc::new(SpyMessageFlow::default());
    let state = Arc::new(WebDispatchState {
        message_flow: flow.clone(),
        collaboration_runtime: Arc::new(NoopCollaborationRuntimeService),
        workbench_sessions: workbench,
        interactions: Arc::new(NoopInteractionService),
        group_session_connections: None,
        frontend_connections: Arc::new(WorkbenchConnectionRegistry::new()),
        run_channels: Arc::new(RunChannelManager::new()),
        delivery_admission: Arc::new(support::AllowAllAdmission),
    });
    WsFixture {
        state,
        flow,
        session_repo,
    }
}

fn chat_send(id: &str, message: &str) -> String {
    serde_json::to_string(&BcsFrame::Request(RequestFrame::new(
        id,
        "chat.send",
        Some(serde_json::json!({
            "group_id": GROUP,
            "sessionId": SESSION,
            "bot_uuid": "b2",
            "message": message,
        })),
    )))
    .unwrap()
}

async fn recv_response(rx: &mut mpsc::Receiver<String>) -> ResponseFrame {
    let raw = rx.recv().await.expect("expected a ws response");
    match serde_json::from_str::<BcsFrame>(&raw).unwrap() {
        BcsFrame::Response(response) => response,
        other => panic!("expected a response frame, got {other:?}"),
    }
}

#[tokio::test]
async fn stale_connection_send_is_reauthorized_and_rejected_after_session_removal() {
    let fixture = ws_fixture().await;
    let (tx, mut rx) = mpsc::channel(8);
    // ONE long-lived Workbench connection reused for both sends.
    let mut connection = WebClientConnectionState::default();
    let _protected_rx = connection.open_protected_queue();
    let auth = WorkbenchConnectionAuth::UserBound {
        actor_id: Some("human_alice".to_string()),
    };

    // Send #1 (b2 still in the Session): admitted end-to-end.
    dispatch_client_frame(&fixture.state, &chat_send("send-1", "hello"), &tx, &mut connection, &auth)
        .await
        .expect("first send dispatches");
    let first = recv_response(&mut rx).await;
    assert!(first.ok, "the current member's send is admitted: {first:?}");
    assert_eq!(fixture.flow.web_sends.lock().await.len(), 1);

    // The removal commits while the transport connection stays open.
    fixture
        .session_repo
        .remove_participant(SESSION, "b2")
        .await
        .expect("remove b2 from the session");

    // Send #2 (same socket, same auth context): per-request
    // strict authorization rejects; nothing downstream happens.
    dispatch_client_frame(&fixture.state, &chat_send("send-2", "sneaked"), &tx, &mut connection, &auth)
        .await
        .expect("second send dispatches to the gate");
    let second = recv_response(&mut rx).await;
    assert!(!second.ok, "the removed member's send is rejected: {second:?}");
    let error = second.error.expect("rejection carries the error");
    assert_eq!(error.code, "sender_not_in_group");
    assert_eq!(
        fixture.flow.web_sends.lock().await.len(),
        1,
        "no persistence, no queue admission, no Bot delivery request"
    );
    assert!(
        rx.try_recv().is_err(),
        "no HumanInput or queue side frames follow the rejection"
    );
}
