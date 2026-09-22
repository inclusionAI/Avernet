//! Unit fixtures for the MySQL-backed Group store.
//!
//! These tests drive the persistence primitives against a recording/failing
//! `DbPlugin` decorator; behavioral contracts live in `tests/` and the
//! shared test-support harnesses.
use super::*;
use bcs_db_api::{DbExecuteResult, DbHealth};
use std::collections::{BTreeMap, VecDeque};
use std::sync::Mutex as StdMutex;

#[derive(Default)]
struct RecordingDbPlugin {
    transaction_sql: StdMutex<Vec<String>>,
    transaction_statements: StdMutex<Vec<DbStatement>>,
    execute_statements: StdMutex<Vec<DbStatement>>,
    query_sqls: StdMutex<Vec<String>>,
    first_execute_affected_rows: u64,
    fail_queries: bool,
    query_rows: Vec<DbRow>,
    query_results: StdMutex<VecDeque<Vec<DbRow>>>,
    transaction_error: Option<String>,
    missing_transaction_lock_row: bool,
}

impl RecordingDbPlugin {
    fn with_first_execute_affected_rows(first_execute_affected_rows: u64) -> Self {
        Self {
            first_execute_affected_rows,
            ..Self::default()
        }
    }

    fn failing_queries() -> Self {
        Self {
            fail_queries: true,
            ..Self::default()
        }
    }

    fn with_query_rows(query_rows: Vec<DbRow>) -> Self {
        Self {
            query_rows,
            ..Self::default()
        }
    }

    fn with_duplicate_transaction_error() -> Self {
        Self {
            transaction_error: Some(
                "UNIQUE constraint failed: bcs_groups.env, bcs_groups.dm_pair_key".into(),
            ),
            ..Self::default()
        }
    }

    fn with_missing_transaction_lock_row(query_rows: Vec<DbRow>) -> Self {
        Self {
            query_rows,
            missing_transaction_lock_row: true,
            ..Self::default()
        }
    }
}

#[async_trait]
impl DbPlugin for RecordingDbPlugin {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.query_sqls
            .lock()
            .expect("query sqls")
            .push(statement.sql().to_string());
        if self.fail_queries {
            return Err(DbError::Backend("database unavailable".to_string()));
        }
        if let Some(rows) = self
            .query_results
            .lock()
            .expect("query results")
            .pop_front()
        {
            return Ok(rows);
        }
        Ok(self.query_rows.clone())
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.execute_statements
            .lock()
            .expect("execute statements")
            .push(statement);
        Ok(DbExecuteResult {
            affected_rows: self.first_execute_affected_rows,
            last_insert_id: None,
        })
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        if self.missing_transaction_lock_row {
            return Err(DbError::InvalidInput(
                "transaction parameter 2 references missing row 0 from step 0".to_string(),
            ));
        }
        if let Some(error) = &self.transaction_error {
            return Err(DbError::Backend(error.clone()));
        }
        let mut results = Vec::with_capacity(steps.len());
        let mut sql = self.transaction_sql.lock().expect("transaction sql");
        let mut execute_index = 0;

        for step in steps {
            match step {
                DbTransactionStep::ExecuteChecked { .. } => {
                    return Err(DbError::Unsupported("group SQL recorder does not execute conditional transactions".into()));
                }
                DbTransactionStep::Query(statement) => {
                    self.transaction_statements
                        .lock()
                        .expect("transaction statements")
                        .push(statement.clone());
                    sql.push(statement.sql().to_string());
                    results.push(DbTransactionStepResult::Rows(Vec::new()));
                }
                DbTransactionStep::Execute(statement) => {
                    self.transaction_statements
                        .lock()
                        .expect("transaction statements")
                        .push(statement.clone());
                    sql.push(statement.sql().to_string());
                    let affected_rows = if execute_index == 0 {
                        self.first_execute_affected_rows
                    } else {
                        0
                    };
                    execute_index += 1;
                    results.push(DbTransactionStepResult::Executed(DbExecuteResult {
                        affected_rows,
                        last_insert_id: None,
                    }));
                }
            }
        }

