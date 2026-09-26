use super::*;
use bcs_service_api::{StateMachineTerminalImPayload as Payload, StateMachineTerminalImDelivery as Delivery,
    StateMachineTerminalImStatus as Status, StateMachineTerminalEvent, StateMachineTerminalStatus, StateMachineTerminalNotification,
    SessionRepoPort, NewSessionParams};

pub(super) async fn terminal_im_contract(store: &dyn StateMachineRunRepoPort, sessions: &dyn SessionRepoPort) {
    for case in ["deliver", "unknown", "new-activation", "empty"] {
        let session = sessions.create("group-1", NewSessionParams { id: None,
            session_kind: bcs_domain::SessionKind::ServiceInvocation, ..Default::default() }).await.unwrap();
        let mut run = test_run(); run.run_id = format!("terminal-im-{case}"); run.session_id = session.id.clone();
        run.session_activation_count = Some(session.activation_count); run.status = StateMachineRunStatus::Completed;
        run.completed_at = Some(100); run.output = Some("original output".into());
        store.create_run(run.clone(), Vec::new()).await.unwrap();
        let r = &run.run_id;
        let mut p = Payload { event: StateMachineTerminalEvent { group_id: run.group_id.clone(), session_id: session.id.clone(), run_id: r.clone(),
            workflow_name: "original workflow".into(), status: StateMachineTerminalStatus::Completed, output: run.output.clone() },
            session_activation_count: session.activation_count, created_at_ms: 100, deadline_ms: 1000,
            notifications: vec![StateMachineTerminalNotification { binding_id: "binding".into(), channel_type: "test-im".into(), account_ref: "account".into(),
                im_conversation_id: "original-target".into(), im_conversation_type: "2".into(), im_user_id: None,
                request_id: "request".into(), node_id: "node".into(), text: "原文".into() }] };
        if case == "empty" { p.notifications.clear(); }
        let mut wrong = p.clone(); wrong.session_activation_count += 1; assert!(!store.save_terminal_im(wrong).await.unwrap());
        let (a, b) = tokio::join!(store.save_terminal_im(p.clone()), store.save_terminal_im(p.clone())); assert!(a.unwrap() && b.unwrap());
        let mut changed = p.clone(); changed.event.workflow_name = "changed".into(); assert!(store.save_terminal_im(changed).await.is_err());
        assert!(store.claim_terminal_im(r, "before-session-completion".into(), 110, 150).await.unwrap().is_none());
        assert!(!store.supersede_terminal_im(r).await.unwrap());
        sessions.complete_running_service_activation(&session.id, session.activation_count, None, None).await.unwrap().unwrap();
        let (a, b) = tokio::join!(store.claim_terminal_im(r, "a".into(), 110, 150), store.claim_terminal_im(r, "b".into(), 110, 150));
        let claims = [a.unwrap(), b.unwrap()].into_iter().flatten().collect::<Vec<_>>(); assert_eq!(claims.len(), 1);
        let old = &claims[0]; let claim = store.claim_terminal_im(r, "takeover".into(), 150, 900).await.unwrap().unwrap();
        assert!(claim.token > old.token); assert!(!store.release_terminal_im(old).await.unwrap());
        let mut progress = claim.checkpoint.progress.clone(); let mut next = progress.clone(); next.cleanup_completed = true;
        assert!(!store.update_terminal_im_progress(old, progress.clone(), next.clone(), 160).await.unwrap());
        assert!(store.update_terminal_im_progress(&claim, progress.clone(), next.clone(), 160).await.unwrap()); progress = next;
        if case == "empty" {
            assert_eq!(store.get_terminal_im(r).await.unwrap().unwrap().status, Status::Delivered); continue;
        }
        let mut next = progress.clone(); next.deliveries[0] = Delivery::Sending;
        assert!(store.update_terminal_im_progress(&claim, progress.clone(), next.clone(), 170).await.unwrap());
        assert!(!store.update_terminal_im_progress(&claim, progress.clone(), next.clone(), 171).await.unwrap()); // stale progress CAS
        assert!(store.update_terminal_im_progress(&claim, next.clone(), progress.clone(), 172).await.is_err()); // no Sending -> Pending
        progress = next;
        let mut next = progress.clone(); next.deliveries[0] = Delivery::Delivered { provider_message_ref: Some("original-ref".into()) };
        if case == "new-activation" {
            sessions.update_callback_status(&session.id, "not_applicable").await.unwrap();
            sessions.reactivate(&session.id, None).await.unwrap();
            assert!(!store.update_terminal_im_progress(&claim, progress.clone(), next, 180).await.unwrap());
            assert!(store.supersede_terminal_im(r).await.unwrap());
            assert_eq!(store.get_terminal_im(r).await.unwrap().unwrap().status, Status::Superseded);
        } else if case == "unknown" {
            assert!(store.release_terminal_im(&claim).await.unwrap());
            let recovered = store.claim_terminal_im(r, "unknown-owner".into(), 181, 950).await.unwrap().unwrap();
            assert_eq!(recovered.checkpoint.progress, progress);
            next.deliveries[0] = Delivery::Failed { error: "unknown; not resent".into() };
            assert!(store.update_terminal_im_progress(&recovered, progress, next, 182).await.unwrap());
            assert_eq!(store.get_terminal_im(r).await.unwrap().unwrap().status, Status::Failed);
        } else {
            assert!(store.update_terminal_im_progress(&claim, progress.clone(), next.clone(), 180).await.unwrap());
            assert!(!store.update_terminal_im_progress(&claim, progress, next, 181).await.unwrap());
            assert_eq!(store.get_terminal_im(r).await.unwrap().unwrap().status, Status::Delivered);
        }
        assert_eq!(store.get_terminal_im(r).await.unwrap().unwrap().payload, p);
        assert_eq!(store.get_run(r).await.unwrap().unwrap().status, StateMachineRunStatus::Completed);
    }
    assert!(store.list_terminal_im_pending(None, 0).await.unwrap().is_empty());
    assert!(store.list_terminal_im_pending(None, 10).await.unwrap().is_empty());
}

#[tokio::test]
async fn memory_terminal_im_contract() {
    let sessions = Arc::new(bcs_session_store::MemorySessionRepo::new());
    terminal_im_contract(&MemoryCollaborationStore::new().with_session_repo(sessions.clone()), sessions.as_ref()).await;
}
#[tokio::test]
async fn sqlite_terminal_im_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap()); bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    terminal_im_contract(&store, &bcs_session_store::MySqlSessionStore::sqlite(db.clone(), "test".into())).await;
    assert!(MySqlCollaborationStore::sqlite(db.clone(), "other".into()).get_terminal_im("terminal-im-deliver").await.unwrap().is_none());
    db.execute(DbStatement::new("UPDATE bcs_collaboration_delivery_checkpoints SET progress_json = '{}' WHERE aggregate_id = 'terminal-im-deliver'")).await.unwrap();
    assert!(store.get_terminal_im("terminal-im-deliver").await.is_err());
}
