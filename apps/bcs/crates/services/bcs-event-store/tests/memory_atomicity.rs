mod common;
use bcs_event_store::MemoryEventStore;
use bcs_service_api::port::repo::{EventRepoPort, EventRepoError};
use bcs_service_api::types::EventSubscriptionStatus;

#[path = "memory_delivery_regressions.rs"]
mod delivery_regressions;

#[tokio::test]
async fn coverage_memory_provisioning_rolls_back_activation_and_sequence_on_commit_failure() {
    let repo = MemoryEventStore::new();
    let mut pending = common::subscription("pending"); pending.subscription.status = EventSubscriptionStatus::Pending;
    repo.create_subscription(pending).await.unwrap();
    let first = common::append("first", "first-key", "group.created");
    let second = common::append("second", "second-key", "group.updated");
    let subscriptions = ["pending".into()];
    let error = repo.finalize_group_provisioning(common::GROUP_ID, &subscriptions,
        &[first.clone(), second.clone()], 1_755_561_600_005, common::ENV,
        || Err(EventRepoError::Storage("business write failed".into()))).await.unwrap_err();
    assert!(matches!(error, EventRepoError::Storage(_)));
    assert_eq!(repo.get_subscription("pending", common::ENV).await.unwrap().unwrap().0.status, EventSubscriptionStatus::Pending);
    assert!(repo.get_event("first", common::ENV).await.unwrap().is_none());
    assert!(repo.get_event("second", common::ENV).await.unwrap().is_none());
    let committed = repo.finalize_group_provisioning(common::GROUP_ID, &subscriptions,
        &[first, second], 1_755_561_600_005, common::ENV, || Ok(())).await.unwrap();
    assert_eq!(committed[0].event.envelope.stream.sequence, 1);
    assert_eq!(committed[1].event.envelope.stream.sequence, 2);
    assert_eq!(repo.get_subscription("pending", common::ENV).await.unwrap().unwrap().0.status, EventSubscriptionStatus::Active);
    assert!(matches!(repo.finalize_group_provisioning(common::GROUP_ID, &subscriptions,
        &[], 1_755_561_600_006, common::ENV, || panic!("invalid activation must not commit")).await, Err(EventRepoError::Conflict(_))));
    assert!(matches!(repo.finalize_group_provisioning(common::GROUP_ID, &["missing".into()],
        &[], 1_755_561_600_006, common::ENV, || panic!("missing subscription must not commit")).await, Err(EventRepoError::NotFound(_))));
}

#[tokio::test]
async fn coverage_memory_event_batch_and_group_deletion_do_not_publish_partial_mutations() {
    let repo = MemoryEventStore::new();
    repo.create_subscription(common::subscription("active")).await.unwrap();
    let first = common::append("batch-first", "batch-first", "group.updated");
    let second = common::append("batch-second", "batch-second", "group.updated");
    assert!(repo.commit_business_mutations(&[], || panic!("empty batch must not commit")).await.is_err());
    assert!(repo.commit_group_mutation("wrong-group", &first, || panic!("wrong scope must not commit")).await.is_err());
    assert!(repo.commit_business_mutations(&[first.clone(), second.clone()],
        || Err(EventRepoError::Storage("rollback batch".into()))).await.is_err());
    assert!(repo.get_event("batch-first", common::ENV).await.unwrap().is_none());
    let committed = repo.commit_business_mutations(&[first, second], || Ok(())).await.unwrap();
    assert_eq!(committed.iter().map(|r| r.event.envelope.stream.sequence).collect::<Vec<_>>(), [1, 2]);
    assert!(repo.commit_group_deletion(common::GROUP_ID, common::ENV, 10,
        || Err(EventRepoError::Storage("rollback deletion".into()))).await.is_err());
    assert_eq!(repo.get_subscription("active", common::ENV).await.unwrap().unwrap().0.status, EventSubscriptionStatus::Active);
    repo.commit_group_deletion(common::GROUP_ID, common::ENV, 11, || Ok(())).await.unwrap();
    assert_eq!(repo.get_subscription("active", common::ENV).await.unwrap().unwrap().0.status, EventSubscriptionStatus::Disabled);
    let next = repo.append_event(common::append("after-delete", "after-delete", "group.updated")).await.unwrap();
    assert!(next.fanout_target_ids.is_empty(), "deleted Group subscriptions must not receive new Events");
    assert_eq!(next.event.envelope.stream.sequence, 3);
    assert!(repo.commit_group_deletion("", common::ENV, 11, || panic!("invalid Group must not commit")).await.is_err());
}
