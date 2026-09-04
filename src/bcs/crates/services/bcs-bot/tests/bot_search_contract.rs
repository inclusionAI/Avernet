//! Contract tests for `BotQueryService::search_bots` (the `/bots/search`
//! data source), focused on the TC (TeamClaw backend) bot filter.

use std::{collections::HashMap, sync::Arc};

use async_trait::async_trait;
use bcs_bot::{Bot, BotControlPlaneCore, BotCore};
use bcs_config::resolve_env_str;
use bcs_domain::edge_permission::EdgeGrant;
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore, PersistentBotRepo};
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement, DbValue as Value};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{BotCapabilities, BotControlPlaneCoreService, BotQueryService, BotRegistryCoreService, BotRepoPort, BotSearchFriendshipFilter, BotUseCaseError, SearchBotsCommand, ServiceResult};
use bcs_service_api::FriendCoreService;
use bcs_service_api::port::repo::EdgeGrantRepoPort;
use tempfile::TempDir;

fn capabilities(name: &str, visibility: &str) -> BotCapabilities {
    BotCapabilities {
        name: Some(name.to_string()),
        summary: Some("test bot".to_string()),
        visibility: visibility.to_string(),
        ..Default::default()
    }
}

async fn sqlite_db() -> Arc<dyn DbPlugin> {
    let db = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    db.execute(DbStatement::new(
        "CREATE TABLE bcs_bots (
            bot_uuid TEXT NOT NULL,
            name TEXT,
            bot_info TEXT,
            session_token TEXT,
            created_by TEXT,
            visibility TEXT,
            status TEXT NOT NULL DEFAULT 'online',
            actor_kind TEXT NOT NULL DEFAULT 'bot',
            is_deleted INTEGER NOT NULL DEFAULT 0,
            agent_code TEXT DEFAULT NULL,
            task_claim_mode INTEGER NOT NULL DEFAULT 0,
            task_dream_mode INTEGER NOT NULL DEFAULT 0,
            user_visibility TEXT NOT NULL DEFAULT 'protected',
            friend_ext JSON,
            friend_check_in_strategy TEXT NOT NULL DEFAULT 'APPROVAL',
            env TEXT NOT NULL,
            gmt_create TEXT DEFAULT CURRENT_TIMESTAMP,
            gmt_modified TEXT DEFAULT CURRENT_TIMESTAMP,
            registered_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (bot_uuid, env)
        )",
    ))
    .await
    .expect("create bcs_bots table");
    db
}

async fn build_bot() -> (Bot, Arc<BotCore>, TempDir) {
    let data_dir = tempfile::tempdir().expect("temp data dir");
    let repo = Arc::new(bcs_bot_store::MemoryBotRepo::with_base_dir(data_dir.path().to_path_buf()));
    let core = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane = Arc::new(BotControlPlaneCore::new(repo.clone(), providers.clone(), providers));
    let bot = Bot::new(core.clone() as Arc<dyn BotRegistryCoreService>)
        .with_control_plane(control_plane as Arc<dyn BotControlPlaneCoreService>);
    (bot, core, data_dir)
}

async fn insert_bot_row(
    db: &Arc<dyn DbPlugin>,
    bot_uuid: &str,
    name: &str,
    visibility: &str,
    created_by: Option<&str>,
) {
    let bot_info = serde_json::json!({
        "summary": "test bot",
        "domains": [],
        "skills": [],
        "scopes": [],
        "binding_channels": null,
        "hidden": false,
        "visibility": visibility,
        "agent_code": null,
        "agent_token": null
    })
    .to_string();
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (
            bot_uuid, name, bot_info, session_token, created_by, visibility,
            status, actor_kind, is_deleted, agent_code, env, registered_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'online', 'bot', 0, NULL, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        vec![
            Value::from(bot_uuid),
            Value::from(name),
            Value::from(bot_info),
            Value::from(format!("{bot_uuid}-token")),
            Value::from(created_by.map(str::to_string)),
            Value::from(visibility),
            Value::from(resolve_env_str()),
        ],
    ))
    .await
    .expect("insert bot row");
}


#[derive(Default)]
struct RecordingEdgeGrantRepo {
    friends: HashMap<String, Vec<String>>,
    calls: tokio::sync::Mutex<Vec<(String, String)>>,
}

