use super::*;

#[tokio::test]
async fn sqlite_group_provisioning_finalization_commits_group_subscription_event_and_target() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let groups = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let events = DbEventStore::sqlite(db.clone());
    seed_provisioning_group(&groups).await;
    let mut prewindow_cause = provisioning_runtime_event(
        PROVISIONING_PREWINDOW_CAUSE_EVENT,
        "state_machine.node.completed",
        None,
    );
    prewindow_cause.event.occurred_at = "2026-08-18T04:39:58.500Z".to_string();
    prewindow_cause.recorded_at = "2026-08-18T04:39:58.500Z".to_string();
    events
        .append_event(prewindow_cause)
        .await
        .expect("append cause before Subscription creation");
    events
        .create_subscription(pending_subscription())
        .await
        .expect("create pending Subscription");
    events
        .append_event(provisioning_runtime_event(
            PROVISIONING_WINDOW_EFFECT_EVENT,
            "state_machine.node.started",
            Some(PROVISIONING_PREWINDOW_CAUSE_EVENT),
        ))
        .await
        .expect("append effect while Subscription is pending");
    events
        .append_event(provisioning_runtime_event(
            PROVISIONING_RUN_CREATED_EVENT,
            "state_machine.run.created",
            None,
        ))
        .await
        .expect("append run.created while Subscription is pending");
    events
        .append_event(provisioning_runtime_event(
            PROVISIONING_RUN_STARTED_EVENT,
            "state_machine.run.started",
            Some(PROVISIONING_RUN_CREATED_EVENT),
        ))
        .await
        .expect("append run.started while Subscription is pending");
    assert_eq!(
        target_count(db.as_ref(), PROVISIONING_RUN_CREATED_EVENT).await,
        0
    );
    assert_eq!(
        target_count(db.as_ref(), PROVISIONING_RUN_STARTED_EVENT).await,
        0
    );

    groups
        .finalize_provisioning(finalization(event_record(None)))
        .await
        .expect("commit provisioning finalization");

    assert_eq!(
        groups
            .get(PROVISIONING_GROUP)
            .await
            .expect("Group")
            .record_status,
        "active"
    );
    let (subscription, revision) = events
        .get_subscription(PROVISIONING_SUBSCRIPTION, PROVISIONING_ENV)
        .await
        .expect("load Subscription")
        .expect("Subscription");
    assert_eq!(subscription.status, EventSubscriptionStatus::Active);
    assert_eq!(revision.activated_at_ms, 1_787_028_000_000);
    let event = events
        .get_event(PROVISIONING_EVENT, PROVISIONING_ENV)
        .await
        .expect("load Event")
        .expect("Event");
    assert_eq!(event.envelope.stream.sequence, 1);
    let rows = db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS target_count FROM bcs_event_fanout_targets WHERE env = ? AND event_id = ?",
            vec![
                DbValue::from(PROVISIONING_ENV),
                DbValue::from(PROVISIONING_EVENT),
            ],
        ))
        .await
        .expect("query target snapshot");
    assert_eq!(db_get_column::<i64>(&rows[0], "target_count").unwrap(), 1);
    assert_eq!(
        target_count(db.as_ref(), PROVISIONING_RUN_CREATED_EVENT).await,
        1
    );
    assert_eq!(
        target_count(db.as_ref(), PROVISIONING_RUN_STARTED_EVENT).await,
        1
    );
    assert_eq!(
        target_count(db.as_ref(), PROVISIONING_PREWINDOW_CAUSE_EVENT).await,
        1
    );
    assert_eq!(
        target_count(db.as_ref(), PROVISIONING_WINDOW_EFFECT_EVENT).await,
        1
    );
    let claimed_targets = events
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "provisioning-order".to_string(),
            now_ms: 1_787_028_000_001,
            lease_until_ms: 1_787_028_010_001,
            limit: 100,
            env: PROVISIONING_ENV.to_string(),
        })
        .await
        .expect("claim provisioning targets");
    let run_targets = claimed_targets
        .iter()
        .filter(|target| {
            target.event_id == PROVISIONING_RUN_CREATED_EVENT
                || target.event_id == PROVISIONING_RUN_STARTED_EVENT
        })
        .collect::<Vec<_>>();
    assert_eq!(
        run_targets
            .iter()
            .map(|target| target.event_id.as_str())
            .collect::<Vec<_>>(),
        vec![
            PROVISIONING_RUN_CREATED_EVENT,
            PROVISIONING_RUN_STARTED_EVENT
        ]
    );
    assert_eq!(
        run_targets[1].depends_on_target_id.as_deref(),
        Some(run_targets[0].target_id.as_str())
    );
    let prewindow_cause_target = claimed_targets
        .iter()
        .find(|target| target.event_id == PROVISIONING_PREWINDOW_CAUSE_EVENT)
        .expect("pre-window causal prerequisite target");
    assert_eq!(
        prewindow_cause_target.purpose,
        EventFanoutTargetPurpose::CausalPrerequisite
    );
    let window_effect_target = claimed_targets
        .iter()
        .find(|target| target.event_id == PROVISIONING_WINDOW_EFFECT_EVENT)
        .expect("in-window effect target");
    assert_eq!(
        window_effect_target.depends_on_target_id.as_deref(),
        Some(prewindow_cause_target.target_id.as_str())
    );
}

