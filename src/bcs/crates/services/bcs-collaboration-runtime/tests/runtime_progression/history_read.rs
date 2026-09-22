use super::*;
use bcs_db_api::{DbPlugin, DbStatement, DbResult, DbRow, DbExecuteResult, DbTransactionStep, DbTransactionStepResult, DbHealth};
use bcs_collaboration_store::MySqlCollaborationStore;
use bcs_service_api::{CreateOrReactivateOutcome, Session, SessionUseCaseError};

struct ForbiddenWorkflowDb;
#[async_trait]
impl DbPlugin for ForbiddenWorkflowDb {
    async fn query(&self, _s: DbStatement) -> DbResult<Vec<DbRow>> { panic!("messages read accessed a workflow source") }
    async fn execute(&self, _s: DbStatement) -> DbResult<DbExecuteResult> { panic!("history read wrote workflow data") }
    async fn transaction(&self, _s: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { panic!("history read opened workflow transaction") }
    async fn health_check(&self) -> DbResult<DbHealth> { panic!("history read checked workflow backend") }
}

fn isolated_reader(h: &HistoryHarness) -> CollaborationRuntime {
    let forbidden = Arc::new(MySqlCollaborationStore::sqlite(Arc::new(ForbiddenWorkflowDb), "local".into()));
    CollaborationRuntime::new(forbidden.clone(), forbidden.clone(), forbidden.clone(), forbidden,
        h.groups.clone(), h.sessions.clone(), h.delivery.clone(), noop_judge())
        .with_message_repo(h.messages.clone()).with_history_persistence(true)
}

fn view() -> HumanMessageView {
    HumanMessageView { actor_id: "human_1001".into(), scope: MessageViewScope::Participant, allow_legacy_unclassified_chat: false }
}

#[tokio::test]
async fn dual_write_messages_read_isolated_from_all_workflow_repos_and_rollback() {
    let mut h = HistoryHarness::new(noop_judge()).await;
    let run = h.start(human_input_yaml(), true).await;
    h.runtime.respond_human_node(response(&run, "accepted user answer")).await.unwrap();
    assert_eq!(pending(&h).await, 0);
    let before = h.runtime.get_state_machine_session_history_for_view(&run.session_id, 100, None, view()).await.unwrap().unwrap();
    let reader = isolated_reader(&h);
    let messages = reader.get_state_machine_session_history_for_view(&run.session_id, 100, None, view()).await.unwrap().unwrap();
    let canonical = |rows: &[bcs_domain::GroupMessage]| rows.iter().map(|m| (&m.id, &m.content)).map(|(id,text)| (id.clone(),text.clone())).collect::<BTreeMap<_,_>>();
    assert_eq!(canonical(&before.messages), canonical(&messages.messages));
    assert_eq!(messages.messages.len(), 3, "panel, frozen prompt, accepted answer");
    assert!(messages.messages.iter().all(|m| m.run_id.is_empty()));
    let full = reader.get_state_machine_session_history(&run.session_id, 100, None).await.unwrap().unwrap();
    assert_eq!(full.messages.len(), 2, "Full/Bot excludes Human prompts");
    assert!(reader.get_state_machine_session_history(&run.session_id, 0, None).await.is_err());
    h.messages.fail.store(4, Ordering::SeqCst);
    assert!(reader.get_state_machine_session_history(&run.session_id, 100, None).await.unwrap_err().to_string().contains("injected history read failure"));
    h.messages.fail.store(0, Ordering::SeqCst);
    h.restart(true);
    let rollback = h.runtime.get_state_machine_session_history_for_view(&run.session_id, 100, None, view()).await.unwrap().unwrap();
    assert_eq!(canonical(&messages.messages), canonical(&rollback.messages));
}

#[tokio::test]
async fn no_backfill_reads_existing_panel_and_omits_unpersisted_old_output() {
    let mut h = HistoryHarness::new(noop_judge()).await;
    h.restart(false);
    let run = h.start(human_input_yaml(), true).await;
    h.runtime.respond_human_node(response(&run, "old unpersisted answer")).await.unwrap();
    let runtime = h.runtime.get_state_machine_session_history_for_view(&run.session_id, 100, None, view()).await.unwrap().unwrap();
    assert_eq!(runtime.messages.len(), 3);
    let seq = h.messages.get_current_seq(&run.session_id).await.unwrap();
    let rows = isolated_reader(&h).get_state_machine_session_history_for_view(&run.session_id, 100, None, view()).await.unwrap().unwrap();
    assert_eq!(rows.messages.len(), 1);
    assert!(rows.messages[0].id.ends_with(":000-panel"));
    assert_eq!(h.messages.get_current_seq(&run.session_id).await.unwrap(), seq, "reads never backfill old workflow data");
}

#[tokio::test]
async fn cutoff_uses_session_creation_for_all_views_and_keeps_old_history() {
    let mut h = HistoryHarness::new(noop_judge()).await;
    h.restart(false);
    let run = h.start(human_input_yaml(), true).await;
    h.runtime.respond_human_node(response(&run, "legacy answer")).await.unwrap();
    let created_at = h.sessions.get(&run.session_id).await.unwrap().unwrap().created_at;
    let seq = h.messages.get_current_seq(&run.session_id).await.unwrap();
    for (enabled, cutoff, legacy) in [(true, 0, false), (true, created_at - 1, false),
        (true, created_at, false), (true, created_at + 1, true), (false, 0, true),
        (false, created_at + 1, true)] {
        h.runtime = h.runtime.with_history_persistence(enabled).with_history_cutoff_timestamp(cutoff);
        for scope in [MessageViewScope::Full, MessageViewScope::Participant] {
            let mut human = view(); human.scope = scope;
            let history = h.runtime.get_state_machine_session_history_for_view(
                &run.session_id, 100, None, human,
            ).await.unwrap().unwrap();
            assert_eq!(history.messages.iter().any(|m| m.content == "legacy answer"), legacy,
                "enabled={enabled}, cutoff={cutoff}, scope={scope:?}");
            assert_eq!(history.messages.len(), if legacy && scope == MessageViewScope::Participant { 3 } else if legacy { 2 } else { 1 });
        }
        let full = h.runtime.get_state_machine_session_history(&run.session_id, 100, None).await.unwrap().unwrap();
        assert_eq!(full.messages.iter().any(|m| m.content == "legacy answer"), legacy);
    }
    assert_eq!(h.messages.get_current_seq(&run.session_id).await.unwrap(), seq, "cutover never backfills");
    let reader = isolated_reader(&h).with_history_cutoff_timestamp(created_at);
    assert!(reader.get_state_machine_session_history("missing-session", 100, None).await.unwrap().is_none());
    h.messages = Arc::new(HistoryMessages::default());
    let empty = isolated_reader(&h).with_history_cutoff_timestamp(created_at)
        .get_state_machine_session_history(&run.session_id, 100, None).await.unwrap().unwrap();
    assert!(empty.messages.is_empty(), "an empty messages page must not fall back to workflow storage");
}

struct UnavailableHistorySession { fail: bool }
#[async_trait]
impl SessionManagementService for UnavailableHistorySession {
    async fn get(&self, _: &str) -> Result<Option<Session>, SessionUseCaseError> {
        if self.fail { Err(SessionUseCaseError::Internal(ServiceError::InternalError("session lookup failed".into()))) }
        else { Ok(None) }
    }
    async fn create_or_reactivate(&self, _: CreateOrReactivateCommand) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> { panic!("history mutated session") }
    async fn belongs_to_group(&self, _: &str, _: &str) -> Result<bool, SessionUseCaseError> { panic!("unexpected lookup") }
    async fn list_by_group(&self, _: &str, _: Option<SessionStatus>, _: u64, _: u64, _: Option<&str>, _: Option<&str>) -> Result<Vec<Session>, SessionUseCaseError> { panic!("history listed sessions") }
    async fn count_running_service(&self, _: &str) -> Result<u64, SessionUseCaseError> { panic!("history counted sessions") }
    async fn list_running_service(&self, _: u64, _: u64) -> Result<Vec<Session>, SessionUseCaseError> { panic!("history listed sessions") }
    async fn update_callback_status(&self, _: &str, _: &str) -> Result<(), SessionUseCaseError> { panic!("history mutated session") }
    async fn complete_if_running(&self, _: &str, _: Option<Value>, _: Option<String>) -> Result<Option<Session>, SessionUseCaseError> { panic!("history mutated session") }
    async fn add_participant(&self, _: &str, _: Participant) -> Result<Session, SessionUseCaseError> { panic!("history mutated session") }
    async fn remove_participant(&self, _: &str, _: &str) -> Result<Session, SessionUseCaseError> { panic!("history mutated session") }
    async fn update_participant_mode(&self, _: &str, _: &str, _: ParticipantMode) -> Result<Session, SessionUseCaseError> { panic!("history mutated session") }
    async fn update_title(&self, _: &str, _: Option<String>) -> Result<Session, SessionUseCaseError> { panic!("history mutated session") }
    async fn list_group_ids_by_session_participant(&self, _: &str) -> Result<Vec<String>, SessionUseCaseError> { panic!("history listed groups") }
    async fn delete(&self, _: &str) -> Result<bool, SessionUseCaseError> { panic!("history mutated session") }
}

#[tokio::test]
async fn cutoff_session_lookup_failure_and_missing_session_never_fall_back() {
    let h = HistoryHarness::new(noop_judge()).await;
    let forbidden = Arc::new(MySqlCollaborationStore::sqlite(Arc::new(ForbiddenWorkflowDb), "local".into()));
    for fail in [true, false] {
        let reader = CollaborationRuntime::new(forbidden.clone(), forbidden.clone(), forbidden.clone(), forbidden.clone(),
            h.groups.clone(), Arc::new(UnavailableHistorySession { fail }), h.delivery.clone(), noop_judge())
            .with_history_persistence(true);
        for scope in [MessageViewScope::Full, MessageViewScope::Participant] {
            let mut human = view(); human.scope = scope;
            let result = reader.get_state_machine_session_history_for_view("unavailable", 10, None, human.clone()).await;
            if fail { assert!(result.unwrap_err().to_string().contains("session lookup failed")); }
            else { assert!(result.unwrap().is_none()); }
            assert!(matches!(reader.get_state_machine_session_history_for_view("unavailable", 0, None, human).await,
                Err(CollaborationRuntimeError::InvalidRequest(_))));
        }
    }
}
