use super::*;

pub(super) async fn send(State(agent):State<Arc<Agent>>,headers:HeaderMap,Json(body):Json<Value>)->Response {
    if matches!(agent.mode,Mode::Anonymous) {assert!(!headers.contains_key("authorization"));}
    else {assert_eq!(headers["authorization"],"Bearer test-a2a-bearer");}
    assert_eq!(body["configuration"]["returnImmediately"],true);
    assert!(!headers.contains_key("a2a-extensions"));
    agent.requests.lock().unwrap().push(body);
    if matches!(agent.mode,Mode::Message) {
        return Json(json!({"message":{"messageId":"reply","role":"ROLE_AGENT","parts":[{"text":"直接回复"}]}})).into_response();
    }
    let task=json!({"id":"poll-task","contextId":"remote-context","status":{"state":"TASK_STATE_WORKING"}});
    let mut snapshot=task.clone();
    if matches!(agent.mode,Mode::Complete | Mode::RecoverGet | Mode::Anonymous) {
        snapshot["status"]["state"]=json!("TASK_STATE_COMPLETED");
        snapshot["artifacts"]=json!([{"artifactId":"answer","parts":[{"text":"完整回复"}]}]);
    }
    if let Mode::Terminal(state)=agent.mode {snapshot["status"]["state"]=json!(state);}
    agent.tasks.lock().unwrap().insert("poll-task".into(),snapshot);
    Json(json!({"task":task})).into_response()
}
fn polling(f:&mut Fixture) {
    let BotDeliveryTarget::A2a {interface,..}=&mut f.target else {panic!("A2A target required")};
    interface.update_mode=A2aUpdateMode::Poll;
}

#[tokio::test]
async fn non_streaming_message_completes_without_query_or_subscription() {
    let mut f=fixture(Mode::Message).await;polling(&mut f);
    assert!(f.transport.deliver(command(&f,"poll-message").await).await.unwrap().delivered);
    completed(&f,"poll-message").await;
    assert_eq!(f.agent.queries.load(Ordering::SeqCst),0);
    assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),0);
    assert_eq!(f.agent.requests.lock().unwrap().len(),1);
}

#[tokio::test]
async fn non_streaming_task_queries_and_recovers_without_resubmission() {
    for mode in [Mode::Complete,Mode::RecoverGet] {
        let mut f=fixture(mode).await;polling(&mut f);
        assert!(f.transport.deliver(command(&f,"poll-task-run").await).await.unwrap().delivered);
        completed(&f,"poll-task-run").await;
        assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),0);
        assert_eq!(f.agent.requests.lock().unwrap().len(),1);
        assert_eq!(f.agent.queries.load(Ordering::SeqCst),if matches!(mode,Mode::RecoverGet) {2} else {1});
        let events=f.ingest.events.lock().unwrap();
        assert_eq!(events.iter().filter(|e|e.state==ChatEventState::Delta).count(),1);
        assert_eq!(events.iter().filter(|e|e.state==ChatEventState::Final).count(),1);
    }
}

#[tokio::test]
async fn anonymous_submission_does_not_leak_existing_provider_credential() {
    let mut f=fixture(Mode::Anonymous).await;polling(&mut f);
    if let BotDeliveryTarget::A2a {credential_kind,bearer,..}=&mut f.target {
        *credential_kind=bcs_domain::a2a_auth::ANONYMOUS.into();
        *bearer=RedactedToken::new("");
    }
    assert!(f.transport.deliver(command(&f,"anonymous-poll").await).await.unwrap().delivered);
    completed(&f,"anonymous-poll").await;
    assert_eq!(f.agent.requests.lock().unwrap().len(),1);
}

async fn wait_for_cleanup(f:&Fixture,run:&str) {
    tokio::time::timeout(Duration::from_secs(6),async {
        loop {
            if f.contexts.get_provider_transport(run).await==Some(bcs_service_api::ProviderRunTransport::Terminal) {break;}
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.unwrap();
}

#[tokio::test]
async fn polling_has_bounded_failures_and_cancels_the_actual_remote_task() {
    let mut f=fixture(Mode::HttpError(503)).await;polling(&mut f);
    f.transport.deliver(command(&f,"poll-failures").await).await.unwrap();
    wait_for_cleanup(&f,"poll-failures").await;
    assert_eq!(f.agent.queries.load(Ordering::SeqCst),3);
    assert_eq!(f.agent.requests.lock().unwrap().len(),1);
    assert_eq!(f.agent.cancels.lock().unwrap().as_slice(),["poll-task"]);
    assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),0);
    assert!(f.ingest.events.lock().unwrap().iter().any(|e|e.state==ChatEventState::Error));
}

#[tokio::test]
async fn polling_wait_states_fail_explicitly_and_release_tracking_after_cancel() {
    for state in ["TASK_STATE_INPUT_REQUIRED","TASK_STATE_AUTH_REQUIRED"] {
        let mut f=fixture(Mode::Terminal(state)).await;polling(&mut f);
        f.transport.deliver(command(&f,"poll-wait-state").await).await.unwrap();
        wait_for_cleanup(&f,"poll-wait-state").await;
        assert_eq!(f.agent.cancels.lock().unwrap().as_slice(),["poll-task"]);
        assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),0);
        assert!(!f.ingest.events.lock().unwrap().iter().any(|e|e.state==ChatEventState::Final));
        assert!(f.ingest.events.lock().unwrap().iter().any(|e|e.state==ChatEventState::Error));
    }
}

#[tokio::test]
async fn polling_abort_cancels_remote_task_and_does_not_invent_success_on_rejection() {
    for mode in [Mode::Wait,Mode::CancelDenied] {
        let mut f=fixture(mode).await;polling(&mut f);
        f.transport.deliver(command(&f,"poll-abort").await).await.unwrap();
        let result=f.transport.abort(BotAbortDeliveryCommand {command_id:"abort".into(),target:f.target.clone(),
            run_id:Some("poll-abort".into()),group_id:"group".into(),session_id:"session".into(),timeout_ms:1000,provider_bypass_headers:vec![]}).await;
        if matches!(mode,Mode::CancelDenied) {
            assert!(result.is_err());
            assert_eq!(f.runs.get("poll-abort").await.unwrap().unwrap().state,A2aRunState::Running);
        } else {
            assert!(result.is_ok());
            assert_eq!(f.runs.get("poll-abort").await.unwrap().unwrap().state,A2aRunState::Canceled);
        }
        assert_eq!(f.agent.cancels.lock().unwrap().as_slice(),["poll-task"]);
        assert_eq!(f.agent.subscriptions.load(Ordering::SeqCst),0);
    }
}

#[tokio::test]
async fn polling_does_not_negotiate_optional_tool_observations() {
    let mut f=fixture(Mode::Complete).await;polling(&mut f);
    if let BotDeliveryTarget::A2a {extensions,..}=&mut f.target {
        extensions.push(bcs_service_api::types::a2a_bot::A2aExtensionDeclaration {
            uri:bcs_a2a_http::tools::EXTENSION.into(),required:Some(false),params:Value::Null});
    }
    f.transport.deliver(command(&f,"poll-no-tools").await).await.unwrap();
    completed(&f,"poll-no-tools").await;
    assert!(f.runs.get("poll-no-tools").await.unwrap().unwrap().extensions.is_empty());
}
