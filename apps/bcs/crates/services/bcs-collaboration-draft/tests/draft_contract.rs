use bcs_collaboration_draft::CollaborationDraftCore;
use bcs_collaboration_store::draft::DbCollaborationDraftStore;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_service_api::{ValidateCollaborationDefinitionYamlCommand, core::collaboration_draft::*};
use std::sync::Arc;

const ID: &str = "8d75fb68-628c-4e92-8431-e76870788f3c";
const OTHER: &str = "879160ac-3cb0-4cf4-9c4e-c8db18c1a541";

async fn initialize(db: &dyn DbPlugin) {
    for sql in include_str!("../../../../migrations/sqlite/036_collaboration_drafts.sql")
        .split(';')
        .filter(|s| !s.trim().is_empty())
    {
        db.execute(DbStatement::new(sql)).await.unwrap();
    }
}
fn core(db: Arc<dyn DbPlugin>) -> CollaborationDraftCore {
    CollaborationDraftCore::new(Arc::new(DbCollaborationDraftStore::new(db)))
}
fn content(text: &str) -> DraftContent {
    DraftContent {
        definition_yaml: text.into(),
        title: Some("协作 <&' \" 😀".into()),
    }
}
async fn setup() -> (Arc<LocalSqliteDbPlugin>, CollaborationDraftCore) {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    initialize(db.as_ref()).await;
    let service = core(db.clone());
    (db, service)
}

#[tokio::test]
async fn incident_bytes_survive_storage_and_validate_while_transcribed_source_fails() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/collaboration-definition-draft/indentation-regression.json"
    ))
    .unwrap();
    let yaml = fixture["revision"]["definition_yaml"].as_str().unwrap();
    let (_, service) = setup().await;
    let saved = service.create("owner", ID, content(yaml)).await.unwrap();
    assert!(saved.created);
    assert_eq!(saved.content.size_bytes, 2174);
    assert_eq!(
        saved.content.content_hash,
        fixture["revision"]["content_hash"]
    );
    let reference = service.reference("owner", ID, 1).await.unwrap();
    let fetched = service
        .read(DraftAccess::ReadToken(reference.read_token), ID, 1)
        .await
        .unwrap();
    assert_eq!(fetched.definition_yaml.as_bytes(), yaml.as_bytes());
    let validate = |yaml: String| {
        bcs_collaboration_runtime::validate_authoring_definition_yaml(
            ValidateCollaborationDefinitionYamlCommand {
                definition_yaml: yaml,
                judge_available: true,
            },
        )
    };
    assert!(validate(fetched.definition_yaml).valid);
    let damaged = validate(fixture["damaged_inline_yaml"].as_str().unwrap().into());
    assert!(!damaged.valid);
    assert!(damaged.errors.iter().any(|e| e.code == "YAML_PARSE"));
}

#[tokio::test]
async fn create_is_idempotent_against_first_revision_and_cas_has_one_winner() {
    let (_, service) = setup().await;
    let (created, retried) = tokio::join!(
        service.create("owner", ID, content("a:\r\n  中: 😀\r\n")),
        service.create("owner", ID, content("a:\r\n  中: 😀\r\n")),
    );
    let first = created.unwrap();
    let retried = retried.unwrap();
    assert_ne!(first.created, retried.created);
    assert_eq!(first.content.created_at, retried.content.created_at);
    let token = service.reference("owner", ID, 1).await.unwrap().read_token;
    let (a, b) = tokio::join!(
        service.update("owner", ID, 1, content("a: 2")),
        service.update("owner", ID, 1, content("a: 3")),
    );
    assert_ne!(a.is_ok(), b.is_ok());
    assert!(matches!(
        a.err().or_else(|| b.err()),
        Some(DraftCoreError::Conflict)
    ));
    let replay = service
        .create("owner", ID, content("a:\r\n  中: 😀\r\n"))
        .await
        .unwrap();
    assert!(!replay.created);
    assert_eq!(replay.content.revision, 1);
    assert_eq!(replay.content.created_at, first.content.created_at);
    assert_eq!(
        token,
        service.reference("owner", ID, 1).await.unwrap().read_token
    );
    assert!(matches!(
        service.create("owner", ID, content("different")).await,
        Err(DraftCoreError::Conflict)
    ));
    assert_eq!(
        service.metadata("owner", ID).await.unwrap().head_revision,
        2
    );
}

