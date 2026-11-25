//! Commit-consistent Group metadata/labels, shared by every repository.
use std::sync::Arc;
use bcs_service_api::port::repo::{CommitGroupEventfulMutation, GroupEventfulMutation, GroupRepoPort};
use bcs_service_api::types::catalog_labels::CatalogLabels;
use bcs_service_api::{Group, GroupMutableFieldsPatch, GroupStrategy, ServiceError};
use serde_json::json;

pub async fn group_mutation_snapshot_contract_tests(repo: Arc<dyn GroupRepoPort>) {
    assert!(repo.read_detail_snapshot("missing").await.unwrap().is_none());
    let mut group = Group::new("snapshot-group", "driver", vec![]);
    group.group_strategy = GroupStrategy::ManagerWorker;
    let trial: CatalogLabels = serde_json::from_value(json!({"stage":["trial"]})).unwrap();
    let running: CatalogLabels = serde_json::from_value(json!({"stage":["running"]})).unwrap();
    repo.create_with_catalog_labels(group.clone(), Some(trial)).await.unwrap();
    let mut version = group.version;
    for (supplied, expected) in [
        (Some(running.clone()), running.clone()),
        (None, running),
        (Some(CatalogLabels::default()), CatalogLabels::default()),
    ] {
        let snapshot = repo.commit_eventful_mutation_with_snapshot(CommitGroupEventfulMutation {
            group_id: group.id.clone(), expected_version: version, mutated_at_ms: 1_800_000_000_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                label: Some(format!("version-{}", version+1)), catalog_labels: supplied, ..Default::default()
            }), event: None,
        }).await.unwrap();
        assert_eq!(snapshot.group.version, version+1);
        assert_eq!(snapshot.group.label, Some(format!("version-{}", version+1)));
        assert_eq!(snapshot.catalog_labels, expected);
        let detail = repo.read_detail_snapshot(&group.id).await.unwrap().unwrap();
        assert_eq!(detail.group.version, snapshot.group.version);
        assert_eq!(detail.group.label, snapshot.group.label);
        assert_eq!(detail.catalog_labels, snapshot.catalog_labels);
        let current = repo.read_card_snapshot(&group.id).await.unwrap().unwrap();
        assert_eq!(current.version, snapshot.group.version);
        assert_eq!(current.catalog_labels, snapshot.catalog_labels);
        version = snapshot.group.version;
    }
    let stale = repo.commit_eventful_mutation_with_snapshot(CommitGroupEventfulMutation {
        group_id: group.id.clone(), expected_version: version-1, mutated_at_ms: 1_800_000_001_000,
        mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
            label: Some("must not save".into()), ..Default::default()
        }), event: None,
    }).await;
    assert!(matches!(stale, Err(ServiceError::Conflict(_))));
    let current = repo.try_get(&group.id).await.unwrap().unwrap();
    assert_eq!(current.version, version);
    assert_eq!(current.label, Some(format!("version-{version}")));
    assert!(repo.read_card_snapshot(&group.id).await.unwrap().unwrap().catalog_labels.is_empty());
}
