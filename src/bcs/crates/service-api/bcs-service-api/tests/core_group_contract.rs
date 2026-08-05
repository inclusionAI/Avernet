use bcs_service_api::{
    ActorKind, DmActorSpec, Group, GroupCoreService, GroupKind, GroupMessage,
    GroupMutableFieldsPatch, GroupStatus, Participant, ParticipantMode, ParticipantRole,
    ServiceError, Workspace,
};
use bcs_test_support::NoopGroupCoreService;

fn sample_group() -> Group {
    Group::new(
        "group-1",
        "driver",
        vec![
            Participant::bot("driver", ParticipantRole::Driver),
            Participant::bot("consultant", ParticipantRole::Consultant),
        ],
    )
}

fn sample_message() -> GroupMessage {
    GroupMessage {
        id: "msg-1".to_string(),
        timestamp: 0,
        sender: "driver".to_string(),
        content: "hello".to_string(),
        message_type: Default::default(),
        bot_name: None,
        role: Default::default(),
        history_meta: None,
        metadata: None,
        run_id: String::new(),
    }
}

#[tokio::test]
async fn noop_group_queries_and_default_helpers_are_empty() {
    let service = NoopGroupCoreService::default();

    assert!(service.get("missing").await.is_none());
    assert!(service.list().await.is_empty());
    assert!(service.list_paginated(0, 10).await.is_empty());
    assert!(service.find_by_participant("driver").await.is_empty());
    assert!(
        service
            .find_by_participant_paginated("driver", 0, 10)
            .await
            .is_empty()
    );
    assert_eq!(service.count().await, 0);
    assert_eq!(service.count_by_participant("driver").await, 0);
    assert_eq!(service.count_by_kind(None).await, 0);
    assert_eq!(service.count_by_kind(Some(GroupKind::Dm)).await, 0);
    assert!(
        service
            .list_paginated_by_kind(Some(GroupKind::Dm), 0, 10)
            .await
            .is_empty()
    );
    assert!(
        service
            .find_dm_by_pair_key("driver|consultant")
            .await
            .is_none()
    );
    assert_eq!(service.message_count("missing").await.unwrap(), 0);
    assert!(service.delete("missing").await.unwrap().is_none());
}

#[tokio::test]
async fn noop_group_mutations_fail_closed_on_missing_groups() {
    let service = NoopGroupCoreService::default();
    let missing = "missing";

    service.upsert(sample_group()).await.unwrap();
    assert!(service.get("group-1").await.is_none());
    assert!(matches!(
        service
            .patch_mutable_fields(missing, GroupMutableFieldsPatch::default())
            .await,
        Err(ServiceError::InvalidOperation { message, .. })
            if message == "atomic mutable Group patch is not configured"
    ));

    assert!(matches!(
        service.add_message(missing, sample_message()).await,
        Err(ServiceError::GroupNotFound(id)) if id == missing
    ));
    assert!(matches!(
        service
            .add_participant(
                missing,
                Participant::bot("observer", ParticipantRole::Observer),
            )
            .await,
        Err(ServiceError::GroupNotFound(id)) if id == missing
    ));
    assert!(matches!(
        service
            .insert_human_participant(missing, "human_staff001", ParticipantMode::Present)
            .await,
        Err(ServiceError::GroupNotFound(id)) if id == missing
    ));
    assert!(matches!(
        service
            .update_participant_mode(missing, "driver", ParticipantMode::Muted)
            .await,
        Err(ServiceError::GroupNotFound(id)) if id == missing
    ));
    assert!(matches!(
        service.update_workspace(missing, Workspace::default()).await,
        Err(ServiceError::GroupNotFound(id)) if id == missing
    ));
    assert!(matches!(
        service
            .update_label(missing, Some("renamed".to_string()))
            .await,
        Err(ServiceError::GroupNotFound(id)) if id == missing
    ));
    assert!(matches!(
        service.update_status(missing, GroupStatus::Completed).await,
        Err(ServiceError::GroupNotFound(id)) if id == missing
    ));
    assert!(matches!(
        service.terminate(missing, "driver").await,
        Err(ServiceError::GroupNotFound(id)) if id == missing
    ));
    let dm = service
        .create_or_reuse_actor_dm_group(
            "dm-1",
            DmActorSpec {
                actor_id: "human_alice".to_string(),
                actor_kind: ActorKind::Human,
                display_name: Some("Alice".to_string()),
            },
            DmActorSpec {
                actor_id: "driver".to_string(),
                actor_kind: ActorKind::Bot,
                display_name: Some("Driver".to_string()),
            },
            "driver",
            "human_alice",
            None,
            None,
        )
        .await;
    assert!(matches!(
        dm,
        Err(ServiceError::InvalidOperation { message, request_id: None })
            if message == "group core service is not configured"
    ));
}

#[tokio::test]
async fn noop_group_count_helpers_are_benign() {
    let service = NoopGroupCoreService::default();
    let missing = "missing";

    service.increment_message_count(missing).await.unwrap();
    service.reset_message_count(missing).await.unwrap();
}
