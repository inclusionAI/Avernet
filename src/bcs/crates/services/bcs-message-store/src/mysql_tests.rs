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
