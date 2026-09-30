    use super::*;
    use bcs_db_local::LocalSqliteDbPlugin;

    const MYSQL_EVENTING_MIGRATION: &str =
        include_str!("../../../../../migrations/mysql/009_eventing.sql");
    const MYSQL_GROUP_OPENING_MIGRATION: &str =
        include_str!("../../../../../migrations/mysql/010_group_opening_message.sql");

    const EVENTING_TABLES: &[&str] = &[
        "bcs_event_subscriptions",
        "bcs_event_subscription_revisions",
        "bcs_event_scope_epochs",
        "bcs_event_streams",
        "bcs_events",
        "bcs_event_fanout_targets",
        "bcs_event_deliveries",
        "bcs_event_delivery_attempts",
        "bcs_event_subscription_audits",
    ];

    async fn sqlite_index_names(db: &dyn DbPlugin) -> DbResult<Vec<String>> {
        let rows = db
            .query(DbStatement::new(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_event_%' ORDER BY name",
            ))
            .await?;
        rows.into_iter()
            .map(|row| db_get_column(&row, "name"))
            .collect()
    }

    #[tokio::test]
    async fn empty_encrypted_revision_schema_migrates_to_plaintext_endpoint() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_event_subscription_revisions (
                subscription_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                event_filters_json TEXT NOT NULL,
                payload_mode TEXT NOT NULL,
                endpoint_ciphertext BLOB NOT NULL,
                endpoint_key_id TEXT NOT NULL,
                endpoint_key_version INTEGER NOT NULL,
                endpoint_nonce BLOB NOT NULL,
                endpoint_auth_tag BLOB NOT NULL,
                secret_ciphertext BLOB NOT NULL,
                secret_key_id TEXT NOT NULL,
                secret_key_version INTEGER NOT NULL,
                secret_nonce BLOB NOT NULL,
                secret_auth_tag BLOB NOT NULL,
                request_timeout_ms INTEGER NOT NULL,
                activated_at TEXT NOT NULL,
                retired_at TEXT DEFAULT NULL,
                env TEXT NOT NULL,
                PRIMARY KEY(subscription_id, revision)
            )",
        ))
        .await?;

        migrate_sqlite_eventing_plaintext_endpoint(&db).await?;

        let columns = sqlite_table_columns(&db, "bcs_event_subscription_revisions").await?;
        assert!(columns.iter().any(|column| column == "endpoint_url"));
        assert!(!columns.iter().any(|column| column == "endpoint_ciphertext"));
        assert!(!columns.iter().any(|column| column == "secret_ciphertext"));
        Ok(())
    }

    #[tokio::test]
    async fn fresh_sqlite_schema_contains_all_eventing_tables_columns_and_indexes() -> DbResult<()>
    {
        let db = LocalSqliteDbPlugin::new()?;
        run_sqlite_migrations(&db).await?;

        let group_columns = sqlite_table_columns(&db, "bcs_groups").await?;
        assert!(
            group_columns
                .iter()
                .any(|column| column == "opening_message_json"),
            "bcs_groups missing opening_message_json"
        );

        for table in EVENTING_TABLES {
            assert!(
                table_exists(&db, table).await?,
                "missing Eventing table {table}"
            );
        }
        for (table, required_columns) in [
            (
                "bcs_event_subscriptions",
                &[
                    "subscription_id",
                    "scope_type",
                    "scope_id",
                    "current_revision",
                    "env",
                ][..],
            ),
            (
                "bcs_event_subscription_revisions",
                &["subscription_id", "revision", "endpoint_url"][..],
            ),
            (
                "bcs_events",
                &[
                    "event_id",
                    "producer_key",
                    "stream_key",
                    "sequence",
                    "retention_until",
                ][..],
            ),
            (
                "bcs_event_fanout_targets",
                &[
                    "target_id",
                    "purpose",
                    "depends_on_target_id",
                    "replay_request_id",
                    "lease_owner",
                    "lease_until",
                ][..],
            ),
            (
                "bcs_event_deliveries",
                &[
                    "delivery_id",
                    "payload_bytes",
                    "payload_sha256",
                    "lease_owner",
                    "lease_until",
                ][..],
            ),
            (
                "bcs_event_delivery_attempts",
                &["delivery_id", "attempt_no", "result", "worker_id"][..],
            ),
        ] {
            let columns = sqlite_table_columns(&db, table).await?;
            for required in required_columns {
                assert!(
                    columns.iter().any(|column| column == required),
                    "{table} missing column {required}"
                );
            }
        }

        let indexes = sqlite_index_names(&db).await?;
        for required in [
            "idx_event_subscription_scope",
            "idx_event_subscription_status",
            "idx_event_claim_due",
            "idx_event_strict_lane",
            "idx_event_retention",
        ] {
            assert!(
                indexes.iter().any(|index| index == required),
                "missing index {required}"
            );
        }
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_scope_epoch_is_scope_local_and_has_no_global_offset_table() -> DbResult<()> {
        let db = LocalSqliteDbPlugin::new()?;
        run_sqlite_migrations(&db).await?;

        let columns = db
            .query(DbStatement::new(
                "PRAGMA table_info(bcs_event_scope_epochs)",
            ))
            .await?;
        let primary_key = columns
            .into_iter()
            .filter_map(|row| {
                let order: i64 = db_get_column(&row, "pk").ok()?;
                let name: String = db_get_column(&row, "name").ok()?;
                (order > 0).then_some((order, name))
            })
            .collect::<Vec<_>>();
        assert_eq!(
            primary_key,
            vec![
                (1, "env".to_string()),
                (2, "scope_type".to_string()),
                (3, "scope_id".to_string()),
            ]
        );
        assert!(!table_exists(&db, "bcs_event_offsets").await?);
        assert!(!table_exists(&db, "bcs_event_global_cursor").await?);
        Ok(())
    }

    #[test]
    fn mysql_eventing_migration_is_additive_scope_local_and_indexed() {
        for table in EVENTING_TABLES {
            assert!(
                MYSQL_EVENTING_MIGRATION.contains(&format!("CREATE TABLE IF NOT EXISTS `{table}`")),
                "missing MySQL Eventing table {table}"
            );
        }
        assert!(MYSQL_EVENTING_MIGRATION.contains("PRIMARY KEY (`env`, `scope_type`, `scope_id`)"));
        assert!(MYSQL_EVENTING_MIGRATION.contains("KEY `idx_event_claim_due`"));
        assert!(MYSQL_EVENTING_MIGRATION.contains("KEY `idx_event_strict_lane`"));
        assert!(MYSQL_EVENTING_MIGRATION.contains("KEY `idx_event_retention`"));
        assert!(!MYSQL_EVENTING_MIGRATION.contains("bcs_event_offsets"));
        assert!(!MYSQL_EVENTING_MIGRATION.contains("bcs_event_global_cursor"));
        assert!(!MYSQL_EVENTING_MIGRATION.contains("ALTER TABLE"));
        assert!(
            MYSQL_GROUP_OPENING_MIGRATION
                .contains("ADD COLUMN `opening_message_json` text DEFAULT NULL")
        );
    }
