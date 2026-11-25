//! Fresh-chain schema and exact version inventory. Kept separate from the
//! upgrade regressions so adding a migration does not exceed the file limit.
use super::*;

#[tokio::test]
async fn fresh_sqlite_migrations_create_human_output_metadata() -> DbResult<()> {
    let db = LocalSqliteDbPlugin::new()?;

    run_sqlite_migrations(&db).await?;

    let columns = column_names(&db, "bcs_bots").await?;
    assert!(columns.iter().any(|column| column == "agent_code"));
    assert!(columns.iter().any(|column| column == "task_claim_mode"));
    assert!(columns.iter().any(|column| column == "task_dream_mode"));
    assert!(columns.iter().any(|column| column == "user_visibility"));
    assert!(columns.iter().any(|column| column == "friend_ext"));
    assert!(columns
        .iter()
        .any(|column| column == "friend_check_in_strategy"));
    let node_columns = column_names(&db, "bcs_state_machine_node_runs").await?;
    assert!(node_columns.iter().any(|column| column == "outcome"));
    assert!(node_columns.iter().any(|column| column == "responded_by"));
    let request_columns = column_names(&db, "bcs_human_input_requests").await?;
    assert!(request_columns
        .iter()
        .any(|column| column == "active_slot_key"));
    assert!(request_columns
        .iter()
        .any(|column| column == "provider_message_ref"));
    assert!(request_columns.iter().any(|column| column == "created_at"));
    let session_columns = column_names(&db, "bcs_group_sessions").await?;
    assert!(session_columns
        .iter()
        .any(|column| column == "callback_lease_owner"));
    assert!(session_columns
        .iter()
        .any(|column| column == "callback_lease_token"));
    assert!(session_columns
        .iter()
        .any(|column| column == "callback_lease_until_ms"));
    assert!(index_exists(&db, "idx_session_callback_recovery").await?);
    assert_eq!(
        migration_rows(&db).await?,
        vec![
            (1, "init_schema".to_string(), "sqlite".to_string()),
            (
                2,
                "channel_binding_audit_timestamps".to_string(),
                "sqlite".to_string()
            ),
            (3, "add_organizations".to_string(), "sqlite".to_string()),
            (
                4,
                "add_session_collection".to_string(),
                "sqlite".to_string()
            ),
            (
                5,
                "add_session_collection_timestamp".to_string(),
                "sqlite".to_string()
            ),
            (6, "session_files".to_string(), "sqlite".to_string()),
            (
                7,
                "human_input_output_metadata".to_string(),
                "sqlite".to_string()
            ),
            (
                8,
                "human_input_im_requests".to_string(),
                "sqlite".to_string()
            ),
            (9, "eventing".to_string(), "sqlite".to_string()),
            (
                10,
                "eventing_plaintext_endpoint".to_string(),
                "sqlite".to_string()
            ),
            (
                11,
                "group_opening_message".to_string(),
                "sqlite".to_string()
            ),
            (12, "add_bot_task_modes".to_string(), "sqlite".to_string()),
            (13, "edge_permission".to_string(), "sqlite".to_string()),
            (
                14,
                "add_bot_internal_attributes".to_string(),
                "sqlite".to_string()
            ),
            (
                15,
                "group_participant_tags".to_string(),
                "sqlite".to_string()
            ),
            (16, "expand_session_ids".to_string(), "sqlite".to_string()),
            (
                17,
                "session_callback_lease".to_string(),
                "sqlite".to_string()
            ),
            (
                18,
                "state_machine_rerun_lineage".to_string(),
                "sqlite".to_string()
            ),
            (
                19,
                "one_shot_opening_message_override".to_string(),
                "sqlite".to_string()
            ),
            (20, "invite_code_id".to_string(), "sqlite".to_string()),
            (
                21,
                "human_participant_message_visibility".to_string(),
                "sqlite".to_string()
            ),
            (22, "message_deliveries".to_string(), "sqlite".to_string()),
            (
                23,
                "message_delivery_policy".to_string(),
                "sqlite".to_string()
            ),
            (
                24,
                "delivery_worker_queries".to_string(),
                "sqlite".to_string()
            ),
            (
                25,
                "delivery_context_selection".to_string(),
                "sqlite".to_string()
            ),
            (
                26,
                "delivery_pending_abort".to_string(),
                "sqlite".to_string()
            ),
            (27, "run_reply_segments".to_string(), "sqlite".to_string()),
            (28, "provider_bot_webhook".to_string(), "sqlite".to_string()),
            (29, "fixed_loop_runtime".to_string(), "sqlite".to_string()),
            (30, "bot_provider_storage".to_string(), "sqlite".to_string()),
            (
                31,
                "group_human_mention_notify_mode".to_string(),
                "sqlite".to_string()
            ),
            (32, "session_registry".to_string(), "sqlite".to_string()),
            (33, "provider_slug".to_string(), "sqlite".to_string()),
            (34, "group_context".to_string(), "sqlite".to_string()),
            (35, "group_context_owners".to_string(), "sqlite".to_string()),
            (36, "collaboration_drafts".to_string(), "sqlite".to_string()),
            (37, "bot_authority".to_string(), "sqlite".to_string()),
            (38, "task_handle_lookup".to_string(), "sqlite".to_string()),
            (39, "bot_a2a".to_string(), "sqlite".to_string()),
            (40, "bot_avatar".to_string(), "sqlite".to_string()),
            (41, "group_agent_cards".to_string(), "sqlite".to_string()),
            (42, "auth_session_version".to_string(), "sqlite".to_string()),
            (43, "delivery_session_lookup".to_string(), "sqlite".to_string()),
            (44, "group_context_preset".to_string(), "sqlite".to_string()),
            (45, "message_activity_indexes".to_string(), "sqlite".to_string()),
            (46, "configured_catalog_labels".to_string(), "sqlite".to_string()),
            (47, "dist_immutable_objects".to_string(), "sqlite".to_string()),
            (48, "agent_invoke_authorization".to_string(), "sqlite".to_string()),
            (49, "agent_invoke_command_fences".to_string(), "sqlite".to_string()),
            (50, "agent_invoke_governance_queries".to_string(), "sqlite".to_string()),
        ]
    );
    Ok(())
}
