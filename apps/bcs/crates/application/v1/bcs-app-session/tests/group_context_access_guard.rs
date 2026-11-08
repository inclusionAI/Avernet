use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_session::GroupContextAddAccessGuard;
use bcs_domain::{GroupContextPolicy, ScopeLevel};
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::application::v1::{
    AddGroupContext, ApplicationError, GetGroupContext, GroupContextAck,
    GroupContextApplicationService, GroupContextGetResponse, GroupContextListResponse,
    GroupContextRequestContext, ListGroupContexts, SystemSetGroupContext, UpdateGroupContext,
};
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_service_api::{
    Group, GroupCoreService, NewSessionParams, Participant, ParticipantRole, SessionKind,
};
use bcs_session_store::MemorySessionRepo;

const GROUP_ID: &str = "bcs_grp_gcguard";
const SESSION_ID: &str = "bcs_grp_gcguard:00000001";

#[derive(Default)]
struct RecordingGroupContextService {
    adds: Mutex<Vec<AddGroupContext>>,
}

#[async_trait]
impl GroupContextApplicationService for RecordingGroupContextService {
    async fn add(&self, command: AddGroupContext) -> Result<GroupContextAck, ApplicationError> {
        self.adds.lock().expect("adds lock").push(command);
        Ok(GroupContextAck::ok())
    }

    async fn update(
        &self,
        _command: UpdateGroupContext,
    ) -> Result<GroupContextAck, ApplicationError> {
        Ok(GroupContextAck::ok())
    }

    async fn list(
        &self,
        _query: ListGroupContexts,
    ) -> Result<GroupContextListResponse, ApplicationError> {
        Ok(GroupContextListResponse {
            contexts: Vec::new(),
        })
    }

    async fn get(
        &self,
        _query: GetGroupContext,
    ) -> Result<GroupContextGetResponse, ApplicationError> {
        Ok(GroupContextGetResponse { items: Vec::new() })
    }

    async fn systemset(
        &self,
        _command: SystemSetGroupContext,
    ) -> Result<GroupContextAck, ApplicationError> {
        Ok(GroupContextAck::ok())
    }
}

async fn guard_with_group_member(
    group_member: &'static str,
) -> (
    GroupContextAddAccessGuard,
    Arc<RecordingGroupContextService>,
) {
    let group_repo = Arc::new(MemoryGroupRepo::new());
    let groups = Arc::new(GroupCore::with_repo(group_repo));
    groups
        .upsert(Group::new(
            GROUP_ID,
            group_member,
            vec![Participant::bot(group_member, ParticipantRole::Driver)],
        ))
        .await
        .expect("upsert group");

    let sessions = Arc::new(MemorySessionRepo::new());
    sessions
        .create(
            GROUP_ID,
            NewSessionParams {
                session_kind: SessionKind::Chat,
                id: Some(SESSION_ID.to_string()),
                participants: vec![Participant::bot(
                    "session_member",
                    ParticipantRole::Consultant,
                )],
                ..Default::default()
            },
        )
        .await
        .expect("create session");

    let inner = Arc::new(RecordingGroupContextService::default());
    let guard = GroupContextAddAccessGuard::new(
        inner.clone(),
        groups.clone(),
        sessions.clone() as Arc<dyn SessionRepoPort>,
    );
    (guard, inner)
}

fn request_context(
    group_id: Option<&str>,
    session_id: Option<&str>,
    round_id: Option<&str>,
) -> GroupContextRequestContext {
    GroupContextRequestContext {
        group_id: group_id.map(str::to_string),
        session_id: session_id.map(str::to_string),
        round_id: round_id.map(str::to_string),
    }
}

fn add_command(
    actor_id: &str,
    scope_level: ScopeLevel,
    request_context: GroupContextRequestContext,
) -> AddGroupContext {
    AddGroupContext {
        actor_id: actor_id.to_string(),
        request_context,
        name: "test_context".to_string(),
        scope_level,
        content: "test content".to_string(),
        visible_to: GroupContextPolicy {
            user_ids: Vec::new(),
            extra: serde_json::Map::new(),
        },
        collect_from: Some(GroupContextPolicy::users([actor_id])),
        valid_from: None,
        valid_to: None,
    }
}

fn add_count(inner: &RecordingGroupContextService) -> usize {
    inner.adds.lock().expect("adds lock").len()
}

#[tokio::test]
async fn group_add_requires_group_membership() {
    let (guard, inner) = guard_with_group_member("group_member").await;

    let mut command = add_command(
        "group_member",
        ScopeLevel::Group,
        request_context(Some(GROUP_ID), None, None),
    );
    command.collect_from = None;
    guard
        .add(command)
        .await
        .expect("group member may add without collect_from");
    assert_eq!(add_count(&inner), 1);
    let commands = inner.adds.lock().expect("adds lock");
    assert_eq!(
        commands
            .last()
            .expect("inner add")
            .request_context
            .group_id
            .as_deref(),
        Some(GROUP_ID)
    );
    assert_eq!(
        commands.last().expect("inner add").collect_from,
        None,
        "guard must preserve the omitted add policy"
    );
    drop(commands);

    let error = guard
        .add(add_command(
            "session_member",
            ScopeLevel::Group,
            request_context(Some(GROUP_ID), None, None),
        ))
        .await
        .expect_err("Session-only member cannot add Group context");
    assert_eq!(error.code(), "permission_denied");
    assert_eq!(add_count(&inner), 1);
}

