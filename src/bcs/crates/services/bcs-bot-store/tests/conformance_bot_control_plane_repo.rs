#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::HashSet;
use std::sync::Arc;

use bcs_bot_store::{MemoryBotRepo, PersistentBotRepo};
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement, DbValue as Value};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{
    ActorKind, ActorStatus, BotCandidateReadQuery, BotCandidateVisibility, BotCapabilities,
    BotControlPlaneDescriptorPatch, BotControlPlaneOwnedQuery, BotControlPlanePatch,
    BotControlPlaneRepoPort, BotRepoPort,
};

#[tokio::test]
async fn memory_control_plane_supports_both_kinds_candidates_and_patch_timestamps() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = MemoryBotRepo::with_base_dir(temp.path().to_path_buf());
    let env = bcs_config::resolve_env_str();
    repo.register_with_owner_and_token(
        "acting-memory".to_string(),
        BotCapabilities {
            name: Some("Acting Memory".to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        "staff-1",
        "acting-token",
    )
    .await
    .expect("register acting bot");
    repo.register_with_owner_and_token(
        "friend-memory".to_string(),
        BotCapabilities {
            name: Some("Friend Memory".to_string()),
            summary: Some("friend".to_string()),
            visibility: "private".to_string(),
            ..Default::default()
        },
        "staff-2",
        "friend-token",
    )
    .await
    .expect("register friend bot");
    repo.register_with_owner_and_token(
        "default-visibility-memory".to_string(),
        BotCapabilities {
            name: Some("Default Visibility".to_string()),
            ..Default::default()
        },
        "staff-2",
        "default-visibility-token",
    )
    .await
    .expect("register default visibility bot");
    repo.ensure_human_actor("staff-1", "Memory Human")
        .await
        .expect("ensure human");

    bcs_test_support::contract::repo::bot_control_plane_repo_port_contract_tests(
        &repo,
        &env,
        "acting-memory",
    )
    .await;

    let human = repo
        .get_control_plane("human_staff-1", &env)
        .await
        .expect("get human")
        .expect("human exists");
    assert_eq!(human.kind, ActorKind::Human);
    assert_eq!(
        repo.get_control_plane("default-visibility-memory", &env)
            .await
            .expect("get default visibility bot")
            .expect("default visibility bot exists")
            .visibility,
        "protected"
    );

    let (candidates, total) = repo
        .list_control_plane_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting-memory".to_string(),
            env: env.clone(),
            visibility: BotCandidateVisibility::Collaboration,
            friend_ids: HashSet::from(["friend-memory".to_string()]),
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("memory candidates");
    assert_eq!(total, 1);
    assert_eq!(candidates[0].bot.bot_id, "friend-memory");
    assert!(candidates[0].is_friend);

    let before = repo
        .get_control_plane("acting-memory", &env)
        .await
        .expect("get acting")
        .expect("acting exists");
    tokio::time::sleep(std::time::Duration::from_millis(2)).await;
    let after = repo
        .patch_control_plane(
            "acting-memory",
            &env,
            BotControlPlanePatch {
                name: Some("Acting Renamed".to_string()),
                descriptor: Some(BotControlPlaneDescriptorPatch {
                    domains: Some(vec!["memory".to_string()]),
                    ..Default::default()
                }),
                ..Default::default()
            },
        )
        .await
        .expect("patch memory")
        .expect("memory bot exists");
    assert_eq!(after.name, "Acting Renamed");
    assert_eq!(after.descriptor.domains, vec!["memory"]);
    assert_eq!(after.created_at, before.created_at);
    assert!(after.updated_at > before.updated_at);
}

#[tokio::test]
async fn persistent_control_plane_reads_project_audit_fields_and_preserve_batch_order() {
    let (repo, db) = fixture().await;
    seed_bot(
        db.as_ref(),
        "bot-a",
        "Alpha",
        "bot",
        "public",
        "online",
        Some("staff-1"),
        "2026-01-02 03:04:05",
    )
    .await;

    bcs_test_support::contract::repo::bot_control_plane_repo_port_contract_tests(
        &repo, "dev", "bot-a",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "human-1",
        "Human",
        "human",
        "protected",
        "hidden",
        Some("staff-1"),
        "2026-01-01 03:04:05",
    )
    .await;

    let bot = repo
        .get_control_plane("bot-a", "dev")
        .await
        .expect("get bot")
        .expect("bot exists");
    assert_eq!(bot.kind, ActorKind::Bot);
    assert_eq!(bot.status, ActorStatus::Online);
    assert_eq!(bot.descriptor.summary, "summary-bot-a");
    assert_eq!(bot.agent_code.as_deref(), Some("agent-bot-a"));
    assert!(bot.created_at > 0);
    assert_eq!(bot.created_at, bot.updated_at);

    let rows = repo
        .get_control_plane_by_ids(
            &[
                "human-1".to_string(),
                "missing".to_string(),
                "bot-a".to_string(),
                "human-1".to_string(),
            ],
            "dev",
        )
        .await
        .expect("batch query");
    assert_eq!(
        rows.iter()
            .map(|row| row.bot_id.as_str())
            .collect::<Vec<_>>(),
        vec!["human-1", "bot-a"]
    );
}

#[tokio::test]
async fn persistent_control_plane_candidates_apply_purpose_and_stable_ordering() {
    let (repo, db) = fixture().await;
    for (id, name, kind, visibility, created) in [
        ("acting", "Acting", "bot", "private", "2026-01-05 00:00:00"),
        (
            "public-a",
            "Public A",
            "bot",
            "public",
            "2026-01-04 00:00:00",
        ),
        (
            "public-b",
            "Public B",
            "bot",
            "public",
            "2026-01-04 00:00:00",
        ),
        (
            "protected",
            "Protected",
            "bot",
            "protected",
            "2026-01-03 00:00:00",
        ),
        (
            "private-friend",
            "Private Friend",
            "bot",
            "private",
            "2026-01-02 00:00:00",
        ),
        (
            "human-row",
            "Human Row",
            "human",
            "public",
            "2026-01-06 00:00:00",
        ),
    ] {
        seed_bot(
            db.as_ref(),
            id,
            name,
            kind,
            visibility,
            "online",
            Some("staff-1"),
            created,
        )
        .await;
    }
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_friendships (left_bot, right_bot, env) VALUES (?, ?, ?)",
        vec![
            Value::from("acting"),
            Value::from("private-friend"),
            Value::from("dev"),
        ],
    ))
    .await
    .expect("insert friendship");

    let (discovery, total) = repo
        .list_control_plane_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::from(["private-friend".to_string()]),
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("discovery candidates");
    assert_eq!(total, 3);
    assert_eq!(
        discovery
            .iter()
            .map(|row| row.bot.bot_id.as_str())
            .collect::<Vec<_>>(),
        vec!["public-a", "public-b", "protected"]
    );
    assert!(discovery.iter().all(|row| !row.is_friend));

    let (without_friends, total) = repo
        .list_control_plane_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Collaboration,
            friend_ids: HashSet::new(),
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("collaboration candidates without supplied friends");
    assert_eq!(total, 2);
    assert_eq!(
        without_friends
            .iter()
            .map(|row| row.bot.bot_id.as_str())
            .collect::<Vec<_>>(),
        vec!["public-a", "public-b"]
    );

    let (collaboration, total) = repo
        .list_control_plane_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Collaboration,
            friend_ids: HashSet::from(["private-friend".to_string()]),
            name: Some("  ".to_string()),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("collaboration candidates");
    assert_eq!(total, 3);
    assert_eq!(
        collaboration
            .iter()
            .map(|row| (row.bot.bot_id.as_str(), row.is_friend))
            .collect::<Vec<_>>(),
        vec![
            ("public-a", false),
            ("public-b", false),
            ("private-friend", true),
        ]
    );
}