        Ok(results)
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        Ok(DbHealth::healthy())
    }
}

#[test]
fn test_logical_db_must_stay_empty() {
    assert!(assert_empty_logical_db("").is_ok());
    assert!(matches!(
        assert_empty_logical_db("legacy-db"),
        Err(DbError::InvalidInput(_))
    ));
}

#[test]
fn empty_opening_message_json_is_invalid_persisted_data() {
    let error = MySqlGroupStore::deserialize_opening_message(Some(String::new()))
        .expect_err("an empty persisted value is not valid JSON");
    assert!(
        error
            .to_string()
            .contains("deserialize opening_message_json")
    );
}

#[test]
fn stored_routing_policy_patch_handles_defaults_and_rejects_invalid_json() {
    let default_json = patch_stored_routing_policy_json(None, DefaultDelivery::InjectObservers)
        .expect("serialize default routing policy");
    let default_policy =
        serde_json::from_str::<RoutingPolicy>(&default_json).expect("routing policy");
    assert_eq!(
        default_policy.default_bot_final_delivery,
        DefaultDelivery::InjectObservers
    );

    for invalid_json in ["", "{invalid-json"] {
        let error = patch_stored_routing_policy_json(
            Some(invalid_json),
            DefaultDelivery::InjectObservers,
        )
        .expect_err("invalid stored routing policy must not be overwritten");
        assert!(error.to_string().contains("before patch"));
    }
}

#[tokio::test]
async fn fallible_group_reads_propagate_database_failures() {
    let db = Arc::new(RecordingDbPlugin::failing_queries());
    let repo = MySqlGroupStore::new(db, "local".to_string());

    let get_error = repo.try_get("group-1").await.expect_err("get must fail");
    assert!(get_error.to_string().contains("database unavailable"));

    let list_error = repo
        .try_find_by_participant("bot-1")
        .await
        .expect_err("participant query must fail");
    assert!(list_error.to_string().contains("database unavailable"));
}

#[tokio::test]
async fn mutable_patch_propagates_preflight_read_failures() {
    let db = Arc::new(RecordingDbPlugin::failing_queries());
    let repo = MySqlGroupStore::new(db, "local".to_string());

    let error = repo
        .patch_mutable_fields(
            "group-1",
            GroupMutableFieldsPatch {
                label: Some("Renamed".to_string()),
                ..Default::default()
            },
        )
        .await
        .expect_err("patch preflight read must fail");

    assert!(error.to_string().contains("database unavailable"));

    let routing_error = repo
        .patch_mutable_fields(
            "group-1",
            GroupMutableFieldsPatch {
                default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
                ..Default::default()
            },
        )
        .await
        .expect_err("routing patch preflight read must fail");

    assert!(routing_error.to_string().contains("database unavailable"));
}

#[tokio::test]
async fn mutable_patch_rejects_a_non_string_routing_policy_column() {
    let malformed = DbRow::new(BTreeMap::from([(
        "routing_policy_json".to_string(),
        Value::from(7_i64),
    )]));
    let db = Arc::new(RecordingDbPlugin::with_query_rows(vec![malformed]));
    let repo = MySqlGroupStore::new(db, "local".to_string());

    let error = repo
        .patch_mutable_fields(
            "group-1",
            GroupMutableFieldsPatch {
                default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
                ..Default::default()
            },
        )
        .await
        .expect_err("non-string routing policy must fail");

    assert!(error.to_string().contains("routing_policy_json"));
}

