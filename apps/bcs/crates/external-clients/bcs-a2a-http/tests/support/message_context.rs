//! Taskless Message contexts must survive into the next public Send.
use super::*;

#[tokio::test]
async fn standalone_message_establishes_context_without_inventing_a_task() {
    for next in [Some("message-context"),None] {
        let f=fixture(Mode::MessageContext {first:"message-context",next}).await;
        for run in ["message-first","message-next"] {
            assert!(f.transport.deliver(command(&f,run).await).await.unwrap().delivered);
            completed(&f,run).await;
            let row=f.runs.get(run).await.unwrap().unwrap();
            assert_eq!(row.context_id.as_deref(),Some("message-context"));
            assert!(row.remote_task_id.is_none());assert!(row.task_version.is_none());
            assert_eq!(f.runs.context(&row.scope).await.unwrap(),row.context_id);
            let mut other=row.scope.clone();other.session_id="other-session".into();
            assert!(f.runs.context(&other).await.unwrap().is_none());
        }
        let requests=f.agent.requests.lock().unwrap();
        assert!(requests[0]["message"]["contextId"].is_null());
        assert_eq!(requests[1]["message"]["contextId"],"message-context");
        assert!(f.agent.cancels.lock().unwrap().is_empty());
    }
}

#[tokio::test]
async fn empty_or_changed_message_context_is_rejected_before_text_publication() {
    for (first,next) in [("",None),("original-context",Some("foreign-context"))] {
        let f=fixture(Mode::MessageContext {first,next}).await;
        if !first.is_empty() {
            f.transport.deliver(command(&f,"valid-first").await).await.unwrap();
            completed(&f,"valid-first").await;
        }
        assert!(f.transport.deliver(command(&f,"invalid-context").await).await.is_err());
        let row=f.runs.get("invalid-context").await.unwrap().unwrap();
        assert_ne!(row.state,A2aRunState::Completed);assert!(row.remote_task_id.is_none());
        assert_eq!(f.runs.context(&row.scope).await.unwrap().as_deref(),
            if first.is_empty() {None} else {Some(first)});
        assert!(!f.ingest.events.lock().unwrap().iter().any(|event|
            event.run_id=="invalid-context" && matches!(event.state,ChatEventState::Delta | ChatEventState::Final)));
        assert!(f.agent.cancels.lock().unwrap().is_empty());
    }
}
