use super::*;

use bcs_service_api::ChannelBindingCleanupPort;

#[derive(Default)]
struct RecordingChannelBindingCleanup {
    deleted_group_ids: Mutex<Vec<String>>,
    deleted_bot_ids: Mutex<Vec<String>>,
    fail: bool,
}

impl RecordingChannelBindingCleanup {
    fn failing() -> Self {
        Self {
            deleted_group_ids: Mutex::new(Vec::new()),
            deleted_bot_ids: Mutex::new(Vec::new()),
            fail: true,
        }
    }
}

#[async_trait]
impl ChannelBindingCleanupPort for RecordingChannelBindingCleanup {
    async fn delete_bindings_for_group(&self, group_id: &str) -> ServiceResult<u64> {
        self.deleted_group_ids.lock().await.push(group_id.to_string());
        if self.fail {
            return Err(ServiceError::InternalError(
                "channel binding cleanup failed".to_string(),
            ));
        }
        Ok(1)
    }

    async fn delete_bindings_for_bot(&self, bot_id: &str) -> ServiceResult<u64> {
        self.deleted_bot_ids.lock().await.push(bot_id.to_string());
        if self.fail {
            return Err(ServiceError::InternalError(
                "channel binding cleanup failed".to_string(),
            ));
        }
        Ok(1)
    }
}

#[tokio::test]
async fn delete_group_enforces_legacy_driver_and_dm_rules() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", None);
    let service = fixture.service_with_limits(5, 10, 10);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    let wrong_driver = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "other".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect_err("only driver can delete a normal group");
    assert!(matches!(
        wrong_driver,
        GroupUseCaseError::Forbidden(_)
    ));

    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .unwrap();
    assert!(deleted.deleted);
    assert!(fixture.group.get("group-under-test").await.is_none());

    let mut dm = Group::new(
        "dm-under-test",
        "driver",
        vec![bcs_service_api::Participant::bot(
            "driver",
            bcs_service_api::ParticipantRole::Driver,
        )],
    );
    dm.group_kind = bcs_service_api::GroupKind::Dm;
    fixture.group.upsert(dm).await.unwrap();
    let dm_error = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "dm-under-test".to_string(),
        })
        .await
        .expect_err("dm groups keep the legacy delete prohibition");
    assert!(matches!(
        dm_error,
        GroupUseCaseError::InvalidProposal(message)
            if message.contains("DM groups")
    ));
}

#[tokio::test]
async fn delete_group_is_idempotent_when_group_is_missing() {
    let fixture = Fixture::new();
    let result = fixture
        .service_with_limits(5, 10, 10)
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "missing-group".to_string(),
        })
        .await
        .expect("missing group deletion should be idempotent");

    assert_eq!(result.group_id, "missing-group");
    assert!(!result.deleted);
}

#[tokio::test]
async fn delete_group_cleans_up_channel_bindings() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", None);
    let cleanup = Arc::new(RecordingChannelBindingCleanup::default());
    let service = fixture
        .service_with_limits(5, 10, 10)
        .with_channel_binding_cleanup(cleanup.clone());
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    let deleted = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect("group and channel bindings should be deleted together");

    assert!(deleted.deleted);
    assert!(fixture.group.get("group-under-test").await.is_none());
    assert_eq!(
        cleanup.deleted_group_ids.lock().await.as_slice(),
        ["group-under-test"]
    );
}

#[tokio::test]
async fn delete_group_keeps_committed_deletion_when_channel_binding_cleanup_fails() {
    let fixture = Fixture::new().with_bot("driver", "Driver", "public", None);
    let cleanup = Arc::new(RecordingChannelBindingCleanup::failing());
    let service = fixture
        .service_with_limits(5, 10, 10)
        .with_channel_binding_cleanup(cleanup);
    service
        .create_group(create_cmd(
            Some("driver"),
            "driver",
            vec![participant("driver", Some("driver"))],
        ))
        .await
        .unwrap();

    let error = service
        .delete_group(GroupDeleteCommand {
            caller_actor_id: "driver".to_string(),
            group_id: "group-under-test".to_string(),
        })
        .await
        .expect_err("binding cleanup failure must fail group deletion");

    assert!(matches!(
        error,
        GroupUseCaseError::Service(ServiceError::InternalError(ref message))
            if message == "channel binding cleanup failed"
    ));
    assert!(
        fixture.group.get("group-under-test").await.is_none(),
        "terminal Group deletion cannot be rolled back after its Event is committed"
    );
}
