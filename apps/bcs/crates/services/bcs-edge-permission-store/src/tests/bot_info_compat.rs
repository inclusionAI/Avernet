use super::*;

async fn seed_bot_info(store: &DbBotActorConfigStore, bot_info: String, friend_ext: DbValue) {
    store.execute(
        "seed_bot_info_compat",
        DbStatement::with_params(
            "INSERT INTO bcs_bots \
             (bot_uuid, env, name, visibility, user_visibility, friend_check_in_strategy, bot_info, friend_ext) \
             VALUES ('legacy-bot', 'pre', 'Legacy', 'protected', 'private', 'DEPT_FREE', ?, ?)",
            vec![DbValue::from(bot_info), friend_ext],
        ),
    ).await.expect("seed legacy bot");
}

#[tokio::test]
async fn legacy_friend_ext_survives_string_encoded_bot_info() {
    let friend_ext = serde_json::json!({
        "view_scope_user_friend_deps": ["dept-user"],
        "view_scope_agent_friend_deps": ["dept-agent"],
        "no_check_scope_friend_deps": ["dept-auto"],
        "description": "保留 \"引号\" 和反斜杠 \\"
    });
    let object = serde_json::json!({"friend_ext": friend_ext}).to_string();
    for raw in [object.clone(), serde_json::to_string(&object).unwrap()] {
        let store = bot_config_store().await;
        seed_bot_info(&store, raw, DbValue::Null).await;

        let config = store.get("legacy-bot", "pre").await.expect("bot config");

        assert_eq!(serde_json::Value::Object(config.friend_ext), friend_ext);
        assert_eq!(config.visibility, "protected");
        assert_eq!(config.user_visibility, "private");
        assert_eq!(config.friend_check_in_strategy, "DEPT_FREE");
    }
}

#[tokio::test]
async fn dedicated_friend_ext_takes_precedence_over_encoded_legacy_bot_info() {
    let object = serde_json::json!({
        "friend_ext": {"no_check_scope_friend_deps": ["legacy-dept"]}
    }).to_string();
    for column in [serde_json::json!({}), serde_json::json!({"scope": "column"})] {
        let store = bot_config_store().await;
        seed_bot_info(
            &store,
            serde_json::to_string(&object).unwrap(),
            DbValue::from(column.to_string()),
        ).await;

        let config = store.get("legacy-bot", "pre").await.expect("bot config");

        assert_eq!(serde_json::Value::Object(config.friend_ext), column);
    }
}

#[tokio::test]
async fn invalid_legacy_bot_info_preserves_dedicated_policy_columns() {
    for raw in ["null", "[]", "123", "\"not-an-object\"", "{not-json"] {
        let store = bot_config_store().await;
        seed_bot_info(&store, raw.to_string(), DbValue::Null).await;

        let config = store.get("legacy-bot", "pre").await.expect("bot config");

        assert!(config.friend_ext.is_empty());
        assert_eq!(config.visibility, "protected");
        assert_eq!(config.user_visibility, "private");
        assert_eq!(config.friend_check_in_strategy, "DEPT_FREE");
    }
}
