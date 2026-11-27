//! Public SQLite provider conformance for the immutable workflow-revision
//! directory (plan W). The store is the real DbPlugin-backed implementation
//! with the concrete strict-FSM validator injected; migrations run first so
//! the directory tables exist in a fresh file. The centralized directory port
//! harness and the W publication/no-fallback vectors both run against it.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "contract assertion")]
use bcs_db_api::{DbPlugin, DbResult, DbStatement, DbTransactionStep, DbValue};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_db_migrations::run_sqlite_migrations;
use bcs_dist_model::*;
use bcs_dist_runner_fsm::{d0_capabilities, FsmValidator};
use bcs_dist_store::workflow_revision::{
    composite_content_digest, load_key_id, revision_key_id, DbWorkflowRevisionStore,
};
use bcs_service_api::port::dist_agent::DistGroupDirectoryPort;
use bcs_service_api::port::repo::distributed::common::{IdempotentWrite, StoreError};
use bcs_test_support::contract::distributed::services::dist_group_directory_port_contract_tests;
use bcs_test_support::contract::distributed::workflow_revision::{
    idempotent_republish_returns_the_same_revision, pointer_never_references_an_unreadable_object,
    publish_v2_restart_loads_pinned_v1, same_revision_rejects_different_content, PublishFault,
    RevisionDamage, SameRevisionWrite, WorkflowPublication, WorkflowRevisionFixture,
    WORKFLOW_DEFINITION_ID,
};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// DbPlugin wrapper that poisons the publication transaction at its pointer
/// step: the matching step's SQL is replaced so the backend fails that step,
/// rolls the whole transaction back, and the failure propagates out of
/// publish (never swallowed into a new pointer).
struct PointerFaultDb {
    inner: Arc<dyn DbPlugin>,
    armed: AtomicBool,
}

#[async_trait::async_trait]
impl DbPlugin for PointerFaultDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<bcs_db_api::DbRow>> {
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<bcs_db_api::DbExecuteResult> {
        self.inner.execute(statement).await
    }
    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<bcs_db_api::DbTransactionStepResult>> {
        if self.armed.load(Ordering::SeqCst) {
            let mut poisoned = Vec::with_capacity(steps.len());
            for step in steps {
                let rendered = format!("{step:?}");
                if rendered.contains("bcs_fsm_group_publications")
                    && rendered.starts_with("Execute")
                {
                    poisoned.push(DbTransactionStep::Execute(DbStatement::new(
                        "UPDATE bcs_fault_injected_missing_table SET poisoned = 1",
                    )));
                    continue;
                }
                poisoned.push(step);
            }
            return self.inner.transaction(poisoned).await;
        }
        self.inner.transaction(steps).await
    }
    async fn health_check(&self) -> DbResult<bcs_db_api::DbHealth> {
        self.inner.health_check().await
    }
}

/// The SQLite provider fixture: one migrated database file. Store and
/// directory instances are constructed fresh per call (each directory is
/// really disconnected from the previous one), so a `generation` bump is a
/// faithful process restart with cleared caches.
struct SqliteDirectoryFixture {
    path: PathBuf,
    fault: Arc<PointerFaultDb>,
}

impl SqliteDirectoryFixture {
    async fn new() -> (tempfile::TempDir, Self) {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("workflow-revision.db");
        let db = LocalSqliteDbPlugin::new_file(&path).unwrap();
        run_sqlite_migrations(&db).await.unwrap();
        let fault = Arc::new(PointerFaultDb {
            inner: Arc::new(db),
            armed: AtomicBool::new(false),
        });
        (directory, Self { path, fault })
    }

    fn raw(&self) -> LocalSqliteDbPlugin {
        LocalSqliteDbPlugin::new_file(&self.path).unwrap()
    }

    fn store(&self, db: Arc<dyn DbPlugin>) -> DbWorkflowRevisionStore {
        DbWorkflowRevisionStore::new(db, Arc::new(FsmValidator), d0_capabilities()).unwrap()
    }
}

#[async_trait::async_trait]
impl WorkflowRevisionFixture for SqliteDirectoryFixture {
    async fn publish(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        definition_id: &str,
        publication: &WorkflowPublication,
    ) -> Result<(WorkflowRef, IdempotentWrite), StoreError> {
        let db = Arc::new(LocalSqliteDbPlugin::new_file(&self.path).unwrap());
        self.store(db)
            .publish(
                scope,
                group,
                definition_id,
                &publication.definition,
                &publication.participants,
                &publication.limits,
            )
            .await
    }

    async fn publish_with_fault(
        &self,
        fault: PublishFault,
        scope: &TaskScope,
        group: &GroupId,
        definition_id: &str,
        publication: &WorkflowPublication,
    ) -> Result<(WorkflowRef, IdempotentWrite), StoreError> {
        assert_eq!(fault, PublishFault::AtPointerWrite);
        self.fault.armed.store(true, Ordering::SeqCst);
        let outcome = self
            .store(self.fault.clone())
            .publish(
                scope,
                group,
                definition_id,
                &publication.definition,
                &publication.participants,
                &publication.limits,
            )
            .await;
        self.fault.armed.store(false, Ordering::SeqCst);
        outcome
    }

    async fn directory(&self, _generation: u64) -> Arc<dyn DistGroupDirectoryPort> {
        let db = Arc::new(LocalSqliteDbPlugin::new_file(&self.path).unwrap());
        Arc::new(self.store(db))
    }

