//! Write-side Group Card contract, on real SQL. No Discovery or backfill API.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::{Arc, Mutex};
use async_trait::async_trait;
use bcs_db_api::{DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow,
    DbSqlFlavor, DbStatement, DbTransactionStep, DbTransactionStepResult};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_group_store::MySqlGroupStore;
use bcs_service_api::port::repo::{CommitGroupEventfulMutation, GroupEventfulMutation, GroupRepoPort};
use bcs_service_api::{Group, GroupKind, GroupMutableFieldsPatch, GroupStrategy, Participant, ParticipantRole};
use serde_json::{Value, json};

use bcs_db_migrations as migrations;
#[path = "../../../test-support/authority_mysql_chain.rs"]
mod mysql_chain;

const ENV: &str = "group-card-test";

fn store(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor, env: &str) -> MySqlGroupStore {
    match flavor {
        DbSqlFlavor::Mysql => MySqlGroupStore::new(db, env.into()),
        DbSqlFlavor::Sqlite => MySqlGroupStore::sqlite(db, env.into()),
    }
}

fn group(id: &str, strategy: GroupStrategy) -> Group {
    let mut group = Group::new(id, "manager", vec![Participant::bot("manager", ParticipantRole::Manager)]);
    group.group_strategy = strategy;
    group.label = Some("协作群".into());
    group
}

async fn card(db: &dyn DbPlugin, env: &str, id: &str) -> Option<Value> {
    let rows = db.query(DbStatement::with_params(
        "SELECT card_template FROM bcs_group_agent_cards WHERE env = ? AND group_id = ?",
        vec![env.into(), id.into()],
    )).await.unwrap();
    rows.first().map(|row| serde_json::from_str(&row.get_string("card_template").unwrap().unwrap()).unwrap())
}

fn assert_template(card: &Value, name: &str, kind: &str) {
    assert_eq!(card["name"], name);
    assert_eq!(card["skills"][0]["name"], name);
    let prefix = if kind == "manager_worker" { "Manager–Worker 群：" } else { "State Machine 群：" };
    assert_eq!(card["description"], format!("{prefix}{name}"));
    assert_eq!(card["skills"][0]["description"], card["description"]);
    assert_eq!(card["capabilities"]["extensions"][0]["params"], json!({"groupKind": kind}));
    assert!(card.get("version").is_none());
    assert!(card.get("providerName").is_none());
}

async fn mutate(repo: &MySqlGroupStore, id: &str, mutation: GroupEventfulMutation) {
    let version = repo.try_get(id).await.unwrap().unwrap().version;
    repo.commit_eventful_mutation(CommitGroupEventfulMutation {
        group_id: id.into(), expected_version: version, mutation, event: None,
        mutated_at_ms: 1_800_000_000_000,
    }).await.unwrap();
}

