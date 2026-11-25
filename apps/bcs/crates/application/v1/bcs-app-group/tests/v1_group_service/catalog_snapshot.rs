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

// The real label read is paused after metadata has been captured, then a
// second facade commits B. GET/create must return one valid version, not A+B.
struct AuditPauseBeforeLabels {
    inner: Arc<LocalSqliteDbPlugin>, armed: AtomicBool, skip: AtomicUsize, reached: Notify, resume: Notify,
}
#[async_trait]
impl DbPlugin for AuditPauseBeforeLabels {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        if statement.sql().contains("c.catalog_labels FROM bcs_groups AS g") && self.armed.load(Ordering::SeqCst)
            && self.skip.fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| n.checked_sub(1)).is_err()
            && self.armed.swap(false, Ordering::SeqCst) {
            self.reached.notify_one(); self.resume.notified().await;
        }
        self.inner.query(statement).await
    }
    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> { self.inner.execute(statement).await }
    async fn transaction(&self, steps: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { self.inner.transaction(steps).await }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}
async fn detail_projection_race(create_path: bool, state_machine: bool) {
    let inner = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(inner.as_ref()).await.unwrap();
    let db = Arc::new(AuditPauseBeforeLabels { inner, armed: AtomicBool::new(false), skip: AtomicUsize::new(0), reached: Notify::new(), resume: Notify::new() });
    let runtime = Arc::new(RecordingRuntime::default());
    let runtime_port: Option<Arc<dyn CollaborationRuntimeService>> = if state_machine { Some(runtime) } else { None };
    let fixture = catalog_labels::configure(Fixture::build_with_group_repo(runtime_port.clone(), None, None,
        Arc::new(MySqlGroupStore::sqlite(db.clone(), "dev".into()))).await);
    fixture.add_public_bot("driver").await;
    let caller = authenticated_bot_principal("driver", "owner");
    let mut command = collaboration_create_command(); command.caller = caller.clone();
    let CreateGroupSpec::Collaboration(spec) = &mut command.group else { panic!() };
    spec.name = Some("A".into());
    spec.collaboration = if state_machine {
        use bcs_service_api::application::v1::{StateMachineConfiguration, StateMachineDefinition, StateMachineDefinitionReference};
        CollaborationConfiguration::StateMachine(StateMachineConfiguration {
            definition: StateMachineDefinition::Reference(StateMachineDefinitionReference { definition_id: "definition-1".into(), version: 1 }),
            participant_bindings: vec![],
        })
    } else { CollaborationConfiguration::ManagerWorker(Default::default()) };
    spec.catalog_labels = catalog_labels::input(serde_json::json!({"stage":["trial"]}));
    let id = if create_path { None } else { Some(detail_group_id(&fixture.service.create(command.clone()).await.unwrap()).to_string()) };
    let other = catalog_labels::configure(Fixture::build_with_group_repo(runtime_port, None, Some(fixture.bots.clone()),
        Arc::new(MySqlGroupStore::sqlite(db.clone(), "dev".into()))).await);
    // create first projects the legacy management result, then the V1 detail.
    // Pause the latter, after StateMachine runtime configuration has completed.
    db.skip.store(usize::from(create_path), Ordering::SeqCst);
    db.armed.store(true, Ordering::SeqCst);
    let a = async {
        if create_path { fixture.service.create(command).await }
        else { fixture.service.get(GetGroup { caller: caller.clone(), group_id: id.clone().unwrap() }).await }
    };
    let b = async {
        db.reached.notified().await;
        let rows = db.inner.query(DbStatement::new("SELECT group_id FROM bcs_groups WHERE record_status = 'active' LIMIT 1")).await.unwrap();
        let group_id: String = bcs_db_api::db_get_column(&rows[0], "group_id").unwrap();
        let result = other.service.update(UpdateGroup { caller: caller.clone(), group_id, patch: GroupPatch {
            name: Some("B".into()), catalog_labels: catalog_labels::input(serde_json::json!({"stage":["running"]})), ..Default::default()
        }}).await;
        db.resume.notify_one(); result
    };
    let (a,b) = tokio::time::timeout(std::time::Duration::from_secs(10), async { tokio::join!(a,b) }).await.unwrap();
    let GroupDetail::Collaboration(a) = a.unwrap() else { panic!() };
    let GroupDetail::Collaboration(b) = b.unwrap() else { panic!() };
    assert_eq!(a.name.as_deref(), Some("B"));
    assert_eq!(b.name.as_deref(), Some("B"));
    assert_eq!(b.version, a.version);
    assert_eq!(a.catalog_labels.as_map()["stage"], ["running"], "the returned labels belong to the returned metadata version");
}
#[tokio::test]
async fn get_retries_a_version_change_before_labels_without_mixing_metadata() { detail_projection_race(false, false).await; }
#[tokio::test]
async fn create_detail_retries_a_later_patch_without_mixing_metadata() { detail_projection_race(true, false).await; }

