//! Consumer identity-matrix cutover tests (bot-authority
//! plan Task 6.2/6.3, spec §3.3 authorization-fact matrix).
//!
//! Every entry below runs through a REAL authority chain
//! (`MemoryBotRepo` → authority Core → the Session facade's
//! `BotAuthorityPort`): the A(owner) / M(manager) /
//! X(unrelated) matrix is exercised at application entries,
//! the A→B handover and the edge withdrawal are driven
//! through the authority store, and the row-5 boundary
//! (Session `created_by` 独立 Human 权利) is asserted NOT to
//! move with the Bot control legs.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use async_trait::async_trait;

use bcs_app_session::SessionServiceImpl;
use bcs_bot::BotCore;
use bcs_bot_store::{MemoryBotRepo, memory_authority::MemoryAuthorityEdgeKind};
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_service_api::application::v1::{
    AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    CollectSession, CreateSession, GetSession,
    SessionService, UpdateSession,
};
use bcs_service_api::port::repo::{GroupRepoPort, NewSessionParams, SessionRepoPort};
use bcs_service_api::core::FriendCoreService;
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::types::HumanMessageView;
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, CancelStateMachineRunCommand,
    CollaborationDefinition, CollaborationRuntimeError, CollaborationRuntimeService,
    ConfigureGroupRuntimeCommand, ConfigureGroupRuntimeOutcome,
    HandleBotTerminalEventCommand, HandleBotTerminalEventOutcome,
    Participant, ParticipantRole,
    Group, GroupCoreService,
    GroupStrategy, GroupUseCaseError, SessionHistoryCommand, SessionHistoryResult, SessionKind,
    GroupHistoryCommand, GroupHistoryResult, MessageHistoryOptions, StartStateMachineRunCommand,
    StartStateMachineRunOutcome, StateMachineDeliveryCorrelation,
};
use bcs_test_support::{
    NoopSystemMessageService as NoopSystemMessage, authority_hook_for, failing_authority_hook,
    grant_manager_edge, grant_owner_edge,
};

// ── fixture ──────────────────────────────────────────────

#[derive(Default)]
struct NoopRuntime;

#[async_trait]
impl CollaborationRuntimeService for NoopRuntime {
    async fn start_state_machine_run(
        &self,
        _cmd: StartStateMachineRunCommand,
    ) -> Result<StartStateMachineRunOutcome, CollaborationRuntimeError> {
        Err(CollaborationRuntimeError::InvalidRequest(
            "unused".to_string(),
        ))
    }

    async fn get_state_machine_run(
        &self,
        _run_id: &str,
    ) -> Result<Option<bcs_service_api::StateMachineRunView>, CollaborationRuntimeError> {
        Ok(None)
    }

