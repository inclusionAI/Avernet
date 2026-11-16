mod common;
use common::*;
use bcn_agent_connectors::a2a::A2aConnector;
use bcn_runtime_api::port::AgentConnector;
use bcn_runtime_model::*;
use serde_json::json;

#[tokio::test]
async fn direct_message_preserves_server_context_and_selected_transport() {
    for protocol in ["HTTP+JSON", "JSONRPC"] {
        let f=fixture(json!({"message":{"messageId":"reply","role":"ROLE_AGENT","contextId":"server-context","parts":[{"text":"answer"}]}}).to_string(),"application/json",protocol=="JSONRPC").await;
        let connector=A2aConnector::new(binding(),card(f.url.clone(),protocol),http()).unwrap();
        let response=connector.send_message(request()).await.unwrap();
        let AgentSendResponse::Message {parts,handle:Some(handle),observation_error:None}=response else {panic!("expected context-bearing message")};
        assert_eq!(parts,vec![MessagePart::Text{text:"answer".into()}]);
        assert_eq!(handle.remote_context_id.as_deref(),Some("server-context"));
        assert_eq!(handle.remote_task_id,None);
        let calls=f.calls.lock().unwrap(); assert_eq!(calls.len(),1);
        assert_eq!(calls[0].0["authorization"],"Bearer fixture");
        let wire=if protocol=="JSONRPC" {&calls[0].1["params"]} else {&calls[0].1};
        assert!(wire["message"].get("contextId").is_none());
        assert_eq!(wire["message"]["parts"][0]["text"],"hello");
    }
}

#[tokio::test]
async fn task_states_and_bound_identity_survive_projection() {
    for (state,status) in [("TASK_STATE_INPUT_REQUIRED",TaskStatus::InputRequired),("TASK_STATE_AUTH_REQUIRED",TaskStatus::AuthRequired),("TASK_STATE_COMPLETED",TaskStatus::Completed)] {
        let f=fixture(json!({"task":{"id":"task","contextId":"context","status":{"state":state},
            "artifacts":[{"artifactId":"a","parts":[{"text":"output"}]}]}}).to_string(),"application/json",false).await;
        let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
        let AgentSendResponse::Task{snapshot}=c.send_message(request()).await.unwrap() else {panic!("expected task")};
        assert_eq!(snapshot.handle,handle()); assert_eq!(snapshot.status,status);
        assert_eq!(snapshot.output,vec![MessagePart::Text{text:"output".into()}]);
    }
}