async fn lifecycle(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor) {
    let repo = store(db.clone(), flavor, ENV);
    for (id, strategy, visibility, status) in [
        ("mw-private", GroupStrategy::ManagerWorker, "private", "active"),
        ("mw-public", GroupStrategy::ManagerWorker, "public", "active"),
        ("sm-private", GroupStrategy::StateMachine, "private", "active"),
        ("sm-public", GroupStrategy::StateMachine, "public", "provisioning"),
    ] {
        let mut g = group(id, strategy);
        g.visibility = visibility.into();
        g.record_status = status.into();
        repo.upsert(g).await.unwrap();
        let saved = card(db.as_ref(), ENV, id).await.unwrap();
        assert_template(&saved, "协作群", if strategy == GroupStrategy::ManagerWorker { "manager_worker" } else { "state_machine" });
        assert_eq!(saved["supportedInterfaces"][0]["url"], format!("local://groups/{id}"));
        assert_eq!(saved["supportedInterfaces"][0]["protocolBinding"], "LOCAL");
    }
    let mut dm = group("dm", GroupStrategy::ManagerWorker);
    dm.group_kind = GroupKind::Dm;
    dm.dm_pair_key = Some("a:b".into());
    repo.upsert(dm).await.unwrap();
    repo.upsert(group("chat", GroupStrategy::Chat)).await.unwrap();
    assert!(card(db.as_ref(), ENV, "chat").await.is_none());
    assert!(card(db.as_ref(), ENV, "dm").await.is_none());

    // Name-only updates retain non-display content, including a future real interface.
    let mut retained = card(db.as_ref(), ENV, "mw-private").await.unwrap();
    retained["supportedInterfaces"] = json!([{"url":"https://example.com/a2a/groups/mw-private", "protocolBinding":"JSONRPC", "protocolVersion":"1.0"}]);
    retained["opaque"] = json!({"unicode": "保留字段", "array": [1, 2]});
    db.execute(DbStatement::with_params(
        "UPDATE bcs_group_agent_cards SET card_template = ? WHERE env = ? AND group_id = ?",
        vec![retained.to_string().into(), ENV.into(), "mw-private".into()],
    )).await.unwrap();
    for (index, label) in ["新名字\\\"", "patch 名称", "event 名称", "upsert 名称"].into_iter().enumerate() {
        match index {
            0 => repo.update_label("mw-private", Some(label.into())).await.unwrap(),
            1 => repo.patch_mutable_fields("mw-private", GroupMutableFieldsPatch { label: Some(label.into()), ..Default::default() }).await.unwrap(),
            2 => mutate(&repo, "mw-private", GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch { label: Some(label.into()), ..Default::default() })).await,
            _ => {
                let mut g = repo.try_get("mw-private").await.unwrap().unwrap();
                g.label = Some(label.into());
                repo.upsert(g).await.unwrap();
            }
        }
        let saved = card(db.as_ref(), ENV, "mw-private").await.unwrap();
        assert_template(&saved, label, "manager_worker");
        assert_eq!(saved["supportedInterfaces"], retained["supportedInterfaces"]);
        assert_eq!(saved["opaque"], retained["opaque"]);
    }
    // A combined label/routing patch uses the existing routing CAS in the same transaction.
    repo.patch_mutable_fields("mw-private", GroupMutableFieldsPatch {
        label: Some("routing patch".into()),
        default_bot_final_delivery: Some(bcs_service_api::DefaultDelivery::InjectObservers),
        ..Default::default()
    }).await.unwrap();
    assert_template(&card(db.as_ref(), ENV, "mw-private").await.unwrap(), "routing patch", "manager_worker");
    repo.update_label("mw-private", None).await.unwrap();
    assert_template(&card(db.as_ref(), ENV, "mw-private").await.unwrap(), "mw-private", "manager_worker");
    repo.patch_mutable_fields("sm-private", GroupMutableFieldsPatch { label: Some(String::new()), ..Default::default() }).await.unwrap();
    assert_template(&card(db.as_ref(), ENV, "sm-private").await.unwrap(), "sm-private", "state_machine");

    // Ordinary writes on an old row need no deployment-wide backfill.
    db.execute(DbStatement::with_params("DELETE FROM bcs_group_agent_cards WHERE env = ? AND group_id = ?", vec![ENV.into(), "sm-private".into()])).await.unwrap();
    repo.update_label("sm-private", Some("补模板".into())).await.unwrap();
    assert_template(&card(db.as_ref(), ENV, "sm-private").await.unwrap(), "补模板", "state_machine");

    // Dynamic fields remain in their authoritative tables; no template rewrite.
    let before = card(db.as_ref(), ENV, "mw-private").await.unwrap();
    repo.patch_mutable_fields("mw-private", GroupMutableFieldsPatch { context: Some("new instruction".into()), ..Default::default() }).await.unwrap();
    repo.add_participant("mw-private", Participant::bot("worker", ParticipantRole::Worker)).await.unwrap();
    repo.update_visibility("mw-private", "public").await.unwrap();
    assert_eq!(card(db.as_ref(), ENV, "mw-private").await.unwrap(), before);

    // Environment isolation, plus both deletion routes (also used by creation rollback).
    let other = store(db.clone(), flavor, "other-env");
    other.upsert(group("mw-private", GroupStrategy::StateMachine)).await.unwrap();
    assert!(repo.delete("mw-private").await.unwrap().is_some());
    assert!(card(db.as_ref(), ENV, "mw-private").await.is_none());
    assert!(card(db.as_ref(), "other-env", "mw-private").await.is_some());
    mutate(&repo, "sm-private", GroupEventfulMutation::Delete).await;
    assert!(card(db.as_ref(), ENV, "sm-private").await.is_none());
}

