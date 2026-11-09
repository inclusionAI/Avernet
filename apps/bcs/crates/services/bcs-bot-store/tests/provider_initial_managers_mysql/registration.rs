//! Real MySQL ordinary-registration and A2A transaction boundaries.
use super::*;
use std::sync::Arc;
use bcs_service_api::{BotCapabilities, bot_provider::{BotProviderRecord, BotConnectionMode},
    port::repo::{bot_provider::BotProviderRepoPort, a2a_bot::A2aBotRecord}};

fn record(id: &str) -> BotProviderRecord {
    BotProviderRecord { bot_uuid: id.into(), provider_id: "provider-a".into(),
        provider_bot_ref: id.into(), connection_mode: BotConnectionMode::Gateway,
        webhook_url: None, is_deleted: false }
}
fn capabilities(id: &str) -> BotCapabilities {
    BotCapabilities {name: Some(id.into()), ..Default::default()}
}
fn card(id: &str) -> A2aBotRecord {
    A2aBotRecord { bot_uuid: id.into(), provider_id: "provider-a".into(), provider_bot_ref: id.into(),
        agent_card: serde_json::json!({"name":id}), agent_card_url: None, revision: 1 }
}
async fn scalar(db: &dyn DbPlugin, sql: &str) -> i64 {
    db.query(DbStatement::new(sql)).await.unwrap()[0].get_i64("n").unwrap().unwrap()
}

pub(super) async fn verify(db: Arc<dyn DbPlugin>, env: &str) {
    let providers = bcs_bot_store::DbProviderStore::mysql(db.clone());
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Mysql);
    let authority = bcs_edge_permission_store::DbBotAuthorityStore::mysql(db.clone(), env.into());
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_providers (provider_id,env,name,config,created_by,owners) VALUES ('provider-a',?,'Provider A','{}','alice','[]')",
        vec![env.into()])).await.unwrap();

    // Same identity snapshots succeed through both real registration store paths.
    for (index, users) in [vec!["Carol", "carol"], vec!["Bob"], vec!["Jose", "José"]].into_iter().enumerate() {
        for a2a in [false, true] {
            let bot = format!("registered-{index}-{a2a}");
            let init = init(users.iter().map(|id| (*id).into()).collect(), &format!("init-{bot}"));
            if a2a {
                providers.create_provider_admin_a2a_bot_with_ownership(record(&bot), capabilities(&bot),
                    "alice", &format!("token-{bot}"), init, card(&bot)).await.unwrap();
            } else {
                providers.create_provider_admin_bot_with_ownership(record(&bot), capabilities(&bot),
                    "alice", &format!("token-{bot}"), init).await.unwrap();
            }
            for user in &users {
                assert_eq!(authority.role(&format!("human_{user}"), &bot).await.unwrap(), Some(BotAccessRelation::Manager));
            }
        }
    }

    db.execute(DbStatement::new("ALTER TABLE edge_grants ADD CONSTRAINT fail_registration_manager CHECK (NOT (to_id IN ('ordinary-failure','a2a-failure') AND grant_kind='manager'))")).await.unwrap();
    providers.create_provider_bot(record("ordinary-failure"), capabilities("ordinary-failure"), "alice", "ordinary-token").await.unwrap();
    assert!(repo.initialize_provider_authority("ordinary-failure", init(vec!["Partial".into(), "partial".into()], "ordinary-init")).await.is_err());
    assert_eq!(scalar(db.as_ref(), "SELECT ownership_version AS n FROM bcs_bots WHERE bot_uuid='ordinary-failure'").await, 0);
    assert_eq!(scalar(db.as_ref(), "SELECT COUNT(*) AS n FROM bcs_provider_bot_bindings WHERE bot_uuid='ordinary-failure'").await, 1);
    for table in ["edge_grants", "bot_authority_changes"] {
        let column = if table == "edge_grants" {"to_id"} else {"bot_id"};
        assert_eq!(scalar(db.as_ref(), &format!("SELECT COUNT(*) AS n FROM {table} WHERE {column}='ordinary-failure'")).await, 0);
    }
    assert_eq!(scalar(db.as_ref(), "SELECT COUNT(*) AS n FROM bcs_bots WHERE bot_uuid IN ('human_Partial','human_partial')").await, 0);
    let make_a2a_init = || init(vec!["Atomic".into(), "atomic".into()], "a2a-init");
    assert!(providers.create_provider_admin_a2a_bot_with_ownership(record("a2a-failure"), capabilities("a2a-failure"),
        "alice", "a2a-failure-token", make_a2a_init(), card("a2a-failure")).await.is_err());
    for sql in ["SELECT COUNT(*) AS n FROM bcs_bots WHERE bot_uuid IN ('a2a-failure','human_Atomic','human_atomic')",
        "SELECT COUNT(*) AS n FROM bcs_provider_bot_bindings WHERE bot_uuid='a2a-failure'",
        "SELECT COUNT(*) AS n FROM edge_grants WHERE to_id='a2a-failure'",
        "SELECT COUNT(*) AS n FROM bot_authority_changes WHERE bot_id='a2a-failure'",
        "SELECT COUNT(*) AS n FROM permission_profiles WHERE bot_id='a2a-failure'"] {
        assert_eq!(scalar(db.as_ref(), sql).await, 0);
    }
    // Multi-user ordinary admission now uses the SAME all-or-nothing boundary.
    assert!(providers.create_provider_admin_bot_with_ownership(record("a2a-failure"), capabilities("a2a-failure"),
        "alice", "ordinary-atomic-token", make_a2a_init()).await.is_err());
    assert_eq!(scalar(db.as_ref(), "SELECT COUNT(*) AS n FROM bcs_bots WHERE bot_uuid IN ('a2a-failure','human_Atomic','human_atomic')").await, 0);
    assert_eq!(scalar(db.as_ref(), "SELECT COUNT(*) AS n FROM bcs_provider_bot_bindings WHERE bot_uuid='a2a-failure'").await, 0);
    db.execute(DbStatement::new("ALTER TABLE edge_grants DROP CHECK fail_registration_manager")).await.unwrap();
    repo.initialize_provider_authority("ordinary-failure", init(vec!["Partial".into(), "partial".into()], "ordinary-init")).await.unwrap();
    providers.create_provider_admin_a2a_bot_with_ownership(record("a2a-failure"), capabilities("a2a-failure"),
        "alice", "a2a-failure-token", make_a2a_init(), card("a2a-failure")).await.unwrap();
    assert_eq!(authority.role("human_Atomic", "a2a-failure").await.unwrap(), Some(BotAccessRelation::Manager));
    assert_eq!(authority.role("human_atomic", "a2a-failure").await.unwrap(), Some(BotAccessRelation::Manager));
}

