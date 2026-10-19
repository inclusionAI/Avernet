use super::*;

#[tokio::test]
async fn discovery_accepts_mixed_object_and_string_encoded_bot_info() {
    let summary = "说明包含 \"引号\"、反斜杠 \\ 和换行\n下一行";
    let object = serde_json::json!({
        "name": "legacy name",
        "summary": summary,
        "domains": ["database"],
        "skills": [{"name": "sql", "description": "SQL review"}],
        "scopes": ["pre"],
        "agent_code": "legacy-agent",
        "agent_token": "synthetic-private-marker"
    }).to_string();
    let encoded = serde_json::to_string(&object).unwrap();
    let db = Arc::new(RecordingDbPlugin::with_rows(vec![
        discovery_row(DbValue::from(object)),
        discovery_row(DbValue::from(encoded)),
    ]));

    let bots = DbOrganizationStore::sqlite(db)
        .list_discovery_bots("contract", "promo-2026", None)
        .await
        .expect("one legacy encoded member must not fail the whole organization")
        .expect("database discovery implementation");

    assert_eq!(bots.len(), 2);
    for bot in bots {
        assert_eq!(bot.capabilities.name.as_deref(), Some("Bot A"));
        assert_eq!(bot.capabilities.summary.as_deref(), Some(summary));
        assert_eq!(bot.capabilities.domains, ["database"]);
        assert_eq!(bot.capabilities.skills[0].name, "sql");
        assert_eq!(bot.capabilities.skills[0].description.as_deref(), Some("SQL review"));
        assert_eq!(bot.capabilities.scopes, ["pre"]);
        assert_eq!(bot.capabilities.visibility, "protected");
        assert_eq!(bot.capabilities.agent_code.as_deref(), Some("legacy-agent"));
        assert!(bot.capabilities.agent_token.is_none());
    }
}

#[tokio::test]
async fn discovery_rejects_invalid_bot_info_without_echoing_private_payload() {
    let private_marker = "synthetic-private-marker";
    let invalid = [
        serde_json::to_string(private_marker).unwrap(),
        serde_json::to_string(&serde_json::json!({
            "summary": [private_marker]
        }).to_string()).unwrap(),
        "[]".to_string(),
        "null".to_string(),
        "{not-json".to_string(),
    ];
    for raw in invalid {
        let db = Arc::new(RecordingDbPlugin::with_rows(vec![
            discovery_row(DbValue::from("{}")),
            discovery_row(DbValue::from(raw)),
        ]));
        let error = DbOrganizationStore::sqlite(db)
            .list_discovery_bots("contract", "promo-2026", None)
            .await
            .expect_err("genuinely invalid data must remain an explicit error");
        assert!(matches!(
            error,
            ServiceError::InternalError(ref message)
                if message == "organization db bot_info: invalid capabilities JSON"
        ));
        assert!(!error.to_string().contains(private_marker));
    }
}
