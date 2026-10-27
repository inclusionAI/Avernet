//! Integration-style unit tests for the edge-permission
//! services (moved verbatim from `lib.rs`).

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::actor::ActorKind;
use bcs_domain::edge_permission::{
    AdmissionReason, EdgeGrant, EdgeStatus, GrantKind, GrantSource, OriginatorPolicyType, PermissionRequest,
    RequestStatus,
};
use bcs_service_api::application::admission::AdmissionService;
use bcs_service_api::application::connect::{
    ConnectService, ConnectStatus, FriendListQuery, RequestDirection,
};
use bcs_service_api::port::{
    FriendConnectNotificationCommand, FriendConnectNotificationKind,
    FriendConnectNotificationPort,
};
use bcs_service_api::RequestAuthHeaders;
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
    PermissionRequestRepoPort,
};
use bcs_service_api::{EdgePermissionFriendSyncService, ServiceError, ServiceResult};
use bcs_user_directory_api::{UserDirectoryLookupContext, UserDirectoryPlugin};
    use super::*;

    mod dept_scope;
    use bcs_db_api::{DbPlugin, DbStatement, DbValue};
    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_service_api::FriendRepoPort;
    use bcs_service_api::port::FriendshipWritebackPort;

    #[path = "../../tests/support/sqlite_fixture.rs"]
    mod sqlite_fixture;
    use sqlite_fixture::assemble;

    fn service(
        edge_grants: &Arc<dyn EdgeGrantRepoPort>,
        profiles: &Arc<dyn PermissionProfileRepoPort>,
        requests: &Arc<dyn PermissionRequestRepoPort>,
        bot_config: &Arc<dyn BotActorConfigRepoPort>,
    ) -> DbConnectService {
        service_in_env(edge_grants, profiles, requests, bot_config, "dev")
    }

    fn service_in_env(
        edge_grants: &Arc<dyn EdgeGrantRepoPort>,
        profiles: &Arc<dyn PermissionProfileRepoPort>,
        requests: &Arc<dyn PermissionRequestRepoPort>,
        bot_config: &Arc<dyn BotActorConfigRepoPort>,
        env: &str,
    ) -> DbConnectService {
        DbConnectService::new(
            edge_grants.clone(),
            profiles.clone(),
            requests.clone(),
            bot_config.clone(),
            None,
            Arc::new(bcs_service_api::NoopFriendConnectNotificationPort),
            Arc::new(bcs_service_api::NoopFriendAuthSyncPort),
            env.to_string(),
        )
    }

    #[derive(Clone, Default)]
    struct StaticUserDirectoryPlugin {
        departments: Arc<std::collections::HashMap<String, String>>,
    }

    #[async_trait]
    impl UserDirectoryPlugin for StaticUserDirectoryPlugin {
        async fn lookup_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<Option<bcs_user_directory_api::UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
            Ok(Some(bcs_user_directory_api::UserDirectoryProfile {
                staff_no: staff_no.to_string(),
                nick_name: None,
            }))
        }

        async fn lookup_department_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            Ok(self.departments.get(staff_no).cloned())
        }
    }

    /// User-directory stub that returns a fixed nick name for any staff_no —
    /// used to verify friend-connect notifications resolve the applicant's name.
    struct FixedNickUserDirectoryPlugin {
        nick: String,
    }

    #[async_trait]
    impl UserDirectoryPlugin for FixedNickUserDirectoryPlugin {
        async fn lookup_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<Option<bcs_user_directory_api::UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
            Ok(Some(bcs_user_directory_api::UserDirectoryProfile {
                staff_no: staff_no.to_string(),
                nick_name: Some(self.nick.clone()),
            }))
        }

        async fn lookup_department_by_staff_no(
            &self,
            _staff_no: &str,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            Ok(None)
        }
    }

    fn service_with_departments(
        edge_grants: &Arc<dyn EdgeGrantRepoPort>,
        profiles: &Arc<dyn PermissionProfileRepoPort>,
        requests: &Arc<dyn PermissionRequestRepoPort>,
        bot_config: &Arc<dyn BotActorConfigRepoPort>,
        departments: Arc<dyn UserDirectoryPlugin>,
    ) -> DbConnectService {
        DbConnectService::new(
            edge_grants.clone(),
            profiles.clone(),
            requests.clone(),
            bot_config.clone(),
            Some(departments),
            Arc::new(bcs_service_api::NoopFriendConnectNotificationPort),
            Arc::new(bcs_service_api::NoopFriendAuthSyncPort),
            "dev".to_string(),
        )
    }

    #[derive(Clone, Default)]
    struct RecordingContextUserDirectoryPlugin {
        department: String,
        contexts: Arc<tokio::sync::Mutex<Vec<UserDirectoryLookupContext>>>,
    }

    #[async_trait]
    impl UserDirectoryPlugin for RecordingContextUserDirectoryPlugin {
        async fn lookup_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<Option<bcs_user_directory_api::UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
            Ok(Some(bcs_user_directory_api::UserDirectoryProfile {
                staff_no: staff_no.to_string(),
                nick_name: None,
            }))
        }

        async fn lookup_department_by_staff_no(
            &self,
            _staff_no: &str,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            Ok(Some(self.department.clone()))
        }

        async fn lookup_department_by_staff_no_with_context(
            &self,
            _staff_no: &str,
            context: &UserDirectoryLookupContext,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            self.contexts.lock().await.push(context.clone());
            Ok(Some(self.department.clone()))
        }
    }

    #[derive(Clone, Default)]
    struct FailingUserDirectoryPlugin;

    #[async_trait]
    impl UserDirectoryPlugin for FailingUserDirectoryPlugin {
        async fn lookup_by_staff_no(
            &self,
            staff_no: &str,
        ) -> Result<Option<bcs_user_directory_api::UserDirectoryProfile>, bcs_user_directory_api::UserDirectoryError> {
            Ok(Some(bcs_user_directory_api::UserDirectoryProfile {
                staff_no: staff_no.to_string(),
                nick_name: None,
            }))
        }

        async fn lookup_department_by_staff_no(
            &self,
            _staff_no: &str,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            Err(bcs_user_directory_api::UserDirectoryError::Request("boom".to_string()))
        }

        async fn lookup_department_by_staff_no_with_context(
            &self,
            _staff_no: &str,
            _context: &UserDirectoryLookupContext,
        ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
            Err(bcs_user_directory_api::UserDirectoryError::Request("boom".to_string()))
        }
    }

    #[derive(Clone, Default)]
    struct RecordingFriendConnectNotificationPort {
        events: Arc<tokio::sync::Mutex<Vec<FriendConnectNotificationCommand>>>,
    }

    #[async_trait]
    impl FriendConnectNotificationPort for RecordingFriendConnectNotificationPort {
        async fn notify(&self, command: FriendConnectNotificationCommand) -> ServiceResult<()> {
            self.events.lock().await.push(command);
            Ok(())
        }
    }

    #[derive(Default)]
    struct RecordingWriteback {
        calls: std::sync::Mutex<Vec<String>>,
    }

    impl RecordingWriteback {
        fn calls(&self) -> Vec<String> {
            self.calls.lock().expect("calls lock").clone()
        }
    }

    #[async_trait]
    impl FriendshipWritebackPort for RecordingWriteback {
        async fn add_friendship(&self, caller: &str, target: &str) -> ServiceResult<()> {
            self.calls
                .lock()
                .expect("calls lock")
                .push(format!("add:{caller}:{target}"));
            Ok(())
        }

        async fn remove_friendship(&self, caller: &str, target: &str) -> ServiceResult<bool> {
            self.calls
                .lock()
                .expect("calls lock")
                .push(format!("remove:{caller}:{target}"));
            Ok(true)
        }
    }

    struct FailingWriteback;

    #[async_trait]
    impl FriendshipWritebackPort for FailingWriteback {
        async fn add_friendship(&self, _caller: &str, _target: &str) -> ServiceResult<()> {
            Err(ServiceError::InternalError(
                "friendship writeback unavailable".to_string(),
            ))
        }

        async fn remove_friendship(&self, _caller: &str, _target: &str) -> ServiceResult<bool> {
            Err(ServiceError::InternalError(
                "friendship writeback unavailable".to_string(),
            ))
        }
    }

    fn service_with_writeback(
        edge_grants: &Arc<dyn EdgeGrantRepoPort>,
        profiles: &Arc<dyn PermissionProfileRepoPort>,
        requests: &Arc<dyn PermissionRequestRepoPort>,
        bot_config: &Arc<dyn BotActorConfigRepoPort>,
        writeback: Arc<dyn FriendshipWritebackPort>,
    ) -> DbConnectService {
        service(edge_grants, profiles, requests, bot_config)
            .with_friendship_writeback(Some(writeback))
    }

    fn service_with_notification(
        edge_grants: &Arc<dyn EdgeGrantRepoPort>,
        profiles: &Arc<dyn PermissionProfileRepoPort>,
        requests: &Arc<dyn PermissionRequestRepoPort>,
        bot_config: &Arc<dyn BotActorConfigRepoPort>,
        notification: Arc<dyn FriendConnectNotificationPort>,
    ) -> DbConnectService {
        DbConnectService::new(
            edge_grants.clone(),
            profiles.clone(),
            requests.clone(),
            bot_config.clone(),
            None,
            notification,
            Arc::new(bcs_service_api::NoopFriendAuthSyncPort),
            "dev".to_string(),
        )
    }

    async fn seed_bot(
        db: &Arc<LocalSqliteDbPlugin>,
        bot_uuid: &str,
        visibility: &str,
        user_visibility: &str,
        friend_check_in_strategy: &str,
        status: &str,
        created_by: Option<&str>,
    ) {
        seed_bot_with_friend_ext(
            db,
            bot_uuid,
            visibility,
            user_visibility,
            friend_check_in_strategy,
            status,
            created_by,
            serde_json::Map::new(),
        )
        .await;
    }

    async fn seed_bot_with_friend_ext(
        db: &Arc<LocalSqliteDbPlugin>,
        bot_uuid: &str,
        visibility: &str,
        user_visibility: &str,
        friend_check_in_strategy: &str,
        status: &str,
        created_by: Option<&str>,
        friend_ext: serde_json::Map<String, serde_json::Value>,
    ) {
        let friend_ext_json = serde_json::to_string(&friend_ext).expect("friend_ext json");
        let bot_info = serde_json::json!({
            "friend_check_in_strategy": friend_check_in_strategy,
            "friend_ext": friend_ext,
        });
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_bots \
             (bot_uuid, env, name, visibility, user_visibility, friend_check_in_strategy, bot_info, friend_ext, status, created_by) \
             VALUES (?, 'dev', ?, ?, ?, ?, ?, ?, ?, ?)",
            vec![
                DbValue::from(bot_uuid),
                DbValue::from(bot_uuid),
                DbValue::from(visibility),
                DbValue::from(user_visibility),
                DbValue::from(friend_check_in_strategy),
                DbValue::from(serde_json::to_string(&bot_info).expect("bot_info json")),
                DbValue::from(friend_ext_json),
                DbValue::from(status),
                match created_by {
                    Some(v) => DbValue::from(v),
                    None => DbValue::Null,
                },
            ],
        ))
        .await
        .expect("seed bot");
    }

    #[tokio::test]
    async fn actor_kind_helper() {
        assert_eq!(actor_kind_of("human_88001"), ActorKind::Human);
        assert_eq!(actor_kind_of("20260421_x:85020"), ActorKind::Bot);
        // BCS-native bot uuids (no `:`) now fall back to Bot (A1 fix) instead
        // of None — the direction gate in create_connect rejects invalid dirs,
        // not actor_kind_of.
        assert_eq!(actor_kind_of("plainbot"), ActorKind::Bot);
    }

    #[tokio::test]
    async fn edge_permission_friend_sync_adds_and_removes_bot_friend_edges() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:syncA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:syncB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("x:syncA", "x:syncB")
            .await
            .expect("sync add");

        assert!(eg.has_friend_edge("x:syncA", "x:syncB", "dev").await);
        assert_eq!(eg.list_active_grants("x:syncA", "x:syncB", "dev").await.len(), 1);
        assert_eq!(eg.list_active_grants("x:syncB", "x:syncA", "dev").await.len(), 1);

        svc.sync_remove_friendship("x:syncA", "x:syncB")
            .await
            .expect("sync remove");

        assert!(!eg.has_friend_edge("x:syncA", "x:syncB", "dev").await);
    }

    #[tokio::test]
    async fn edge_permission_friend_sync_normalizes_human_bot_direction() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:syncBot", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("x:syncBot", "human_1")
            .await
            .expect("sync add");

        assert!(eg.has_friend_edge("human_1", "x:syncBot", "dev").await);
        assert_eq!(eg.list_active_grants("human_1", "x:syncBot", "dev").await.len(), 1);
        assert!(eg.list_active_grants("x:syncBot", "human_1", "dev").await.is_empty());
    }

    #[tokio::test]
    async fn cannot_add_self() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("bot_a:1", "bot_a:1", None, None)
            .await
            .expect_err("self-add rejected");
        assert!(matches!(err, ServiceError::CannotAddSelf), "got {err:?}");
    }

    #[tokio::test]
    async fn human_to_human_rejected() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "human_2", None, None)
            .await
            .expect_err("human→human rejected");
        assert!(
            matches!(err, ServiceError::InvalidOperation { .. }),
            "got {err:?}"
        );
    }

    #[tokio::test]
    async fn request_lookup_miss_paths_and_env_scope() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        assert_eq!(svc.env(), "dev");

        let err = svc
            .approve("missing-request-approve", "decider_1", None)
            .await
            .expect_err("approve missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");

        let err = svc
            .reject("missing-request-reject", "decider_2", None)
            .await
            .expect_err("reject missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");

        let err = svc
            .cancel("missing-request-cancel")
            .await
            .expect_err("cancel missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");

        let err = svc
            .get_request("missing-request-get")
            .await
            .expect_err("get_request missing request should fail");
        assert!(matches!(err, ServiceError::FriendRequestNotFound(_)), "got {err:?}");
    }

    #[tokio::test]
    async fn bot_to_human_rejected() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("x:1", "human_2", None, None)
            .await
            .expect_err("bot→human rejected");
        assert!(
            matches!(err, ServiceError::InvalidOperation { .. }),
            "got {err:?}"
        );
    }

    #[tokio::test]
    async fn bot_not_found() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:missing", None, None)
            .await
            .expect_err("missing bot → BotNotFound");
        assert!(
            matches!(err, ServiceError::BotNotFound(_)),
            "got {err:?}"
        );
    }

    #[tokio::test]
    async fn hidden_bot_rejected() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:hidden", "public", "protected", "OPEN", "hidden", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:hidden", None, None)
            .await
            .expect_err("hidden → BotHidden");
        assert!(matches!(err, ServiceError::BotHidden(_)), "got {err:?}");
    }

    #[tokio::test]
    async fn private_bot_rejected() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // visibility=private blocks bot→bot collaboration (a bot adding a
        // private-visibility bot). Human callers are gated by `user_visibility`,
        // not `visibility` — see user_visibility_private_for_human_caller and
        // human_adds_visibility_private_user_visibility_public_bot_succeeds.
        seed_bot(&db, "x:priv", "private", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("caller_bot:1", "x:priv", None, None)
            .await
            .expect_err("bot→private visibility → PrivateBotCannotCollaborate");
        assert!(
            matches!(err, ServiceError::PrivateBotCannotCollaborate),
            "got {err:?}"
        );
    }

    #[tokio::test]
    async fn user_visibility_private_for_human_caller() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:nha", "protected", "private", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let err = svc
            .create_connect("human_1", "x:nha", None, None)
            .await
            .expect_err("user_visibility=private → Forbidden for human caller");
        assert!(matches!(err, ServiceError::Forbidden(_)), "got {err:?}");
    }

    #[tokio::test]
    async fn human_adds_visibility_private_user_visibility_public_bot_succeeds() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // visibility=private no longer blocks a human caller — humans are gated by
        // `user_visibility=public`, so this bot is human-addable (mirrors the
        // /bots/search viewer-kind selection).
        seed_bot(&db, "x:privuvis", "private", "public", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:privuvis", None, None)
            .await
            .expect("human→(visibility=private, user_visibility=public) is human-addable");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1);
        assert!(eg.has_friend_edge("human_1", "x:privuvis", "dev").await);
    }

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

    #[tokio::test]
    async fn pending_friend_request_emits_notification_to_target_owner() {

        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pending_notify", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        let request_auth = RequestAuthHeaders { authorization: Some("Bearer user-token".to_string()), cookie: Some("session=abc".to_string()), forwarded_headers: Vec::new() };
        let res = svc
            .create_connect("human_1", "x:pending_notify", Some("hi".into()), Some(request_auth.clone()))
            .await
            .expect("manual pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        let event = &events[0];
        assert_eq!(event.kind, FriendConnectNotificationKind::ApprovalRequested);
        assert_eq!(event.env, "dev");
        assert_eq!(event.request_ids, res.request_ids);
        assert_eq!(event.applicant_actor_id, "human_1");
        assert_eq!(event.target_bot_id, "x:pending_notify");
        assert_eq!(event.recipient_user_ids, vec!["85020".to_string()]);
        assert_eq!(event.message.as_deref(), Some("hi"));
        assert_eq!(event.request_auth, Some(request_auth));
    }

    #[tokio::test]
    async fn friend_connect_notification_resolves_applicant_and_target_names() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // Target bot: seed (default name = bot_uuid) then set a human-friendly name.
        seed_bot(&db, "x:expert", "protected", "public", "APPROVAL", "online", Some("85020")).await;
        db.execute(DbStatement::with_params(
            "UPDATE bcs_bots SET name = ? WHERE bot_uuid = ?",
            vec![DbValue::from("本地代码专家"), DbValue::from("x:expert")],
        ))
        .await
        .expect("set target bot name");
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let user_directory: Arc<dyn UserDirectoryPlugin> =
            Arc::new(FixedNickUserDirectoryPlugin { nick: "李四".to_string() });
        let svc = DbConnectService::new(
            eg.clone(),
            pp.clone(),
            rq.clone(),
            bc.clone(),
            Some(user_directory),
            Arc::new(recorder),
            Arc::new(bcs_service_api::NoopFriendAuthSyncPort),
            "dev".to_string(),
        );
        // Human applicant (nick 李四) → bot "本地代码专家" (owner 85020); APPROVAL → pending.
        svc.create_connect("human_12345", "x:expert", None, None)
            .await
            .expect("manual pending connect");
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, FriendConnectNotificationKind::ApprovalRequested);
        // applicant nick resolved via the user directory; target name via bot config.
        assert_eq!(events[0].applicant_name.as_deref(), Some("李四"));
        assert_eq!(events[0].target_bot_name.as_deref(), Some("本地代码专家"));
        assert_eq!(events[0].recipient_user_ids, vec!["85020".to_string()]);
    }

    #[tokio::test]
    async fn friend_connect_notification_falls_back_when_user_directory_absent() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // No UPDATE → name stays the seed default (= bot_uuid).
        seed_bot(&db, "x:noexp", "protected", "public", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        // service_with_notification wires user_directory = None.
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        svc.create_connect("human_12345", "x:noexp", None, None)
            .await
            .expect("manual pending connect");
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(
            events[0].applicant_name,
            None,
            "no user directory → applicant name unresolved (falls back to id)"
        );
        // Target name still resolves from the bot's config (seed default = bot_uuid).
        assert_eq!(events[0].target_bot_name.as_deref(), Some("x:noexp"));
    }

    #[tokio::test]
    async fn friend_connect_notification_carries_no_human_applicant_for_a_bot() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // The applicant bot HAS a historical creator (152819) — which
        // is not necessarily the user performing this request — and the
        // target bot is owned by 85020.
        seed_bot(&db, "x:applicant", "protected", "public", "APPROVAL", "online", Some("152819")).await;
        seed_bot(&db, "x:target", "protected", "public", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        // Bot→Bot pending connect (APPROVAL strategy → needs approval → ApprovalRequested).
        svc.create_connect("x:applicant", "x:target", None, None)
            .await
            .expect("bot→bot manual pending connect");
        let events = events.lock().await;
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].kind, FriendConnectNotificationKind::ApprovalRequested);
        // A Bot applicant carries NO Human applicant id: the work-order
        // intake derives the acting Human (owner/manager performing the
        // request) from the verified principal — the Bot's historical
        // `created_by` (152819 here) must never leak into it.
        assert_eq!(events[0].applicant_user_id, None);
        assert_eq!(events[0].applicant_actor_id, "x:applicant");
        assert_eq!(events[0].target_bot_id, "x:target");
        // Approver/recipient = the target bot's owner.
        assert_eq!(events[0].recipient_user_ids, vec!["85020".to_string()]);
    }

    #[tokio::test]
    async fn pending_notification_is_not_duplicated_on_idempotent_create() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pending_idem", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let recorder = RecordingFriendConnectNotificationPort::default();
        let events = recorder.events.clone();
        let svc = service_with_notification(&eg, &pp, &rq, &bc, Arc::new(recorder));
        let first = svc
            .create_connect("human_1", "x:pending_idem", None, None)
            .await
            .expect("first pending");
        let second = svc
            .create_connect("human_1", "x:pending_idem", None, None)
            .await
            .expect("idempotent pending");
        assert_eq!(first.request_ids, second.request_ids);
        assert_eq!(events.lock().await.len(), 1);
    }

    #[tokio::test]
    async fn human_to_bot_manual_returns_pending_one_request_no_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:man", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:man", Some("hi".into()), None)
            .await
            .expect("manual → Pending");
        assert_eq!(res.status, ConnectStatus::Pending);
        assert_eq!(res.request_ids.len(), 1, "exactly one pending request");
        assert!(res.edge_ids.is_empty());
        // default profile should NOT have been seeded (no edge built).
        assert!(pp.get_active_default("x:man", "dev").await.is_none());
        let id = res.request_ids[0].clone();
        let r = rq.get(&id, "dev").await.expect("pending request exists");
        assert_eq!(r.status, RequestStatus::Pending);
        assert_eq!(r.from_id, "human_1");
        assert_eq!(r.to_id, "x:man");
        assert!(r.edge_id.is_none());
    }

    #[tokio::test]
    async fn human_to_bot_auto_approves_one_edge_one_request() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:auto1", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("human_1", "x:auto1", None, None)
            .await
            .expect("auto → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert!(res.auto_accepted);
        assert_eq!(res.edge_ids.len(), 1, "Human→Bot: exactly 1 edge");
        assert_eq!(res.request_ids.len(), 1, "Human→Bot: 1 request");

        // The edge caller→to_bot references to_bot's default profile.
        let active = eg.list_active_grants("human_1", "x:auto1", "dev").await;
        assert_eq!(active.len(), 1);
        let default_id = eg.get_default_profile_id("x:auto1", "dev").await;
        assert_eq!(active[0].grant_ref_id, default_id.unwrap());
        assert!(eg.has_friend_edge("human_1", "x:auto1", "dev").await);

        // The request is approved, decided_by=auto, backfilled with edge_id.
        let r = rq.get(&res.request_ids[0], "dev").await.expect("approved req");
        assert_eq!(r.status, RequestStatus::Approved);
        assert_eq!(r.decided_by.as_deref(), Some("auto"));
        assert_eq!(r.edge_id, Some(res.edge_ids[0]));
    }

    #[tokio::test]
    async fn bot_to_bot_auto_approves_two_edges_two_requests() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:botA", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:botB", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let res = svc
            .create_connect("x:botA", "x:botB", None, None)
            .await
            .expect("Bot↔Bot auto → Approved");
        assert_eq!(res.status, ConnectStatus::Approved);
        assert_eq!(res.edge_ids.len(), 2, "Bot↔Bot: 2 edges");
        assert_eq!(res.request_ids.len(), 2, "Bot↔Bot: 2 requests");

        let fwd = eg.list_active_grants("x:botA", "x:botB", "dev").await;
        let rev = eg.list_active_grants("x:botB", "x:botA", "dev").await;
        assert_eq!(fwd.len(), 1);
        assert_eq!(rev.len(), 1);
        assert_eq!(
            fwd[0].grant_ref_id,
            eg.get_default_profile_id("x:botB", "dev").await.unwrap()
        );
        assert_eq!(
            rev[0].grant_ref_id,
            eg.get_default_profile_id("x:botA", "dev").await.unwrap()
        );
        assert!(eg.has_friend_edge("x:botA", "x:botB", "dev").await);
    }

    #[tokio::test]
    async fn already_friends_is_idempotent_approved() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:idem", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let first = svc
            .create_connect("human_1", "x:idem", None, None)
            .await
            .expect("first connect");
        assert_eq!(first.status, ConnectStatus::Approved);
        let second = svc
            .create_connect("human_1", "x:idem", None, None)
            .await
            .expect("second connect idempotent");
        assert_eq!(second.status, ConnectStatus::Approved);
        assert!(second.edge_ids.is_empty(), "no new edge on idempotent");
        assert!(second.request_ids.is_empty(), "no new request on idempotent");
        // Exactly one edge overall.
        let active = eg.list_active_grants("human_1", "x:idem", "dev").await;
        assert_eq!(active.len(), 1);
    }

    #[tokio::test]
    async fn pending_connect_is_idempotent_pending() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pend", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let first = svc
            .create_connect("human_1", "x:pend", None, None)
            .await
            .expect("first manual");
        assert_eq!(first.status, ConnectStatus::Pending);
        let second = svc
            .create_connect("human_1", "x:pend", None, None)
            .await
            .expect("second idempotent");
        assert_eq!(second.status, ConnectStatus::Pending);
        // Returns the SAME pending request id (no duplicate insert).
        assert_eq!(first.request_ids, second.request_ids);
    }

    #[tokio::test]
    async fn approve_pending_human_to_bot_builds_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:appr", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:appr", None, None)
            .await
            .expect("manual pending");
        assert_eq!(pending.status, ConnectStatus::Pending);
        let rid = pending.request_ids[0].clone();

        let edge_ids = svc.approve(&rid, "85020", None).await.expect("approve ok");
        assert_eq!(edge_ids.len(), 1, "Human→Bot approve: 1 edge");
        // Original pending request is now approved + edge_id backfilled.
        let r = rq.get(&rid, "dev").await.expect("request still exists");
        assert_eq!(r.status, RequestStatus::Approved);
        assert_eq!(r.edge_id, Some(edge_ids[0]));
        assert!(eg.has_friend_edge("human_1", "x:appr", "dev").await);
        assert!(eg.list_active_grants("x:appr", "human_1", "dev").await.is_empty());
        let bot_friends = svc.list_friends("x:appr").await.expect("bot friends");
        assert_eq!(bot_friends.len(), 1);
        assert_eq!(bot_friends[0].actor_id, "human_1");
        assert_eq!(bot_friends[0].kind, ActorKind::Human);
        let human_friends = svc.list_friends("human_1").await.expect("human friends");
        assert_eq!(human_friends.len(), 1);
        assert_eq!(human_friends[0].actor_id, "x:appr");
        svc.revoke_friend("x:appr", "human_1", None).await.expect("bot removes friend");
        assert!(svc.list_friends("x:appr").await.expect("bot friends").is_empty());
        assert!(svc.list_friends("human_1").await.expect("human friends").is_empty());
    }

    #[tokio::test]
    async fn friend_page_service_preserves_single_edge_and_propagates_store_errors() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:paged", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        for human in ["human_2", "human_1"] {
            svc.create_connect(human, "x:paged", None, None).await.unwrap();
        }
        let query = FriendListQuery { target_type: Some(ActorKind::Human), offset: 1, limit: 1 };
        let page = svc.list_friends_paginated("x:paged", query).await.unwrap();
        assert_eq!(page.total, 2);
        assert_eq!(page.items.len(), 1);
        assert_eq!(page.items[0].actor_id, "human_2");
        assert_eq!(page.items[0].kind, ActorKind::Human);
        assert!(eg.list_active_grants("x:paged", "human_2", "dev").await.is_empty());
        let bots = svc.list_friends_paginated("x:paged", FriendListQuery {
            target_type: Some(ActorKind::Bot), offset: 0, limit: 20,
        }).await.unwrap();
        assert_eq!(bots.total, 0);
        assert!(bots.items.is_empty());
        svc.revoke_friend("x:paged", "human_1", None).await.unwrap();
        let empty = svc.list_friends_paginated("x:paged", query).await.unwrap();
        assert_eq!(empty.total, 1);
        assert!(empty.items.is_empty());
        db.execute(DbStatement::new("DROP TABLE edge_grants")).await.unwrap();
        assert!(matches!(svc.list_friends_paginated("x:paged", query).await,
            Err(ServiceError::InternalError(_))));
    }

    #[tokio::test]
    async fn approve_does_not_duplicate_request_rows() {
        // The approve path must decide the existing pending row in place —
        // NOT insert a second approved snapshot row (only the create_connect
        // auto path inserts snapshots). Total connect request rows for this
        // Human→Bot connect must remain 1 after approve.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:nodupe", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:nodupe", None, None)
            .await
            .expect("manual pending");
        let rid = pending.request_ids[0].clone();

        svc.approve(&rid, "85020", None).await.expect("approve ok");

        // The bot's inbox (to_id=x:nodupe) should contain exactly 1 request
        // for this connect (the original, now approved) — not 2.
        let inbox = rq.list_inbox("x:nodupe", "dev", None).await;
        let ours: Vec<&PermissionRequest> = inbox
            .iter()
            .filter(|r| r.from_id == "human_1")
            .collect();
        assert_eq!(ours.len(), 1, "approve must not duplicate the request row");
        assert_eq!(ours[0].status, RequestStatus::Approved);
    }

    #[tokio::test]
    async fn approve_bot_to_bot_approves_both_and_builds_two_edges() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:bbA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:bbB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:bbA", "x:bbB", None, None)
            .await
            .expect("manual pending Bot↔Bot");
        assert_eq!(pending.request_ids.len(), 2);
        let fwd_id = pending.request_ids[0].clone();

        let edge_ids = svc.approve(&fwd_id, "owner", None).await.expect("approve ok");
        assert_eq!(edge_ids.len(), 2, "Bot↔Bot approve: 2 edges");

        // BOTH pending requests are now approved (single accept, §4.1).
        let fwd = rq.get(&fwd_id, "dev").await.expect("fwd present");
        let rev = rq
            .get(&pending.request_ids[1], "dev")
            .await
            .expect("rev present");
        assert_eq!(fwd.status, RequestStatus::Approved);
        assert_eq!(rev.status, RequestStatus::Approved);
        assert!(eg.has_friend_edge("x:bbA", "x:bbB", "dev").await);
    }

    #[tokio::test]
    async fn reject_does_not_build_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:rej", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:rej", None, None)
            .await
            .expect("manual pending");
        let rid = pending.request_ids[0].clone();
        svc.reject(&rid, "85020", Some("no thanks".into()))
            .await
            .expect("reject ok");
        let r = rq.get(&rid, "dev").await.expect("request present");
        assert_eq!(r.status, RequestStatus::Rejected);
        assert!(r.edge_id.is_none());
        let active = eg.list_active_grants("human_1", "x:rej", "dev").await;
        assert!(active.is_empty(), "no edge built on reject");
    }

    #[tokio::test]
    async fn reject_bot_to_bot_rejects_both() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:rbA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:rbB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:rbA", "x:rbB", None, None)
            .await
            .expect("pending");
        svc.reject(&pending.request_ids[0], "owner", None)
            .await
            .expect("reject ok");
        let fwd = rq.get(&pending.request_ids[0], "dev").await.expect("fwd");
        let rev = rq.get(&pending.request_ids[1], "dev").await.expect("rev");
        assert_eq!(fwd.status, RequestStatus::Rejected);
        assert_eq!(rev.status, RequestStatus::Rejected);
    }

    #[tokio::test]
    async fn cancel_only_pending() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:canc", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:canc", None, None)
            .await
            .expect("pending");
        let rid = pending.request_ids[0].clone();
        svc.cancel(&rid).await.expect("cancel ok");
        let r = rq.get(&rid, "dev").await.expect("request still exists");
        assert_eq!(r.status, RequestStatus::Cancelled);
        // cancelling an already-cancelled request is idempotent (B4e): Ok, not
        // an error. Spec says "已 rejected/cancelled 幂等".
        svc.cancel(&rid).await.expect("idempotent cancel ok");
        let r2 = rq.get(&rid, "dev").await.expect("request still exists");
        assert_eq!(r2.status, RequestStatus::Cancelled);
    }

    #[tokio::test]
    async fn sync_add_friendship_bot_to_bot_writes_two_default_edges_idempotently() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("x:syncA", "x:syncB").await.expect("sync add");
        svc.sync_add_friendship("x:syncA", "x:syncB").await.expect("sync add idempotent");

        let a_to_b = eg.list_active_grants("x:syncA", "x:syncB", "dev").await;
        let b_to_a = eg.list_active_grants("x:syncB", "x:syncA", "dev").await;
        assert_eq!(a_to_b.len(), 1);
        assert_eq!(b_to_a.len(), 1);
        assert_eq!(
            a_to_b[0].grant_ref_id,
            pp.get_active_default("x:syncB", "dev").await.unwrap().permission_profile_id
        );
        assert_eq!(
            b_to_a[0].grant_ref_id,
            pp.get_active_default("x:syncA", "dev").await.unwrap().permission_profile_id
        );
    }

    #[tokio::test]
    async fn sync_add_friendship_normalizes_bot_human_to_human_bot_edge() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("x:human_target", "human_1").await.expect("sync add");

        let human_to_bot = eg.list_active_grants("human_1", "x:human_target", "dev").await;
        let bot_to_human = eg.list_active_grants("x:human_target", "human_1", "dev").await;
        assert_eq!(human_to_bot.len(), 1);
        assert!(bot_to_human.is_empty(), "Bot→Human edge must not be created");
        assert_eq!(
            human_to_bot[0].grant_ref_id,
            pp.get_active_default("x:human_target", "dev").await.unwrap().permission_profile_id
        );
    }

    #[tokio::test]
    async fn sync_add_friendship_uses_service_env() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service_in_env(&eg, &pp, &rq, &bc, "pre");

        svc.sync_add_friendship("x:envA", "x:envB").await.expect("sync add");

        assert!(eg.has_friend_edge("x:envA", "x:envB", "pre").await);
        assert!(!eg.has_friend_edge("x:envA", "x:envB", "dev").await);
        assert!(pp.get_active_default("x:envA", "pre").await.is_some());
        assert!(pp.get_active_default("x:envA", "dev").await.is_none());
    }

    #[tokio::test]
    async fn sync_remove_friendship_revokes_only_default_friend_edges() {
        let (eg, pp, rq, bc, _db) = assemble().await;
        let svc = service(&eg, &pp, &rq, &bc);

        svc.sync_add_friendship("human_1", "x:sync_keep").await.expect("sync add");
        eg.insert_grant(EdgeGrant {
            edge_id: 0,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:sync_keep".to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: 4002,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert non-friend edge");

        svc.sync_remove_friendship("human_1", "x:sync_keep").await.expect("sync remove");

        assert!(!eg.has_friend_edge("human_1", "x:sync_keep", "dev").await);
        let active = eg.list_active_grants("human_1", "x:sync_keep", "dev").await;
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].grant_ref_id, 4002);
    }

    #[tokio::test]
    async fn revoke_friend_human_to_bot_one_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:unf", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let created = svc
            .create_connect("human_1", "x:unf", None, None)
            .await
            .expect("auto connect");
        assert_eq!(created.edge_ids.len(), 1);
        assert!(eg.has_friend_edge("human_1", "x:unf", "dev").await);

        let n = svc.revoke_friend("human_1", "x:unf", None).await.expect("revoke ok");
        assert_eq!(n.len(), 1, "Human→Bot: revoked exactly 1 friend edge");
        assert!(!eg.has_friend_edge("human_1", "x:unf", "dev").await);
    }

    #[tokio::test]
    async fn revoke_friend_bot_to_bot_two_edges() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:uA", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:uB", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("x:uA", "x:uB", None, None).await.expect("connect");
        assert!(eg.has_friend_edge("x:uA", "x:uB", "dev").await);

        let n = svc.revoke_friend("x:uA", "x:uB", None).await.expect("revoke ok");
        assert_eq!(n.len(), 2, "Bot↔Bot: revoked both friend edges");
        assert!(!eg.has_friend_edge("x:uA", "x:uB", "dev").await);
    }

    #[tokio::test]
    async fn revoke_friend_leaves_non_default_edges() {
        // A non-default profile edge (grant_ref_id != default) must survive
        // revoke_friend (it is not a friend edge per D12).
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:keep", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("human_1", "x:keep", None, None).await.expect("connect");
        // Manually insert a writer-profile edge with a different ref id.
        eg.insert_grant(EdgeGrant {
            edge_id: 4001,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:keep".to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: 4002, // NOT the default
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert writer edge");

        let n = svc.revoke_friend("human_1", "x:keep", None).await.expect("revoke");
        assert_eq!(n.len(), 1, "only the friend (default) edge revoked");
        let active = eg.list_active_grants("human_1", "x:keep", "dev").await;
        assert_eq!(active.len(), 1, "writer edge survives");
        assert_eq!(active[0].grant_ref_id, 4002);
    }

    #[tokio::test]
    async fn list_friends_after_connect() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:lf1", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:lf2", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        svc.create_connect("human_1", "x:lf1", None, None).await.expect("c1");
        svc.create_connect("human_1", "x:lf2", None, None).await.expect("c2");

        let friends = svc.list_friends("human_1").await.expect("list ok");
        let ids: Vec<String> = friends.iter().map(|f| f.actor_id.clone()).collect();
        let mut sorted = ids.clone();
        sorted.sort();
        assert_eq!(sorted, vec!["x:lf1".to_string(), "x:lf2".to_string()]);
        // Entries carry the right kind and no enrichment (T13 leaves it None).
        for f in &friends {
            assert_eq!(f.kind, ActorKind::Bot);
            assert!(f.name.is_none());
        }
    }

    #[tokio::test]
    async fn list_requests_received_returns_pending() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:lr", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:lr", None, None)
            .await
            .expect("pending");
        // The bot's inbox should list the pending request received.
        let page = svc
            .list_requests("x:lr", RequestDirection::Received, None, 1, 20)
            .await
            .expect("list ok");
        assert_eq!(page.total, 1);
        assert_eq!(page.items.len(), 1);
        assert_eq!(page.items[0].request_id, pending.request_ids[0]);
        // Status filter works.
        let page = svc
            .list_requests(
                "x:lr",
                RequestDirection::Received,
                Some(RequestStatus::Approved),
                1,
                20,
            )
            .await
            .expect("list ok");
        assert_eq!(page.total, 0, "no approved requests in inbox");
        // Sent direction is now backed by list_sent (B4d): the human caller's
        // outbox contains the pending request they just sent.
        let page = svc
            .list_requests("human_1", RequestDirection::Sent, None, 1, 20)
            .await
            .expect("list ok");
        assert_eq!(page.total, 1, "Sent direction backed by list_sent");
        assert_eq!(page.items[0].request_id, pending.request_ids[0]);
        assert_eq!(page.items[0].from_id, "human_1");
        assert_eq!(page.items[0].to_id, "x:lr");
    }

    #[tokio::test]
    async fn list_requests_pagination() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pg", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        // Create 3 separate human callers connecting to the same bot.
        for h in ["human_a", "human_b", "human_c"] {
            svc.create_connect(h, "x:pg", None, None).await.expect("pending");
        }
        let page1 = svc
            .list_requests("x:pg", RequestDirection::Received, None, 1, 2)
            .await
            .expect("ok");
        assert_eq!(page1.total, 3);
        assert_eq!(page1.items.len(), 2, "page_size=2");
        let page2 = svc
            .list_requests("x:pg", RequestDirection::Received, None, 2, 2)
            .await
            .expect("ok");
        assert_eq!(page2.items.len(), 1, "remainder on page 2");
    }

    // ---- AdmissionService (T14) -------------------------------------------

    /// Build a `DbAdmissionService` from the assembled stores.
    fn admission_service(
        eg: &Arc<dyn EdgeGrantRepoPort>,
        bc: &Arc<dyn BotActorConfigRepoPort>,
        pp: &Arc<dyn PermissionProfileRepoPort>,
    ) -> DbAdmissionService {
        DbAdmissionService::new(eg.clone(), bc.clone(), pp.clone())
    }

    #[tokio::test]
    async fn admission_bot_not_found() {
        let (eg, pp, _rq, bc, _db) = assemble().await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:missing", "originator", "dev")
            .await
            .expect("bot-not-found deny result");
        assert!(!r.allowed);
        assert!(r.grants.is_empty());
        assert_eq!(r.reason_code, AdmissionReason::BotNotFound);
        assert!(!r.public_default);
    }

    #[tokio::test]
    async fn admission_bot_hidden() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:hid", "public", "protected", "OPEN", "hidden", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:hid", "originator", "dev")
            .await
            .expect("hidden deny result");
        assert!(!r.allowed);
        assert!(r.grants.is_empty());
        assert_eq!(r.reason_code, AdmissionReason::BotHidden);
        assert!(!r.public_default);
    }

    #[tokio::test]
    async fn admission_friend_edge_allowed() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:fr", "protected", "protected", "OPEN", "online", Some("85020")).await;
        // Seed a friend edge by going through ConnectService (auto path),
        // which also ensures the target default profile and builds the edge.
        let conn = service(&eg, &pp, &rq, &bc);
        conn.create_connect("human_1", "x:fr", None, None)
            .await
            .expect("connect");
        assert!(eg.has_friend_edge("human_1", "x:fr", "dev").await);

        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:fr", "originator", "dev")
            .await
            .expect("friend-edge allow");
        assert!(r.allowed);
        assert_eq!(r.reason_code, AdmissionReason::Ok);
        assert!(!r.public_default, "friend-edge path is not public_default");
        assert_eq!(r.grants.len(), 1);
        assert_eq!(r.grants[0].source, GrantSource::EdgeGrant);
        assert_eq!(r.grants[0].kind, GrantKind::PermissionProfile);
        // revision/digest enriched from the profile store.
        assert!(r.grants[0].revision.is_some(), "revision enriched");
        assert!(r.grants[0].digest.is_some(), "digest enriched");
        // ref_id matches the cached default profile id.
        let default_id = eg.get_default_profile_id("x:fr", "dev").await.unwrap();
        assert_eq!(r.grants[0].ref_id, default_id);
    }

    #[tokio::test]
    async fn admission_public_default() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pub", "public", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:pub", "originator", "dev")
            .await
            .expect("public_default allow");
        assert!(r.allowed);
        assert_eq!(r.reason_code, AdmissionReason::PublicDefault);
        assert!(r.public_default);
        assert_eq!(r.grants.len(), 1);
        assert_eq!(r.grants[0].source, GrantSource::PublicDefault);
        assert!(r.grants[0].revision.is_some());
        assert!(r.grants[0].digest.is_some());
    }

    #[tokio::test]
    async fn admission_no_edge_protected_bot() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:prot", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:prot", "originator", "dev")
            .await
            .expect("no-edge deny");
        assert!(!r.allowed);
        assert!(r.grants.is_empty());
        assert_eq!(r.reason_code, AdmissionReason::NoEdge);
        assert!(!r.public_default);
    }

    #[tokio::test]
    async fn build_authz_context_with_active_edge() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:az", "protected", "protected", "OPEN", "online", Some("85020")).await;
        // Seed an approved friend edge.
        let conn = service(&eg, &pp, &rq, &bc);
        conn.create_connect("human_1", "x:az", None, None)
            .await
            .expect("connect");

        let svc = admission_service(&eg, &bc, &pp);
        let ctx = svc
            .build_authz_context("human_1", "x:az", "o", "task_1", "run_1", "dev")
            .await
            .expect("authz ctx");
        assert_eq!(ctx.from_id, "human_1");
        assert_eq!(ctx.to_id, "x:az");
        assert_eq!(ctx.task_id, "task_1");
        assert_eq!(ctx.run_id, "run_1");
        assert_eq!(ctx.env, "dev");
        assert_eq!(ctx.originator, "o");
        assert!(ctx.signature.is_none());
        assert!(ctx.grants.len() >= 1, "active edge present in grants");
        assert_eq!(ctx.grants[0].source, GrantSource::EdgeGrant);
        assert_eq!(ctx.grants[0].kind, GrantKind::PermissionProfile);
    }

    #[tokio::test]
    async fn build_authz_context_empty_for_protected_no_edge() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:prot2", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let ctx = svc
            .build_authz_context("human_1", "x:prot2", "o", "t", "r", "dev")
            .await
            .expect("ctx");
        assert!(
            ctx.grants.is_empty(),
            "protected bot with no edge: no grants, no public_default fallback"
        );
    }

    #[tokio::test]
    async fn build_authz_context_public_default_fallback() {
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:pub2", "public", "protected", "OPEN", "online", Some("85020")).await;
        let svc = admission_service(&eg, &bc, &pp);
        let ctx = svc
            .build_authz_context("human_1", "x:pub2", "o", "t", "r", "dev")
            .await
            .expect("ctx");
        assert_eq!(ctx.grants.len(), 1, "public bot: public_default grant injected");
        assert_eq!(ctx.grants[0].source, GrantSource::PublicDefault);
    }

    // ---- B4b: is_authorized distinguishes Rules-edge admission from friend ----

    #[tokio::test]
    async fn is_authorized_true_for_rules_edge_while_has_friend_edge_false() {
        // A GrantKind::Rules edge (NOT a default-profile edge) must admit via
        // `is_authorized` but NOT via `has_friend_edge`. This is the B4b
        // conformance check: admission uses is_authorized (edge superset),
        // friendship uses has_friend_edge (default-profile only).
        let (eg, pp, _rq, _bc, db) = assemble().await;
        seed_bot(&db, "x:rules", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        // Ensure the target has a default profile so has_friend_edge can resolve,
        // then insert a Rules edge (grant_kind=Rules, arbitrary ref) from→to.
        pp.ensure_default_profile("x:rules", "dev").await.expect("ensure default");
        eg.insert_grant(EdgeGrant {
            edge_id: 5001,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:rules".to_string(),
            grant_kind: GrantKind::Rules,
            grant_ref_id: 5003,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert rules edge");

        // is_authorized ⇒ true (any active edge from→to).
        assert!(
            eg.is_authorized("human_1", "x:rules", "dev").await,
            "Rules edge must authorize via is_authorized"
        );
        // has_friend_edge ⇒ false (Rules edge is not a default-profile friend edge).
        assert!(
            !eg.has_friend_edge("human_1", "x:rules", "dev").await,
            "Rules edge is not a friend edge (D12 default-profile only)"
        );
        // list_active_grants surfaces the Rules edge.
        let active = eg.list_active_grants("human_1", "x:rules", "dev").await;
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].grant_kind, GrantKind::Rules);
    }

    #[tokio::test]
    async fn admission_admits_via_rules_edge_without_friend_edge() {
        // Admission must admit via a Rules edge (is_authorized superset), even
        // when no friend (default-profile) edge exists. This is the behavioral
        // proof that check_admission uses is_authorized, not has_friend_edge.
        let (eg, pp, _rq, bc, db) = assemble().await;
        seed_bot(&db, "x:radm", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        pp.ensure_default_profile("x:radm", "dev").await.expect("ensure default");
        eg.insert_grant(EdgeGrant {
            edge_id: 5002,
            env: "dev".to_string(),
            from_id: "human_1".to_string(),
            to_id: "x:radm".to_string(),
            grant_kind: GrantKind::Rules,
            grant_ref_id: 5004,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .expect("insert rules edge");

        let svc = admission_service(&eg, &bc, &pp);
        let r = svc
            .check_admission("human_1", "x:radm", "originator", "dev")
            .await
            .expect("rules-edge admission");
        assert!(r.allowed, "Rules edge must admit via is_authorized");
        assert_eq!(r.reason_code, AdmissionReason::Ok);
        assert!(
            !r.public_default,
            "Rules-edge admission is not the public_default path"
        );
    }

    // ---- B4d: Sent + All directions of list_requests ----

    #[tokio::test]
    async fn list_requests_sent_and_all_with_status_filter() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:sa", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("human_1", "x:sa", Some("hi".into()), None)
            .await
            .expect("pending");
        let rid = pending.request_ids[0].clone();

        // Sent: the human caller's outbox has the pending request.
        let sent = svc
            .list_requests("human_1", RequestDirection::Sent, None, 1, 20)
            .await
            .expect("sent ok");
        assert_eq!(sent.total, 1);
        assert_eq!(sent.items[0].request_id, rid);
        assert_eq!(sent.items[0].from_id, "human_1");

        // Sent + Approved filter ⇒ empty (still pending).
        let sent_approved = svc
            .list_requests(
                "human_1",
                RequestDirection::Sent,
                Some(RequestStatus::Approved),
                1,
                20,
            )
            .await
            .expect("sent approved ok");
        assert_eq!(sent_approved.total, 0);

        // All from the human caller's view: inbox (none for human_1) ∪ sent
        // (1) ⇒ total 1.
        let all = svc
            .list_requests("human_1", RequestDirection::All, None, 1, 20)
            .await
            .expect("all ok");
        assert_eq!(all.total, 1);
        assert_eq!(all.items[0].request_id, rid);

        // Approve, then All from the bot's view: inbox (1 approved) ∪ sent
        // (the bot sent nothing) ⇒ total 1, status approved.
        svc.approve(&rid, "85020", None).await.expect("approve");
        let all_bot = svc
            .list_requests("x:sa", RequestDirection::All, None, 1, 20)
            .await
            .expect("all bot ok");
        assert_eq!(all_bot.total, 1);
        assert_eq!(all_bot.items[0].status, RequestStatus::Approved);
    }

    #[tokio::test]
    async fn list_requests_all_dedupes_and_appplies_pagination() {
        // All = inbox ∪ sent, deduped by request_id. For a Bot↔Bot connect,
        // each side's All view sees both rows (one from_id, one to_id) — no
        // dedup collapse (distinct request_ids), exercising the union + sort.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:btA", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "x:btB", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        let svc = service(&eg, &pp, &rq, &bc);
        let pending = svc
            .create_connect("x:btA", "x:btB", None, None)
            .await
            .expect("pending bot↔bot");
        assert_eq!(pending.request_ids.len(), 2);

        // btA's All view: inbox (btB→btA) ∪ sent (btA→btB) ⇒ 2 rows.
        let all = svc
            .list_requests("x:btA", RequestDirection::All, None, 1, 20)
            .await
            .expect("all ok");
        assert_eq!(all.total, 2);
        assert_eq!(all.items.len(), 2);
        // Pagination: page_size=1 returns 1 item, total stays 2.
        let page1 = svc
            .list_requests("x:btA", RequestDirection::All, None, 1, 1)
            .await
            .expect("page1 ok");
        assert_eq!(page1.total, 2);
        assert_eq!(page1.items.len(), 1);
        let page2 = svc
            .list_requests("x:btA", RequestDirection::All, None, 2, 1)
            .await
            .expect("page2 ok");
        assert_eq!(page2.items.len(), 1);
        // The two pages return distinct request_ids (union order is stable
        // within a run: gmt_modified DESC).
        assert_ne!(page1.items[0].request_id, page2.items[0].request_id);

    // ---- Edge→legacy friendship writeback reverse sync ----

    #[tokio::test]
    async fn auto_approved_bot_bot_connect_writebacks_legacy_friendship() {
        let (eg, pp, rq, bc, db) = assemble().await;
        // OPEN strategy → needs_approval false → auto-approve inside create_connect.
        seed_bot(&db, "x:autoA", "protected", "protected", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:autoB", "protected", "protected", "OPEN", "online", Some("85020")).await;
        let recorder = Arc::new(RecordingWriteback::default());
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

        let result = svc
            .create_connect("x:autoA", "x:autoB", None, None)
            .await
            .expect("auto connect");

        assert_eq!(result.status, ConnectStatus::Approved);
        assert_eq!(
            recorder.calls(),
            vec!["add:x:autoA:x:autoB".to_string()],
            "auto-approve must mirror the Bot↔Bot pair into the legacy store"
        );
    }

    #[tokio::test]
    async fn human_bot_connect_does_not_writeback() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:api", "public", "public", "OPEN", "online", Some("85020")).await;
        let recorder = Arc::new(RecordingWriteback::default());
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

        let result = svc
            .create_connect("human_1", "x:api", None, None)
            .await
            .expect("human connect");

        assert_eq!(result.status, ConnectStatus::Approved);
        assert!(
            recorder.calls().is_empty(),
            "Human→Bot edges are out of writeback scope"
        );
    }

    #[tokio::test]
    async fn manual_approve_writebacks_and_idempotent_approve_backfills() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "bot_82ce3565", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "20260525_tzgcxt13:195302", "protected", "protected", "APPROVAL", "online", Some("330172")).await;
        let recorder = Arc::new(RecordingWriteback::default());
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

        let result = svc
            .create_connect("bot_82ce3565", "20260525_tzgcxt13:195302", None, None)
            .await
            .expect("pending connect");
        assert_eq!(result.status, ConnectStatus::Pending);

        let request_id = result.request_ids.first().expect("pending request id").clone();
        svc.approve(&request_id, "human_330172", None)
            .await
            .expect("manual approve");
        assert_eq!(
            recorder.calls(),
            vec!["add:bot_82ce3565:20260525_tzgcxt13:195302".to_string()]
        );

        // Idempotent re-approve hits the already-Approved branch and backfills the
        // legacy row (covers friendships approved before this sync shipped).
        recorder.calls.lock().expect("clear").clear();
        svc.approve(&request_id, "human_330172", None)
            .await
            .expect("idempotent approve");
        assert_eq!(
            recorder.calls(),
            vec!["add:bot_82ce3565:20260525_tzgcxt13:195302".to_string()],
            "idempotent approve must backfill the legacy row"
        );
    }

    #[tokio::test]
    async fn revoke_friend_writebacks_removal() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:r1", "public", "public", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:r2", "public", "public", "OPEN", "online", Some("85020")).await;
        let recorder = Arc::new(RecordingWriteback::default());
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

        svc.create_connect("x:r1", "x:r2", None, None)
            .await
            .expect("connect");
        recorder.calls.lock().expect("clear").clear();

        svc.revoke_friend("x:r1", "x:r2", None)
            .await
            .expect("revoke");

        assert_eq!(
            recorder.calls(),
            vec!["remove:x:r1:x:r2".to_string()],
            "unfriend must drop the legacy friendship row"
        );
    }

    #[tokio::test]
    async fn connect_flow_keeps_legacy_friendship_in_sync() {
        // Composition mirrors the DB profile in bootstrap/server.rs: DbConnectService
        // with the real FriendshipWritebackService over a shared FriendRepoPort.
        // The plain-token bot ids reproduce the original 403 payload shape.
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "bot_82ce3565", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
        seed_bot(&db, "20260525_tzgcxt13:195302", "protected", "protected", "APPROVAL", "online", Some("330172")).await;

        let friend_repo: Arc<bcs_friend::MemoryFriendRepo> =
            Arc::new(bcs_friend::MemoryFriendRepo::new());
        let writeback = Arc::new(bcs_friend::FriendshipWritebackService::new(
            friend_repo.clone(),
            Arc::new(bcs_test_support::NoopRelationCoreService),
        ));
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, writeback);

        // Before: the legacy gate sees no friendship (the original 403 state).
        assert!(!friend_repo
            .are_friends("bot_82ce3565", "20260525_tzgcxt13:195302")
            .await
            .expect("are_friends before"));

        let result = svc
            .create_connect("bot_82ce3565", "20260525_tzgcxt13:195302", None, None)
            .await
            .expect("connect");
        assert_eq!(result.status, ConnectStatus::Pending);

        let request_id = result.request_ids.first().expect("pending request id").clone();
        svc.approve(&request_id, "human_330172", None)
            .await
            .expect("approve");

        // After approve: the legacy gate (bcs-group ensure_v1_reachable reads
        // this exact repo/port) now sees the friendship → group creation passes.
        assert!(friend_repo
            .are_friends("bot_82ce3565", "20260525_tzgcxt13:195302")
            .await
            .expect("are_friends after approve"));

        // After unfriend: the legacy row is gone → gate closes again.
        svc.revoke_friend("bot_82ce3565", "20260525_tzgcxt13:195302", None)
            .await
            .expect("revoke");
        assert!(!friend_repo
            .are_friends("bot_82ce3565", "20260525_tzgcxt13:195302")
            .await
            .expect("are_friends after revoke"));
    }

    #[tokio::test]
    async fn writeback_failure_is_best_effort() {
        let (eg, pp, rq, bc, db) = assemble().await;
        seed_bot(&db, "x:f1", "public", "public", "OPEN", "online", Some("85020")).await;
        seed_bot(&db, "x:f2", "public", "public", "OPEN", "online", Some("85020")).await;
        let svc = service_with_writeback(&eg, &pp, &rq, &bc, Arc::new(FailingWriteback));

        let result = svc
            .create_connect("x:f1", "x:f2", None, None)
            .await
            .expect("connect must succeed despite writeback failure");

        assert_eq!(result.status, ConnectStatus::Approved);
        assert!(eg.has_friend_edge("x:f1", "x:f2", "dev").await);

        // Remove path is also best-effort: unfriend succeeds even though the
        // writeback port fails, and the edge grant is torn down.
        svc.revoke_friend("x:f1", "x:f2", None)
            .await
            .expect("revoke must succeed despite writeback failure");
        assert!(!eg.has_friend_edge("x:f1", "x:f2", "dev").await);
    }

    // --- friend_ext scope department-reference parsing ------------------------
    //
    // Admin writes may persist department references as
    // `{"deptNo": "L3480", "deptName": "蚂蚁集团-信贷事业群"}` objects (the
    // shape found in production bcs_bots.friend_ext) while the parser only
    // accepted plain strings — silently reading the exemption allowlist as
    // empty and routing approval-exempt applicants into the approval flow.
}
