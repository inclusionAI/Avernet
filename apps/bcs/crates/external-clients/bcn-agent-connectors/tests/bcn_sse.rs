mod common;
use common::*;
use bcn_agent_connectors::bcn_sse::{ProviderConnector,ProviderConfig};
use bcn_runtime_api::port::AgentConnector;
use bcn_runtime_model::*;
use bcs_protocol::http::ProviderWebhookBotRef;
use futures::StreamExt;
use serde_json::json;
fn provider_binding()->FrozenTargetBinding {
    let mut b=binding();b.capabilities.query=false;b.capabilities.subscribe=false;b.capabilities.input_continuation=false;
    b.capabilities.cancellation=CancellationSupport::ExclusiveContext;b
}
fn connector(url:String)->ProviderConnector {
    ProviderConnector::new(provider_binding(),ProviderConfig{endpoint:url,bot:ProviderWebhookBotRef{provider_id:"provider".into(),provider_bot_ref:"bot-ref".into(),tags:vec![]},sender:None,group_id:"group".into(),timeout_ms:1000},http()).unwrap()
}
fn send()->AgentSendRequest {let mut r=request();r.binding=provider_binding();r}
fn frame(seq:u64,state:&str,text:&str)->String {
    format!("event: chat\r\ndata: {}\r\n\r\n",json!({"runId":"remote-run","seq":seq,"state":state,"message":{"content":[{"type":"text","text":text}]}}))
}
fn parts(text:&str)->Vec<MessagePart>{vec![MessagePart::Text{text:text.into()}]}
#[tokio::test]
async fn provider_fragments_heartbeats_duplicates_and_final_text() {
    for (final_text,expected) in [("","你好"),("replacement","replacement")] {
        let body=format!(": heartbeat\r\n\r\nevent: ping\r\ndata: {{\r\ndata: \"ts\":1}}\r\n\r\n{}{}{}",frame(1,"delta","你好"),frame(1,"delta","duplicate"),frame(2,"final",final_text));
        let f=fixture(body,"text/event-stream",false).await;
        let c=connector(f.url.clone());let events=c.send_streaming_message(send()).await.unwrap().collect::<Vec<_>>().await;
        assert!(matches!(events.last(),Some(Ok(AgentEvent::Completed{output})) if output==&parts(expected)),"{events:?}");
        assert_eq!(events.iter().filter(|e|matches!(e,Ok(AgentEvent::Delta{..}))).count(),1);
        assert!(events.iter().any(|e|matches!(e,Ok(AgentEvent::Accepted{handle}) if handle.remote_task_id.as_deref()==Some("remote-run"))));
        let calls=f.calls.lock().unwrap();assert_eq!(calls.len(),1);
        assert_eq!(calls[0].0["x-bcn-protocol-version"],"2.0"); assert_eq!(calls[0].0["x-bcn-transport"],"sse");
        assert_eq!(calls[0].0["accept"],"text/event-stream");assert_eq!(calls[0].0["authorization"],"Bearer fixture");
        assert_eq!(calls[0].0["x-bcn-message-id"].to_str().unwrap(),calls[0].1["id"].as_str().unwrap());
        assert_eq!(calls[0].1["method"],"chat.send"); assert_eq!(calls[0].1["to_bot"]["provider_bot_ref"],"bot-ref");
        assert_eq!(calls[0].1["message"],"hello");
    }
}
#[tokio::test]
async fn ack_and_eof_are_uncertain_without_second_post() {
    let f=fixture("{\"ok\":true}".into(),"application/json",false).await;
    assert!(matches!(connector(f.url.clone()).send_streaming_message(send()).await,Err(ConnectorError::MayHaveBeenAccepted(_))));
    assert_eq!(f.calls.lock().unwrap().len(),1);
    let f=fixture(frame(1,"delta","partial"),"text/event-stream",false).await;
    let events=connector(f.url.clone()).send_streaming_message(send()).await.unwrap().collect::<Vec<_>>().await;
    assert!(matches!(events.last(),Some(Ok(AgentEvent::ObservationFailed{output,..})) if output==&parts("partial")));
    assert_eq!(f.calls.lock().unwrap().len(),1);
}
#[tokio::test]
async fn unsupported_queries_and_nonexclusive_abort_do_no_io() {
    let f=fixture("{}".into(),"application/json",false).await;let c=connector(f.url.clone());
    assert!(matches!(c.get_task(handle()).await,Err(ConnectorError::Unsupported(_))));
    assert!(matches!(c.subscribe_to_task(handle()).await,Err(ConnectorError::Unsupported(_))));
    assert!(matches!(c.cancel_task(AgentCancelRequest{operation_id:OperationId::new(scope(),"cancel"),handle:handle(),exclusive_context:false}).await,Err(ConnectorError::NotSent(_))));
    assert!(f.calls.lock().unwrap().is_empty());
}
#[tokio::test]
async fn abort_requires_trusted_matching_run_evidence() {
    for (runs,confirmed) in [(json!([]),false),(json!(["other"]),false),(json!(["task"]),true)] {
        let f=fixture(json!({"ok":true,"aborted":true,"aborted_run_ids":runs}).to_string(),"application/json",false).await;
        let outcome=connector(f.url.clone()).cancel_task(AgentCancelRequest{operation_id:OperationId::new(scope(),"cancel"),handle:handle(),exclusive_context:true}).await.unwrap();
        assert_eq!(outcome,if confirmed{CancelOutcome::Confirmed{affected_remote_task_ids:vec!["task".into()]}}else{CancelOutcome::Unknown});
        assert_eq!(f.calls.lock().unwrap()[0].1["method"],"chat.abort");
    }
}

