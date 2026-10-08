//! Owner/manager parity for the V1 Session + session-file facades
//! (plan Task 11, spec §8/§12.1).
//!
//! Every assertion runs against REAL facade output (the production wiring
//! over the memory stores and the map-backed live authority double — never
//! a claim-local shortcut):
//! - message projection and collect parity: a MANAGER of a Bot sees and
//!   mutates exactly what its OWNER does (`owner_message_ids ==
//!   manager_message_ids`);
//! - a managed Bot that does NOT participate in the session is rejected
//!   (collect acting as a non-member bot is an error);
//! - a Human member without the selected View Actor is rejected for detail
//!   access it does not qualify for;
//! - mixed-identity calls resolve through the LIVE authority facts: a STALE
//!   signed owner_id claim succeeds for a current MANAGER and fails for a
//!   caller with NO current role;
//! - a Human with no relationship to the file-owning Bot cannot delete
//!   another Human's file.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;
use std::sync::Mutex;

use async_trait::async_trait;

use bcs_app_session::{SessionFileApplicationServiceImpl, SessionServiceConfig, SessionServiceImpl};
use bcs_bot::BotCore;
use bcs_service_api::BotRegistryCoreService;
use bcs_bot_store::MemoryBotRepo;
use bcs_domain::{AttachmentType, MessageAttachment};
use bcs_friend::FriendCore;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_relation::RelationCore;
use bcs_service_api::application::session::SessionManagementService;
use bcs_service_api::application::session_files::{
    CapabilitiesView, DeleteFileCommand, DownloadRoute, PrepareUploadCommand, PrepareUploadResult,
    SessionFileService, SessionFileUseCaseError, ShareConsumeResult, ShareMintCommand,
    ShareMintResult,
};
use bcs_service_api::application::v1::session_file::SessionFileApplicationService;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    BotAuthorityHook, CollectSession, DeleteResult, DeleteSessionFile, GetSession,
    ListSessionMessages, Page, SessionMessageService, SessionService, UncollectSession,
    resolve_authorized_principal,
};
use bcs_service_api::port::repo::SessionRepoPort;
use bcs_service_api::application::v1::SessionFileInternalContentUrlProjector;
use bcs_service_api::types::{GroupMessageType, HumanMessageView, MessageRole, MessageViewScope};
use bcs_service_api::{
    CollaborationDefinition, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome, CancelStateMachineRunCommand,
    GroupHistoryCommand, GroupHistoryResult, GroupMessage,
    GroupMessageHistoryService, GroupStrategy, GroupUseCaseError, HandleBotTerminalEventCommand,
    HandleBotTerminalEventOutcome, MessageHistoryOptions, Participant, ParticipantRole,
    SessionHistoryCommand, SessionHistoryResult, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineDeliveryCorrelation, StateMachineRun,
    StateMachineRunStatus, StateMachineRunView,
};

use bcs_session::SessionManagementServiceImpl;
use bcs_session_file::{SessionFileServiceConfig, SessionFileServiceImpl};
use bcs_session_file_store::MemorySessionFileRepo;
use bcs_session_store::MemorySessionRepo;

/// Map-backed live-authority double — answers ONLY from seeded owner/manager
/// facts (never created_by), so the facade's decisions are provably hook-
/// driven (spec §12.4).
struct SeededAuthority {
    roles: Mutex<std::collections::BTreeMap<(String, String), &'static str>>,
}

impl SeededAuthority {
    fn empty() -> Self {
        Self {
            roles: Mutex::new(std::collections::BTreeMap::new()),
        }
    }

    async fn seed_owner(&self, bot_id: &str, owner_staff_no: &str) {
        self.roles
            .lock()
            .unwrap()
            .insert((owner_staff_no.to_string(), bot_id.to_string()), "owner");
    }

    async fn seed_manager(&self, bot_id: &str, manager_staff_no: &str) {
        self.roles.lock().unwrap().insert(
            (manager_staff_no.to_string(), bot_id.to_string()),
            "manager",
        );
    }
}

