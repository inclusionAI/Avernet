use super::*;

#[tokio::test]
async fn sqlite_group_opening_message_round_trips_and_can_be_cleared() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let repo = MySqlGroupStore::sqlite(db, "contract".to_string());
    let mut group = GroupBuilder::new("driver").id("opening-group").build();
    group.group_strategy = GroupStrategy::StateMachine;
    group.opening_message = Some(OpeningMessage::Text("Run {{bcs.run_id}}".to_string()));
    repo.upsert(group).await.expect("persist group");

    assert_eq!(
        repo.try_get("opening-group")
            .await
            .expect("load group")
            .expect("group")
            .opening_message,
        Some(OpeningMessage::Text("Run {{bcs.run_id}}".to_string()))
    );

    let structured = OpeningMessage::AixUi(AixUiOpeningMessage {
        message_type: AixUiOpeningMessageType::Panel,
        component: "releasePanel.RunOverview".to_string(),
        params: Some(BTreeMap::from([(
            "runId".to_string(),
            serde_json::Value::String("{{bcs.run_id}}".to_string()),
        )])),
        tab: Some(AixUiOpeningTab {
            id: Some("run-{{bcs.run_id}}".to_string()),
            title: None,
            closable: Some(true),
        }),
    });
    repo.patch_mutable_fields(
        "opening-group",
        GroupMutableFieldsPatch {
            opening_message: Some(Some(structured.clone())),
            ..Default::default()
        },
    )
    .await
    .expect("replace opening message with structured AixUI");
    assert_eq!(
        repo.try_get("opening-group")
            .await
            .expect("reload structured group")
            .expect("group")
            .opening_message,
        Some(structured)
    );

    repo.patch_mutable_fields(
        "opening-group",
        GroupMutableFieldsPatch {
            opening_message: Some(None),
            ..Default::default()
        },
    )
    .await
    .expect("clear opening message");
    assert_eq!(
        repo.try_get("opening-group")
            .await
            .expect("reload group")
            .expect("group")
            .opening_message,
        None
    );
}

#[tokio::test]
async fn sqlite_eventful_notify_mode_patch_round_trips_and_bumps_version() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let writer = MySqlGroupStore::sqlite(Arc::clone(&db), PROVISIONING_ENV.to_string());
    let mut group = GroupBuilder::new("driver").id("notify-eventful").build();
    group.human_mention_notify_mode = HumanMentionNotifyMode::DriverBotOnly;
    let expected_version = group.version;
    writer.upsert(group).await.expect("seed Group");

    let updated = writer
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "notify-eventful".to_string(),
            expected_version,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..Default::default()
            }),
            event: None,
        })
        .await
        .expect("commit eventful notify-mode patch");
    assert_eq!(updated.version, expected_version + 1);
    assert_eq!(
        updated.human_mention_notify_mode,
        HumanMentionNotifyMode::None
    );

    // A freshly-built Store on the same DB proves the patch is in SQL, not a
    // writer-cache residue.
    let cold = MySqlGroupStore::sqlite(Arc::clone(&db), PROVISIONING_ENV.to_string());
    let loaded = cold
        .try_get("notify-eventful")
        .await
        .expect("cold read")
        .expect("group");
    assert_eq!(loaded.version, expected_version + 1);
    assert_eq!(
        loaded.human_mention_notify_mode,
        HumanMentionNotifyMode::None
    );

    // The authoritative policy read on the cold reader must agree.
    let policy = cold
        .read_human_notify_policy("notify-eventful")
        .await
        .expect("policy read")
        .expect("policy");
    assert_eq!(policy.mode, HumanMentionNotifyMode::None);
    assert_eq!(policy.driver_bot_id, "driver");
}

