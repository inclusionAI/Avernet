use super::*;
use bcs_domain::{DeliveryType, MessageOwnerFilter, MessageQuery, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_domain::message_delivery::{DeliveryFlowKind, MessageDeliveryStatus as S};
use bcs_service_api::port::repo::MessageRepoPort;
use bcs_service_api::port::repo::message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget, DeliveryCompareAndSet, MessageDeliveryRepoPort};

async fn contract<R: MessageRepoPort + MessageDeliveryRepoPort>(repo: &R) {
    let session = "contract-group:abcd1234";
    let cases: Vec<(Vec<(S, bool)>, bool)> = vec![
        (vec![(S::Cancelled, false)], false),
        (vec![(S::Cancelled, false), (S::Queued, false)], true),
        (vec![(S::Cancelled, false), (S::Cancelled, true)], true),
        (vec![(S::Cancelled, false), (S::DiscardedContext, false)], false),
        (vec![(S::Cancelled, false), (S::Completed, true)], true),
        (vec![(S::Cancelled, false), (S::Unknown, false)], true),
        (vec![(S::Cancelled, false), (S::Bound, false)], true),
        (vec![(S::Expired, false)], true),
        (vec![(S::Cancelled, false), (S::RejectedCapacity, false)], false),
        (vec![], true), // Legacy canonical message without managed deliveries.
    ];
    let mut expected = Vec::new();
    for (index, (states, visible)) in cases.into_iter().enumerate() {
        let id = format!("message-{index}");
        let admitted = repo.admit(AdmitMessageDeliveries {
            display_message: None, message_id: id.clone(),
            message: NewMessage {
                group_id: "contract-group".into(), session_id: session.into(),
                sender_id: "human_a".into(), sender_type: SenderType::Human,
                message_type: "chat".into(), content: serde_json::json!({"text":"duplicate content"}),
                client_msg_id: Some(format!("client-{index}")), owner_bot_id: None,
                visibility_domain: MessageVisibilityDomain::Chat, audience: None,
                created_at: index as u64 + 1, run_id: String::new(),
            },
            flow_kind: DeliveryFlowKind::Group, now_ms: 1, expire_at_ms: None, event: None,
            targets: states.iter().enumerate().map(|(target, _)| DeliveryAdmissionTarget {
                target_bot_id: format!("bot-{target}"), kind: DeliveryType::Send,
                max_queued: 100, semantic_projection_json: serde_json::json!({"version":1}),
                run_id: None, rejection: None,
            }).collect(),
        }).await.unwrap();
        let changes = admitted.deliveries.into_iter().zip(states).map(|(mut delivery, (status, sent))| {
            let expected_state_version = delivery.state.state_version;
            delivery.state.status = status;
            delivery.state.may_have_been_sent = sent;
            delivery.state.state_version += 1;
            DeliveryCompareAndSet { expected_state_version, delivery }
        }).collect();
        repo.commit_transition(changes, None).await.unwrap();
        assert!(repo.get_message_by_id(session, &id).await.unwrap().is_some(), "raw message must be retained");
        if visible { expected.push(id); }
    }
    expected.reverse();
    let mut found = Vec::new();
    let mut before = None;
    loop {
        let page = repo.list_session_history(session, MessageOwnerFilter::Any, None, None, before, 2).await.unwrap();
        found.extend(page.messages.iter().map(|m| m.message_id.clone()));
        if !page.has_more { break; }
        assert_eq!(page.messages.len(), 2, "filter before LIMIT, never return an empty middle page");
        before = page.next_cursor;
    }
    assert_eq!(found, expected);
    let page = repo.query_messages(MessageQuery {
        group_id: "contract-group".into(), session_id: session.into(), cursor: None, limit: 100,
        keyword: None, sender_id: None, message_type: None, owner_filter: MessageOwnerFilter::Any,
        time_range: None, visible_from_seq: None, human_view: None,
    }).await.unwrap();
    assert_eq!(page.messages.iter().map(|m| m.message_id.clone()).collect::<Vec<_>>(), expected);
}

#[tokio::test]
async fn memory_cancelled_queue_messages_do_not_reappear_after_refresh() {
    contract(&MemoryMessageRepo::new()).await;
}

#[tokio::test]
async fn sqlite_cancelled_queue_messages_do_not_reappear_after_refresh() {
    contract(&MySqlMessageStore::sqlite(sqlite_db().await, "dev".into())).await;
}

struct QueryProbe {
    inner: Arc<dyn DbPlugin>,
    queries: std::sync::Mutex<Vec<DbStatement>>,
}

#[async_trait::async_trait]
impl DbPlugin for QueryProbe {
    async fn query(&self, statement: DbStatement) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbRow>> {
        self.queries.lock().unwrap().push(statement.clone());
        self.inner.query(statement).await
    }
    async fn execute(&self, _: DbStatement) -> bcs_db_api::DbResult<bcs_db_api::DbExecuteResult> { panic!("history is read only") }
    async fn transaction(&self, _: Vec<bcs_db_api::DbTransactionStep>) -> bcs_db_api::DbResult<Vec<bcs_db_api::DbTransactionStepResult>> { panic!("history is read only") }
    async fn health_check(&self) -> bcs_db_api::DbResult<bcs_db_api::DbHealth> { self.inner.health_check().await }
}

#[tokio::test]
async fn sqlite_cancellation_history_is_one_indexed_query_and_storage_failures_propagate() {
    let db = sqlite_db().await;
    contract(&MySqlMessageStore::sqlite(db.clone(), "dev".into())).await;
    let probe = Arc::new(QueryProbe { inner: db.clone(), queries: Default::default() });
    let reader = MySqlMessageStore::sqlite(probe.clone(), "dev".into());
    reader.list_session_history("contract-group:abcd1234", MessageOwnerFilter::Any, None, None, None, 100).await.unwrap();
    let queries = probe.queries.lock().unwrap().clone();
    assert_eq!(queries.len(), 1, "no per-message DB lookups");
    let plan = db.query(DbStatement::with_params(format!("EXPLAIN QUERY PLAN {}", queries[0].sql()), queries[0].params().to_vec())).await.unwrap();
    let detail = plan.iter().map(|row| bcs_db_api::db_get_column::<String>(row, "detail").unwrap()).collect::<Vec<_>>().join("; ");
    // Either per-message index serves the aggregate; the Session-scoped one
    // (043) matches all three equality columns of the correlated subquery.
    assert_eq!(detail.matches("idx_delivery_source").count() + detail.matches("idx_delivery_session_message").count(), 1,
        "the per-message delivery aggregate must be indexed: {detail}");
    assert!(!detail.contains("SCAN history_delivery"), "{detail}");
    db.execute(DbStatement::new("DROP TABLE bcs_message_deliveries")).await.unwrap();
    assert!(reader.list_session_history("contract-group:abcd1234", MessageOwnerFilter::Any, None, None, None, 100).await.is_err());
}
