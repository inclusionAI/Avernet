#![allow(clippy::unwrap_used, reason = "real SQLite query and fault assertions")]
use bcs_db_api::*;
use bcs_domain::edge_permission::{EdgeStatus, RequestStatus};
use bcs_edge_permission::agent_invoke::{
    AgentInvokeQueryApplicationService, AgentInvokeQueryPolicy,
};
use bcs_edge_permission_store::agent_invoke::{DbAgentInvokeQueryStore, DbAgentInvokeStore};
use bcs_service_api::{
    application::agent_invoke::AgentInvokeGovernanceQueryService,
    types::{agent_invoke::*, agent_invoke_query::*},
};
use bcs_test_support::contract::{
    agent_invoke::AgentInvokeContractDriver,
    agent_invoke_query::{query, setup},
};
use std::{
    num::{NonZeroU16, NonZeroU32, NonZeroU64},
    sync::{
        Arc, Mutex,
        atomic::{AtomicU8, AtomicUsize, Ordering},
    },
};
#[path = "support/agent_invoke_sql_driver.rs"]
mod driver;
use driver::Driver;
fn limits() -> AgentInvokeLimits {
    let n = NonZeroU32::new(1000).unwrap();
    AgentInvokeLimits {
        max_out_degree: n,
        max_in_degree: n,
        max_pending_out: n,
        max_pending_in: n,
    }
}
async fn fixture() -> Driver {
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref())
        .await
        .unwrap();
    Driver::new_on_with_limits(db, DbSqlFlavor::Sqlite, "query-regression", limits()).await
}
fn command(
    d: &Driver,
    from: &str,
    to: &str,
    key: &str,
    change: AgentInvokeChange,
) -> AgentInvokeCommand {
    AgentInvokeCommand {
        authenticated_scope: d.scope(),
        principal: AgentInvokePrincipal::Human("human_query_target".into()),
        pair: AgentInvokePair {
            from_bot_id: from.into(),
            to_bot_id: to.into(),
        },
        change,
        idempotency_key: key.into(),
        reason: "query regression".into(),
    }
}
struct Observed {
    inner: Arc<dyn DbPlugin>,
    calls: AtomicUsize,
    max_rows: AtomicUsize,
    transactions: AtomicUsize,
    statements: Mutex<Vec<DbStatement>>,
    fault: AtomicU8,
    gate: AtomicU8,
    entered: tokio::sync::Notify,
    resume: tokio::sync::Notify,
}
impl Observed {
    fn new(inner: Arc<dyn DbPlugin>) -> Self {
        Self {
            inner,
            calls: AtomicUsize::new(0),
            max_rows: AtomicUsize::new(0),
            transactions: AtomicUsize::new(0),
            statements: Mutex::new(vec![]),
            fault: AtomicU8::new(0),
            gate: AtomicU8::new(0),
            entered: tokio::sync::Notify::new(),
            resume: tokio::sync::Notify::new(),
        }
    }
    fn record(&self, s: &DbStatement) {
        self.calls.fetch_add(1, Ordering::SeqCst);
        assert!(s.params().len() <= 64);
        self.statements.lock().unwrap().push(s.clone());
    }
}
#[async_trait::async_trait]
impl DbPlugin for Observed {
    async fn query(&self, s: DbStatement) -> DbResult<Vec<DbRow>> {
        self.record(&s);
        if self.fault.load(Ordering::SeqCst) == 1 {
            return Err(DbError::Backend("private query outage".into()));
        }
        let r = self.inner.query(s).await?;
        self.max_rows.fetch_max(r.len(), Ordering::SeqCst);
        Ok(r)
    }
    async fn execute(&self, s: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(s).await
    }
    async fn transaction(
        &self,
        s: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.inner.transaction(s).await
    }
    async fn transaction_with_options(
        &self,
        mut steps: Vec<DbTransactionStep>,
        o: DbTransactionOptions,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.transactions.fetch_add(1, Ordering::SeqCst);
        assert!(steps.len() <= 8, "bounded auth + four aggregate plan");
        if self.gate.swap(0, Ordering::SeqCst) == 1 {
            self.entered.notify_one();
            self.resume.notified().await;
        }
        if self.fault.load(Ordering::SeqCst) == 2 {
            *steps.last_mut().unwrap() = DbTransactionStep::Query(DbStatement::new(
                "SELECT * FROM deliberately_missing_query_data",
            ));
        }
        for step in &steps {
            let DbTransactionStep::Query(s) = step else {
                panic!("governance must be read-only")
            };
            self.record(s);
        }
        let mut r = self.inner.transaction_with_options(steps, o).await?;
        if self.fault.load(Ordering::SeqCst) == 3 {
            if let Some(DbTransactionStepResult::Rows(rows)) = r.get_mut(3) {
                let revision = db_get_column::<u64>(&rows[0], "revision")?;
                let mut fields = rows[0].columns().clone();
                fields.insert("revision".into(), DbValue::U64(revision + 1));
                rows[0] = DbRow::new(fields);
            }
        }

        for step in &r {
            if let DbTransactionStepResult::Rows(rows) = step {
                self.max_rows.fetch_max(rows.len(), Ordering::SeqCst);
            }
        }
        Ok(r)
    }
    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}