#[tokio::test]
async fn authorization_revocation_and_tombstones_are_independent_of_chat() {
    let (db, service) = setup().await;
    service.create("owner", ID, content(" ")).await.unwrap();
    service
        .update("owner", ID, 1, content("invalid: ["))
        .await
        .unwrap();
    let first = service.reference("owner", ID, 1).await.unwrap();
    let second = service.reference("owner", ID, 2).await.unwrap();
    assert!(matches!(
        service.metadata("other", ID).await,
        Err(DraftCoreError::Forbidden)
    ));
    assert!(matches!(
        service.create("other", ID, content(" ")).await,
        Err(DraftCoreError::Forbidden)
    ));
    assert!(matches!(
        service.delete("other", ID).await,
        Err(DraftCoreError::Forbidden)
    ));
    assert!(matches!(
        service
            .read(DraftAccess::ReadToken(first.read_token.clone()), ID, 2)
            .await,
        Err(DraftCoreError::Unavailable)
    ));
    // Independent databases issue different random grants for the same ID and revision.
    let (_, other_deployment) = setup().await;
    other_deployment
        .create("owner", ID, content(" "))
        .await
        .unwrap();
    assert!(matches!(
        other_deployment
            .read(DraftAccess::ReadToken(first.read_token.clone()), ID, 1)
            .await,
        Err(DraftCoreError::Unavailable)
    ));
    service.revoke("owner", ID, 1).await.unwrap();
    service.revoke("owner", ID, 1).await.unwrap();
    assert!(matches!(
        service.reference("owner", ID, 1).await,
        Err(DraftCoreError::Unavailable)
    ));
    service.create("owner", ID, content(" ")).await.unwrap();
    assert!(matches!(
        service
            .read(DraftAccess::ReadToken(first.read_token), ID, 1)
            .await,
        Err(DraftCoreError::Unavailable)
    ));
    assert_eq!(
        service
            .read(DraftAccess::Owner("owner".into()), ID, 1)
            .await
            .unwrap()
            .definition_yaml,
        " "
    );
    service
        .read(DraftAccess::ReadToken(second.read_token.clone()), ID, 2)
        .await
        .unwrap();
    service.delete("owner", ID).await.unwrap();
    service.delete("owner", ID).await.unwrap();
    assert!(service.metadata("owner", ID).await.unwrap().deleted);
    assert!(matches!(
        service.create("owner", ID, content(" ")).await,
        Err(DraftCoreError::Unavailable)
    ));
    assert!(matches!(
        service.update("owner", ID, 2, content(" ")).await,
        Err(DraftCoreError::Unavailable)
    ));
    assert!(matches!(
        service
            .read(DraftAccess::ReadToken(second.read_token), ID, 2)
            .await,
        Err(DraftCoreError::Unavailable)
    ));
    let rows = db.query(DbStatement::new("SELECT COUNT(*) AS n FROM bcs_collaboration_draft_revisions")).await.unwrap();
    assert_eq!(bcs_db_api::db_get_column::<i64>(&rows[0], "n").unwrap(), 0);
}

#[tokio::test]
async fn restart_preserves_references_and_revocation_without_keys() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("drafts.sqlite");
    let reference;
    {
        let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
        initialize(db.as_ref()).await;
        let service = core(db);
        service
            .create("owner", ID, content("\r\n😀\r\n"))
            .await
            .unwrap();
        service
            .create("owner", OTHER, content("old"))
            .await
            .unwrap();
        service.revoke("owner", OTHER, 1).await.unwrap();
        reference = service.reference("owner", ID, 1).await.unwrap();
    }
    let db = Arc::new(LocalSqliteDbPlugin::new_file(&path).unwrap());
    let service = core(db);
    assert_eq!(
        reference.read_token,
        service.reference("owner", ID, 1).await.unwrap().read_token
    );
    assert_eq!(
        service
            .read(DraftAccess::ReadToken(reference.read_token), ID, 1)
            .await
            .unwrap()
            .definition_yaml,
        "\r\n😀\r\n"
    );
    assert!(matches!(
        service.reference("owner", OTHER, 1).await,
        Err(DraftCoreError::Unavailable)
    ));
}

#[tokio::test]
async fn byte_title_revision_limits_and_storage_rollback() {
    let (db, service) = setup().await;
    let large = DraftContent {
        definition_yaml: "x".repeat(MAX_DRAFT_BYTES),
        title: Some("😀".repeat(120)),
    };
    service.create("owner", ID, large).await.unwrap();
    assert!(matches!(
        service
            .update("owner", ID, 1, content(&"x".repeat(MAX_DRAFT_BYTES + 1)))
            .await,
        Err(DraftCoreError::Invalid)
    ));
    let invalid_title = DraftContent {
        definition_yaml: "".into(),
        title: Some("😀".repeat(121)),
    };
    assert!(matches!(
        service.update("owner", ID, 1, invalid_title).await,
        Err(DraftCoreError::Invalid)
    ));
    assert!(matches!(
        service
            .update("owner", ID, MAX_DRAFT_REVISION, content(""))
            .await,
        Err(DraftCoreError::Invalid)
    ));
    // A storage failure after the CAS must roll back its head change.
    db.execute(DbStatement::new("CREATE TRIGGER reject_revision BEFORE INSERT ON bcs_collaboration_draft_revisions BEGIN SELECT RAISE(ABORT,'test write failure'); END")).await.unwrap();
    assert!(matches!(
        service.update("owner", ID, 1, content("v2")).await,
        Err(DraftCoreError::Storage)
    ));
    assert_eq!(
        service.metadata("owner", ID).await.unwrap().head_revision,
        1
    );
    assert!(matches!(
        service.create("owner", OTHER, content("v1")).await,
        Err(DraftCoreError::Storage)
    ));
    assert!(matches!(
        service.metadata("owner", OTHER).await,
        Err(DraftCoreError::Unavailable)
    ));
}
