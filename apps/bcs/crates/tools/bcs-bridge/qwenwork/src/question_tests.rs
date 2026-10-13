use super::*;
use bcs_protocol::stream::{InteractionKind, InteractionPhase};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt};

fn question(session: &str) -> Value {
    json!({"session_id":session,"request_set_id":"query-1","hook_event_name":"PreToolUse",
        "tool_use_id":"ask-1","tool_name":"AskUserQuestion","tool_input":{"questions":[
            {"header":"Color","question":"Which color?","options":[{"label":"Blue"},{"label":"Red"}],"multiSelect":false}]}})
}

async fn native(f: &Fixture, state: &str) -> tokio::task::JoinHandle<Vec<Value>> {
    let listener = tokio::net::UnixListener::bind(f.dir.path().join("qwen.sock")).unwrap();
    let state = state.to_owned();
    tokio::spawn(async move {
        let mut requests = Vec::new();
        let delayed = state == "delayed";
        for _ in 0..if delayed { 3 } else if state == "call" { 2 } else { 1 } {
            let (stream, _) = listener.accept().await.unwrap();
            let mut reader = tokio::io::BufReader::new(stream);
            let mut line = String::new();
            reader.read_line(&mut line).await.unwrap();
            let request: Value = serde_json::from_str(&line).unwrap();
            let tool = request["params"]["name"].as_str().unwrap();
            let data = if tool == "qwenwork_task_get_detail" {
                json!({"success":true,"status":"running","messages":[{"toolCalls":[
                    {"toolName":"AskUserQuestion","toolCallId":"ask-1","state":if delayed {"call"} else {&state}}]}]})
            } else {
                assert_eq!(tool, "qwenwork_task_submit_response");
                assert_eq!(request["params"]["arguments"]["answers"], json!({"Color":"Blue","Which color?":"Blue"}));
                if delayed && requests.len() == 1 {
                    json!({"success":false,"error":"No pending AskUserQuestion found for this task."})
                } else {
                    json!({"success":true,"respondedTo":"ask_user_question"})
                }
            };
            requests.push(request);
            reader.get_mut().write_all(format!("{}\n",json!({"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":data.to_string()}]}})).as_bytes()).await.unwrap();
        }
        requests
    })
}

async fn interaction(events: &mut mpsc::Receiver<StreamEvent>) -> String {
    loop {
        let event = tokio::time::timeout(Duration::from_secs(3), events.recv()).await.unwrap().unwrap();
        if let StreamEvent::Interaction(event) = event {
            assert_eq!(event.kind, InteractionKind::AskUser);
            assert_eq!(event.phase, InteractionPhase::Requested);
            assert_eq!(event.raw["questions"][0]["questionId"], "question_1");
            return event.interaction_id;
        }
    }
}

#[tokio::test]
async fn question_resolution_resumes_original_tool_and_run() {
    let f = Fixture::new();
    let native = native(&f, "call").await;
    let (mut socket, _) = f.connect("test-secret").await;
    let mut req = TurnRequest::new("ask-run", "hello", f.dir.path(), Arc::new(Observer::default()));
    let interactions = req.interactions.clone();
    req.engine_session_id = Some("question-conversation".into());
    let (tx, mut events) = mpsc::channel(32);
    let engine = f.engine.clone();
    let task = tokio::spawn(async move { engine.run_turn(req, tx, CancellationToken::new()).await });
    let request = frame(&mut socket).await;
    let request = request["headers"]["req_id"].as_str().unwrap().to_owned();
    f.correlate("question-conversation", "sdk-question");
    f.begin("sdk-question", "query-1", "hello").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let url = format!("http://{}/hooks", f.address);
    let hook = question("sdk-question");
    let http = tokio::spawn(async move { client.post(url).bearer_auth("test-hooks").json(&hook).send().await.unwrap().json::<Value>().await.unwrap() });
    let id = interaction(&mut events).await;
    assert!(!http.is_finished());
    let resolution = json!({"action":"submit","answers":{"question_1":{"values":["Blue"]}}});
    assert_eq!(interactions.resolve(&id,"answer-1",resolution.clone()), bcs_bridge_core::interaction::ResolveOutcome::Delivered);
    assert_eq!(interactions.resolve(&id,"answer-1",resolution), bcs_bridge_core::interaction::ResolveOutcome::Duplicate);
    assert_eq!(http.await.unwrap(), json!({"continue":true}));
    let calls = native.await.unwrap();
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[1]["params"]["arguments"]["chatId"], "sdk-question");
    assert!(!task.is_finished());
    assert_eq!(f.raw_hook(question("sdk-question"),"test-hooks").await.json::<Value>().await.unwrap(),json!({"continue":true}));
    let mut result = question("sdk-question");
    result["hook_event_name"] = json!("PostToolUse");
    result["tool_response"] = json!({"answers":{"Which color?":"Blue"}});
    assert!(f.raw_hook(result,"test-hooks").await.status().is_success());
    f.end("sdk-question","query-1").await;
    reply(&mut socket,&request,"Answer=Blue",true).await;
    let outcome = task.await.unwrap().unwrap();
    assert_eq!(outcome.engine_session_id.as_deref(),Some("question-conversation"));
    assert_eq!(outcome.final_text.as_deref(),Some("Answer=Blue"));
    let mut resolved = 0;
    while let Some(event) = events.recv().await {
        if let StreamEvent::Interaction(event) = event
            && event.phase == InteractionPhase::Resolved { resolved += 1; }
    }
    assert_eq!(resolved,1);
}

