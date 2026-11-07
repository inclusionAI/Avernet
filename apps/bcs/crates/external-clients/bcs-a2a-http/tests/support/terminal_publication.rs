//! Observe the actual SQL correlation at the callback that can free a lane.
use super::*;

struct TerminalIngest {
    base:Arc<Ingest>, runs:Arc<DbA2aRunStore>, transport:A2aHttpTransport,
    next:Mutex<Option<BotDeliveryCommand>>, next_accepted:Mutex<Option<bool>>,
    observed:Mutex<Vec<(String,A2aRunState,u64)>>,
    failures:AtomicUsize,
}

impl TerminalIngest {
    fn bind(f:&Fixture,failures:usize,next:Option<BotDeliveryCommand>)->Arc<Self> {
        let ingest=Arc::new(Self {base:f.ingest.clone(),runs:f.runs.clone(),transport:f.transport.clone(),
            next:Mutex::new(next),next_accepted:Mutex::new(None),observed:Mutex::new(vec![]),failures:AtomicUsize::new(failures)});
        f.transport.set_ingest(ingest.clone(),f.contexts.clone()).unwrap();
        ingest
    }
}

#[async_trait]
impl ProviderEventIngestService for TerminalIngest {
    async fn ingest_provider_event(&self,command:ProviderEventIngestCommand)->ServiceResult<BotEventOutcome> {
        if matches!(command.event.state,ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted) {
            let record=self.runs.get(&command.event.run_id).await?.unwrap();
            self.observed.lock().unwrap().push((record.run_id,record.state,record.version));
            if self.failures.fetch_update(Ordering::SeqCst,Ordering::SeqCst,|left|left.checked_sub(1)).is_ok() {
                return Err(bcs_service_api::ServiceError::InternalError("injected terminal ingestion failure".into()));
            }
            // The real queue can dispatch its next Send while ingestion is
            // still returning. Re-enter the public delivery port at that seam.
            let next={self.next.lock().unwrap().take()};
            if let Some(next)=next {
                let accepted=self.transport.deliver(next).await.is_ok_and(|result|result.delivered);
                *self.next_accepted.lock().unwrap()=Some(accepted);
            }
        }
        self.base.ingest_provider_event(command).await
    }
}

