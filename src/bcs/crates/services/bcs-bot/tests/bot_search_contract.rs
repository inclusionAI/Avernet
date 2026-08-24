//! Contract tests for `BotQueryService::search_bots` (the `/bots/search`
//! data source), focused on the TC (TeamClaw backend) bot filter.

use std::sync::Arc;

use bcs_bot::{Bot, BotCore};
use bcs_service_api::{
    BotCapabilities, BotQueryService, BotRegistryCoreService, SearchBotsCommand,
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