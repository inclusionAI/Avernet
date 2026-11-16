use std::sync::Arc;

use bcs_db_api::DbPlugin;
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_organization_store::{DbOrganizationStore, MemoryOrganizationRepo};
use bcs_service_api::port::repo::{
    CreateOrganizationRecord, OrganizationRepoPort, UpdateOrganizationRecord,
    UpsertOrganizationMemberRecord,
};

use bcs_db_migrations as bootstrap_migrations;

#[tokio::test]
async fn memory_organization_repo_contract() {
    let repo = MemoryOrganizationRepo::new();
    bcs_test_support::contract::repo::organization_repo_contract_tests(&repo).await;
    disabling_organization_exits_members_without_restoring_them(&repo).await;
}

#[tokio::test]
async fn sqlite_organization_repo_contract() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("run sqlite migrations");
    let repo = DbOrganizationStore::sqlite(db);
    bcs_test_support::contract::repo::organization_repo_contract_tests(&repo).await;
    disabling_organization_exits_members_without_restoring_them(&repo).await;
}

async fn disabling_organization_exits_members_without_restoring_them(
    repo: &dyn OrganizationRepoPort,
) {
    repo.create_organization(CreateOrganizationRecord {
        env: "contract".into(),
        code: "disable-members".into(),
        name: "Disable members".into(),
        description: None,
        managing_provider_id: "provider-a".into(),
    }).await.unwrap();
    repo.upsert_member(UpsertOrganizationMemberRecord {
        env: "contract".into(),
        organization_code: "disable-members".into(),
        bot_uuid: "member-bot".into(),
        role: None,
    }).await.unwrap();
    assert!(repo.has_active_membership_by_bot("contract", "member-bot").await.unwrap());

    repo.update_organization(UpdateOrganizationRecord {
        env: "contract".into(),
        code: "disable-members".into(),
        name: None,
        description: None,
        disabled: Some(true),
    }).await.unwrap();
    let member = repo.get_member("contract", "disable-members", "member-bot").await.unwrap().unwrap();
    assert!(member.disabled);
    assert!(!repo.has_active_membership_by_bot("contract", "member-bot").await.unwrap());

    repo.update_organization(UpdateOrganizationRecord {
        env: "contract".into(),
        code: "disable-members".into(),
        name: None,
        description: None,
        disabled: Some(false),
    }).await.unwrap();
    let member = repo.get_member("contract", "disable-members", "member-bot").await.unwrap().unwrap();
    assert!(member.disabled, "re-enabling an organization must not restore old members");
    assert!(!repo.has_active_membership_by_bot("contract", "member-bot").await.unwrap());
}