#[tokio::test]
async fn mutable_patch_rejects_a_concurrent_routing_policy_change() {
    let policy = RoutingPolicy {
        mode: bcs_service_api::RoutingMode::Mention,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: HashMap::new(),
    };
    let stored_policy_json =
        serde_json::to_string(&policy).expect("serialize stored routing policy");
    let routing_row = DbRow::new(BTreeMap::from([(
        "routing_policy_json".to_string(),
        Value::from(stored_policy_json.as_str()),
    )]));
    let group_row = DbRow::new(BTreeMap::from([
        ("group_id".to_string(), Value::from("group-1")),
        ("status".to_string(), Value::from("active")),
        ("driver_bot".to_string(), Value::from("driver")),
        (
            "routing_policy_json".to_string(),
            Value::from(stored_policy_json.as_str()),
        ),
    ]));
    let db = Arc::new(RecordingDbPlugin {
        query_results: StdMutex::new(VecDeque::from([
            vec![routing_row],
            vec![group_row],
            Vec::new(),
        ])),
        ..Default::default()
    });
    let repo = MySqlGroupStore::new(db, "local".to_string());

    let error = repo
        .patch_mutable_fields(
            "group-1",
            GroupMutableFieldsPatch {
                default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
                ..Default::default()
            },
        )
        .await
        .expect_err("concurrent routing change must fail the guarded write");

    assert!(
        matches!(error, ServiceError::Conflict(message) if message.contains("concurrently"))
    );
}

#[tokio::test]
async fn eventful_mutable_patch_reports_a_missing_routing_lock_as_a_conflict() {
    let policy = RoutingPolicy {
        mode: bcs_service_api::RoutingMode::Mention,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: HashMap::new(),
    };
    let stored_policy_json =
        serde_json::to_string(&policy).expect("serialize stored routing policy");
    let routing_row = DbRow::new(BTreeMap::from([(
        "routing_policy_json".to_string(),
        Value::from(stored_policy_json),
    )]));
    let db = Arc::new(RecordingDbPlugin::with_missing_transaction_lock_row(vec![
        routing_row,
    ]));
    let repo = MySqlGroupStore::new(db, "local".to_string());
    let mut group = Group::new("group-1", "driver", Vec::new());
    group.routing_policy = Some(policy);
    let expected_version = group.version;
    repo.cache.write().await.insert(group.id.clone(), group);

    let error = repo
        .commit_eventful_mutation(CommitGroupEventfulMutation {
            group_id: "group-1".to_string(),
            expected_version,
            mutated_at_ms: 1,
            mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch {
                default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
                ..Default::default()
            }),
            event: None,
        })
        .await
        .expect_err("a missing guarded lock row must be reported as a conflict");

    assert!(
        matches!(error, ServiceError::Conflict(message) if message.contains("concurrently"))
    );
    assert!(repo.cache.read().await.get("group-1").is_none());
}

#[tokio::test]
async fn mutable_patch_reports_a_missing_group() {
    let repo = MySqlGroupStore::new(Arc::new(RecordingDbPlugin::default()), "local".into());

    let error = repo
        .patch_mutable_fields(
            "missing-group",
            GroupMutableFieldsPatch {
                default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
                ..Default::default()
            },
        )
        .await
        .expect_err("missing group must fail");

    assert!(matches!(error, ServiceError::GroupNotFound(id) if id == "missing-group"));
}

#[tokio::test]
async fn delete_aborts_before_persistence_when_snapshot_read_fails() {
    let db = Arc::new(RecordingDbPlugin::failing_queries());
    let repo = MySqlGroupStore::new(db.clone(), "local".to_string());

    let error = repo
        .delete("group-1")
        .await
        .expect_err("snapshot failure must abort deletion");

    assert!(error.to_string().contains("database unavailable"));
    assert!(
        db.transaction_sql
            .lock()
            .expect("transaction sql")
            .is_empty(),
        "delete transaction must not run without a rollback snapshot"
    );
}

#[tokio::test]
async fn participant_row_decode_failures_are_not_silently_dropped() {
    let malformed = DbRow::new(BTreeMap::from([(
        "role".to_string(),
        Value::from("consultant"),
    )]));
    let db = Arc::new(RecordingDbPlugin::with_query_rows(vec![malformed]));
    let repo = MySqlGroupStore::new(db, "local".to_string());

    let error = repo
        .load_participants_from_mysql("group-1")
        .await
        .expect_err("missing bot_uuid must fail the Group read");

    assert!(error.to_string().contains("bot_uuid"));
}

