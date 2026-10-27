//! Group-facade §8.3 snapshot consumption tests: the
//! resource-role legs of `can_read_group_detail` /
//! `can_read_group` and the management-actor checks read
//! the STRICT authorization snapshot through the Group
//! Core — never this instance's display cache
//! (`try_get`).
//!
//! The scenario shape mirrors the 6b store conformance:
//! TWO INDEPENDENT store instances over ONE database. The
//! app's instance caches its `try_get` copy; the
//! resource-qualification change is committed through the
//! INDEPENDENT peer instance; a cached pass must fail
//! after the peer's change, and the CURRENT-control
//! matrix (A → B handover) stays exact.

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
use bcs_friend::FriendCore;
use bcs_group::{
    GroupConfig, GroupCore, GroupManagement, MemoryGroupRepo as InMemoryGroupRepo,
};
use bcs_group_store::MySqlGroupStore;
use bcs_relation::RelationCore;
use bcs_app_group::GroupServiceImpl;
use bcs_service_api::application::v1::{
    AddGroupParticipant, ApplicationError, GetGroup, GroupService,
};
use bcs_service_api::port::repo::{GroupRepoPort, SessionRepoPort};
use bcs_service_api::application::v1::{
    AuthenticatedCaller, AuthenticatedUserIdentity,
};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, Group, GroupCoreService,
    Participant, ParticipantRole,
};
use bcs_session::SessionManagementServiceImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::{authority_hook_for, grant_owner_edge, revoke_authority_edge};

#[path = "../../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

/// The env binding of both store instances.
const ENV: &str = "cutover-snap";

struct Fixture {
    app: GroupServiceImpl,
    /// The Group Core instance A (the app) reads
    /// through — its `try_get` caches rows.
    groups: Arc<GroupCore>,
    /// The independent peer instance over the SAME
    /// database: the resource-qualification change is
    /// committed here, so the app instance's cached
    /// `try_get` copy goes stale.
    peer: Arc<dyn GroupRepoPort>,
    /// The authority store backing the app hook: the
    /// CURRENT control edges are driven here.
    authority_repo: Arc<MemoryBotRepo>,
    bots: Arc<BotCore>,
}

