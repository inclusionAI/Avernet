use super::*;

    #[tokio::test]
    async fn human_protected_view_scope_user_friend_deps_allows_matching_department() {
        let (eg, pp, rq, bc, db) = assemble().await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "view_scope_user_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:user_scope_hit",
            "protected",
            "protected",
            "OPEN",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "1".to_string(),
                "蚂蚁集团-大安全-大安全技术部-AI基础设施-系统智能".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect("human_1", "x:user_scope_hit", None, None)
            .await
            .expect("protected user scope hit should auto-approve when OPEN");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert_eq!(res.request_ids.len(), 1);
    }
    #[tokio::test]
    async fn human_protected_view_scope_user_friend_deps_blocks_out_of_scope_department() {
        let (eg, pp, rq, bc, db) = assemble().await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "view_scope_user_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:user_scope_miss",
            "protected",
            "protected",
            "OPEN",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "1".to_string(),
                "蚂蚁集团-其他事业群-销售部".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let err = svc
            .create_connect("human_1", "x:user_scope_miss", None, None)
            .await
            .expect_err("out-of-scope human applicant should be rejected");
        assert!(matches!(err, ServiceError::Forbidden(_)), "got {err:?}");
    }
    #[tokio::test]
    async fn bot_protected_view_scope_agent_friend_deps_allows_matching_owner_department() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "x:applicant",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("owner_1"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "view_scope_agent_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:target_scope_hit",
            "protected",
            "protected",
            "OPEN",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "owner_1".to_string(),
                "蚂蚁集团-大安全-大安全技术部-AI基础设施-系统智能".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect("x:applicant", "x:target_scope_hit", None, None)
            .await
            .expect("protected agent scope hit should auto-approve when OPEN");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 2);
        assert_eq!(res.request_ids.len(), 2);
    }
    #[tokio::test]
    async fn bot_protected_view_scope_agent_friend_deps_blocks_out_of_scope_owner_department() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "x:applicant",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("owner_1"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "view_scope_agent_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:target_scope_miss",
            "protected",
            "protected",
            "OPEN",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "owner_1".to_string(),
                "蚂蚁集团-其他事业群-销售部".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let err = svc
            .create_connect("x:applicant", "x:target_scope_miss", None, None)
            .await
            .expect_err("out-of-scope bot owner should be rejected");
        assert!(matches!(err, ServiceError::Forbidden(_)), "got {err:?}");
    }
    #[tokio::test]
    async fn public_auto_bot_returns_approved_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pub", "public", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:pub", None, None)
            .await
            .expect("public+auto → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert_eq!(res.request_ids.len(), 1);
        assert!(eg.has_friend_edge("human_1", "x:pub", "dev").await);
        let active = eg.list_active_grants("human_1", "x:pub", "dev").await;
        assert_eq!(active.len(), 1, "public auto should create a durable edge");
        let r = rq.get(&res.request_ids[0], "dev").await.expect("approved req");
        assert_eq!(r.status, RequestStatus::Approved);
        assert_eq!(r.edge_id, Some(res.edge_ids[0]));
    }
    #[tokio::test]
    async fn department_lookup_logs_success_result_path() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "human_1",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("85020"),
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "1".to_string(),
                "F4858".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);

        assert_eq!(
            svc.resolve_user_department_code("human_1", None).await.as_deref(),
            Some("F4858")
        );
    }
    #[tokio::test]
    async fn department_lookup_logs_failure_result_path() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "human_1",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("85020"),
        )
        .await;
        let svc = service_with_departments(&eg, &pp, &rq, &bc, Arc::new(FailingUserDirectoryPlugin));

        assert_eq!(svc.resolve_user_department_code("human_1", None).await, None);
    }
    #[tokio::test]
    async fn department_lookup_logs_missing_directory_path() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        assert_eq!(svc.resolve_user_department_code("human_1", None).await, None);
    }
    #[tokio::test]
    async fn department_lookup_logs_skip_paths() {
        let (eg, pp, rq, bc, db) = assemble().await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::new()),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);

        assert_eq!(svc.resolve_user_department_code("human_", None).await, None);
        assert_eq!(svc.resolve_user_department_code("x:missing", None).await, None);

        seed_bot(
            &db,
            "x:no_owner",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            None,
        )
        .await;
        assert_eq!(svc.resolve_user_department_code("x:no_owner", None).await, None);
    }
