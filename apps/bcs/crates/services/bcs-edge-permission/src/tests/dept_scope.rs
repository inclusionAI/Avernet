//! Department-scope object-entry allowlist tests for the
//! connect/decision parser (`friend_ext` `{deptNo, deptName}`
//! objects, dev #2843/#2888 companion scenarios). Helpers are
//! shared from `super` (`assemble`, servers seeding, static
//! departments) — one family, one file.

use super::*;
    #[tokio::test]
    async fn human_protected_view_scope_user_friend_deps_object_entry_allows_matching_department() {
        let (eg, pp, rq, bc, db) = assemble().await;
        let mut target_friend_ext = serde_json::Map::new();
        target_friend_ext.insert(
            "view_scope_user_friend_deps".to_string(),
            serde_json::Value::Array(vec![serde_json::json!({
                "deptNo": "TECH-001",
                "deptName": "示例集团-技术事业部-平台团队"
            })]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:user_scope_object_hit",
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
                "示例集团-技术事业部-平台团队-系统组".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect("human_1", "x:user_scope_object_hit", None, None)
            .await
            .expect("protected user scope object entry hit should auto-approve when OPEN");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert_eq!(res.request_ids.len(), 1);
    }

    #[tokio::test]
    async fn dept_free_object_entry_auto_approves_when_human_department_matches() {
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
            serde_json::Value::Array(vec![serde_json::json!({
                "deptNo": "TECH-001",
                "deptName": "示例集团-技术事业部-平台团队"
            })]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept_object",
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
                "示例集团-技术事业部-平台团队-系统组".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect("human_1", "x:dept_object", None, None)
            .await
            .expect("dept_free object entry hit → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert_eq!(res.request_ids.len(), 1);
        let req = rq.get(&res.request_ids[0], "dev").await.expect("approved req");
        assert_eq!(req.status, RequestStatus::Approved);
    }

    #[tokio::test]
    async fn dept_free_object_entry_falls_back_to_dept_no_without_name() {
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
            serde_json::Value::Array(vec![serde_json::json!({
                "deptNo": "TECH-201"
            })]),
        );
        seed_bot_with_friend_ext(
            &db,
            "x:dept_object_fallback",
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
                "TECH-201".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect("human_1", "x:dept_object_fallback", None, None)
            .await
            .expect("dept_free object entry deptNo fallback hit → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert_eq!(res.request_ids.len(), 1);
    }

    #[test]
    fn friend_scope_deps_accepts_department_reference_objects() {
        let friend_ext: serde_json::Map<String, serde_json::Value> = serde_json::from_value(
            serde_json::json!({
                "no_check_scope_friend_deps": [
                    {"deptNo": "L3480", "deptName": "蚂蚁集团-信贷事业群"},
                    "蚂蚁集团-大安全-大安全技术部-AI基础设施",
                    {"deptNo": ""},
                    {}
                ],
            }),
        )
        .expect("friend_ext object");
        let deps = bot_friend_ext_no_check_scope_friend_deps(&friend_ext);

        // Object entries resolve by the department-NAME label (deptNo is the
        // fallback only when the name is absent); a blank/entry-less object
        // yields nothing. Plain strings ride along untouched.
        let expected: std::collections::HashSet<String> = [
            "蚂蚁集团-信贷事业群".to_string(),
            "蚂蚁集团-大安全-大安全技术部-AI基础设施".to_string(),
        ]
        .into_iter()
        .collect();
        assert_eq!(deps, expected);
    }

    #[test]
    fn friend_scope_deps_view_scope_accepts_objects_and_ignores_garbage() {
        // Production rows persist the view scopes in the same {deptNo,
        // deptName} object shape as the exemption list — the shared parser
        // must read them identically (this key is human→protected-bot
        // visibility scoping, the agent twin covers bot callers).
        let friend_ext: serde_json::Map<String, serde_json::Value> = serde_json::from_value(
            serde_json::json!({
                "view_scope_user_friend_deps": [
                    {"deptNo": "F4858"},
                    "  ",
                    42,
                    {"deptName": "蚂蚁集团-信贷事业群"},
                ],
                "view_scope_agent_friend_deps": [],
            }),
        )
        .expect("friend_ext object");
        let deps = bot_friend_ext_view_scope_user_friend_deps(&friend_ext);
        let expected: std::collections::HashSet<String> = [
            "F4858".to_string(),
            "蚂蚁集团-信贷事业群".to_string(),
        ]
        .into_iter()
        .collect();
        assert_eq!(deps, expected);
        // An explicitly empty list stays empty — "no scope restriction" is a
        // legitimate configuration and must not warn or misread.
        assert!(bot_friend_ext_view_scope_agent_friend_deps(&friend_ext).is_empty());
    }

    #[tokio::test]
    async fn dept_free_auto_approves_from_object_department_reference() {
        // Mirrors the production incident shape: friend_ext persisted with
        // {deptNo, deptName} objects must not read as an empty allowlist —
        // an in-scope human applicant is auto-approved, not routed to approval.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "human_1", "protected", "protected", "APPROVAL", "online", Some("85020"))
            .await;
        let friend_ext: serde_json::Map<String, serde_json::Value> = serde_json::from_value(
            serde_json::json!({"no_check_scope_friend_deps": [
                {"deptNo": "L3480", "deptName": "蚂蚁集团-信贷事业群"},
            ]}),
        )
        .expect("friend_ext object");
        // The directory returns a hierarchical path under the configured
        // deptName — either form has to hit the allowlist.
        seed_bot_with_friend_ext(
            &db,
            "x:dept_object",
            "protected",
            "protected",
            "DEPT_FREE",
            "online",
            Some("85020"),
            friend_ext,
        )
        .await;
        let departments = Arc::new(StaticUserDirectoryPlugin {
            departments: Arc::new(std::collections::HashMap::from([(
                "1".to_string(),
                "蚂蚁集团-信贷事业群-信贷技术".to_string(),
            )])),
        });
        let svc = service_with_departments(&eg, &pp, &rq, &bc, departments);
        let res = svc
            .create_connect("human_1", "x:dept_object", None, None)
            .await
            .expect("deptNo/deptName object allowlist must auto-approve");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
    }