#[test]
fn infallible_group_reads_do_not_fail_open_on_invalid_human_scope() {
    let unknown_scope = DbRow::new(BTreeMap::from([(
        "message_view_scope".to_string(),
        Value::from("unknown"),
    )]));
    let bot_participant_scope = DbRow::new(BTreeMap::from([(
        "message_view_scope".to_string(),
        Value::from("participant"),
    )]));

    assert_eq!(
        MySqlGroupStore::message_view_scope_from_row(
            &unknown_scope,
            "message_view_scope",
            "group-1",
            "human-1",
            ActorKind::Human,
        ),
        MessageViewScope::Participant
    );
    assert_eq!(
        MySqlGroupStore::message_view_scope_from_row(
            &bot_participant_scope,
            "message_view_scope",
            "group-1",
            "bot-1",
            ActorKind::Bot,
        ),
        MessageViewScope::Full
    );
}

#[tokio::test]
async fn sqlite_dm_pair_unique_conflict_is_treated_as_a_lost_race() {
    let db = Arc::new(RecordingDbPlugin::with_duplicate_transaction_error());
    let repo = MySqlGroupStore::sqlite(db, "local".to_string());
    let mut group = Group::new(
        "loser",
        "alice",
        vec![
            Participant::bot("alice", ParticipantRole::Driver),
            Participant::bot("bob", ParticipantRole::Consultant),
        ],
    );
    group.group_kind = bcs_domain::GroupKind::Dm;
    group.dm_pair_key = Some(Group::compute_dm_pair_key("alice", "bob"));

    let created = repo
        .insert_dm_group_if_absent(group)
        .await
        .expect("duplicate pair key must be handled as a lost race");

    assert!(!created);
}

#[tokio::test]
async fn dm_insert_loser_participant_steps_are_guarded_by_group_row() {
    let db = Arc::new(RecordingDbPlugin::with_first_execute_affected_rows(1));
    let repo = MySqlGroupStore::new(db.clone(), "race".to_string());
    let pair_key = Group::compute_dm_pair_key("alice", "bob");
    let participants = vec![
        Participant {
            bot_uuid: "alice".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        },
        Participant {
            bot_uuid: "bob".to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: ActorKind::Bot,
            mode: None,
            tags: Vec::new(),
            message_view_scope: MessageViewScope::Full,
        },
    ];
    let mut group = Group::new("loser-group", "alice", participants);
    group.group_kind = bcs_domain::GroupKind::Dm;
    group.dm_pair_key = Some(pair_key);

    let created = repo
        .insert_dm_group_if_absent(group)
        .await
        .expect("insert dm group");

    assert!(!created);
    let sql = db.transaction_sql.lock().expect("transaction sql");
    let participant_sql: Vec<_> = sql
        .iter()
        .filter(|statement| statement.contains("bcs_group_participants"))
        .collect();

    assert_eq!(participant_sql.len(), 2);
    assert!(participant_sql.iter().all(|statement| {
        statement.contains("SELECT")
            && statement.contains("FROM bcs_groups")
            && statement.contains("group_id = ?")
            && statement.contains("dm_pair_key = ?")
    }));
}

#[tokio::test]
async fn dm_insert_persists_the_requested_context() {
    let db = Arc::new(RecordingDbPlugin::with_first_execute_affected_rows(1));
    let repo = MySqlGroupStore::new(db.clone(), "local".to_string());
    let mut group = Group::new(
        "dm-context",
        "alice",
        vec![
            Participant::bot("alice", ParticipantRole::Driver),
            Participant::bot("bob", ParticipantRole::Consultant),
        ],
    );
    group.group_kind = bcs_domain::GroupKind::Dm;
    group.dm_pair_key = Some(Group::compute_dm_pair_key("alice", "bob"));
    group.context = Some("review release".to_string());

    repo.insert_dm_group_if_absent(group)
        .await
        .expect("insert DM with context");

    let statements = db.transaction_statements.lock().expect("transaction steps");
    let insert = statements.first().expect("group insert");
    assert!(insert.sql().contains("NULL, ?, ?, ?, ?"));
    assert_eq!(insert.params()[6], Value::from("review release"));
}

