use super::*;

#[tokio::test]
async fn mysql_progression_query_is_bounded_and_propagates_database_failure() {
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "test".into());
    store.list_running_runs(Some("run-cursor"), 7).await.unwrap();
    let queries = db.queries.lock().await;
    let query = &queries[0];
    assert!(query.sql().contains("env = ? AND status = 'running' AND record_status = 'active'"));
    assert!(query.sql().contains("run_id > ? ORDER BY run_id ASC LIMIT ?"));
    assert_eq!(query.params(), &[DbValue::from("test"), DbValue::from("run-cursor"), DbValue::from(7u64)]);
    assert!(MySqlCollaborationStore::new(Arc::new(AlwaysFailDb), "test".into())
        .list_running_runs(None, 7).await.is_err());
}

#[tokio::test]
async fn mysql_fixed_loop_snapshot_rejects_partial_or_malformed_persisted_plan() {
    let snapshot = fixed_loop_snapshot_fixture();
    for value in [None, Some("{invalid"), Some("null")] {
        let db = Arc::new(RecordingDb {
            snapshot_json: Mutex::new(Some(serde_json::to_string(&snapshot.definition).unwrap())),
            ..RecordingDb::default()
        });
        if let Some(value) = value {
            let mut columns = db.snapshot_extra_columns.lock().await;
            columns.insert("execution_plan_json".into(), DbValue::from(value));
            columns.insert("execution_plan_content_hash".into(), DbValue::from("0".repeat(64)));
            columns.insert("execution_plan_compiler_version".into(), DbValue::from("bcs.fixed-loop.compiler/v1"));
        }
        let store = MySqlCollaborationStore::new(db, "test".into());
        assert!(store.get_run_snapshot("sm-run-1").await.is_err());
    }
}