#[tokio::test]
async fn sqlite_group_provisioning_finalization_rolls_back_every_component_on_event_failure() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let groups = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let events = DbEventStore::sqlite(db);
    seed_provisioning_group(&groups).await;
    events
        .create_subscription(pending_subscription())
        .await
        .expect("create pending Subscription");

    let error = groups
        .finalize_provisioning(finalization(event_record(Some("missing-cause"))))
        .await
        .expect_err("missing causation Event must roll back the transaction");
    assert!(error.to_string().contains("finalization failed"));
    assert_eq!(
        groups
            .get(PROVISIONING_GROUP)
            .await
            .expect("Group")
            .record_status,
        "provisioning"
    );
    let (subscription, _) = events
        .get_subscription(PROVISIONING_SUBSCRIPTION, PROVISIONING_ENV)
        .await
        .expect("load Subscription")
        .expect("Subscription");
    assert_eq!(subscription.status, EventSubscriptionStatus::Pending);
    assert!(
        events
            .get_event(PROVISIONING_EVENT, PROVISIONING_ENV)
            .await
            .expect("load Event")
            .is_none()
    );
}

#[tokio::test]
async fn sqlite_group_member_add_commits_version_event_and_target_atomically() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let groups = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let events = DbEventStore::sqlite(db.clone());
    seed_active_group(&groups).await;
    events
        .create_subscription(active_subscription("group.participant.added"))
        .await
        .expect("create active Subscription");

    let updated = groups
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: PROVISIONING_GROUP.to_string(),
            expected_version: 1,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::AddParticipant {
                participant: Participant::bot("observer", ParticipantRole::Observer),
                actor_is_public: true,
            },
            event: Some(mutation_event(
                "evt-group-member-added",
                "group.participant.added",
                None,
            )),
        })
        .await
        .expect("commit Group update");

    assert!(
        updated
            .participants
            .iter()
            .any(|p| p.bot_uuid == "observer")
    );
    assert_eq!(updated.version, 2);
    assert!(
        events
            .get_event("evt-group-member-added", PROVISIONING_ENV)
            .await
            .expect("load Event")
            .is_some()
    );
    assert_eq!(target_count(db.as_ref(), "evt-group-member-added").await, 1);
}

#[tokio::test]
async fn sqlite_group_member_add_rolls_back_when_event_append_fails() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let groups = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let events = DbEventStore::sqlite(db);
    seed_active_group(&groups).await;

    groups
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: PROVISIONING_GROUP.to_string(),
            expected_version: 1,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::AddParticipant {
                participant: Participant::bot("observer", ParticipantRole::Observer),
                actor_is_public: true,
            },
            event: Some(mutation_event(
                "evt-group-member-add-failed",
                "group.participant.added",
                Some("missing-cause"),
            )),
        })
        .await
        .expect_err("missing causation Event must fail the unit of work");

    let unchanged = groups.get(PROVISIONING_GROUP).await.expect("Group remains");
    assert!(
        !unchanged
            .participants
            .iter()
            .any(|p| p.bot_uuid == "observer")
    );
    assert_eq!(unchanged.version, 1);
    assert!(
        events
            .get_event("evt-group-member-add-failed", PROVISIONING_ENV)
            .await
            .expect("load Event")
            .is_none()
    );
}