#[async_trait]
impl EdgeGrantRepoPort for RecordingEdgeGrantRepo {
    async fn list_active_grants(&self, _: &str, _: &str, _: &str) -> Vec<EdgeGrant> {
        Vec::new()
    }

    async fn is_authorized(&self, _: &str, _: &str, _: &str) -> bool {
        false
    }

    async fn has_friend_edge(&self, _: &str, _: &str, _: &str) -> bool {
        false
    }

    async fn list_friends(&self, actor: &str, env: &str) -> Vec<String> {
        self.calls
            .lock()
            .await
            .push((actor.to_string(), env.to_string()));
        self.friends.get(actor).cloned().unwrap_or_default()
    }

    async fn insert_grant(&self, _: EdgeGrant) -> ServiceResult<u64> {
        Ok(1)
    }

    async fn revoke_grant(&self, _: u64, _: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn get_default_profile_id(&self, _: &str, _: &str) -> Option<u64> {
        None
    }
}

#[derive(Default)]
struct RecordingFriendCoreService {
    friends: Vec<String>,
    calls: tokio::sync::Mutex<Vec<String>>,
}

#[async_trait]
impl FriendCoreService for RecordingFriendCoreService {
    async fn list_friends(&self, bot_id: &str) -> Vec<String> {
        self.calls.lock().await.push(bot_id.to_string());
        self.friends.clone()
    }

    async fn are_friends(&self, _: &str, _: &str) -> bool {
        false
    }

    async fn are_all_friends(&self, _: &str, _: &[String]) -> ServiceResult<()> {
        Ok(())
    }

    async fn add_friendship(&self, _: &str, _: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_all_friendships(&self, _: &str) -> ServiceResult<usize> {
        Ok(0)
    }
}

async fn seed_search_bot<R>(
    repo: &Arc<R>,
    bot_id: &str,
    name: &str,
    visibility: &str,
    created_by: Option<&str>,
    active: bool,
) where
    R: BotRegistryCoreService + ?Sized,
{
    let caps = capabilities(name, visibility);
    match created_by {
        Some(owner) => repo
            .register_with_owner_and_token(
                bot_id.to_string(),
                caps,
                owner,
                &format!("{bot_id}-token"),
            )
            .await
            .expect("register bot with owner"),
        None => repo
            .register(bot_id.to_string(), caps)
            .await
            .expect("register bot"),
    }
    if active {
        repo.register_streaming_connection(bot_id.to_string())
            .await
            .expect("register streaming connection");
    }
}

#[tokio::test]
async fn search_bots_tc_bot_filter_keeps_only_owner_suffixed_bots() {
    let db = sqlite_db().await;
    let cache = Arc::new(InMemoryCachePlugin::new());
    let repo = Arc::new(PersistentBotRepo::with_plugins_flavor(cache, db.clone(), DbSqlFlavor::Sqlite));
    let core = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane = Arc::new(BotControlPlaneCore::new(repo.clone(), providers.clone(), providers));
    let bot = Bot::new(core.clone() as Arc<dyn BotRegistryCoreService>)
        .with_control_plane(control_plane as Arc<dyn BotControlPlaneCoreService>);

    insert_bot_row(&db, "ws-native-bot", "Native", "public", None).await;
    insert_bot_row(&db, "tc-prefix:85020", "TC Assistant", "public", Some("85020")).await;

    let all = bot
        .search_bots(SearchBotsCommand::default())
        .await
        .expect("search all");
    let all_uuids: Vec<&str> = all.items.iter().map(|b| b.bot_uuid.as_str()).collect();
    assert!(all_uuids.contains(&"ws-native-bot"));
    assert!(all_uuids.contains(&"tc-prefix:85020"));

    let only_tc = bot
        .search_bots(SearchBotsCommand {
            tc_bot: Some(true),
            ..Default::default()
        })
        .await
        .expect("search tc only");
    let tc_uuids: Vec<&str> = only_tc.items.iter().map(|b| b.bot_uuid.as_str()).collect();
    assert_eq!(tc_uuids, vec!["tc-prefix:85020"]);

    let only_native = bot
        .search_bots(SearchBotsCommand {
            tc_bot: Some(false),
            ..Default::default()
        })
        .await
        .expect("search non-tc only");
    let native_uuids: Vec<&str> = only_native
        .items
        .iter()
        .map(|b| b.bot_uuid.as_str())
        .collect();
    assert_eq!(native_uuids, vec!["ws-native-bot"]);
}

#[tokio::test]
async fn search_bots_visibility_filter_accepts_multiple_values() {
    let (bot, core, _data_dir) = build_bot().await;
    core.register("public-bot".to_string(), capabilities("Public", "public"))
        .await
        .expect("register public bot");
    core.register("protected-bot".to_string(), capabilities("Protected", "protected"))
        .await
        .expect("register protected bot");
    core.register("private-bot".to_string(), capabilities("Private", "private"))
        .await
        .expect("register private bot");

    let result = bot
        .search_bots(SearchBotsCommand {
            visibility: Some(vec!["public".to_string(), "private".to_string()]),
            ..Default::default()
        })
        .await
        .expect("search bots by multiple visibility values");

    let uuids: Vec<&str> = result.items.iter().map(|b| b.bot_uuid.as_str()).collect();
    assert_eq!(uuids, vec!["private-bot", "public-bot"]);
    assert_eq!(result.total, 2);
}

#[tokio::test]
async fn search_bots_applies_exact_bot_uuid_candidates() {
    let (bot, core, _data_dir) = build_bot().await;
    core.register("candidate-bot".to_string(), capabilities("Candidate", "public"))
        .await
        .expect("register candidate bot");
    core.register("other-bot".to_string(), capabilities("Other", "public"))
        .await
        .expect("register other bot");

    let result = bot
        .search_bots(SearchBotsCommand {
            bot_uuids: Some(vec![
                "candidate-bot".to_string(),
                "missing-bot".to_string(),
            ]),
            ..Default::default()
        })
        .await
        .expect("search exact candidates");

    assert_eq!(result.total, 1);
    assert_eq!(result.items[0].bot_uuid, "candidate-bot");
}

#[tokio::test]
async fn search_bots_excludes_soft_deleted_persistent_rows_even_if_memory_has_bot() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = Arc::new(PersistentBotRepo::with_plugins_flavor(cache, db, DbSqlFlavor::Sqlite));
    let core = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane = Arc::new(BotControlPlaneCore::new(repo.clone(), providers.clone(), providers));
    let bot = Bot::new(core.clone() as Arc<dyn BotRegistryCoreService>)
        .with_control_plane(control_plane as Arc<dyn BotControlPlaneCoreService>);

