//! bcs-session cutover tests (row 17 of the mounted-entry
//! table; bot-authority plan Task 6.2/6.3, spec §3.3/§8.1).
//!
//! The Human→Bot control legs of the launch flow
//! (`caller_has_group_access`, `resolve_creator`,
//! `creator_has_group_access`) run through the REAL
//! authority chain (the lazy-seed hook over the
//! authority Core); the §8.1 sponsorship semantics (a
//! private Group admits a controlled non-hidden Bot
//! without friendship; public Groups stay public-only;
//! the manager never becomes an automatic Human
//! participant) and the launch-flow participant
//! seeding are asserted on the persisted Session.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_bot::BotCore;
use bcs_service_api::types::{HumanMessageView, MessageViewScope};
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_service_api::port::repo::{GroupRepoPort, SessionRepoPort};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotRegistryCoreService, CancelStateMachineRunCommand,
    CollaborationDefinition, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome, CreateSessionLaunch, DeliveryType,
    Group, GroupCoreService, GroupStrategy, HandleBotTerminalEventCommand,
    HandleBotTerminalEventOutcome, Participant, ParticipantMode, ParticipantRole,
    RequestedSessionRole, ServiceResult, SessionCaller,
    SessionHistoryResult, SessionKind, SessionLaunchError, SessionLaunchRequest,
    SessionLaunchService, SessionManagementService, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineDeliveryCorrelation, StateMachineRun,
    StateMachineRunStatus, StateMachineRunView, SystemMessageEvent, SystemMessageService,
    SystemMessageDispatchOutcome, SystemMessageRecipientResult,
};
use bcs_session::{SessionLaunchApplication, SessionManagementServiceImpl};
use bcs_session_store::MemorySessionRepo;

use bcs_test_support::LazySeedAuthorityHook;

#[derive(Default)]
struct RecordingRuntime {
    commands: Mutex<Vec<StartStateMachineRunCommand>>,
}