#[tokio::test]
async fn state_machine_get_retries_metadata_label_race() { detail_projection_race(false, true).await; }
#[tokio::test]
async fn state_machine_create_retries_metadata_label_race() { detail_projection_race(true, true).await; }

#[tokio::test]
async fn get_authorizes_current_private_snapshot_not_cached_public_metadata() {
    let db=Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let fixture=catalog_labels::configure(Fixture::build_with_group_repo(None,None,None,
        Arc::new(MySqlGroupStore::sqlite(db.clone(),"dev".into()))).await);
    fixture.add_public_bot("driver").await; fixture.add_public_bot("outsider").await;
    let owner=authenticated_bot_principal("driver","owner");
    let viewer=authenticated_bot_principal("outsider","owner");
    let mut command=collaboration_create_command(); command.caller=owner.clone();
    let CreateGroupSpec::Collaboration(spec)=&mut command.group else {panic!()};
    spec.visibility=GroupVisibility::Public;
    spec.collaboration=CollaborationConfiguration::ManagerWorker(Default::default());
    let created=fixture.service.create(command).await.unwrap(); let id=detail_group_id(&created).to_string();
    fixture.service.get(GetGroup{caller:viewer.clone(),group_id:id.clone()}).await.unwrap();
    let other=catalog_labels::configure(Fixture::build_with_group_repo(None,None,Some(fixture.bots.clone()),
        Arc::new(MySqlGroupStore::sqlite(db,"dev".into()))).await);
    other.service.update(UpdateGroup{caller:owner,group_id:id.clone(),patch:GroupPatch{
        visibility:Some(GroupVisibility::Private),..Default::default()
    }}).await.unwrap();
    assert!(matches!(fixture.service.get(GetGroup{caller:viewer,group_id:id}).await,Err(ApplicationError::Forbidden { .. })));
}

async fn legacy_detail_race(update: bool) {
    use bcs_service_api::{GroupQueryService, GroupManagementService, GroupDetailCommand, GroupUpdateLabelCommand};
    let inner=Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(inner.as_ref()).await.unwrap();
    let db=Arc::new(AuditPauseBeforeLabels {inner,armed:AtomicBool::new(false),skip:AtomicUsize::new(0),reached:Notify::new(),resume:Notify::new()});
    let fixture=catalog_labels::configure(Fixture::build_with_group_repo(None,None,None,
        Arc::new(MySqlGroupStore::sqlite(db.clone(),"dev".into()))).await);
    fixture.add_public_bot("driver").await;
    let caller=authenticated_bot_principal("driver","owner");
    let mut command=collaboration_create_command();command.caller=caller.clone();
    let CreateGroupSpec::Collaboration(spec)=&mut command.group else {panic!()};
    spec.name=Some("original".into());spec.collaboration=CollaborationConfiguration::ManagerWorker(Default::default());
    spec.catalog_labels=catalog_labels::input(serde_json::json!({"stage":["trial"]}));
    let created=fixture.service.create(command).await.unwrap();let id=detail_group_id(&created).to_string();
    let legacy=GroupManagement::new(fixture.groups.clone(),fixture.bots.clone(),fixture.friends.clone(),fixture.relation.clone(),
        GroupConfig::default(),fixture.sessions.clone(),Arc::new(NoopSystemMessageService))
        .with_bot_authority(bcs_test_support::authority_hook_for(fixture.authority_repo.clone())).for_v1_openapi();
    let other=catalog_labels::configure(Fixture::build_with_group_repo(None,None,Some(fixture.bots.clone()),
        Arc::new(MySqlGroupStore::sqlite(db.clone(),"dev".into()))).await);
    db.armed.store(true,Ordering::SeqCst);
    let a=async {
        if update { legacy.update_label(GroupUpdateLabelCommand {caller_actor_id:"driver".into(),group_id:id.clone(),label:Some("A".into())}).await }
        else { legacy.get_group(GroupDetailCommand {group_id:id.clone()}).await }
    };
    let b=async {
        db.reached.notified().await;
        let result=other.service.update(UpdateGroup {caller,group_id:id.clone(),patch:GroupPatch {
            name:Some("B".into()),catalog_labels:catalog_labels::input(serde_json::json!({"stage":["running"]})),..Default::default()
        }}).await;
        db.resume.notify_one();result
    };
    let (a,b)=tokio::time::timeout(std::time::Duration::from_secs(10),async {tokio::join!(a,b)}).await.unwrap();
    b.unwrap();let a=a.unwrap();assert_eq!(a.label.as_deref(),Some("B"));
    assert_eq!(a.catalog_labels.as_map()["stage"],["running"]);
}
#[tokio::test]
async fn legacy_get_keeps_metadata_and_labels_together() { legacy_detail_race(false).await; }
#[tokio::test]
async fn legacy_label_response_keeps_metadata_and_labels_together() { legacy_detail_race(true).await; }
