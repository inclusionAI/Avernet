use std::sync::Arc;

use bcs_bot_store::PersistentBotRepo;
use bcs_db_api::{DbPlugin, DbSqlFlavor, DbStatement, db_get_column};
use bcs_service_api::{BotCapabilities, BotControlPlaneDescriptorPatch, BotControlPlanePatch, BotControlPlaneRepoPort, BotRepoPort};
use serde_json::{Value, json};

#[path = "common/bot_provider.rs"]
mod database;

async fn fixture() -> (Arc<dyn DbPlugin>, PersistentBotRepo) {
    let db = database::sqlite().await;
    for column in [
        "gmt_create TEXT DEFAULT '2026-10-14 00:00:00'", "gmt_modified TEXT DEFAULT '2026-10-14 00:00:00'", "task_claim_mode INTEGER DEFAULT 0",
        "task_dream_mode INTEGER DEFAULT 0", "user_visibility TEXT DEFAULT 'protected'",
        "friend_ext TEXT", "friend_check_in_strategy TEXT DEFAULT 'APPROVAL'",
    ] {
        db.execute(DbStatement::new(format!("ALTER TABLE bcs_bots ADD COLUMN {column}"))).await.unwrap();
    }
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), DbSqlFlavor::Sqlite);
    (db, repo)
}

fn original() -> Value {
    json!({
        "summary": "说明含\"引号\"、C:\\work\\bot\n第二行",
        "domains": ["engineering"], "skills": [{"name":"review","description":"review code"}],
        "scopes": ["team"], "hidden": false,
        "agent_code": "test-agent", "agent_token": "synthetic-private-value",
        "future_private": {"keep": true}, "friend_ext": {"team":"engineering"}
    })
}

async fn seed(db: &dyn DbPlugin, bot_id: &str, wrapped: bool) {
    let text = original().to_string();
    let stored = if wrapped { serde_json::to_string(&text).unwrap() } else { text };
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid,env,name,bot_info,session_token,visibility) VALUES (?,?,'legacy',?,'synthetic-session','public')",
        vec![bot_id.into(), bcs_config::resolve_env_str().into(), stored.into()],
    )).await.unwrap();
}

async fn stored(db: &dyn DbPlugin, bot_id: &str) -> Value {
    let rows = db.query(DbStatement::with_params("SELECT bot_info FROM bcs_bots WHERE bot_uuid=?", vec![bot_id.into()])).await.unwrap();
    let text: String = db_get_column(&rows[0], "bot_info").unwrap();
    let value: Value = serde_json::from_str(&text).unwrap();
    assert!(value.is_object(), "stored bot_info must be an object");
    value
}

#[tokio::test]
async fn wrapped_info_is_read_by_registry_control_plane_and_agent_lookup() {
    let (db, repo) = fixture().await;
    seed(db.as_ref(), "legacy", true).await;
    let bot = repo.get("legacy").await.unwrap();
    assert_eq!(bot.capabilities.summary.as_deref(), original()["summary"].as_str());
    assert_eq!(bot.capabilities.skills[0].name, "review");
    assert!(bot.capabilities.agent_token.is_none(), "public projection must hide credentials");
    let credentials = repo.get_agent_credentials("legacy").await.unwrap();
    assert_eq!(credentials.agent_token.as_deref(), Some("synthetic-private-value"));
    let record = repo.get_control_plane("legacy", &bcs_config::resolve_env_str()).await.unwrap().unwrap();
    assert_eq!(record.descriptor.domains, ["engineering"]);
    let registration = repo.find_agent_registration("test-agent").await.unwrap().unwrap();
    assert_eq!(registration.summary.as_deref(), original()["summary"].as_str());
    // Reconnect loads persisted capabilities into the active registry; get()
    // alone intentionally does not make an offline Bot discoverable there.
    let restored = repo.load_from_storage("legacy").await.unwrap();
    repo.register("legacy".into(), restored).await.unwrap();
    let discovered = repo.discover("engineering").await;
    assert_eq!(discovered.len(), 1);
    assert_eq!(discovered[0].capabilities.skills[0].name, "review");
}

#[tokio::test]
async fn control_plane_search_treats_wrapped_metadata_like_object_metadata() {
    use bcs_service_api::{BotCandidateVisibility, BotSearchCandidateQuery};
    let (db, repo) = fixture().await;
    seed(db.as_ref(), "legacy", true).await;
    let (records, count) = repo.search_control_plane_candidates(BotSearchCandidateQuery {
        acting_bot_id: "requester".into(), env: bcs_config::resolve_env_str(),
        visibility: BotCandidateVisibility::Collaboration, friend_ids: Default::default(),
        bot_uuids: None, name: None, q: Some("\"engineering\"".into()),
        visibility_filter: None, user_visibility: None, status: None, friendship: None,
        tc_bot: None, offset: 0, limit: 10,
    }).await.unwrap();
    assert_eq!(count, 1);
    assert_eq!(records[0].bot.descriptor.domains, ["engineering"]);
}

#[tokio::test]
async fn partial_patch_retains_non_object_fallback_without_json_function_errors() {
    for raw in ["not-json", "null", "[]", "42", "true", "\"plain string\""] {
        let (db, repo) = fixture().await;
        seed(db.as_ref(), "legacy", false).await;
        db.execute(DbStatement::with_params("UPDATE bcs_bots SET bot_info=?", vec![raw.into()])).await.unwrap();
        repo.patch_control_plane("legacy", &bcs_config::resolve_env_str(), BotControlPlanePatch {
            descriptor: Some(BotControlPlaneDescriptorPatch { summary: Some("updated".into()), ..Default::default() }),
            ..Default::default()
        }).await.unwrap();
        assert_eq!(stored(db.as_ref(), "legacy").await, json!({"summary":"updated"}));
    }
}

