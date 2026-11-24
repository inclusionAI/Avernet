//! Deterministic real-SQLite commit interleavings, without timing sleeps.
use super::*;
use bcs_db_local::LocalSqliteDbPlugin;
use tokio::sync::Notify;

struct PauseAfterCommit {
    inner: Arc<LocalSqliteDbPlugin>,
    armed: AtomicBool,
    committed: Notify,
    resume: Notify,
}

#[async_trait]
impl DbPlugin for PauseAfterCommit {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        self.inner.execute(statement).await
    }
    async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> {
        let results = self.inner.transaction(steps).await?;
        if self.armed.swap(false, Ordering::SeqCst) {
            // COMMIT has really completed. Another facade/store can now commit
            // B before A returns from its store or starts display projection.
            self.committed.notify_one();
            self.resume.notified().await;
        }
        Ok(results)
    }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}

async fn interleaved_patch(labels: Option<serde_json::Value>) {
    let inner = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(inner.as_ref()).await.unwrap();
    let db = Arc::new(PauseAfterCommit {
        inner, armed: AtomicBool::new(false), committed: Notify::new(), resume: Notify::new(),
    });
    let fixture = catalog_labels::configure(Fixture::build_with_group_repo(
        None, None, None, Arc::new(MySqlGroupStore::sqlite(db.clone(), "dev".into())),
    ).await);
    fixture.add_public_bot("driver").await;
    let caller = authenticated_bot_principal("driver", "owner");
    let mut create = collaboration_create_command();
    create.caller = caller.clone();
    let CreateGroupSpec::Collaboration(spec) = &mut create.group else { panic!() };
    spec.collaboration = CollaborationConfiguration::ManagerWorker(Default::default());
    spec.catalog_labels = catalog_labels::input(serde_json::json!({"stage":["trial"]}));
    let created = fixture.service.create(create).await.unwrap();
    let id = detail_group_id(&created).to_string();
    let initial_version = match created { GroupDetail::Collaboration(d) => d.version, _ => panic!() };
    // A second process has a distinct cache but uses the same durable database.
    let other = catalog_labels::configure(Fixture::build_with_group_repo(
        None, None, Some(fixture.bots.clone()), Arc::new(MySqlGroupStore::sqlite(db.clone(), "dev".into())),
    ).await);
    db.armed.store(true, Ordering::SeqCst);
    let a = fixture.service.update(UpdateGroup {
        caller: caller.clone(), group_id: id.clone(),
        patch: GroupPatch {
            name: Some("A".into()), catalog_labels: labels.clone().and_then(catalog_labels::input),
            ..Default::default()
        },
    });
    let b = async {
        db.committed.notified().await;
        let result = other.service.update(UpdateGroup {
            caller, group_id: id.clone(), patch: GroupPatch {
                name: Some("B".into()), catalog_labels: catalog_labels::input(serde_json::json!({"stage":["running"]})),
                ..Default::default()
            },
        }).await;
        db.resume.notify_one();
        result
    };
    let (a, b) = tokio::time::timeout(std::time::Duration::from_secs(10), async { tokio::join!(a,b) }).await.unwrap();
    let GroupDetail::Collaboration(a) = a.unwrap() else { panic!() };
    let GroupDetail::Collaboration(b) = b.unwrap() else { panic!() };
    assert_eq!((a.version, a.name.as_deref()), (initial_version+1, Some("A")));
    assert_eq!((b.version, b.name.as_deref()), (initial_version+2, Some("B")));
    let expected = labels.unwrap_or_else(|| serde_json::json!({"stage":["trial"]}));
    assert_eq!(serde_json::to_value(a.catalog_labels).unwrap(), expected);
    assert_eq!(b.catalog_labels.as_map()["stage"], ["running"]);
    let persisted = fixture.service.get(GetGroup { caller: authenticated_bot_principal("driver", "owner"), group_id: id }).await.unwrap();
    let GroupDetail::Collaboration(persisted) = persisted else { panic!() };
    assert_eq!((persisted.version, persisted.name.as_deref()), (b.version, Some("B")));
    assert_eq!(persisted.catalog_labels, b.catalog_labels);
}

#[tokio::test]
async fn patch_response_labels_match_own_commit_when_a_later_patch_commits_before_projection() {
    interleaved_patch(Some(serde_json::json!({"stage":["trial"]}))).await;
}
#[tokio::test]
async fn omitted_labels_return_the_preserved_commit_snapshot_not_a_later_patch() {
    interleaved_patch(None).await;
}
#[tokio::test]
async fn explicit_clear_returns_empty_even_if_a_later_patch_sets_labels() {
    interleaved_patch(Some(serde_json::json!({}))).await;
}

#[tokio::test]
async fn unchanged_patch_preserves_version_and_labels_without_a_write() {
    let fixture = catalog_labels::configure(Fixture::new().await);
    fixture.add_public_bot("driver").await;
    let caller = authenticated_bot_principal("driver", "owner");
    let mut create = collaboration_create_command(); create.caller = caller.clone();
    let CreateGroupSpec::Collaboration(spec) = &mut create.group else { panic!() };
    spec.collaboration = CollaborationConfiguration::ManagerWorker(Default::default());
    spec.catalog_labels = catalog_labels::input(serde_json::json!({"stage":["trial"]}));
    let GroupDetail::Collaboration(created) = fixture.service.create(create).await.unwrap() else { panic!() };
    let GroupDetail::Collaboration(updated) = fixture.service.update(UpdateGroup {
        caller, group_id: created.group_id.clone(), patch: GroupPatch { name: created.name.clone(), ..Default::default() },
    }).await.unwrap() else { panic!() };
    assert_eq!(updated.version, created.version);
    assert_eq!(updated.catalog_labels, created.catalog_labels);
}
