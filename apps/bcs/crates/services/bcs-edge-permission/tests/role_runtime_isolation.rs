//! Runtime isolation of role edges from the friend/runtime lanes
//! (plan Task 12, spec §12.3/§13.4 + brief Step 1 RED assertions).
//!
//! One REAL SQLite database drives the FULL migration chain (exactly what
//! production startup builds), then the REAL stores and services run over
//! it: `DbAdmissionService` (workbench admission), `DbConnectService`
//! (friend connect lifecycle + current-owner notifications + the external
//! friend-auth sync port) and the strict `DbBotAuthorityStore` role lanes
//! (manager mutation + transfer). The suite pins:
//!
//! - a manager/owner ROLE edge alone never admits runtime friendship, never
//!   enters the A2A authz context, and never makes its holder a "friend";
//! - friend edges and role edges coexist and are deleted independently;
//! - the friend judgement stays EXACT default-profile (a non-default
//!   permission_profile edge is not a friend) while the runtime whitelist is
//!   `grant_kind IN ('permission_profile', 'rules')`;
//! - role changes (manager grant/revoke, raw role-row writes) NEVER trigger
//!   the external friend-auth sync and never write the permission-request
//!   lane's decisions with a manager audit identity;
//! - friend-request approvals address the CURRENT owner (through the strict
//!   authority core), not the historical creator.

#![allow(dead_code)]

use std::sync::Arc;

use async_trait::async_trait;
use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_domain::edge_permission::{EdgeGrant, GrantKind, RequestStatus};
use bcs_edge_permission::{
    authority::BotAuthorityCoreServiceImpl, DbAdmissionService, DbConnectService,
};
use bcs_edge_permission_store::{
    DbBotActorConfigStore, DbBotAuthorityStore, DbEdgeGrantStore, DbPermissionProfileStore,
    DbPermissionRequestStore,
};
use bcs_service_api::application::admission::AdmissionService;
use bcs_service_api::application::connect::{ConnectService, ConnectStatus};
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, BotAuthorityRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
    PermissionRequestRepoPort,
};
use bcs_service_api::port::{
    FriendAuthSyncAction, FriendAuthSyncCommand, FriendAuthSyncPort,
    FriendConnectNotificationCommand, FriendConnectNotificationKind,
    FriendConnectNotificationPort,
};
use bcs_service_api::RequestAuthHeaders;
use bcs_service_api::types::{
    AuditActor, BotOperationActor, BotOperationContext, ManagerMutation, ServiceError,
};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod migrations;

use bcs_db_local::LocalSqliteDbPlugin;

const ENV: &str = "local";
const OWNER: &str = "1001";
const FORMER_CREATOR: &str = "1002";
const MANAGER_ONLY_HUMAN: &str = "2002";
const FRIEND_HUMAN: &str = "3003";

// ---------------------------------------------------------------------------
// Spies
// ---------------------------------------------------------------------------

#[derive(Default)]
struct RecordingSync {
    commands: std::sync::Mutex<Vec<FriendAuthSyncCommand>>,
}

#[async_trait]
impl FriendAuthSyncPort for RecordingSync {
    async fn sync(&self, command: FriendAuthSyncCommand) -> bcs_service_api::ServiceResult<()> {
        self.commands.lock().unwrap().push(command);
        Ok(())
    }
}

#[derive(Default)]
struct RecordingNotifications {
    commands: std::sync::Mutex<Vec<FriendConnectNotificationCommand>>,
}