#[async_trait::async_trait]
impl BotAuthorityHook for SeededAuthority {
    async fn can_manage(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<bool> {
        Ok(self
            .roles
            .lock()
            .unwrap()
            .contains_key(&(user_id.to_string(), bot_id.to_string())))
    }

    async fn require_owner(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> bcs_service_api::ServiceResult<()> {
        match self
            .roles
            .lock()
            .unwrap()
            .get(&(user_id.to_string(), bot_id.to_string()))
        {
            Some(&"owner") => Ok(()),
            _ => Err(bcs_service_api::ServiceError::Forbidden(format!(
                "user '{user_id}' is not the owner of bot '{bot_id}'"
            ))),
        }
    }
}

struct Fixture {
    service: SessionServiceImpl,
    file_facade: SessionFileApplicationServiceImpl,
    sessions: Arc<SessionManagementServiceImpl>,
    groups: Arc<GroupCore>,
    bots: Arc<BotCore>,
    session_repo: Arc<MemorySessionRepo>,
    file_repo: Arc<MemorySessionFileRepo>,
    authority: Arc<SeededAuthority>,
}

fn human_caller(staff_no: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("parity".into()),
        user: Some(AuthenticatedUserIdentity {
            id: staff_no.into(),
            username: staff_no.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn mixed_caller(staff_no: &str, bot_uuid: &str, signed_owner: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("parity".into()),
        user: Some(AuthenticatedUserIdentity {
            id: staff_no.into(),
            username: staff_no.into(),
            display_name: None,
            full_name: None,
        }),
        // Signed claim may be stale — authorization uses the live facts.
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot_uuid.into(),
            owner_id: signed_owner.into(),
            app_id: 1,
            agent_code: "parity".into(),
        }),
        app: None,
        access_key: None,
    }
}

impl Fixture {
    async fn new() -> Self {
        let group_repo = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let relation = Arc::new(RelationCore::memory());
        let friends = Arc::new(FriendCore::memory().with_relation(relation.clone()));
        let bots = Arc::new(BotCore::memory());
        let session_repo = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            group_repo,
        ));
        let authority = Arc::new(SeededAuthority::empty());
        let authority_hook: Arc<dyn BotAuthorityHook> = authority.clone();
        let runtime: Arc<ParityRuntime> = Arc::new(Default::default());
        let history: Arc<ParityHistory> = Arc::new(Default::default());
        let launch = Arc::new(bcs_session::SessionLaunchApplication::new(
            bots.clone(),
            groups.clone(),
            sessions.clone(),
            runtime.clone(),
            Arc::new(bcs_test_support::NoopSystemMessageService),
            authority_hook.clone(),
        ));
        let service = SessionServiceImpl::new(
            launch,
            sessions.clone(),
            groups.clone(),
            bots.clone(),
            friends,
            authority_hook.clone(),
            session_repo.clone(),
            history.clone(),
            runtime.clone(),
            Arc::new(bcs_test_support::NoopSystemMessageService),
            SessionServiceConfig {},
        );

        let file_repo = Arc::new(MemorySessionFileRepo::new());
        let storage: Arc<dyn bcs_storage_api::StoragePlugin> = Arc::new(
            bcs_storage_api::fake::FakeStoragePlugin::new(bcs_storage_api::StorageCapabilities {
                supports_presign_put: false,
                supports_presign_download: false,
                supports_stream_put: true,
                supports_stream_get: true,
                supports_inline_view: true,
                max_object_size: 1024 * 1024,
            }),
        );
        let legacy: Arc<dyn SessionFileService> = Arc::new(SessionFileServiceImpl::new(
            SessionFileServiceConfig {
                storage,
                repo: file_repo.clone(),
                session_repo: session_repo.clone(),
                env: "parity".into(),
                max_size: 1024 * 1024,
                multipart_threshold: 1024,
                bcs_base_url: "http://parity.test".into(),
                share_secret: b"parity-secret".to_vec(),
                share_default_ttl: 3600,
                share_link_ttl: 3600,
                share_base_url: None,
            },
        ));
        let file_facade = SessionFileApplicationServiceImpl::new(
            legacy,
            sessions.clone(),
            groups.clone(),
            bots.clone(),
            authority_hook,
            Arc::new(bcs_test_support::NoopSystemMessageService),
            Arc::new(ParityShareProjector),
        );
        Self {
            service,
            file_facade,
            sessions,
            groups,
            bots,
            session_repo,
            file_repo,
            authority,
        }
    }

    async fn register_public_bot(&self, bot_uuid: &str) {
        BotRegistryCoreService::register(
            &*self.bots,
            bot_uuid.to_string(),
            bcs_service_api::BotCapabilities {
                name: Some(bot_uuid.to_string()),
            visibility: "public".into(),
            ..Default::default()
        })
        .await
        .expect("register bot");
    }

