use super::*;
use bcs_service_api::SessionStatus;
use serde_json::json;

async fn contract(repo: &dyn SessionRepoPort) {
    let mut sessions = Vec::new();
    for _ in 0..5 {
        sessions.push(repo.create("recovery", NewSessionParams {
            session_kind: SessionKind::ServiceInvocation, ..Default::default()
        }).await.unwrap());
    }
    sessions.sort_by(|a, b| a.id.cmp(&b.id));
    let chat = repo.create("recovery", NewSessionParams::default()).await.unwrap();
    assert!(repo.complete_running_service_activation(&chat.id, chat.activation_count, None, None).await.unwrap().is_none());
    assert!(repo.complete_running_service_activation("missing", 1, None, None).await.unwrap().is_none());
    let target = &sessions[1];
    assert!(repo.complete_running_service_activation(&target.id, target.activation_count + 1, None, None).await.unwrap().is_none());
    let (one, two) = tokio::join!(
        repo.complete_running_service_activation(&target.id, target.activation_count, Some(json!("first")), None),
        repo.complete_running_service_activation(&target.id, target.activation_count, Some(json!("second")), None),
    );
    let winners: Vec<_> = [one.unwrap(), two.unwrap()].into_iter().flatten().collect();
    assert_eq!(winners.len(), 1);
    let completed = &winners[0];
    assert_eq!(completed.activation_count, target.activation_count);
    assert_eq!(completed.status, SessionStatus::Completed);
    assert!(repo.complete_running_service_activation(&target.id, target.activation_count, Some(json!("overwrite")), None).await.unwrap().is_none());
    assert_eq!(repo.try_get(&target.id).await.unwrap().unwrap().output, completed.output);
    assert!(repo.list_running_service_after(None, 0).await.unwrap().is_empty());
    let first = repo.list_running_service_after(None, 2).await.unwrap();
    assert_eq!(first.iter().map(|s| &s.id).collect::<Vec<_>>(), [&sessions[0].id, &sessions[2].id]);
    let last = repo.list_running_service_after(Some(&sessions[2].id), 2).await.unwrap();
    assert_eq!(last.iter().map(|s| &s.id).collect::<Vec<_>>(), [&sessions[3].id, &sessions[4].id]);
    assert!(repo.list_running_service_after(Some(&sessions[4].id), 2).await.unwrap().is_empty());
    repo.update_callback_status(&target.id, "not_applicable").await.unwrap();
    let next = repo.reactivate(&target.id, None).await.unwrap();
    assert!(repo.complete_running_service_activation(&target.id, target.activation_count, None, Some("old failure".into())).await.unwrap().is_none());
    let unchanged = repo.try_get(&target.id).await.unwrap().unwrap();
    assert_eq!(unchanged.status, SessionStatus::Running);
    assert_eq!(unchanged.activation_count, next.activation_count);
    assert!(unchanged.error_message.is_none());
}

#[tokio::test]
async fn memory_session_completion_recovery_conforms() {
    contract(&MemorySessionRepo::new()).await;
}

#[tokio::test]
async fn sqlite_session_completion_recovery_conforms_and_uses_index() {
    let db = sqlite_db().await;
    contract(&MySqlSessionStore::sqlite(db.clone(), "dev".into())).await;
    assert!(MySqlSessionStore::sqlite(db.clone(), "other".into()).list_running_service_after(None, 20).await.unwrap().is_empty());
    let rows = db.query(DbStatement::new("EXPLAIN QUERY PLAN SELECT session_id FROM bcs_group_sessions WHERE env = 'dev' AND session_kind = 'service_invocation' AND status = 'running' AND session_id > '' ORDER BY session_id ASC LIMIT 2")).await.unwrap();
    let details = rows.iter().map(|row| bcs_db_api::db_get_column::<String>(row, "detail").unwrap()).collect::<Vec<_>>().join("\n");
    assert!(details.contains("idx_session_running_recovery"), "{details}");
    assert!(!details.contains("TEMP B-TREE"), "{details}");
}

#[tokio::test]
async fn session_recovery_query_and_decode_failures_are_not_empty_pages() {
    for db in [Arc::new(AlwaysFailDb) as Arc<dyn DbPlugin>, Arc::new(MalformedMembershipRowDb)] {
        let repo = MySqlSessionStore::new(db, "dev".into());
        assert!(repo.list_running_service_after(None, 2).await.is_err());
        assert!(repo.complete_running_service_activation("session", 1, None, None).await.is_err());
        assert!(repo.list_running_service_after(None, 0).await.unwrap().is_empty());
    }
}

struct CompletionRaceDb {
    inner: Arc<dyn DbPlugin>,
    mode: AtomicUsize,
}

#[async_trait]
impl DbPlugin for CompletionRaceDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        if !statement.sql().starts_with("UPDATE bcs_group_sessions SET status = 'completed'") {
            return self.inner.execute(statement).await;
        }
        let mode = self.mode.swap(0, Ordering::SeqCst);
        if mode == 3 { return Err(DbError::Backend("injected completion write failure".into())); }
        let reactivate = "UPDATE bcs_group_sessions SET activation_count = activation_count + 1, status = 'running', output = NULL, error_message = NULL, completed_at = NULL";
        if mode == 1 { self.inner.execute(DbStatement::new(reactivate)).await?; }
        let result = self.inner.execute(statement).await?;
        if mode == 2 { self.inner.execute(DbStatement::new(reactivate)).await?; }
        Ok(result)
    }
    async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(steps).await
    }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}

#[tokio::test]
async fn completion_cas_handles_activation_races_and_returns_its_own_snapshot() {
    for mode in [1, 2, 3] {
        let db = Arc::new(CompletionRaceDb { inner: sqlite_db().await, mode: AtomicUsize::new(mode) });
        let repo = MySqlSessionStore::sqlite(db, "dev".into());
        let session = repo.create("group-race", NewSessionParams { session_kind: SessionKind::ServiceInvocation, ..Default::default() }).await.unwrap();
        let result = repo.complete_running_service_activation(&session.id, session.activation_count, Some(json!("old result")), None).await;
        let current = repo.try_get(&session.id).await.unwrap().unwrap();
        assert_eq!(current.status, SessionStatus::Running);
        match mode {
            1 => assert!(result.unwrap().is_none()),
            2 => {
                let completed = result.unwrap().unwrap();
                assert_eq!(completed.status, SessionStatus::Completed);
                assert_eq!(completed.activation_count, session.activation_count);
                assert_eq!(completed.output, Some(json!("old result")));
                assert_eq!(current.activation_count, completed.activation_count + 1);
                assert!(current.output.is_none());
            }
            3 => assert!(result.unwrap_err().to_string().contains("injected completion write failure")),
            _ => unreachable!(),
        }
    }
}