#[async_trait]
impl CollaborationRuntimeService for RecordingRuntime {
    async fn start_state_machine_run(
        &self,
        command: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        self.commands
            .lock()
            .expect("runtime commands lock")
            .push(command.clone());
        let session_id = command.session_id.clone().expect("Session launch pins id");
        Ok(StartStateMachineRunOutcome {
            view: StateMachineRunView {
                run: StateMachineRun {
                    run_id: format!("run-{session_id}"),
                    root_run_id: Some(format!("run-{session_id}")),
                    rerun_of: None,
                    definition_id: "definition-1".into(),
                    definition_version: 1,
                    group_id: command.group_id,
                    group_version: 1,
                    session_id,
                    session_activation_count: None,
                    created_by: command.caller_id,
                    status: StateMachineRunStatus::Running,
                    input: command.input,
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
        Ok(None)
    }

    async fn get_state_machine_session_history(
        &self,
        _session_id: &str,
        _limit: u64,
        _before: Option<u64>,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn get_state_machine_session_history_for_view(
        &self,
        session_id: &str,
        limit: u64,
        before: Option<u64>,
        _human_view: HumanMessageView,
    ) -> Result<Option<SessionHistoryResult>, CollaborationRuntimeError> {
        self.get_state_machine_session_history(session_id, limit, before)
            .await
    }

    async fn cancel_state_machine_run(
        &self,
        _cmd: CancelStateMachineRunCommand,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest(
            "unused".to_string(),
        ))
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
        Err(CollaborationRuntimeError::InvalidRequest(
            "unused".to_string(),
        ))
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
        Err(CollaborationRuntimeError::InvalidRequest(
            "unused".to_string(),
        ))
    }
}

#[derive(Default)]
struct RecordingSystemMessage {
    events: Mutex<Vec<(String, SystemMessageEvent, String, Vec<Participant>)>>,
}

#[async_trait]
impl SystemMessageService for RecordingSystemMessage {
    async fn notify(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        participants: &[Participant],
    ) -> ServiceResult<usize> {
        self.events.lock().expect("events lock").push((
            group_id.to_string(),
            event,
            session_id.to_string(),
            participants.to_vec(),
        ));
        Ok(participants.len())
    }

    async fn notify_with_outcome(
        &self,
        group_id: &str,
        event: SystemMessageEvent,
        session_id: &str,
        participants: &[Participant],
    ) -> ServiceResult<SystemMessageDispatchOutcome> {
        self.notify(group_id, event, session_id, participants)
            .await?;
        Ok(SystemMessageDispatchOutcome {
            total_recipients: participants.len(),
            successful_deliveries: participants.len(),
            failed_deliveries: 0,
            recipient_results: Vec::new(),
        })
    }
}

struct Fixture {
    service: SessionLaunchApplication,
    bots: Arc<BotCore>,
    authority: Arc<LazySeedAuthorityHook>,
    groups: Arc<GroupCore>,
    sessions: Arc<SessionManagementServiceImpl>,
    session_repo: Arc<MemorySessionRepo>,
    runtime: Arc<RecordingRuntime>,
}

impl Fixture {
    fn new() -> Self {
        let bots = Arc::new(BotCore::memory());
        let group_repo: Arc<dyn GroupRepoPort> = Arc::new(MemoryGroupRepo::new());
        let groups = Arc::new(GroupCore::with_repo(group_repo.clone()));
        let session_repo = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            group_repo,
        ));
        let authority = bcs_test_support::lazy_authority_chain().hook;
        let service = SessionLaunchApplication::new(
            bots.clone(),
            groups.clone(),
            sessions.clone(),
            Arc::new(RecordingRuntime::default()),
            Arc::new(RecordingSystemMessage::default()),
        )
        .with_bot_authority_hook(authority.clone());
        Self {
            service,
            bots,
            authority,
            groups,
            sessions,
            session_repo,
            runtime: Arc::new(RecordingRuntime::default()),
        }
    }

    async fn add_bot(&self, bot_id: &str, owner: &str, visibility: &str) {
        self.bots
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: visibility.into(),
                    ..Default::default()
                },
            )
            .await
            .expect("register bot");
        self.bots
            .save_created_by(bot_id, owner, true)
            .await
            .expect("store owner");
        // The CURRENT owner edge is the fixture fact the
        // launch flow consults (row 17).
        self.authority.seed_owner(bot_id, owner);
    }

    async fn add_group(
        &self,
        group_id: &str,
        driver: &str,
        visibility: &str,
        extra: Vec<Participant>,
    ) {
        let mut participants = vec![Participant::bot(driver, ParticipantRole::Driver)];
        participants.extend(extra);
        let mut group = Group::new(group_id, driver, participants);
        group.group_strategy = GroupStrategy::Chat;
        group.visibility = visibility.into();
        self.groups.upsert(group.clone()).await.expect("store group");
        assert!(
            self.groups.try_get(group_id).await.unwrap().is_some(),
            "fixture group must be stored: {group_id}"
        );
    }
}

fn human(owner_id: &str) -> SessionCaller {
    SessionCaller::Human {
        actor_id: format!("human_{owner_id}"),
        owner_id: owner_id.to_string(),
        display_name: Some(owner_id.to_string()),
    }
}

fn bot(bot_uuid: &str) -> SessionCaller {
    SessionCaller::Bot {
        bot_uuid: bot_uuid.to_string(),
    }
}

fn request(
    caller: SessionCaller,
    group_id: &str,
    requested_creator: Option<&str>,
) -> SessionLaunchRequest {
    SessionLaunchRequest {
        caller,
        group_id: group_id.to_string(),
        requested_creator: requested_creator.map(str::to_string),
        title: None,
        kind: Some(SessionKind::Chat),
        input: None,
        meta: None,
        public_creator_role: None,
        human_message_view_scope: None,
        context_delivery: None,
    }
}

