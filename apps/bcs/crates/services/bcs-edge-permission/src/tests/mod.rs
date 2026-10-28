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

    // --- friend_ext scope department-reference parsing ------------------------
    //
    // Admin writes may persist department references as
    // `{"deptNo": "L3480", "deptName": "蚂蚁集团-信贷事业群"}` objects (the
    // shape found in production bcs_bots.friend_ext) while the parser only
    // accepted plain strings — silently reading the exemption allowlist as
    // empty and routing approval-exempt applicants into the approval flow.
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

    // ---- AdmissionService (T14) -------------------------------------------

    /// Build a `DbAdmissionService` from the assembled stores.
    fn admission_service(
        eg: &Arc<dyn EdgeGrantRepoPort>,
        bc: &Arc<dyn BotActorConfigRepoPort>,
        pp: &Arc<dyn PermissionProfileRepoPort>,
    ) -> DbAdmissionService {
        DbAdmissionService::new(eg.clone(), bc.clone(), pp.clone())
    }


mod connect_validation;

mod department_scope;

mod department_exemption;

mod notifications;

mod connect_lifecycle;

mod admission;

mod request_pagination;

mod friendship_writeback;
