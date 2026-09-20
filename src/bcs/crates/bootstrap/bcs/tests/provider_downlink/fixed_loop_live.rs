//! FL-25: real HTTP routes, Provider callbacks, Event webhooks and IM delivery.
//! No runtime/store methods are called to advance or inspect these stories.

use super::*;
use std::collections::{BTreeMap, VecDeque};

#[path = "loop_channel.rs"]
mod channel;

const HUMAN: &str = "11111111";

struct LiveStory {
    client: reqwest::Client,
    addr: SocketAddr,
    provider: ProviderServer,
    bot: RegisteredProviderBot,
    _driver: MockBot,
    _directory: tempfile::TempDir,
    server: tokio::task::JoinHandle<Result<(), bcs::BcsError>>,
    receiver: tokio::task::JoinHandle<()>,
    events: Arc<Mutex<Vec<Value>>>,
    notifications: Arc<Mutex<Vec<Value>>>,
    decisions: Arc<Mutex<VecDeque<&'static str>>>,
    group_id: String,
    run_id: String,
    session_id: String,
}

impl Drop for LiveStory {
    fn drop(&mut self) {
        self.server.abort();
        self.receiver.abort();
        self.provider._handle.abort();
    }
}

async fn checked(request: reqwest::RequestBuilder) -> Value {
    let path = request.try_clone().unwrap().build().unwrap().url().path().to_owned();
    let response = request.send().await.expect("HTTP request");
    let status = response.status();
    let body: Value = response.json().await.expect("JSON response");
    assert!(status.is_success(), "HTTP {status} at {path}: {body}");
    body
}

fn principal() -> String {
    use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
    let mut header = Header::new(Algorithm::HS256);
    header.kid = Some("bare".into());
    encode(&header, &json!({"iss": "gateway", "aud": "bcs", "iat": now - 1, "exp": now + 300,
        "principals": [{"type": "user", "tenant": "loop-live", "subject": {
            "id": HUMAN, "username": HUMAN, "tenant_id": "loop-live"
        }}]}), &EncodingKey::from_secret(b"test-only-gateway-principal-signing-key")).unwrap()
}

