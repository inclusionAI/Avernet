use super::*;

    #[tokio::test]
    async fn dept_free_allowlist_stays_pending_with_noop_department_port() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(
            &db,
            "human_1",
            "protected",
            "protected",
            "APPROVAL",
            "online",
            Some("owner_1"),
        )
        .await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept_noop",
            "protected",
            "protected",
            "DEPT_FREE",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:dept_noop", None, None)
            .await
            .expect("noop department port should not auto-approve");
        assert_eq!(res.status, ConnectStatus::Pending);
        assert!(!res.auto_accepted);
        assert_eq!(res.request_ids.len(), 1);
        assert!(res.edge_ids.is_empty());
        let req = rq.get(&res.request_ids[0], "dev").await.expect("pending req");
        assert_eq!(req.status, RequestStatus::Pending);
    }
    #[tokio::test]
    async fn dept_free_allowlist_auto_approves_when_human_department_matches() {
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
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept",
            "protected",
            "protected",
            "DEPT_FREE",
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
            .create_connect("human_1", "x:dept", None, None)
            .await
            .expect("dept_free ancestor hit → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert_eq!(res.request_ids.len(), 1);
        let req = rq.get(&res.request_ids[0], "dev").await.expect("approved req");
        assert_eq!(req.status, RequestStatus::Approved);
    }
    #[tokio::test]
    async fn dept_free_lookup_receives_forwarded_auth_context() {
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
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String("F4858".to_string())]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept_context",
            "protected",
            "protected",
            "DEPT_FREE",
            "online",
            Some("85020"),
            target_friend_ext,
        )
        .await;
        let contexts = Arc::new(tokio::sync::Mutex::new(Vec::new()));
        let departments = Arc::new(RecordingContextUserDirectoryPlugin {
            department: "F4858".to_string(),
            contexts: contexts.clone(),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect(
                "human_1",
                "x:dept_context",
                None,
                Some(RequestAuthHeaders {
                    authorization: Some("Bearer caller-token".to_string()),
                    cookie: None,
                    forwarded_headers: vec![(
                        "authorization".to_string(),
                        "Bearer caller-token".to_string(),
                    )],
                }),
            )
            .await
            .expect("dept_free exact match from department port → Approved");

        assert_eq!(res.status, ConnectStatus::Approved);
        assert_eq!(
            contexts.lock().await[0].forwarded_headers,
            vec![("authorization".to_string(), "Bearer caller-token".to_string())]
        );
    }
    #[tokio::test]
    async fn dept_free_allowlist_auto_approves_when_department_port_matches() {
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
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施-系统智能".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept_from_port",
            "protected",
            "protected",
            "DEPT_FREE",
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
            .create_connect("human_1", "x:dept_from_port", None, None)
            .await
            .expect("dept_free exact match from department port → Approved");

        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
    }
    #[tokio::test]
    async fn dept_free_bot_applicant_falls_back_to_owner_department_port() {
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
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:target",
            "protected",
            "protected",
            "DEPT_FREE",
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
            .create_connect("x:applicant", "x:target", None, None)
            .await
            .expect("bot applicant owner ancestor dept from department port → Approved");

        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
    }
    #[tokio::test]
    async fn dept_free_allowlist_miss_keeps_pending() {
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
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "no_check_scope_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::Value::String(
                "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
            )]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept_miss",
            "protected",
            "protected",
            "DEPT_FREE",
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
        let res = svc
            .create_connect("human_1", "x:dept_miss", None, None)
            .await
            .expect("dept_free miss → Pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        assert!(res.edge_ids.is_empty());
        assert_eq!(res.request_ids.len(), 1);
        let req = rq.get(&res.request_ids[0], "dev").await.expect("pending req");
        assert_eq!(req.status, RequestStatus::Pending);
    }
