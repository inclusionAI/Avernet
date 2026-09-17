use super::*;
use bcs_service_api::{StateMachineChatResultPayload as Payload, StateMachineChatResultStatus as Status,
    StateMachineChatResultOutcome as Outcome, StateMachineResultPublishCommand};

pub(super) async fn publication_contract(store: &dyn StateMachineRunRepoPort) {
    for case in ["ack", "unknown", "pending", "failed", "cancel", "race", "unfinished"] {
        let mut run = test_run(); run.run_id = format!("publication-{case}"); run.session_id = format!("publication-session-{case}");
        run.status = StateMachineRunStatus::Running; run.created_by = Some("original-bot".into());
        let r = run.run_id.as_str();
        let mut node = test_node(); node.run_id = r.into();
        node.status = if case == "unfinished" { StateMachineNodeStatus::Running } else { StateMachineNodeStatus::Completed };
        store.create_run(run.clone(), vec![node]).await.unwrap();
        let payload = Payload { command: StateMachineResultPublishCommand { run_id: r.into(), group_id: run.group_id.clone(),
            session_id: run.session_id.clone(), sender_bot_id: "original-bot".into(), content: "原结果\n原目标".into(), created_at_ms: 100 }, deadline_ms: 1000 };
        if case == "unfinished" { assert!(!store.save_chat_result(payload).await.unwrap()); continue; }
        for change in [0, 1, 2] {
            let mut wrong = payload.clone();
            match change { 0 => wrong.command.group_id = "wrong".into(), 1 => wrong.command.session_id = "wrong".into(), _ => wrong.command.sender_bot_id = "wrong".into() }
            assert!(!store.save_chat_result(wrong).await.unwrap());
        }
        let (a, b) = tokio::join!(store.save_chat_result(payload.clone()), store.save_chat_result(payload.clone()));
        assert!(a.unwrap() && b.unwrap());
        let mut changed = payload.clone(); changed.command.content.push('!');
        assert!(store.save_chat_result(changed).await.is_err());
        assert_eq!(store.get_chat_result(r).await.unwrap().unwrap().payload, payload);
        let (a, b) = tokio::join!(store.claim_chat_result(r, "first".into(), 110, 150), store.claim_chat_result(r, "second".into(), 110, 150));
        let claims = [a.unwrap(), b.unwrap()].into_iter().flatten().collect::<Vec<_>>();
        assert_eq!(claims.len(), 1);
        let old = &claims[0];
        let new = store.claim_chat_result(r, "takeover".into(), 150, 900).await.unwrap().unwrap();
        assert!(new.token > old.token);
        assert!(!store.begin_chat_result_send(old, 160).await.unwrap());
        assert!(!store.finish_chat_result(old, Outcome::Published, 160).await.unwrap());
        assert!(!store.release_chat_result(old).await.unwrap());
        assert!(!store.expire_chat_result(r, 999).await.unwrap());
        if case == "cancel" {
            store.update_run_status(r, StateMachineRunStatus::Aborted, None, None, 160, Some(160)).await.unwrap();
            assert!(!store.begin_chat_result_send(&new, 170).await.unwrap());
            assert!(!store.finish_chat_result(&new, Outcome::Failed { error: "late".into() }, 170).await.unwrap());
            assert!(!store.expire_chat_result(r, 1000).await.unwrap());
            assert!(store.claim_chat_result(r, "cancelled".into(), 901, 999).await.unwrap().is_none());
        } else if case == "pending" {
            assert!(store.release_chat_result(&new).await.unwrap());
            assert!(store.expire_chat_result(r, 1000).await.unwrap());
        } else {
            assert!(store.begin_chat_result_send(&new, 160).await.unwrap());
            assert!(!store.begin_chat_result_send(&new, 161).await.unwrap());
            match case {
                "ack" => {
                    assert!(store.finish_chat_result(&new, Outcome::Published, 170).await.unwrap());
                    assert!(!store.expire_chat_result(r, 1000).await.unwrap());
                    let saved = store.get_chat_result(r).await.unwrap().unwrap();
                    assert_eq!(saved.status, Status::Delivered); assert_eq!(saved.delivered_at_ms, Some(170));
                    assert!(saved.lease_owner.is_none());
                }
                "failed" => {
                    assert!(store.finish_chat_result(&new, Outcome::Failed { error: "saved rejection".into() }, 170).await.unwrap());
                    assert_eq!(store.get_chat_result(r).await.unwrap().unwrap().error.as_deref(), Some("saved rejection"));
                }
                "race" => {
                    let (ack, expire) = tokio::join!(store.finish_chat_result(&new, Outcome::Published, 899), store.expire_chat_result(r, 1000));
                    assert_ne!(ack.unwrap(), expire.unwrap());
                }
                "unknown" => {
                    assert!(store.release_chat_result(&new).await.unwrap());
                    assert!(store.claim_chat_result(r, "no-replay".into(), 900, 999).await.unwrap().is_none());
                    assert!(!store.finish_chat_result(&new, Outcome::Published, 901).await.unwrap());
                    assert!(store.expire_chat_result(r, 1000).await.unwrap());
                }
                _ => unreachable!(),
            }
        }
        assert!(store.save_chat_result(payload).await.unwrap());
        assert!(!store.finish_chat_result(&new, Outcome::Published, 1001).await.unwrap());
        if matches!(case, "pending" | "unknown") {
            assert_eq!(store.get_chat_result(r).await.unwrap().unwrap().status, Status::Failed);
        }
    }
}

#[tokio::test]
async fn memory_chat_publication_contract() { publication_contract(&MemoryCollaborationStore::new()).await; }

#[tokio::test]
async fn sqlite_chat_publication_contract() {
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bootstrap_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let store = MySqlCollaborationStore::sqlite(db.clone(), "test".into());
    publication_contract(&store).await;
    assert!(MySqlCollaborationStore::sqlite(db.clone(), "other".into()).get_chat_result("publication-ack").await.unwrap().is_none());
    db.execute(DbStatement::new("UPDATE bcs_collaboration_delivery_checkpoints SET payload_json = '{}' WHERE aggregate_id = 'publication-ack'")).await.unwrap();
    assert!(store.get_chat_result("publication-ack").await.is_err());
}