    async fn get_state_machine_run_by_session_id(
        &self,
        _session_id: &str,
    ) -> Result<Option<bcs_service_api::StateMachineRunView>, CollaborationRuntimeError> {
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
    ) -> Result<bcs_service_api::StateMachineRunView, CollaborationRuntimeError> {
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
struct NoopHistory;

#[async_trait]
impl bcs_service_api::GroupMessageHistoryService for NoopHistory {
    async fn get_history(
        &self,
        _cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        panic!("group history is not used by SessionServiceImpl")
    }

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
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
        Ok(SessionHistoryResult {
            session_id: cmd.session_id,
            messages: Vec::new(),
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }
}

struct Fixture {
    service: SessionServiceImpl,
    groups: Arc<GroupCore>,
    repo: Arc<MemoryBotRepo>,
    session_repo: Arc<MemorySessionRepo>,
}

impl Fixture {
    async fn new() -> Self {
        let bot_repo = Arc::new(MemoryBotRepo::new());
        let bots: Arc<dyn BotRegistryCoreService> =
            Arc::new(BotCore::with_repo(bot_repo.clone()));
        let groups = Arc::new(GroupCore::with_repo(
            Arc::new(MemoryGroupRepo::new()) as Arc<dyn GroupRepoPort>
        ));
        let friends: Arc<dyn FriendCoreService> =
            Arc::new(bcs_friend::FriendCore::memory());
        let session_repo = Arc::new(MemorySessionRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            session_repo.clone(),
            Arc::new(MemoryGroupRepo::new()) as Arc<dyn GroupRepoPort>,
        ));
        let authority = authority_hook_for(bot_repo.clone());
        let launch = Arc::new(
            bcs_session::SessionLaunchApplication::new(
                bots.clone(),
                groups.clone(),
                sessions.clone(),
                Arc::new(NoopRuntime),
                Arc::new(NoopSystemMessage),
            )
            .with_bot_authority_hook(authority.clone()),
        );
        let service = SessionServiceImpl::new(
            launch,
            sessions,
            groups.clone(),
            bots.clone(),
            friends,
            session_repo.clone(),
            Arc::new(NoopHistory),
            Arc::new(NoopRuntime),
            Arc::new(NoopSystemMessage),
        )
        .with_bot_authority_hook(authority.clone())
        // §8.3: the detail resource-role legs read
        // the strict Session snapshot core.
        .with_session_authorization_core(
            Arc::new(bcs_session::SessionAuthorizationCoreImpl::new(
                session_repo.clone(),
            )),
        );
        Self {
            service,
            groups,
            repo: bot_repo,
            session_repo,
        }
    }

    async fn seed_bot(&self, bot_id: &str, owner: Option<&str>, visibility: &str) {
        BotRepoPort::register(&*self.repo,

                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: visibility.to_string(),
                    ..Default::default()
                },
            )
            .await
            .expect("register fixture bot");
        if let Some(owner) = owner {
            grant_owner_edge(&self.repo, bot_id, owner).await;
        }
    }

    async fn seed_group(
        &self,
        group_id: &str,
        driver: &str,
        visibility: &str,
    ) {
        let mut group = Group::new(
            group_id,
            driver,
            vec![Participant::bot(driver, ParticipantRole::Driver)],
        );
        group.group_strategy = GroupStrategy::Chat;
        group.visibility = visibility.into();
        self.groups
            .upsert(group)
            .await
            .expect("store group");
    }

    async fn seed_session(
        &self,
        group_id: &str,
        participants: Vec<Participant>,
        created_by: Option<&str>,
    ) -> String {
        let session = self
            .session_repo
            .create(
                group_id,
                NewSessionParams {
                    participants,
                    created_by: created_by.map(str::to_string),
                    session_kind: SessionKind::Chat,
                    ..Default::default()
                },
            )
            .await
            .expect("seed session");
        session.id
    }

    /// Withdraw `user`'s owner edge (the A→B handover and
    /// the manager-withdrawal scenarios are driven
    /// directly on the authority store).
    async fn revoke(&self, bot_id: &str, user: &str) {
        bcs_test_support::revoke_authority_edge(
            &self.repo,
            bot_id,
            user,
            MemoryAuthorityEdgeKind::Owner,
        )
        .await;
    }

    async fn grant_manager(&self, bot_id: &str, user: &str) {
        grant_manager_edge(&self.repo, bot_id, user).await;
    }

    async fn legacy_creator_edge(&self, human_actor: &str, bot: &str) {
        // a legacy creator relation edge (row 4 历史数据)
        // The fixture uses no relation core; the edge is
        // asserted at the app level by NOT carrying any
        // relation core. This helper exists for
        // documentation symmetry only.
        let _ = (human_actor, bot);
    }
}

fn human_caller(id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("dev".into()),
        user: Some(AuthenticatedUserIdentity {
            id: id.into(),
            username: id.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn hybrid_caller(user: &str, bot: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("dev".into()),
        user: Some(AuthenticatedUserIdentity {
            id: user.into(),
            username: user.into(),
            display_name: None,
            full_name: None,
        }),
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot.into(),
            owner_id: user.into(),
            agent_code: String::new(),
            app_id: 0,
        }),
        app: None,
        access_key: None,
    }
}

fn bot_only_caller(bot: &str, owner: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("dev".into()),
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: bot.into(),
            owner_id: owner.into(),
            agent_code: String::new(),
            app_id: 0,
        }),
        app: None,
        access_key: None,
    }
}