#[tokio::test]
async fn abort_while_waiting_for_answer_releases_hook_and_stops_task() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let abort = CancellationToken::new();
    let mut req = TurnRequest::new("ask-abort","hello",f.dir.path(),Arc::new(Observer::default()));
    let interactions = req.interactions.clone();
    req.engine_session_id = Some("question-conversation".into());
    let engine = f.engine.clone();
    let (tx,mut events) = mpsc::channel(32);
    let child = abort.clone();
    let task = tokio::spawn(async move {engine.run_turn(req,tx,child).await});
    frame(&mut socket).await;
    f.correlate("question-conversation","sdk-question");
    f.begin("sdk-question","query-1","hello").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let url = format!("http://{}/hooks",f.address);
    let http = tokio::spawn(async move {client.post(url).bearer_auth("test-hooks").json(&question("sdk-question")).send().await.unwrap().json::<Value>().await.unwrap()});
    let id = interaction(&mut events).await;
    abort.cancel();
    let response = http.await.unwrap();
    assert_eq!(response["hookSpecificOutput"]["permissionDecision"],"deny");
    let stop = frame(&mut socket).await;
    assert_eq!(stop["body"]["text"]["content"],"/stop");
    reply(&mut socket,stop["headers"]["req_id"].as_str().unwrap(),"✅ stopped",true).await;
    assert!(matches!(task.await.unwrap(),Err(TurnError::Aborted)));
    assert_eq!(interactions.resolve(&id,"late",json!({"action":"submit"})),bcs_bridge_core::interaction::ResolveOutcome::Duplicate);
}

#[tokio::test]
async fn cancel_question_denies_original_tool_without_sending_native_answer() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let mut req = TurnRequest::new("ask-cancel","hello",f.dir.path(),Arc::new(Observer::default()));
    let interactions = req.interactions.clone();
    req.engine_session_id = Some("question-conversation".into());
    let engine = f.engine.clone();
    let (tx,mut events) = mpsc::channel(32);
    let task = tokio::spawn(async move {engine.run_turn(req,tx,CancellationToken::new()).await});
    let request = frame(&mut socket).await;
    f.correlate("question-conversation","sdk-question");
    f.begin("sdk-question","query-1","hello").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let url = format!("http://{}/hooks",f.address);
    let http = tokio::spawn(async move {client.post(url).bearer_auth("test-hooks").json(&question("sdk-question")).send().await.unwrap().json::<Value>().await.unwrap()});
    let id = interaction(&mut events).await;
    interactions.resolve(&id,"cancel",json!({"action":"cancel"}));
    assert_eq!(http.await.unwrap()["hookSpecificOutput"]["permissionDecision"],"deny");
    let mut result = question("sdk-question");
    result["hook_event_name"] = json!("PostToolUseFailure");
    result["error"] = json!("User declined");
    f.raw_hook(result,"test-hooks").await;
    f.end("sdk-question","query-1").await;
    reply(&mut socket,request["headers"]["req_id"].as_str().unwrap(),"Question skipped",true).await;
    assert_eq!(task.await.unwrap().unwrap().final_text.as_deref(),Some("Question skipped"));
}