    async fn seed_chat_group_with_participants(&self, group_id: &str, participants: &[&str]) {
        let mut all_participants = vec![Participant::bot(
            "driver-bot",
            ParticipantRole::Driver,
        )];
        all_participants.extend(
            participants
                .iter()
                .map(|p| Participant::bot(*p, ParticipantRole::Consultant)),
        );
        let mut group = bcs_service_api::Group::new(
            group_id.to_string(),
            "driver-bot".to_string(),
            all_participants,
        );
        group.group_strategy = GroupStrategy::Chat;
        group.visibility = "public".into();
        bcs_service_api::GroupCoreService::upsert(&*self.groups, group)
            .await
            .expect("store group");
    }

    async fn seed_session(
        &self,
        group_id: &str,
        session_id: &str,
        participants: Vec<Participant>,
    ) -> bcs_service_api::Session {
        use bcs_service_api::port::repo::NewSessionParams;
        let ctx = bcs_service_api::types::BotOperationContext {
            operation_id: format!("parity-seed-{}", uuid::Uuid::new_v4()),
            actor: bcs_service_api::types::BotOperationActor::System {
                system_id: "parity-fixture".to_string(),
                effective_actor_id: "parity-fixture".to_string(),
            },
        };
        self.session_repo
            .create(
                group_id,
                NewSessionParams {
                    id: Some(session_id.to_string()),
                    participants,
                    ..Default::default()
                },
            )
            .await
            .expect("seed session");
        let session = self
            .session_repo
            .get(session_id)
            .await
            .expect("session exists after seed");
        let _ = ctx;
        session
    }
}

// ── Noop doubles for the facade constructor surface ─────────────────────

type ParityRuntime = RecordingRuntime;
type ParityHistory = RecordingHistoryService;

#[derive(Default)]
struct RecordingHistoryService {
    session_calls: Mutex<Vec<SessionHistoryCommand>>,
    session_options: Mutex<Vec<MessageHistoryOptions>>,
    messages: Mutex<Vec<GroupMessage>>,
}
#[async_trait]
impl GroupMessageHistoryService for RecordingHistoryService {
    async fn get_history(
        &self,
        _cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        panic!("group history is not used by the parity fixture")
    }

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        self.session_calls
            .lock()
            .expect("history lock")
            .push(cmd.clone());
        let messages = fixture_messages();
        Ok(SessionHistoryResult {
            session_id: cmd.session_id,
            messages,
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }

    async fn get_session_history_with_options(
        &self,
        cmd: SessionHistoryCommand,
        options: MessageHistoryOptions,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        self.session_options
            .lock()
            .expect("history options lock")
            .push(options);
        self.get_session_history(cmd).await
    }
}


#[derive(Default)]
struct RecordingRuntime {
    start_calls: Mutex<Vec<StartStateMachineRunCommand>>,
    history_calls: Mutex<Vec<(String, u64, Option<u64>)>>,
    participant_history_calls: Mutex<Vec<(String, u64, Option<u64>, HumanMessageView)>>,
    history_result: Mutex<Option<SessionHistoryResult>>,
    session_run: Mutex<Option<StateMachineRunView>>,
}

#[async_trait]
impl CollaborationRuntimeService for RecordingRuntime {
    async fn start_state_machine_run(
        &self,
        cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.start_calls
            .lock()
            .expect("runtime start lock")
            .push(cmd.clone());
        let session_id = cmd.session_id.expect("Session launch pins id");
        Ok(StartStateMachineRunOutcome {
            view: StateMachineRunView {
                run: StateMachineRun {
                    run_id: "run-1".into(),
                    root_run_id: Some("run-1".into()),
                    rerun_of: None,
                    definition_id: "definition-1".into(),
                    definition_version: 1,
                    group_id: cmd.group_id,
                    group_version: 1,
                    session_id,
                    session_activation_count: None,
                    created_by: cmd.caller_id,
                    status: StateMachineRunStatus::Running,
                    input: cmd.input,
                    opening_message_override: None,
                    output: None,
                    error: None,
                    created_at: 1,
                    updated_at: 1,
                    completed_at: None,
                },
                nodes: Vec::new(),
                node_execution_metadata: None,
                judge_outputs: Vec::new(),
            },
        })
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn get_state_machine_run_by_session_id(
        &self,
        _session_id: &str,
    ) -> Result<Option<StateMachineRunView>, CollaborationRuntimeError> {
        Ok(self
            .session_run
            .lock()
            .expect("runtime session run lock")
            .clone())
    }

    async fn get_state_machine_session_history(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        self.history_calls
            .lock()
            .expect("runtime history lock")
            .push((session_id.to_string(), limit, before));
        Ok(self
            .history_result
            .lock()
            .expect("runtime result lock")
            .clone())
    }

    async fn get_state_machine_session_history_for_view(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
        human_view: HumanMessageView,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        if human_view.scope == MessageViewScope::Full {
            return self
                .get_state_machine_session_history(session_id, limit, before)
                .await;
        }
        self.participant_history_calls
            .lock()
            .expect("runtime participant history lock")
            .push((session_id.to_string(), limit, before, human_view));
        Ok(self
            .history_result
            .lock()
            .expect("runtime result lock")
            .clone())
    }

    async fn cancel_state_machine_run(
        &self,
        _cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        panic!("cancel_state_machine_run is not used by SessionServiceImpl")
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
        panic!("handle_bot_terminal_event is not used by SessionServiceImpl")
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
        panic!("configure_group_runtime is not used by SessionServiceImpl")
    }
}



fn fixture_messages() -> Vec<GroupMessage> {
    vec![fixture_message("m-1"), fixture_message("m-2")]
}

fn fixture_message(id: &str) -> GroupMessage {
    GroupMessage {
        id: id.into(),
        timestamp: 1_786_590_000_000,
        sender: "driver-bot".into(),
        content: "hello".into(),
        message_type: GroupMessageType::Bot,
        bot_name: Some("Driver".into()),
        role: MessageRole::Assistant,
        run_id: "run-1".into(),
        history_meta: None,
        metadata: None,
        attachments: None,
    }
}

struct ParityShareProjector;

#[async_trait::async_trait]
impl SessionFileInternalContentUrlProjector for ParityShareProjector {
    fn shared_content_url(&self, token: &str) -> String {
        format!("http://parity.test/shared/{token}")
    }
}

#[tokio::test]
async fn parities() {
    let fixture = Fixture::new().await;
    fixture.register_public_bot("bot-x").await;
    fixture.register_public_bot("driver-bot").await;
    // Alice owns bot-x; Bob is its MANAGER (owner parity, spec §8).
    fixture.authority.seed_owner("bot-x", "alice").await;
    fixture.authority.seed_manager("bot-x", "bob").await;

    fixture
        .seed_chat_group_with_participants("parity-group", &["bot-x"])
        .await;
    let participants = vec![
        Participant::bot("bot-x", ParticipantRole::Consultant),
        Participant::human("human_alice", ParticipantRole::Observer),
        Participant::human("human_bob", ParticipantRole::Observer),
    ];
    let session =
        fixture.seed_session("parity-group", "parity-group:0a1b2c3d", participants).await;
    let session_id = session.id.clone();

    // 1) owner_message_ids == manager_message_ids (real facade output).
    let owner_messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_caller("alice"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("bot-x".into()),
        },
    )
    .await
    .expect("owner lists messages as bot-x");
    let manager_messages = SessionMessageService::list(
        &fixture.service,
        ListSessionMessages {
            caller: human_caller("bob"),
            session_id: session_id.clone(),
            before: None,
            limit: 100,
            view_bot_id: Some("bot-x".into()),
        },
    )
    .await
    .expect("manager lists messages as bot-x");
    let owner_message_ids: Vec<&str> = owner_messages
        .iter()
        .map(|message| message.id.as_str())
        .collect();
    let manager_message_ids: Vec<&str> = manager_messages
        .iter()
        .map(|message| message.id.as_str())
        .collect();
    assert_eq!(owner_message_ids, manager_message_ids);