#[tokio::test]
async fn session_add_requires_current_session_membership() {
    let (guard, inner) = guard_with_group_member("group_member").await;

    let error = guard
        .add(add_command(
            "group_member",
            ScopeLevel::Session,
            request_context(None, Some(SESSION_ID), None),
        ))
        .await
        .expect_err("Group-only member cannot add session context");
    assert_eq!(error.code(), "permission_denied");
    assert_eq!(add_count(&inner), 0);

    guard
        .add(add_command(
            "session_member",
            ScopeLevel::Session,
            request_context(Some(GROUP_ID), Some(SESSION_ID), None),
        ))
        .await
        .expect("current session member may add session context");
    let commands = inner.adds.lock().expect("adds lock");
    assert_eq!(
        commands
            .last()
            .expect("inner add")
            .request_context
            .group_id
            .as_deref(),
        Some(GROUP_ID)
    );
    drop(commands);

    let error = guard
        .add(add_command(
            "outsider",
            ScopeLevel::Session,
            request_context(Some(GROUP_ID), Some(SESSION_ID), None),
        ))
        .await
        .expect_err("outsider is denied");
    assert_eq!(error.code(), "permission_denied");
    assert_eq!(add_count(&inner), 1);
}

#[tokio::test]
async fn add_reports_missing_scope_boundary_with_scope_level() {
    let (guard, inner) = guard_with_group_member("group_member").await;

    for (scope_level, request_context, expected_message) in [
        (
            ScopeLevel::Group,
            request_context(None, None, None),
            "param groupId is required when scope_level=group",
        ),
        (
            ScopeLevel::Session,
            request_context(None, None, None),
            "param sessionId is required when scope_level=session",
        ),
        (
            ScopeLevel::Round,
            request_context(None, None, None),
            "param roundId is required when scope_level=round",
        ),
    ] {
        let error = guard
            .add(add_command("group_member", scope_level, request_context))
            .await
            .expect_err("selected scope boundary is required");
        assert_eq!(error.code(), "invalid_param");
        assert!(
            error.to_string().contains(expected_message),
            "error: {error}"
        );
    }
    assert_eq!(add_count(&inner), 0);
}

#[tokio::test]
async fn session_add_rejects_mismatched_group_id() {
    let (guard, _inner) = guard_with_group_member("group_member").await;

    let error = guard
        .add(add_command(
            "session_member",
            ScopeLevel::Session,
            request_context(Some("wrong_group"), Some(SESSION_ID), None),
        ))
        .await
        .expect_err("groupId must match session parent");
    assert_eq!(error.code(), "invalid_param");
}

#[tokio::test]
async fn round_add_resolves_session_and_uses_session_membership_rule() {
    let (guard, inner) = guard_with_group_member("group_member").await;
    let round_id = format!("{SESSION_ID}:round:001");

    guard
        .add(add_command(
            "session_member",
            ScopeLevel::Round,
            request_context(None, None, Some(&round_id)),
        ))
        .await
        .expect("session member may add round context");
    let commands = inner.adds.lock().expect("adds lock");
    let command = commands.last().expect("inner add");
    assert_eq!(command.request_context.group_id.as_deref(), Some(GROUP_ID));
    assert_eq!(
        command.request_context.session_id.as_deref(),
        Some(SESSION_ID)
    );
    assert_eq!(
        command.request_context.round_id.as_deref(),
        Some(round_id.as_str())
    );

    for (actor_id, description) in [
        ("group_member", "Group-only member"),
        ("outsider", "outsider"),
    ] {
        let error = guard
            .add(add_command(
                actor_id,
                ScopeLevel::Round,
                request_context(Some(GROUP_ID), Some(SESSION_ID), Some(&round_id)),
            ))
            .await
            .expect_err(description);
        assert_eq!(error.code(), "permission_denied");
    }
    drop(commands);
    assert_eq!(add_count(&inner), 1);

    for invalid_round_id in [
        "round-001",
        SESSION_ID,
        &format!("{SESSION_ID}:"),
        "group :suffix:round",
        "group:suffix\n:round",
    ] {
        let error = guard
            .add(add_command(
                "group_member",
                ScopeLevel::Round,
                request_context(Some(GROUP_ID), None, Some(invalid_round_id)),
            ))
            .await
            .expect_err("invalid roundId is rejected");
        assert_eq!(error.code(), "invalid_param");
    }

    let error = guard
        .add(add_command(
            "group_member",
            ScopeLevel::Round,
            request_context(
                Some(GROUP_ID),
                Some(SESSION_ID),
                Some(&format!("other_session:00000001:round")),
            ),
        ))
        .await
        .expect_err("explicit sessionId and roundId must refer to one session");
    assert_eq!(error.code(), "invalid_param");
}
