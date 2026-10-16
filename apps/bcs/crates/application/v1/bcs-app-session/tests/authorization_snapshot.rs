//! Session-facade §8.3 snapshot consumption tests: the
//! Session detail resource-role legs
//! (`can_read_session_detail`) read the STRICT
//! authorization snapshot — never this instance's display
//! session fetch.
//!
//! The scenario shape mirrors the 6b store conformance
//! and the group twin (`bcs-app-group`): TWO INDEPENDENT
//! session store instances over ONE database. The app's
//! instance caches its session rows; the
//! resource-qualification change is committed through the
//! INDEPENDENT peer; a cached pass must fail after the
//! peer's change, and the CURRENT-control matrix
//! (A → B handover) stays exact.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use bcs_bot::BotCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_db_api::DbPlugin;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group::{GroupCore, MemoryGroupRepo};
use bcs_session::{
    SessionAuthorizationCoreImpl, SessionLaunchApplication,
    SessionManagementServiceImpl,
};
use bcs_session_store::MySqlSessionStore;
use bcs_app_session::SessionServiceImpl;
use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedCaller, AuthenticatedUserIdentity,
    GetSession, SessionService,
};
use bcs_service_api::port::repo::{
    GroupRepoPort, NewSessionParams, SessionRepoPort,
};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupCoreService,
    Participant, ParticipantRole, SessionKind,
};
use bcs_test_support::{
    authority_hook_for, grant_owner_edge, revoke_authority_edge,
    NoopCollaborationRuntimeService as NoopRuntime,
    NoopGroupMessageHistoryService as NoopHistory,
    NoopSystemMessageService,
};

#[path = "../../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

/// The env binding of both store instances.
const ENV: &str = "cutover-snap";

struct Fixture {
    app: SessionServiceImpl,
    /// The Group Core instance A (the app) reads
    /// the parent Group through.
    groups: Arc<GroupCore>,
    /// The session store instance the app reads
    /// through (its `get` caches rows).
    store: Arc<dyn SessionRepoPort>,
    /// The independent peer session store over the
    /// SAME database: the participant change is
    /// committed here, so the app instance's cached
    /// session rows go stale.
    peer: Arc<dyn SessionRepoPort>,
    authority_repo: Arc<MemoryBotRepo>,
    bots: Arc<BotCore>,
}

impl Fixture {
    async fn build() -> Self {
        Self::build_with_snapshot_core(true).await
    }

    /// `wired=false` proves the composition rule:
    /// without the strict snapshot Core the
    /// detail read fails CLOSED (internal), it
    /// never silently falls back to the display
    /// fetch.
    async fn build_with_snapshot_core(wired: bool) -> Self {
        let db: Arc<dyn DbPlugin> =
            Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
        bootstrap_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .expect("migrate sqlite");
        // Instance A: the app's session store.
        let store = MySqlSessionStore::sqlite(Arc::clone(&db), ENV.to_string());
        // The peer: SAME database, OWN cache.
        let peer: Arc<dyn SessionRepoPort> = Arc::new(
            MySqlSessionStore::sqlite(db, ENV.to_string()),
        );
        let store_as_port: Arc<dyn SessionRepoPort> = Arc::new(store);

        let group_repo = Arc::new(MemoryGroupRepo::new());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            store_as_port.clone(),
            group_repo.clone(),
        ));
        let groups = Arc::new(GroupCore::with_repo(
            group_repo as Arc<dyn GroupRepoPort>,
        ));
        let bots = Arc::new(BotCore::memory());
        let friends = Arc::new(bcs_friend::FriendCore::memory());
        let authority_repo = Arc::new(MemoryBotRepo::new());
        let authority = authority_hook_for(authority_repo.clone());
        let launch = Arc::new(
            SessionLaunchApplication::new(
                bots.clone(),
                groups.clone() as Arc<dyn GroupCoreService>,
                sessions.clone(),
                Arc::new(NoopRuntime),
                Arc::new(NoopSystemMessageService),
            )
            .with_bot_authority_hook(authority.clone()),
        );
        let app = SessionServiceImpl::new(
            launch,
            sessions,
            groups.clone() as Arc<dyn GroupCoreService>,
            bots.clone(),
            friends,
            store_as_port.clone(),
            Arc::new(NoopHistory),
            Arc::new(NoopRuntime),
            Arc::new(NoopSystemMessageService),
        )
        .with_bot_authority_hook(authority.clone());
        let app = if wired {
            app.with_session_authorization_core(Arc::new(
                SessionAuthorizationCoreImpl::new(
                    store_as_port.clone(),
                ),
            ))
        } else {
            app
        };
        Self {
            app,
            groups,
            store: store_as_port,
            peer,
            authority_repo,
            bots,
        }
    }

    async fn register_bot(&self, bot_id: &str) {
        self.bots
            .register(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    visibility: "public".to_string(),
                    ..Default::default()
                },
            )
            .await
            .expect("register fixture bot");
    }

    /// The CURRENT-owner control grant (`can_manage`).
    async fn control(&self, bot_id: &str, user: &str) {
        grant_owner_edge(&self.authority_repo, bot_id, user).await;
    }

    /// Seed a private Group (`bot-d` driver) and a session
    /// whose Bot participants are `bot-d` plus the given
    /// extras.
    async fn seed_private_session(&self, session_id: &str, extras: &[&str]) {
        let mut participants =
            vec![Participant::bot("bot-d", ParticipantRole::Driver)];
        for bot_id in extras {
            participants.push(Participant::bot(
                *bot_id,
                ParticipantRole::Consultant,
            ));
        }
        let group =
            Group::new("g1", "bot-d", participants.clone());
        self.groups.upsert(group).await.expect("store group");
        self.store
            .create(
                "g1",
                NewSessionParams {
                    id: Some(session_id.to_string()),
                    session_kind: SessionKind::Chat,
                    participants,
                    created_by: Some("bot-d".to_string()),
                    ..Default::default()
                },
            )
            .await
            .expect("store session");
    }

    /// Read the Session detail through the app.
    async fn detail(&self, session_id: &str, user: &str) -> Result<(), ApplicationError> {
        self.app
            .get(GetSession {
                caller: human(user),
                session_id: session_id.to_string(),
            })
            .await
            .map(|_| ())
    }
}