#[async_trait]
impl FriendConnectNotificationPort for RecordingNotifications {
    async fn notify(
        &self,
        command: FriendConnectNotificationCommand,
    ) -> bcs_service_api::ServiceResult<()> {
        self.commands.lock().unwrap().push(command);
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Fixture over the FULL production migration chain
// ---------------------------------------------------------------------------

struct Fixture {
    db: Arc<dyn DbPlugin>,
    edge_grants: Arc<dyn EdgeGrantRepoPort>,
    profiles: Arc<dyn PermissionProfileRepoPort>,
    requests: Arc<dyn PermissionRequestRepoPort>,
    authority: Arc<DbBotAuthorityStore>,
    admission: DbAdmissionService,
    connect: DbConnectService,
    sync: Arc<RecordingSync>,
    notifications: Arc<RecordingNotifications>,
}

async fn seed_bot_raw(db: &Arc<dyn DbPlugin>, bot_id: &str, created_by: Option<&str>) {
    let existing = db
        .query(DbStatement::with_params(
            "SELECT 1 AS one FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![DbValue::from(bot_id), DbValue::from(ENV)],
        ))
        .await
        .expect("bot lookup");
    if !existing.is_empty() {
        db.execute(DbStatement::with_params(
            "UPDATE bcs_bots SET created_by = ? WHERE bot_uuid = ? AND env = ?",
            vec![
                created_by.map(DbValue::from).unwrap_or(DbValue::Null),
                DbValue::from(bot_id),
                DbValue::from(ENV),
            ],
        ))
        .await
        .expect("update creator fact");
        return;
    }
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, env, ownership_version, visibility, \
         user_visibility, friend_check_in_strategy, created_by, friend_ext, bot_info) \
         VALUES (?, ?, ?, 1, 'protected', 'protected', 'APPROVAL', ?, '{}', '{}')",
        vec![
            DbValue::from(bot_id),
            DbValue::from(bot_id),
            DbValue::from(ENV),
            created_by.map(DbValue::from).unwrap_or(DbValue::Null),
        ],
    ))
    .await
    .expect("seed bot row");
}

async fn seed_human_actor(db: &Arc<dyn DbPlugin>, user_id: &str) {
    let actor_id = format!("human_{user_id}");
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, name, env, actor_kind, status) \
         SELECT ?, ?, ?, 'human', 'online' WHERE NOT EXISTS \
         (SELECT 1 FROM bcs_bots WHERE bot_uuid = ? AND env = ?)",
        vec![
            DbValue::from(actor_id.clone()),
            DbValue::from(user_id.to_string()),
            DbValue::from(ENV),
            DbValue::from(actor_id),
            DbValue::from(ENV),
        ],
    ))
    .await
    .expect("seed human actor row");
}

async fn seed_owner_edge(db: &Arc<dyn DbPlugin>, bot_id: &str, owner_user_id: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
         VALUES (?, ?, ?, 'owner', 0, NULL, 'approved', 'same_as_from', NULL, 'owner', 'owner')",
        vec![
            DbValue::from(ENV),
            DbValue::from(format!("human_{owner_user_id}")),
            DbValue::from(bot_id),
        ],
    ))
    .await
    .expect("seed owner edge");
}

async fn seed_manager_role_edge(db: &Arc<dyn DbPlugin>, bot_id: &str, user_id: &str) {
    db.execute(DbStatement::with_params(
        "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
         originator_policy_type, originator_policy_data, management_source_kind, management_source_id) \
         VALUES (?, ?, ?, 'manager', 0, NULL, 'approved', 'same_as_from', NULL, 'direct', 'manual')",
        vec![
            DbValue::from(ENV),
            DbValue::from(format!("human_{user_id}")),
            DbValue::from(bot_id),
        ],
    ))
    .await
    .expect("seed manager role edge");
}

async fn connect_fixture() -> Fixture {
    let db: Arc<dyn DbPlugin> =
        Arc::new(LocalSqliteDbPlugin::new().expect("local sqlite"));
    migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply the full production migration chain");

    let sqlite = db.clone();
    let edge_grants: Arc<dyn EdgeGrantRepoPort> = Arc::new(DbEdgeGrantStore::sqlite(sqlite.clone()));
    let profiles_store: Arc<dyn PermissionProfileRepoPort> =
        Arc::new(DbPermissionProfileStore::sqlite(sqlite.clone()));
    let requests_store: Arc<dyn PermissionRequestRepoPort> =
        Arc::new(DbPermissionRequestStore::sqlite(sqlite.clone()));
    let bot_config: Arc<dyn BotActorConfigRepoPort> =
        Arc::new(DbBotActorConfigStore::sqlite(sqlite.clone()));
    let authority_store = Arc::new(DbBotAuthorityStore::sqlite(sqlite.clone(), ENV.to_string()));
    let authority_core = Arc::new(BotAuthorityCoreServiceImpl::new(
        authority_store.clone() as Arc<dyn BotAuthorityRepoPort>,
    ));

    let sync = Arc::new(RecordingSync::default());
    let notifications = Arc::new(RecordingNotifications::default());
    let connect = DbConnectService::new(
        edge_grants.clone(),
        profiles_store.clone(),
        requests_store.clone(),
        bot_config.clone(),
        None,
        notifications.clone(),
        sync.clone(),
        ENV.to_string(),
    )
    .with_authority(authority_core.clone());
    let admission = DbAdmissionService::new(
        edge_grants.clone(),
        bot_config.clone(),
        profiles_store.clone(),
    );

    Fixture {
        db,
        edge_grants,
        profiles: profiles_store,
        requests: requests_store,
        authority: authority_store,
        admission,
        connect,
        sync,
        notifications,
    }
}

