//! Contract tests for `BotQueryService::search_bots` (the `/bots/search`
//! data source), focused on the TC (TeamClaw backend) bot filter.

use std::sync::Arc;

use bcs_bot::{Bot, BotCore};
use bcs_bot_store::PersistentBotRepo;
use bcs_cache_local::InMemoryCachePlugin;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{
    BotCapabilities, BotQueryService, BotRegistryCoreService, BotRepoPort, SearchBotsCommand,
};
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
            env TEXT NOT NULL,
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
    let core = Arc::new(BotCore::with_base_dir(data_dir.path().to_path_buf()));
    let bot = Bot::new(core.clone() as Arc<dyn BotRegistryCoreService>);
    (bot, core, data_dir)
}

#[tokio::test]
async fn search_bots_tc_bot_filter_keeps_only_owner_suffixed_bots() {
    let (bot, core, _data_dir) = build_bot().await;

    // Native WebSocket bot: no owner-suffix, no `created_by`. Registered via
    // the plain `register` (no owner binding).
    core.register("ws-native-bot".to_string(), capabilities("Native", "public"))
        .await
        .expect("register native bot");

    // TC backend bot: owner-suffixed `bot_uuid` with matching `created_by`.
    core.register_with_owner_and_token(
        "tc-prefix:85020".to_string(),
        capabilities("TC Assistant", "public"),
        "85020",
        "token-irrelevant",
    )
    .await
    .expect("register tc bot");

    // No filter → both bots present.
    let all = bot
        .search_bots(SearchBotsCommand::default())
        .await
        .expect("search all");
    let all_uuids: Vec<&str> = all.items.iter().map(|b| b.bot_uuid.as_str()).collect();
    assert!(all_uuids.contains(&"ws-native-bot"));
    assert!(all_uuids.contains(&"tc-prefix:85020"));

    // tc_bot=true → only the TC bot.
    let only_tc = bot
        .search_bots(SearchBotsCommand {
            tc_bot: Some(true),
            ..Default::default()
        })
        .await
        .expect("search tc only");
    let tc_uuids: Vec<&str> = only_tc.items.iter().map(|b| b.bot_uuid.as_str()).collect();
    assert_eq!(tc_uuids, vec!["tc-prefix:85020"]);

    // tc_bot=false → only the native bot.
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
async fn search_bots_excludes_soft_deleted_persistent_rows_even_if_memory_has_bot() {
    let cache = Arc::new(InMemoryCachePlugin::new());
    let db = sqlite_db().await;
    let repo = Arc::new(PersistentBotRepo::with_plugins(cache, db));
    let core = Arc::new(BotCore::with_repo(repo.clone()));
    let bot = Bot::new(core.clone() as Arc<dyn BotRegistryCoreService>);

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
