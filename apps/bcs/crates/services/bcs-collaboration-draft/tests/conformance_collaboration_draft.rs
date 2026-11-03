use bcs_app_collaboration_draft::CollaborationDraftApplication;
use bcs_collaboration_draft::CollaborationDraftCore;
use bcs_collaboration_store::{MemoryCollaborationStore, draft::DbCollaborationDraftStore};
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{JudgeDecision, JudgeEvaluatorPort, JudgeRequest, ServiceResult};
use std::sync::Arc;

struct UnusedJudge;
#[async_trait::async_trait]
impl JudgeEvaluatorPort for UnusedJudge {
    async fn judge(&self, _: JudgeRequest) -> ServiceResult<JudgeDecision> {
        panic!("must not execute");
    }
}

#[tokio::test]
async fn conformance_draft_sql_core_and_application() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    for sql in include_str!("../../../../migrations/sqlite/036_collaboration_drafts.sql")
        .split(';')
        .filter(|s| !s.trim().is_empty())
    {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
    let repo = Arc::new(DbCollaborationDraftStore::new(db));
    bcs_test_support::contract::collaboration_draft::collaboration_draft_repo_port_contract_tests(
        repo.as_ref(),
    )
    .await;
    let core = Arc::new(CollaborationDraftCore::new(repo));
    bcs_test_support::contract::collaboration_draft::collaboration_draft_core_service_contract_tests(core.as_ref()).await;
    let services = bcs_services_container::Services::builder().build_for_test();
    let memory = Arc::new(MemoryCollaborationStore::new());
    let validator = Arc::new(bcs_collaboration_runtime::CollaborationRuntime::new(
        memory.clone(),
        memory.clone(),
        memory.clone(),
        memory,
        services.group,
        services.session_management,
        services.bot_delivery,
        Arc::new(UnusedJudge),
    ));
    let application = CollaborationDraftApplication::new(core, validator, false);
    bcs_test_support::contract::collaboration_draft::collaboration_draft_service_contract_tests(
        &application,
    )
    .await;
}