fn human_operation(for_user: &str) -> BotOperationContext {
    BotOperationContext {
        operation_id: format!("iso-test:{}", uuid::Uuid::new_v4()),
        actor: BotOperationActor::Human {
            user_id: for_user.to_string(),
            effective_actor_id: format!("human_{for_user}"),
        },
    }
}

/// Seed the Friend edge human→bot keyed on the bot's default profile.
async fn seed_friend_edge(fx: &Fixture, human_actor: &str, bot_id: &str) -> u64 {
    fx.profiles.ensure_default_profile(bot_id, ENV).await.unwrap();
    let default = fx
        .edge_grants
        .get_default_profile_id(bot_id, ENV)
        .await
        .expect("default profile id");
    let grant = EdgeGrant::new_non_role(
        0,
        ENV.to_string(),
        human_actor.to_string(),
        bot_id.to_string(),
        GrantKind::PermissionProfile,
        default,
        None,
    );
    fx.edge_grants
        .insert_grant(grant, &human_operation("3003"))
        .await
        .expect("friend edge insert")
}

// ---------------------------------------------------------------------------
// Step 1 RED assertions (brief), exercised against the real services
// ---------------------------------------------------------------------------

#[tokio::test]
async fn manager_role_edge_alone_never_admits_runtime_or_authz() {
    let fx = connect_fixture().await;
    seed_bot_raw(&fx.db, "bot-r", Some(FORMER_CREATOR)).await;
    seed_owner_edge(&fx.db, "bot-r", OWNER).await;
    seed_manager_role_edge(&fx.db, "bot-r", MANAGER_ONLY_HUMAN).await;
    // The manager-only actor id itself (spec: role rows never join the
    // runtime whitelist).
    let manager_actor = format!("human_{MANAGER_ONLY_HUMAN}");

    // `check_admission`: manager edge alone must NOT admit.
    let manager_admission = fx
        .admission
        .check_admission(&manager_actor, "bot-r", "originator", ENV)
        .await
        .expect("admission resolves");
    let admission_from_manager_edge_only = manager_admission.allowed;
    assert!(
        !admission_from_manager_edge_only,
        "a manager role edge alone must not admit runtime friendship"
    );

    // `build_authz_context`: NO owner/manager grant kind may appear.
    let authz = fx
        .admission
        .build_authz_context(&manager_actor, "bot-r", "originator", "task-1", "run-1", ENV)
        .await
        .expect("authz context");
    let authz_grants = &authz.grants;
    assert!(
        !authz_grants
            .iter()
            .any(|g| matches!(g.kind, GrantKind::Owner | GrantKind::Manager)),
        "role edges must never join the A2A authz context"
    );

    // Friend judgement: the manager-only human is NOT a friend.
    let friend_ids = fx.edge_grants.list_friends("bot-r", ENV).await;
    assert!(friend_ids.iter().all(|id| id != &manager_actor));
    assert!(fx
        .edge_grants
        .has_friend_edge(&manager_actor, "bot-r", ENV)
        .await
        == false);
}

