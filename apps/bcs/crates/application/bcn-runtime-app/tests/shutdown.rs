mod common;
use bcn_runtime_api::{
    application::*,
    port::{Clock, repo::*},
};
use bcn_runtime_model::*;
use common::*;
use std::sync::Arc;
#[tokio::test]
async fn connector_keeps_receiving_while_worker_is_stopped_and_drain_preserves_recovery() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("independent")).await.unwrap();
    let w = f.worker("w");
    w.step(&task.session_id).await.unwrap();
    let t = Arc::new(Transport::new(vec![
        Ok(AgentEvent::Accepted { handle: handle() }),
        Ok(AgentEvent::Completed {
            output: text("while-worker-stopped"),
        }),
    ]));
    let c = f.connector("c", t);
    w.drain(1000).await.unwrap();
    assert_eq!(w.status().await.unwrap().phase, RuntimePhase::Stopped);
    for _ in 0..4 {
        c.tick().await.unwrap();
        f.clock.set(Clock::now_ms(f.clock.as_ref()) + 250);
    }
    assert_eq!(
        g.get(caller(), task.task_id.clone()).await.unwrap().status,
        TaskStatus::Working
    );
    assert!(
        f.repo
            .read_batch(&task.session_id, 1, 10)
            .await
            .unwrap()
            .len()
            >= 2
    );
    let new = f.worker("new");
    f.pump(&new, &c, 30).await;
    assert_eq!(
        g.get(caller(), task.task_id).await.unwrap().status,
        TaskStatus::Completed
    );
}
#[tokio::test]
async fn drain_deadline_drops_socket_but_preserves_active_dispatch_and_rejects_new_claims() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("drain")).await.unwrap();
    let w = f.worker("w");
    w.step(&task.session_id).await.unwrap();
    let t = Arc::new(Transport::new(vec![]));
    let c = f.connector("c", t);
    c.tick().await.unwrap();
    let report = c.drain(2000).await.unwrap();
    assert_eq!(report.remaining, 1);
    assert!(!report.deadline_reached);
    f.clock.set(2000);
    let report = c.drain(2000).await.unwrap();
    assert!(report.deadline_reached);
    assert_eq!(c.status().await.unwrap().phase, RuntimePhase::Stopped);
    assert_eq!(
        DispatchStore::scan_active(f.repo.as_ref(), &scope(), None, 10)
            .await
            .unwrap()
            .items
            .len(),
        1
    );
}