#[tokio::test]
async fn workspace_update_is_visible_until_restart() {
    let db = Arc::new(RecordingDbPlugin::default());
    let repo = MySqlGroupStore::new(db, "local".to_string());
    let group = Group::new("group-1", "driver", Vec::new());
    repo.cache.write().await.insert(group.id.clone(), group);
    let workspace = Workspace {
        decisions: vec!["ship the hotfix".to_string()],
        notes: vec!["customer impact contained".to_string()],
        ..Workspace::default()
    };

    repo.update_workspace("group-1", workspace.clone())
        .await
        .expect("update workspace");

    let stored = repo.get("group-1").await.expect("group exists");
    assert_eq!(stored.workspace.decisions, workspace.decisions);
    assert_eq!(stored.workspace.notes, workspace.notes);
}

#[tokio::test]
async fn mutable_patch_serializes_routing_policy_before_sql() {
    let mut policy = RoutingPolicy {
        mode: bcs_service_api::RoutingMode::Mention,
        default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: HashMap::from([("driver".to_string(), vec!["observer".to_string()])]),
    };
    let stored_policy_json =
        serde_json::to_string(&policy).expect("serialize stored routing policy");
    let policy_row = DbRow::new(BTreeMap::from([(
        "routing_policy_json".to_string(),
        Value::from(stored_policy_json.as_str()),
    )]));
    let db = Arc::new(RecordingDbPlugin {
        first_execute_affected_rows: 1,
        query_rows: vec![policy_row],
        ..Default::default()
    });
    let repo = MySqlGroupStore::new(db.clone(), "local".to_string());
    let mut group = Group::new("group-1", "driver", Vec::new());
    group.routing_policy = Some(policy.clone());
    repo.cache.write().await.insert(group.id.clone(), group);

    repo.patch_mutable_fields(
        "group-1",
        GroupMutableFieldsPatch {
            label: Some("Renamed".to_string()),
            default_bot_final_delivery: Some(DefaultDelivery::InjectObservers),
            ..Default::default()
        },
    )
    .await
    .expect("patch mutable fields");

    let statements = db.execute_statements.lock().expect("execute statements");
    assert_eq!(statements.len(), 1);
    let sql = statements[0].sql();
    assert!(sql.contains("label = ?"));
    assert!(sql.contains("routing_policy_json = ?"));
    assert!(sql.contains("routing_policy_json <=> ?"));
    assert!(!sql.contains("JSON_SET"));
    assert!(!sql.contains("json_set"));
    assert!(!sql.contains("participants"));
    policy.default_bot_final_delivery = DefaultDelivery::InjectObservers;
    let policy_json = serde_json::to_string(&policy).expect("serialize routing policy");
    assert_eq!(
        statements[0].params(),
        &[
            Value::from("Renamed"),
            Value::from(policy_json),
            Value::from("group-1"),
            Value::from("local"),
            Value::from(stored_policy_json),
        ]
    );
}

#[tokio::test]
async fn guarded_participant_insert_preserves_group_version() {
    let db = Arc::new(RecordingDbPlugin::with_first_execute_affected_rows(1));
    let repo = MySqlGroupStore::new(db.clone(), "local".to_string());

    repo.add_participant_with_visibility_guard(
        "group-1",
        Participant::bot("protected", ParticipantRole::Consultant),
        false,
    )
    .await
    .expect("guarded insert");

    let sql = db.transaction_sql.lock().expect("transaction sql");
    assert_eq!(sql.len(), 2);
    assert!(!sql[0].contains("version"));
    assert!(sql[0].contains("visibility <> 'public' OR ?"));
    assert!(sql[0].contains("NOT EXISTS"));
    assert!(sql[0].contains("bcs_group_participants"));
    assert!(sql[1].contains("INSERT IGNORE"));
    assert!(sql[1].contains("visibility <> 'public' OR ?"));
}