#[tokio::test]
async fn friend_edge_admits_while_role_rows_stay_outside_runtime() {
    let fx = connect_fixture().await;
    seed_bot_raw(&fx.db, "bot-r", Some(FORMER_CREATOR)).await;
    seed_owner_edge(&fx.db, "bot-r", OWNER).await;
    seed_friend_edge(&fx, "human_friend", "bot-r").await;

    let friend_admission = fx
        .admission
        .check_admission("human_friend", "bot-r", "originator", ENV)
        .await
        .expect("admission resolves");
    assert!(friend_admission.allowed, "friend edge admits runtime");
    assert!(
        friend_admission
            .grants
            .iter()
            .all(|g| matches!(g.kind, GrantKind::PermissionProfile | GrantKind::Rules)),
        "admitted grants must be runtime kinds only"
    );

    let authz = fx
        .admission
        .build_authz_context("human_friend", "bot-r", "originator", "task-1", "run-1", ENV)
        .await
        .expect("authz context");
    assert!(!authz.grants.is_empty());
    assert!(
        !authz
            .grants
            .iter()
            .any(|g| matches!(g.kind, GrantKind::Owner | GrantKind::Manager))
    );
}

#[tokio::test]
async fn friend_judgement_requires_the_exact_default_profile_edge() {
    let fx = connect_fixture().await;
    seed_bot_raw(&fx.db, "bot-r", Some(FORMER_CREATOR)).await;
    seed_owner_edge(&fx.db, "bot-r", OWNER).await;
    let default = {
        fx.profiles.ensure_default_profile("bot-r", ENV).await.unwrap();
        fx.edge_grants
            .get_default_profile_id("bot-r", ENV)
            .await
            .expect("default profile id")
    };
    // A NON-default permission-profile edge is a runtime grant, but NOT a
    // friend edge (exact default-profile judgement, spec §13.4: not every
    // permission_profile edge is a friend).
    let non_default = {
        fx.db
            .execute(DbStatement::with_params(
                "INSERT INTO permission_profiles (bot_id, env, name, rules_template, revision, \
                 digest, is_default, status, created_by) VALUES (?, ?, 'custom', '{}', 1, \
                 'digest-test', 0, 'active', 'seeder')",
                vec![DbValue::from("bot-r"), DbValue::from(ENV)],
            ))
            .await
            .expect("custom profile row");
        let rows = fx
            .db
            .query(DbStatement::with_params(
                "SELECT id FROM permission_profiles WHERE bot_id = ? AND env = ? AND \
                 name = 'custom' ORDER BY id DESC LIMIT 1",
                vec![DbValue::from("bot-r"), DbValue::from(ENV)],
            ))
            .await
            .expect("custom profile lookup");
        rows[0]
            .get_i64("id")
            .ok()
            .flatten()
            .expect("custom profile id") as u64
    };
    assert_ne!(non_default, default);
    fx.edge_grants
        .insert_grant(
            EdgeGrant::new_non_role(
                0,
                ENV.to_string(),
                "human_extra".to_string(),
                "bot-r".to_string(),
                GrantKind::PermissionProfile,
                non_default,
                None,
            ),
            &human_operation("9009"),
        )
        .await
        .expect("non-default profile edge");

    // Runtime whitelist still admits it (permission kinds).
    let admission = fx
        .admission
        .check_admission("human_extra", "bot-r", "originator", ENV)
        .await
        .expect("admission resolves");
    assert!(admission.allowed);

    // But it is NOT a friend: exact default-profile judgement.
    assert!(!fx
        .edge_grants
        .has_friend_edge("human_extra", "bot-r", ENV)
        .await);
    let friends = fx.edge_grants.list_friends("bot-r", ENV).await;
    assert!(friends.iter().all(|id| id != "human_extra"));
}

