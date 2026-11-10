use std::sync::Arc;

use bcs_db_local::LocalSqliteDbPlugin;
use bcs_domain::{GroupContextEntry, GroupContextOwners, GroupContextPolicy, ScopeLevel};
use bcs_group_context_store::{
    GroupContextRepo, MemoryGroupContextRepo, NewGroupContextEntry, SqlGroupContextStore,
};
use bcs_service_api::port::repo::{NewSessionParams, SessionRepoPort};
use bcs_session_store::{MemorySessionRepo, MySqlSessionStore};

const GROUP: &str = "inheritance";
const SOURCE: &str = "inheritance:00000001";
const DESTINATION: &str = "inheritance:00000002";

fn context(name: &str) -> NewGroupContextEntry {
    let now = chrono::Utc::now();
    NewGroupContextEntry {
        is_slot: true,
        context_id: format!("source-{name}"),
        name: name.into(),
        scope_level: ScopeLevel::Session,
        scope: SOURCE.into(),
        content: format!("content-{name}"),
        creator: "author".into(),
        visible_to: GroupContextPolicy::users(["creator"]),
        collect_from: GroupContextPolicy::users(["writer"]),
        owners: GroupContextOwners::new(["author"]),
        supersedes: None,
        change_reason: None,
        valid_from: now,
        valid_to: None,
        tx_time: now,
    }
}

fn creation(contexts: Vec<GroupContextEntry>) -> NewSessionParams {
    NewSessionParams {
        id: Some(DESTINATION.into()),
        created_by: Some("creator".into()),
        inherited_contexts: contexts,
        ..Default::default()
    }
}

async fn assert_atomic_inheritance(
    sessions: &dyn SessionRepoPort,
    contexts: &dyn GroupContextRepo,
) {
    let source = contexts.create_active(context("timeline")).await.unwrap();
    let stale = contexts.create_active(context("events")).await.unwrap();
    let updated = contexts
        .supersede_active(
            "events",
            SOURCE,
            stale.id,
            NewGroupContextEntry {
                supersedes: Some(stale.id),
                ..context("events")
            },
        )
        .await
        .unwrap();

    let failed = sessions
        .create(GROUP, creation(vec![source.clone(), stale]))
        .await;
    assert!(failed.is_err());
    assert!(sessions.get(DESTINATION).await.is_none());
    assert!(
        sessions
            .session_registration(DESTINATION)
            .await
            .unwrap()
            .is_none()
    );
    assert!(
        contexts
            .get_active("timeline", DESTINATION)
            .await
            .unwrap()
            .is_none()
    );

    sessions
        .create(GROUP, creation(vec![source.clone(), updated]))
        .await
        .unwrap();
    let copy = contexts
        .get_active("timeline", DESTINATION)
        .await
        .unwrap()
        .unwrap();
    assert_ne!(copy.context_id, source.context_id);
    assert!(copy.supersedes.is_none());
    assert!(copy.is_slot);
    assert_eq!(copy.content, source.content);
    assert_eq!(copy.visible_to, source.visible_to);
    assert_eq!(copy.collect_from, source.collect_from);
    assert_eq!(copy.creator, "creator");
    assert_eq!(copy.owners, GroupContextOwners::new(["creator"]));
    assert!(
        contexts
            .get_active("events", DESTINATION)
            .await
            .unwrap()
            .is_some()
    );
}

#[tokio::test]
async fn memory_commits_contexts_with_session() {
    let contexts = MemoryGroupContextRepo::new();
    let sessions = MemorySessionRepo::new().with_group_contexts(contexts.clone());
    assert_atomic_inheritance(&sessions, &contexts).await;
}

#[tokio::test]
async fn sqlite_commits_contexts_with_session() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    let contexts = SqlGroupContextStore::sqlite(db.clone());
    let sessions = MySqlSessionStore::sqlite(db, "dev".into());
    assert_atomic_inheritance(&sessions, &contexts).await;
}

#[allow(dead_code)]
#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;

#[tokio::test]
#[ignore = "requires BCS_TEST_MYSQL_URL pointing to an empty disposable MySQL database"]
async fn mysql_commits_contexts_with_session() {
    let db = mysql_chain::MysqlConformanceDb::open_empty().await;
    let versions = db.apply_full_migration_chain().await;
    assert_eq!(versions.last(), Some(&46));
    let contexts = SqlGroupContextStore::oceanbase(db.plugin.clone());
    let sessions = MySqlSessionStore::new(db.plugin.clone(), "dev".into());
    assert_atomic_inheritance(&sessions, &contexts).await;
    use bcs_domain::group_context::GroupContextSlotChange;
    contexts.change_slot(GROUP, "owner", GroupContextSlotChange::Add {
        name: "timeline".into(), description: String::new(),
    }).await.unwrap();
    let before = contexts.list_slots(GROUP).await.unwrap().remove(0);
    contexts.change_slot(GROUP, "admin", GroupContextSlotChange::Edit {
        name: "timeline".into(), new_name: Some("events".into()), description: None,
    }).await.unwrap();
    let after = contexts.list_slots(GROUP).await.unwrap().remove(0);
    assert_eq!(after.name, "events");
    assert_eq!(after.creator, "owner");
    assert_eq!(after.modifier, "admin");
    assert_eq!(before.gmt_create, after.gmt_create);
    assert!(after.gmt_modified >= before.gmt_modified);
    db.close().await;
}

#[tokio::test]
async fn sqlite_commits_multiple_context_batches() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    let contexts = SqlGroupContextStore::sqlite(db.clone());
    let sessions = MySqlSessionStore::sqlite(db, "dev".into());
    let names = (0..101)
        .map(|index| format!("context-{index}"))
        .collect::<Vec<_>>();
    for name in &names {
        contexts.create_active(context(name)).await.unwrap();
    }
    let source = contexts.inheritance_sources(SOURCE, None).await.unwrap();
    assert_eq!(source.len(), names.len());
    sessions.create(GROUP, creation(source)).await.unwrap();
    let copies = contexts
        .inheritance_sources(DESTINATION, None)
        .await
        .unwrap();
    assert_eq!(copies.len(), names.len());
}
