use super::*;

#[tokio::test]
async fn memory_fixed_loop_snapshot_round_trip_is_immutable() {
    snapshot_round_trip_is_immutable(&MemoryCollaborationStore::new()).await;
}

#[tokio::test]
async fn sqlite_fixed_loop_snapshot_round_trip_is_immutable() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    snapshot_round_trip_is_immutable(&MySqlCollaborationStore::sqlite(db, "test".into())).await;
}

#[tokio::test]
async fn memory_nested_loop_path_snapshot_round_trip_is_immutable() {
    nested_snapshot_round_trip_is_immutable(&MemoryCollaborationStore::new()).await;
}

#[tokio::test]
async fn sqlite_nested_loop_path_snapshot_round_trip_is_immutable() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    nested_snapshot_round_trip_is_immutable(&MySqlCollaborationStore::sqlite(db, "test".into())).await;
}

#[tokio::test]
async fn mysql_fixed_loop_snapshot_preserves_all_fields_in_one_write_and_read() {
    let snapshot = fixed_loop_snapshot_fixture();
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "test".into());
    store.save_run_snapshot(&test_run(), 7, &snapshot.definition, snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await.unwrap();
    let writes = db.executes.lock().await;
    assert_eq!(writes.len(), 1);
    let statement = &writes[0];
    assert!(statement.sql().contains("execution_plan_json, execution_plan_content_hash, execution_plan_compiler_version"));
    assert_eq!(statement.params().len(), 13);
    *db.snapshot_json.lock().await = Some(statement.params()[8].as_str().unwrap().into());
    let mut columns = db.snapshot_extra_columns.lock().await;
    for (index, name) in [(9, "resolved_participant_bindings_json"), (10, "execution_plan_json"), (11, "execution_plan_content_hash"), (12, "execution_plan_compiler_version")] {
        columns.insert(name.into(), statement.params()[index].clone());
    }
    drop(columns);
    drop(writes);
    let loaded = store.get_run_snapshot("sm-run-1").await.unwrap().unwrap();
    assert_snapshot_eq(&loaded, &snapshot);
}

#[tokio::test]
async fn mysql_nested_loop_path_snapshot_preserves_v2_fields_in_one_write_and_read() {
    let snapshot = nested_loop_path_snapshot_fixture();
    let db = Arc::new(RecordingDb::default());
    let store = MySqlCollaborationStore::new(db.clone(), "test".into());
    store.save_run_snapshot(&test_run(), 7, &snapshot.definition, snapshot.resolved_participant_bindings.as_ref(), snapshot.execution_plan.as_ref()).await.unwrap();
    let writes = db.executes.lock().await;
    let statement = &writes[0];
    *db.snapshot_json.lock().await = Some(statement.params()[8].as_str().unwrap().into());
    let mut columns = db.snapshot_extra_columns.lock().await;
    for (index, name) in [(9, "resolved_participant_bindings_json"), (10, "execution_plan_json"), (11, "execution_plan_content_hash"), (12, "execution_plan_compiler_version")] {
        columns.insert(name.into(), statement.params()[index].clone());
    }
    drop(columns);
    drop(writes);
    let loaded = store.get_run_snapshot("sm-run-1").await.unwrap().unwrap();
    assert_snapshot_eq(&loaded, &snapshot);
    let stored = loaded.execution_plan.unwrap();
    let nested = stored.plan.node_metadata["ln-stored-1"]
        .loop_path.as_ref().expect("v2 loop path");
    assert_eq!(nested.iter().map(|frame| frame.loop_id.as_str()).collect::<Vec<_>>(), ["outer", "rounds"]);
}