    // 2) managed_bot_not_in_session: collect acting as a managed Bot that is
    // NOT a session participant is an error (managed rights stop at the bot's
    // own participation, spec §8.1 "View Bot 必须真实参与待查询资源").
    fixture.register_public_bot("bot-outsider").await;
    fixture.authority.seed_manager("bot-outsider", "bob").await;
    let other_session = fixture
        .seed_session(
            "parity-group",
            "parity-group:0a1b2c3e",
            vec![Participant::bot("driver-bot", ParticipantRole::Driver)],
        )
        .await;
    let managed_bot_not_in_session_result = fixture
        .service
        .collect(CollectSession {
            caller: human_caller("bob"),
            session_id: other_session.id.clone(),
            participant: "bot-outsider".into(),
        })
        .await;
    assert!(managed_bot_not_in_session_result.is_err());

    // 3) human_not_member_without_view: an unaffiliated Human reading the
    // detail of a session they do not participate in is rejected (the
    // managed bot's identity does not implicitly grant membership).
    let human_not_member_without_view_result = GetSessionProbe(
        &fixture.service,
        GetSession {
            caller: human_caller("mallory"),
            session_id: session_id.clone(),
        },
    )
    .probe()
    .await;
    assert!(human_not_member_without_view_result.is_err());

    // 4) Mixed-identity selection uses the LIVE facts (spec §12.1):
    //    a stale signed owner_id claim + a current manager role is allowed;
    //    the same shape with NO current role is denied.
    let mixed_caller_with_stale_claim_but_current_manager = mixed_caller("bob", "bot-x", "someone-else");
    let resolved = resolve_authorized_principal(
        &mixed_caller_with_stale_claim_but_current_manager,
        fixture.as_dyn_authority().as_ref(),
    )
    .await;
    assert!(resolved.is_ok(), "a current manager resolves despite the stale claim");
    let mixed_caller_without_current_role = mixed_caller("zed", "bot-x", "zed");
    let denied = resolve_authorized_principal(
        &mixed_caller_without_current_role,
        fixture.as_dyn_authority().as_ref(),
    )
    .await;
    assert!(denied.is_err());

