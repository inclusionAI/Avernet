use std::sync::Arc;

use bcs_service_api::application::session::{
    CreateOrReactivateCommand, SessionManagementService, SessionUseCaseError,
};
use std::collections::HashSet;

use async_trait::async_trait;
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::{
    BotDeliveryTarget, BotRuntimeConnectCommand, BotRuntimeConnectOutcome,
    BotRuntimeConnectionService, BotRuntimeDisconnectCommand, BotRuntimeStatusCommand,
    BotRuntimeStatusOutcome, BotUseCaseError, Group, GroupStrategy, Participant, ParticipantRole,
    ParticipantMode, ServiceError, ServiceResult, Session, SessionKind, SessionStatus,
};
use bcs_group_store::MemoryGroupRepo;
use bcs_session::{NoopSessionManagementService, SessionManagementServiceImpl};
use bcs_session_store::MemorySessionRepo;

fn participants() -> Vec<Participant> {
    vec![Participant::bot("bot1", ParticipantRole::Driver)]
}

fn manager_worker_participants(worker_id: &str) -> Vec<Participant> {
    vec![
        Participant::bot("manager", ParticipantRole::Manager),
        Participant::bot(worker_id, ParticipantRole::Worker),
    ]
}

struct FailingMembershipSessionRepo;

#[async_trait]
impl SessionRepoPort for FailingMembershipSessionRepo {
    async fn create(&self, _group_id: &str, _params: NewSessionParams) -> ServiceResult<Session> {
        unimplemented!("not used by membership error test")
    }

    async fn get(&self, _session_id: &str) -> Option<Session> {
        unimplemented!("not used by membership error test")
    }

    async fn belongs_to_group(&self, _session_id: &str, _group_id: &str) -> bool {
        unimplemented!("not used by membership error test")
    }

    async fn list_by_group(
        &self,
        _group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Vec<Session> {
        unimplemented!("not used by membership error test")
    }

    async fn latest_running(&self, _group_id: &str) -> Option<Session> {
        unimplemented!("not used by membership error test")
    }

    async fn count_running_service(&self, _group_id: &str) -> u64 {
        unimplemented!("not used by membership error test")
    }

    async fn list_running_service(&self, _offset: u64, _limit: u64) -> Vec<Session> {
        unimplemented!("not used by membership error test")
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> ServiceResult<Option<Session>> {
        unimplemented!("not used by membership error test")
    }

    async fn reactivate(
        &self,
        _session_id: &str,
        _new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Session> {
        unimplemented!("not used by membership error test")
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> ServiceResult<Session> {
        unimplemented!("not used by membership error test")
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> ServiceResult<Session> {
        unimplemented!("not used by membership error test")
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> ServiceResult<Session> {
        unimplemented!("not used by membership error test")
    }

    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> ServiceResult<()> {
        unimplemented!("not used by membership error test")
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> ServiceResult<Session> {
        unimplemented!("not used by membership error test")
    }

    async fn list_group_ids_by_session_participant(&self, _bot_uuid: &str) -> Vec<String> {
        Vec::new()
    }

    async fn try_list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> ServiceResult<Vec<String>> {
        Err(ServiceError::InternalError(
            "session membership store unavailable".into(),
        ))
    }

    async fn delete(&self, _session_id: &str) -> ServiceResult<bool> {
        unimplemented!("not used by membership error test")
    }
}

#[tokio::test]
async fn impl_propagates_session_participant_group_list_failure() {
    let repo = Arc::new(FailingMembershipSessionRepo);
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let svc = SessionManagementServiceImpl::new(repo, group_repo);

    let result = svc
        .list_group_ids_by_session_participant("bot-1")
        .await;

    assert!(matches!(
        result,
        Err(SessionUseCaseError::Internal(ServiceError::InternalError(message)))
            if message == "session membership store unavailable"
    ));
}

#[tokio::test]
async fn impl_creates_chat_session() {
    let repo = Arc::new(MemorySessionRepo::new());
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let svc = SessionManagementServiceImpl::new(repo, group_repo);
    let outcome = svc
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "g1".to_string(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: participants(),
                ..Default::default()
            },
        })
        .await
        .expect("create chat ok");
    assert!(outcome.created);
    assert_eq!(outcome.session.status, SessionStatus::Running);
}

#[tokio::test]
async fn impl_blocks_reactivate_when_callback_pending() {
    let repo = Arc::new(MemorySessionRepo::new());
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let svc = SessionManagementServiceImpl::new(repo.clone(), group_repo);

    // Create svc-invocation session
    let outcome = svc
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "g".to_string(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: participants(),
                ..Default::default()
            },
        })
        .await
        .expect("create svc ok");
    let sid = outcome.session.id.clone();

    // Complete it (callback_status remains "pending")
    let completed = svc.complete_if_running(&sid, None, None).await.expect("complete");
    assert!(completed.is_some());

    // Reactivate should fail with CallbackPending or Internal(SessionCallbackPending)
    let r = svc
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "g".to_string(),
            session_id: Some(sid),
            params: NewSessionParams::default(),
        })
        .await;
    match r {
        Err(SessionUseCaseError::CallbackPending(_) | SessionUseCaseError::Internal(_)) => {}
        other => panic!("expected CallbackPending or Internal-ServiceCallbackPending, got {other:?}"),
    }
}

#[tokio::test]
async fn impl_complete_if_running_is_idempotent_via_cas() {
    let repo = Arc::new(MemorySessionRepo::new());
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let svc = SessionManagementServiceImpl::new(repo, group_repo);
    let outcome = svc
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "g".to_string(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: participants(),
                ..Default::default()
            },
        })
        .await
        .expect("create");
    let sid = outcome.session.id.clone();

    let first = svc.complete_if_running(&sid, None, None).await.expect("first");
    assert!(first.is_some());
    let again = svc.complete_if_running(&sid, None, None).await.expect("second");
    assert!(again.is_none(), "CAS short-circuit");
}