fn observed_app(d: &Driver, db: Arc<Observed>) -> AgentInvokeQueryApplicationService {
    let store = Arc::new(
        DbAgentInvokeStore::new(
            db,
            DbSqlFlavor::Sqlite,
            d.scope(),
            limits(),
            NonZeroU64::new(60_000).unwrap(),
        )
        .unwrap(),
    );
    let policy = Arc::new(AgentInvokeQueryPolicy);
    let repo = Arc::new(DbAgentInvokeQueryStore::new(store, policy.clone()));
    AgentInvokeQueryApplicationService::new(d.scope(), policy, repo).unwrap()
}
#[tokio::test]
async fn max_page_and_degree_plans_are_bounded_and_use_directed_indexes() {
    let d = fixture().await;
    setup(&d).await;
    // Trusted load fixture: actual business lifecycle is exercised separately
    // by shared conformance. These rows test large range/batch/plan behavior.
    for i in 0..110 {
        let to = format!("load-{i:03}");
        d.register(&to).await;
        d.role("human_query_target", &to, Some(AgentInvokeRole::Owner))
            .await;
        d.management()
            .change(command(
                &d,
                "query-a",
                &to,
                &format!("load-{i}"),
                AgentInvokeChange::Grant {
                    expected_version: 0,
                },
            ))
            .await
            .unwrap();
    }
    let observed = Arc::new(Observed::new(d.db.clone()));
    let app = observed_app(&d, observed.clone());
    let mut q = query(
        d.scope(),
        AgentInvokeQueryFilter::Grants {
            direction: AgentInvokeDirection::Outbound,
            status: EdgeStatus::Approved,
        },
    );
    q.page.as_mut().unwrap().limit = NonZeroU16::new(100).unwrap();
    let AgentInvokeQueryData::Grants(page) = app.read(q.clone()).await.unwrap().data else {
        panic!("page")
    };
    assert_eq!(page.items.len(), 100);
    assert_eq!(observed.calls.load(Ordering::SeqCst), 6);
    assert_eq!(observed.max_rows.load(Ordering::SeqCst), 101);
    q.page.as_mut().unwrap().cursor = page.next_cursor;
    let AgentInvokeQueryData::Grants(next) = app.read(q).await.unwrap().data else {
        panic!("page")
    };
    assert_eq!(next.items.len(), 10);
    assert!(next.next_cursor.is_none());
    let sql = observed.statements.lock().unwrap().last().unwrap().clone();
    let plan =
        d.db.query(DbStatement::with_params(
            format!("EXPLAIN QUERY PLAN {}", sql.sql()),
            sql.params().to_vec(),
        ))
        .await
        .unwrap();
    let text = format!("{plan:?}");
    assert!(text.contains("idx_agent_invoke_edge_out"));
    assert!(text.contains("id>?") || text.contains("rowid>?"), "{text}");
    assert!(!text.contains("USE TEMP B-TREE"), "{text}");
    observed.calls.store(0, Ordering::SeqCst);
    let result = app
        .read(query(d.scope(), AgentInvokeQueryFilter::Degrees))
        .await
        .unwrap();
    let AgentInvokeQueryData::Degrees(degrees) = result.data else {
        panic!("degrees")
    };
    assert_eq!(degrees.out_degree, 110);
    assert_eq!(degrees.in_degree, 0);
    assert_eq!(observed.calls.load(Ordering::SeqCst), 9);
    assert!(observed.max_rows.load(Ordering::SeqCst) <= 101);
}
#[tokio::test]
async fn unauthorized_and_invalid_cursor_reads_never_start_an_aggregate_transaction() {
    let d = fixture().await;
    setup(&d).await;
    let observed = Arc::new(Observed::new(d.db.clone()));
    let app = observed_app(&d, observed.clone());
    let mut q = query(d.scope(), AgentInvokeQueryFilter::Degrees);
    q.context.principal = AgentInvokePrincipal::Human("human_outsider".into());
    assert_eq!(app.read(q).await, Err(AgentInvokeError::Forbidden));
    assert_eq!(observed.calls.load(Ordering::SeqCst), 1);
    assert_eq!(observed.transactions.load(Ordering::SeqCst), 0);
    observed.calls.store(0, Ordering::SeqCst);
    let mut q = query(
        d.scope(),
        AgentInvokeQueryFilter::Grants {
            direction: AgentInvokeDirection::Outbound,
            status: EdgeStatus::Approved,
        },
    );
    let mut context = q.context.clone();
    context.scope.namespace.push_str("-wrong");
    q.page.as_mut().unwrap().cursor = Some(AgentInvokeQueryCursor {
        context,
        position: AgentInvokeCursorPosition::RowId(NonZeroU64::MIN),
    });
    assert_eq!(app.read(q).await, Err(AgentInvokeError::InvalidInput));
    assert_eq!(observed.calls.load(Ordering::SeqCst), 0);
}
#[tokio::test]
async fn losing_authority_after_preflight_is_rejected_by_the_same_snapshot_as_data() {
    let d = Arc::new(fixture().await);
    setup(d.as_ref()).await;
    let observed = Arc::new(Observed::new(d.db.clone()));
    let app = observed_app(d.as_ref(), observed.clone());
    observed.gate.store(1, Ordering::SeqCst);
    let q = query(d.scope(), AgentInvokeQueryFilter::Degrees);
    let task = tokio::spawn(async move { app.read(q).await });
    observed.entered.notified().await;
    d.role("human_query_manager", "query-a", None).await;
    observed.resume.notify_one();
    assert_eq!(task.await.unwrap(), Err(AgentInvokeError::Forbidden));
}
#[tokio::test]
async fn storage_and_lookahead_corruption_never_become_successful_empty_or_partial_pages() {
    let d = fixture().await;
    setup(&d).await;
    for (to, key) in [("query-b", "corrupt-b"), ("query-c", "corrupt-c")] {
        d.management()
            .change(command(
                &d,
                "query-a",
                to,
                key,
                AgentInvokeChange::Grant {
                    expected_version: 0,
                },
            ))
            .await
            .unwrap();
    }
    let observed = Arc::new(Observed::new(d.db.clone()));
    let app = observed_app(&d, observed.clone());
    let page = query(
        d.scope(),
        AgentInvokeQueryFilter::Grants {
            direction: AgentInvokeDirection::Outbound,
            status: EdgeStatus::Approved,
        },
    );
    for fault in [1, 2] {
        observed.fault.store(fault, Ordering::SeqCst);
        assert_eq!(
            app.read(page.clone()).await,
            Err(AgentInvokeError::Unavailable)
        );
    }
    observed.fault.store(0, Ordering::SeqCst);
    d.sql("UPDATE edge_grants SET rules='null' WHERE env=? AND from_id='query-a' AND to_id='query-c' AND grant_kind='agent_invoke'",vec![d.scope.env.as_str().into()]).await;
    assert_eq!(
        app.read(page).await,
        Err(AgentInvokeError::InvalidState),
        "corrupt second/lookahead row invalidates the whole first page"
    );
    assert_eq!(
        app.read(query(d.scope(), AgentInvokeQueryFilter::Degrees))
            .await,
        Err(AgentInvokeError::InvalidState)
    );
}
#[tokio::test]
async fn history_does_not_require_invocation_eligibility_or_open_writer_gates() {
    let d = fixture().await;
    setup(&d).await;
    let record = d
        .management()
        .change(command(
            &d,
            "query-a",
            "query-b",
            "history",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        ))
        .await
        .unwrap()
        .current_grant
        .unwrap();
    d.publish("query-b", false, false).await;
    let state = d.store.scope_state().await.unwrap();
    d.store
        .set_enabled(state.revision, false, false)
        .await
        .unwrap();
    let q = query(
        d.scope(),
        AgentInvokeQueryFilter::Grant {
            direction: AgentInvokeDirection::Outbound,
            edge_id: record.edge_id,
        },
    );
    let AgentInvokeQueryData::Grant(history) = d.query_service.read(q).await.unwrap().data else {
        panic!("grant history")
    };
    assert_eq!(history.grant.status, EdgeStatus::Revoked);
    assert!(history.grant.version > record.version);
    let AgentInvokeQueryData::Degrees(counts) = d
        .query_service
        .read(query(d.scope(), AgentInvokeQueryFilter::Degrees))
        .await
        .unwrap()
        .data
    else {
        panic!("degree")
    };
    assert_eq!(counts.out_degree, 0);
}
#[tokio::test]
async fn request_pair_and_audit_time_keysets_use_their_own_indexes() {
    let d = fixture().await;
    setup(&d).await;
    d.management()
        .change(command(
            &d,
            "query-a",
            "query-b",
            "audit-index",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        ))
        .await
        .unwrap();
    let observed = Arc::new(Observed::new(d.db.clone()));
    let app = observed_app(&d, observed.clone());
    let mut q = query(
        d.scope(),
        AgentInvokeQueryFilter::Requests {
            direction: AgentInvokeDirection::Outbound,
            status: RequestStatus::Pending,
        },
    );
    q.context.target.peer_bot_id = Some("query-b".into());
    app.read(q).await.unwrap();
    let sql = observed.statements.lock().unwrap().last().unwrap().clone();
    let plan =
        d.db.query(DbStatement::with_params(
            format!("EXPLAIN QUERY PLAN {}", sql.sql()),
            sql.params().to_vec(),
        ))
        .await
        .unwrap();
    assert!(format!("{plan:?}").contains("idx_agent_invoke_request_pair"));
    for action in [None, Some(AgentInvokeAuditAction::Grant)] {
        let mut q = query(
            d.scope(),
            AgentInvokeQueryFilter::Audit {
                direction: AgentInvokeDirection::Outbound,
                action,
            },
        );
        q.context.target.peer_bot_id = Some("query-b".into());
        app.read(q).await.unwrap();
        let sql = observed.statements.lock().unwrap().last().unwrap().clone();
        let plan =
            d.db.query(DbStatement::with_params(
                format!("EXPLAIN QUERY PLAN {}", sql.sql()),
                sql.params().to_vec(),
            ))
            .await
            .unwrap();
        let text = format!("{plan:?}");
        assert!(text.contains(if action.is_some() {
            "idx_agent_invoke_audit_action_time"
        } else {
            "idx_agent_invoke_audit_time"
        }));
        assert!(!text.contains("USE TEMP B-TREE"), "{text}");
    }
}