#[tokio::test]
async fn memory_group_member_add_rolls_back_when_event_append_fails() {
    let events = Arc::new(MemoryEventStore::new());
    let groups = MemoryGroupRepo::new().with_event_store(events.clone(), PROVISIONING_ENV);
    let mut group = GroupBuilder::new("driver").id(PROVISIONING_GROUP).build();
    group.label = Some("Active".to_string());
    groups.upsert(group).await.expect("seed active Group");

    groups
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: PROVISIONING_GROUP.to_string(),
            expected_version: 1,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::AddParticipant {
                participant: Participant::bot("observer", ParticipantRole::Observer),
                actor_is_public: true,
            },
            event: Some(mutation_event(
                "evt-memory-member-add-failed",
                "group.participant.added",
                Some("missing-cause"),
            )),
        })
        .await
        .expect_err("missing causation Event must fail the unit of work");

    let unchanged = groups.get(PROVISIONING_GROUP).await.expect("Group remains");
    assert!(
        !unchanged
            .participants
            .iter()
            .any(|p| p.bot_uuid == "observer")
    );
    assert_eq!(unchanged.version, 1);
}

#[tokio::test]
async fn memory_group_deletion_disables_subscription_and_cancels_pending_target() {
    let events = Arc::new(MemoryEventStore::new());
    let groups = MemoryGroupRepo::new().with_event_store(events.clone(), PROVISIONING_ENV);
    seed_memory_active_group(&groups).await;
    events
        .create_subscription(active_subscription("group.participant.added"))
        .await
        .expect("create active Subscription");
    events
        .append_event(mutation_event(
            "evt-memory-before-group-delete",
            "group.participant.added",
            None,
        ))
        .await
        .expect("append Event with pending target");

    groups
        .commit_eventful_mutation(group_deletion())
        .await
        .expect("delete Group and disable Subscription");

    assert!(groups.get(PROVISIONING_GROUP).await.is_none());
    let (subscription, _) = events
        .get_subscription(PROVISIONING_SUBSCRIPTION, PROVISIONING_ENV)
        .await
        .expect("load Subscription")
        .expect("Subscription");
    assert_eq!(subscription.status, EventSubscriptionStatus::Disabled);
    let targets = events
        .claim_fanout_targets(ClaimFanoutTargets {
            worker_id: "after-group-delete".to_string(),
            now_ms: 1_787_028_200_000,
            lease_until_ms: 1_787_028_210_000,
            limit: 10,
            env: PROVISIONING_ENV.to_string(),
        })
        .await
        .expect("claim targets after Group deletion");
    assert!(targets.is_empty());
}

#[tokio::test]
async fn sqlite_group_deletion_disables_subscription_and_cancels_pending_target_atomically() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("SQLite plugin"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("apply SQLite schema");
    let groups = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let events = DbEventStore::sqlite(db.clone());
    seed_active_group(&groups).await;
    events
        .create_subscription(active_subscription("group.participant.added"))
        .await
        .expect("create active Subscription");
    events
        .append_event(mutation_event(
            "evt-sqlite-before-group-delete",
            "group.participant.added",
            None,
        ))
        .await
        .expect("append Event with pending target");

    groups
        .commit_eventful_mutation(group_deletion())
        .await
        .expect("delete Group and disable Subscription");

    assert!(groups.get(PROVISIONING_GROUP).await.is_none());
    let (subscription, _) = events
        .get_subscription(PROVISIONING_SUBSCRIPTION, PROVISIONING_ENV)
        .await
        .expect("load Subscription")
        .expect("Subscription");
    assert_eq!(subscription.status, EventSubscriptionStatus::Disabled);
    let rows = db
        .query(DbStatement::with_params(
            "SELECT status FROM bcs_event_fanout_targets WHERE env = ? AND event_id = ?",
            vec![
                DbValue::from(PROVISIONING_ENV),
                DbValue::from("evt-sqlite-before-group-delete"),
            ],
        ))
        .await
        .expect("query cancelled target");
    assert_eq!(
        db_get_column::<String>(&rows[0], "status").expect("target status"),
        "cancelled"
    );
    let audits = db
        .query(DbStatement::with_params(
            "SELECT COUNT(*) AS audit_count FROM bcs_event_subscription_audits \
             WHERE env = ? AND subscription_id = ? AND action = 'disabled' \
               AND reason = 'scope_deleted'",
            vec![
                DbValue::from(PROVISIONING_ENV),
                DbValue::from(PROVISIONING_SUBSCRIPTION),
            ],
        ))
        .await
        .expect("query automatic disable audit");
    assert_eq!(
        db_get_column::<i64>(&audits[0], "audit_count").expect("audit count"),
        1
    );
}