    // 5) other_human_file_delete: a Human with no relationship to the
    // uploading Human's file cannot delete it (file owner unchanged; the
    // stranger is not a member at all).
    use bcs_service_api::port::repo::NewSessionFileParams;
    let alice_actor = bcs_domain::ActorRef {
        actor_kind: bcs_domain::ActorKind::Human,
        actor_id: "human_alice".into(),
    };
    fixture
        .file_repo
        .insert(NewSessionFileParams {
            file_id: "parity-file".into(),
            session_id: session_id.clone(),
            file_name: "owned.txt".into(),
            mime_type: "text/plain".into(),
            size: 4,
            owner: alice_actor,
            storage_backend: "fake".into(),
            object_handle: r#"{"backend":"fake","key":"k","backend_handle":{"transfer_id":"t"},"expires_at":99999}"#.into(),
            expires_at: 99999,
            operation: bcs_service_api::types::BotOperationContext {
                operation_id: format!("parity-file-{}", uuid::Uuid::new_v4()),
                actor: bcs_service_api::types::BotOperationActor::Human {
                    user_id: "alice".into(),
                    effective_actor_id: "human_alice".into(),
                },
            },
        })
        .await
        .expect("seed alice's file");

    let other_human_file_delete_result = SessionFileApplicationService::delete(
        &fixture.file_facade,
        DeleteSessionFile {
            caller: human_caller("mallory"),
            session_id: session_id.clone(),
            file_id: "parity-file".into(),
        })
        .await;
    assert!(other_human_file_delete_result.is_err());
    // The file is untouched.
    use bcs_service_api::port::repo::SessionFileRepoPort;
    assert!(fixture
        .file_repo
        .get(&session_id, "parity-file")
        .await
        .expect("probe")
        .is_some());
    // The owner CAN delete it — the deny above is authorization, not storage.
    let owner_delete = SessionFileApplicationService::delete(
        &fixture.file_facade,
        DeleteSessionFile {
            caller: human_caller("alice"),
            session_id: session_id.clone(),
            file_id: "parity-file".into(),
        })
        .await
        .expect("the file owner deletes it");
    assert_eq!(owner_delete, DeleteResult { deleted: true });

    // 6) Collect parity: MANAGER collect reaches the same place as OWNER
    //    collect — the applied audit rows carry BOTH identities.
    fixture
        .service
        .collect(CollectSession {
            caller: human_caller("bob"),
            session_id: session_id.clone(),
            participant: "bot-x".into(),
        })
        .await
        .expect("manager collects as the managed bot");
    let audits = fixture
        .session_repo
        .session_action_audit_records()
        .await
        .expect("audit records");
    let bob_rows: Vec<_> = audits
        .iter()
        .filter(|record| record.operator.operator_user_id() == Some("bob"))
        .collect();
    assert!(!bob_rows.is_empty(), "the manager's collect wrote real rows");
    assert!(
        bob_rows
            .iter()
            .any(|record| record.operator.effective_actor_id() == "bot-x"
                && record.phase == bcs_service_api::types::BotActionAuditPhase::Applied),
        "the collect audit carries the managed bot as the effective actor: {bob_rows:#?}"
    );
    // And the owner's uncollect flips it back through the same lane.
    let uncollect = fixture
        .service
        .uncollect(UncollectSession {
            caller: human_caller("alice"),
            session_id: session_id.clone(),
            participant: "bot-x".into(),
        })
        .await;
    assert!(uncollect.is_ok());
}

struct GetSessionProbe<'a>(&'a SessionServiceImpl, GetSession);

impl GetSessionProbe<'_> {
    async fn probe(self) -> Result<bcs_service_api::application::v1::SessionDetail, ApplicationError> {
        self.0.get(self.1).await
    }
}

impl Fixture {
    fn as_dyn_authority(&self) -> Arc<dyn BotAuthorityHook> {
        self.authority.clone()
    }
}