#[tokio::test]
async fn fixed_loop_snapshot_write_failure_is_propagated_and_missing_plan_is_rejected() {
    let snapshot = fixed_loop_snapshot_fixture();
    let failed = MySqlCollaborationStore::new(Arc::new(AlwaysFailDb), "test".into());
    assert!(failed.save_run_snapshot(&test_run(), 7, &snapshot.definition,
        snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await.is_err());
    let memory = MemoryCollaborationStore::new();
    assert!(memory.save_run_snapshot(&test_run(), 7, &snapshot.definition,
        snapshot.resolved_participant_bindings.as_ref(), None).await.is_err());
    assert!(memory.get_run_snapshot("sm-run-1").await.unwrap().is_none());
}

#[tokio::test]
async fn mysql_definition_upsert_rejects_same_id_version_with_different_content() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb {
        definition_metadata_rows: Mutex::new(VecDeque::from([Some((
            "existing-different-hash".to_string(),
            Some("existing-blob".to_string()),
        ))])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    let err = StateMachineDefinitionRepoPort::upsert(&store, definition)
        .await
        .expect_err("different content should conflict");

    assert!(
        err.to_string()
            .contains("already exists with different content")
    );
    assert!(db.transactions.lock().await.is_empty());
}

#[tokio::test]
async fn mysql_definition_and_snapshot_reads_reject_invalid_persisted_json() {
    let db = Arc::new(RecordingDb {
        definition_json: Mutex::new(Some("{invalid".to_string())),
        snapshot_json: Mutex::new(Some("{invalid".to_string())),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

    assert!(
        StateMachineDefinitionRepoPort::get(&store, "invalid", 1)
            .await
            .is_err()
    );
    assert!(store.get_run_snapshot("invalid-run").await.is_err());
}

#[tokio::test]
async fn mysql_binding_rejects_missing_definition_metadata() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb {
        definition_metadata_rows: Mutex::new(VecDeque::from([None])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

    let error = store
        .bind_default_definition(
            "group-1",
            1,
            Some(CollaborationDefinitionRef {
                id: definition.id,
                version: definition.version,
            }),
            None,
            false,
        )
        .await
        .expect_err("missing definition metadata must reject binding");
    assert!(error.to_string().contains("not found for binding"));
}

#[tokio::test]
async fn mysql_runtime_rejects_invalid_persisted_opening_message_override_json() {
    let run = test_run();
    let db = Arc::new(RecordingDb {
        runtime_run_row: Mutex::new(Some(run_row_with_opening_json(
            &run,
            DbValue::from("{invalid"),
        ))),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

    let error = store
        .get_run(&run.run_id)
        .await
        .expect_err("invalid persisted override must fail the read");
    assert!(error.to_string().contains("opening_message_override_json"));
}

#[tokio::test]
async fn mysql_runtime_cas_failures_return_false() {
    let db = Arc::new(RecordingDb {
        execute_affected_rows: Mutex::new(VecDeque::from([0, 0, 0, 0, 0, 0])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

    let completed = store
        .complete_node_attempt(
            "sm-run-1",
            "answer",
            1,
            "complete".to_string(),
            "done".to_string(),
            None,
            2_000,
        )
        .await
        .expect("complete node");
    let artifact_recorded = store
        .record_node_artifact_if_running(
            "sm-run-1",
            "answer",
            1,
            "candidate".to_string(),
        )
        .await
        .expect("record node artifact");
    let human_response_recorded = store
        .record_human_response_if_running(
            "sm-run-1",
            "review",
            1,
            "response".to_string(),
            "human_1001".to_string(),
        )
        .await
        .expect("record Human response");
    let failed = store
        .fail_node_attempt("sm-run-1", "answer", 1, "error".to_string(), 2_000)
        .await
        .expect("fail node");
    let retry_scheduled = store
        .schedule_node_retry("sm-run-1", "answer", 1, 2)
        .await
        .expect("schedule retry");
    let run_updated = store
        .update_run_status(
            "sm-run-1",
            StateMachineRunStatus::Completed,
            Some("done".to_string()),
            None,
            2_100,
            Some(2_100),
        )
        .await
        .expect("update run");

    assert!(!completed);
    assert!(!artifact_recorded);
    assert!(!human_response_recorded);
    assert!(!failed);
    assert!(!retry_scheduled);
    assert!(!run_updated);
}

#[tokio::test]
async fn mysql_runtime_register_delivery_alias_errors_when_correlation_missing() {
    let db = Arc::new(RecordingDb {
        transaction_affected_rows: Mutex::new(VecDeque::from([0, 0])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "dev".to_string());

    let err = store
        .register_delivery_alias("missing-delivery", "bot-run-1".to_string())
        .await
        .expect_err("missing correlation should error");

    assert!(err.to_string().contains("delivery correlation not found"));
}

#[tokio::test]
async fn memory_runtime_rejects_updates_for_missing_runs_nodes_and_correlations() {
    let store = MemoryCollaborationStore::new();

    assert!(
        store
            .update_run_status(
                "missing-run",
                StateMachineRunStatus::Failed,
                None,
                Some("failed".to_string()),
                2_000,
                Some(2_000),
            )
            .await
            .is_err()
    );
    assert!(
        store
            .mark_node_running(
                "missing-run",
                "missing-node",
                0,
                "delivery-1".to_string(),
                1_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .register_delivery_alias("missing-delivery", "bot-run-1".to_string())
            .await
            .is_err()
    );

    store
        .create_run(test_run(), Vec::new())
        .await
        .expect("seed run without nodes");
    assert!(
        store
            .mark_node_running(
                "sm-run-1",
                "missing-node",
                0,
                "delivery-1".to_string(),
                1_000,
            )
            .await
            .is_err()
    );
}

#[tokio::test]
async fn mysql_runtime_propagates_database_failures_across_repository_operations() {
    let store = MySqlCollaborationStore::new(Arc::new(AlwaysFailDb), "dev".to_string());
    let definition = test_definition();
    let run = test_run();
    let node = test_node();

    assert!(
        StateMachineDefinitionRepoPort::upsert(&store, definition.clone())
            .await
            .is_err()
    );
    assert!(
        StateMachineDefinitionRepoPort::get(&store, &definition.id, definition.version)
            .await
            .is_err()
    );
    assert!(
        store
            .save_run_snapshot(&run, run.group_version, &definition, None, None)
            .await
            .is_err()
    );
    assert!(store.get_run_snapshot(&run.run_id).await.is_err());
    assert!(
        GroupRuntimeBindingRepoPort::get(&store, &run.group_id)
            .await
            .is_err()
    );
    assert!(
        store
            .bind_default_definition(&run.group_id, run.group_version, None, None, false)
            .await
            .is_err()
    );
    assert!(
        store
            .bind_default_definition_if_current(
                &run.group_id,
                run.group_version,
                None,
                None,
                None,
                false,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .create_run(run.clone(), vec![node.clone()])
            .await
            .is_err()
    );
    assert!(store.get_run(&run.run_id).await.is_err());
    assert!(store.get_run_by_session_id(&run.session_id).await.is_err());
    assert!(store.list_node_runs(&run.run_id).await.is_err());
    assert!(
        store
            .get_node_run(&run.run_id, &node.node_id)
            .await
            .is_err()
    );
    assert!(
        store
            .mark_node_running(
                &run.run_id,
                &node.node_id,
                node.attempt,
                "delivery-failed".to_string(),
                1_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .mark_node_running_if_run_active(
                &run.run_id,
                &node.node_id,
                node.attempt,
                "delivery-cas-failed".to_string(),
                1_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .mark_human_node_running_if_run_active(MarkHumanNodeRunningCommand {
                run_id: run.run_id.clone(),
                node_id: "review".to_string(),
                attempt: 0,
                started_at_ms: 1_000,
                timeout_deadline_ms: 61_000,
            })
            .await
            .is_err()
    );
    assert!(
        store
            .record_node_artifact_if_running(
                &run.run_id,
                &node.node_id,
                node.attempt,
                "artifact".to_string(),
            )
            .await
            .is_err()
    );
    assert!(
        store
            .record_human_response_if_running(
                &run.run_id,
                "review",
                0,
                "response".to_string(),
                "human_1001".to_string(),
            )
            .await
            .is_err()
    );
    assert!(
        store
            .complete_node_attempt(
                &run.run_id,
                &node.node_id,
                node.attempt,
                "complete".to_string(),
                "artifact".to_string(),
                None,
                2_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .fail_node_attempt(
                &run.run_id,
                &node.node_id,
                node.attempt,
                "failed".to_string(),
                2_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .schedule_node_retry(&run.run_id, &node.node_id, node.attempt, node.attempt + 1)
            .await
            .is_err()
    );
    assert!(
        store
            .skip_node(&run.run_id, &node.node_id, 2_000)
            .await
            .is_err()
    );
    assert!(
        store
            .update_run_status(
                &run.run_id,
                StateMachineRunStatus::Failed,
                None,
                Some("failed".to_string()),
                2_000,
                Some(2_000),
            )
            .await
            .is_err()
    );
    assert!(
        store
            .upsert_delivery_correlation(test_correlation())
            .await
            .is_err()
    );
    assert!(
        store
            .register_delivery_alias("delivery-1", "bot-run-1".to_string())
            .await
            .is_err()
    );
    assert!(
        store
            .lookup_delivery_correlation("delivery-1")
            .await
            .is_err()
    );
    assert!(
        store
            .list_expired_running_node_runs(10_000, 0, 10)
            .await
            .is_err()
    );
    assert!(
        store
            .append_event(
                &run.run_id,
                Some(&node.node_id),
                Some(node.attempt),
                "test.event",
                json!({"ok": true}),
                2_000,
            )
            .await
            .is_err()
    );
    assert!(
        store
            .list_events_by_run_and_type(&run.run_id, "test.event")
            .await
            .is_err()
    );
    assert!(
        store
            .list_events_by_run_node_and_type(&run.run_id, &node.node_id, "test.event")
            .await
            .is_err()
    );
}

#[tokio::test]
async fn mysql_alias_lookup_propagates_second_query_failure() {
    let store =
        MySqlCollaborationStore::new(Arc::new(AliasLookupFailDb), "dev".to_string());

    assert!(
        store
            .lookup_delivery_correlation("bot-run-1")
            .await
            .is_err()
    );
}