#[tokio::test]
async fn rejects_wrong_binding_and_unsupported_capabilities_before_io() {
    let f=fixture("{}".into(),"application/json",false).await;
    let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
    let mut req=request(); req.binding.binding_id="different".into();
    assert!(matches!(c.send_message(req).await,Err(ConnectorError::NotSent(_))));
    let mut h=handle(); h.target.scope.tenant="other".into();
    assert!(matches!(c.get_task(h).await,Err(ConnectorError::NotSent(_))));
    let mut b=binding(); b.capabilities.cancellation=CancellationSupport::ExclusiveContext;
    assert!(A2aConnector::new(b,card(f.url.clone(),"HTTP+JSON"),http()).is_err());
    assert!(f.calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn query_and_cancel_require_exact_remote_identity() {
    let f=fixture(json!({"id":"task","contextId":"context","status":{"state":"TASK_STATE_CANCELED"}}).to_string(),"application/json",true).await;
    let c=A2aConnector::new(binding(),card(f.url.clone(),"JSONRPC"),http()).unwrap();
    let snapshot=c.get_task(handle()).await.unwrap(); assert_eq!(snapshot.status,TaskStatus::Cancelled);
    assert_eq!(c.cancel_task(AgentCancelRequest{operation_id:OperationId::new(scope(),"cancel"),handle:handle(),exclusive_context:false}).await.unwrap(),
        CancelOutcome::Confirmed{affected_remote_task_ids:vec!["task".into()]});
    let calls=f.calls.lock().unwrap(); assert_eq!(calls.len(),2);
    assert_eq!(calls[0].1["method"],"GetTask"); assert_eq!(calls[1].1["method"],"CancelTask");
    assert_eq!(calls[0].1["params"]["id"],"task");
}

#[tokio::test]
async fn streaming_terminal_and_premature_eof_retain_output_and_identity() {
    use futures::StreamExt;
    for complete in [false,true] {
        let mut body=format!("data: {}\n\n",json!({"task":{"id":"task","contextId":"context","status":{"state":"TASK_STATE_WORKING"},"artifacts":[{"artifactId":"a","parts":[{"text":"partial"}]}]}}));
        if complete {body.push_str(&format!("data: {}\n\n",json!({"statusUpdate":{"taskId":"task","contextId":"context","status":{"state":"TASK_STATE_COMPLETED"}}})));}
        let f=fixture(body,"text/event-stream",false).await;
        let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
        let events=c.send_streaming_message(request()).await.unwrap().collect::<Vec<_>>().await;
        assert!(events.iter().any(|e|matches!(e,Ok(AgentEvent::Accepted{handle:h}) if h==&handle())));
        if complete {assert!(matches!(events.last(),Some(Ok(AgentEvent::Completed{output})) if output==&vec![MessagePart::Text{text:"partial".into()}]));}
        else {assert!(matches!(events.last(),Some(Err(ConnectorError::MayHaveBeenAccepted(_))))); assert!(events.iter().any(|e|matches!(e,Ok(AgentEvent::Delta{text}) if text=="partial")));}
        assert_eq!(f.calls.lock().unwrap().len(),1);
    }
}

#[tokio::test]
async fn unspecified_initial_status_preserves_handle_before_observation_failure() {
    use futures::StreamExt;
    let f=fixture(format!("data: {}\n\n",json!({"task":{"id":"task","contextId":"context","status":{"state":"TASK_STATE_UNSPECIFIED"}}})),"text/event-stream",false).await;
    let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
    let events=c.send_streaming_message(request()).await.unwrap().collect::<Vec<_>>().await;
    assert!(matches!(events.first(),Some(Ok(AgentEvent::Accepted{handle:h})) if h==&handle()));
    assert!(matches!(events.last(),Some(Ok(AgentEvent::ObservationFailed{..}))));
}

#[tokio::test]
async fn context_reuse_is_distinct_from_input_continuation_and_local_deadline() {
    let f=fixture(json!({"message":{"messageId":"reply","role":"ROLE_AGENT","contextId":"context","parts":[{"text":"second"}]}}).to_string(),"application/json",false).await;
    let mut b=binding();b.capabilities.input_continuation=false;
    let c=A2aConnector::new(b.clone(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
    let mut r=request();r.binding=b;r.expires_at_ms=Some(12345);let mut h=handle();h.remote_task_id=None;r.handle=Some(h);
    assert!(matches!(c.send_message(r).await,Ok(AgentSendResponse::Message{..})));
    let calls=f.calls.lock().unwrap();assert_eq!(calls.len(),1);
    assert_eq!(calls[0].1["message"]["contextId"],"context");assert!(calls[0].1["message"].get("taskId").is_none());
}

#[tokio::test]
async fn known_handle_subscription_maps_waiting_states_without_resend() {
    use futures::StreamExt;
    for state in ["TASK_STATE_INPUT_REQUIRED","TASK_STATE_AUTH_REQUIRED"] {
        let f=fixture(format!("data: {}\n\n",json!({"task":{"id":"task","contextId":"context","status":{"state":state,"message":{"messageId":"prompt","role":"ROLE_AGENT","parts":[{"text":"approve?"}]}}}})),"text/event-stream",false).await;
        let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
        let events=c.subscribe_to_task(handle()).await.unwrap().collect::<Vec<_>>().await;
        if state=="TASK_STATE_INPUT_REQUIRED" {assert!(matches!(events.last(),Some(Ok(AgentEvent::InputRequired{handle:h,prompt})) if h==&handle() && prompt==&vec![MessagePart::Text{text:"approve?".into()}]));}
        else {assert!(matches!(events.last(),Some(Ok(AgentEvent::AuthRequired{handle:h})) if h==&handle()));}
        let calls=f.calls.lock().unwrap();assert_eq!(calls.len(),1);assert_eq!(calls[0].1["id"],"task");assert!(calls[0].1.get("message").is_none());
    }
}

#[tokio::test]
async fn unsupported_live_artifact_preserves_new_handle_for_recovery() {
    use futures::StreamExt;
    let f=fixture(format!("data: {}\n\n",json!({"task":{"id":"task","contextId":"context","status":{"state":"TASK_STATE_WORKING"},"artifacts":[{"artifactId":"a","parts":[{"data":{"unknown":true}}]}]}})),"text/event-stream",false).await;
    let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
    let events=c.send_streaming_message(request()).await.unwrap().collect::<Vec<_>>().await;
    assert!(matches!(events.first(),Some(Ok(AgentEvent::Accepted{handle:h})) if h==&handle()));
    assert!(matches!(events.last(),Some(Ok(AgentEvent::ObservationFailed{..}))));
}

#[tokio::test]
async fn oversized_stream_output_distinguishes_live_loss_from_terminal_evidence() {
    use futures::StreamExt;
    for terminal in [false,true] {
        let state=if terminal{"TASK_STATE_COMPLETED"}else{"TASK_STATE_WORKING"};
        let f=fixture(format!("data: {}\n\n",json!({"task":{"id":"task","contextId":"context","status":{"state":state},"artifacts":[{"artifactId":"a","parts":[{"text":"x".repeat(65536)}]}]}})),"text/event-stream",false).await;
        let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
        let events=c.send_streaming_message(request()).await.unwrap().collect::<Vec<_>>().await;
        assert!(matches!(events.first(),Some(Ok(AgentEvent::Accepted{..}))));
        if terminal {assert!(matches!(events.last(),Some(Ok(AgentEvent::FailedWithOutput{error:RuntimeError::CapacityExceeded,..}))));}
        else {assert!(matches!(events.last(),Some(Ok(AgentEvent::ObservationFailed{error:RuntimeError::CapacityExceeded,..}))));}
    }
}

#[tokio::test]
async fn unary_projection_failure_preserves_task_identity_and_remote_terminal_state() {
    for terminal in [false,true] {for oversized in [false,true] {
        let state=if terminal{"TASK_STATE_COMPLETED"}else{"TASK_STATE_WORKING"};
        let part=if oversized{json!({"text":"x".repeat(65536)})}else{json!({"data":{"unsupported":true}})};
        let f=fixture(json!({"task":{"id":"task","contextId":"context","status":{"state":state},"artifacts":[{"artifactId":"a","parts":[part]}]}}).to_string(),"application/json",false).await;
        let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
        let AgentSendResponse::Task{snapshot}=c.send_message(request()).await.unwrap() else{panic!("task")};
        assert_eq!(snapshot.handle,handle());assert_eq!(snapshot.status,if terminal{TaskStatus::Completed}else{TaskStatus::Working});
        assert!(snapshot.observation_error.is_some());assert!(snapshot.error.is_none());assert!(snapshot.output.is_empty());
    }}
}
#[tokio::test]
async fn nontext_input_and_missing_text_card_modes_are_rejected_before_io() {
    let f=fixture("{}".into(),"application/json",false).await;
    let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
    for part in [MessagePart::Data{value:json!({"x":1})},MessagePart::File{uri:"fixture:file".into(),media_type:"image/png".into()}] {
        let mut r=request();r.message.parts=vec![part];assert!(matches!(c.send_message(r).await,Err(ConnectorError::Unsupported(_))));
    }
    let mut selected=card(f.url.clone(),"HTTP+JSON");selected.raw["defaultOutputModes"]=json!(["application/json"]);
    assert!(A2aConnector::new(binding(),selected,http()).is_err());assert!(f.calls.lock().unwrap().is_empty());
}

#[tokio::test]
async fn nontext_direct_message_preserves_context_and_termination_with_observation_error() {
    for context in [None,Some("server-context")] {
        let mut message=json!({"messageId":"reply","role":"ROLE_AGENT","parts":[{"data":{"unsupported":true}}]});
        if let Some(context)=context {message["contextId"]=json!(context);}
        let f=fixture(json!({"message":message}).to_string(),"application/json",false).await;
        let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
        let response=serde_json::to_value(c.send_message(request()).await.unwrap()).unwrap();
        assert_eq!(response["kind"],"message");assert!(response.get("observation_error").is_some());
        if let Some(context)=context {assert_eq!(response["handle"]["remote_context_id"],context);assert!(response["handle"]["remote_task_id"].is_null());}
        else {assert!(response.get("handle").is_none());}
        assert_eq!(f.calls.lock().unwrap().len(),1);
    }
}

#[tokio::test]
async fn stream_nontext_terminal_task_and_message_keep_termination_evidence() {
    use futures::StreamExt;
    for event in [json!({"task":{"id":"task","contextId":"context","status":{"state":"TASK_STATE_COMPLETED"},"artifacts":[{"artifactId":"a","parts":[{"data":{}}]}]}}),
        json!({"message":{"messageId":"reply","role":"ROLE_AGENT","contextId":"context","parts":[{"data":{}}]}}),
        json!({"message":{"messageId":"reply","role":"ROLE_AGENT","parts":[{"data":{}}]}})] {
        let f=fixture(format!("data: {event}\n\n"),"text/event-stream",false).await;
        let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
        let events=c.send_streaming_message(request()).await.unwrap().collect::<Vec<_>>().await;
        assert!(matches!(events.last(),Some(Ok(AgentEvent::FailedWithOutput{..}))),"{events:?}");
    }
}

#[tokio::test]
async fn cumulative_empty_artifact_budget_stops_observation_before_terminal() {
    use futures::StreamExt;
    let mut wire=format!("data: {}\n\n",json!({"task":{"id":"task","contextId":"context","status":{"state":"TASK_STATE_WORKING"},"artifacts":[{"artifactId":"initial","parts":[{"text":"partial"}]}]}}));
    // Boundary fixture, not a throughput test: empty artifacts still retain IDs
    // in the existing projection, so text size alone cannot bound observation.
    for n in 0..1024 {
        wire.push_str(&format!("data: {}\n\n",json!({"artifactUpdate":{"taskId":"task","contextId":"context","artifact":{"artifactId":format!("{n}{}","x".repeat(8192)),"parts":[{"text":""}]},"append":false}})));
    }
    wire.push_str(&format!("data: {}\n\n",json!({"statusUpdate":{"taskId":"task","contextId":"context","status":{"state":"TASK_STATE_COMPLETED"}}})));
    let f=fixture(wire,"text/event-stream",false).await;
    let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
    let events=c.send_streaming_message(request()).await.unwrap().collect::<Vec<_>>().await;
    assert!(matches!(events.first(),Some(Ok(AgentEvent::Accepted{handle:h})) if h==&handle()));
    assert!(matches!(events.last(),Some(Ok(AgentEvent::ObservationFailed{output,..})) if output==&vec![MessagePart::Text{text:"partial".into()}]));
    assert_eq!(f.calls.lock().unwrap().len(),1);
}

#[tokio::test]
async fn status_message_projection_failure_preserves_validated_execution_evidence() {
    use futures::StreamExt;
    for initial in [false,true] {for state in ["TASK_STATE_COMPLETED","TASK_STATE_FAILED","TASK_STATE_CANCELED","TASK_STATE_WORKING"] {for part in [json!({"data":{"unsupported":true}}),json!({"url":"fixture:file","mediaType":"image/png"})] {
        let status=json!({"state":state,"message":{"messageId":"status","role":"ROLE_AGENT","parts":[part]}});
        let body=if initial {
            format!("data: {}\n\n",json!({"task":{"id":"task","contextId":"context","status":status,"artifacts":[{"artifactId":"a","parts":[{"text":"partial"}]}]}}))
        } else {
            format!("data: {}\n\ndata: {}\n\n",json!({"task":{"id":"task","contextId":"context","status":{"state":"TASK_STATE_WORKING"},"artifacts":[{"artifactId":"a","parts":[{"text":"partial"}]}]}}),
                json!({"statusUpdate":{"taskId":"task","contextId":"context","status":status}}))
        };
        let f=fixture(body,"text/event-stream",false).await;
        let c=A2aConnector::new(binding(),card(f.url.clone(),"HTTP+JSON"),http()).unwrap();
        let events=c.send_streaming_message(request()).await.unwrap().collect::<Vec<_>>().await;
        assert!(matches!(events.first(),Some(Ok(AgentEvent::Accepted{handle:h})) if h==&handle()));
        let expected=vec![MessagePart::Text{text:"partial".into()}];
        if state=="TASK_STATE_WORKING" {
            assert!(matches!(events.last(),Some(Ok(AgentEvent::ObservationFailed{error:RuntimeError::Unsupported(_),output})) if output==&expected),"{events:?}");
        } else {
            assert!(matches!(events.last(),Some(Ok(AgentEvent::FailedWithOutput{error:RuntimeError::Unsupported(_),output})) if output==&expected),"{events:?}");
        }
        assert_eq!(f.calls.lock().unwrap().len(),1);
    }}}
}

#[tokio::test]
async fn unary_and_query_input_prompt_is_separate_from_artifacts() {
    for query in [false,true] {for artifacts in [false,true] {for content in ["text","absent","unsupported","file","oversized"] {
        let mut task=json!({"id":"task","contextId":"context","status":{"state":"TASK_STATE_INPUT_REQUIRED"}});
        if artifacts {task["artifacts"]=json!([{"artifactId":"a","parts":[{"text":"unrelated partial"}]}]);}
        if content!="absent" {
            let part=match content {"unsupported"=>json!({"data":{}}),"file"=>json!({"url":"fixture:file","mediaType":"image/png"}),"oversized"=>json!({"text":"x".repeat(65536)}),_=>json!({"text":"Which account should I use?"})};
            task["status"]["message"]=json!({"messageId":"question","role":"ROLE_AGENT","parts":[part]});
        }
        let body=if query{task}else{json!({"task":task})};
        let f=fixture(body.to_string(),"application/json",query).await;
        let c=A2aConnector::new(binding(),card(f.url.clone(),if query{"JSONRPC"}else{"HTTP+JSON"}),http()).unwrap();
        let snapshot=if query{c.get_task(handle()).await.unwrap()}else{
            let AgentSendResponse::Task{snapshot}=c.send_message(request()).await.unwrap() else{panic!("task")};snapshot
        };
        assert_eq!(snapshot.handle,handle());assert_eq!(snapshot.status,TaskStatus::InputRequired);
        assert_eq!(snapshot.output,if artifacts{vec![MessagePart::Text{text:"unrelated partial".into()}]}else{vec![]});
        let value=serde_json::to_value(&snapshot).unwrap();
        assert_eq!(value.get("input_prompt"),Some(&if content=="text"{json!([{"kind":"text","text":"Which account should I use?"}])}else{json!([])}));
        assert_eq!(snapshot.observation_error.is_some(),matches!(content,"unsupported"|"file"|"oversized"));
        assert_eq!(f.calls.lock().unwrap().len(),1);
    }}}
}