fn human(user_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("t".into()),
        user: Some(AuthenticatedUserIdentity {
            id: user_id.into(),
            username: user_id.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

/// The Session detail read consumes the §8.3 SNAPSHOT
/// for its participants: after the peer removes the
/// participant, the app's cached session copy must NOT
/// keep the pass alive.
#[tokio::test]
async fn session_detail_read_consumes_authoritative_roles() {
    let fixture = Fixture::build().await;
    for bot in ["bot-d", "bot-x"] {
        fixture.register_bot(bot).await;
    }
    fixture.seed_private_session("g1:abcd1234", &["bot-x"]).await;
    fixture.control("bot-x", "alice").await;

    // A(owner): reads the Session through the participant
    // Bot it CURRENTLY controls.
    fixture
        .detail("g1:abcd1234", "alice")
        .await
        .expect("the owner reads through the controlled participant");

    // A→B handover: B's owner grant REPLACES A's; A's
    // historical control is gone even though the app's
    // cached Session copy still lists bot-x.
    grant_owner_edge(&fixture.authority_repo, "bot-x", "bob").await;
    revoke_authority_edge(
        &fixture.authority_repo,
        "bot-x",
        "alice",
        bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner,
    )
    .await;
    let denied = fixture
        .detail("g1:abcd1234", "alice")
        .await
        .expect_err("the revoked owner no longer reads the session");
    assert!(matches!(denied, ApplicationError::Forbidden(_)));
    fixture
        .detail("g1:abcd1234", "bob")
        .await
        .expect("the CURRENT controller reads the session");

    // The resource-qualification removal through the
    // INDEPENDENT peer: bot-x leaves the session. The
    // app instance's cached session copy still holds
    // bot-x — the SNAPSHOT no longer does, so B's
    // cached pass must now fail.
    fixture
        .peer
        .remove_participant("g1:abcd1234", "bot-x")
        .await
        .expect("peer removes participant");
    let denied_after_removal = fixture
        .detail("g1:abcd1234", "bob")
        .await
        .expect_err("the removed participant's controller no longer reads the session");
    assert!(matches!(
        denied_after_removal,
        ApplicationError::Forbidden(_)
    ));
}

/// Unwired snapshot core: the detail read fails
/// closed as an internal error — the resource
/// decision never silently falls back to the
/// display fetch.
#[tokio::test]
async fn session_detail_fails_closed_without_the_snapshot_core() {
    let fixture = Fixture::build_with_snapshot_core(false).await;
    for bot in ["bot-d", "bot-x"] {
        fixture.register_bot(bot).await;
    }
    fixture.seed_private_session("g1:abcd1234", &["bot-x"]).await;
    fixture.control("bot-x", "alice").await;

    let error = fixture
        .detail("g1:abcd1234", "alice")
        .await
        .expect_err("an unwired snapshot core fails closed");
    assert!(matches!(error, ApplicationError::Internal(_)));
}
