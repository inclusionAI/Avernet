use super::*;

#[tokio::test]
async fn create_uses_the_authenticated_human_as_originator() {
    let fixture = Fixture::new().await;
    for bot in ["requester", "driver", "helper"] {
        fixture.add_public_bot(bot).await;
    }

    let detail = fixture
        .service
        .create(CreateGroup {
            caller: bot_principal("requester"),
            group: CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                human_mention_notify_mode: Default::default(),
                originator: None,
                name: Some("Planning".into()),
                context: Some("Plan the release".into()),
                opening_message: None,
                visibility: GroupVisibility::Private,
                driver_bot_uuid: "driver".into(),
                participants: vec![CreateParticipant {
                    actor_id: "helper".into(),
                    role: ParticipantRole::Consultant,
                    tags: vec![
                        " tenant-a ".to_string(),
                        "".to_string(),
                        "scene-review".to_string(),
                    ],
                    message_view_scope: None,
                }],
                collaboration: CollaborationConfiguration::Chat(ChatConfiguration {
                    delivery_policy: GroupDeliveryPolicy {
                        bot_final_delivery: BotFinalDelivery::SendToDriver,
                    },
                }),
            }),
        })
        .await
        .expect("requester may select a collaboration-eligible driver");

    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration detail");
    };
    assert_eq!(detail.driver_bot_uuid, "driver");
    assert_eq!(detail.originator_actor_id, "human_requester");
    assert_eq!(
        detail
            .participants
            .iter()
            .find(|participant| participant.actor_id == "helper")
            .expect("helper participant")
            .tags,
        vec!["tenant-a".to_string(), "scene-review".to_string()]
    );
    assert!(
        detail
            .participants
            .iter()
            .all(|p| p.actor_id != "requester")
    );
}
#[tokio::test]
async fn inline_subscription_create_uses_provisioning_and_finalizes_initial_session() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());
    let mut command = collaboration_create_command();
    let CreateGroupSpec::Collaboration(input) = &mut command.group else {
        panic!("expected normal Group");
    };
    input.human_mention_notify_mode = bcs_service_api::HumanMentionNotifyMode::None;

    let outcome = fixture
        .service
        .create_with_event_subscriptions(
            command,
            vec![inline_group_subscription()],
        )
        .await
        .expect("create Group with inline Subscription");

    assert!(outcome.created);
    let group_id = provisioner
        .prepared_group_ids
        .lock()
        .expect("provisioner lock")[0]
        .clone();
    assert_eq!(
        provisioner
            .finalized_group_ids
            .lock()
            .expect("provisioner lock")
            .as_slice(),
        [group_id.as_str()]
    );
    let group = fixture
        .groups
        .try_get(&group_id)
        .await
        .expect("load Group")
        .expect("created Group");
    assert_eq!(group.record_status, "active");
    assert_eq!(group.human_mention_notify_mode, bcs_service_api::HumanMentionNotifyMode::None);
    let sessions = fixture
        .sessions
        .list_by_group(&group_id, None, 0, 10, None, None)
        .await
        .expect("list initial Sessions");
    assert_eq!(sessions.len(), 1);
}
#[tokio::test]
async fn eventing_enabled_create_without_inline_subscription_still_finalizes_events() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    fixture
        .service
        .create(collaboration_create_command())
        .await
        .expect("create Group without an inline Subscription");

    let prepared = provisioner
        .prepared_group_ids
        .lock()
        .expect("provisioner lock");
    let finalized = provisioner
        .finalized_group_ids
        .lock()
        .expect("provisioner lock");
    assert_eq!(prepared.len(), 1);
    assert_eq!(finalized.as_slice(), prepared.as_slice());
}
#[tokio::test]
async fn bot_caller_creates_reads_updates_and_deletes_group() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("caller-bot").await;
    fixture.add_public_bot("driver").await;
    let caller = authenticated_bot_principal("caller-bot", "owner-1");
    let mut command = collaboration_create_command();
    command.caller = caller.clone();

    let detail = fixture
        .service
        .create(command)
        .await
        .expect("Bot Principal creates Group");

    let GroupDetail::Collaboration(detail) = detail else {
        panic!("expected collaboration Group");
    };
    assert_eq!(detail.originator_actor_id, "caller-bot");
    let group_id = detail.group_id;

    let read = fixture
        .service
        .get(GetGroup {
            caller: caller.clone(),
            group_id: group_id.clone(),
        })
        .await
        .expect("Bot Principal reads its Group");
    let GroupDetail::Collaboration(read) = read else {
        panic!("expected collaboration Group");
    };
    assert_eq!(read.group_id, group_id);

    let updated = fixture
        .service
        .update(UpdateGroup {
            caller: caller.clone(),
            group_id: group_id.clone(),
            patch: GroupPatch {
                name: Some("Bot managed".into()),
                context: None,
                opening_message: None,
                visibility: None,
                delivery_policy: None,
                human_mention_notify_mode: None,
            },
        })
        .await
        .expect("Bot Principal updates its Group");
    let GroupDetail::Collaboration(updated) = updated else {
        panic!("expected collaboration Group");
    };
    assert_eq!(updated.name.as_deref(), Some("Bot managed"));

    let deleted = fixture
        .service
        .delete(DeleteGroup {
            caller,
            group_id,
            acting_bot_id: Some("caller-bot".into()),
        })
        .await
        .expect("Bot Principal deletes its Group");
    assert!(deleted.deleted);
}
#[tokio::test]
async fn unrelated_bot_cannot_read_update_or_delete_group() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("caller-bot").await;
    fixture.add_public_bot("driver").await;
    let mut command = collaboration_create_command();
    command.caller = authenticated_bot_principal("caller-bot", "owner-1");
    let created = fixture
        .service
        .create(command)
        .await
        .expect("create Group as Bot");
    let GroupDetail::Collaboration(created) = created else {
        panic!("expected collaboration Group");
    };
    let group_id = created.group_id;

    let read = fixture
        .service
        .get(GetGroup {
            caller: authenticated_bot_principal("outsider", "owner-2"),
            group_id: group_id.clone(),
        })
        .await;
    assert!(matches!(read, Err(ApplicationError::Forbidden { .. })));

    let update = fixture
        .service
        .update(UpdateGroup {
            caller: authenticated_bot_principal("outsider", "owner-2"),
            group_id: group_id.clone(),
            patch: GroupPatch {
                name: Some("forged".into()),
                context: None,
                opening_message: None,
                visibility: None,
                delivery_policy: None,
                human_mention_notify_mode: None,
            },
        })
        .await;
    assert!(matches!(update, Err(ApplicationError::Forbidden { .. })));

    let delete = fixture
        .service
        .delete(DeleteGroup {
            caller: authenticated_bot_principal("outsider", "owner-2"),
            group_id,
            acting_bot_id: None,
        })
        .await;
    assert!(matches!(delete, Err(ApplicationError::Forbidden { .. })));
}
#[tokio::test]
async fn bot_caller_creates_group_when_eventing_is_enabled() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("caller-bot").await;
    fixture.add_public_bot("driver").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());
    let mut command = collaboration_create_command();
    command.caller = authenticated_bot_principal("caller-bot", "owner-1");

    let outcome = fixture
        .service
        .create_with_event_subscriptions(command, vec![inline_group_subscription()])
        .await
        .expect("Bot Principal creates Group with inline Subscription");

    let GroupDetail::Collaboration(detail) = outcome.group else {
        panic!("expected collaboration Group");
    };
    assert_eq!(detail.originator_actor_id, "caller-bot");
    assert_eq!(
        provisioner
            .prepared_group_ids
            .lock()
            .expect("provisioner lock")
            .len(),
        1
    );
}
#[tokio::test]
async fn mismatched_human_and_bot_caller_is_rejected_before_provisioning() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());
    let mut command = collaboration_create_command();
    command.caller = authenticated_bot_principal("caller-bot", "another-owner");
    command.caller.user = Some(AuthenticatedUserIdentity {
        id: "owner-1".into(),
        username: "owner-1".into(),
        display_name: None,
        full_name: None,
    });

    let error = fixture
        .service
        .create(command)
        .await
        .expect_err("mismatched User/Bot ownership must be rejected");

    assert!(matches!(error, ApplicationError::Forbidden { .. }));
    assert!(
        provisioner
            .prepared_group_ids
            .lock()
            .expect("provisioner lock")
            .is_empty()
    );
}
#[tokio::test]
async fn inline_subscription_dm_create_finalizes_without_an_initial_session() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("assistant").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    let outcome = fixture
        .service
        .create_with_event_subscriptions(dm_create_command(), vec![inline_group_subscription()])
        .await
        .expect("create DM with inline Subscription");

    assert!(outcome.created);
    let group_id = provisioner
        .prepared_group_ids
        .lock()
        .expect("provisioner lock")[0]
        .clone();
    assert_eq!(
        fixture
            .groups
            .try_get(&group_id)
            .await
            .expect("load DM")
            .expect("created DM")
            .record_status,
        "active"
    );
    assert!(
        fixture
            .sessions
            .list_by_group(&group_id, None, 0, 10, None, None)
            .await
            .expect("list DM Sessions")
            .is_empty()
    );
}
#[tokio::test]
async fn inline_subscription_dm_reuse_cancels_the_new_pending_set() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("assistant").await;
    let existing = fixture
        .service
        .create_with_outcome(dm_create_command())
        .await
        .expect("create canonical DM");
    assert!(existing.created);
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    let reused = fixture
        .service
        .create_with_event_subscriptions(dm_create_command(), vec![inline_group_subscription()])
        .await
        .expect("reuse canonical DM");

    assert!(!reused.created);
    assert!(
        provisioner
            .finalized_group_ids
            .lock()
            .expect("provisioner lock")
            .is_empty()
    );
    assert_eq!(
        provisioner
            .cancelled_group_ids
            .lock()
            .expect("provisioner lock")
            .len(),
        1
    );
}
#[tokio::test]
async fn inline_subscription_validation_failure_creates_no_group_or_session() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    provisioner.fail_prepare.store(true, Ordering::SeqCst);
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    let error = fixture
        .service
        .create_with_event_subscriptions(
            collaboration_create_command(),
            vec![inline_group_subscription()],
        )
        .await
        .expect_err("Subscription validation must fail before Group creation");
    assert_eq!(error.code(), "invalid_event_filter");
    let group_id = provisioner
        .prepared_group_ids
        .lock()
        .expect("provisioner lock")[0]
        .clone();
    assert!(fixture.groups.try_get(&group_id).await.unwrap().is_none());
    assert!(
        fixture
            .sessions
            .list_by_group(&group_id, None, 0, 10, None, None)
            .await
            .expect("list Sessions")
            .is_empty()
    );
}
#[tokio::test]
async fn inline_subscription_finalization_failure_compensates_group_session_and_pending_set() {
    let fixture = Fixture::new().await;
    fixture.add_public_bot("driver").await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    provisioner.fail_finalize.store(true, Ordering::SeqCst);
    let fixture = fixture.with_event_subscription_provisioner(provisioner.clone());

    let error = fixture
        .service
        .create_with_event_subscriptions(
            collaboration_create_command(),
            vec![inline_group_subscription()],
        )
        .await
        .expect_err("finalization failure must fail Group creation");
    assert!(error.to_string().contains("test finalization failure"));
    let group_id = provisioner
        .prepared_group_ids
        .lock()
        .expect("provisioner lock")[0]
        .clone();
    assert!(fixture.groups.try_get(&group_id).await.unwrap().is_none());
    assert!(
        fixture
            .sessions
            .list_by_group(&group_id, None, 0, 10, None, None)
            .await
            .expect("list Sessions")
            .is_empty()
    );
    assert_eq!(
        provisioner
            .cancelled_group_ids
            .lock()
            .expect("provisioner lock")
            .as_slice(),
        [group_id.as_str()]
    );
}
#[tokio::test]
async fn provisioning_groups_are_hidden_from_v1_reads_and_lists() {
    let fixture = Fixture::new().await;
    let mut group = normal_group(
        "hidden-provisioning",
        "driver",
        vec![Participant::human("human_alice", ParticipantRole::Observer)],
        GroupStrategy::Chat,
        1,
    );
    group.record_status = "provisioning".to_string();
    fixture.groups.upsert(group).await.expect("seed Group");

    let error = fixture
        .service
        .get(GetGroup {
            caller: bot_principal("alice"),
            group_id: "hidden-provisioning".to_string(),
        })
        .await
        .expect_err("provisioning Group must be hidden");
    assert_eq!(error.code(), "group_not_found");
    let page = fixture
        .service
        .list_groups(ListGroups {
            caller: bot_principal("alice"),
            view_bot_id: None,
            offset: 0,
            limit: 10,
            q: None,
            visibility: None,
            membership: MembershipFilter::All,
            kind: GroupKindFilter::All,
            strategy: None,
        })
        .await
        .expect("list Groups");
    assert_eq!(page.total, 0);
}
#[tokio::test]
async fn provisioning_reconciler_completes_a_crash_interrupted_group() {
    let fixture = Fixture::new().await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let mut group = Group::new(
        "crash-interrupted",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.label = Some("Recovered".to_string());
    group.record_status = "provisioning".to_string();
    fixture
        .groups
        .upsert(group.clone())
        .await
        .expect("seed provisioning Group");
    fixture
        .sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                participants: group.participants.clone(),
                created_by: Some("human_alice".to_string()),
                ..NewSessionParams::default()
            },
        })
        .await
        .expect("seed initial Session");
    let reconciler = GroupProvisioningReconciler::new(
        fixture.groups.clone(),
        fixture.sessions.clone(),
        None,
        provisioner,
    );

    let outcome = reconciler.reconcile_once(u64::MAX, 0).await;

    assert_eq!(outcome.finalized, 1);
    assert_eq!(outcome.compensated, 0);
    assert_eq!(outcome.deferred, 0);
    assert_eq!(
        fixture
            .groups
            .try_get(&group.id)
            .await
            .expect("load Group")
            .expect("recovered Group")
            .record_status,
        "active"
    );
}
#[tokio::test]
async fn provisioning_reconciler_compensates_an_incomplete_group() {
    let fixture = Fixture::new().await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let mut group = Group::new(
        "crash-incomplete",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.record_status = "provisioning".to_string();
    fixture
        .groups
        .upsert(group.clone())
        .await
        .expect("seed incomplete Group");
    let reconciler = GroupProvisioningReconciler::new(
        fixture.groups.clone(),
        fixture.sessions.clone(),
        None,
        provisioner.clone(),
    );

    let outcome = reconciler.reconcile_once(u64::MAX, 0).await;

    assert_eq!(outcome.finalized, 0);
    assert_eq!(outcome.compensated, 1);
    assert!(fixture.groups.try_get(&group.id).await.unwrap().is_none());
    assert_eq!(
        provisioner
            .cancelled_group_ids
            .lock()
            .expect("provisioner lock")
            .as_slice(),
        [group.id.as_str()]
    );
}
#[tokio::test]
async fn provisioning_reconciler_compensates_state_machine_without_runtime_state() {
    let fixture = Fixture::new().await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let mut group = Group::new(
        "crash-state-machine",
        "driver",
        vec![Participant::bot("driver", ParticipantRole::Driver)],
    );
    group.group_strategy = GroupStrategy::StateMachine;
    group.record_status = "provisioning".to_string();
    fixture
        .groups
        .upsert(group.clone())
        .await
        .expect("seed provisioning Group");
    fixture
        .sessions
        .create_or_reactivate(CreateOrReactivateCommand {
            group_id: group.id.clone(),
            session_id: None,
            params: NewSessionParams {
                participants: group.participants.clone(),
                created_by: Some("human_alice".to_string()),
                ..NewSessionParams::default()
            },
        })
        .await
        .expect("seed initial Session");
    let reconciler = GroupProvisioningReconciler::new(
        fixture.groups.clone(),
        fixture.sessions.clone(),
        None,
        provisioner.clone(),
    );

    let outcome = reconciler.reconcile_once(u64::MAX, 0).await;

    assert_eq!(outcome.finalized, 0);
    assert_eq!(outcome.compensated, 1);
    assert_eq!(outcome.deferred, 0);
    assert!(fixture.groups.try_get(&group.id).await.unwrap().is_none());
}
#[tokio::test]
async fn provisioning_reconciler_cancels_only_stale_orphaned_pending_sets() {
    let fixture = Fixture::new().await;
    let provisioner = Arc::new(RecordingGroupProvisioner::new(fixture.groups.clone()));
    let recovery_actor = EventActor {
        actor_type: EventActorType::System,
        id: "test-reconciler".to_string(),
        display_name: None,
    };
    provisioner
        .pending_groups
        .lock()
        .expect("provisioner lock")
        .extend([
            PendingGroupEventSubscriptions {
                prepared: PreparedGroupEventSubscriptions {
                    group_id: "orphan-stale".to_string(),
                    subscription_ids: vec!["sub-stale".to_string()],
                    actor: recovery_actor.clone(),
                },
                created_at_ms: 100,
            },
            PendingGroupEventSubscriptions {
                prepared: PreparedGroupEventSubscriptions {
                    group_id: "orphan-in-flight".to_string(),
                    subscription_ids: vec!["sub-in-flight".to_string()],
                    actor: recovery_actor,
                },
                created_at_ms: 250,
            },
        ]);
    let reconciler = GroupProvisioningReconciler::new(
        fixture.groups.clone(),
        fixture.sessions.clone(),
        None,
        provisioner.clone(),
    );

    let outcome = reconciler.reconcile_once(300, 100).await;

    assert_eq!(outcome.finalized, 0);
    assert_eq!(outcome.compensated, 1);
    assert_eq!(outcome.deferred, 0);
    assert_eq!(
        provisioner
            .cancelled_group_ids
            .lock()
            .expect("provisioner lock")
            .as_slice(),
        ["orphan-stale"]
    );
}