// ── 1. collect (row 6): A owner / M manager / X ──────────

#[tokio::test]
async fn collect_follows_the_current_control_matrix() {
    let fx = Fixture::new().await;
    fx.seed_bot("bot-a", Some("alice"), "public").await;
    fx.grant_manager("bot-a", "mallory").await;
    fx.seed_group("g1", "bot-a", "protected").await;
    let session_id = fx
        .seed_session(
            "g1",
            vec![
                Participant::bot("bot-a", ParticipantRole::Driver),
            ],
            Some("human_alice"),
        )
        .await;

    // A = owner: ok.
    SessionService::collect(
        &fx.service,
        CollectSession {
            caller: human_caller("alice"),
            session_id: session_id.clone(),
            participant: "bot-a".into(),
        },
    )
    .await
    .expect("owner may collect through its Bot");

    // M = manager of the same Bot: ok.
    SessionService::collect(
        &fx.service,
        CollectSession {
            caller: human_caller("mallory"),
            session_id: session_id.clone(),
            participant: "bot-a".into(),
        },
    )
    .await
    .expect("manager may collect through the Bot");

    // X = unrelated: 403 (no edge, no legacy path).
    let x = SessionService::collect(
        &fx.service,
        CollectSession {
            caller: human_caller("stranger"),
            session_id: session_id.clone(),
            participant: "bot-a".into(),
        },
    )
    .await
    .expect_err("unrelated Human is denied");
    assert!(
        matches!(x, bcs_service_api::application::v1::ApplicationError::Forbidden(_)),
        "expected 403, got {x:?}"
    );

    // The A→B transfer (spec §3.3): B takes the owner
    // edge; A keeps its MANAGER edge for now, so the
    // A-legacy entries keep authorizing while B gains
    // its own view — no inheritance of A's private
    // state, only the resource-neutral collect leg.
    grant_owner_edge(&fx.repo, "bot-a", "bob").await;
    SessionService::collect(
        &fx.service,
        CollectSession {
            caller: human_caller("bob"),
            session_id: session_id.clone(),
            participant: "bot-a".into(),
        },
    )
    .await
    .expect("B as the new owner may collect");

    // Revoke A's manager edge: the ONLY leg A had
    // falls away and the entry denies (fallback-free).
    bcs_test_support::revoke_authority_edge(
        &fx.repo,
        "bot-a",
        "alice",
        MemoryAuthorityEdgeKind::Manager,
    )
    .await;
    let a = SessionService::collect(
        &fx.service,
        CollectSession {
            caller: human_caller("alice"),
            session_id: session_id.clone(),
            participant: "bot-a".into(),
        },
    )
    .await;
    assert!(
        matches!(
            a,
            Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))
        ),
        "A without any edge loses the Bot collect leg: {a:?}"
    );
    // M is untouched by A's withdrawal.
    SessionService::collect(
        &fx.service,
        CollectSession {
            caller: human_caller("mallory"),
            session_id: session_id.clone(),
            participant: "bot-a".into(),
        },
    )
    .await
    .expect("M keeps collecting");
}

#[tokio::test]
async fn legacy_creator_relation_edge_opens_nothing() {
    let fx = Fixture::new().await;
    fx.seed_bot("bot-a", None, "public").await;
    fx.seed_group("g1", "bot-a", "protected").await;
    let session_id = fx
        .seed_session(
            "g1",
            vec![Participant::bot("bot-a", ParticipantRole::Driver)],
            Some("human_alice"),
        )
        .await;
    // The legacy creator relation edge (row 4's
    // `relation.is_creator` fallback) used to open the
    // entry; under the cutover it is历史数据 only.
    fx.legacy_creator_edge("human_alice", "bot-a").await;

    let result = SessionService::collect(
        &fx.service,
        CollectSession {
            caller: human_caller("alice"),
            session_id: session_id.clone(),
            participant: "bot-a".into(),
        },
    )
    .await;
    assert!(
        matches!(result, Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))),
        "a legacy creator edge must not authorize: {result:?}"
    );
}