/// Inject a failing SQL step *inside* the real transaction at the template
/// write, proving rollback of earlier Group/participant/version writes.
struct ProbeDb {
    inner: Arc<dyn DbPlugin>,
    fail_template: std::sync::atomic::AtomicBool,
    before_transaction: Mutex<Vec<DbTransactionStep>>,
    transactions: Mutex<Vec<Vec<String>>>,
}
#[async_trait]
impl DbPlugin for ProbeDb {
    async fn query(&self, s: DbStatement) -> DbResult<Vec<DbRow>> { self.inner.query(s).await }
    async fn execute(&self, s: DbStatement) -> DbResult<DbExecuteResult> { self.inner.execute(s).await }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
    async fn transaction(&self, mut steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        // Deterministically commit a competing write after the caller has read
        // its routing snapshot but before it executes the guarded transaction.
        let competing = std::mem::take(&mut *self.before_transaction.lock().unwrap());
        if !competing.is_empty() { self.inner.transaction(competing).await?; }
        let sql = steps.iter().map(|step| match step {
            DbTransactionStep::Execute(s) | DbTransactionStep::Query(s) | DbTransactionStep::ExecuteChecked { statement: s, .. } => s.sql().to_owned(),
        }).collect::<Vec<_>>();
        if self.fail_template.load(std::sync::atomic::Ordering::SeqCst) {
            for (step, sql) in steps.iter_mut().zip(&sql) {
                if sql.contains("bcs_group_agent_cards") {
                    *step = DbTransactionStep::Execute(DbStatement::new("INSERT INTO nonexistent_card_failure_table VALUES (1)"));
                }
            }
        }
        self.transactions.lock().unwrap().push(sql);
        self.inner.transaction(steps).await
    }
}

async fn failures_and_cost(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor) {
    use std::sync::atomic::Ordering::SeqCst;
    let probe = Arc::new(ProbeDb { inner: db.clone(), fail_template: false.into(), before_transaction: Mutex::new(vec![]), transactions: Mutex::new(vec![]) });
    let repo = store(probe.clone(), flavor, ENV);
    let mut g = group("rollback", GroupStrategy::ManagerWorker);
    probe.fail_template.store(true, SeqCst);
    assert!(repo.upsert(g.clone()).await.is_err());
    assert!(repo.try_get(&g.id).await.unwrap().is_none());
    assert!(card(db.as_ref(), ENV, &g.id).await.is_none());
    let participants = db.query(DbStatement::with_params("SELECT bot_uuid FROM bcs_group_participants WHERE env = ? AND group_id = ?", vec![ENV.into(), g.id.as_str().into()])).await.unwrap();
    assert!(participants.is_empty());
    probe.fail_template.store(false, SeqCst);
    repo.upsert(g.clone()).await.unwrap();
    let original = card(db.as_ref(), ENV, &g.id).await.unwrap();
    probe.fail_template.store(true, SeqCst);
    assert!(repo.update_label(&g.id, Some("failed label".into())).await.is_err());
    assert!(repo.patch_mutable_fields(&g.id, GroupMutableFieldsPatch { label: Some("failed patch".into()), ..Default::default() }).await.is_err());
    assert!(repo.commit_eventful_mutation(CommitGroupEventfulMutation {
        group_id: g.id.clone(), expected_version: g.version,
        mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch { label: Some("failed event".into()), ..Default::default() }),
        event: None, mutated_at_ms: 1_800_000_000_000,
    }).await.is_err());
    assert!(repo.delete(&g.id).await.is_err());
    assert!(repo.commit_eventful_mutation(CommitGroupEventfulMutation {
        group_id: g.id.clone(), expected_version: g.version,
        mutation: GroupEventfulMutation::Delete, event: None, mutated_at_ms: 1_800_000_000_000,
    }).await.is_err());
    g.label = Some("failed upsert".into());
    g.participants.push(Participant::bot("new-worker", ParticipantRole::Worker));
    assert!(repo.upsert(g.clone()).await.is_err());
    // Context/membership writes should succeed even when template writes fail.
    repo.patch_mutable_fields(&g.id, GroupMutableFieldsPatch { context: Some("allowed".into()), ..Default::default() }).await.unwrap();
    let fresh = store(db.clone(), flavor, ENV).try_get(&g.id).await.unwrap().unwrap();
    assert_eq!(fresh.label.as_deref(), Some("协作群"));
    assert_eq!(fresh.version, g.version);
    assert_eq!(fresh.participants.len(), 1);
    assert_eq!(repo.try_get(&g.id).await.unwrap().unwrap().label, fresh.label);
    assert_eq!(card(db.as_ref(), ENV, &g.id).await.unwrap(), original);
    probe.fail_template.store(false, SeqCst);

    // One extra indexed statement regardless of participant count; no member Card reads.
    for count in [0, 1, 128] {
        let mut sized = group(&format!("size-{count}"), GroupStrategy::ManagerWorker);
        sized.participants = (0..count).map(|n| Participant::bot(format!("worker-{n}"), ParticipantRole::Worker)).collect();
        repo.upsert(sized).await.unwrap();
        let tx = probe.transactions.lock().unwrap();
        let sql = tx.last().unwrap();
        assert_eq!(sql.len(), 3 + count);
        assert_eq!(sql.iter().filter(|s| s.contains("bcs_group_agent_cards")).count(), 1);
        assert!(!sql.iter().any(|s| s.contains("bcs_bots") || s.contains("bcs_collaboration_definitions")));
    }
    repo.update_label("size-1", Some("updated".into())).await.unwrap();
    assert_eq!(probe.transactions.lock().unwrap().last().unwrap().len(), 2);
    repo.delete("size-1").await.unwrap();
    assert_eq!(probe.transactions.lock().unwrap().last().unwrap().len(), 3);
}

