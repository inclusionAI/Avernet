mod common;
use common::*;
use bcn_runtime_api::{application::*,port::Clock};
use bcn_runtime_model::*;
use futures::StreamExt;
use serde_json::{json, Value};

fn active(t: &TaskView) -> Vec<Value> {
    serde_json::to_value(t).unwrap().get("active_interactions").and_then(Value::as_array).cloned().unwrap_or_default()
}
#[tokio::test]
async fn parallel_questions_are_durable_and_only_resolved_question_disappears() {
    let f = Fixture::new();
    let g = group::gateway(&f, json!({"a":group::human(),"b":group::human()}));
    let w = f.worker("w");
    let task = group::start_parallel(&f, &g, &w, group::group_request("parallel")).await;
    let view = g.get(caller(), task.task_id.clone()).await.unwrap();
    let questions = active(&view);
    assert_eq!(questions.len(), 2);
    let mut reply = group::group_request("answer-a");
    reply.message.task_id = Some(task.task_id.clone());
    assert!(matches!(g.submit(caller(), reply.clone()).await, Err(RuntimeError::InvalidInput(_))));
    reply.interaction_id = Some(serde_json::from_value(questions[0]["interaction_id"].clone()).unwrap());
    g.submit(caller(), reply).await.unwrap();
    for _ in 0..4 { w.step(&task.session_id).await.unwrap(); w.tick().await.unwrap(); }
    let after = g.get(caller(), task.task_id.clone()).await.unwrap();
    assert_eq!(active(&after), vec![questions[1].clone()]);
    let mut stream = g.subscribe(caller(), task.task_id.clone(), None).await.unwrap();
    let TaskEventKind::Reset { snapshot } = stream.next().await.unwrap().unwrap().kind else { panic!("snapshot first") };
    assert_eq!(active(&snapshot), active(&after));
    g.cancel(caller(), CancelCommand { task_id: task.task_id.clone(), operation_id: OperationId::new(scope(), "cancel") }).await.unwrap();
    for _ in 0..8 { w.step(&task.session_id).await.unwrap(); w.tick().await.unwrap(); }
    let terminal = g.get(caller(), task.task_id).await.unwrap();
    assert!(terminal.status.is_terminal()); assert!(active(&terminal).is_empty());
}