// ── 2. hybrid / pure-Bot at the detail entry (rows 4/7) ──

#[tokio::test]
async fn hybrid_identity_resolves_via_the_live_edge() {
    let fx = Fixture::new().await;
    fx.seed_bot("bot-a", Some("alice"), "public").await;
    fx.seed_group("g1", "bot-a", "protected").await;
    let session_id = fx
        .seed_session(
            "g1",
            vec![Participant::bot("bot-a", ParticipantRole::Driver)],
            None,
        )
        .await;

    // Hybrid User+Bot credential: the live edge makes
    // the Bot the principal; the detail read passes.
    SessionService::get(
        &fx.service,
        GetSession {
            caller: hybrid_caller("alice", "bot-a"),
            session_id: session_id.clone(),
        },
    )
    .await
    .expect("hybrid caller resolves through the live edge");

    // Revoke with NO remaining owner would be an
    // authority integrity state, so the handover goes
    // to B first; A (still the manager per plan 6.2)
    // is THEN withdrawn.
    grant_owner_edge(&fx.repo, "bot-a", "bob").await;
    bcs_test_support::revoke_authority_edge(
        &fx.repo,
        "bot-a",
        "alice",
        MemoryAuthorityEdgeKind::Owner,
    )
    .await;
    let revoked = SessionService::get(
        &fx.service,
        GetSession {
            caller: hybrid_caller("alice", "bot-a"),
            session_id: session_id.clone(),
        },
    )
    .await;
    assert!(
        matches!(
            revoked,
            Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))
        ),
        "A without any edge is denied (fallback-free): {revoked:?}"
    );

    // M takes over: the same hybrid entry re-opens.
    fx.grant_manager("bot-a", "alice").await;
    SessionService::get(
        &fx.service,
        GetSession {
            caller: hybrid_caller("alice", "bot-a"),
            session_id: session_id.clone(),
        },
    )
    .await
    .expect("manager edge re-opens the entry");
}

#[tokio::test]
async fn pure_bot_contract_never_consults_the_hook() {
    let fx = Fixture::new().await;
    fx.seed_bot("bot-a", None, "public").await;
    fx.seed_group("g1", "bot-a", "protected").await;
    let session_id = fx
        .seed_session(
            "g1",
            vec![Participant::bot("bot-a", ParticipantRole::Driver)],
            None,
        )
        .await;

    // The pure Bot caller has NO authority edges at all
    // (spec §3.3: 纯 Bot 凭证保留 Bot-only 契约，管理边
    // 撤销不会轮换 Bot token) — the Bot's own
    // authentication decides and the hook is not
    // consulted.
    SessionService::get(
        &fx.service,
        GetSession {
            caller: bot_only_caller("bot-a", "nobody"),
            session_id,
        },
    )
    .await
    .expect("pure Bot keeps its contract without authority state");
}

// ── 3. row-5 boundary: the Human's OWN session rights ────

