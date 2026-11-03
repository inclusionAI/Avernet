//! SQLite fixture for the friend-auth trigger integration tests.
use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;

#[path = "sqlite_fixture.rs"]
mod sqlite_fixture;
pub use sqlite_fixture::assemble;

pub async fn seed_bot(db: &LocalSqliteDbPlugin, bot_id: &str, strategy: &str) {
    let bot_info = serde_json::json!({"friend_check_in_strategy": strategy, "friend_ext": {}});
    db.execute(DbStatement::with_params(
        "INSERT INTO bcs_bots (bot_uuid, env, visibility, user_visibility, friend_check_in_strategy, bot_info, created_by) VALUES (?, 'dev', 'protected', 'protected', ?, ?, '85020')",
        vec![DbValue::from(bot_id), DbValue::from(strategy), DbValue::from(bot_info.to_string())],
    )).await.expect("seed bot");
}