async fn published(f:&Fixture,runs:&[&str]) {
    tokio::time::timeout(Duration::from_secs(5),async {
        loop {
            let mut done=true;
            for run in runs {
                done &= f.contexts.get_provider_transport(run).await==Some(bcs_service_api::ProviderRunTransport::Terminal);
            }
            if done {break;}
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.unwrap();
}

#[tokio::test]
async fn next_send_at_terminal_ingestion_observes_committed_task_or_message() {
    for (mode,expected) in [
        (Mode::Complete,A2aRunState::Completed),(Mode::Message,A2aRunState::Completed),
        (Mode::Terminal("TASK_STATE_FAILED"),A2aRunState::Failed),
        (Mode::Terminal("TASK_STATE_REJECTED"),A2aRunState::Failed),
        (Mode::Terminal("TASK_STATE_CANCELED"),A2aRunState::Canceled),
    ] {
        let f=fixture(mode).await;
        let next=command(&f,"next-at-terminal").await;
        let ingest=TerminalIngest::bind(&f,0,Some(next));
        assert!(f.transport.deliver(command(&f,"first-at-terminal").await).await.unwrap().delivered);
        published(&f,&["first-at-terminal","next-at-terminal"]).await;
        assert_eq!(*ingest.next_accepted.lock().unwrap(),Some(true));
        let observed=ingest.observed.lock().unwrap();
        assert_eq!(observed.len(),2);
        assert!(observed.iter().all(|(_,state,_)|*state==expected));
        assert_eq!(f.agent.requests.lock().unwrap().len(),2,"next message must reach the peer exactly once");
        assert!(f.agent.cancels.lock().unwrap().is_empty());
    }
}

#[tokio::test]
async fn terminal_ingestion_failure_retries_notification_without_replaying_text_or_state() {
    for (mode,text) in [(Mode::Complete,"你好世界"),(Mode::Message,"无任务回复")] {
        let f=fixture(mode).await;
        let ingest=TerminalIngest::bind(&f,1,None);
        assert!(f.transport.deliver(command(&f,"retry-terminal").await).await.unwrap().delivered);
        published(&f,&["retry-terminal"]).await;
        let observed=ingest.observed.lock().unwrap();
        assert_eq!(observed.len(),2);
        assert!(observed.iter().all(|(_,state,version)|*state==A2aRunState::Completed && *version==observed[0].2));
        let events=f.ingest.events.lock().unwrap();
        assert_eq!(events.iter().filter(|event|event.state==ChatEventState::Final).count(),1);
        assert_eq!(events.iter().filter_map(|event|event.event_payload["delta_text"].as_str()).collect::<String>(),text);
        assert_eq!(f.agent.requests.lock().unwrap().len(),1);
        assert!(f.agent.cancels.lock().unwrap().is_empty());
    }
}

#[tokio::test]
async fn exhausted_terminal_ingestion_keeps_confirmed_remote_state_without_false_local_completion() {
    for mode in [Mode::Complete,Mode::Message] {
        let f=fixture(mode).await;
        let ingest=TerminalIngest::bind(&f,usize::MAX,None);
        let result=f.transport.deliver(command(&f,"blocked-terminal").await).await;
        if matches!(mode,Mode::Message) {assert!(result.is_err());} else {assert!(result.unwrap().delivered);}
        tokio::time::timeout(Duration::from_secs(4),async {
            while ingest.observed.lock().unwrap().len()<3 {tokio::time::sleep(Duration::from_millis(10)).await;}
        }).await.unwrap();
        assert_eq!(ingest.observed.lock().unwrap().len(),3);
        assert_eq!(f.runs.get("blocked-terminal").await.unwrap().unwrap().state,A2aRunState::Completed);
        assert_ne!(f.contexts.get_provider_transport("blocked-terminal").await,Some(bcs_service_api::ProviderRunTransport::Terminal));
        assert!(!f.ingest.events.lock().unwrap().iter().any(|event|matches!(event.state,ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted)));
        assert!(f.agent.cancels.lock().unwrap().is_empty(),"a confirmed remote terminal must not be canceled after local ingestion failure");
        assert_eq!(f.agent.requests.lock().unwrap().len(),1);
    }
}

#[tokio::test]
async fn failed_terminal_cas_never_publishes_a_lane_releasing_event() {
    for mode in [Mode::Complete,Mode::Message] {
        let f=fixture(mode).await;
        f.db.execute(DbStatement::new("CREATE TRIGGER reject_terminal BEFORE UPDATE ON bcs_a2a_run_bindings WHEN NEW.state IN ('completed','failed','canceled') BEGIN SELECT RAISE(ABORT,'injected terminal persistence failure'); END")).await.unwrap();
        let ingest=TerminalIngest::bind(&f,0,None);
        let result=f.transport.deliver(command(&f,"failed-terminal-cas").await).await;
        if matches!(mode,Mode::Message) {assert!(result.is_err());} else {assert!(result.unwrap().delivered);}
        tokio::time::timeout(Duration::from_secs(4),async {
            loop {
                let failed=if matches!(mode,Mode::Message) {
                    f.runs.get("failed-terminal-cas").await.unwrap().is_some_and(|run|run.state==A2aRunState::Unknown)
                } else {!f.agent.cancels.lock().unwrap().is_empty()};
                if failed {break;}
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }).await.unwrap();
        let saved=f.runs.get("failed-terminal-cas").await.unwrap().unwrap();
        assert!(!saved.state.is_terminal());
        assert_eq!(f.runs.active(&saved.scope).await.unwrap().len(),1);
        assert!(ingest.observed.lock().unwrap().is_empty());
        assert!(!f.ingest.events.lock().unwrap().iter().any(|event|matches!(event.state,ChatEventState::Final | ChatEventState::Error | ChatEventState::Aborted)));
        assert_ne!(f.contexts.get_provider_transport("failed-terminal-cas").await,Some(bcs_service_api::ProviderRunTransport::Terminal));
    }
}