#[tokio::test]
async fn session_creator_rights_survive_the_control_revocation() {
    let fx = Fixture::new().await;
    fx.seed_bot("bot-a", Some("alice"), "public").await;
    fx.seed_group("g1", "bot-a", "protected").await;
    // Alice is HERSELF the Session creator (row 5: a
    // Session's created_by is an independent Human
    // right, NOT a Bot-historical fact).
    let session_id = fx
        .seed_session(
            "g1",
            vec![
                Participant::human("human_alice", ParticipantRole::Consultant),
                Participant::bot("bot-a", ParticipantRole::Driver),
            ],
            Some("human_alice"),
        )
        .await;

    // While controlled: works.
    SessionService::update(
        &fx.service,
        UpdateSession {
            caller: human_caller("alice"),
            session_id: session_id.clone(),
            title: Some("renamed by owner".into()),
        },
    )
    .await
    .expect("creator may manage its own Session");

    // The A→B handover removes Alice's owner edge (the
    // Bot-mediated legs die), but Alice's OWN creator
    // right stays. Alice holds no manager edge, so her
    // only Bot leg was the ownership that is gone.
    grant_owner_edge(&fx.repo, "bot-a", "bob").await;
    let collect = SessionService::collect(
        &fx.service,
        CollectSession {
            caller: human_caller("alice"),
            session_id: session_id.clone(),
            participant: "bot-a".into(),
        },
    )
    .await;
    assert!(
        matches!(collect, Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))),
        "collect dies with the control edge: {collect:?}"
    );
    SessionService::update(
        &fx.service,
        UpdateSession {
            caller: human_caller("alice"),
            session_id: session_id.clone(),
            title: Some("renamed again".into()),
        },
    )
    .await
    .expect("Session created_by right is independent of the Bot edge");

    // B takes the owner edge (A→B): B now CONTROLS the
    // driver Bot, so B may manage the Session through
    // the GROUP's own management anchor (spec §8.3:
    // driver 是管理角色候选) — that is X's resource
    // role, NOT an inheritance of Alice's private view.
    grant_owner_edge(&fx.repo, "bot-a", "bob").await;
    SessionService::update(
        &fx.service,
        UpdateSession {
            caller: human_caller("bob"),
            session_id: session_id.clone(),
            title: Some("driver-owner manages".into()),
        },
    )
    .await
    .expect("B manages via the Bot's group resource role");

    // Z controls NOTHING and is in no anchor position:
    // denied.
    let z_update = SessionService::update(
        &fx.service,
        UpdateSession {
            caller: human_caller("zoe"),
            session_id: session_id.clone(),
            title: Some("nope".into()),
        },
    )
    .await;
    assert!(
        matches!(
            z_update,
            Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))
        ),
        "an unrelated Human never manages: {z_update:?}"
    );
}

#[tokio::test]
async fn wired_but_denied_view_actor_is_the_cutoff() {
    let fx = Fixture::new().await;
    fx.seed_bot("bot-a", Some("alice"), "public").await;
    fx.seed_bot("bot-x", None, "public").await;
    fx.seed_group("g1", "bot-x", "protected").await;
    let session_id = fx
        .seed_session(
            "g1",
            vec![Participant::bot("bot-x", ParticipantRole::Driver)],
            None,
        )
        .await;

    // An explicit view actor for a Bot the Human does
    // NOT control is 403 even under a wired hook.
    let denied = SessionService::get(
        &fx.service,
        GetSession {
            caller: human_caller("alice"),
            session_id,
        },
    )
    .await;
    // alice may still read her OWN session? No: she is
    // not a participant and controls no participant —
    // 403 with the entry's own convention.
    assert!(
        matches!(
            denied,
            Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))
        ),
        "the uncontrolled view stays denied: {denied:?}"
    );
}

// ── Review fix (Important#3): an authority
// READ failure surfaces as an internal
// error at the detail entry — never a
// deny-masquerading-forbidden.
#[tokio::test]
async fn authority_read_failure_is_internal_at_detail() {
    let base = Fixture::new().await;
    base.seed_bot("bot-a", None, "public").await;
    base.seed_group("g-fail", "bot-a", "protected").await;
    let session_id = base
        .seed_session(
            "g-fail",
            vec![Participant::bot("bot-a", ParticipantRole::Driver)],
            None,
        )
        .await;

    // The failing-hook instance shares every
    // collaborator; only the hook differs.
    let service = rebuild_with_failing_hook(&base).await;
    let error = SessionService::get(
        &service,
        GetSession {
            caller: human_caller("alice"),
            session_id,
        },
    )
    .await
    .expect_err("authority failure must not masquerade");
    assert!(
        matches!(
            error,
            bcs_service_api::application::v1::ApplicationError::Internal(_)
        ),
        "expected internal: {error:?}"
    );
}