impl LiveStory {
    async fn start(scenario: &str, outcomes: Vec<&'static str>) -> Self {
        let events = Arc::new(Mutex::new(Vec::new()));
        let notifications = Arc::new(Mutex::new(Vec::new()));
        let decisions = Arc::new(Mutex::new(VecDeque::from(outcomes)));
        let app = Router::new()
            .route("/events", post({ let events = events.clone(); move |Json(event): Json<Value>| {
                let events = events.clone(); async move { events.lock().await.push(event); Json(json!({"ok": true})) }
            }}))
            .route("/notifications", post({ let notifications = notifications.clone(); move |Json(event): Json<Value>| {
                let notifications = notifications.clone(); async move {
                    let mut received = notifications.lock().await;
                    received.push(event);
                    Json(json!({"message_ref": format!("local-im-{}", received.len())}))
                }
            }}))
            .route("/v1/chat/completions", post({ let decisions = decisions.clone(); move || {
                let decisions = decisions.clone(); async move {
                    let outcome = decisions.lock().await.pop_front().expect("unexpected Judge invocation");
                    Json(json!({"choices": [{"message": {"content": json!({"outcome": outcome,
                        "reason": "scripted editor decision", "confidence": 1.0,
                        "checked_criteria": [], "retry_instruction": ""}).to_string()}}]}))
                }
            }}));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let receiver_addr = listener.local_addr().unwrap();
        let receiver = tokio::spawn(async move { axum::serve(listener, app).await.unwrap(); });
        let directory = create_temp_bots_dir();
        let mut config = create_test_config(&directory.path().to_path_buf());
        config.metrics.enabled = false;
        config.auth.chain = vec!["local".into()];
        config.auth.mock_user_id = Some(HUMAN.into());
        config.auth.allow_mock_headers = true;
        config.collaboration.loop_execution_enabled = true;
        config.llm.provider_type = LlmProviderType::OpenAiCompatible;
        config.llm.base_url = format!("http://{receiver_addr}/v1");
        config.llm.api_key_env = None;
        config.llm.api_key = Some(Secret::new("local-test-judge-key".into()));
        config.eventing.enabled = true;
        config.eventing.dispatcher_enabled = true;
        config.eventing.webhook.allow_http_loopback = true;
        config.eventing.webhook.allow_non_standard_ports = true;
        config.eventing.fanout_poll_interval_ms = 10;
        config.eventing.delivery_poll_interval_ms = 10;
        config.channels.enabled = true;
        config.channels.providers.insert("loop-live-im".into(), bcs_config_api::ChannelProviderConfig {
            enabled: true, options: BTreeMap::from([("endpoint".into(), json!(format!("http://{receiver_addr}/notifications")))]),
        });
        let (addr, server) = start_test_server_with_config(config).await;
        let client = reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(10)).build().unwrap();
        checked(client.post(format!("http://{addr}/me/ensure-human")).header("X-Mock-User-Id", HUMAN).json(&json!({}))).await;
        let mut driver = MockBot::connect(addr).await;
        driver.register("Loop live driver", &["drive"], addr).await;
        let provider = start_provider_webhook().await;
        let bot = register_provider_bot(&client, addr, provider.url(), "loop-live-worker", "worker").await;
        let definition = definition(scenario);
        let group = checked(client.post(format!("http://{addr}/groups")).header("X-Mock-User-Id", HUMAN)
            .json(&json!({"driver_bot": driver.bot_id, "label": format!("Loop live {scenario}"),
                "group_strategy": "state_machine", "collaboration_definition_yaml": definition, "start_initial_run": false,
                "participants": [{"bot_uuid": driver.bot_id}, {"bot_uuid": bot.bot_uuid}],
                "participant_bindings": {"worker": {"source": "manual", "bot_ids": [bot.bot_uuid]}}
            }))).await;
        let group_id = group["id"].as_str().unwrap().to_owned();
        checked(client.post(format!("http://{addr}/channels/bindings")).header("X-Mock-User-Id", HUMAN)
            .json(&json!({"channel_type": "loop-live-im", "account_ref": "local-test-account",
                "target": {"group": {"group_id": group_id}}, "outbound_visibility": "full_transcript", "config": {}}))).await;
        checked(client.post(format!("http://{addr}/openapi/v1/collaboration/event-subscriptions"))
            .header("x-avernet-principal", principal()).json(&json!({
                "name": "Loop live observer", "scope": {"type": "group", "id": group_id},
                "event_filters": ["state_machine.*"], "payload": {"mode": "full"},
                "sink": {"type": "webhook", "url": format!("http://{receiver_addr}/events")}
            }))).await;
        provider.capture.clear().await;
        let started = checked(client.post(format!("http://{addr}/groups/{group_id}/state-machine-runs"))
            .header("X-Mock-User-Id", HUMAN).json(&json!({"input": {"question": "Exercise the Loop contract"}}))).await;
        Self { run_id: started["run"]["run_id"].as_str().unwrap().into(),
            session_id: started["run"]["session_id"].as_str().unwrap().into(),
            client, addr, provider, bot, _driver: driver, _directory: directory,
            server, receiver, events, notifications, decisions, group_id }
    }

    async fn get(&self, path: &str) -> Value {
        checked(self.client.get(format!("http://{}{path}", self.addr)).header("X-Mock-User-Id", HUMAN)).await
    }

    async fn run(&self) -> Value { self.get(&format!("/state-machine-runs/{}", self.run_id)).await }

    async fn pending(&self) -> Value {
        for _ in 0..200 {
            let pending = self.get(&format!("/state-machine-runs/{}/pending-human-nodes", self.run_id)).await;
            if let Some(node) = pending.as_array().unwrap().first() { return node.clone(); }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        panic!("expected pending human input: {}", self.run().await);
    }

    async fn finish_bot(&self, text: &str, previous: Option<&str>) {
        let request = self.provider.capture.wait_for_method("chat.send").await;
        assert_eq!(request.authorization.as_deref(), Some(format!("Bearer {}", self.bot.bcs_to_provider_token).as_str()));
        assert_eq!(request.body["bcn_group_id"], self.session_id);
        if let Some(previous) = previous {
            assert!(request.body["message"].to_string().contains(previous), "missing previous output: {}", request.body["message"]);
        }
        self.provider.capture.clear().await;
        checked(self.client.post(format!("http://{}/bot/events", self.addr))
            .header("X-BCN-Provider-Id", &self.bot.provider_id).bearer_auth(&self.bot.bot_runtime_token)
            .json(&json!({"run_id": request.body["id"], "state": "final", "message": {"text": text}}))).await;
    }

    async fn assert_next_entry_context(&self, outcome: &str, output: &str) {
        let request = self.provider.capture.wait_for_method("chat.send").await;
        let prompt = request.body["message"].to_string();
        assert!(prompt.contains("[Loop Context]"));
        assert!(prompt.contains("iteration: 2"));
        assert!(prompt.contains(&format!("outcome: {outcome}")));
        assert_eq!(prompt.matches(output).count(), 1, "previous result must not be duplicated");
    }

    async fn respond(&self, pending: &Value, text: &str) {
        assert_eq!(pending["response_ref"], format!("{}/{}", self.run_id, pending["node_id"].as_str().unwrap()));
        checked(self.client.post(format!("http://{}/state-machine-runs/{}/nodes/{}/respond", self.addr, self.run_id,
            pending["node_id"].as_str().unwrap())).header("X-Mock-User-Id", HUMAN).json(&json!({"content": text}))).await;
    }

    async fn notification(&self, pending: &Value) -> Value {
        for _ in 0..200 {
            if let Some(event) = self.notifications.lock().await.iter().find(|event|
                event["purpose"] == "HumanInputRequest" && event["payload"]["node_id"] == pending["node_id"]) {
                assert_eq!(event["recipient"], "local-reviewer");
                assert_eq!(event["conversation_type"], "1");
                assert_eq!(event["payload"]["run_id"], self.run_id);
                return event.clone();
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        panic!("missing direct_assignee notification for {pending}");
    }

    async fn verify_completed(&self, expected_completed: usize, expected_skipped: usize) -> Value {
        let run = wait_for_state_machine_run_status(&self.client, self.addr, &self.run_id, "completed").await;
        assert_eq!(run["run"]["output"], "final-live-output");
        let nodes = run["nodes"].as_array().unwrap();
        assert_eq!(nodes.iter().filter(|node| node["status"] == "completed").count(), expected_completed);
        assert_eq!(nodes.iter().filter(|node| node["status"] == "skipped").count(), expected_skipped);
        assert!(self.decisions.lock().await.is_empty());
        let graph = self.get(&format!("/state-machine-runs/{}/graph", self.run_id)).await;
        let history = self.get(&format!("/sessions/{}/messages", self.session_id)).await;
        let messages = history.as_array().unwrap();
        assert_eq!(messages.iter().filter(|message| message["metadata"]["state_machine"]["event"] == "output").count(), expected_completed);
        for _ in 0..200 {
            if self.events.lock().await.iter().filter(|event| event["event_type"] == "state_machine.node.completed").count() >= expected_completed { break; }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        let events = self.events.lock().await;
        assert_eq!(events.iter().filter(|event| event["event_type"] == "state_machine.node.completed").count(), expected_completed);
        for node in nodes {
            let id = node["node_id"].as_str().unwrap();
            let detail = self.get(&format!("/state-machine-runs/{}/nodes/{id}", self.run_id)).await;
            let projected = graph["nodes"].as_array().unwrap().iter().find(|item| item["node_id"] == id).unwrap();
            assert_eq!(projected["status"], node["status"]);
            let execution = &run["node_execution_metadata"][id];
            assert_eq!(&detail["execution"], execution);
            assert_eq!(&projected["execution"], execution);
            let outputs: Vec<_> = messages.iter().filter(|message| message["metadata"]["state_machine"]["event"] == "output"
                && message["metadata"]["state_machine"]["node_id"] == id).collect();
            if node["status"] == "skipped" {
                assert!(outputs.is_empty());
                assert!(!events.iter().any(|event| event["subject"]["id"] == id
                    && ["state_machine.node.started", "state_machine.node.completed"].contains(&event["event_type"].as_str().unwrap())));
                continue;
            }
            assert_eq!(outputs.len(), 1, "duplicate/missing output for {id}");
            assert_eq!(&outputs[0]["metadata"]["state_machine"]["execution"], execution);
            assert_eq!(outputs[0]["content"], node["artifact_text"]);
            for kind in ["state_machine.node.started", "state_machine.node.completed"] {
                let matches: Vec<_> = events.iter().filter(|event| event["event_type"] == kind && event["subject"]["id"] == id).collect();
                assert_eq!(matches.len(), 1, "missing or duplicate {kind}: {id}");
                assert_eq!(matches[0]["scope"]["group_id"], self.group_id);
                assert_eq!(matches[0]["scope"]["session_id"], self.session_id);
                assert_eq!(matches[0]["scope"]["run_id"], self.run_id);
                assert_eq!(&matches[0]["data"]["execution"], execution);
                assert_eq!(matches[0]["data"]["attempt"], node["attempt"]);
                if kind.ends_with("completed") {
                    assert_eq!(matches[0]["data"]["outcome"], node["outcome"]);
                    assert_eq!(matches[0]["data"]["output"]["included"], true);
                    assert_eq!(matches[0]["data"]["output"]["content_type"], "application/json");
                    assert_eq!(matches[0]["data"]["output"]["json"], node["artifact_text"]);
                }
            }
        }
        graph
    }
}

fn human_node() -> Value {
    json!({"kind": "human_input", "display_name": "Review input",
        "assignee": {"type": "runtime_actor", "actor": "human_11111111"},
        "notification": {"mode": "direct_assignee"}, "instruction": "Consider the previous result.", "node_timeout_ms": 60000})
}

fn bot_node() -> Value {
    json!({"kind": "bot_task", "display_name": "Work", "assignee": {"type": "bot_binding", "binding": "worker"},
        "instruction": "Use the previous result to produce the next output."})
}

fn definition(scenario: &str) -> String {
    let human_entry = scenario == "human_entry";
    let approved = scenario == "approved";
    let mut entry = if human_entry { human_node() } else { bot_node() };
    entry["transitions"] = json!({"complete": {"targets": ["review"]}});
    let mut result = bot_node();
    if approved { result["judge"] = json!({"type": "llm", "criteria": ["The editor approves the result."], "outcomes": ["revise", "approved"]}); }
    let mut finalize = bot_node();
    finalize["final_output"] = json!(true);
    let mut definition = json!({"name": format!("Loop live {scenario}"), "participants": {"worker": {"required": true}},
        "runtime": {"kind": "state_machine", "state_machine": {"version": 2, "graph_mode": "hierarchical",
            "human_input_channel": {"channel_type": "loop-live-im"},
            "nodes": {
                "cycle": {"kind": "loop", "display_name": "Loop", "loop": {
                    "mode": "fixed", "max_iterations": if approved { 3 } else { 2 },
                    "entry_node": "work", "result_node": "review", "continue_outcomes": [if approved { "revise" } else { "complete" }],
                    "break_outcomes": if approved { json!(["approved"]) } else { json!([]) },
                    "exhausted_outcome": "exhausted", "nodes": {"work": entry, "review": result}
                }, "transitions": {"exhausted": {"targets": [if scenario == "exhausted" { "resolve" } else { "finalize" }]}}},
                "finalize": finalize
            }
        }}});
    if approved { definition["runtime"]["state_machine"]["nodes"]["cycle"]["transitions"]["approved"] = json!({"targets": ["finalize"]}); }
    if scenario == "exhausted" {
        let mut human = human_node();
        human["transitions"] = json!({"complete": {"targets": ["finalize"]}});
        definition["runtime"]["state_machine"]["nodes"]["resolve"] = human;
    }
    // JSON is a YAML subset accepted by the same public definition parser.
    definition.to_string()
}

#[tokio::test]
async fn story_a_editor_breaks_second_iteration_and_future_body_is_skipped() {
    let story = LiveStory::start("approved", vec!["revise", "approved"]).await;
    story.finish_bot("draft-first", None).await;
    story.finish_bot("review-first", Some("draft-first")).await;
    story.assert_next_entry_context("revise", "review-first").await;
    story.finish_bot("draft-second", Some("review-first")).await;
    story.finish_bot("review-second", Some("draft-second")).await;
    story.finish_bot("final-live-output", Some("review-second")).await;
    let graph = story.verify_completed(5, 2).await;
    let third: Vec<_> = graph["nodes"].as_array().unwrap().iter().filter(|node| node["execution"]["iteration"] == 3).collect();
    assert_eq!(third.len(), 2);
    assert!(third.iter().all(|node| node["status"] == "skipped"));
}

#[tokio::test]
async fn story_b_empty_break_exhausts_then_waits_for_human_before_final() {
    let story = LiveStory::start("exhausted", vec![]).await;
    story.finish_bot("draft-first", None).await;
    story.finish_bot("review-first", Some("draft-first")).await;
    story.assert_next_entry_context("complete", "review-first").await;
    story.finish_bot("draft-second", Some("review-first")).await;
    story.finish_bot("review-second", Some("draft-second")).await;
    let pending = story.pending().await;
    assert_eq!(pending["node_id"], "resolve");
    assert_eq!(story.run().await["run"]["status"], "running");
    let notification = story.notification(&pending).await;
    assert!(notification["text"].as_str().unwrap().contains("review-second"));
    story.respond(&pending, "human-resolution").await;
    story.finish_bot("final-live-output", Some("human-resolution")).await;
    let graph = story.verify_completed(6, 0).await;
    let result = graph["nodes"].as_array().unwrap().iter().find(|node| node["execution"]["iteration"] == 2
        && node["execution"]["definition_node_id"] == "review").unwrap();
    assert_eq!(result["outcome"], "complete");
    let exit = graph["edges"].as_array().unwrap().iter().find(|edge| edge["source"] == result["node_id"] && edge["target"] == "resolve").unwrap();
    assert_eq!(exit["outcome"], "complete");
    assert_eq!(exit["loop_route"], json!({"kind": "exhausted", "logical_outcome": "exhausted"}));
}

#[tokio::test]
async fn story_c_human_entry_receives_previous_result_and_replies_to_current_execution() {
    let story = LiveStory::start("human_entry", vec![]).await;
    let first = story.pending().await;
    assert_eq!(first["loop_context"]["iteration"], 1);
    assert!(first["loop_context"]["previous_result"].is_null());
    let initial = story.notification(&first).await;
    assert!(initial["text"].as_str().unwrap().contains("本轮没有上一轮结果。"));
    story.respond(&first, "first-human-input").await;
    story.finish_bot("first-reviewed-result", Some("first-human-input")).await;
    let second = story.pending().await;
    assert_eq!(second["loop_context"]["iteration"], 2);
    assert_ne!(second["response_ref"], first["response_ref"]);
    let previous = &second["loop_context"]["previous_result"];
    assert_eq!(previous["outcome"], "complete");
    assert_eq!(previous["output"], "first-reviewed-result");
    let notification = story.notification(&second).await;
    let text = notification["text"].as_str().unwrap();
    assert_eq!(text.matches("first-reviewed-result").count(), 1);
    assert!(text.contains("结果：complete"));
    let stale = story.client.post(format!("http://{}/state-machine-runs/{}/nodes/{}/respond", story.addr, story.run_id,
        first["node_id"].as_str().unwrap())).header("X-Mock-User-Id", HUMAN).json(&json!({"content": "stale"})).send().await.unwrap();
    assert_eq!(stale.status(), reqwest::StatusCode::CONFLICT);
    assert_eq!(story.pending().await["node_id"], second["node_id"]);
    story.respond(&second, "second-human-input").await;
    story.finish_bot("second-reviewed-result", Some("second-human-input")).await;
    story.finish_bot("final-live-output", Some("second-reviewed-result")).await;
    story.verify_completed(5, 0).await;
}
