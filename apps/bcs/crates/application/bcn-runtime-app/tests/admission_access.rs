mod common;
use bcn_runtime_api::application::*;
use bcn_runtime_model::*;
use common::*;
use futures::StreamExt;
use std::sync::Arc;
#[tokio::test]
async fn all_routes_enforce_principal_scope_target_and_original_context() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("private")).await.unwrap();
    let mut other = caller();
    other.principal = "bob".into();
    assert_eq!(
        g.get(other.clone(), task.task_id.clone()).await,
        Err(RuntimeError::AccessDenied)
    );
    assert!(
        g.subscribe(other.clone(), task.task_id.clone(), None)
            .await
            .is_err()
    );
    assert!(
        g.cancel(
            other.clone(),
            CancelCommand {
                task_id: task.task_id.clone(),
                operation_id: OperationId::new(scope(), "cancel")
            }
        )
        .await
        .is_err()
    );
    assert!(
        g.list(
            other.clone(),
            TaskQuery {
                target: None,
                session_id: None,
                cursor: None,
                limit: 10
            }
        )
        .await
        .unwrap()
        .items
        .is_empty()
    );
    let mut hijack = request("hijack");
    hijack.message.context_id = Some(task.session_id.clone());
    assert_eq!(
        g.submit(other, hijack).await,
        Err(RuntimeError::AccessDenied)
    );
    let mut cross = caller();
    cross.scope.tenant = "other".into();
    assert_eq!(
        g.get(cross, task.task_id.clone()).await,
        Err(RuntimeError::AccessDenied)
    );
    let mut conflict = request("private");
    conflict.message.parts = text("changed");
    assert!(matches!(
        g.submit(caller(), conflict).await,
        Err(RuntimeError::Conflict(_))
    ));
    let mut trace = caller();
    trace.trace_id = Some("retry-trace".into());
    assert_eq!(
        g.submit(trace, request("private")).await.unwrap().task_id,
        task.task_id
    );
    let mut reply = request("reply");
    reply.message.task_id = Some(task.task_id.clone());
    reply.message.context_id = Some(SessionId::new(scope(), "wrong"));
    assert_eq!(
        g.submit(caller(), reply).await,
        Err(RuntimeError::AccessDenied)
    );
    let mut stream = g.subscribe(caller(), task.task_id, None).await.unwrap();
    assert!(matches!(
        stream.next().await.unwrap().unwrap().kind,
        TaskEventKind::Reset { .. }
    ));
}
#[tokio::test]
async fn reply_and_cancel_bypass_full_admission_and_replay_after_clock_and_task_switch() {
    let f = Fixture::new();
    let g = f.gateway();
    let first = g.submit(caller(), request("first")).await.unwrap();
    let w = f.worker("w");
    let transport = Arc::new(Transport::new(vec![Ok(AgentEvent::InputRequired {
        handle: handle(),
        prompt: text("answer"),
    })]));
    let c = f.connector("c", transport.clone());
    f.pump(&w, &c, 8).await;
    for n in 0..15 {
        let mut next = request(&format!("queued{n}"));
        next.message.context_id = Some(first.session_id.clone());
        g.submit(caller(), next).await.unwrap();
    }
    let mut overflow = request("overflow");
    overflow.message.context_id = Some(first.session_id.clone());
    assert_eq!(
        g.submit(caller(), overflow).await,
        Err(RuntimeError::CapacityExceeded)
    );
    let mut reply = request("answer");
    reply.message.task_id = Some(first.task_id.clone());
    g.submit(caller(), reply.clone()).await.unwrap();
    f.clock.set(7000);
    g.submit(caller(), reply.clone()).await.unwrap();
    *transport.events.lock().unwrap() = vec![Ok(AgentEvent::Completed {
        output: text("answered"),
    })];
    f.pump(&w, &c, 12).await;
    assert_eq!(
        g.get(caller(), first.task_id.clone()).await.unwrap().status,
        TaskStatus::Completed
    );
    assert_eq!(
        g.submit(caller(), reply.clone()).await.unwrap().status,
        TaskStatus::Completed
    );
    reply.message.parts = text("changed");
    assert!(matches!(
        g.submit(caller(), reply).await,
        Err(RuntimeError::Conflict(_))
    ));
    let task = g
        .list(
            caller(),
            TaskQuery {
                target: None,
                session_id: Some(first.session_id),
                cursor: None,
                limit: 100,
            },
        )
        .await
        .unwrap()
        .items
        .into_iter()
        .find(|t| t.status == TaskStatus::Submitted)
        .unwrap();
    let cancel = CancelCommand {
        task_id: task.task_id.clone(),
        operation_id: OperationId::new(scope(), "cancel-queued"),
    };
    g.cancel(caller(), cancel.clone()).await.unwrap();
    f.clock.set(12000);
    g.cancel(caller(), cancel.clone()).await.unwrap();
    f.pump(&w, &c, 12).await;
    assert_eq!(
        g.cancel(caller(), cancel).await.unwrap().status,
        TaskStatus::Cancelled
    );
}
