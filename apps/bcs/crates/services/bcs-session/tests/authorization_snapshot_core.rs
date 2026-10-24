//! Core-forwarding conformance for the narrow
//! `SessionAuthorizationCoreService` (bot-authority plan
//! Task 6.4, spec §8.3): the core forwards the strict
//! repo read; it may not add a cache fallback or turn a
//! missing session into anything other than `Ok(None)`.

use std::sync::Arc;

use bcs_domain::{ActorKind, MessageViewScope, ParticipantMode, ParticipantRole};
use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::types::authorization_snapshot::SessionAuthorizationSnapshot;
use bcs_service_api::{
    Group as DomainGroup, Participant, ServiceError, ServiceResult, Session,
    SessionStatus,
};
use bcs_session::SessionAuthorizationCoreImpl;
use bcs_group_store::MemoryGroupRepo;
use bcs_session_store::MemorySessionRepo;

fn human_participant(actor_id: &str, scope: MessageViewScope) -> Participant {
    Participant {
        bot_uuid: actor_id.to_string(),
        bot_name: Some(actor_id.to_string()),
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: ActorKind::Human,
        mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: scope,
    }
}

#[tokio::test]
async fn core_forwards_the_strict_snapshot_read() {
    let sessions = Arc::new(MemorySessionRepo::new());
    let groups = Arc::new(MemoryGroupRepo::new());
    groups
        .upsert(DomainGroup::new("core-snap-group", "bot-driver", Vec::new()))
        .await
        .expect("upsert group");
    let session = sessions
        .create(
            "core-snap-group",
            NewSessionParams {
                participants: vec![human_participant(
                    "human-view",
                    MessageViewScope::Participant,
                )],
                created_by: Some("human-creator".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect("create session");

    let core = SessionAuthorizationCoreImpl::new(Arc::clone(&sessions) as Arc<dyn SessionRepoPort>);
    let snapshot: SessionAuthorizationSnapshot = core
        .read_authorization_snapshot(&session.id)
        .await
        .expect("strict read")
        .expect("snapshot");
    assert_eq!(snapshot.group_id, "core-snap-group");
    assert_eq!(snapshot.created_by.as_deref(), Some("human-creator"));
    assert_eq!(
        snapshot
            .participants
            .first()
            .expect("participant")
            .message_view_scope,
        MessageViewScope::Participant
    );

    // A missing session is `Ok(None)` — never an error and
    // never a fabricated empty snapshot.
    assert!(
        core.read_authorization_snapshot("never-created:00000000")
            .await
            .expect("missing is Ok(None)")
            .is_none()
    );

    // The core does not invent a `Group` fallback: reads go to
    // the Session port only (the Group store here carries
    // nothing for a session id).
    assert!(
        groups
            .read_authorization_snapshot("core-snap-group")
            .await
            .expect("group snapshot")
            .is_some()
    );
}

/// A repo that does not implement the snapshot read keeps the
/// port's fail-closed default: strict `Err`, not a silent
/// miss-passing-as-`None`.
struct NoSnapshotRepo;

#[async_trait::async_trait]
impl SessionRepoPort for NoSnapshotRepo {
    async fn get(&self, _session_id: &str) -> Option<Session> {
        None
    }
    async fn belongs_to_group(
        &self,
        _session_id: &str,
        _group_id: &str,
    ) -> bool {
        false
    }
    async fn create(
        &self,
        group_id: &str,
        _params: NewSessionParams,
    ) -> ServiceResult<Session> {
        Err(ServiceError::InternalError(format!(
            "create unsupported: {group_id}"
        )))
    }
    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<serde_json::Value>,
        _error: Option<String>,
    ) -> ServiceResult<Option<Session>> {
        Err(ServiceError::InternalError("complete unsupported".into()))
    }
    async fn reactivate(
        &self,
        _session_id: &str,
        _new_input: Option<serde_json::Value>,
    ) -> ServiceResult<Session> {
        Err(ServiceError::InternalError("reactivate unsupported".into()))
    }
    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> ServiceResult<Session> {
        Err(ServiceError::InternalError("add unsupported".into()))
    }
    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> ServiceResult<Session> {
        Err(ServiceError::InternalError("remove unsupported".into()))
    }
    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> ServiceResult<Session> {
        Err(ServiceError::InternalError("mode unsupported".into()))
    }
    async fn update_callback_status(
        &self,
        _session_id: &str,
        _status: &str,
    ) -> ServiceResult<()> {
        Err(ServiceError::InternalError("callback unsupported".into()))
    }
    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> ServiceResult<Session> {
        Err(ServiceError::InternalError("title unsupported".into()))
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
        Vec::new()
    }
    async fn latest_running(&self, _group_id: &str) -> Option<Session> {
        None
    }
    async fn count_running_service(&self, _group_id: &str) -> u64 {
        0
    }
    async fn list_running_service(&self, _offset: u64, _limit: u64) -> Vec<Session> {
        Vec::new()
    }
    async fn list_group_ids_by_session_participant(&self, _bot_uuid: &str) -> Vec<String> {
        Vec::new()
    }
    async fn delete(&self, _session_id: &str) -> ServiceResult<bool> {
        Err(ServiceError::InternalError("delete unsupported".into()))
    }
}

#[tokio::test]
async fn unconfigured_repo_forward_fails_closed() {
    let core = SessionAuthorizationCoreImpl::new(Arc::new(NoSnapshotRepo));
    let error = core
        .read_authorization_snapshot("any-session")
        .await
        .err()
        .expect("unconfigured repo read is Err");
    matches!(error, ServiceError::InternalError(_));
}