// ── 1. the A(owner)/M(manager)/X matrix at launch ────────

#[tokio::test]
async fn launch_control_matrix_follows_the_current_edges() {
    let fx = Fixture::new();
    fx.add_bot("driver", "alice", "public").await;
    fx.add_bot("worker", "alice", "public").await;
    fx.add_group(
        "group-1",
        "driver",
        "private",
        vec![Participant::bot("worker", ParticipantRole::Consultant)],
    )
    .await;

    // A = owner launches through its controlled Bot.
    let outcome = fx
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", Some("worker")),
        })
        .await
        .expect("owner may launch");
    assert_eq!(outcome.session.created_by.as_deref(), Some("worker"));
    assert_eq!(outcome.session.caller_principal.as_deref(), Some("human_alice"));

    // X = unrelated: denied.
    let denied = fx
        .service
        .create(CreateSessionLaunch {
            request: request(human("stranger"), "group-1", None),
        })
        .await;
    assert!(
        matches!(denied, Err(SessionLaunchError::Forbidden(_))),
        "unrelated Human is denied: {denied:?}"
    );
}

// ── 2. A→B handover: A revoked, B fine ───────────────────

#[tokio::test]
async fn revoked_edge_falls_back_to_nothing() {
    let fx = Fixture::new();
    fx.add_bot("driver", "alice", "public").await;
    fx.add_group("group-1", "driver", "private", vec![]).await;

    println!("DIRECT TRY_GET {:?}", fx.groups.try_get("group-1").await);
    // Alice launches while she owns the Bot.
    let before = fx
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", Some("driver")),
        })
        .await
        .expect("owner launches");
    assert_eq!(before.session.created_by.as_deref(), Some("driver"));

    // A→B: B becomes the owner, A keeps NOTHING.
    fx.authority.seed_owner("driver", "bob");
    fx.authority.revoke_owner("driver", "alice");

    let revoked = fx
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-1", Some("driver")),
        })
        .await;
    assert!(
        matches!(revoked, Err(SessionLaunchError::Forbidden(_))),
        "A without the ownership edge is fallback-free denied: {revoked:?}"
    );

    // B replaces Alice in the SAME entries.
    let outcome = fx
        .service
        .create(CreateSessionLaunch {
            request: request(human("bob"), "group-1", Some("driver")),
        })
        .await
        .expect("B launches through the handed-over Bot");
    assert_eq!(outcome.session.created_by.as_deref(), Some("driver"));
    assert_eq!(outcome.session.caller_principal.as_deref(), Some("human_bob"));
}

// ── 3. §8.1 sponsorship: private vs public Group ─────────

#[tokio::test]
async fn sponsorship_private_group_admits_controlled_bot_without_friendship() {
    let fx = Fixture::new();
    fx.add_bot("driver", "alice", "public").await;
    // A PROTECTED (non-hidden, non-public) Bot with
    // NO friendship anywhere in the fixture — the
    // ONLY fact that lets Alice reach it is the
    // CURRENT ownership edge.
    fx.add_bot("sponsor", "alice", "protected").await;
    fx.add_group(
        "group-private",
        "driver",
        "private",
        vec![Participant::bot("sponsor", ParticipantRole::Consultant)],
    )
    .await;

    // First session AND subsequent launches both go
    // through the sponsorship (§8.1 covers the first
    // Session and every later launch).
    for round in 1..=2 {
        let outcome = fx
            .service
            .create(CreateSessionLaunch {
                request: request(
                    human("alice"),
                    "group-private",
                    Some("sponsor"),
                ),
            })
            .await
            .unwrap_or_else(|error| {
                panic!("sponsorship round {round} must pass: {error:?}")
            });
        assert_eq!(outcome.session.created_by.as_deref(), Some("sponsor"));
        // The manager is NOT auto-added as a Human
        // participant (spec §8.1).
        assert!(
            !outcome
                .session
                .participants
                .iter()
                .any(|participant| participant.is_human()
                    && participant.bot_uuid == "human_alice"),
            "the controlling Human is not an automatic participant",
        );
    }

    // The same sponsorship survives the A→B handover.
    fx.authority.seed_owner("sponsor", "bob");
    fx.authority.revoke_owner("sponsor", "alice");
    let outcome = fx
        .service
        .create(CreateSessionLaunch {
            request: request(human("bob"), "group-private", Some("sponsor")),
        })
        .await
        .expect("B launches the sponsored Bot after the handover");
    assert_eq!(outcome.session.created_by.as_deref(), Some("sponsor"));
    // And A, without ANY edge, is fallback-free denied.
    let revoked = fx
        .service
        .create(CreateSessionLaunch {
            request: request(
                human("alice"),
                "group-private",
                Some("sponsor"),
            ),
        })
        .await;
    assert!(
        matches!(revoked, Err(SessionLaunchError::Forbidden(_))),
        "A without edges is denied: {revoked:?}"
    );
}

