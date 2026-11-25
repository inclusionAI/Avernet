    use super::*;
    use bcs_db_api::DbPlugin;
    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_service_api::{
        CollaborationTemplateRepoPort, StateMachineDefinitionRepoPort, StateMachineRunRepoPort,
    };

    async fn sqlite_template_repo()
    -> Result<(Arc<dyn DbPlugin>, DbCollaborationTemplateRepo), Box<dyn std::error::Error>> {
        let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new()?);
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_collaboration_templates (
                env TEXT NOT NULL,
                template_id TEXT NOT NULL,
                visibility TEXT NOT NULL,
                priority INTEGER NOT NULL,
                record_status TEXT NOT NULL,
                PRIMARY KEY (env, template_id)
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_collaboration_template_contents (
                env TEXT NOT NULL,
                template_id TEXT NOT NULL,
                lang TEXT NOT NULL,
                yaml_text TEXT NOT NULL,
                record_status TEXT NOT NULL,
                PRIMARY KEY (env, template_id, lang)
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_collaboration_template_tags (
                env TEXT NOT NULL,
                template_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (env, template_id, tag)
            )",
        ))
        .await?;

        Ok((db.clone(), DbCollaborationTemplateRepo::new(db, "dev")))
    }

    async fn sqlite_state_machine_store()
    -> Result<(Arc<dyn DbPlugin>, MySqlCollaborationStore), Box<dyn std::error::Error>> {
        let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new()?);
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_group_sessions (
                env TEXT NOT NULL,
                session_id TEXT NOT NULL,
                group_id TEXT NOT NULL DEFAULT 'group',
                session_kind TEXT NOT NULL DEFAULT 'chat',
                status TEXT NOT NULL DEFAULT 'running',
                input TEXT DEFAULT NULL,
                output TEXT DEFAULT NULL,
                error_message TEXT DEFAULT NULL,
                activation_count INTEGER NOT NULL DEFAULT 1,
                completed_at INTEGER DEFAULT NULL,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(env, session_id)
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_state_machine_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                env TEXT NOT NULL,
                run_id TEXT NOT NULL,
                root_run_id TEXT DEFAULT NULL,
                rerun_of TEXT DEFAULT NULL,
                definition_id TEXT NOT NULL,
                definition_version INTEGER NOT NULL,
                group_id TEXT NOT NULL,
                group_version INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                session_activation_count INTEGER DEFAULT NULL,
                created_by TEXT DEFAULT NULL,
                status TEXT NOT NULL,
                input_json TEXT DEFAULT NULL,
                opening_message_override_json TEXT DEFAULT NULL,
                output_text TEXT DEFAULT NULL,
                error_message TEXT DEFAULT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                completed_at_ms INTEGER DEFAULT NULL,
                record_status TEXT NOT NULL DEFAULT 'active',
                UNIQUE(env, run_id),
                UNIQUE(env, rerun_of)
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_state_machine_definition_snapshots (
                env TEXT NOT NULL,
                run_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                group_version INTEGER NOT NULL,
                definition_id TEXT NOT NULL,
                definition_version INTEGER NOT NULL,
                definition_content_hash TEXT NOT NULL,
                snapshot_blob_id TEXT DEFAULT NULL,
                snapshot_json TEXT DEFAULT NULL,
                execution_plan_json TEXT DEFAULT NULL,
                execution_plan_content_hash TEXT DEFAULT NULL,
                execution_plan_compiler_version TEXT DEFAULT NULL,
                resolved_participant_bindings_json TEXT DEFAULT NULL,
                source_format TEXT NOT NULL DEFAULT 'yaml',
                UNIQUE(env, run_id)
            )",
        ))
        .await?;
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_state_machine_node_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                env TEXT NOT NULL,
                run_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                node_timeout_ms INTEGER DEFAULT NULL,
                timeout_deadline_ms INTEGER DEFAULT NULL,
                max_attempts INTEGER NOT NULL DEFAULT 1,
                assignee_bot_id TEXT NOT NULL,
                outcome TEXT DEFAULT NULL,
                responded_by TEXT DEFAULT NULL,
                delivery_request_id TEXT DEFAULT NULL,
                bot_delivery_run_id TEXT DEFAULT NULL,
                artifact_text TEXT DEFAULT NULL,
                runtime_phase TEXT DEFAULT NULL,
                error_message TEXT DEFAULT NULL,
                started_at_ms INTEGER DEFAULT NULL,
                completed_at_ms INTEGER DEFAULT NULL,
                record_status TEXT NOT NULL DEFAULT 'active',
                UNIQUE(env, run_id, node_id)
            )",
        ))
        .await?;

        Ok((
            db.clone(),
            MySqlCollaborationStore::sqlite(db, "dev".to_string()),
        ))
    }

    #[tokio::test]
    async fn sqlite_store_marks_node_running_with_cas_sql() -> Result<(), Box<dyn std::error::Error>>
    {
        let (db, store) = sqlite_state_machine_store().await?;
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_group_sessions (env, session_id) VALUES (?, ?)",
            vec![DbValue::from("dev"), DbValue::from("session")],
        ))
        .await?;
        let run = StateMachineRun {
            run_id: "run-sqlite-cas".to_string(),
            root_run_id: Some("run-sqlite-cas".to_string()),
            rerun_of: None,
            definition_id: "definition".to_string(),
            definition_version: 1,
            group_id: "group".to_string(),
            group_version: 1,
            session_id: "session".to_string(),
            session_activation_count: None,
            created_by: None,
            status: StateMachineRunStatus::Running,
            input: serde_json::Value::Null,
            opening_message_override: None,
            output: None,
            error: None,
            created_at: 1_000,
            updated_at: 1_000,
            completed_at: None,
        };
        let node = StateMachineNodeRun {
            run_id: run.run_id.clone(),
            node_id: "answer".to_string(),
            status: StateMachineNodeStatus::Pending,
            attempt: 0,
            node_timeout_ms: Some(120_000),
            timeout_deadline_ms: None,
            max_attempts: 2,
            assignee_bot_id: Some("bot-a".to_string()),
            outcome: None,
            responded_by: None,
            delivery_request_id: None,
            bot_delivery_run_id: None,
            artifact_text: None,
            error: None,
            started_at: None,
            completed_at: None,
        };
        assert!(
            store
                .create_run_if_session_idle(run, vec![node])
                .await?
        );
        assert!(
            !store
                .create_run_if_session_idle(
                    StateMachineRun {
                        run_id: "run-sqlite-concurrent".to_string(),
                        root_run_id: Some("run-sqlite-concurrent".to_string()),
                        rerun_of: None,
                        definition_id: "definition".to_string(),
                        definition_version: 1,
                        group_id: "group".to_string(),
                        group_version: 1,
                        session_id: "session".to_string(),
                        session_activation_count: None,
                        created_by: None,
                        status: StateMachineRunStatus::Running,
                        input: serde_json::Value::Null,
                        opening_message_override: None,
                        output: None,
                        error: None,
                        created_at: 1_001,
                        updated_at: 1_001,
                        completed_at: None,
                    },
                    Vec::new(),
                )
                .await?
        );

        let marked = store
            .mark_node_running_if_run_active(
                "run-sqlite-cas",
                "answer",
                0,
                "delivery-1".to_string(),
                2_000,
            )
            .await?;

        assert!(marked);
        let node = store
            .get_node_run("run-sqlite-cas", "answer")
            .await?
            .expect("node exists");
        assert_eq!(node.status, StateMachineNodeStatus::Running);
        assert_eq!(node.delivery_request_id.as_deref(), Some("delivery-1"));
        assert_eq!(node.started_at, Some(2_000));
        assert_eq!(node.timeout_deadline_ms, Some(122_000));

        let recorded = store
            .record_node_artifact_if_running("run-sqlite-cas", "answer", 0, "candidate".to_string())
            .await?;
        assert!(recorded);
        let node = store
            .get_node_run("run-sqlite-cas", "answer")
            .await?
            .expect("node exists");
        assert_eq!(node.status, StateMachineNodeStatus::Running);
        assert_eq!(node.artifact_text.as_deref(), Some("candidate"));

        Ok(())
    }

    #[tokio::test]
    async fn sqlite_store_round_trips_and_rejects_invalid_run_opening_message_overrides()
    -> Result<(), Box<dyn std::error::Error>> {
        let (db, store) = sqlite_state_machine_store().await?;
        let opening_messages = vec![
            None,
            Some(OpeningMessage::Text("Run {{bcs.run_id}}".to_string())),
            Some(serde_json::from_value(serde_json::json!({
                "type": "card",
                "component": "partnerCard.OneShotSummary",
                "params": {"runId": "{{bcs.run_id}}"}
            }))?),
            Some(serde_json::from_value(serde_json::json!({
                "type": "panel",
                "component": "partnerPanel.OneShotRunView",
                "params": {"runId": "{{bcs.run_id}}"},
                "tab": {"title": "One Shot", "closable": true}
            }))?),
        ];

        for (index, opening_message_override) in opening_messages.into_iter().enumerate() {
            let run_id = format!("run-opening-{index}");
            let run = StateMachineRun {
                run_id: run_id.clone(),
                root_run_id: Some(run_id.clone()),
                rerun_of: None,
                definition_id: "definition".to_string(),
                definition_version: 1,
                group_id: "group".to_string(),
                group_version: 1,
                session_id: format!("session-opening-{index}"),
                session_activation_count: None,
                created_by: None,
                status: StateMachineRunStatus::Running,
                input: serde_json::Value::Null,
                opening_message_override: opening_message_override.clone(),
                output: None,
                error: None,
                created_at: index as u64 + 1,
                updated_at: index as u64 + 1,
                completed_at: None,
            };
            store.create_run(run, Vec::new()).await?;
            assert_eq!(
                store
                    .get_run(&run_id)
                    .await?
                    .expect("round-tripped run")
                    .opening_message_override,
                opening_message_override
            );
        }

        db.execute(DbStatement::with_params(
            "UPDATE bcs_state_machine_runs \
             SET opening_message_override_json = ? WHERE env = ? AND run_id = ?",
            vec![
                DbValue::from("{invalid"),
                DbValue::from("dev"),
                DbValue::from("run-opening-0"),
            ],
        ))
        .await?;
        let error = store
            .get_run("run-opening-0")
            .await
            .expect_err("invalid persisted override must fail the read");
        assert!(error.to_string().contains("opening_message_override_json"));
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_one_shot_rerun_atomically_inherits_source_opening_message_override()
    -> Result<(), Box<dyn std::error::Error>> {
        let (db, store) = sqlite_state_machine_store().await?;
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_group_sessions \
             (env, session_id, group_id, session_kind, status, activation_count) \
             VALUES (?, ?, ?, 'chat', 'running', 1)",
            vec![
                DbValue::from("dev"),
                DbValue::from("session-one-shot-rerun"),
                DbValue::from("group"),
            ],
        ))
        .await?;
        let definition: CollaborationDefinition = serde_yaml::from_str(
            r#"
api_version: bcs.collaboration/v1
id: definition
version: 1
name: One-Shot Rerun Definition
runtime:
  kind: chat
"#,
        )?;
        let opening_message: OpeningMessage = serde_json::from_value(serde_json::json!({
            "type": "panel",
            "component": "partnerPanel.OneShotRunView",
            "params": {"runId": "{{bcs.run_id}}"}
        }))?;
        let source = StateMachineRun {
            run_id: "run-one-shot-source".to_string(),
            root_run_id: Some("run-one-shot-source".to_string()),
            rerun_of: None,
            definition_id: "definition".to_string(),
            definition_version: 1,
            group_id: "group".to_string(),
            group_version: 1,
            session_id: "session-one-shot-rerun".to_string(),
            session_activation_count: None,
            created_by: None,
            status: StateMachineRunStatus::Failed,
            input: serde_json::Value::Null,
            opening_message_override: Some(opening_message.clone()),
            output: None,
            error: Some("failed".to_string()),
            created_at: 1,
            updated_at: 2,
            completed_at: Some(2),
        };
        store.create_run(source.clone(), Vec::new()).await?;
        store
            .save_run_snapshot(&source, 1, &definition, None, None)
            .await?;
        let mut child = source.clone();
        child.run_id = "run-one-shot-child".to_string();
        child.rerun_of = Some(source.run_id.clone());
        child.status = StateMachineRunStatus::Pending;
        child.opening_message_override = None;
        child.error = None;
        child.created_at = 3;
        child.updated_at = 3;
        child.completed_at = None;

        assert!(matches!(
            store
                .create_rerun_if_session_idle(CreateStateMachineRerun {
                    source_run_id: source.run_id,
                    run: child.clone(),
                    nodes: Vec::new(),
                    reactivate_service_session: false,
                })
                .await?,
            CreateStateMachineRerunOutcome::Created
        ));
        assert_eq!(
            store
                .get_run(&child.run_id)
                .await?
                .expect("rerun child")
                .opening_message_override,
            Some(opening_message)
        );
        Ok(())
    }

    #[tokio::test]
    async fn sqlite_store_reactivates_session_creates_one_rerun_and_copies_snapshot_atomically()
    -> Result<(), Box<dyn std::error::Error>> {
        let (db, store) = sqlite_state_machine_store().await?;
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_group_sessions \
             (env, session_id, group_id, session_kind, status, activation_count) \
             VALUES (?, ?, ?, 'service_invocation', 'completed', 1)",
            vec![
                DbValue::from("dev"),
                DbValue::from("session-rerun"),
                DbValue::from("group"),
            ],
        ))
        .await?;
        let definition: CollaborationDefinition = serde_yaml::from_str(
            r#"
api_version: bcs.collaboration/v1
id: definition
version: 1
name: Rerun Definition
runtime:
  kind: chat
"#,
        )?;
        let mut source = StateMachineRun {
            run_id: "run-source".to_string(),
            root_run_id: Some("run-source".to_string()),
            rerun_of: None,
            definition_id: "definition".to_string(),
            definition_version: 1,
            group_id: "group".to_string(),
            group_version: 1,
            session_id: "session-rerun".to_string(),
            session_activation_count: Some(1),
            created_by: None,
            status: StateMachineRunStatus::Failed,
            input: serde_json::json!({"question": "retry"}),
            opening_message_override: None,
            output: None,
            error: Some("failed".to_string()),
            created_at: 1,
            updated_at: 2,
            completed_at: Some(2),
        };
        let mut completed_source = source.clone();
        completed_source.run_id = "run-completed-source".to_string();
        completed_source.root_run_id = Some(completed_source.run_id.clone());
        completed_source.status = StateMachineRunStatus::Completed;
        completed_source.error = None;
        store
            .create_run(completed_source.clone(), Vec::new())
            .await?;
        store
            .save_run_snapshot(&completed_source, 1, &definition, None, None)
            .await?;
        let mut rejected_child = completed_source.clone();
        rejected_child.run_id = "run-completed-child".to_string();
        rejected_child.rerun_of = Some(completed_source.run_id.clone());
        rejected_child.session_activation_count = Some(2);
        rejected_child.status = StateMachineRunStatus::Pending;
        rejected_child.completed_at = None;
        assert!(matches!(
            store
                .create_rerun_if_session_idle(CreateStateMachineRerun {
                    source_run_id: completed_source.run_id,
                    run: rejected_child,
                    nodes: Vec::new(),
                    reactivate_service_session: true,
                })
                .await?,
            CreateStateMachineRerunOutcome::Conflict
        ));

        store.create_run(source.clone(), Vec::new()).await?;
        store
            .save_run_snapshot(&source, 1, &definition, None, None)
            .await?;

        let mut child = source.clone();
        child.run_id = "run-child".to_string();
        child.rerun_of = Some(source.run_id.clone());
        child.session_activation_count = Some(2);
        child.status = StateMachineRunStatus::Pending;
        child.error = None;
        child.created_at = 3;
        child.updated_at = 3;
        child.completed_at = None;
        let command = CreateStateMachineRerun {
            source_run_id: source.run_id.clone(),
            run: child.clone(),
            nodes: Vec::new(),
            reactivate_service_session: true,
        };
        assert!(matches!(
            store.create_rerun_if_session_idle(command.clone()).await?,
            CreateStateMachineRerunOutcome::Created
        ));
        assert!(store.get_run_snapshot(&child.run_id).await?.is_some());
        let session_rows = db
            .query(DbStatement::with_params(
                "SELECT status, activation_count \
                 FROM bcs_group_sessions WHERE env = ? AND session_id = ?",
                vec![DbValue::from("dev"), DbValue::from("session-rerun")],
            ))
            .await?;
        let session_row = session_rows.first().expect("reactivated session row");
        assert_eq!(db_string(session_row, "status")?, "running");
        assert_eq!(db_i32(session_row, "activation_count")?, 2);
        assert!(matches!(
            store.create_rerun_if_session_idle(command).await?,
            CreateStateMachineRerunOutcome::Existing(existing)
                if existing.run_id == child.run_id
        ));

        source.run_id = "run-other-source".to_string();
        source.root_run_id = Some(source.run_id.clone());
        source.created_at = 4;
        source.updated_at = 4;
        store.create_run(source.clone(), Vec::new()).await?;
        store
            .save_run_snapshot(&source, 1, &definition, None, None)
            .await?;
        let mut other_child = child;
        other_child.run_id = "run-other-child".to_string();
        other_child.root_run_id = Some(source.run_id.clone());
        other_child.rerun_of = Some(source.run_id.clone());
        other_child.session_activation_count = Some(3);
        assert!(matches!(
            store
                .create_rerun_if_session_idle(CreateStateMachineRerun {
                    source_run_id: source.run_id,
                    run: other_child,
                    nodes: Vec::new(),
                    reactivate_service_session: true,
                })
                .await?,
            CreateStateMachineRerunOutcome::Conflict
        ));
        Ok(())
    }

    async fn insert_template(
        db: &dyn DbPlugin,
        id: &str,
        visibility: &str,
        priority: i64,
        status: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_collaboration_templates \
             (env, template_id, visibility, priority, record_status) VALUES (?, ?, ?, ?, ?)",
            vec![
                DbValue::from("dev"),
                DbValue::from(id.to_string()),
                DbValue::from(visibility.to_string()),
                DbValue::from(priority),
                DbValue::from(status.to_string()),
            ],
        ))
        .await?;
        Ok(())
    }

    async fn insert_template_content(
        db: &dyn DbPlugin,
        id: &str,
        lang: &str,
        yaml: &str,
        status: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_collaboration_template_contents \
             (env, template_id, lang, yaml_text, record_status) VALUES (?, ?, ?, ?, ?)",
            vec![
                DbValue::from("dev"),
                DbValue::from(id.to_string()),
                DbValue::from(lang.to_string()),
                DbValue::from(yaml.to_string()),
                DbValue::from(status.to_string()),
            ],
        ))
        .await?;
        Ok(())
    }

    async fn insert_template_tag(
        db: &dyn DbPlugin,
        id: &str,
        tag: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_collaboration_template_tags (env, template_id, tag) VALUES (?, ?, ?)",
            vec![
                DbValue::from("dev"),
                DbValue::from(id.to_string()),
                DbValue::from(tag.to_string()),
            ],
        ))
        .await?;
        Ok(())
    }

    #[tokio::test]
    async fn template_repo_reads_only_active_public_templates()
    -> Result<(), Box<dyn std::error::Error>> {
        let (db, repo) = sqlite_template_repo().await?;
        insert_template(db.as_ref(), "public-template", "public", 10, "active").await?;
        insert_template(db.as_ref(), "private-template", "private", 20, "active").await?;
        insert_template(db.as_ref(), "deleted-template", "public", 30, "deleted").await?;
        insert_template_content(
            db.as_ref(),
            "public-template",
            "zh-CN",
            "name: Public\nruntime:\n  kind: chat\n",
            "active",
        )
        .await?;
        insert_template_content(
            db.as_ref(),
            "public-template",
            "en-US",
            "name: Public\nruntime:\n  kind: chat\n",
            "active",
        )
        .await?;
        insert_template_content(
            db.as_ref(),
            "private-template",
            "zh-CN",
            "name: Private\nruntime:\n  kind: chat\n",
            "active",
        )
        .await?;
        insert_template_content(
            db.as_ref(),
            "deleted-template",
            "zh-CN",
            "name: Deleted\nruntime:\n  kind: chat\n",
            "active",
        )
        .await?;
        insert_template_tag(db.as_ref(), "public-template", "qa").await?;

        let entries = repo.list_entries().await?;
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].id, "public-template");
        assert_eq!(entries[0].tags, vec!["qa"]);
        assert_eq!(entries[0].available_languages, vec!["en-US", "zh-CN"]);

        assert_eq!(
            repo.supported_languages().await?,
            vec!["en-US".to_string(), "zh-CN".to_string()]
        );
        assert_eq!(
            repo.available_languages("private-template").await?,
            Vec::<String>::new()
        );
        assert!(
            repo.get_raw_yaml("public-template", "zh-CN")
                .await?
                .is_some()
        );
        assert!(
            repo.get_raw_yaml("private-template", "zh-CN")
                .await?
                .is_none()
        );
        assert!(
            repo.get_raw_yaml("deleted-template", "zh-CN")
                .await?
                .is_none()
        );

        Ok(())
    }

    #[tokio::test]
    async fn memory_store_preserves_source_yaml_and_rejects_same_version_source_mismatch() {
        let store = MemoryCollaborationStore::new();
        let source_yaml = r#"
api_version: bcs.collaboration/v1
id: def-source
version: 1
name: Source Definition
runtime:
  kind: chat
"#;
        let definition: CollaborationDefinition = serde_yaml::from_str(source_yaml).unwrap();
        store
            .upsert_with_source_yaml(definition.clone(), source_yaml.to_string())
            .await
            .unwrap();

        let record = store.get_record("def-source", 1).await.unwrap().unwrap();
        assert_eq!(record.source_format.as_deref(), Some("yaml"));
        assert_eq!(record.yaml_text.as_deref(), Some(source_yaml));

        let source_yaml_with_comment = r#"
# same normalized definition, different authoring source
api_version: bcs.collaboration/v1
id: def-source
version: 1
name: Source Definition
runtime:
  kind: chat
"#;
        let err = store
            .upsert_with_source_yaml(definition, source_yaml_with_comment.to_string())
            .await
            .unwrap_err();
        assert!(matches!(err, ServiceError::Conflict(_)));
    }

    #[tokio::test]
    async fn memory_store_expires_only_running_nodes_on_running_runs() {
        let store = MemoryCollaborationStore::new();
        let run = StateMachineRun {
            run_id: "run-timeout".to_string(),
            root_run_id: Some("run-timeout".to_string()),
            rerun_of: None,
            definition_id: "def".to_string(),
            definition_version: 1,
            group_id: "group".to_string(),
            group_version: 1,
            session_id: "session".to_string(),
            session_activation_count: None,
            created_by: None,
            status: StateMachineRunStatus::Running,
            input: serde_json::Value::Null,
            opening_message_override: None,
            output: None,
            error: None,
            created_at: 1,
            updated_at: 1,
            completed_at: None,
        };
        let node = StateMachineNodeRun {
            run_id: run.run_id.clone(),
            node_id: "node-a".to_string(),
            status: StateMachineNodeStatus::Running,
            attempt: 0,
            node_timeout_ms: Some(100),
            timeout_deadline_ms: Some(100),
            max_attempts: 1,
            assignee_bot_id: Some("bot-a".to_string()),
            outcome: None,
            responded_by: None,
            delivery_request_id: Some("delivery-a".to_string()),
            bot_delivery_run_id: None,
            artifact_text: None,
            error: None,
            started_at: Some(0),
            completed_at: None,
        };
        store.create_run(run.clone(), vec![node]).await.unwrap();

        let expired = store
            .list_expired_running_node_runs(700, 500, 10)
            .await
            .unwrap();
        assert_eq!(expired.len(), 1);

        store
            .update_run_status(
                &run.run_id,
                StateMachineRunStatus::Failed,
                None,
                Some("failed".to_string()),
                800,
                Some(800),
            )
            .await
            .unwrap();
        assert!(
            !store
                .fail_node_attempt(&run.run_id, "node-a", 0, "too late".to_string(), 900,)
                .await
                .unwrap()
        );
        let expired_after_terminal = store
            .list_expired_running_node_runs(900, 500, 10)
            .await
            .unwrap();
        assert!(expired_after_terminal.is_empty());
    }