#[tokio::test]
async fn delete_returns_none_when_the_group_row_lost_a_race() {
    let db = Arc::new(RecordingDbPlugin::default());
    let repo = MySqlGroupStore::new(db, "local".to_string());
    let group = Group::new("group-1", "driver", Vec::new());
    repo.cache.write().await.insert(group.id.clone(), group);

    let deleted = repo.delete("group-1").await.expect("delete group");

    assert!(deleted.is_none());
    assert!(repo.cache.read().await.get("group-1").is_none());
}

#[test]
fn test_db_timestamp_from_millis_is_mysql_compatible() {
    // The `gmt_modified` value must stay in `YYYY-MM-DD HH:MM:SS.mmm`
    // form: MySQL `timestamp` columns reject RFC3339 `T`/`Z` strings with
    // ERROR 1292, while SQLite `strftime('%s')` reads keep parsing this
    // form.
    let formatted = db_timestamp_from_millis(1_756_902_260_462).expect("valid timestamp");
    assert_eq!(formatted, "2025-09-03 12:24:20.462");
    assert!(!formatted.contains('T'));
    assert!(!formatted.ends_with('Z'));
}

#[test]
fn test_status_conversion() {
    assert_eq!(
        MySqlGroupStore::status_to_str(&GroupStatus::Active),
        "active"
    );
    assert_eq!(
        MySqlGroupStore::status_to_str(&GroupStatus::Completed),
        "completed"
    );
    assert_eq!(
        MySqlGroupStore::status_to_str(&GroupStatus::Closed),
        "closed"
    );
    assert_eq!(
        MySqlGroupStore::status_to_str(&GroupStatus::Inactive),
        "inactive"
    );

    assert!(matches!(
        MySqlGroupStore::str_to_status("active"),
        GroupStatus::Active
    ));
    assert!(matches!(
        MySqlGroupStore::str_to_status("completed"),
        GroupStatus::Completed
    ));
    assert!(matches!(
        MySqlGroupStore::str_to_status("unknown"),
        GroupStatus::Active
    ));
}

#[test]
fn test_role_conversion() {
    assert_eq!(
        MySqlGroupStore::role_to_str(&ParticipantRole::Driver),
        "driver"
    );
    assert_eq!(
        MySqlGroupStore::role_to_str(&ParticipantRole::Consultant),
        "consultant"
    );
    assert_eq!(
        MySqlGroupStore::role_to_str(&ParticipantRole::Observer),
        "observer"
    );

    assert!(matches!(
        MySqlGroupStore::str_to_role("driver"),
        ParticipantRole::Driver
    ));
    assert!(matches!(
        MySqlGroupStore::str_to_role("consultant"),
        ParticipantRole::Consultant
    ));
    assert!(matches!(
        MySqlGroupStore::str_to_role("unknown"),
        ParticipantRole::Driver
    ));
}

// ======================================================================
// M.6 Human Actor V1 — actor_kind / mode parsing & normalization tests
// ======================================================================

#[test]
fn test_actor_kind_to_str_and_back() {
    assert_eq!(MySqlGroupStore::actor_kind_to_str(ActorKind::Bot), "bot");
    assert_eq!(
        MySqlGroupStore::actor_kind_to_str(ActorKind::Human),
        "human"
    );

    assert!(matches!(
        MySqlGroupStore::parse_actor_kind(Some("bot")),
        ActorKind::Bot
    ));
    assert!(matches!(
        MySqlGroupStore::parse_actor_kind(Some("human")),
        ActorKind::Human
    ));
    // Unknown / NULL → falls back to Bot
    assert!(matches!(
        MySqlGroupStore::parse_actor_kind(Some("alien")),
        ActorKind::Bot
    ));
    assert!(matches!(
        MySqlGroupStore::parse_actor_kind(None),
        ActorKind::Bot
    ));
}