#[tokio::test]
async fn audit_time_ties_advance_by_id_without_duplicates_or_read_side_writes() {
    let d = fixture().await;
    setup(&d).await;
    let mut grant = d
        .management()
        .change(command(
            &d,
            "query-a",
            "query-b",
            "tie-first",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        ))
        .await
        .unwrap()
        .current_grant
        .unwrap();
    for i in 0..2 {
        let mut revoke = command(
            &d,
            "query-a",
            "query-b",
            &format!("tie-revoke-{i}"),
            AgentInvokeChange::Revoke {
                edge_id: grant.edge_id,
                expected_version: grant.version,
            },
        );
        revoke.principal = AgentInvokePrincipal::Human("human_query_source".into());
        let revoked = d
            .management()
            .change(revoke)
            .await
            .unwrap()
            .current_grant
            .unwrap();
        grant = d
            .management()
            .change(command(
                &d,
                "query-a",
                "query-b",
                &format!("tie-regrant-{i}"),
                AgentInvokeChange::Grant {
                    expected_version: revoked.version.get(),
                },
            ))
            .await
            .unwrap()
            .current_grant
            .unwrap();
    }
    d.sql("UPDATE agent_invoke_audit SET created_at_ms=12345 WHERE from_id='query-a' AND to_id='query-b'",vec![]).await;
    let before = d.store.scope_state().await.unwrap();
    let count = d.audit_count().await;
    let mut q = query(
        d.scope(),
        AgentInvokeQueryFilter::Audit {
            direction: AgentInvokeDirection::Outbound,
            action: None,
        },
    );
    q.context.target.peer_bot_id = Some("query-b".into());
    let mut ids = Vec::new();
    loop {
        let AgentInvokeQueryData::Audit(page) = d.query_service.read(q.clone()).await.unwrap().data
        else {
            panic!("audit")
        };
        for record in page.items {
            assert_eq!(record.created_at_ms, 12345);
            ids.push(record.id.get());
        }
        let Some(cursor) = page.next_cursor else {
            break;
        };
        q.page.as_mut().unwrap().cursor = Some(cursor);
        assert!(ids.len() <= 5, "cursor must progress");
    }
    assert_eq!(ids.len(), 5);
    assert!(ids.windows(2).all(|x| x[0] < x[1]));
    assert_eq!(d.audit_count().await, count);
    assert_eq!(d.store.scope_state().await.unwrap(), before);
}
#[tokio::test]
async fn corrupted_metadata_and_delegations_fail_closed_not_as_zero_degree() {
    let d = fixture().await;
    setup(&d).await;
    let mut c = command(
        &d,
        "query-a",
        "query-b",
        "corrupt-request",
        AgentInvokeChange::Request,
    );
    c.principal = AgentInvokePrincipal::Human("human_query_source".into());
    d.management().change(c).await.unwrap();
    d.sql(
        "UPDATE agent_invoke_requests SET creator_kind='agent'",
        vec![],
    )
    .await;
    let q = query(
        d.scope(),
        AgentInvokeQueryFilter::Requests {
            direction: AgentInvokeDirection::Outbound,
            status: RequestStatus::Pending,
        },
    );
    assert_eq!(
        d.query_service.read(q).await,
        Err(AgentInvokeError::InvalidState)
    );
    assert_eq!(
        d.query_service
            .read(query(d.scope(), AgentInvokeQueryFilter::Degrees))
            .await,
        Err(AgentInvokeError::InvalidState)
    );
    d.sql(
        "UPDATE agent_invoke_requests SET creator_kind='human'",
        vec![],
    )
    .await;
    d.delegate(
        &AgentInvokePrincipal::Governance("query-governor".into()),
        &AgentInvokePair {
            from_bot_id: "query-a".into(),
            to_bot_id: "query-b".into(),
        },
        0,
        vec![AgentInvokeAction::Query],
        "governance corruption fixture",
    )
    .await
    .unwrap();
    let mut q = query(
        d.scope(),
        AgentInvokeQueryFilter::Requests {
            direction: AgentInvokeDirection::Outbound,
            status: RequestStatus::Pending,
        },
    );
    q.context.principal = AgentInvokePrincipal::Governance("query-governor".into());
    q.context.target.peer_bot_id = Some("query-b".into());
    for invalid in ["null", "{}", "[\"query\",\"query\"]", "[\"unknown\"]"] {
        d.sql(
            "UPDATE agent_invoke_governance SET actions_json=?",
            vec![invalid.into()],
        )
        .await;
        assert_eq!(
            d.query_service.read(q.clone()).await,
            Err(AgentInvokeError::InvalidState)
        );
    }
    let all = "[\"request\",\"grant\",\"approve\",\"reject\",\"cancel\",\"revoke\",\"query\"]";
    d.sql(
        "UPDATE agent_invoke_governance SET actions_json=?",
        vec![all.into()],
    )
    .await;
    assert!(d.query_service.read(q).await.is_ok());
    d.sql(
        "UPDATE edge_grants SET rules='[]' WHERE grant_kind='owner' AND to_id='query-a'",
        vec![],
    )
    .await;
    assert_eq!(
        d.query_service
            .read(query(d.scope(), AgentInvokeQueryFilter::Degrees))
            .await,
        Err(AgentInvokeError::InvalidState)
    );
}