/// Recreate legacy identity collation BEFORE case-distinct fixtures are admitted,
/// then apply the new migration to a retained dataset and verify unchanged rows.
pub(super) async fn retained_upgrade(db: &dyn DbPlugin) {
    db.execute(DbStatement::new("ALTER TABLE bcs_bots MODIFY bot_uuid VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL")).await.unwrap();
    db.execute(DbStatement::new("ALTER TABLE edge_grants MODIFY from_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL, MODIFY to_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL")).await.unwrap();
    // NO PAD legacy databases can contain both rows. Neither may be trimmed
    // or merged by the collation repair, including the identity-reference keys.
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid,name,env,actor_kind) VALUES ('human_retained','First','dev','human'),('human_retained ','Second','dev','human')")).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO edge_grants (env,from_id,to_id,grant_kind,grant_ref_id) VALUES ('dev','human_retained','batch-bot','manager',0),('dev','human_retained ','batch-bot','manager',0)")).await.unwrap();
    let before_bots = scalar(db, "SELECT COUNT(*) AS n FROM bcs_bots").await;
    let before_edges = scalar(db, "SELECT COUNT(*) AS n FROM edge_grants").await;
    for sql in bcs_db_api::split_sql_statements(include_str!("../../../../../migrations/mysql/043_actor_identity_collation.sql")) {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    assert_eq!(scalar(db, "SELECT COUNT(*) AS n FROM bcs_bots").await, before_bots);
    assert_eq!(scalar(db, "SELECT COUNT(*) AS n FROM edge_grants").await, before_edges);
    let rows = db.query(DbStatement::new("SELECT COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND ((TABLE_NAME='bcs_bots' AND COLUMN_NAME='bot_uuid') OR (TABLE_NAME='edge_grants' AND COLUMN_NAME IN ('from_id','to_id')))")).await.unwrap();
    assert_eq!(rows.len(), 3);
    for row in rows { assert_eq!(row.get_string("COLLATION_NAME").unwrap().as_deref(), Some("utf8mb4_0900_bin")); }
}

pub(super) async fn distinct_runtime_profiles_and_memberships(db: Arc<dyn DbPlugin>, env: &str) {
    use bcs_service_api::port::repo::{PermissionProfileRepoPort, GroupRepoPort};
    use bcs_service_api::{Group, Participant, ParticipantRole};
    let profiles = bcs_edge_permission_store::DbPermissionProfileStore::mysql(db.clone());
    let upper = profiles.ensure_default_profile("ProfileBot", env).await.unwrap();
    let lower = profiles.ensure_default_profile("profilebot", env).await.unwrap();
    assert_ne!(upper, lower, "case-distinct Bots must never share runtime permissions");
    assert_eq!(profiles.get_active_default("ProfileBot", env).await.unwrap().permission_profile_id, upper);
    assert_eq!(profiles.get_active_default("profilebot", env).await.unwrap().permission_profile_id, lower);

    let groups = bcs_group_store::MySqlGroupStore::new(db.clone(), env.into());
    groups.upsert(Group::new("identity-group", "bot-driver", vec![])).await.unwrap();
    for user in ["human_Carol", "human_carol", "human_Jose", "human_José"] {
        groups.add_participant("identity-group", Participant::human(user, ParticipantRole::Observer)).await.unwrap();
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_session_participants (session_id,group_id,bot_uuid,role,env) VALUES ('identity-session','identity-group',?,'observer',?)",
            vec![user.into(), env.into()])).await.unwrap();
    }
    assert_eq!(groups.get("identity-group").await.unwrap().participants.len(), 4);
    groups.remove_participant("identity-group", "human_Carol").await.unwrap();
    let group = groups.get("identity-group").await.unwrap();
    assert_eq!(group.participants.len(), 3);
    assert!(group.participants.iter().any(|p| p.bot_uuid == "human_carol"));
    db.execute(DbStatement::with_params(
        "DELETE FROM bcs_session_participants WHERE session_id='identity-session' AND bot_uuid=? AND env=?",
        vec!["human_Carol".into(), env.into()])).await.unwrap();
    assert_eq!(scalar(db.as_ref(), "SELECT COUNT(*) AS n FROM bcs_session_participants WHERE session_id='identity-session'").await, 3);
}