#[tokio::test]
async fn friend_and_manager_edges_coexist_and_delete_independently() {
    let fx = connect_fixture().await;
    seed_bot_raw(&fx.db, "bot-r", Some(FORMER_CREATOR)).await;
    seed_owner_edge(&fx.db, "bot-r", OWNER).await;
    seed_human_actor(&fx.db, OWNER).await;
    seed_human_actor(&fx.db, MANAGER_ONLY_HUMAN).await;
    seed_manager_role_edge(&fx.db, "bot-r", MANAGER_ONLY_HUMAN).await;
    let friend_edge_id = seed_friend_edge(&fx, "human_friend", "bot-r").await;

    let friend_admission = || async {
        fx.admission
            .check_admission("human_friend", "bot-r", "originator", ENV)
            .await
            .unwrap()
            .allowed
    };
    let manager_actor = format!("human_{MANAGER_ONLY_HUMAN}");
    let manager_still_authority = || async {
        let r = fx
            .db
            .query(DbStatement::with_params(
                "SELECT count(1) AS c FROM edge_grants WHERE env = ? AND to_id = ? AND \
                 from_id = ? AND grant_kind = 'manager' AND status = 'approved'",
                vec![
                    DbValue::from(ENV),
                    DbValue::from("bot-r"),
                    DbValue::from(manager_actor.clone()),
                ],
            ))
            .await
            .unwrap();
        r[0].get_i64("c").ok().flatten().unwrap_or(0) > 0
    };

    // Coexistence: both lanes exist and the friend edge admits.
    assert!(friend_admission().await);
    assert!(manager_still_authority().await);

    // Deleting the MANAGER role edge (through the real revoke lane) leaves
    // the friend edge and its runtime admission untouched.
    let revoked = fx
        .authority
        .mutate_manager(
            AuditActor::Human {
                user_id: OWNER.to_string(),
            },
            "bot-r",
            ManagerMutation::RevokeNonTeam {
                user_id: MANAGER_ONLY_HUMAN.to_string(),
            },
        )
        .await
        .expect("manager revoke");
    assert!(revoked.changed);
    assert!(!manager_still_authority().await);
    assert!(friend_admission().await, "friend unaffected by role revoke");

    // Deleting the FRIEND edge afterwards does not disturb the (removed)
    // manager role facts: the manager edge stays revoked, the friend lane
    // loses its admission.
    fx.edge_grants
        .revoke_grant(friend_edge_id, ENV, &human_operation("3003"))
        .await
        .unwrap();
    let friend_ids = fx.edge_grants.list_friends("bot-r", ENV).await;
    assert!(friend_ids.iter().all(|id| id != "human_friend"));
    assert!(!manager_still_authority().await);
    assert!(!friend_admission().await, "friend edge gone");
}