#[tokio::test]
async fn sqlite_group_cards_lifecycle_and_rollback() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    lifecycle(db.clone(), DbSqlFlavor::Sqlite).await;
    failures_and_cost(db, DbSqlFlavor::Sqlite).await;
}

#[tokio::test]
#[ignore = "requires an empty disposable database in BCS_TEST_MYSQL_URL"]
async fn mysql_group_cards_full_chain_lifecycle_and_rollback() {
    let database = mysql_chain::MysqlConformanceDb::open_empty().await;
    let versions = database.apply_full_migration_chain().await;
    assert_eq!(versions.last(), Some(&41));
    lifecycle(database.plugin.clone(), DbSqlFlavor::Mysql).await;
    failures_and_cost(database.plugin.clone(), DbSqlFlavor::Mysql).await;
    database.close().await;
}

#[tokio::test]
async fn concurrent_renames_cas_and_invalid_json_preserve_consistency() {
    let dir = tempfile::tempdir().unwrap();
    let db = Arc::new(LocalSqliteDbPlugin::new_file(dir.path().join("cards.db")).unwrap());
    migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let first = store(db.clone(), DbSqlFlavor::Sqlite, ENV);
    let second = store(db.clone(), DbSqlFlavor::Sqlite, ENV);
    first.upsert(group("concurrent", GroupStrategy::ManagerWorker)).await.unwrap();
    // Warm two independent caches before direct writes that do not increment version.
    second.try_get("concurrent").await.unwrap().unwrap();
    for n in 0..12 {
        let (a, b) = tokio::join!(
            first.update_label("concurrent", Some(format!("name-a-{n}"))),
            second.patch_mutable_fields("concurrent", GroupMutableFieldsPatch { label: Some(format!("name-b-{n}")), ..Default::default() }),
        );
        a.unwrap(); b.unwrap();
        let fresh = store(db.clone(), DbSqlFlavor::Sqlite, ENV).try_get("concurrent").await.unwrap().unwrap();
        assert_eq!(card(db.as_ref(), ENV, "concurrent").await.unwrap()["name"], fresh.label.unwrap());
    }
    let fresh = store(db.clone(), DbSqlFlavor::Sqlite, ENV);
    mutate(&fresh, "concurrent", GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch { label: Some("event name".into()), ..Default::default() })).await;
    let saved = card(db.as_ref(), ENV, "concurrent").await.unwrap();
    let stale = first.commit_eventful_mutation(CommitGroupEventfulMutation {
        group_id: "concurrent".into(), expected_version: 1, mutated_at_ms: 1_800_000_000_000,
        mutation: GroupEventfulMutation::PatchMutableFields(GroupMutableFieldsPatch { label: Some("stale".into()), ..Default::default() }), event: None,
    }).await;
    assert!(matches!(stale, Err(bcs_service_api::ServiceError::Conflict(_))));
    assert_eq!(card(db.as_ref(), ENV, "concurrent").await.unwrap(), saved);
    db.execute(DbStatement::with_params("UPDATE bcs_group_agent_cards SET card_template = ? WHERE env = ? AND group_id = ?", vec!["{bad-json".into(), ENV.into(), "concurrent".into()])).await.unwrap();
    assert!(fresh.update_label("concurrent", Some("must rollback".into())).await.is_err());
    let reloaded = store(db.clone(), DbSqlFlavor::Sqlite, ENV).try_get("concurrent").await.unwrap().unwrap();
    assert_eq!(reloaded.label.as_deref(), Some("event name"));
}