#[tokio::test]
async fn sqlite_delivery_patches_persist_canonical_routing_policy_json() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let repo = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let mut group = GroupBuilder::new("driver")
        .id("routing-policy-group")
        .build();
    group.routing_policy = Some(RoutingPolicy {
        mode: RoutingMode::Mention,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: HashMap::from([("driver".to_string(), vec!["observer".to_string()])]),
    });
    repo.upsert(group).await.expect("persist group");

    let concurrent_policy = RoutingPolicy {
        mode: RoutingMode::Structured,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: HashMap::from([("driver".to_string(), vec!["new-observer".to_string()])]),
    };
    let concurrent_policy_json =
        serde_json::to_string(&concurrent_policy).expect("serialize concurrent routing policy");
    db.execute(DbStatement::with_params(
        "UPDATE bcs_groups SET routing_policy_json = ? WHERE env = ? AND group_id = ?",
        vec![
            DbValue::from(concurrent_policy_json),
            DbValue::from(PROVISIONING_ENV),
            DbValue::from("routing-policy-group"),
        ],
    ))
    .await
    .expect("simulate a concurrent routing update behind the repository cache");

    repo.patch_mutable_fields(
        "routing-policy-group",
        GroupMutableFieldsPatch {
            default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
            ..Default::default()
        },
    )
    .await
    .expect("apply direct delivery patch");
    let directly_updated = repo
        .try_get("routing-policy-group")
        .await
        .expect("load directly updated group")
        .expect("group");
    let direct_policy = directly_updated.routing_policy.expect("routing policy");
    assert_eq!(direct_policy.mode, RoutingMode::Structured);
    assert_eq!(
        direct_policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );
    assert_eq!(
        direct_policy.sender_routes.get("driver"),
        Some(&vec!["new-observer".to_string()])
    );

    let transactionally_updated = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "routing-policy-group".to_string(),
            expected_version: directly_updated.version,
            mutated_at_ms: 1_787_028_100_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                default_bot_final_delivery: Some(DefaultDelivery::SendToDriver),
                ..Default::default()
            }),
            event: None,
        })
        .await
        .expect("apply transactional delivery patch");
    let repeatedly_updated = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "routing-policy-group".to_string(),
            expected_version: transactionally_updated.version,
            mutated_at_ms: 1_787_028_200_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
                ..Default::default()
            }),
            event: None,
        })
        .await
        .expect("apply repeated transactional delivery patch");
    let repeated_policy = repeatedly_updated.routing_policy.expect("routing policy");
    assert_eq!(repeated_policy.mode, RoutingMode::Structured);
    assert_eq!(
        repeated_policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );
    assert_eq!(
        repeated_policy.sender_routes.get("driver"),
        Some(&vec!["new-observer".to_string()])
    );

    let rows = db
        .query(DbStatement::with_params(
            "SELECT routing_policy_json FROM bcs_groups WHERE env = ? AND group_id = ?",
            vec![
                DbValue::from(PROVISIONING_ENV),
                DbValue::from("routing-policy-group"),
            ],
        ))
        .await
        .expect("query stored routing policy");
    let stored_json =
        db_get_column::<String>(&rows[0], "routing_policy_json").expect("routing_policy_json");
    assert!(
        serde_json::from_str::<serde_json::Value>(&stored_json)
            .expect("valid JSON")
            .is_object()
    );
    let stored_policy =
        serde_json::from_str::<RoutingPolicy>(&stored_json).expect("routing policy object");
    assert_eq!(stored_policy.mode, RoutingMode::Structured);
    assert_eq!(
        stored_policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );
    assert_eq!(
        stored_policy.sender_routes.get("driver"),
        Some(&vec!["new-observer".to_string()])
    );
}

#[tokio::test]
async fn sqlite_transactional_delivery_patch_rejects_stringified_routing_policy_json() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let repo = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let group = GroupBuilder::new("driver")
        .id("invalid-routing-policy-group")
        .build();
    let expected_version = group.version;
    repo.upsert(group).await.expect("persist group");

    let embedded_policy = serde_json::json!({
        "mode": "mention",
        "default_bot_final_delivery": "send_to_driver",
        "sender_routes": {"driver": ["observer"]}
    })
    .to_string();
    let stringified_policy =
        serde_json::to_string(&embedded_policy).expect("stringify routing policy object");
    db.execute(DbStatement::with_params(
        "UPDATE bcs_groups SET routing_policy_json = ? WHERE env = ? AND group_id = ?",
        vec![
            DbValue::from(stringified_policy.as_str()),
            DbValue::from(PROVISIONING_ENV),
            DbValue::from("invalid-routing-policy-group"),
        ],
    ))
    .await
    .expect("persist historical stringified routing policy");

    let uncached_repo = MySqlGroupStore::sqlite(db.clone(), PROVISIONING_ENV.to_string());
    let error = uncached_repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "invalid-routing-policy-group".to_string(),
            expected_version,
            mutated_at_ms: 1_787_028_300_000,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
                ..Default::default()
            }),
            event: None,
        })
        .await
        .expect_err("stringified routing policy must not be overwritten");
    assert!(error.to_string().contains("before patch"));

    let rows = db
        .query(DbStatement::with_params(
            "SELECT routing_policy_json FROM bcs_groups WHERE env = ? AND group_id = ?",
            vec![
                DbValue::from(PROVISIONING_ENV),
                DbValue::from("invalid-routing-policy-group"),
            ],
        ))
        .await
        .expect("reload stringified routing policy");
    let stored_json =
        db_get_column::<String>(&rows[0], "routing_policy_json").expect("routing_policy_json");
    assert_eq!(stored_json, stringified_policy);
}

#[tokio::test]
async fn sqlite_group_opening_message_rejects_invalid_persisted_json() {
    let db: Arc<dyn DbPlugin> = Arc::new(LocalSqliteDbPlugin::new().expect("sqlite db"));
    bootstrap_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .expect("migrate sqlite");
    let repo = MySqlGroupStore::sqlite(db.clone(), "contract".to_string());
    let group = GroupBuilder::new("driver")
        .id("invalid-opening-group")
        .build();
    repo.upsert(group).await.expect("persist group");
    db.execute(DbStatement::with_params(
        "UPDATE bcs_groups SET opening_message_json = ? WHERE group_id = ? AND env = ?",
        vec![
            DbValue::from("{invalid-json"),
            DbValue::from("invalid-opening-group"),
            DbValue::from("contract"),
        ],
    ))
    .await
    .expect("corrupt persisted opening message");

    let reloaded = MySqlGroupStore::sqlite(db, "contract".to_string());
    let error = reloaded
        .try_get("invalid-opening-group")
        .await
        .expect_err("invalid persisted JSON must not fall back to the default opening message");
    assert!(
        error
            .to_string()
            .contains("deserialize opening_message_json")
    );

    let listed = reloaded.list().await;
    assert!(
        listed.is_empty(),
        "an infallible legacy list must fail the whole read instead of returning a Group with the default opening message"
    );
}