#[tokio::test]
async fn a_torn_scope_generation_from_a_broken_provider_never_returns_data() {
    let d = fixture().await;
    setup(&d).await;
    let observed = Arc::new(Observed::new(d.db.clone()));
    let app = observed_app(&d, observed.clone());
    observed.fault.store(3, Ordering::SeqCst);
    assert_eq!(
        app.read(query(d.scope(), AgentInvokeQueryFilter::Degrees))
            .await,
        Err(AgentInvokeError::Conflict)
    );
}

#[tokio::test]
async fn invalid_human_agent_endpoint_is_not_reported_as_a_valid_degree() {
    let d = fixture().await;
    setup(&d).await;
    d.management()
        .change(command(
            &d,
            "query-a",
            "query-b",
            "bad-human-endpoint",
            AgentInvokeChange::Grant {
                expected_version: 0,
            },
        ))
        .await
        .unwrap();
    d.sql("UPDATE edge_grants SET from_id='human_corrupt' WHERE from_id='query-a' AND to_id='query-b' AND grant_kind='agent_invoke'",vec![]).await;
    let mut q = query(d.scope(), AgentInvokeQueryFilter::Degrees);
    q.context.target.bot_id = "query-b".into();
    q.context.principal = AgentInvokePrincipal::Human("human_query_target".into());
    assert_eq!(
        d.query_service.read(q).await,
        Err(AgentInvokeError::InvalidState)
    );
}