#[test]
fn test_mode_to_str_and_back() {
    assert_eq!(MySqlGroupStore::mode_to_str(ParticipantMode::Auto), "auto");
    assert_eq!(
        MySqlGroupStore::mode_to_str(ParticipantMode::Muted),
        "muted"
    );
    assert_eq!(
        MySqlGroupStore::mode_to_str(ParticipantMode::Present),
        "present"
    );
    assert_eq!(
        MySqlGroupStore::mode_to_str(ParticipantMode::Absent),
        "absent"
    );

    assert_eq!(
        MySqlGroupStore::parse_participant_mode_opt(Some("auto")),
        Some(ParticipantMode::Auto)
    );
    assert_eq!(
        MySqlGroupStore::parse_participant_mode_opt(Some("muted")),
        Some(ParticipantMode::Muted)
    );
    assert_eq!(
        MySqlGroupStore::parse_participant_mode_opt(Some("present")),
        Some(ParticipantMode::Present)
    );
    assert_eq!(
        MySqlGroupStore::parse_participant_mode_opt(Some("absent")),
        Some(ParticipantMode::Absent)
    );
    assert_eq!(
        MySqlGroupStore::parse_participant_mode_opt(Some("supervised")),
        None
    );
    assert_eq!(MySqlGroupStore::parse_participant_mode_opt(None), None);
}

#[test]
fn test_normalize_kind_mode_legal_combinations_passthrough() {
    let cases = [
        ("bot", "auto", ActorKind::Bot, ParticipantMode::Auto),
        ("bot", "muted", ActorKind::Bot, ParticipantMode::Muted),
        (
            "human",
            "present",
            ActorKind::Human,
            ParticipantMode::Present,
        ),
        ("human", "absent", ActorKind::Human, ParticipantMode::Absent),
    ];
    for (kind_str, mode_str, expect_kind, expect_mode) in cases {
        let (k, m) = MySqlGroupStore::normalize_kind_mode(
            "g1",
            "a1",
            "dev",
            Some(kind_str),
            Some(mode_str),
        );
        assert_eq!(k, expect_kind, "kind for ({}, {})", kind_str, mode_str);
        assert_eq!(m, expect_mode, "mode for ({}, {})", kind_str, mode_str);
    }
}

#[test]
fn test_normalize_kind_mode_illegal_pair_falls_back_to_default_for_kind() {
    // Bot + Present is illegal → fallback to ParticipantMode::Auto
    let (k, m) =
        MySqlGroupStore::normalize_kind_mode("g1", "b1", "dev", Some("bot"), Some("present"));
    assert_eq!(k, ActorKind::Bot);
    assert_eq!(m, ParticipantMode::Auto);

    // Human + Auto is illegal → fallback to ParticipantMode::Absent
    let (k, m) =
        MySqlGroupStore::normalize_kind_mode("g1", "h1", "dev", Some("human"), Some("auto"));
    assert_eq!(k, ActorKind::Human);
    assert_eq!(m, ParticipantMode::Absent);
}

#[test]
fn test_normalize_kind_mode_unknown_inputs_default() {
    // Unknown actor_kind → Bot, unknown mode → default_for(Bot) = Auto
    let (k, m) = MySqlGroupStore::normalize_kind_mode(
        "g1",
        "b1",
        "dev",
        Some("alien"),
        Some("supervised"),
    );
    assert_eq!(k, ActorKind::Bot);
    assert_eq!(m, ParticipantMode::Auto);
}

/// Regression test for review Finding #4.
///
/// NULL / absent `mode` is the normal compatibility path (rows that
/// pre-date Migration 003) and MUST NOT emit ERROR logs. The result
/// must be the kind-aware default per Requirement 3.18#3.
#[test]
fn test_normalize_kind_mode_null_mode_is_silent_compat_path() {
    // (bot, NULL) → auto
    let (k, m) = MySqlGroupStore::normalize_kind_mode("g1", "b1", "dev", Some("bot"), None);
    assert_eq!(k, ActorKind::Bot);
    assert_eq!(m, ParticipantMode::Auto);

    // (human, NULL) → absent
    let (k, m) = MySqlGroupStore::normalize_kind_mode("g1", "h1", "dev", Some("human"), None);
    assert_eq!(k, ActorKind::Human);
    assert_eq!(m, ParticipantMode::Absent);

    // (NULL, NULL) → bot/auto (full compat for legacy rows)
    let (k, m) = MySqlGroupStore::normalize_kind_mode("g1", "b1", "dev", None, None);
    assert_eq!(k, ActorKind::Bot);
    assert_eq!(m, ParticipantMode::Auto);

    // (NULL, "auto") → kind defaults to Bot (compat); mode passes through
    let (k, m) = MySqlGroupStore::normalize_kind_mode("g1", "b1", "dev", None, Some("auto"));
    assert_eq!(k, ActorKind::Bot);
    assert_eq!(m, ParticipantMode::Auto);
}