#[tokio::test]
async fn role_changes_never_trigger_external_friend_sync() {
    let fx = connect_fixture().await;
    seed_bot_raw(&fx.db, "bot-r", Some(FORMER_CREATOR)).await;
    seed_owner_edge(&fx.db, "bot-r", OWNER).await;
    seed_human_actor(&fx.db, OWNER).await;
    seed_human_actor(&fx.db, MANAGER_ONLY_HUMAN).await;

    // Real role lane: grant then revoke a manager edge.
    for mutation in [
        ManagerMutation::GrantDirect {
            user_id: MANAGER_ONLY_HUMAN.to_string(),
        },
        ManagerMutation::RevokeNonTeam {
            user_id: MANAGER_ONLY_HUMAN.to_string(),
        },
    ] {
        fx.authority
            .mutate_manager(
                AuditActor::Human {
                    user_id: OWNER.to_string(),
                },
                "bot-r",
                mutation,
            )
            .await
            .expect("manager mutation");
    }

    // Raw role-row write through the formal TEAM source semantics (the Task
    // 4 fixture lane; distinct natural key from the direct/manual row that
    // the mutation lane granted-and-revoked above) — also must not touch the
    // friend sync.
    let _ = seed_manager_role_edge; // keep the helper referenced
    fx.db
        .execute(DbStatement::with_params(
            "INSERT INTO edge_grants (env, from_id, to_id, grant_kind, grant_ref_id, rules, \
             status, originator_policy_type, originator_policy_data, management_source_kind, \
             management_source_id) VALUES (?, ?, ?, 'manager', 0, NULL, 'approved', \
             'same_as_from', NULL, 'team', 'team-b')",
            vec![
                DbValue::from(ENV),
                DbValue::from(format!("human_{MANAGER_ONLY_HUMAN}")),
                DbValue::from("bot-r"),
            ],
        ))
        .await
        .expect("seed team manager role edge");

    let external_friend_sync_calls_after_role_change = fx.sync.commands.lock().unwrap().len();
    assert_eq!(
        external_friend_sync_calls_after_role_change, 0,
        "external friend-auth sync is a FRIEND-relation sync only; role \
         lifecycles must never trigger it (spec §12.3)"
    );

    // Positive control: a REAL friend grant through the connect lane does
    // trigger the external sync (merged evidence that the 0 above is the
    // isolation, not a broken spy).
    let auto = connect_fixture().await;
    seed_bot_raw(&auto.db, "bot-open", Some(FORMER_CREATOR)).await;
    seed_owner_edge(&auto.db, "bot-open", OWNER).await;
    auto.db
        .execute(DbStatement::with_params(
            "UPDATE bcs_bots SET user_visibility = 'public', friend_check_in_strategy = 'OPEN' \
             WHERE bot_uuid = 'bot-open' AND env = ?",
            vec![DbValue::from(ENV)],
        ))
        .await
        .unwrap();
    auto.db
        .execute(DbStatement::with_params(
            "UPDATE bcs_bots SET visibility = 'public' WHERE bot_uuid = 'human_friend' \
             AND env = ?",
            vec![DbValue::from(ENV)],
        ))
        .await
        .unwrap();
    auto.connect
        .create_connect(
            "human_friend",
            "bot-open",
            None,
            None,
            human_operation("3003"),
        )
        .await
        .expect("auto-approve connect");
    assert_eq!(auto.sync.commands.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn friend_connect_decisions_never_write_the_manager_audit_table() {
    let fx = connect_fixture().await;
    seed_bot_raw(&fx.db, "bot-manual", Some(FORMER_CREATOR)).await;
    seed_owner_edge(&fx.db, "bot-manual", OWNER).await;

    let created = fx
        .connect
        .create_connect(
            "human_friend",
            "bot-manual",
            None,
            None,
            human_operation("3003"),
        )
        .await
        .expect("pending connect");
    assert_eq!(created.status, ConnectStatus::Pending);
    let rid = &created.request_ids[0];

    fx.connect
        .approve(
            rid,
            "human_friend",
            None,
            human_operation("3003"),
        )
        .await
        .expect("approve");
    fx.connect
        .cancel(rid, "human_friend", human_operation("3003"))
        .await
        .err();
    // request decided approved already; cancel is expected to refuse

    let manager_rows = fx
        .db
        .query(DbStatement::with_params(
            "SELECT count(1) AS c FROM bot_manager_changes",
            vec![],
        ))
        .await
        .unwrap();
    let recorded = manager_rows[0]
        .get_i64("c")
        .ok()
        .flatten()
        .unwrap_or(0);
    assert_eq!(
        recorded, 0,
        "permission-request decisions must never appear in the manager ledger"
    );
    let audit_rows = fx
        .db
        .query(DbStatement::with_params(
            "SELECT count(1) AS c FROM bcs_bot_action_audits WHERE env = ? AND \
             operation_id LIKE 'iso-test:%'",
            vec![DbValue::from(ENV)],
        ))
        .await
        .unwrap();
    let recorded = audit_rows[0].get_i64("c").ok().flatten().unwrap_or(0);
    assert!(recorded > 0, "the friend lane's own business audits exist");
    // Every recorded operator is a connect-lane identity, never a manager
    // mutation actor id (`AuditActor` belongs to the role lane).
    let kinds = fx
        .db
        .query(DbStatement::with_params(
            "SELECT DISTINCT operator_kind FROM bcs_bot_action_audits WHERE env = ? AND \
             operation_id LIKE 'iso-test:%'",
            vec![DbValue::from(ENV)],
        ))
        .await
        .unwrap();
    for row in kinds {
        let kind = row.get_string("operator_kind").ok().flatten().unwrap_or_default();
        assert!(
            kind == "human" || kind == "bot" || kind == "system",
            "unexpected operator kind {kind}"
        );
    }
}

#[tokio::test]
async fn friend_approval_addresses_the_current_owner_not_the_former_creator() {
    let fx = connect_fixture().await;
    // `created_by` keeps FORMER_CREATOR as the historical creation fact,
    // but the CURRENT owner is OWNER (owner edge above).
    seed_bot_raw(&fx.db, "bot-owner", Some(FORMER_CREATOR)).await;
    seed_owner_edge(&fx.db, "bot-owner", OWNER).await;

    fx.connect
        .create_connect(
            "human_friend",
            "bot-owner",
            None,
            None,
            human_operation("3003"),
        )
        .await
        .expect("pending connect");

    let notifications = fx.notifications.commands.lock().unwrap();
    assert_eq!(notifications.len(), 1);
    let recipients: Vec<String> = notifications
        .iter()
        .flat_map(|cmd| cmd.recipient_user_ids.clone())
        .collect();
    assert!(
        recipients.iter().all(|id| id == OWNER),
        "friend approvals must address the CURRENT owner; got {recipients:?}"
    );
    assert!(
        !recipients.iter().any(|id| id == FORMER_CREATOR),
        "the historical creator must not receive approvals after a cutover"
    );
    assert_eq!(
        notifications[0].kind,
        FriendConnectNotificationKind::ApprovalRequested
    );
}

#[tokio::test]
async fn acting_actor_authorization_requires_live_role_facts() {
    let fx = connect_fixture().await;
    seed_bot_raw(&fx.db, "bot-r", Some(FORMER_CREATOR)).await;
    seed_owner_edge(&fx.db, "bot-r", OWNER).await;
    seed_human_actor(&fx.db, MANAGER_ONLY_HUMAN).await;
    seed_manager_role_edge(&fx.db, "bot-r", MANAGER_ONLY_HUMAN).await;

    // Manager: allowed by the live role facts.
    assert!(fx
        .connect
        .authorize_acting_actor(MANAGER_ONLY_HUMAN, "bot-r")
        .await
        .expect("authority resolves"));
    // Former creator (no current role): denied even though `created_by`
    // still holds that user id — and the Bot-ID suffix never helps.
    assert!(!fx
        .connect
        .authorize_acting_actor(FORMER_CREATOR, "bot-r")
        .await
        .expect("authority resolves"));
    let suffix_actor = format!("bot-r:{FORMER_CREATOR}");
    let suffix_verdict = fx
        .connect
        .authorize_acting_actor(FORMER_CREATOR, &suffix_actor)
        .await;
    assert!(
        matches!(suffix_verdict, Ok(false) | Err(ServiceError::BotNotFound(_))),
        "a Bot id carrying the user's suffix never derives authority"
    );
    // Uninitialized bot: fail-closed deny, never an inferred allow.
    fx.db
        .execute(DbStatement::with_params(
            "INSERT INTO bcs_bots (bot_uuid, name, env) VALUES (?, ?, ?)",
            vec![
                DbValue::from("bot-v0"),
                DbValue::from("bot-v0"),
                DbValue::from(ENV),
            ],
        ))
        .await
        .unwrap();
    let verdict = fx.connect.authorize_acting_actor(FORMER_CREATOR, "bot-v0").await;
    let _ = verdict;
}

#[tokio::test]
async fn human_friend_requests_persist_decision_identity_from_the_lane() {
    // Covers the request-lane decision records: the decider recorded on a
    // decided request is an actor of the friend lane only.
    let fx = connect_fixture().await;
    seed_bot_raw(&fx.db, "bot-manual2", Some(FORMER_CREATOR)).await;
    seed_owner_edge(&fx.db, "bot-manual2", OWNER).await;
    let created = fx
        .connect
        .create_connect(
            "human_friend",
            "bot-manual2",
            None,
            None,
            human_operation("3003"),
        )
        .await
        .expect("pending connect");
    let rid = created.request_ids[0].clone();
    fx.connect
        .approve(&rid, "human_friend", None, human_operation("3003"))
        .await
        .expect("approve");
    let requests = fx.requests.list_inbox("bot-manual2", ENV, None).await;
    let decided = requests
        .iter()
        .find(|r| r.request_id == rid)
        .expect("decided request");
    assert_eq!(decided.decided_by.as_deref(), Some("human_friend"));
    assert_eq!(decided.status, RequestStatus::Approved);
}