#[tokio::test]
async fn cumulative_questions_fail_with_saved_result_instead_of_stuck_publication() {
    use bcn_runtime_api::port::repo::*;
    use std::sync::atomic::Ordering;
    let f=Fixture::new();
    let nodes=(0..25).map(|n|(format!("n{n:02}"),group::bot())).collect::<serde_json::Map<_,_>>();
    let g=group::gateway(&f,Value::Object(nodes));let w=f.worker("w");
    let task=group::start_parallel(&f,&g,&w,group::group_request("prompt-budget")).await;
    let cp=f.repo.load(&task.session_id).await.unwrap().unwrap();
    let running=cp.state.current_task.unwrap().nodes.into_iter().filter(|(_,n)|n.phase==NodePhase::Running).collect::<Vec<_>>();
    assert_eq!(running.len(),25);
    // 900000 bytes of independently published artifacts must coexist with questions.
    let commands=f.repo.receive(&scope(),&node("publisher"),CommandConsumerKind::Connector,None,100).await.unwrap();
    for (index,command) in commands.items.iter().take(15).enumerate() {
        let record=f.repo.claim(&command.command_id,node("publisher")).await.unwrap();
        let record=f.repo.mark_may_have_sent(&record.fence,record.revision).await.unwrap();
        let cursor=f.repo.read_snapshot(&task.task_id).await.unwrap().unwrap().cursor;
        f.repo.publish_reset(&record.fence,ConnectorProjection{task_id:task.task_id.clone(),output:text(&"o".repeat(60000)),expected_cursor:cursor},EventId::new(scope(),format!("large-output-{index}"))).await.unwrap();
    }
    assert!(serde_json::to_vec(&g.get(caller(),task.task_id.clone()).await.unwrap().output).unwrap().len()>890000);
    f.results.down.store(true,Ordering::SeqCst);
    let mut questions=0;
    for (node,n) in running {
        f.ports.inbox.append_and_wake(&task.session_id,SessionInput {
            event_id:EventId::new(scope(),format!("question-{node}")),at_ms:2000,
            kind:SessionInputKind::Agent{task_id:task.task_id.clone(),node_id:Some(node),dispatch_id:n.dispatch_id.unwrap(),event:AgentEvent::InputRequired{handle:handle(),prompt:text(&"q".repeat(60000))}},
        }).await.unwrap();
        w.step(&task.session_id).await.unwrap();questions+=1;
        let view=g.get(caller(),task.task_id.clone()).await.unwrap();
        if questions==1 {
            assert_eq!(view.status,TaskStatus::InputRequired);
            assert!(serde_json::to_vec(&view).unwrap().len()<MAX_VALUE_BYTES);
            assert_eq!(active(&view).len(),1);
        }
        if view.status==TaskStatus::PendingResult {
            assert!(questions<25,"cumulative checkpoint limit applied before all prompts");
            assert!(active(&view).is_empty(),"failed task must not expose stale answerable questions");
            break;
        }
    }
    assert!(questions<25,"budget must fail explicitly, not fill unbounded snapshot");
    f.results.down.store(false,Ordering::SeqCst);
    for _ in 0..12 {w.tick().await.unwrap();w.step(&task.session_id).await.unwrap();f.clock.set(Clock::now_ms(f.clock.as_ref())+1000);}
    let view=g.get(caller(),task.task_id.clone()).await.unwrap();assert_eq!(view.status,TaskStatus::Failed);assert_eq!(view.error,Some(RuntimeError::CapacityExceeded));
    assert!(active(&view).is_empty());
    assert!(f.repo.load(&task.session_id).await.unwrap().unwrap().pending_events.is_empty());
}
#[tokio::test]
async fn new_reply_waits_for_durable_question_publication_before_acceptance(){
    use bcn_runtime_api::port::repo::*;
    let f=Fixture::new();let g=group::gateway(&f,json!({"a":group::human()}));
    let task=g.submit(caller(),group::group_request("unpublished")).await.unwrap();
    let fence=f.repo.claim_shard(session_shard(&task.session_id),node("publisher"),None).await.unwrap();
    let spec=f.repo.load_spec(&task.session_id).await.unwrap().unwrap();let runner=f.runners().remove(1);
    let cp=f.repo.activate(spec.clone(),runner.initialize(&spec).unwrap(),fence).await.unwrap();
    let input=f.repo.read_batch(&task.session_id,0,1).await.unwrap().remove(0);
    let cp=f.repo.commit(SessionCommit{session_id:task.session_id.clone(),fence:cp.fence,expected_revision:cp.revision,consumed_input:input.position,transition:runner.reduce(&cp.state,&input.input).unwrap()}).await.unwrap();
    assert!(g.get(caller(),task.task_id.clone()).await.unwrap().active_interactions.is_empty());
    let mut reply=group::group_request("answer-unpublished");reply.message.task_id=Some(task.task_id.clone());
    assert!(matches!(g.submit(caller(),reply.clone()).await,Err(RuntimeError::Conflict(_))),"checkpoint alone must not admit a reply before its question is published");
    assert_eq!(f.repo.read_batch(&task.session_id,0,100).await.unwrap().len(),1);
    for event in &cp.pending_events{f.repo.publish_hard_event(&cp.fence,CommittedEventRef{session_id:task.session_id.clone(),event_id:event.event_id.clone()}).await.unwrap();}
    let accepted=g.submit_with_receipt(caller(),reply).await.unwrap();
    assert_eq!(accepted.reply_interaction_id,Some(accepted.task.active_interactions[0].interaction_id.clone()));
}
