use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement,
    DbTransactionStep, DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::port::repo::{NewSessionParams, SessionRepoPort};
use bcs_service_api::{Participant, ParticipantRole};
use bcs_session_store::{MemorySessionRepo, MySqlSessionStore};

#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;

async fn membership_contract(repo: &dyn SessionRepoPort) {
    let ids = vec!["unknown".into(), "human_alice".into(), "owned".into()];
    assert!(!repo.has_group_session_participant("g1", &[]).await.unwrap());
    assert!(!repo.has_group_session_participant("g1", &ids).await.unwrap());
    let session = repo.create("g1", NewSessionParams {
        participants: vec![
            Participant::human("human_alice", ParticipantRole::Observer),
            Participant::bot("owned", ParticipantRole::Consultant),
        ],
        ..Default::default()
    }).await.unwrap();
    assert!(repo.has_group_session_participant("g1", &ids).await.unwrap());
    assert!(!repo.has_group_session_participant("g2", &ids).await.unwrap());
    assert!(!repo.has_group_session_participant("g1", &["other".into()]).await.unwrap());
    assert!(repo.complete_if_running(&session.id, None, None).await.unwrap().is_some());
    assert!(repo.has_group_session_participant("g1", &ids).await.unwrap());
    repo.remove_participant(&session.id, "human_alice").await.unwrap();
    assert!(!repo.has_group_session_participant("g1", &["human_alice".into()]).await.unwrap());
    assert!(repo.has_group_session_participant("g1", &["owned".into()]).await.unwrap());
    repo.delete(&session.id).await.unwrap();
    assert!(!repo.has_group_session_participant("g1", &ids).await.unwrap());
}

#[tokio::test]
async fn memory_passes_group_session_membership_contract() {
    membership_contract(&MemorySessionRepo::new()).await;
}

#[tokio::test]
async fn sqlite_passes_group_session_membership_contract_and_environment_isolation() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let repo = MySqlSessionStore::sqlite(db.clone(), "dev".into());
    membership_contract(&repo).await;
    repo.create("g1", NewSessionParams {
        participants: vec![Participant::bot("owned", ParticipantRole::Consultant)],
        ..Default::default()
    }).await.unwrap();
    let other_env = MySqlSessionStore::sqlite(db, "other".into());
    assert!(!other_env.has_group_session_participant("g1", &["owned".into()]).await.unwrap());
}

#[derive(Default)]
struct RecordingDb {
    statements: Mutex<Vec<DbStatement>>,
    fail: bool,
    found: bool,
}

#[async_trait]
impl DbPlugin for RecordingDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.statements.lock().unwrap().push(statement);
        if self.fail {
            Err(DbError::Backend("membership database unavailable".into()))
        } else if self.found {
            Ok(vec![DbRow::new(Default::default())])
        } else {
            Ok(Vec::new())
        }
    }
    async fn execute(&self, _: DbStatement) -> DbResult<DbExecuteResult> {
        panic!("membership lookup must not write")
    }
    async fn transaction(&self, _: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        panic!("membership lookup must not start a transaction")
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

#[tokio::test]
async fn sql_membership_batches_actors_and_stops_at_first_match() {
    for (found, expected) in [(false, 3), (true, 1)] {
        let db = Arc::new(RecordingDb { found, ..Default::default() });
        let repo = MySqlSessionStore::new(db.clone(), "dev".into());
        let ids = (0..401).map(|i| format!("bot-{i}")).collect::<Vec<_>>();
        assert!(!repo.has_group_session_participant("g1", &[]).await.unwrap());
        assert!(db.statements.lock().unwrap().is_empty());
        assert_eq!(repo.has_group_session_participant("g1", &ids).await.unwrap(), found);
        let statements = db.statements.lock().unwrap();
        assert_eq!(statements.len(), expected);
        for statement in statements.iter() {
            assert!(statement.sql().contains("LIMIT 1"));
            assert!(statement.params().len() <= 202);
        }
    }
}

#[tokio::test]
async fn sql_membership_propagates_database_failures_without_retrying() {
    let db = Arc::new(RecordingDb { fail: true, ..Default::default() });
    let repo = MySqlSessionStore::new(db.clone(), "dev".into());
    let ids = (0..401).map(|i| format!("bot-{i}")).collect::<Vec<_>>();
    let error = repo.has_group_session_participant("g1", &ids).await.unwrap_err();
    assert!(error.to_string().contains("membership database unavailable"));
    assert_eq!(db.statements.lock().unwrap().len(), 1);
}
