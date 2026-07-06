//! Integration tests for the `tags` field in /actors/list and /actors/search.
//!
//! These tests verify:
//! - The `tags` field is present in every bot entry returned by both endpoints
//! - When fuse is unavailable (default test config), `tags` is an empty object `{}`
//!
//! Full fuse integration (with real tags from POST v1/workers/batch) requires
//! a live bcsfuse service and is tested in staging/production environments.

mod helpers;

use helpers::{MockBot, create_temp_bots_dir, start_test_server};
use reqwest::Client;

#[tokio::test]
async fn test_list_actors_includes_tags_field() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);

    let mut target_bot = MockBot::connect(addr).await;
    target_bot.register("TagsTestBot1", &["test"], addr).await;

    let mut observer_bot = MockBot::connect(addr).await;
    observer_bot.register("ObserverBot1", &["test"], addr).await;
    let observer_id = observer_bot.bot_id.clone();

    let client = Client::new();
    let url = format!(
        "{}/actors/list?current_bot_uuid={}&cooperatable_only=false",
        base_url, observer_id
    );
    let resp = client.get(&url).send().await.expect("list failed");
    assert!(resp.status().is_success());

    let json: serde_json::Value = resp.json().await.expect("invalid JSON");
    let bots = json["bots"].as_array().expect("bots should be array");
    assert!(!bots.is_empty(), "should have at least one bot");

    for bot in bots {
        let tags = &bot["tags"];
        assert!(tags.is_object(), "tags field should be an object, got: {}", tags);
    }
}

#[tokio::test]
async fn test_list_actors_tags_empty_when_fuse_unavailable() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);

    let mut target_bot = MockBot::connect(addr).await;
    target_bot.register("TagsTestBot2", &["test"], addr).await;

    let mut observer_bot = MockBot::connect(addr).await;
    observer_bot.register("ObserverBot2", &["test"], addr).await;
    let observer_id = observer_bot.bot_id.clone();

    let client = Client::new();
    let url = format!(
        "{}/actors/list?current_bot_uuid={}&cooperatable_only=false",
        base_url, observer_id
    );
    let resp = client.get(&url).send().await.expect("list failed");
    assert!(resp.status().is_success());

    let json: serde_json::Value = resp.json().await.expect("invalid JSON");
    let bots = json["bots"].as_array().expect("bots should be array");

    for bot in bots {
        let tags = bot["tags"].as_object().expect("tags should be object");
        assert!(tags.is_empty(), "tags should be empty when fuse is unavailable");
    }

    assert!(json["total"].is_number(), "total should be a number");
}

#[tokio::test]
async fn test_search_actors_includes_tags_field() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);

    let mut bot = MockBot::connect(addr).await;
    bot.register("SearchTagsBot", &["test"], addr).await;
    let bot_id = bot.bot_id.clone();

    let client = Client::new();
    let url = format!(
        "{}/actors/search?q=SearchTagsBot&current_bot_uuid={}&cooperatable_only=false",
        base_url, bot_id
    );
    let resp = client.get(&url).send().await.expect("search failed");
    assert!(resp.status().is_success());

    let json: serde_json::Value = resp.json().await.expect("invalid JSON");
    let bots = json["bots"].as_array().expect("bots should be array");

    for bot in bots {
        let tags = &bot["tags"];
        assert!(tags.is_object(), "tags field should be an object in search results, got: {}", tags);
    }
}

#[tokio::test]
async fn test_search_actors_tags_empty_when_fuse_unavailable() {
    let bots_dir = create_temp_bots_dir();
    let (addr, _server) = start_test_server(&bots_dir.path().to_path_buf()).await;
    let base_url = format!("http://{}", addr);

    let mut bot = MockBot::connect(addr).await;
    bot.register("SearchTagsBot2", &["test"], addr).await;
    let bot_id = bot.bot_id.clone();

    let client = Client::new();
    let url = format!(
        "{}/actors/search?q=SearchTagsBot2&current_bot_uuid={}&cooperatable_only=false",
        base_url, bot_id
    );
    let resp = client.get(&url).send().await.expect("search failed");
    assert!(resp.status().is_success());

    let json: serde_json::Value = resp.json().await.expect("invalid JSON");
    let bots = json["bots"].as_array().expect("bots should be array");

    for bot in bots {
        let tags = bot["tags"].as_object().expect("tags should be object");
        assert!(tags.is_empty(), "tags should be empty when fuse is unavailable");
    }
}