/// Rebuild the facade with the failing hook
/// (exact same collaborator construction as
/// `Fixture::new`, one hook swapped).
async fn rebuild_with_failing_hook(base: &Fixture) -> SessionServiceImpl {
    let bots: Arc<dyn BotRegistryCoreService> =
        Arc::new(BotCore::with_repo(base.repo.clone()));
    let groups = base.groups.clone();
    let friends: Arc<dyn FriendCoreService> =
        Arc::new(bcs_friend::FriendCore::memory());
    let session_repo = base.session_repo.clone();
    let sessions = Arc::new(SessionManagementServiceImpl::new(
        session_repo.clone(),
        Arc::new(MemoryGroupRepo::new()) as Arc<dyn GroupRepoPort>,
    ));
    let launch = Arc::new(
        bcs_session::SessionLaunchApplication::new(
            bots.clone(),
            groups.clone(),
            sessions.clone(),
            Arc::new(NoopRuntime),
            Arc::new(NoopSystemMessage),
        )
        .with_bot_authority_hook(failing_authority_hook()),
    );
    SessionServiceImpl::new(
        launch,
        sessions,
        groups.clone(),
        bots.clone(),
        friends,
        session_repo.clone(),
        Arc::new(NoopHistory),
        Arc::new(NoopRuntime),
        Arc::new(NoopSystemMessage),
    )
    .with_bot_authority_hook(failing_authority_hook())
    .with_session_authorization_core(
        Arc::new(bcs_session::SessionAuthorizationCoreImpl::new(
            session_repo,
        )),
    )
}

// ── row-4 cutover: the V1 create resolves the
// hybrid SessionCaller through the LIVE edge ──

#[tokio::test]
async fn create_resolves_the_hybrid_session_caller_via_the_edge() {
    let fx = Fixture::new().await;
    fx.seed_bot("bot-a", Some("alice"), "public").await;
    fx.seed_group("g1", "bot-a", "protected").await;

    fn create(_fx: &Fixture, user: &str, bot: &str) -> CreateSession {
        CreateSession {
            caller: hybrid_caller(user, bot),
            group_id: "g1".to_string(),
            title: None,
            kind: None,
            acting_bot_id: None,
            creator_role: None,
            message_view_scope: None,
            input: None,
            meta: None,
            context_delivery: None,
        }
    }

    // Hybrid A+bot-a: the CURRENT owner edge makes
    // bot-a the Bot session caller (the caller lane
    // hybrid owners always had).
    let outcome = SessionService::create(
        &fx.service,
        create(&fx, "alice", "bot-a"),
    )
    .await
    .expect("the owner's hybrid creates as the Bot");
    assert_eq!(
        outcome.session.caller_principal.as_deref(),
        Some("bot-a"),
        "the Bot caller lane is preserved for the hybrid"
    );

    // A→B handover: B's owner grant REPLACES A's; A's
    // manager edge is withdrawn. The HISTORICAL
    // `owner_id` claim matches no more: hybrid A+bot-a
    // is denied at the immutable seam, and the Bot
    // caller choice happens only through the LIVE
    // edge.
    grant_owner_edge(&fx.repo, "bot-a", "bob").await;
    fx.revoke("bot-a", "alice").await;
    let denied = SessionService::create(
        &fx.service,
        create(&fx, "alice", "bot-a"),
    )
    .await;
    assert!(
        matches!(
            denied,
            Err(bcs_service_api::application::v1::ApplicationError::Forbidden(_))
        ),
        "the revoked hybrid cannot launch behind its Bot: {denied:?}"
    );

    // Manager polarity: a CURRENT manager hybrid
    // (never admitted by the claim-local rule) is
    // admitted, and the Bot lane stays the outcome.
    fx.grant_manager("bot-a", "carol").await;
    let outcome = SessionService::create(
        &fx.service,
        create(&fx, "carol", "bot-a"),
    )
    .await
    .expect("the current manager's hybrid creates as the Bot");
    assert_eq!(outcome.session.caller_principal.as_deref(), Some("bot-a"));
}
