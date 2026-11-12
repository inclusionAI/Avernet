use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_app_session::GroupContextAddAccessGuard;
use bcs_domain::{GroupContextPolicy, ScopeLevel};
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::application::v1::{
    AddGroupContext, ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity,
    GetGroupContext, GroupContextAck,
    GroupContextApplicationService, GroupContextGetResponse, GroupContextListResponse,
    GroupContextRequestContext, ListGroupContexts, ListSharedGroupContexts, SharedGroupContextItem,
    SystemSetGroupContext, UpdateGroupContext,
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
    preset_actors: Mutex<Vec<String>>,
    shared_queries: Mutex<Vec<ListSharedGroupContexts>>,
}

#[async_trait]
impl GroupContextApplicationService for RecordingGroupContextService {
    async fn add(&self, command: AddGroupContext) -> Result<GroupContextAck, ApplicationError> {
        self.adds.lock().expect("adds lock").push(command);
        Ok(GroupContextAck::ok())
    }

    async fn list_shared_contexts(
        &self,
        query: ListSharedGroupContexts,
    ) -> Result<Vec<SharedGroupContextItem>, ApplicationError> {
        self.shared_queries.lock().unwrap().push(query);
        Ok(Vec::new())
    }

    async fn list_presets(
        &self,
        _: &str,
        _: &AuthenticatedCaller,
        _: Option<&str>,
    ) -> Result<Vec<bcs_service_api::application::v1::GroupContextItemPreset>, ApplicationError> {
        Ok(Vec::new())
    }

    async fn add_preset(&self, _: &str, actor: &str, _: String, _: String) -> Result<(), ApplicationError> {
        self.preset_actors.lock().unwrap().push(actor.to_owned());
        Ok(())
    }

    async fn update_preset(&self, _: &str, actor: &str, _: String, _: Option<String>, _: Option<String>) -> Result<(), ApplicationError> {
        self.preset_actors.lock().unwrap().push(actor.to_owned());
        Ok(())
    }