#[tokio::test]
async fn persistent_control_plane_name_filters_treat_sql_wildcards_as_literals() {
    let (repo, db) = fixture().await;
    for (id, name, owner) in [
        ("acting", "Acting", "staff-1"),
        ("literal-percent", "100% Real", "staff-1"),
        ("wildcard-match", "100x Real", "staff-1"),
    ] {
        seed_bot(
            db.as_ref(),
            id,
            name,
            "bot",
            "public",
            "online",
            Some(owner),
            "2026-01-01 00:00:00",
        )
        .await;
    }

    let (candidates, total) = repo
        .list_control_plane_candidates(BotCandidateReadQuery {
            acting_bot_id: "acting".to_string(),
            env: "dev".to_string(),
            visibility: BotCandidateVisibility::Discovery,
            friend_ids: HashSet::new(),
            name: Some("%".to_string()),
            offset: 0,
            limit: 20,
        })
        .await
        .expect("literal candidate filter");
    assert_eq!(total, 1);
    assert_eq!(candidates[0].bot.bot_id, "literal-percent");

    let owned = repo
        .list_control_plane_by_creator(BotControlPlaneOwnedQuery {
            created_by: "staff-1".to_string(),
            env: "dev".to_string(),
            kind: None,
            name: Some("%".to_string()),
            status: None,
        })
        .await
        .expect("literal owned filter");
    assert_eq!(owned.len(), 1);
    assert_eq!(owned[0].bot_id, "literal-percent");
}

