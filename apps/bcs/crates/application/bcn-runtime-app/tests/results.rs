mod common;
use bcn_runtime_api::{application::*, port::repo::*};
use bcn_runtime_model::*;
use common::faults::*;
use common::*;
use std::{sync::Arc, sync::atomic::Ordering};
#[tokio::test]
async fn terminal_snapshot_is_retained_when_projection_write_fails_after_stream_read() {
    let mut f = Fixture::new();
    let events = Arc::new(EventsFault::new(f.repo.clone()));
    f.ports.events = events.clone();
    let g = f.gateway();
    let task = g.submit(caller(), request("snapshot")).await.unwrap();
    let w = f.worker("w");
    w.step(&task.session_id).await.unwrap();
    let t = Arc::new(Transport::new(vec![Ok(AgentEvent::Snapshot {
        snapshot: ExecutionSnapshot { input_prompt: None, observation_error: None,
            handle: handle(),
            status: TaskStatus::Completed,
            output: text("authoritative streamed result"),
            error: None,
        },
    })]));
    let c = f.connector("c", t.clone());
    c.tick().await.unwrap();
    events.reset.store(true, Ordering::SeqCst);
    assert!(!c.tick().await.unwrap().errors.is_empty());
    f.pump(&w, &c, 35).await;
    let task = g.get(caller(), task.task_id).await.unwrap();
    assert_eq!(task.status, TaskStatus::Completed);
    assert_eq!(task.output, text("authoritative streamed result"));
    assert_eq!(t.sends.load(Ordering::SeqCst), 1);
}
#[tokio::test]
async fn lost_hard_publish_response_replays_once_without_stale_revision() {
    let mut f = Fixture::new();
    let events = Arc::new(EventsFault::new(f.repo.clone()));
    events.hard_after_commit.store(true, Ordering::SeqCst);
    f.ports.events = events;
    let g = f.gateway();
    let task = g.submit(caller(), request("publish")).await.unwrap();
    let w = f.worker("w");
    assert!(w.step(&task.session_id).await.is_err());
    let cp = f.repo.load(&task.session_id).await.unwrap().unwrap();
    assert_eq!(cp.consumed_input, 1);
    assert!(cp.pending_events.is_empty());
    assert_eq!(cp.pending_effects.len(), 1);
    let t = Arc::new(Transport::new(vec![Ok(AgentEvent::Completed {
        output: text("done"),
    })]));
    let c = f.connector("c", t);
    f.pump(&w, &c, 20).await;
    let task = g.get(caller(), task.task_id).await.unwrap();
    assert_eq!(task.status, TaskStatus::Completed);
    assert_eq!(task.output, text("done"));
}
#[tokio::test]
async fn oversized_unicode_output_becomes_bounded_saved_failure() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("oversize")).await.unwrap();
    let w = f.worker("w");
    let t = Arc::new(Transport::new(vec![Ok(AgentEvent::Delta {
        text: "中文".repeat(15000),
    })]));
    let c = f.connector("c", t);
    f.pump(&w, &c, 20).await;
    let task = g.get(caller(), task.task_id).await.unwrap();
    assert_eq!(task.status, TaskStatus::Failed);
    assert_eq!(task.error, Some(RuntimeError::CapacityExceeded));
    assert!(!task.output.is_empty());
    assert!(serde_json::to_vec(&task.output).unwrap().len() <= 65536);
}
#[tokio::test]
async fn pending_result_io_does_not_block_bounded_worker_tick_or_heartbeat() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("pending-io")).await.unwrap();
    let w = f.worker("w");
    w.step(&task.session_id).await.unwrap();
    let c = f.connector(
        "c",
        Arc::new(Transport::new(vec![Ok(AgentEvent::Completed {
            output: text("result"),
        })])),
    );
    c.tick().await.unwrap();
    c.tick().await.unwrap();
    w.step(&task.session_id).await.unwrap();
    f.results.hang.store(true, Ordering::SeqCst);
    tokio::time::timeout(std::time::Duration::from_millis(100),w.tick()).await.expect("tick must remain bounded during pending I/O").unwrap();
    f.clock.set(2000);
    tokio::time::timeout(std::time::Duration::from_millis(100),w.tick()).await.unwrap().unwrap();
    assert_eq!(
        g.get(caller(), task.task_id).await.unwrap().status,
        TaskStatus::PendingResult
    );
}

struct YieldingResults(Arc<dyn ResultStore>);
#[async_trait::async_trait]
impl ResultStore for YieldingResults {
 async fn put_if_absent(&self,i:ResultIntent)->RuntimeResult<SavedResult>{for _ in 0..20 {tokio::task::yield_now().await;}self.0.put_if_absent(i).await}
 async fn get(&self,i:&ResultId)->RuntimeResult<Option<SavedResult>>{self.0.get(i).await}
 async fn list_tasks(&self,c:&CallerContext,q:TaskQuery)->RuntimeResult<TaskPage>{self.0.list_tasks(c,q).await}
 async fn read_group_records(&self,t:&TaskId,c:Option<String>,l:u32)->RuntimeResult<Page<SavedResult>>{self.0.read_group_records(t,c,l).await}
}
#[tokio::test]
async fn asynchronous_result_and_ack_progress_between_scheduler_ticks() {
 let mut f=Fixture::new();f.ports.results=Arc::new(YieldingResults(f.ports.results.clone()));
 let g=f.gateway();let task=g.submit(caller(),request("async-save")).await.unwrap();let w=f.worker("w");w.step(&task.session_id).await.unwrap();
 let c=f.connector("c",Arc::new(Transport::new(vec![Ok(AgentEvent::Completed{output:text("saved")})])));
 c.tick().await.unwrap();c.tick().await.unwrap();w.step(&task.session_id).await.unwrap();
 w.tick().await.unwrap();
 tokio::time::sleep(std::time::Duration::from_millis(30)).await;
 let inputs=f.repo.read_batch(&task.session_id,0,100).await.unwrap();
 assert!(inputs.iter().any(|i|matches!(i.input.kind,SessionInputKind::ResultSaved{..})),"external I/O must keep being polled while the scheduler sleeps or scans shards");
 f.clock.set(5000);
 let remaining=f.repo.receive(&scope(),&node("observer"),CommandConsumerKind::Worker,None,100).await.unwrap();assert!(remaining.items.is_empty(),"completed result command must ACK without tick-driven lease mutation");
}