    async fn delete_preset(&self, _: &str, actor: &str, _: String) -> Result<(), ApplicationError> {
        self.preset_actors.lock().unwrap().push(actor.to_owned());
        Ok(())
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
        Arc::new(bcs_test_support::NoopBotAuthorityPort),
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
        preset: false,
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

#[tokio::test]
async fn preset_writes_require_live_authority_on_the_group_driver() {
    use bcs_test_support::{authority_edge_fixture, grant_manager_edge, grant_owner_edge};

    let authority = authority_edge_fixture();
    grant_owner_edge(&authority.repo, "driver", "owner").await;
    grant_manager_edge(&authority.repo, "driver", "admin").await;
    grant_owner_edge(&authority.repo, "originator", "other").await;
    let groups = Arc::new(GroupCore::with_repo(Arc::new(MemoryGroupRepo::new())));
    let mut group = Group::new(
        GROUP_ID, "driver", vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.originator = Some("originator".into());
    groups.upsert(group).await.unwrap();
    let inner = Arc::new(RecordingGroupContextService::default());
    let guard = GroupContextAddAccessGuard::new(
        inner.clone(), groups, Arc::new(MemorySessionRepo::new()), authority.hook,
    );
    for actor in ["human_other", "driver", "human_owner", "human_admin"] {
        let results = [
            guard.add_preset(GROUP_ID, actor, "timeline".into(), String::new()).await,
            guard.update_preset(GROUP_ID, actor, "timeline".into(), None, Some("events".into())).await,
            guard.delete_preset(GROUP_ID, actor, "timeline".into()).await,
        ];
        for result in results {
            if matches!(actor, "human_owner" | "human_admin") {
                result.unwrap();
            } else {
                assert_eq!(result.unwrap_err().code(), "permission_denied");
            }
        }
    }
    assert_eq!(*inner.preset_actors.lock().unwrap(),
        ["human_owner", "human_owner", "human_owner", "human_admin", "human_admin", "human_admin"]);
}

#[tokio::test]
async fn preset_discovery_requires_group_membership_or_an_explicit_session() {
    use bcs_service_api::application::v1::AuthenticatedBotIdentity;

    let (guard, _) = guard_with_group_member("group_member").await;
    let bot = |id: &str| AuthenticatedCaller {
        tenant: None,
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: id.into(),
            owner_id: String::new(),
            app_id: 0,
            agent_code: String::new(),
        }),
        app: None,
        access_key: None,
    };

    guard.list_presets(GROUP_ID, &bot("group_member"), None).await.unwrap();
    guard.list_presets(GROUP_ID, &bot("session_member"), Some(SESSION_ID)).await.unwrap();

    let missing_session = guard.list_presets(GROUP_ID, &bot("session_member"), None).await.unwrap_err();
    assert_eq!(missing_session.code(), "permission_denied");
    assert!(missing_session.to_string().contains("--session-id"));

    let outsider = guard.list_presets(GROUP_ID, &bot("outsider"), Some(SESSION_ID)).await.unwrap_err();
    assert_eq!(outsider.code(), "permission_denied");
}

#[tokio::test]
async fn shared_info_checks_membership_and_limits_the_read_principals() {
    use bcs_test_support::{authority_edge_fixture, grant_manager_edge, grant_owner_edge};

    let authority = authority_edge_fixture();
    grant_owner_edge(&authority.repo, "driver", "owner").await;
    grant_owner_edge(&authority.repo, "guest", "other-owner").await;
    grant_manager_edge(&authority.repo, "guest", "owner").await;
    let groups = Arc::new(GroupCore::with_repo(Arc::new(MemoryGroupRepo::new())));
    groups.upsert(Group::new(
        GROUP_ID, "driver", vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::human("human_reader", ParticipantRole::Consultant),
        ],
    )).await.unwrap();
    let mut public_group = Group::new("public-group", "driver", Vec::new());
    public_group.visibility = "public".into();
    groups.upsert(public_group).await.unwrap();
    let sessions = Arc::new(MemorySessionRepo::new());
    sessions.create(GROUP_ID, NewSessionParams {
        id: Some(SESSION_ID.into()),
        session_kind: SessionKind::Chat,
        participants: vec![
            Participant::bot("guest", ParticipantRole::Consultant),
            Participant::human("human_reader", ParticipantRole::Consultant),
        ],
        ..Default::default()
    }).await.unwrap();
    let inner = Arc::new(RecordingGroupContextService::default());
    let guard = GroupContextAddAccessGuard::new(inner.clone(), groups, sessions, authority.hook);
    let caller = |id: &str| AuthenticatedCaller {
        tenant: None,
        user: Some(AuthenticatedUserIdentity {
            id: id.into(),
            username: id.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    };
    let owner = caller("owner");
    let reader = caller("reader");

    for (session, view_bot, human) in [
        (Some(SESSION_ID), None, &owner),
        (Some(SESSION_ID), Some("guest"), &owner),
        (Some(SESSION_ID), None, &reader),
        (None, None, &owner),
        (None, None, &reader),
    ] {
        guard.shared_info(GROUP_ID, session, view_bot, human).await.unwrap();
    }
    let outsider = caller("outsider");
    guard.shared_info("public-group", None, None, &outsider).await.unwrap();
    let anonymous = AuthenticatedCaller { user: None, ..outsider.clone() };
    assert!(matches!(
        guard.shared_info(GROUP_ID, None, None, &anonymous).await,
        Err(ApplicationError::Unauthenticated)
    ));
    assert_eq!(
        guard.shared_info(GROUP_ID, Some(SESSION_ID), Some("guest"), &anonymous)
            .await.unwrap_err().code(),
        "permission_denied"
    );

    for (group, session, view_bot, human, code) in [
        (GROUP_ID, Some(SESSION_ID), Some("guest"), &reader, "permission_denied"),
        (GROUP_ID, Some(SESSION_ID), Some("driver"), &owner, "permission_denied"),
        (GROUP_ID, Some(SESSION_ID), None, &outsider, "permission_denied"),
        (GROUP_ID, None, None, &outsider, "permission_denied"),
        (GROUP_ID, Some("missing"), None, &owner, "session_not_found"),
        ("missing", None, None, &owner, "group_not_found"),
    ] {
        assert_eq!(guard.shared_info(group, session, view_bot, human).await.unwrap_err().code(), code);
    }
    assert!(guard.can_manage_presets(GROUP_ID, "human_owner").await.unwrap());
    assert!(!guard.can_manage_presets(GROUP_ID, "human_reader").await.unwrap());
    assert_eq!(guard.can_manage_presets("missing", "human_owner").await.unwrap_err().code(), "group_not_found");

    let queries = inner.shared_queries.lock().unwrap();
    assert_eq!(queries.len(), 6);
    let mut owner_ids = queries[0].actor_ids.clone();
    owner_ids.sort();
    assert_eq!(owner_ids, ["driver", "guest", "human_owner"]);
    assert_eq!(queries[1].actor_ids, ["guest"]);
    assert_eq!(queries[2].actor_ids, ["human_reader"]);
    assert_eq!(queries[3].actor_ids, ["human_owner"]);
    assert_eq!(queries[3].group_id, GROUP_ID);
    assert_eq!(queries[3].session_id, None);
    assert_eq!(queries[4].actor_ids, ["human_reader"]);
    assert_eq!(queries[5].group_id, "public-group");
    assert_eq!(queries[5].actor_ids, ["human_outsider"]);
}

#[tokio::test]
async fn shared_info_defaults_fail_closed_without_an_implementation() {
    let service = bcs_test_support::NoopGroupContextService;
    let caller = AuthenticatedCaller {
        tenant: None,
        user: None,
        bot: None,
        app: None,
        access_key: None,
    };
    assert!(matches!(
        service.shared_info(GROUP_ID, None, None, &caller).await,
        Err(ApplicationError::Internal(_))
    ));
    assert!(matches!(
        service.list_shared_contexts(ListSharedGroupContexts {
            group_id: GROUP_ID.into(),
            session_id: None,
            actor_ids: Vec::new(),
        }).await,
        Err(ApplicationError::Internal(_))
    ));
    assert!(!service.can_manage_presets(GROUP_ID, "human_reader").await.unwrap());
}