#[tokio::test]
async fn context_reuse_starts_new_run_despite_no_input_continuation() {
    let f=fixture(frame(1,"final","done"),"text/event-stream",false).await;let c=connector(f.url.clone());
    let mut r=send();let mut h=handle();h.remote_task_id=None;r.handle=Some(h);r.expires_at_ms=Some(12345);
    let events=c.send_streaming_message(r).await.unwrap().collect::<Vec<_>>().await;
    assert!(matches!(events.first(),Some(Ok(AgentEvent::Accepted{handle:h})) if h.remote_task_id.as_deref()==Some("remote-run") && h.remote_context_id.as_deref()==Some("context")));
    assert_eq!(f.calls.lock().unwrap()[0].1["session_id"],"context");
    let mut r=send();r.handle=Some(handle());assert!(matches!(c.send_streaming_message(r).await,Err(ConnectorError::Unsupported(_))));
    assert_eq!(f.calls.lock().unwrap().len(),1);
}
#[tokio::test]
async fn malformed_content_preserves_live_or_terminal_execution_evidence() {
    for state in ["delta","final","error","aborted"] {
        let body=format!("{}event: chat\ndata: {}\n\n",frame(1,"delta","partial"),json!({"runId":"remote-run","seq":2,"state":state,"message":{"content":[{"type":"image","url":"unsupported"}]}}));
        let f=fixture(body,"text/event-stream",false).await;
        let events=connector(f.url.clone()).send_streaming_message(send()).await.unwrap().collect::<Vec<_>>().await;
        assert!(matches!(events.first(),Some(Ok(AgentEvent::Accepted{handle})) if handle.remote_task_id.as_deref()==Some("remote-run")));
        if state=="delta" {
            assert!(matches!(events.last(),Some(Ok(AgentEvent::ObservationFailed{output,..})) if output==&parts("partial")),"{events:?}");
        } else {
            assert!(matches!(events.last(),Some(Ok(AgentEvent::FailedWithOutput{error:RuntimeError::Unsupported(_),output})) if output==&parts("partial")),"{events:?}");
        }
        assert_eq!(f.calls.lock().unwrap().len(),1);
    }
}

#[tokio::test]
async fn live_stream_bounds_gaps_and_identity_mismatch_preserve_partial_output() {
    let cases=[
        frame(3,"delta","gap"),
        frame(2,"delta","x").replace("remote-run","other-run"),
        "event: chat\ndata: {broken\n\n".into(),
        "event: chat\ndata: {\"runId\":\"remote-run\"".into(),
        frame(2,"delta",&"x".repeat(65536)),
        format!("data: {}", "x".repeat(256*1024)),
    ];
    for tail in cases {
        let f=fixture(format!("{}{tail}",frame(1,"delta","partial")),"text/event-stream",false).await;
        let events=connector(f.url.clone()).send_streaming_message(send()).await.unwrap().collect::<Vec<_>>().await;
        assert!(matches!(events.last(),Some(Ok(AgentEvent::ObservationFailed{output,..})) if output==&parts("partial")),"{events:?}");
        assert_eq!(f.calls.lock().unwrap().len(),1);
    }
}
#[tokio::test]
async fn stable_dispatch_identity_is_scoped_and_capabilities_match_methods() {
    let f=fixture(frame(1,"final","done"),"text/event-stream",false).await;let c=connector(f.url.clone());
    let r=send();let expected=serde_json::to_string(&r.dispatch_id).unwrap();
    assert!(matches!(c.send_message(r.clone()).await,Err(ConnectorError::Unsupported(_))));
    c.send_streaming_message(r).await.unwrap().collect::<Vec<_>>().await;
    let calls=f.calls.lock().unwrap();assert_eq!(calls.len(),1);assert_eq!(calls[0].1["id"],expected);
    assert!(calls[0].1["session_id"].as_str().unwrap().contains("runtime"));
    assert_eq!(c.capabilities(),provider_binding().capabilities);
}
#[tokio::test]
async fn terminal_overflow_retains_termination_evidence() {
    let f=fixture(format!("{}{}",frame(1,"delta","partial"),frame(2,"final",&"x".repeat(65536))),"text/event-stream",false).await;
    let events=connector(f.url.clone()).send_streaming_message(send()).await.unwrap().collect::<Vec<_>>().await;
    assert!(matches!(events.last(),Some(Ok(AgentEvent::FailedWithOutput{error:RuntimeError::CapacityExceeded,output})) if output==&parts("partial")));
}

#[tokio::test]
async fn bare_cr_final_frame_at_eof_is_a_complete_terminal() {
    let f=fixture(frame(1,"final","done").replace("\r\n","\r"),"text/event-stream",false).await;
    let events=connector(f.url.clone()).send_streaming_message(send()).await.unwrap().collect::<Vec<_>>().await;
    assert!(matches!(events.last(),Some(Ok(AgentEvent::Completed{output})) if output==&parts("done")),"{events:?}");
}
