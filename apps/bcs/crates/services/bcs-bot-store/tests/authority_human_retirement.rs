#[path = "common/authority_lifecycle.rs"]
mod lifecycle;
use std::sync::Arc;
use bcs_db_api::{DbPlugin, DbStatement, DbValue};
use bcs_bot_store::PersistentBotRepo;
use bcs_service_api::port::repo::{BotRepoPort, BotAuthorityRepoPort};
use bcs_service_api::types::bot_authority::{AuthorityActor, AuthorityMutation, OwnershipInitialization};
#[tokio::test]
async fn sql_cannot_retire_human_with_owned_bot() {
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    for sql in lifecycle::SCHEMA { db.execute(DbStatement::new(*sql)).await.unwrap(); }
    let repo = PersistentBotRepo::with_sql_flavor(db.clone(), bcs_db_api::DbSqlFlavor::Sqlite);
    let init = OwnershipInitialization { owner_user_id:"alice".into(), mutation:AuthorityMutation {
        mutation_id:"review-create".into(), actor:AuthorityActor::Human{user_id:"alice".into()} } };
    repo.create_registration_if_absent_with_ownership("review-owned".into(),
        bcs_service_api::BotCapabilities { name:Some("owned".into()), ..Default::default() },
        "alice", "test-runtime-not-a-secret", init).await.unwrap();
    let env = lifecycle::resolve_env();
    let authority = bcs_edge_permission_store::DbBotAuthorityStore::sqlite(db.clone(), env.clone())
        .with_legacy_created_by_fallback(true);
    assert_eq!(authority.role("human_alice", "human_alice").await.unwrap(), None);
    let roles = authority.roles_for(&[
        bcs_service_api::types::bot_authority::BotRoleQuery { user_id:"human_alice".into(), bot_id:"human_alice".into() },
        bcs_service_api::types::bot_authority::BotRoleQuery { user_id:"human_alice".into(), bot_id:"review-owned".into() },
    ]).await.unwrap();
    assert_eq!(roles, vec![None, Some(bcs_service_api::types::bot_authority::BotAccessRelation::Owner)]);
    let result = repo.retire_with_authority("human_alice", lifecycle::retire_mutation_human("alice")).await;
    let rows = db.query(DbStatement::with_params("SELECT bot_uuid, is_deleted FROM bcs_bots WHERE env = ?", vec![DbValue::from(env)])).await.unwrap();
    assert!(result.is_err(), "must not leave an initialized live Bot whose sole owner is deleted");
    assert!(rows.iter().all(|row| bcs_db_api::db_get_column::<i64>(row, "is_deleted").unwrap() == 0));
    assert_eq!(authority.role("human_alice", "review-owned").await.unwrap(), Some(bcs_service_api::types::bot_authority::BotAccessRelation::Owner));
}
