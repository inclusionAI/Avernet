    use super::*;

    use std::{collections::BTreeMap, sync::Arc};

    use bcs_db_api::{
        DbError, DbExecuteResult, DbHealth, DbResult, DbRow, DbTransactionStepResult,
    };
    use tokio::sync::Mutex;

    #[derive(Default)]
    struct CapturingDb {
        executed: Mutex<Vec<DbStatement>>,
        queried: Mutex<Vec<DbStatement>>,
        fail_queries: std::sync::atomic::AtomicBool,
    }

    #[async_trait]
    impl DbPlugin for CapturingDb {
        async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
            self.queried.lock().await.push(statement);
            if self.fail_queries.load(std::sync::atomic::Ordering::Relaxed) {
                return Err(DbError::InvalidInput("injected query failure".into()));
            }
            Ok(Vec::new())
        }

        async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
            self.executed.lock().await.push(statement);
            Ok(DbExecuteResult::default())
        }

        async fn transaction(
            &self,
            steps: Vec<DbTransactionStep>,
        ) -> DbResult<Vec<DbTransactionStepResult>> {
            if steps.len() != 3 {
                return Err(DbError::InvalidInput(format!(
                    "unexpected transaction steps: {}",
                    steps.len()
                )));
            }
            let mut executed = self.executed.lock().await;
            for step in &steps {
                if let DbTransactionStep::Execute(statement) = step {
                    executed.push(statement.clone());
                }
            }
            let mut row = BTreeMap::new();
            row.insert("current_msg_seq".to_string(), DbValue::from(1_i64));
            Ok(vec![
                DbTransactionStepResult::Executed(DbExecuteResult {
                    affected_rows: 1,
                    last_insert_id: None,
                }),
                DbTransactionStepResult::Rows(vec![DbRow::new(row)]),
                DbTransactionStepResult::Executed(DbExecuteResult {
                    affected_rows: 1,
                    last_insert_id: None,
                }),
            ])
        }

        async fn health_check(&self) -> DbResult<DbHealth> {
            Ok(DbHealth::healthy())
        }
    }

    #[tokio::test]
    async fn canonical_history_lookup_bounds_batches_and_stops_on_failure() {
        let db = Arc::new(CapturingDb::default());
        let repo = MySqlMessageStore::new(db.clone(), "history-env".into());
        for count in [0_usize, 1, 200, 201, 450] {
            db.queried.lock().await.clear();
            let keys = (0..count).map(|i| format!("key-{i}")).collect::<Vec<_>>();
            repo.get_state_machine_messages_by_keys("session", &keys).await.unwrap();
            let queries = db.queried.lock().await;
            assert_eq!(queries.len(), 2 * count.div_ceil(200));
            for query in queries.iter() {
                assert!(query.params().len() <= 202);
                assert!(query.sql().contains("LIMIT 401"));
                assert_eq!(query.params()[0], DbValue::from("history-env"));
                assert_eq!(query.params()[1], DbValue::from("session"));
            }
        }
        db.queried.lock().await.clear();
        db.fail_queries.store(true, std::sync::atomic::Ordering::Relaxed);
        let keys = (0..450).map(|i| format!("key-{i}")).collect::<Vec<_>>();
        assert!(repo.get_state_machine_messages_by_keys("session", &keys).await.is_err());
        assert_eq!(db.queried.lock().await.len(), 1);
    }

    #[tokio::test]
    async fn append_message_binds_missing_client_msg_id_as_null() {
        let db = Arc::new(CapturingDb::default());
        let store = MySqlMessageStore::new(db.clone(), "dev".to_string());

        store
            .append_message(NewMessage {
                group_id: "group-1".to_string(),
                session_id: "group-1:session".to_string(),
                sender_id: "bot-worker".to_string(),
                sender_type: SenderType::Bot,
                message_type: "chat".to_string(),
                content: serde_json::json!("hello"),
                client_msg_id: None,
                owner_bot_id: Some("bot-worker".to_string()),
                created_at: 1,
                run_id: "run-1".to_string(),
                visibility_domain: MessageVisibilityDomain::Chat,
                audience: None,
            })
            .await
            .expect("append should succeed");

        let executed = db.executed.lock().await;
        let insert = executed
            .iter()
            .find(|statement| statement.sql().contains("INSERT INTO bcs_messages"))
            .expect("expected insert statement");
        assert_eq!(insert.params().get(9), Some(&DbValue::Null));
        assert_eq!(
            insert.params().get(10),
            Some(&DbValue::from(Some("bot-worker".to_string())))
        );
    }

    /// SQLite-backed store that records every `query` it sends, so the
    /// activity tests can assert one round trip per id chunk.
    struct RecordingDb {
        inner: Arc<dyn DbPlugin>,
        queried: Mutex<Vec<DbStatement>>,
    }

    impl RecordingDb {
        fn new(inner: Arc<dyn DbPlugin>) -> Self {
            Self { inner, queried: Mutex::new(Vec::new()) }
        }
    }

    #[async_trait]
    impl DbPlugin for RecordingDb {
        async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
            self.queried.lock().await.push(statement.clone());
            self.inner.query(statement).await
        }

        async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
            self.inner.execute(statement).await
        }

        async fn transaction(
            &self,
            steps: Vec<DbTransactionStep>,
        ) -> DbResult<Vec<DbTransactionStepResult>> {
            self.inner.transaction(steps).await
        }

        async fn health_check(&self) -> DbResult<DbHealth> {
            self.inner.health_check().await
        }
    }

    async fn activity_store(env: &str) -> (Arc<RecordingDb>, MySqlMessageStore) {
        use bcs_db_local::LocalSqliteDbPlugin;
        let db: Arc<dyn DbPlugin> =
            Arc::new(LocalSqliteDbPlugin::new().expect("open in-memory sqlite"));
        bcs_db_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .expect("run sqlite migrations");
        let recording = Arc::new(RecordingDb::new(db));
        let store = MySqlMessageStore::sqlite(recording.clone(), env.to_string());
        (recording, store)
    }

    /// Seed one session's persisted log; each message gets its own
    /// `created_at` so the aggregate assertions are unambiguous.
    async fn seed_activity(
        db: &RecordingDb,
        env: &str,
        group_id: &str,
        session_id: &str,
        created_at: &[u64],
    ) {
        for (index, at) in created_at.iter().enumerate() {
            db.execute(DbStatement::with_params(
                "INSERT INTO bcs_messages (message_id, group_id, session_id, session_seq, env, \
                 sender_id, sender_type, message_type, content, created_at) \
                 VALUES (?, ?, ?, ?, ?, 'bot', 'bot', 'chat', '{}', ?)",
                vec![
                    DbValue::from(format!("{env}-{session_id}-{index}")),
                    DbValue::from(group_id),
                    DbValue::from(session_id),
                    DbValue::from(index as i64 + 1),
                    DbValue::from(env),
                    DbValue::from(*at),
                ],
            ))
            .await
            .expect("seed message");
        }
    }

    /// Empty id lists short-circuit to an empty map and issue no query.
    #[tokio::test]
    async fn last_activity_empty_id_list_makes_no_query() {
        let (db, store) = activity_store("dev").await;
        seed_activity(&db, "dev", "g-a", "s-a", &[1_000]).await;
        db.queried.lock().await.clear();

        assert!(store.last_activity_by_group_ids(&[]).await.unwrap().is_empty());
        assert!(store.last_activity_by_session_ids(&[]).await.unwrap().is_empty());
        assert!(db.queried.lock().await.is_empty(), "empty batches must not query");
    }

    /// Q1/Q3: per-id `MAX(created_at)` in epoch ms, one aggregate per shape.
    #[tokio::test]
    async fn last_activity_returns_max_created_at_per_group_and_session() {
        let (db, store) = activity_store("dev").await;
        seed_activity(&db, "dev", "g-a", "s-a", &[1_000, 5_000, 3_000]).await;
        seed_activity(&db, "dev", "g-b", "s-b", &[2_000, 7_000, 4_000]).await;

        let groups = store
            .last_activity_by_group_ids(&["g-a".to_string(), "g-b".to_string()])
            .await
            .unwrap();
        assert_eq!(groups.len(), 2);
        assert_eq!(groups.get("g-a"), Some(&5_000));
        assert_eq!(groups.get("g-b"), Some(&7_000));

        let sessions = store
            .last_activity_by_session_ids(&["s-a".to_string(), "s-b".to_string()])
            .await
            .unwrap();
        assert_eq!(sessions.len(), 2);
        assert_eq!(sessions.get("s-a"), Some(&5_000));
        assert_eq!(sessions.get("s-b"), Some(&7_000));
    }

    /// Ids without any persisted message are absent from the map (design D1
    /// fallback stays with the caller); they share the chunked round trip.
    #[tokio::test]
    async fn last_activity_omits_ids_without_messages() {
        let (db, store) = activity_store("dev").await;
        seed_activity(&db, "dev", "g-known", "s-known", &[4_200]).await;

        let groups = store
            .last_activity_by_group_ids(&["g-known".to_string(), "g-missing".to_string()])
            .await
            .unwrap();
        assert_eq!(groups.len(), 1);
        assert_eq!(groups.get("g-known"), Some(&4_200));
        assert!(!groups.contains_key("g-missing"));

        let sessions = store
            .last_activity_by_session_ids(&["s-known".to_string(), "s-missing".to_string()])
            .await
            .unwrap();
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions.get("s-known"), Some(&4_200));
        assert!(!sessions.contains_key("s-missing"));
    }

    /// 250 distinct ids exceed the store's 200-id IN-list chunk, so each shape
    /// must issue two round trips and merge both halves.
    #[tokio::test]
    async fn last_activity_covers_ids_beyond_one_chunk() {
        let (db, store) = activity_store("dev").await;
        db.execute(DbStatement::new(
            "WITH RECURSIVE n(x) AS (SELECT 0 UNION ALL SELECT x+1 FROM n WHERE x<249) \
             INSERT INTO bcs_messages (message_id, group_id, session_id, session_seq, env, \
             sender_id, sender_type, message_type, content, created_at) \
             SELECT printf('m-%03d', x), printf('g-%03d', x), printf('s-%03d', x), 1, 'dev', \
             'bot', 'bot', 'chat', '{}', 1000 + x FROM n",
        ))
        .await
        .expect("seed 250 groups");

        let group_ids: Vec<String> = (0..250).map(|index| format!("g-{index:03}")).collect();
        db.queried.lock().await.clear();
        let groups = store.last_activity_by_group_ids(&group_ids).await.unwrap();
        assert_eq!(groups.len(), 250);
        assert_eq!(groups.get("g-000"), Some(&1_000));
        assert_eq!(groups.get("g-249"), Some(&1_249));
        {
            let queries = db.queried.lock().await;
            assert_eq!(queries.len(), 2, "one round trip per 200-id chunk");
            assert_eq!(queries[0].params().len(), 201);
            assert_eq!(queries[1].params().len(), 51);
            for query in queries.iter() {
                assert_eq!(query.params()[0], DbValue::from("dev"));
            }
        }

        let session_ids: Vec<String> = (0..250).map(|index| format!("s-{index:03}")).collect();
        db.queried.lock().await.clear();
        let sessions = store.last_activity_by_session_ids(&session_ids).await.unwrap();
        assert_eq!(sessions.len(), 250);
        assert_eq!(sessions.get("s-000"), Some(&1_000));
        assert_eq!(sessions.get("s-249"), Some(&1_249));
        assert_eq!(db.queried.lock().await.len(), 2);
    }

    /// A candidate with thousands of messages next to shallow ones must still
    /// report its own maximum (functional assertion; cost is measured in the
    /// verification task).
    #[tokio::test]
    async fn last_activity_deep_history_candidate_uses_its_max() {
        let (db, store) = activity_store("dev").await;
        db.execute(DbStatement::new(
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<3000) \
             INSERT INTO bcs_messages (message_id, group_id, session_id, session_seq, env, \
             sender_id, sender_type, message_type, content, created_at) \
             SELECT printf('deep-%05d', x), 'g-deep', 's-deep', x, 'dev', \
             'bot', 'bot', 'chat', '{}', 1000 + x FROM n",
        ))
        .await
        .expect("seed deep history");
        seed_activity(&db, "dev", "g-shallow", "s-shallow", &[9_000_000]).await;

        let groups = store
            .last_activity_by_group_ids(&["g-deep".to_string(), "g-shallow".to_string()])
            .await
            .unwrap();
        assert_eq!(groups.get("g-deep"), Some(&4_000));
        assert_eq!(groups.get("g-shallow"), Some(&9_000_000));

        let sessions = store
            .last_activity_by_session_ids(&["s-deep".to_string(), "s-shallow".to_string()])
            .await
            .unwrap();
        assert_eq!(sessions.get("s-deep"), Some(&4_000));
        assert_eq!(sessions.get("s-shallow"), Some(&9_000_000));
    }

    /// Env isolation: rows of another env never count, even for the same ids
    /// and with newer timestamps — and only-another-env ids stay absent.
    #[tokio::test]
    async fn last_activity_never_counts_another_environment() {
        let (db, store) = activity_store("dev").await;
        seed_activity(&db, "dev", "g-shared", "s-shared", &[1_000]).await;
        seed_activity(&db, "prod", "g-shared", "s-shared", &[9_000]).await;
        seed_activity(&db, "prod", "g-prod-only", "s-prod-only", &[9_000]).await;

        let groups = store
            .last_activity_by_group_ids(&["g-shared".to_string(), "g-prod-only".to_string()])
            .await
            .unwrap();
        assert_eq!(groups.len(), 1);
        assert_eq!(groups.get("g-shared"), Some(&1_000));

        let sessions = store
            .last_activity_by_session_ids(&["s-shared".to_string(), "s-prod-only".to_string()])
            .await
            .unwrap();
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions.get("s-shared"), Some(&1_000));
    }
