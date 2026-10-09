//! Unit tests of `DbEdgeGrantStore`, split out of the former over-limit
//! `edge_grant.rs` (plan Task 12 lib split; suite body unchanged).

    use std::sync::Arc;

    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_db_api::{
        DbExecuteResult, DbHealth, DbResult, DbTransactionStep, DbTransactionStepResult,
    };
    use bcs_domain::edge_permission::{
        EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType,
    };

    use super::*;
    use crate::DbPermissionProfileStore;
    use bcs_service_api::port::repo::PermissionProfileRepoPort;

    fn test_operation(label: &str) -> BotOperationContext {
        BotOperationContext {
            operation_id: format!("test-op-{label}"),
            actor: bcs_service_api::types::BotOperationActor::Human {
                user_id: "85020".to_string(),
                effective_actor_id: "human_85020".to_string(),
            },
        }
    }

    async fn sqlite_store() -> DbEdgeGrantStore {
        let db = LocalSqliteDbPlugin::new().expect("local sqlite");
        // edge_grants + permission_profiles schema (mirrors
        // migrations/mysql/014_edge_permission.sql for SQLite).
        db.execute(DbStatement::new(
            "CREATE TABLE edge_grants (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                env VARCHAR(32) NOT NULL, \
                from_id VARCHAR(128) NOT NULL, \
                to_id VARCHAR(128) NOT NULL, \
                grant_kind VARCHAR(32) NOT NULL, \
                grant_ref_id INTEGER NOT NULL, \
                rules TEXT, \
                status VARCHAR(16) NOT NULL DEFAULT 'approved', \
                originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any', \
                originator_policy_data TEXT, \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                UNIQUE (from_id, to_id, env, grant_ref_id))",
        ))
        .await
        .expect("create edge_grants");
        // Plan Task 12: insert/revoke commit one bcs_bot_action_audits row
        // in the same transaction (mirrors migration-032).
        db.execute(DbStatement::new(
            "CREATE TABLE bcs_bot_action_audits (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                audit_id VARCHAR(128) NOT NULL, \
                env VARCHAR(32) NOT NULL, \
                operation_id VARCHAR(64) NOT NULL, \
                step_key VARCHAR(128) NOT NULL, \
                operator_kind VARCHAR(16) NOT NULL, \
                operator_id VARCHAR(256) NOT NULL, \
                operator_user_id VARCHAR(256), \
                effective_actor_id VARCHAR(256) NOT NULL, \
                resource_kind VARCHAR(32) NOT NULL, \
                resource_id VARCHAR(256) NOT NULL, \
                action VARCHAR(32) NOT NULL, \
                phase VARCHAR(16) NOT NULL, \
                reason_code VARCHAR(64), \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
        ))
        .await
        .expect("create bcs_bot_action_audits");
        db.execute(DbStatement::new(
            "CREATE TABLE permission_profiles (\
                id INTEGER PRIMARY KEY AUTOINCREMENT, \
                bot_id VARCHAR(128) NOT NULL, \
                env VARCHAR(32) NOT NULL, \
                name VARCHAR(128) NOT NULL DEFAULT 'default', \
                description VARCHAR(512), \
                rules_template TEXT NOT NULL, \
                revision INTEGER NOT NULL DEFAULT 1, \
                digest VARCHAR(128) NOT NULL, \
                is_default INTEGER NOT NULL DEFAULT 0, \
                status VARCHAR(16) NOT NULL DEFAULT 'active', \
                created_by VARCHAR(128) NOT NULL, \
                updated_by VARCHAR(128), \
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
                UNIQUE (bot_id, env, is_default, status))",
        ))
        .await
        .expect("create permission_profiles");
        DbEdgeGrantStore::sqlite(Arc::new(db))
    }

    async fn seed_default(store: &DbEdgeGrantStore, bot_id: &str, env: &str) -> u64 {
        let profile_store = DbPermissionProfileStore::sqlite(store.db.clone());
        profile_store
            .ensure_default_profile(bot_id, env)
            .await
            .expect("seed profile")
    }

    fn default_grant(from: &str, to: &str, env: &str, ref_id: u64) -> EdgeGrant {
        EdgeGrant {
            edge_id: 0,
            env: env.to_string(),
            from_id: from.to_string(),
            to_id: to.to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: ref_id,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
            management_source_kind: "none".into(),
            management_source_id: "none".into(),
        }
    }

    #[tokio::test]
    async fn get_default_profile_id_roundtrip() {
        let store = sqlite_store().await;
        let profile_id = seed_default(&store, "bot_a", "dev").await;
        assert_eq!(store.get_default_profile_id("bot_a", "dev").await, Some(profile_id));
        assert_eq!(store.get_default_profile_id("human_x", "dev").await, None);
    }

    #[tokio::test]
    async fn insert_and_list_active_grants() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "b", "dev").await;
        let g = default_grant("a", "b", "dev", ref_id);
        let edge_id = store.insert_grant(g.clone(), &test_operation("one")).await.expect("insert");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].edge_id, edge_id);
        assert_eq!(listed[0].from_id, "a");
        assert_eq!(listed[0].grant_kind, GrantKind::PermissionProfile);
    }

    #[tokio::test]
    async fn insert_idempotent_on_unique_key() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "b", "dev").await;
        let mut g = default_grant("a", "b", "dev", ref_id);
        let edge_id = store
            .insert_grant(g.clone(), &test_operation("idem-1"))
            .await
            .expect("insert 1");
        // Re-insert with same (from,to,env,ref) but different edge_id: DO NOTHING.
        g.edge_id = 9999;
        let dup_id = store
            .insert_grant(g, &test_operation("idem-2"))
            .await
            .expect("insert 2");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert_eq!(listed.len(), 1);
        // The original auto-generated edge_id survives.
        assert_eq!(listed[0].edge_id, edge_id);
        assert_eq!(dup_id, edge_id);
    }

    #[tokio::test]
    async fn revoke_removes_from_active() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "b", "dev").await;
        let g = default_grant("a", "b", "dev", ref_id);
        let edge_id = store.insert_grant(g.clone(), &test_operation("rev")).await.expect("insert");
        store.revoke_grant(edge_id, "dev", &test_operation("rev-i")).await.expect("revoke");
        let listed = store.list_active_grants("a", "b", "dev").await;
        assert!(listed.is_empty());
    }

    #[tokio::test]
    async fn has_friend_edge_any_direction() {
        let store = sqlite_store().await;
        let ref_id = seed_default(&store, "bot_b", "dev").await;
        // a (human) → b : friend edge (ref = b's default).
        let g = default_grant("human_a", "bot_b", "dev", ref_id);
        store
            .insert_grant(g, &test_operation("friend-edge"))
            .await
            .expect("insert");
        assert!(store.has_friend_edge("human_a", "bot_b", "dev").await);
        assert!(store.has_friend_edge("bot_b", "human_a", "dev").await);
    }

    #[tokio::test]
    async fn list_friends_outbound_human_actor() {
        let store = sqlite_store().await;
        let ref_b = seed_default(&store, "bot_b", "dev").await;
        let ref_c = seed_default(&store, "bot_c", "dev").await;
        store
            .insert_grant(default_grant("human_a", "bot_b", "dev", ref_b), &test_operation("b"))
            .await
            .expect("insert b");
        store
            .insert_grant(default_grant("human_a", "bot_c", "dev", ref_c), &test_operation("c"))
            .await
            .expect("insert c");
        // non-friend (wrong ref) should not be listed
        store
            .insert_grant(default_grant("human_a", "bot_c", "dev", ref_b), &test_operation("wrong-ref"))
            .await
            .expect("insert wrong ref (different ref)");

        let mut friends = store.list_friends("human_a", "dev").await;
        friends.sort();
        assert_eq!(friends, vec!["bot_b".to_string(), "bot_c".to_string()]);
    }

    #[tokio::test]
    async fn friend_page_conformance_filters_deduplicates_and_counts_before_paging() {
        let store = sqlite_store().await;
        let bot_default = seed_default(&store, "bot-main", "dev").await;
        // Deliberately unsorted. Near-prefix IDs must remain Bots, not Humans.
        for peer in ["human_2002", "humanX1001", "bot-z", "Human_1001", "human_1001", "bot-A", "bot-a"] {
            store.insert_grant(default_grant(peer, "bot-main", "dev", bot_default), &test_operation("peer")).await.unwrap();
        }
        let peer_default = seed_default(&store, "bot-z", "dev").await;
        store.insert_grant(default_grant("bot-main", "bot-z", "dev", peer_default), &test_operation("bot-z")).await.unwrap();
        assert!(store.list_active_grants("bot-main", "human_1001", "dev").await.is_empty());

        let repo: &dyn EdgeGrantRepoPort = &store;
        let query = |target_type, offset, limit| FriendListQuery { target_type, offset, limit };
        let first = repo.list_friends_paginated("bot-main", "dev", query(Some(ActorKind::Human), 0, 1)).await.unwrap();
        assert_eq!(first, FriendIdsPage { items: vec!["human_1001".into()], total: 2 });
        let second = repo.list_friends_paginated("bot-main", "dev", query(Some(ActorKind::Human), 1, 1)).await.unwrap();
        assert_eq!(second, FriendIdsPage { items: vec!["human_2002".into()], total: 2 });
        let bots = repo.list_friends_paginated("bot-main", "dev", query(Some(ActorKind::Bot), 0, 100)).await.unwrap();
        assert_eq!(bots.total, 5);
        assert_eq!(bots.items, vec!["Human_1001", "bot-A", "bot-a", "bot-z", "humanX1001"]);
        let all = repo.list_friends_paginated("bot-main", "dev", query(None, 0, 100)).await.unwrap();
        assert_eq!(all.total, 7); // bot-z has two grants but is one friend.
        let mut assembled = Vec::new();
        for offset in [0, 2, 4, 6] {
            let page = repo.list_friends_paginated("bot-main", "dev", query(None, offset, 2)).await.unwrap();
            assert_eq!(page.total, all.total);
            assembled.extend(page.items);
        }
        assert_eq!(assembled, all.items);
        for offset in [7, u64::from(u32::MAX) * 100] {
            let empty = repo.list_friends_paginated("bot-main", "dev", query(None, offset, 20)).await.unwrap();
            assert_eq!(empty.total, 7);
            assert!(empty.items.is_empty());
        }
        let human = repo.list_friends_paginated("human_1001", "dev", query(Some(ActorKind::Bot), 0, 20)).await.unwrap();
        assert_eq!(human, FriendIdsPage { items: vec!["bot-main".into()], total: 1 });
        let empty = repo.list_friends_paginated("human_1001", "dev", query(Some(ActorKind::Human), 0, 20)).await.unwrap();
        assert_eq!(empty, FriendIdsPage { items: vec![], total: 0 });
        let unknown = repo.list_friends_paginated("unknown", "dev", query(None, 0, 20)).await.unwrap();
        assert_eq!(unknown, FriendIdsPage { items: vec![], total: 0 });
    }

    #[tokio::test]
    async fn friend_page_excludes_non_friend_edges_and_other_environments() {
        let store = sqlite_store().await;
        let active = seed_default(&store, "bot-main", "dev").await;
        let other = seed_default(&store, "other", "dev").await;
        let prod = seed_default(&store, "bot-main", "prod").await;
        for (peer, env, profile) in [
            ("human_valid", "dev", active),
            ("human_wrong_owner", "dev", other),
            ("human_wrong_profile_env", "dev", prod),
            ("human_other_env", "prod", prod),
        ] {
            store.insert_grant(default_grant(peer, "bot-main", env, profile), &test_operation("exo")).await.unwrap();
        }
        let mut revoked = default_grant("human_revoked", "bot-main", "dev", active);
        revoked.status = EdgeStatus::Revoked;
        store.insert_grant(revoked, &test_operation("revoked")).await.unwrap();
        let mut rules = default_grant("human_rules", "bot-main", "dev", active);
        rules.grant_kind = GrantKind::Rules;
        store.insert_grant(rules, &test_operation("rules")).await.unwrap();
        // Outbound edges must also match an active DEFAULT profile.
        for (bot, assignment) in [("inactive-bot", "status = 'inactive'"), ("custom-bot", "is_default = 0")] {
            let profile = seed_default(&store, bot, "dev").await;
            store.insert_grant(default_grant("bot-main", bot, "dev", profile), &test_operation("custom")).await.unwrap();
            store.db.execute(DbStatement::with_params(
                format!("UPDATE permission_profiles SET {assignment} WHERE id = ?"),
                vec![DbValue::from(profile)],
            )).await.unwrap();
        }
        let query = FriendListQuery { target_type: None, offset: 0, limit: 20 };
        assert_eq!(store.list_friends_paginated("bot-main", "dev", query).await.unwrap(),
            FriendIdsPage { items: vec!["human_valid".into()], total: 1 });
        store.db.execute(DbStatement::with_params(
            "UPDATE permission_profiles SET status = 'inactive' WHERE id = ?", vec![DbValue::from(active)],
        )).await.unwrap();
        assert_eq!(store.list_friends_paginated("bot-main", "dev", query).await.unwrap(),
            FriendIdsPage { items: vec![], total: 0 });
    }

    #[tokio::test]
    async fn friend_page_rejects_invalid_bounds_and_propagates_database_errors() {
        let store = sqlite_store().await;
        for (offset, limit) in [(0, 0), (0, 101), (u64::MAX, 20)] {
            assert!(matches!(store.list_friends_paginated("bot", "dev", FriendListQuery {
                target_type: None, offset, limit,
            }).await, Err(ServiceError::InvalidOperation { .. })));
        }
        store.db.execute(DbStatement::new("DROP TABLE permission_profiles")).await.unwrap();
        assert!(matches!(store.list_friends_paginated("bot", "dev", FriendListQuery {
            target_type: None, offset: 0, limit: 20,
        }).await, Err(ServiceError::InternalError(_))));
    }

    #[test]
    fn friend_page_mysql_uses_binary_identity_and_bound_pagination() {
        let statement = friend_list_statement(EdgeGrantSqlFlavor::Mysql, "bot'quote", "dev", FriendListQuery {
            target_type: Some(ActorKind::Human), offset: 40, limit: 20,
        }).unwrap();
        assert!(statement.sql().contains("CAST(g.to_id AS BINARY)"));
        assert!(statement.sql().contains("CAST(g.from_id AS BINARY)"));
        assert!(statement.sql().contains("SUBSTR(peer_id, 1, 6) = CAST('human_' AS BINARY)"));
        assert!(statement.sql().contains("LIMIT ? OFFSET ?"));
        assert!(!statement.sql().contains("bot'quote"));
        assert_eq!(statement.params(), vec![
            DbValue::from("bot'quote"), DbValue::from("dev"),
            DbValue::from("bot'quote"), DbValue::from("dev"),
            DbValue::from(20_u64), DbValue::from(40_u64),
        ]);
    }

    struct RecordingFriendDb {
        inner: Arc<dyn DbPlugin>,
        reads: std::sync::Mutex<Vec<(DbStatement, usize)>>,
    }

    #[async_trait]
    impl DbPlugin for RecordingFriendDb {
        async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
            let rows = self.inner.query(statement.clone()).await?;
            self.reads.lock().unwrap().push((statement, rows.len()));
            Ok(rows)
        }
        async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
            self.inner.execute(statement).await
        }
        async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
            self.inner.transaction(steps).await
        }
        async fn health_check(&self) -> DbResult<DbHealth> {
            self.inner.health_check().await
        }
    }

    #[tokio::test]
    async fn friend_page_reads_only_one_bounded_result_from_database() {
        let seed = sqlite_store().await;
        let profile = seed_default(&seed, "bot-main", "dev").await;
        for i in 0..31 {
            seed.insert_grant(default_grant(&format!("human_{i:03}"), "bot-main", "dev", profile), &test_operation("bulk")).await.unwrap();
        }
        let db = Arc::new(RecordingFriendDb { inner: seed.db, reads: std::sync::Mutex::new(Vec::new()) });
        let store = DbEdgeGrantStore::sqlite(db.clone());
        let page = store.list_friends_paginated("bot-main", "dev", FriendListQuery {
            target_type: Some(ActorKind::Human), offset: 10, limit: 5,
        }).await.unwrap();
        assert_eq!(page.total, 31);
        assert_eq!(page.items, vec!["human_010", "human_011", "human_012", "human_013", "human_014"]);
        let reads = db.reads.lock().unwrap();
        assert_eq!(reads.len(), 1, "no full-list or N+1 default-profile queries");
        assert_eq!(reads[0].1, 5, "only this page crossed the DB boundary");
        assert!(reads[0].0.sql().contains("LIMIT ? OFFSET ?"));
    }

