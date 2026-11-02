use super::*;

#[tokio::test]
async fn disconnected_candidates_are_indexed_scoped_and_bounded() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "test".into());
    store.disconnected_bot_nodes("worker", 1234, Some(("run", "node")), 1000).await.unwrap();
    let queries = db.queries.lock().await;
    assert_eq!(queries.len(), 1);
    assert!(queries[0].sql().contains("n.assignee_bot_id = ? AND n.status = 'running'"));
    assert!(queries[0].sql().contains("n.started_at_ms <= ?"));
    assert_eq!(queries[0].params(), &[DbValue::from("test"), DbValue::from("worker"), DbValue::from(1234u64),
        DbValue::from("run"), DbValue::from("run"), DbValue::from("node"), DbValue::from(64u64)]);
    assert!(MySqlCollaborationStore::new(Arc::new(AlwaysFailDb), "test".into()).disconnected_bot_nodes("worker", 1, None, 1).await.is_err());
}