#[tokio::test]
async fn noop_returns_empty_for_reads_and_conflict_for_writes() {
    let svc = NoopSessionManagementService::default();

    assert!(svc.get("any").await.expect("noop get").is_none());
    assert!(svc.list_by_group("any", None, 0, 10, None, None).await.expect("noop list").is_empty());

    let r = svc
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "g".to_string(),
            session_id: None,
            params: NewSessionParams::default(),
        })
        .await;
    assert!(matches!(r, Err(SessionUseCaseError::Conflict(_))));
}

#[tokio::test]
async fn impl_allows_provider_downlink_participant_when_creating_manager_worker_session() {
    let repo = Arc::new(MemorySessionRepo::new());
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let mut group = Group::new(
        "g",
        "manager",
        manager_worker_participants("regular-worker"),
    );
    group.group_strategy = GroupStrategy::ManagerWorker;
    group_repo.upsert(group).await.expect("seed group");
    let svc = SessionManagementServiceImpl::new(repo, group_repo)
        .with_bot_runtime(Arc::new(FakeBotRuntimeConnectionService::new([
            "provider-worker",
        ])));

    let outcome = svc
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "g".to_string(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: manager_worker_participants("provider-worker"),
                ..Default::default()
            },
        })
        .await
        .expect("provider downlink bot can join manager_worker session");
    assert!(
        outcome
            .session
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == "provider-worker")
    );
}

#[tokio::test]
async fn impl_allows_provider_downlink_participant_added_to_manager_worker_session() {
    let repo = Arc::new(MemorySessionRepo::new());
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let mut group = Group::new(
        "g",
        "manager",
        manager_worker_participants("regular-worker"),
    );
    group.group_strategy = GroupStrategy::ManagerWorker;
    group_repo.upsert(group).await.expect("seed group");
    let svc = SessionManagementServiceImpl::new(repo, group_repo)
        .with_bot_runtime(Arc::new(FakeBotRuntimeConnectionService::new([
            "provider-worker",
        ])));

    let outcome = svc
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "g".to_string(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::Chat,
                participants: manager_worker_participants("regular-worker"),
                ..Default::default()
            },
        })
        .await
        .expect("regular manager_worker session should be created");

    let session = svc
        .add_participant(
            &outcome.session.id,
            Participant::bot("provider-worker", ParticipantRole::Worker),
        )
        .await
        .expect("provider downlink bot can be added to manager_worker session");
    assert!(
        session
            .participants
            .iter()
            .any(|participant| participant.bot_uuid == "provider-worker")
    );
}

/// Bug fix #9: legacy server.rs:12529-12535 returns 409
/// `session_is_running_cannot_invoke` when reactivating a service-invocation
/// session that is still Running. The use-case error must be `Conflict`,
/// not `InvalidParams`, so the HTTP layer maps it to 409 (not 400).
#[tokio::test]
async fn impl_reactivate_running_service_session_returns_conflict() {
    let repo = Arc::new(MemorySessionRepo::new());
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let svc = SessionManagementServiceImpl::new(repo.clone(), group_repo);

    // Create a running ServiceInvocation session.
    let outcome = svc
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "g".to_string(),
            session_id: None,
            params: NewSessionParams {
                session_kind: SessionKind::ServiceInvocation,
                participants: participants(),
                ..Default::default()
            },
        })
        .await
        .expect("create svc");
    let sid = outcome.session.id.clone();
    assert_eq!(outcome.session.status, SessionStatus::Running);

    // Try to reactivate while still Running → must be Conflict (→ 409 in HTTP).
    let r = svc
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: "g".to_string(),
            session_id: Some(sid),
            params: NewSessionParams::default(),
        })
        .await;
    match r {
        Err(SessionUseCaseError::Conflict(_)) => {}
        other => panic!("expected Conflict for Running reactivate, got {other:?}"),
    }
}

struct FakeBotRuntimeConnectionService {
    provider_downlink_bots: HashSet<String>,
}

impl FakeBotRuntimeConnectionService {
    fn new<const N: usize>(provider_downlink_bots: [&str; N]) -> Self {
        Self {
            provider_downlink_bots: provider_downlink_bots
                .into_iter()
                .map(str::to_string)
                .collect(),
        }
    }
}

#[async_trait]
impl BotRuntimeConnectionService for FakeBotRuntimeConnectionService {
    async fn connect_streaming(
        &self,
        _command: BotRuntimeConnectCommand,
    ) -> Result<BotRuntimeConnectOutcome, BotUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn update_runtime_status(
        &self,
        _command: BotRuntimeStatusCommand,
    ) -> Result<BotRuntimeStatusOutcome, BotUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn disconnect_streaming(
        &self,
        _command: BotRuntimeDisconnectCommand,
    ) -> Result<(), BotUseCaseError> {
        unimplemented!("not needed by this test")
    }

    async fn is_provider_downlink_bot(&self, bot_id: &str) -> ServiceResult<bool> {
        Ok(self.provider_downlink_bots.contains(bot_id))
    }

    async fn resolve_delivery_target(&self, bot_id: &str) -> ServiceResult<BotDeliveryTarget> {
        Ok(BotDeliveryTarget::WebSocket {
            bot_id: bot_id.to_string(),
        })
    }
}