#[tokio::test]
async fn partial_patch_unwraps_without_losing_unmodified_or_unknown_fields() {
    for wrapped in [false, true] {
        let (db, repo) = fixture().await;
        seed(db.as_ref(), "legacy", wrapped).await;
        for summary in ["first", "second"] {
            repo.patch_control_plane("legacy", &bcs_config::resolve_env_str(), BotControlPlanePatch {
                descriptor: Some(BotControlPlaneDescriptorPatch { summary: Some(summary.into()), ..Default::default() }),
                ..Default::default()
            }).await.unwrap();
            let value = stored(db.as_ref(), "legacy").await;
            let mut expected = original();
            expected["summary"] = json!(summary);
            assert_eq!(value, expected);
        }
    }
}

#[tokio::test]
async fn lifecycle_updates_normalize_storage_and_keep_private_fields() {
    let (db, repo) = fixture().await;
    seed(db.as_ref(), "legacy", true).await;
    let caps: BotCapabilities = serde_json::from_value(original()).unwrap();
    for _ in 0..2 {
        repo.register("legacy".into(), caps.clone()).await.unwrap();
        let value = stored(db.as_ref(), "legacy").await;
        assert_eq!(value["future_private"], original()["future_private"]);
        assert_eq!(value["friend_ext"], original()["friend_ext"]);
        assert_eq!(value["summary"], original()["summary"]);
        assert_eq!(value["agent_token"], original()["agent_token"]);
    }
}

#[tokio::test]
async fn new_registration_writes_an_object_and_preserves_string_contents() {
    let (db, repo) = fixture().await;
    let caps: BotCapabilities = serde_json::from_value(original()).unwrap();
    repo.register("new-bot".into(), caps).await.unwrap();
    let value = stored(db.as_ref(), "new-bot").await;
    assert_eq!(value["summary"], original()["summary"]);
    assert_eq!(value["skills"], original()["skills"]);
}

#[tokio::test]
async fn provider_create_is_canonical_and_discovery_accepts_legacy_wrapper() {
    use bcs_bot_store::DbProviderStore;
    use bcs_service_api::{ProviderBotBindingRepoPort, ProviderBotDiscoverySelector};
    use bcs_service_api::bot_provider::{BotConnectionMode, BotProviderRecord};
    use bcs_service_api::port::repo::bot_provider::BotProviderRepoPort;
    let (db, _) = fixture().await;
    for column in ["name TEXT DEFAULT 'Provider'", "disabled INTEGER DEFAULT 0"] {
        db.execute(DbStatement::new(format!("ALTER TABLE bcs_providers ADD COLUMN {column}"))).await.unwrap();
    }
    let store = DbProviderStore::sqlite(db.clone());
    let caps: BotCapabilities = serde_json::from_value(original()).unwrap();
    store.create_provider_bot(BotProviderRecord {
        bot_uuid: "provider-bot".into(), provider_id: "provider-a".into(),
        provider_bot_ref: "provider-ref".into(), connection_mode: BotConnectionMode::Gateway,
        webhook_url: None, is_deleted: false,
    }, caps, "owner", "synthetic-session").await.unwrap();
    let value = stored(db.as_ref(), "provider-bot").await;
    assert_eq!(value["summary"], original()["summary"]);
    db.execute(DbStatement::with_params("UPDATE bcs_bots SET bot_info=?", vec![serde_json::to_string(&value.to_string()).unwrap().into()])).await.unwrap();
    for selector in [ProviderBotDiscoverySelector::All,
                     ProviderBotDiscoverySelector::Query("\"engineering\"".into())] {
        let records = store.list_discoverable_provider_bot_records(&selector).await.unwrap();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].capabilities.as_ref().unwrap().summary.as_deref(), original()["summary"].as_str());
    }
}

#[tokio::test]
async fn create_if_absent_serializes_metadata_once() {
    let (db, repo) = fixture().await;
    let caps: BotCapabilities = serde_json::from_value(original()).unwrap();
    assert!(repo.create_registration_if_absent("admin-bot".into(), caps, "owner", "synthetic-session").await.unwrap());
    let value = stored(db.as_ref(), "admin-bot").await;
    assert_eq!(value["summary"], original()["summary"]);
}

#[tokio::test]
async fn human_backfill_keeps_unknown_fields_from_wrapped_info() {
    let (db, repo) = fixture().await;
    seed(db.as_ref(), "human_staff", true).await;
    repo.ensure_human_actor("staff", "Human").await.unwrap();
    let bot = repo.get("human_staff").await.unwrap();
    assert_eq!(bot.capabilities.summary.as_deref(), original()["summary"].as_str());
    // Force a missing summary so this path actually writes the backfill.
    let mut value = original();
    value["summary"] = Value::Null;
    db.execute(DbStatement::with_params("UPDATE bcs_bots SET bot_info=?", vec![serde_json::to_string(&value.to_string()).unwrap().into()])).await.unwrap();
    repo.ensure_human_actor("staff", "Human").await.unwrap();
    let value = stored(db.as_ref(), "human_staff").await;
    assert_eq!(value["future_private"], original()["future_private"]);
    assert_eq!(value["summary"], "写点什么介绍自己");
}
