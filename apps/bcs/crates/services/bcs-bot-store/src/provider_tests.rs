    use super::*;
    use std::sync::Mutex;

    use bcs_db_api::{
        DbExecuteResult, DbHealth, DbResult, DbTransactionStep, DbTransactionStepResult,
    };

    struct RecordingDbPlugin {
        affected_rows: u64,
        statements: Mutex<Vec<String>>,
    }

    impl RecordingDbPlugin {
        fn new(affected_rows: u64) -> Self {
            Self {
                affected_rows,
                statements: Mutex::new(Vec::new()),
            }
        }

        fn statements(&self) -> Vec<String> {
            self.statements.lock().expect("statements").clone()
        }
    }

    #[async_trait]
    impl DbPlugin for RecordingDbPlugin {
        async fn query(&self, _statement: DbStatement) -> DbResult<Vec<DbRow>> {
            Err(DbError::Unsupported("query not scripted".to_string()))
        }

        async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
            self.statements
                .lock()
                .expect("statements")
                .push(statement.sql().to_string());
            Ok(DbExecuteResult {
                affected_rows: self.affected_rows,
                last_insert_id: None,
            })
        }

        async fn transaction(
            &self,
            _steps: Vec<DbTransactionStep>,
        ) -> DbResult<Vec<DbTransactionStepResult>> {
            Err(DbError::Unsupported("transaction not scripted".to_string()))
        }

        async fn health_check(&self) -> DbResult<DbHealth> {
            Ok(DbHealth::healthy())
        }
    }

    fn provider(provider_id: &str) -> ProviderRecord {
        ProviderRecord {
            provider_id: provider_id.to_string(),
            slug: None,
            name: "Provider".to_string(),
            config: r#"{"downlink":{"enabled":true}}"#.to_string(),
            created_by: "11111111".to_string(),
            owners: r#"["11111111"]"#.to_string(),
            disabled: false,
            created_at: 1,
            updated_at: 1,
        }
    }

    #[tokio::test]
    async fn db_provider_insert_uses_plain_insert_and_rejects_zero_affected_rows() {
        let db = Arc::new(RecordingDbPlugin::new(0));
        let store = DbProviderStore::mysql(db.clone());

        let err = store
            .insert_provider(provider("provider-1"))
            .await
            .expect_err("zero affected rows must fail");

        assert!(matches!(err, ServiceError::InvalidOperation { .. }));
        let statements = db.statements();
        assert_eq!(statements.len(), 1);
        assert!(statements[0].starts_with("INSERT INTO bcs_providers"));
        assert!(!statements[0].contains("IGNORE"));
    }

    // -------------------------------------------------------------------
    // SQLite roundtrip tests — exercise the real read path so that any
    // future regression in SELECT / parse_* (e.g. dropping the *_ts
    // aliases, breaking the seconds→millis mapping, or reverting to raw
    // datetime strings) surfaces immediately.
    // -------------------------------------------------------------------

    use bcs_db_local::LocalSqliteDbPlugin;

    fn unix_now_secs() -> u64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    }

    async fn sqlite_provider_db() -> Arc<dyn DbPlugin> {
        let db: Arc<dyn DbPlugin> =
            Arc::new(LocalSqliteDbPlugin::new().expect("open sqlite"));
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_providers (
                provider_id TEXT NOT NULL,
                slug TEXT,
                env TEXT NOT NULL,
                name TEXT NOT NULL,
                config TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                created_by TEXT NOT NULL,
                owners TEXT NOT NULL,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (env, provider_id)
            )",
        ))
        .await
        .expect("create bcs_providers");
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_provider_credentials (
                provider_id TEXT NOT NULL,
                env TEXT NOT NULL,
                credential_kind TEXT NOT NULL,
                secret_value TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (env, provider_id, credential_kind)
            )",
        ))
        .await
        .expect("create bcs_provider_credentials");
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_provider_bot_bindings (
                webhook_url TEXT,
                bot_uuid TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                provider_bot_ref TEXT NOT NULL DEFAULT '',
                env TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (env, provider_id, provider_bot_ref),
                UNIQUE (env, bot_uuid)
            )",
        ))
        .await
        .expect("create bcs_provider_bot_bindings");
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_bots (
                agent_card TEXT,
                connection_mode TEXT DEFAULT 'plugin',
                bot_uuid TEXT NOT NULL,
                env TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                bot_info TEXT DEFAULT NULL,
                visibility TEXT NOT NULL DEFAULT 'protected',
                actor_kind TEXT NOT NULL DEFAULT 'bot',
                status TEXT NOT NULL DEFAULT 'online',
                agent_code TEXT DEFAULT NULL,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (env, bot_uuid)
            )",
        ))
        .await
        .expect("create bcs_bots");
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_organization_members (
                env TEXT NOT NULL,
                organization_code TEXT NOT NULL,
                bot_uuid TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (env, organization_code, bot_uuid)
            )",
        ))
        .await
        .expect("create bcs_organization_members");
        db
    }

    #[tokio::test]
    async fn db_provider_sqlite_roundtrips_with_nonzero_timestamps() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db);
        let baseline_secs = unix_now_secs();

        store
            .insert_provider(provider("provider-rt"))
            .await
            .expect("insert provider");

        let stored = store
            .get_provider("provider-rt")
            .await
            .expect("get provider")
            .expect("provider exists");

        assert!(stored.created_at > 0, "created_at must not be 0");
        assert!(stored.updated_at > 0, "updated_at must not be 0");
        assert_eq!(stored.created_by, "11111111");
        assert_eq!(stored.owners, r#"["11111111"]"#);
        let stored_secs = stored.created_at / 1000;
        // Allow a 5-second window to absorb test latency / clock drift.
        assert!(
            stored_secs + 5 >= baseline_secs && stored_secs <= baseline_secs + 5,
            "created_at {}s should be near baseline {}s",
            stored_secs,
            baseline_secs
        );
    }

    #[tokio::test]
    async fn db_provider_sqlite_update_advances_modified_timestamp() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db);

        store
            .insert_provider(provider("provider-upd"))
            .await
            .expect("insert provider");

        let before = store
            .get_provider("provider-upd")
            .await
            .expect("get provider")
            .expect("exists");

        // SQLite CURRENT_TIMESTAMP / strftime('%s','now') resolve at 1-second
        // granularity; sleep just past that boundary so the new value differs.
        tokio::time::sleep(std::time::Duration::from_millis(1_100)).await;

        let after = store
            .update_provider_metadata("provider-upd", Some("Renamed"), None, None, 0)
            .await
            .expect("update provider")
            .expect("returned record");

        assert_eq!(after.created_at, before.created_at);
        assert!(
            after.updated_at > before.updated_at,
            "updated_at must advance: before={}, after={}",
            before.updated_at,
            after.updated_at
        );
        assert_eq!(after.name, "Renamed");
    }

    #[tokio::test]
    async fn db_provider_sqlite_credential_and_binding_have_nonzero_timestamps() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db);

        store
            .insert_provider(provider("provider-cb"))
            .await
            .expect("insert provider");
        store
            .insert_credential(ProviderCredential {
                provider_id: "provider-cb".to_string(),
                credential_kind: "provider_admin".to_string(),
                secret_value: "secret-1".to_string(),
                disabled: false,
                created_at: 0,
                updated_at: 0,
            })
            .await
            .expect("insert credential");
        store
            .insert_binding(ProviderBotBinding {
                webhook_url: None,
                bot_uuid: "bot-1".to_string(),
                provider_id: "provider-cb".to_string(),
                provider_bot_ref: "ref-1".to_string(),
                disabled: false,
                created_at: 0,
                updated_at: 0,
            })
            .await
            .expect("insert binding");

        let credential = store
            .get_credential_by_secret("provider_admin", "secret-1")
            .await
            .expect("get credential")
            .expect("credential exists");
        assert!(credential.created_at > 0);
        assert!(credential.updated_at > 0);

        let binding = store
            .get_binding_by_bot_uuid("bot-1")
            .await
            .expect("get binding")
            .expect("binding exists");
        assert!(binding.created_at > 0);
        assert!(binding.updated_at > 0);
    }

    #[tokio::test]
    async fn db_provider_sqlite_lists_discoverable_provider_bot_records_without_credentials() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db.clone());
        let env = resolve_env();

        store
            .insert_provider(provider("provider-ok"))
            .await
            .expect("insert provider");
        let mut disabled_provider = provider("provider-disabled");
        disabled_provider.disabled = true;
        store
            .insert_provider(disabled_provider)
            .await
            .expect("insert disabled provider");

        for (bot_uuid, provider_id, disabled, is_deleted, actor_kind) in [
            ("bot-ok", "provider-ok", false, 0, "bot"),
            ("bot-binding-disabled", "provider-ok", true, 0, "bot"),
            ("bot-provider-disabled", "provider-disabled", false, 0, "bot"),
            ("bot-deleted", "provider-ok", false, 1, "bot"),
            ("human-bound", "provider-ok", false, 0, "human"),
        ] {
            db.execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, env, name, actor_kind, is_deleted) \
                 VALUES (?, ?, ?, ?, ?)",
                vec![
                    DbValue::from(bot_uuid),
                    DbValue::from(env.as_str()),
                    DbValue::from(bot_uuid),
                    DbValue::from(actor_kind),
                    DbValue::from(is_deleted),
                ],
            ))
            .await
            .expect("insert bot row");
            store
                .insert_binding(ProviderBotBinding {
                    webhook_url: None,
                    bot_uuid: bot_uuid.to_string(),
                    provider_id: provider_id.to_string(),
                    provider_bot_ref: format!("ref-{bot_uuid}"),
                    disabled,
                    created_at: 0,
                    updated_at: 0,
                })
                .await
                .expect("insert binding");
        }

        let records = store
            .list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::All)
            .await
            .expect("list records");

        assert_eq!(records.len(), 1);
        assert_eq!(records[0].bot_uuid, "bot-ok");
        assert_eq!(records[0].provider_id, "provider-ok");
        assert_eq!(records[0].provider_name, "Provider");
        assert_eq!(
            records[0]
                .capabilities
                .as_ref()
                .and_then(|capabilities| capabilities.name.as_deref()),
            Some("bot-ok")
        );
    }

    #[tokio::test]
    async fn db_provider_sqlite_filters_discoverable_provider_bot_records_by_selector() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db.clone());
        let env = resolve_env();

        store
            .insert_provider(provider("provider-search"))
            .await
            .expect("insert provider");

        for (bot_uuid, name, bot_info, agent_code) in [
            (
                "bot-query",
                "Needle Runner",
                r#"{"summary":"general helper","domains":["ops"],"skills":[{"name":"deploy"}],"scopes":["logs"]}"#,
                "agent-query",
            ),
            (
                "bot-skill",
                "Skill Runner",
                r#"{"summary":"database helper","domains":["database"],"skills":[{"name":"sql"},{"name":"ops"}],"scopes":["prod"]}"#,
                "agent-skill",
            ),
            (
                "bot-other",
                "Other Runner",
                r#"{"summary":"other helper","domains":["docs"],"skills":[{"name":"writer"}],"scopes":["draft"]}"#,
                "agent-other",
            ),
        ] {
            db.execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, env, name, bot_info, agent_code) \
                 VALUES (?, ?, ?, ?, ?)",
                vec![
                    DbValue::from(bot_uuid),
                    DbValue::from(env.as_str()),
                    DbValue::from(name),
                    DbValue::from(bot_info),
                    DbValue::from(agent_code),
                ],
            ))
            .await
            .expect("insert bot row");
            store
                .insert_binding(ProviderBotBinding {
                    webhook_url: None,
                    bot_uuid: bot_uuid.to_string(),
                    provider_id: "provider-search".to_string(),
                    provider_bot_ref: format!("ref-{bot_uuid}"),
                    disabled: false,
                    created_at: 0,
                    updated_at: 0,
                })
                .await
                .expect("insert binding");
        }

        let query_records = store
            .list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::Query(
                "needle".to_string(),
            ))
            .await
            .expect("query records");
        assert_eq!(query_records.len(), 1);
        assert_eq!(query_records[0].bot_uuid, "bot-query");

        let skill_records = store
            .list_discoverable_provider_bot_records(
                &ProviderBotDiscoverySelector::RequiredSkills(vec![
                    "sql".to_string(),
                    "ops".to_string(),
                ]),
            )
            .await
            .expect("skill records");
        assert_eq!(skill_records.len(), 1);
        assert_eq!(skill_records[0].bot_uuid, "bot-skill");

        let agent_code_records = store
            .list_discoverable_provider_bot_records(&ProviderBotDiscoverySelector::Query(
                "agent-other".to_string(),
            ))
            .await
            .expect("agent code query records");
        assert!(
            agent_code_records.is_empty(),
            "provider discover SQL must not treat agent_code as a -q search field"
        );
    }

    #[tokio::test]
    async fn organization_candidate_read_excludes_active_members_before_paging() {
        let db = sqlite_provider_db().await;
        let store = DbProviderStore::sqlite(db.clone());
        let env = resolve_env();

        store
            .insert_provider(provider("provider-candidate"))
            .await
            .expect("insert provider");
        for bot_uuid in ["bot-a", "bot-b", "bot-c"] {
            db.execute(DbStatement::with_params(
                "INSERT INTO bcs_bots (bot_uuid, env, name) VALUES (?, ?, ?)",
                vec![
                    DbValue::from(bot_uuid),
                    DbValue::from(env.as_str()),
                    DbValue::from(format!("{bot_uuid} name")),
                ],
            ))
            .await
            .expect("insert bot");
            store
                .insert_binding(ProviderBotBinding {
                    webhook_url: None,
                    bot_uuid: bot_uuid.to_string(),
                    provider_id: "provider-candidate".to_string(),
                    provider_bot_ref: format!("ref-{bot_uuid}"),
                    disabled: false,
                    created_at: 0,
                    updated_at: 0,
                })
                .await
                .expect("insert binding");
        }
        for (bot_uuid, disabled) in [("bot-a", 0_i64), ("bot-c", 1_i64)] {
            db.execute(DbStatement::with_params(
                "INSERT INTO bcs_organization_members \
                 (env, organization_code, bot_uuid, disabled) VALUES (?, ?, ?, ?)",
                vec![
                    DbValue::from(env.as_str()),
                    DbValue::from("promo-2026"),
                    DbValue::from(bot_uuid),
                    DbValue::from(disabled),
                ],
            ))
            .await
            .expect("insert organization member");
        }

        let page = store
            .list_organization_candidates_page(OrganizationCandidateReadQuery {
                env: env.clone(),
                organization_code: "promo-2026".to_string(),
                provider_ids: vec!["provider-candidate".to_string()],
                q: None,
                offset: 1,
                limit: 1,
            })
            .await
            .expect("query candidates")
            .expect("database read model is supported");

        assert_eq!(page.total, 2);
        assert_eq!(page.records.len(), 1);
        assert_eq!(page.records[0].bot_uuid, "bot-c");
    }
