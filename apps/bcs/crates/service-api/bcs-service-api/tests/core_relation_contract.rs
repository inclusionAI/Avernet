use bcs_service_api::{RelationCoreService, RelationEdge, ServiceError};
use bcs_test_support::NoopRelationCoreService;

fn sample_edge() -> RelationEdge {
    RelationEdge {
        from_id: "human_staff001".to_string(),
        to_id: "bot-1".to_string(),
        env: "dev".to_string(),
        kinds: 0,
        allow: 0,
        deny: 0,
        is_creator: true,
    }
}

#[tokio::test]
async fn noop_relation_queries_are_empty_and_mutations_are_benign() {
    let service = NoopRelationCoreService::default();

    service.upsert_edge(sample_edge()).await.unwrap();
    assert!(
        service
            .get_edge("human_staff001", "bot-1", "dev")
            .await
            .unwrap()
            .is_none()
    );
    service
        .delete_edge("human_staff001", "bot-1", "dev")
        .await
        .unwrap();
    service
        .ensure_owner_edges("human_staff001", "bot-1", "dev")
        .await
        .unwrap();
    service
        .add_friend_edges("bot-1", "bot-2", "dev")
        .await
        .unwrap();
    service
        .remove_friend_edges("bot-1", "bot-2", "dev")
        .await
        .unwrap();
    service
        .remove_all_friend_edges("bot-1", "dev")
        .await
        .unwrap();
    service
        .add_relation_edge("bot-1", "bot-3", "dev")
        .await
        .unwrap();
    assert!(
        service
            .list_friends_via_relation("bot-1", "dev")
            .await
            .unwrap()
            .is_empty()
    );
}

#[tokio::test]
async fn noop_relation_counted_owner_edges_fail_closed() {
    let service = NoopRelationCoreService::default();

    let err = service
        .ensure_owner_edges_counted("human_staff001", "bot-1", "dev")
        .await
        .unwrap_err();
    assert!(matches!(err, ServiceError::InternalError(_)));
}
