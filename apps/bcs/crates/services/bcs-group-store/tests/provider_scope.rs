//! Public writes check live Provider policy within their persistence transaction.
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group_store::MySqlGroupStore;
use bcs_service_api::port::repo::{
    CommitGroupEventfulMutation, GroupEventfulMutation, GroupRepoPort,
};
use bcs_service_api::{Group, GroupMutableFieldsPatch, Participant, ParticipantRole, ServiceError};
use std::sync::Arc;
#[path = "../../../bootstrap/bcs/src/migrations.rs"]
#[allow(dead_code)]
mod bootstrap_migrations;
const ENV: &str = "scope-contract";
async fn fixture() -> Arc<dyn DbPlugin> {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_providers (provider_id, env, name, config, created_by, owners) VALUES ('local', 'scope-contract', 'Local', '{}', 'alice', '[\"alice\"]')")).await.unwrap();
    db.execute(DbStatement::new("INSERT INTO bcs_bots (bot_uuid, env, name, provider_id, created_by) VALUES ('local-bot', 'scope-contract', 'Local', 'local', 'alice')")).await.unwrap();
    db
}
fn store(db: &Arc<dyn DbPlugin>) -> MySqlGroupStore {
    MySqlGroupStore::sqlite(db.clone(), ENV.into()).with_provider_scope_guard()
}
fn group(id: &str, visibility: &str, actor: &str) -> Group {
    let mut group = Group::new(
        id,
        actor,
        vec![Participant::bot(actor, ParticipantRole::Driver)],
    );
    group.visibility = visibility.into();
    group
}
#[tokio::test]
async fn public_upserts_and_legacy_writes_are_guarded_without_partial_changes() {
    let db = fixture().await;
    let repo = store(&db);
    repo.upsert(group("allowed", "public", "local-bot"))
        .await
        .unwrap();
    repo.upsert(group("private", "private", "local-bot"))
        .await
        .unwrap();
    repo.upsert(group("other", "public", "unbound"))
        .await
        .unwrap();
    // Simulate externally tightened policy to test every persistence entry.
    db.execute(DbStatement::new(
        "UPDATE bcs_providers SET config = '{\"collaboration_scope\":\"same_owner\"}'",
    ))
    .await
    .unwrap();
    assert!(matches!(
        repo.upsert(group("denied", "public", "local-bot")).await,
        Err(ServiceError::Forbidden(_))
    ));
    assert!(store(&db).try_get("denied").await.unwrap().is_none());
    assert!(matches!(
        repo.update_visibility("private", "public").await,
        Err(ServiceError::Forbidden(_))
    ));
    assert!(matches!(
        repo.patch_mutable_fields(
            "private",
            GroupMutableFieldsPatch {
                label: Some("Rejected".into()),
                visibility: Some("public".into()),
                ..Default::default()
            }
        )
        .await,
        Err(ServiceError::Forbidden(_))
    ));
    let current = store(&db).try_get("private").await.unwrap().unwrap();
    assert_eq!(current.visibility, "private");
    assert_ne!(current.label.as_deref(), Some("Rejected"));
    for guarded in [false, true] {
        let actor = Participant::bot("local-bot", ParticipantRole::Consultant);
        let result = if guarded {
            repo.add_participant_with_visibility_guard("other", actor, true)
                .await
        } else {
            repo.add_participant("other", actor).await
        };
        assert!(matches!(result, Err(ServiceError::Forbidden(_))));
        assert!(
            store(&db)
                .try_get("other")
                .await
                .unwrap()
                .unwrap()
                .get_participant("local-bot")
                .is_none()
        );
    }
    // Missing Provider, corrupt policy and JSON null fail closed too.
    for policy in [
        "[]",
        "{\"collaboration_scope\":null}",
        "{\"collaboration_scope\":\"unknown\"}",
    ] {
        db.execute(DbStatement::with_params(
            "UPDATE bcs_providers SET config = ?",
            vec![policy.into()],
        ))
        .await
        .unwrap();
        assert!(matches!(
            repo.upsert(group("bad-policy", "public", "local-bot"))
                .await,
            Err(ServiceError::Forbidden(_))
        ));
    }
    db.execute(DbStatement::new("DELETE FROM bcs_providers"))
        .await
        .unwrap();
    assert!(matches!(
        repo.upsert(group("orphan", "public", "local-bot")).await,
        Err(ServiceError::Forbidden(_))
    ));
    // Private persistence remains available for remediation.
    repo.update_visibility("allowed", "private").await.unwrap();
}
#[tokio::test]
async fn public_transaction_keeps_version_check_and_environment_isolation() {
    let db = fixture().await;
    let repo = store(&db);
    let original = group("cas", "public", "local-bot");
    repo.upsert(original.clone()).await.unwrap();
    let mutation = |version| CommitGroupEventfulMutation {
        group_id: "cas".into(),
        expected_version: version,
        mutated_at_ms: 1_787_028_000_000,
        mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
            label: Some("New".into()),
            ..Default::default()
        }),
        event: None,
    };
    let changed = repo
        .commit_eventful_mutation(mutation(original.version))
        .await
        .unwrap();
    assert_eq!(changed.version, original.version + 1);
    assert!(matches!(
        repo.commit_eventful_mutation(mutation(original.version))
            .await,
        Err(ServiceError::Conflict(_))
    ));
    db.execute(DbStatement::new("INSERT INTO bcs_providers (provider_id, env, name, config, created_by, owners) VALUES ('local', 'other-env', 'Local', '{\"collaboration_scope\":\"same_owner\"}', 'alice', '[]')")).await.unwrap();
    repo.upsert(group("isolated", "public", "local-bot"))
        .await
        .unwrap();
    assert!(
        repo.upsert(Group::new(
            "too-big",
            "local-bot",
            (0..257)
                .map(|i| Participant::bot(format!("bot-{i}"), ParticipantRole::Consultant))
                .collect()
        ))
        .await
        .is_ok()
    );
    let mut oversized = store(&db).try_get("too-big").await.unwrap().unwrap();
    oversized.visibility = "public".into();
    assert!(matches!(
        repo.upsert(oversized).await,
        Err(ServiceError::InvalidOperation { .. })
    ));
}
