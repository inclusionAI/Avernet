//! Reusable conformance checks for Draft implementations, without transport.
use bcs_service_api::{
    application::collaboration_draft::{CollaborationDraftService, DraftError},
    core::collaboration_draft::*,
    port::repo::collaboration_draft::{CollaborationDraftRepoPort, DraftRepoError},
};

pub async fn collaboration_draft_core_service_contract_tests(
    service: &dyn CollaborationDraftCoreService,
) {
    let id = &uuid::Uuid::new_v4().to_string();
    service
        .create(
            "owner",
            id,
            DraftContent {
                definition_yaml: "\r\n".into(),
                title: None,
            },
        )
        .await
        .unwrap();
    assert_eq!(
        service
            .read(id, 1)
            .await
            .unwrap()
            .definition_yaml,
        "\r\n"
    );
    assert!(matches!(
        service.metadata("other", id).await,
        Err(DraftCoreError::Forbidden)
    ));
    service.revoke("owner", id, 1).await.unwrap();
    assert!(matches!(
        service.read(id, 1).await,
        Err(DraftCoreError::Unavailable)
    ));
}

pub async fn collaboration_draft_service_contract_tests(service: &dyn CollaborationDraftService) {
    let id = &uuid::Uuid::new_v4().to_string();
    let saved = service
        .create(
            "owner",
            id,
            DraftContent {
                definition_yaml: "invalid: [".into(),
                title: None,
            },
        )
        .await
        .unwrap();
    let validation = service
        .validate(id, 1)
        .await
        .unwrap();
    assert_eq!(validation.content_hash, saved.content.content_hash);
    assert_eq!(validation.revision, 1);
    assert!(!validation.validation.valid);
    assert!(
        validation
            .validation
            .errors
            .iter()
            .any(|e| e.code == "YAML_PARSE")
    );
    assert!(matches!(
        service.reference("other", id, 1).await,
        Err(DraftError::Forbidden)
    ));
    service.delete("owner", id).await.unwrap();
    assert!(matches!(
        service
            .read(id, 1)
            .await,
        Err(DraftError::Unavailable)
    ));
}

pub async fn collaboration_draft_repo_port_contract_tests(repo: &dyn CollaborationDraftRepoPort) {
    let id = &uuid::Uuid::new_v4().to_string();
    let meta = DraftMetadata {
        draft_id: id.into(),
        owner_bot_id: "owner".into(),
        head_revision: 1,
        created_at: "now".into(),
        deleted: false,
    };
    let first = StoredDraftRevision {
        content: DraftRevision {
            draft_id: id.into(),
            revision: 1,
            definition_yaml: "\r\n".into(),
            title: None,
            content_hash: "test-hash".into(),
            size_bytes: 2,
            created_by: "owner".into(),
            created_at: "now".into(),
        },
        revoked: false,
    };
    repo.create(meta.clone(), first.clone()).await.unwrap();
    assert!(matches!(
        repo.create(meta, first.clone()).await,
        Err(DraftRepoError::Conflict)
    ));
    let mut second = first;
    second.content.revision = 2;
    repo.append("owner", 1, second.clone()).await.unwrap();
    assert!(matches!(
        repo.append("owner", 1, second).await,
        Err(DraftRepoError::Conflict)
    ));
    assert_eq!(repo.metadata(id).await.unwrap().unwrap().head_revision, 2);
    repo.revoke("owner", id, 1).await.unwrap();
    assert!(repo.revision(id, 1).await.unwrap().unwrap().revoked);
    repo.delete("owner", id).await.unwrap();
    assert!(repo.revision(id, 1).await.unwrap().is_none());
    assert!(repo.metadata(id).await.unwrap().unwrap().deleted);
}