#[tokio::test]
async fn native_already_answered_question_never_receives_another_answer() {
    let f = Fixture::new();
    let native = native(&f,"output-available").await;
    let result = desktop_control::answer(&f.dir.path().join("qwen.sock"),"task","ask-1",&json!({"Color":"Blue"})).await;
    assert!(result.is_err());
    assert_eq!(native.await.unwrap().len(),1);
}

#[tokio::test]
async fn permission_registration_race_retries_only_definite_rejection_without_history_polling() {
    let f = Fixture::new();
    let native = native(&f,"delayed").await;
    desktop_control::answer(&f.dir.path().join("qwen.sock"),"task","ask-1",&json!({"Color":"Blue","Which color?":"Blue"})).await.unwrap();
    let requests = native.await.unwrap();
    assert_eq!(requests.iter().filter(|r| r["params"]["name"] == "qwenwork_task_get_detail").count(),1);
    assert_eq!(requests.iter().filter(|r| r["params"]["name"] == "qwenwork_task_submit_response").count(),2);
}

#[tokio::test]
async fn question_task_metadata_cannot_cross_conversations() {
    let f = Fixture::new();
    f.correlate("conversation-a","sdk-a");
    assert_eq!(session::task(&f.dir.path().join("qwen.db"),"sdk-a","test-bot","conversation-a").unwrap(),"sdk-a");
    assert!(session::task(&f.dir.path().join("qwen.db"),"sdk-a","test-bot","conversation-b").is_err());
}

/// Opt-in desktop validation; credentials and paths come only from a private local config.
#[tokio::test]
#[ignore = "requires a running QwenWork desktop and private bridge config"]
async fn live_desktop_question_roundtrip() {
    let path = std::env::var("QWENWORK_LIVE_CONFIG").expect("private config path");
    let config: toml::Value = toml::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let mut options = config["bot"][0]["engine_options"].as_table().unwrap().clone();
    options.insert("mcp_socket_path".into(),toml::Value::String(std::env::var("QWENWORK_LIVE_SOCKET").expect("desktop socket")));
    let engine = QwenWorkFactory.build(PathBuf::new(),&options).unwrap();
    let prompt = std::env::var("QWENWORK_LIVE_PROMPT").expect("explicit validation prompt");
    let req = TurnRequest::new("live-ask","hello",std::env::temp_dir(),Arc::new(Observer::default()));
    let mut req = req;
    req.prompt = prompt;
    let interactions = req.interactions.clone();
    let abort = CancellationToken::new();
    let child = abort.clone();
    let (tx,mut events) = mpsc::channel(128);
    let task = tokio::spawn(async move {engine.run_turn(req,tx,child).await});
    let watch = async {
        let mut questions = 0;
        let mut native_results = 0;
        while let Some(event) = events.recv().await {
            match event {
                StreamEvent::Interaction(event) if event.phase == InteractionPhase::Requested => {
                    let mut answers = serde_json::Map::new();
                    for q in event.raw["questions"].as_array().unwrap() {
                        answers.insert(q["questionId"].as_str().unwrap().into(),json!({"values":["ProbeBlue"]}));
                    }
                    interactions.resolve(&event.interaction_id,"live-answer",json!({"action":"submit","answers":answers}));
                    questions += 1;
                    println!("native question requested and resolved through bridge registry");
                }
                StreamEvent::Agent(event) => {
                    if let AgentData::Tool(tool) = event.data
                        && tool.name.as_deref() == Some("AskUserQuestion") && tool.phase == ToolPhase::Result {
                        let result = tool.result.unwrap_or(Value::Null);
                        if result.to_string().contains("ProbeBlue") { native_results += 1; }
                        println!("native AskUserQuestion result: {result}");
                    }
                }
                _ => {}
            }
        }
        (questions, native_results)
    };
    let result = tokio::time::timeout(Duration::from_secs(120),watch).await;
    abort.cancel();
    let outcome = task.await.unwrap();
    if let Err(error) = &outcome { println!("native turn error: {error}"); }
    if let Ok(outcome) = &outcome { println!("native turn result: {}",outcome.final_text.as_deref().unwrap_or_default()); }
    let (questions, native_results) = result.unwrap_or((0,0));
    assert!(questions>0,"desktop did not invoke AskUserQuestion");
    assert!(native_results>0,"native tool result did not contain the submitted answer");
    let outcome = outcome.unwrap();
    println!("native final: {}",outcome.final_text.as_deref().unwrap_or_default());
    assert!(outcome.final_text.unwrap_or_default().contains("ProbeBlue"));
}