    repo.register_with_owner_and_token(
        "soft-delete-bot".to_string(),
        capabilities("Soft Delete Bot", "public"),
        "11111111",
        "soft-delete-token",
    )
    .await
    .expect("register bot");
    assert!(repo.soft_delete("soft-delete-bot").await);

    // Re-registering a retained soft-deleted row intentionally does not clear
    // `is_deleted`; `/bots/search` must still not leak the in-memory bot.
    repo.register_with_owner_and_token(
        "soft-delete-bot".to_string(),
        capabilities("Soft Delete Bot", "public"),
        "11111111",
        "new-soft-delete-token",
    )
    .await
    .expect("re-register soft-deleted bot");

    let result = bot
        .search_bots(SearchBotsCommand::default())
        .await
        .expect("search bots");

    assert!(result.items.is_empty());
    assert_eq!(result.total, 0);
}

#[tokio::test]
async fn search_bots_uses_edge_grants_for_viewer_friends_and_dynamic_status() {
    let data_dir = tempfile::tempdir().expect("temp data dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(data_dir.path().to_path_buf()));
    let core = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane = Arc::new(BotControlPlaneCore::new(repo.clone(), providers.clone(), providers));
    let friend = Arc::new(RecordingFriendCoreService {
        friends: vec!["legacy-friend-bot".to_string()],
        ..Default::default()
    });
    let mut edge_grants = RecordingEdgeGrantRepo::default();
    edge_grants
        .friends
        .insert("viewer-bot".to_string(), vec!["friend-bot".to_string()]);
    let edge_grants = Arc::new(edge_grants);
    let bot = Bot::new_with_friend(
        core.clone() as Arc<dyn BotRegistryCoreService>,
        friend.clone() as Arc<dyn FriendCoreService>,
    )
    .with_control_plane(control_plane as Arc<dyn BotControlPlaneCoreService>)
    .with_edge_grants(edge_grants.clone() as Arc<dyn EdgeGrantRepoPort>);

    seed_search_bot(&core, "viewer-bot", "Viewer", "public", None, false).await;
    seed_search_bot(&core, "friend-bot", "Friend", "protected", None, true).await;
    seed_search_bot(&core, "requester-bot", "Requester", "public", None, false).await;
    seed_search_bot(&core, "other-bot", "Other", "public", None, false).await;

    let result = bot
        .search_bots(SearchBotsCommand {
            requester_actor_id: Some("requester-bot".to_string()),
            viewer_actor_id: Some("viewer-bot".to_string()),
            ..Default::default()
        })
        .await
        .expect("search with edge grants");

    let ids = result.items.iter().map(|item| item.bot_uuid.as_str()).collect::<Vec<_>>();
    assert_eq!(result.total, 3);
    assert!(ids.contains(&"friend-bot"));
    assert!(ids.contains(&"other-bot"));
    assert!(ids.contains(&"viewer-bot"));
    assert!(!ids.contains(&"requester-bot"));

    let friend_item = result
        .items
        .iter()
        .find(|item| item.bot_uuid == "friend-bot")
        .expect("friend bot in search result");
    assert_eq!(friend_item.dynamic_status.status, "active");
    assert_eq!(friend_item.is_friend, Some(true));

    let other_item = result
        .items
        .iter()
        .find(|item| item.bot_uuid == "other-bot")
        .expect("other bot in search result");
    assert_eq!(other_item.dynamic_status.status, "offline");
    assert_eq!(other_item.is_friend, Some(false));

    let edge_calls = edge_grants.calls.lock().await.clone();
    assert_eq!(edge_calls, vec![("viewer-bot".to_string(), resolve_env_str())]);
    assert!(friend.calls.lock().await.is_empty(), "legacy friend service must not be used when edge_grants are wired");
}

#[tokio::test]
async fn search_bots_falls_back_to_legacy_friend_service_when_edge_grants_are_missing() {
    let data_dir = tempfile::tempdir().expect("temp data dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(data_dir.path().to_path_buf()));
    let core = Arc::new(BotCore::with_repo(repo.clone()));
    let providers = Arc::new(MemoryProviderStore::new());
    let control_plane = Arc::new(BotControlPlaneCore::new(repo.clone(), providers.clone(), providers));
    let friend = Arc::new(RecordingFriendCoreService {
        friends: vec!["legacy-friend-bot".to_string()],
        ..Default::default()
    });
    let bot = Bot::new_with_friend(
        core.clone() as Arc<dyn BotRegistryCoreService>,
        friend.clone() as Arc<dyn FriendCoreService>,
    )
    .with_control_plane(control_plane as Arc<dyn BotControlPlaneCoreService>);

    seed_search_bot(&core, "viewer-bot", "Viewer", "public", None, false).await;
    seed_search_bot(&core, "legacy-friend-bot", "Legacy Friend", "protected", None, false).await;
    seed_search_bot(&core, "other-bot", "Other", "public", None, false).await;

    let result = bot
        .search_bots(SearchBotsCommand {
            viewer_actor_id: Some("viewer-bot".to_string()),
            friendship: Some(BotSearchFriendshipFilter::Friends),
            ..Default::default()
        })
        .await
        .expect("search with legacy friend service");

    let ids = result.items.iter().map(|item| item.bot_uuid.as_str()).collect::<Vec<_>>();
    assert_eq!(result.total, 1);
    assert_eq!(ids, vec!["legacy-friend-bot"]);
    assert_eq!(result.items[0].is_friend, Some(true));
    assert_eq!(friend.calls.lock().await.clone(), vec!["viewer-bot".to_string()]);
}

#[tokio::test]
async fn search_bots_rejects_friendship_filter_without_viewer_actor() {
    let (bot, _, _data_dir) = build_bot().await;

    let result = bot
        .search_bots(SearchBotsCommand {
            friendship: Some(BotSearchFriendshipFilter::Friends),
            ..Default::default()
        })
        .await;

    assert!(matches!(
        result,
        Err(BotUseCaseError::Service(
            bcs_service_api::ServiceError::InvalidOperation { message, .. }
        )) if message == "friendship filter requires viewer_actor_id"
    ));
}