#[tokio::test]
async fn mysql_definition_upsert_and_get_use_009_definition_table() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb {
        definition_json: Mutex::new(Some(serde_json::to_string(&definition).unwrap())),
        definition_metadata_rows: Mutex::new(VecDeque::from([None])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    StateMachineDefinitionRepoPort::upsert(&store, definition.clone())
        .await
        .expect("upsert definition");
    let loaded = StateMachineDefinitionRepoPort::get(&store, &definition.id, definition.version)
        .await
        .expect("get definition")
        .expect("definition row");

    assert_eq!(loaded.id, definition.id);
    assert_eq!(loaded.version, definition.version);

    let transactions = db.transactions.lock().await;
    assert_eq!(transactions.len(), 1);
    let steps = &transactions[0];
    assert_eq!(steps.len(), 2);
    match &steps[0] {
        DbTransactionStep::Execute(stmt) => {
            assert!(stmt.sql().contains("bcs_collaboration_definition_blobs"));
            assert!(stmt.sql().contains("ON DUPLICATE KEY UPDATE"));
        }
        _ => panic!("expected blob execute step"),
    }
    match &steps[1] {
        DbTransactionStep::Execute(stmt) => {
            assert!(stmt.sql().contains("bcs_collaboration_definitions"));
            assert!(stmt.sql().contains("INSERT IGNORE"));
            assert!(!stmt.sql().contains("ON DUPLICATE KEY UPDATE"));
        }
        _ => panic!("expected definition execute step"),
    }

    let queries = db.queries.lock().await;
    assert_eq!(queries.len(), 3);
    assert!(queries[0].sql().contains("content_hash, blob_id"));
    assert!(queries[1].sql().contains("content_hash, blob_id"));
    assert!(queries[2].sql().contains("normalized_json"));
}

#[tokio::test]
async fn mysql_definition_upsert_with_yaml_records_yaml_source_format() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb {
        definition_metadata_rows: Mutex::new(VecDeque::from([None])),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    StateMachineDefinitionRepoPort::upsert_with_source_yaml(
        &store,
        definition,
        "name: source yaml".to_string(),
    )
    .await
    .expect("upsert definition with source YAML");

    let transactions = db.transactions.lock().await;
    let DbTransactionStep::Execute(statement) = &transactions[0][1] else {
        panic!("expected definition execute step");
    };
    assert_eq!(statement.params()[5], DbValue::from("yaml"));
    assert_eq!(statement.params()[8], DbValue::from("name: source yaml"));
}

#[tokio::test]
async fn mysql_binding_writes_group_version_and_denormalized_definition_snapshot() {
    let db = Arc::new(RecordingDb::with_definition(test_definition()));
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    store
        .bind_default_definition(
            "group-1",
            7,
            Some(CollaborationDefinitionRef {
                id: "sm_e2e_single".to_string(),
                version: 3,
            }),
            Some(BTreeMap::from([(
                "driver".to_string(),
                RuntimeParticipantBinding {
                    source: "manual".to_string(),
                    bot_ids: vec!["bot_sm_e2e_driver".to_string()],
                    extensions: Default::default(),
                },
            )])),
            true,
        )
        .await
        .expect("bind definition");

    let transactions = db.transactions.lock().await;
    assert_eq!(transactions.len(), 1);
    let steps = &transactions[0];
    assert_eq!(steps.len(), 2);
    match &steps[1] {
        DbTransactionStep::Execute(stmt) => {
            assert!(stmt.sql().contains("bcs_group_runtime_bindings"));
            assert!(stmt.sql().contains("ON DUPLICATE KEY UPDATE"));
            assert_eq!(stmt.params()[2], DbValue::from(7));
            assert_eq!(stmt.params()[3], DbValue::from(2_147_483_647_i32));
            assert_eq!(stmt.params()[4], DbValue::from("sm_e2e_single"));
            assert_eq!(stmt.params()[5], DbValue::from(3));
            assert_eq!(stmt.params()[6], DbValue::from("hash-from-definition"));
            assert_eq!(stmt.params()[7], DbValue::from("blob-from-definition"));
            assert_eq!(stmt.params()[8], DbValue::from(true));
            assert!(stmt.sql().contains("participant_bindings_json"));
            assert_eq!(
                stmt.params()[9],
                DbValue::from(
                    serde_json::to_string(&BTreeMap::from([(
                        "driver".to_string(),
                        RuntimeParticipantBinding {
                            source: "manual".to_string(),
                            bot_ids: vec!["bot_sm_e2e_driver".to_string()],
                            extensions: Default::default(),
                        },
                    )]))
                    .unwrap()
                    .as_str()
                )
            );
        }
        _ => panic!("expected execute step"),
    }
}

#[tokio::test]
async fn mysql_definition_snapshot_writes_run_definition_snapshot_table() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb::with_definition(definition.clone()));
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());
    let run = StateMachineRun {
        run_id: "sm-run-1".to_string(),
        root_run_id: Some("sm-run-1".to_string()),
        rerun_of: None,
        definition_id: definition.id.clone(),
        definition_version: definition.version,
        group_id: "group-1".to_string(),
        group_version: 7,
        session_id: "group-1:abcdef12".to_string(),
        session_activation_count: None,
        created_by: Some("tester".to_string()),
        status: StateMachineRunStatus::Running,
        input: json!({"question": "hello"}),
        opening_message_override: None,
        output: None,
        error: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
    };

    let resolved = BTreeMap::from([(
        "driver".to_string(),
        ResolvedParticipantBinding {
            source: "group_runtime_binding".to_string(),
            binding_source: Some("manual".to_string()),
            bot_ids: vec!["bot_sm_e2e_driver".to_string()],
            participants: vec![ResolvedParticipant {
                bot_id: "bot_sm_e2e_driver".to_string(),
                bcs_participant_role: ParticipantRole::Driver,
            }],
            extensions: Default::default(),
        },
    )]);
    store
        .save_run_snapshot(&run, 7, &definition, Some(&resolved), None)
        .await
        .expect("save snapshot");

    let executes = db.executes.lock().await;
    assert_eq!(executes.len(), 1);
    let stmt = &executes[0];
    assert!(
        stmt.sql()
            .contains("bcs_state_machine_definition_snapshots")
    );
    assert!(stmt.sql().contains("ON DUPLICATE KEY UPDATE"));
    assert!(stmt.sql().contains("env=env"));
    assert_eq!(stmt.params()[1], DbValue::from("sm-run-1"));
    assert_eq!(stmt.params()[4], DbValue::from(7));
    assert_eq!(stmt.params()[5], DbValue::from("sm_e2e_single"));
    assert!(stmt.sql().contains("resolved_participant_bindings_json"));
    assert_eq!(
        stmt.params()[9],
        DbValue::from(serde_json::to_string(&resolved).unwrap().as_str())
    );
}

#[tokio::test]
async fn mysql_definition_snapshot_reads_run_definition_snapshot_table() {
    let definition = test_definition();
    let db = Arc::new(RecordingDb {
        snapshot_json: Mutex::new(Some(serde_json::to_string(&definition).unwrap())),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db.clone(), "dev".to_string());

    let loaded = store
        .get_run_snapshot("sm-run-1")
        .await
        .expect("get snapshot")
        .expect("snapshot");

    assert_eq!(loaded.definition.id, "sm_e2e_single");
    assert_eq!(loaded.definition.version, 3);
    let queries = db.queries.lock().await;
    assert!(
        queries[0]
            .sql()
            .contains("bcs_state_machine_definition_snapshots")
    );
    assert_eq!(queries[0].params()[1], DbValue::from("sm-run-1"));
}

#[tokio::test]
async fn mysql_legacy_empty_snapshot_keeps_the_existing_missing_snapshot_result() {
    let db = Arc::new(RecordingDb {
        snapshot_json: Mutex::new(Some(String::new())),
        ..RecordingDb::default()
    });
    let store = MySqlCollaborationStore::new(db, "test".into());
    assert!(store.get_run_snapshot("legacy-run").await.unwrap().is_none());
}