#[tokio::test]
async fn persistent_control_plane_owned_filters_and_patch_replace_descriptor_arrays() {
    let (repo, db) = fixture().await;
    seed_bot(
        db.as_ref(),
        "owned",
        "Owned Planner",
        "bot",
        "public",
        "online",
        Some("staff-1"),
        "2026-01-01 00:00:00",
    )
    .await;
    seed_bot(
        db.as_ref(),
        "other",
        "Other Planner",
        "bot",
        "public",
        "online",
        Some("staff-2"),
        "2026-01-02 00:00:00",
    )
    .await;

    let owned = repo
        .list_control_plane_by_creator(BotControlPlaneOwnedQuery {
            created_by: "staff-1".to_string(),
            env: "dev".to_string(),
            kind: Some(ActorKind::Bot),
            name: Some(" planner ".to_string()),
            status: Some(ActorStatus::Online),
        })
        .await
        .expect("owned rows");
    assert_eq!(owned.len(), 1);
    let before = &owned[0];

    tokio::time::sleep(std::time::Duration::from_millis(1_100)).await;
    let updated = repo
        .patch_control_plane(
            "owned",
            "dev",
            BotControlPlanePatch {
                name: Some("Renamed".to_string()),
                visibility: Some("protected".to_string()),
                status: Some(ActorStatus::Hidden),
                descriptor: Some(BotControlPlaneDescriptorPatch {
                    summary: Some("new summary".to_string()),
                    domains: Some(vec![]),
                    skills: None,
                    scopes: Some(vec!["new-scope".to_string()]),
                }),
            },
        )
        .await
        .expect("patch row")
        .expect("patched row");
    assert_eq!(updated.name, "Renamed");
    assert_eq!(updated.visibility, "protected");
    assert_eq!(updated.status, ActorStatus::Hidden);
    assert_eq!(updated.descriptor.summary, "new summary");
    assert!(updated.descriptor.domains.is_empty());
    assert_eq!(updated.descriptor.skills, before.descriptor.skills);
    assert_eq!(updated.descriptor.scopes, vec!["new-scope"]);
    assert_eq!(updated.agent_code, before.agent_code);
    assert_eq!(updated.created_at, before.created_at);
    assert!(updated.updated_at > before.updated_at);

    let credential = db
        .query(DbStatement::with_params(
            "SELECT session_token FROM bcs_bots WHERE bot_uuid = ? AND env = ?",
            vec![Value::from("owned"), Value::from("dev")],
        ))
        .await
        .expect("read credential");
    assert_eq!(
        credential[0].get("session_token").and_then(Value::as_str),
        Some("token-owned")
    );
}

async fn fixture() -> (PersistentBotRepo, Arc<dyn DbPlugin>) {
    let db = sqlite_db().await;
    let repo = PersistentBotRepo::with_plugins_flavor_and_cache_key_prefix(
        Arc::new(InMemoryCachePlugin::new()),
        db.clone(),
        DbSqlFlavor::Sqlite,
        "test:",
    );
    (repo, db)
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bot_uuid TEXT NOT NULL,
            name TEXT NOT NULL,
            bot_info TEXT,
            session_token TEXT,
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            env TEXT,
            visibility TEXT NOT NULL DEFAULT 'public',
            created_by TEXT,
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            status TEXT NOT NULL DEFAULT 'online',
            is_deleted INTEGER NOT NULL DEFAULT 0,
            agent_code TEXT,
            UNIQUE (bot_uuid, env)
        )",
    ))
    .await
    .expect("create bots table");
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_friendships (
            left_bot TEXT NOT NULL,
            right_bot TEXT NOT NULL,
            env TEXT NOT NULL,
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (left_bot, right_bot, env)
        )",
    ))
    .await
    .expect("create friendships table");
    db
}

#[allow(clippy::too_many_arguments)]
async fn seed_bot(
    db: &dyn DbPlugin,
    bot_id: &str,
    name: &str,
    kind: &str,
    visibility: &str,
    status: &str,
    created_by: Option<&str>,
    timestamp: &str,
) {
    let bot_info = serde_json::json!({
        "summary": format!("summary-{bot_id}"),
        "domains": ["planning"],
        "skills": [{"name": "plan", "description": "Make a plan"}],
        "scopes": ["workspace"]
    })
    .to_string();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots
         (gmt_create, gmt_modified, bot_uuid, name, bot_info, session_token,
          registered_at, updated_at, env, visibility, created_by, actor_kind,
          status, is_deleted, agent_code)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        vec![
            Value::from(timestamp),
            Value::from(timestamp),
            Value::from(bot_id),
            Value::from(name),
            Value::from(bot_info),
            Value::from(format!("token-{bot_id}")),
            Value::from(timestamp),
            Value::from(timestamp),
            Value::from("dev"),
            Value::from(visibility),
            created_by.map(Value::from).unwrap_or(Value::Null),
            Value::from(kind),
            Value::from(status),
            Value::from(format!("agent-{bot_id}")),
        ],
    ))
    .await
    .expect("seed bot row");
}