/// X = unrelated Human: denied. Pure-Bot callers
/// keep their own contract regardless of edges.
#[tokio::test]
async fn x_unrelated_human_is_denied_at_the_private_group() {
    let fx = Fixture::new();
    fx.add_bot("driver", "alice", "public").await;
    fx.add_group("group-private", "driver", "private", vec![]).await;

    let denied = fx
        .service
        .create(CreateSessionLaunch {
            request: request(human("stranger"), "group-private", None),
        })
        .await;
    assert!(
        matches!(denied, Err(SessionLaunchError::Forbidden(_))),
        "unrelated Human is denied: {denied:?}"
    );

    let by_bot = fx
        .service
        .create(CreateSessionLaunch {
            request: request(bot("driver"), "group-private", None),
        })
        .await
        .expect("pure Bot keeps its contract");
    assert_eq!(by_bot.session.created_by.as_deref(), Some("driver"));
}

/// The current-edge participant leg: withdrawal closes
/// the Human entry; the pure-Bot contract stays.
#[tokio::test]
async fn participant_controlling_legs_track_current_edges() {
    let fx = Fixture::new();
    fx.add_bot("driver", "driver-owner", "public").await;
    fx.add_bot("worker", "alice", "public").await;
    fx.add_group(
        "group-2",
        "driver",
        "private",
        vec![Participant::bot("worker", ParticipantRole::Consultant)],
    )
    .await;

    let outcome = fx
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-2", Some("worker")),
        })
        .await
        .expect("controlled participant Bot is a launch candidate");
    assert_eq!(outcome.session.created_by.as_deref(), Some("worker"));

    // Withdraw via the handover (a Bot always
    // keeps ONE owner): B takes the owner edge
    // and Alice's ONLY edge falls away — the
    // entry denies fallback-free.
    fx.authority.seed_owner("worker", "bob");
    fx.authority.revoke_owner("worker", "alice");
    let denied = fx
        .service
        .create(CreateSessionLaunch {
            request: request(human("alice"), "group-2", Some("worker")),
        })
        .await;
    assert!(
        matches!(denied, Err(SessionLaunchError::Forbidden(_))),
        "the withdrawn leg is denied: {denied:?}"
    );
    // B (the new owner) takes the SAME entry over.
    let outcome = fx
        .service
        .create(CreateSessionLaunch {
            request: request(human("bob"), "group-2", Some("worker")),
        })
        .await
        .expect("B launches through the handed-over Bot");
    assert_eq!(outcome.session.created_by.as_deref(), Some("worker"));
    // And the pure-Bot contract never depends on
    // any edge.
    let by_bot = fx
        .service
        .create(CreateSessionLaunch {
            request: request(bot("worker"), "group-2", None),
        })
        .await
        .expect("pure Bot keeps its contract without the edge");
    assert_eq!(by_bot.session.created_by.as_deref(), Some("worker"));
}
// ── 4. row-14 boundary: manager edge is not a Group role ──
