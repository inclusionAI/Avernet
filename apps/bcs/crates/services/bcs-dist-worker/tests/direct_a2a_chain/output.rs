use super::*;
use bcs_service_api::{
    core::dist_connector::{DistConnectorCoreService, Report, ReportCursor},
    port::{dist_output::*, repo::distributed::{dispatch::DispatchControlPort, inbox::InboxProducerPort, result::BlobStorePort, task_feed::*}},
};

async fn setup() -> (Fixture, Router, Tid) {
    let f = Fixture::with_shards(Arc::new(RunnerRegistry::new(DirectA2aRunner)), 64, 128, 256, vec![ShardId(0)]).await;
    let app = gateway(&f, Arc::new(Directory(AtomicBool::new(true))));
    let receipt = send(&app, "output", None, None).await;
    let tid = Tid(receipt["result"]["task"]["id"].as_str().unwrap().into());
    f.warm().await;
    wait_for(&app, &f, &tid.0, "TASK_STATE_WORKING").await;
    (f, app, tid)
}

async fn report(f: &Fixture, tid: &Tid, seq: u64, kind: AgentEventKind, value: Value) {
    let dispatch = f.dispatch.get(&Did(format!("{}.agent.1", tid.0))).await.unwrap().unwrap();
    let mut report = DistConnectorRules.report(&dispatch.view,
        &mut ReportCursor { holder_epoch: HolderEpoch(1), last_seq: InputSeq(seq - 1) },
        kind, None, Payload::Inline(value));
    let payload = match &mut report {
        Report::Stream(input) => &mut input.event.payload,
        Report::Key(input) => {
            let InputBody::Agent { event } = &mut input.input.body else { panic!() };
            &mut event.payload
        }
    };
    let Payload::Inline(value) = payload else { panic!() };
    let bytes = serde_json::to_vec(value).unwrap();
    if bytes.len() > 65_536 {
        let reference = bcs_dist_encoding::blob_reference(&format!("review/{}/{seq}", tid.0), &bytes);
        f.archive.put_immutable(reference.clone(), bytes).await.unwrap();
        *payload = Payload::Ref(reference);
    }
    match report {
        Report::Key(input) => { f.inbox.admit_connector(input).await.unwrap(); }
        Report::Stream(input) => { f.inbox.append_stream(input).await.unwrap(); }
    }
}

async fn terminal(f: &Fixture, app: &Router, tid: &Tid) -> Value {
    let mut last = Value::Null;
    for _ in 0..3000 {
        f.step().await.unwrap();
        let result = rpc(app, "GetTask", json!({"id":tid.0})).await;
        let state = result["result"]["status"]["state"].as_str().unwrap();
        if ["TASK_STATE_COMPLETED", "TASK_STATE_FAILED"].contains(&state) { return result["result"].clone(); }
        last = result;
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    panic!("task did not finish: {last}");
}

#[tokio::test(start_paused = true)]
async fn mixed_output_survives_blob_storage_follow_and_final_restore() {
    let (f, app, tid) = setup().await;
    let scope = TaskScope { tenant: TenantId("t".into()), principal: PrincipalId("bot:caller".into()) };
    let reader = WorkerOutputReader::new(f.feed.clone(), f.archive.clone(), Arc::new(bcs_dist_a2a_output::A2aOutputAdapter));
    let Some(RestoredTask::Live { cursor, .. }) = reader.restore(&scope, &tid).await.unwrap() else { panic!() };
    let parts = vec![json!({"text":"before"}), json!({"data":{"large":"x".repeat(70_000)}}),
        json!({"url":"https://example.com/report.pdf", "mediaType":"application/pdf", "metadata":{"label":"report"}}),
        json!({"text":"after"})];
    for (index, part) in parts.iter().enumerate() {
        report(&f, &tid, index as u64 + 1, AgentEventKind::Delta,
            json!({"artifact":{"artifactId":"answer", "parts":[part]},"append":index != 0})).await;
    }
    report(&f, &tid, 5, AgentEventKind::Final, json!({"state":"completed","message":{"parts":[{"text":"Done"}]}})).await;
    let final_task = terminal(&f, &app, &tid).await;
    assert_eq!(final_task["status"]["state"], "TASK_STATE_COMPLETED");
    assert_eq!(final_task["artifacts"][0]["parts"], json!(parts));
    let mut follow = reader.follow(scope, cursor);
    let mut observed = vec![];
    while observed.len() < parts.len() {
        let item = tokio::time::timeout(Duration::from_secs(5), follow.next()).await.unwrap().unwrap().unwrap();
        let FormattedFollowItem::Events { events, .. } = item else { panic!("unexpected gap") };
        for event in events {
            if let FormattedOutputBody::Artifact { content } = event.body {
                observed.extend(content["artifact"]["parts"].as_array().unwrap().iter().cloned());
            }
        }
    }
    assert_eq!(observed, parts);
}

#[tokio::test(start_paused = true)]
async fn final_artifact_action_limit_counts_the_finish_action() {
    for (count, expected) in [(31, "TASK_STATE_COMPLETED"), (32, "TASK_STATE_FAILED")] {
        let (f, app, tid) = setup().await;
        let artifacts: Vec<_> = (0..count).map(|i| json!({"artifactId":i.to_string(),"parts":[{"text":"result"}]})).collect();
        report(&f, &tid, 1, AgentEventKind::Final, json!({"state":"completed", "artifacts":artifacts})).await;
        let result = terminal(&f, &app, &tid).await;
        assert_eq!(result["status"]["state"], expected);
        assert!(f.dispatch.get(&Did(format!("{}.agent.1", tid.0))).await.unwrap().unwrap().view.control.cancel.is_none(),
            "a terminal dispatch must not receive an extra cancel on quota failure");
        assert_eq!(result["artifacts"].as_array().map_or(0, Vec::len), if count == 31 {31} else {0});
    }
}

#[tokio::test(start_paused = true)]
async fn tool_units_share_the_artifact_limit_without_silent_truncation() {
    for (tools, expected) in [(127, "TASK_STATE_COMPLETED"), (128, "TASK_STATE_FAILED")] {
        let (f, app, tid) = setup().await;
        report(&f, &tid, 1, AgentEventKind::Delta, json!({"artifact":{"artifactId":"answer","parts":[{"text":"keep"}]},"append":false})).await;
        for index in 0..tools {
            report(&f, &tid, index + 2, AgentEventKind::Tool, json!({"tool":{"toolCallId":index.to_string(),"phase":"end"}})).await;
            for _ in 0..12 { f.step().await.unwrap(); tokio::time::sleep(Duration::from_millis(5)).await; }
        }
        report(&f, &tid, tools + 2, AgentEventKind::Final, json!({"state":"completed"})).await;
        let result = terminal(&f, &app, &tid).await;
        assert_eq!(result["status"]["state"], expected);
        assert_eq!(result["artifacts"].as_array().unwrap().len(), 128);
        assert_eq!(result["artifacts"][0]["parts"][0]["text"], "keep");
    }
}