    async fn damage(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        reference: &WorkflowRef,
        damage: RevisionDamage,
    ) {
        let db = self.raw();
        let key = revision_key_id(scope, group, &reference.definition_id, &reference.revision);
        let (sql, params): (&str, Vec<DbValue>) = match damage {
            RevisionDamage::Deleted => (
                "DELETE FROM bcs_fsm_workflow_revisions WHERE revision_key_hash = ?",
                vec![DbValue::String(key)],
            ),
            // Parseable but different definition bytes: only the digest
            // comparison (not a JSON parse) can catch this tamper.
            RevisionDamage::Corrupted => (
                "UPDATE bcs_fsm_workflow_revisions SET definition_json = ? \
                 WHERE revision_key_hash = ?",
                vec![DbValue::String("{}".to_string()), DbValue::String(key)],
            ),
            RevisionDamage::OldFormat => (
                "UPDATE bcs_fsm_workflow_revisions SET content_format = ? \
                 WHERE revision_key_hash = ?",
                vec![
                    DbValue::String("v0-legacy".to_string()),
                    DbValue::String(key),
                ],
            ),
        };
        db.execute(DbStatement::with_params(sql, params))
            .await
            .expect("damage statement runs");
    }

    async fn try_overwrite_same_revision(
        &self,
        scope: &TaskScope,
        group: &GroupId,
        reference: &WorkflowRef,
        publication: &WorkflowPublication,
    ) -> SameRevisionWrite {
        // The forbidden write through the provider's own durable layer: a
        // second object row with the SAME (scope, group, workflow, revision)
        // identity but different content bytes. The unique identity key must
        // reject it; object rows have no update path.
        let db = self.raw();
        let definition_json = serde_json::to_string(&publication.definition).unwrap();
        let bindings_json = serde_json::to_string(&publication.participants).unwrap();
        let limits_json = serde_json::to_string(&publication.limits).unwrap();
        let digest = composite_content_digest(
            &serde_json::to_value(&publication.definition).unwrap(),
            &serde_json::to_value(&publication.participants).unwrap(),
            &serde_json::to_value(&publication.limits).unwrap(),
        );
        let outcome = db
            .execute(DbStatement::with_params(
                "INSERT INTO bcs_fsm_workflow_revisions \
                 (revision_key_hash, load_key_hash, scope_tenant, scope_principal, group_id, \
                  workflow_id, revision, content_format, content_digest, definition_json, \
                  bindings_json, limits_json, compiled_format, compiled_json, published_at) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                vec![
                    DbValue::String(revision_key_id(
                        scope,
                        group,
                        &reference.definition_id,
                        &reference.revision,
                    )),
                    DbValue::String(load_key_id(scope, &reference.definition_id, &reference.revision)),
                    DbValue::String(scope.tenant.0.clone()),
                    DbValue::String(scope.principal.0.clone()),
                    DbValue::String(group.0.clone()),
                    DbValue::String(reference.definition_id.clone()),
                    DbValue::String(reference.revision.clone()),
                    DbValue::String("v1".to_string()),
                    DbValue::String(digest.0),
                    DbValue::String(definition_json),
                    DbValue::String(bindings_json),
                    DbValue::String(limits_json),
                    DbValue::String("v1".to_string()),
                    DbValue::String("{}".to_string()),
                    DbValue::I64(0),
                ],
            ))
            .await;
        match outcome {
            Ok(_) => SameRevisionWrite::Overwritten,
            Err(_) => SameRevisionWrite::Rejected,
        }
    }
}

#[tokio::test]
async fn conformance_workflow_revision_directory_port() {
    let (_directory, fixture) = SqliteDirectoryFixture::new().await;
    let scope = bcs_test_support::contract::distributed::workflow_revision::revision_scope();
    let group = bcs_test_support::contract::distributed::workflow_revision::revision_group();
    let v1 = bcs_test_support::contract::distributed::workflow_revision::workflow_publication_v1();
    let (_reference, outcome) = fixture
        .publish(&scope, &group, WORKFLOW_DEFINITION_ID, &v1)
        .await
        .unwrap();
    assert_eq!(outcome, IdempotentWrite::Applied);
    let directory = fixture.directory(0).await;
    dist_group_directory_port_contract_tests(directory.as_ref(), &scope, &group)
        .await
        .unwrap();
}

#[tokio::test]
async fn conformance_publish_v2_restart_loads_pinned_v1() {
    let (_directory, fixture) = SqliteDirectoryFixture::new().await;
    publish_v2_restart_loads_pinned_v1(&fixture).await.unwrap();
}

#[tokio::test]
async fn conformance_idempotent_republish_returns_the_same_revision() {
    let (_directory, fixture) = SqliteDirectoryFixture::new().await;
    idempotent_republish_returns_the_same_revision(&fixture)
        .await
        .unwrap();
}

#[tokio::test]
async fn conformance_same_revision_rejects_different_content() {
    let (_directory, fixture) = SqliteDirectoryFixture::new().await;
    same_revision_rejects_different_content(&fixture)
        .await
        .unwrap();
}

#[tokio::test]
async fn conformance_pointer_never_references_an_unreadable_object() {
    let (_directory, fixture) = SqliteDirectoryFixture::new().await;
    pointer_never_references_an_unreadable_object(&fixture)
        .await
        .unwrap();
}