#[tokio::test]
async fn label_routing_cas_distinguishes_conflict_from_deletion_and_invalidates_cache() {
    use bcs_service_api::{DefaultDelivery, RoutingMode, RoutingPolicy, ServiceError};
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let probe = Arc::new(ProbeDb {
        inner: db.clone(), fail_template: false.into(),
        before_transaction: Mutex::new(vec![]), transactions: Mutex::new(vec![]),
    });
    let repo = store(probe.clone(), DbSqlFlavor::Sqlite, ENV);
    let id = "label-routing-cas";
    repo.upsert(group(id, GroupStrategy::ManagerWorker)).await.unwrap();
    let original = card(db.as_ref(), ENV, id).await.unwrap();
    let policy = RoutingPolicy {
        mode: RoutingMode::Mention, default_bot_final_delivery: DefaultDelivery::SendToDriver,
        sender_routes: Default::default(),
    };
    let policy_json = serde_json::to_string(&policy).unwrap();
    *probe.before_transaction.lock().unwrap() = vec![DbTransactionStep::Execute(DbStatement::with_params(
        "UPDATE bcs_groups SET routing_policy_json = ? WHERE env = ? AND group_id = ?",
        vec![policy_json.into(), ENV.into(), id.into()],
    ))];
    let patch = GroupMutableFieldsPatch {
        label: Some("must not persist".into()),
        default_bot_final_delivery: Some(DefaultDelivery::InjectObservers), ..Default::default()
    };
    assert!(matches!(repo.patch_mutable_fields(id, patch.clone()).await, Err(ServiceError::Conflict(_))));
    let reloaded = repo.try_get(id).await.unwrap().unwrap();
    assert_eq!(reloaded.label.as_deref(), Some("协作群"));
    assert_eq!(serde_json::to_value(reloaded.routing_policy).unwrap(), serde_json::to_value(policy).unwrap());
    assert_eq!(card(db.as_ref(), ENV, id).await.unwrap(), original);

    *probe.before_transaction.lock().unwrap() = ["bcs_group_participants", "bcs_group_agent_cards", "bcs_groups"]
        .into_iter().map(|table| DbTransactionStep::Execute(DbStatement::with_params(
            format!("DELETE FROM {table} WHERE env = ? AND group_id = ?"), vec![ENV.into(), id.into()],
        ))).collect();
    assert!(matches!(repo.patch_mutable_fields(id, patch).await, Err(ServiceError::GroupNotFound(missing)) if missing == id));
    assert!(repo.try_get(id).await.unwrap().is_none(), "deleted Group must not survive in the cache");
    assert!(card(db.as_ref(), ENV, id).await.is_none(), "CAS failure must not recreate the template");
}