impl Fixture {
    async fn build() -> Self {
        let db: Arc<dyn DbPlugin> =
            Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
        bootstrap_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .expect("migrate sqlite");
        // Instance A: the app's store (its `try_get`
        // caches the rows it reads).
        let store = MySqlGroupStore::sqlite(Arc::clone(&db), ENV.to_string());
        // The peer: SAME database, OWN cache.
        let peer: Arc<dyn GroupRepoPort> = Arc::new(
            MySqlGroupStore::sqlite(db, ENV.to_string()),
        );

        let groups = Arc::new(GroupCore::with_repo(
            Arc::new(store) as Arc<dyn GroupRepoPort>
        ));
        let bots = Arc::new(BotCore::memory());
        let friends = Arc::new(FriendCore::memory());
        let relation = Arc::new(RelationCore::memory());
        let sessions = Arc::new(SessionManagementServiceImpl::new(
            Arc::new(MemorySessionRepo::new()) as Arc<dyn SessionRepoPort>,
            Arc::new(InMemoryGroupRepo::new()),
        ));
        let authority_repo = Arc::new(MemoryBotRepo::new());
        let management = Arc::new(
            GroupManagement::new(
                groups.clone(),
                bots.clone(),
                friends.clone(),
                relation,
                GroupConfig::default(),
                sessions.clone(),
                Arc::new(bcs_test_support::NoopSystemMessageService),
            )
            .with_bot_authority(authority_hook_for(authority_repo.clone()))
            .for_v1_openapi(),
        );
        let app = GroupServiceImpl::new(
            groups.clone(),
            bots.clone(),
            friends,
            sessions,
            management,
        )
        .with_bot_authority_hook(authority_hook_for(authority_repo.clone()));
        Self {
            app,
            groups,
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

    /// Seed a private group through instance A: driver
    /// `bot-d`, plus the given extra Bot participants.
    async fn seed_private_group(
        &self,
        group_id: &str,
        extras: &[(&str, ParticipantRole)],
        manager_worker: bool,
    ) {
        let mut participants =
            vec![bot_participant("bot-d", ParticipantRole::Driver)];
        for (bot_id, role) in extras {
            participants.push(bot_participant(bot_id, *role));
        }
        let mut group = Group::new(group_id, "bot-d", participants);
        group.group_strategy = if manager_worker {
            bcs_service_api::GroupStrategy::ManagerWorker
        } else {
            bcs_service_api::GroupStrategy::Chat
        };
        self.groups.upsert(group).await.expect("store group");
    }

    /// The CURRENT-owner control grant (`can_manage`).
    async fn control(&self, bot_id: &str, user: &str) {
        grant_owner_edge(&self.authority_repo, bot_id, user).await;
    }

    /// Commit a resource-qualification change through the
    /// INDEPENDENT peer (the app's cached copy must not
    /// shadow it).
    async fn peer_remove_participant(&self, group_id: &str, actor: &str) {
        self.peer
            .remove_participant(group_id, actor)
            .await
            .expect("peer removes participant");
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

fn bot_participant(actor_id: &str, role: ParticipantRole) -> Participant {
    Participant::bot(actor_id, role)
}

async fn detail(fixture: &Fixture, group_id: &str, user: &str) -> Result<(), ApplicationError> {
    fixture
        .app
        .get(GetGroup {
            caller: human(user),
            group_id: group_id.to_string(),
        })
        .await
        .map(|_| ())
}

/// The detail read (a resource-role decision on the
/// group participants) consumes the §8.3 SNAPSHOT for
/// its participants: after the peer removes the
/// participant, the app's cached `try_get` copy must
/// NOT keep the pass alive.
#[tokio::test]
async fn group_detail_read_consumes_authoritative_roles() {
    let fixture = Fixture::build().await;
    for bot in ["bot-d", "bot-x"] {
        fixture.register_bot(bot).await;
    }
    fixture
        .seed_private_group("g1", &[("bot-x", ParticipantRole::Consultant)], false)
        .await;
    fixture.control("bot-x", "alice").await;

    // A(owner): reads through the participant Bot it
    // CURRENTLY controls.
    detail(&fixture, "g1", "alice")
        .await
        .expect("the owner reads through the controlled participant");

    // A→B handover: B's owner grant REPLACES A's; A's
    // historical control is gone even though the app's
    // cached Group copy still lists bot-x.
    grant_owner_edge(&fixture.authority_repo, "bot-x", "bob").await;
    revoke_authority_edge(
        &fixture.authority_repo,
        "bot-x",
        "alice",
        bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Manager,
    )
    .await;
    let denied = detail(&fixture, "g1", "alice")
        .await
        .expect_err("the revoked owner no longer reads the group");
    assert!(matches!(denied, ApplicationError::Forbidden(_)));

    detail(&fixture, "g1", "bob")
        .await
        .expect("the CURRENT controller reads the group");

    // The resource-qualification removal through the
    // INDEPENDENT peer: bot-x leaves the group. The app
    // instance's `try_get` cache still holds bot-x as a
    // participant — the SNAPSHOT leg no longer does, so
    // B's cached pass must now fail.
    fixture.peer_remove_participant("g1", "bot-x").await;
    let denied_after_removal = detail(&fixture, "g1", "bob")
        .await
        .expect_err("the removed participant's controller no longer reads the group");
    assert!(matches!(
        denied_after_removal,
        ApplicationError::Forbidden(_)
    ));
}

/// The management-actor checks consume the §8.3
/// SNAPSHOT for the management anchors: after the peer
/// removes the Manager participant, the app's cached
/// `try_get` copy must NOT keep a Manager-anchor
/// elevation alive.
#[tokio::test]
async fn group_manage_actors_read_authoritative_anchors() {
    let fixture = Fixture::build().await;
    for bot in ["bot-d", "bot-mx", "bot-new", "bot-new-2"] {
        fixture.register_bot(bot).await;
    }
    // ManagerWorker: `bot-mx` as a same-named Manager
    // participant IS a management anchor.
    fixture
        .seed_private_group(
            "g2",
            &[("bot-mx", ParticipantRole::Manager)],
            true,
        )
        .await;
    fixture.control("bot-mx", "alice").await;

    // A manages through the Manager anchor it controls:
    // add a participant.
    let added = fixture
        .app
        .add_participant(AddGroupParticipant {
            caller: human("alice"),
            group_id: "g2".to_string(),
            actor_id: "bot-new".to_string(),
            message_view_scope: None,
        })
        .await;
    assert!(added.is_ok(), "the manager anchor's controller manages: {added:?}");

    // The peer removes the Manager participant. The app's
    // cached copy still elevates `bot-mx`; the SNAPSHOT
    // must not.
    fixture.peer_remove_participant("g2", "bot-mx").await;
    let denied = fixture
        .app
        .add_participant(AddGroupParticipant {
            caller: human("alice"),
            group_id: "g2".to_string(),
            actor_id: "bot-new-2".to_string(),
            message_view_scope: None,
        })
        .await
        .expect_err("the removed anchor elevates nothing");
    assert!(matches!(denied, ApplicationError::Forbidden(_)));
}