// ── human_mention_notify_mode parser and scoped policy read ─────────────────

#[test]
fn human_mention_notify_mode_parser_maps_only_sql_null_to_all() {
    // Missing (pre-migration-030) column value is the only fallback to All.
    assert_eq!(
        MySqlGroupStore::parse_human_mention_notify_mode(None).expect("missing value"),
        HumanMentionNotifyMode::All
    );
    for (raw, expected) in [
        ("all", HumanMentionNotifyMode::All),
        ("driver_bot_only", HumanMentionNotifyMode::DriverBotOnly),
        ("none", HumanMentionNotifyMode::None),
    ] {
        assert_eq!(
            MySqlGroupStore::parse_human_mention_notify_mode(Some(raw)).expect(raw),
            expected
        );
    }
    // Empty and unknown strings must error instead of failing open to All.
    for bad in ["", "weird", "ALL", "none "] {
        let error = MySqlGroupStore::parse_human_mention_notify_mode(Some(bad))
            .expect_err(bad);
        assert!(
            format!("{error:?}").contains("human_mention_notify_mode"),
            "{bad}: {error:?}"
        );
    }
}

#[tokio::test]
async fn policy_read_runs_one_scoped_select_and_skips_the_group_cache() {
    let mode_row = DbRow::new(BTreeMap::from([
        ("human_mention_notify_mode".to_string(), Value::from("driver_bot_only")),
        ("driver_bot".to_string(), Value::from("scoped-driver")),
    ]));
    let db = Arc::new(RecordingDbPlugin::with_query_rows(vec![mode_row]));
    let store = MySqlGroupStore::new(Arc::clone(&db) as Arc<dyn DbPlugin>, "test".to_string());

    let policy = store
        .read_human_notify_policy("some-group")
        .await
        .expect("scoped policy read")
        .expect("policy");
    assert_eq!(policy.mode, HumanMentionNotifyMode::DriverBotOnly);
    assert_eq!(policy.driver_bot_id, "scoped-driver");

    let sqls = db.query_sqls.lock().expect("query sqls").clone();
    assert_eq!(
        sqls.len(),
        1,
        "the policy read must be exactly one query, got {sqls:?}"
    );
    assert!(
        sqls[0].contains("SELECT human_mention_notify_mode, driver_bot FROM bcs_groups"),
        "unexpected policy query: {}",
        sqls[0]
    );
    assert!(
        !sqls[0].contains("bcs_group_participants"),
        "the policy read must not load participants: {}",
        sqls[0]
    );
}

#[tokio::test]
async fn policy_read_propagates_database_failures() {
    let db = Arc::new(RecordingDbPlugin::failing_queries());
    let store = MySqlGroupStore::new(Arc::clone(&db) as Arc<dyn DbPlugin>, "test".to_string());
    let error = store
        .read_human_notify_policy("some-group")
        .await
        .expect_err("query failure must propagate");
    assert!(matches!(error, ServiceError::InternalError(_)), "{error:?}");
}

#[tokio::test]
async fn mode_only_patch_propagates_preflight_failures() {
    let db = Arc::new(RecordingDbPlugin::failing_queries());
    let store = MySqlGroupStore::new(Arc::clone(&db) as Arc<dyn DbPlugin>, "test".to_string());
    let error = store
        .patch_mutable_fields(
            "some-group",
            GroupMutableFieldsPatch {
                human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
                ..Default::default()
            },
        )
        .await
        .expect_err("preflight read failure must propagate the storage error");
    assert!(matches!(error, ServiceError::InternalError(_)), "{error:?}");
}
