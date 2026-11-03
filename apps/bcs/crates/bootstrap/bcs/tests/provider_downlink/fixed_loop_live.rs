//! FL-25: real HTTP routes, Provider callbacks, Event webhooks and IM delivery.
//! No runtime/store methods are called to advance or inspect these stories.

use super::*;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_db_local::LocalSqliteDbPlugin;
use std::collections::{BTreeMap, VecDeque};

#[path = "loop_channel.rs"]
mod channel;

const HUMAN: &str = "11111111";
static INIT_PERSISTENT_SECRET: std::sync::Once = std::sync::Once::new();

struct LiveStory {
    client: reqwest::Client,
    addr: SocketAddr,
    provider: ProviderServer,
    bot: RegisteredProviderBot,
    _driver: MockBot,
    _directory: tempfile::TempDir,
    server: Option<tokio::task::JoinHandle<Result<(), bcs::BcsError>>>,
    persistent_config: Option<bcs::BcsConfig>,
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
        if let Some(server) = &self.server { server.abort(); }
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
        Self::start_with_storage(scenario, outcomes, false).await
    }

    async fn start_persistent(scenario: &str, outcomes: Vec<&'static str>) -> Self {
        Self::start_with_storage(scenario, outcomes, true).await
    }

    async fn start_with_storage(scenario: &str, outcomes: Vec<&'static str>, persistent: bool) -> Self {
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
        config.collaboration.nested_loop_execution_enabled = true;
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
        let persistent_config = if persistent {
            INIT_PERSISTENT_SECRET.call_once(|| {
                // SAFETY: the dedicated key is initialized once before the persistent server reads it.
                unsafe {
                    std::env::set_var(
                        "BCS_SECRET_LOOP_LIVE",
                        "test-only-gateway-principal-signing-key",
                    )
                };
            });
            config.database.sqlite.path = directory.path().join("nested-loop-live.db").to_string_lossy().into_owned();
            config.secret.provider = "env".into();
            config.gateway_principal.signing_key_secret = Some("loop-live".into());
            config.group_session_ws.signing_key_secret = "loop-live".into();
            config.state_machine_history.persistence_enabled = true;
            config.message_history.state_machine_cutoff_timestamp = 0;
            config.security.outbound_url.block_private_networks = false;
            config.security.outbound_url.allow_loopback = true;
            Some(config.clone())
        } else {
            None
        };
        let (addr, server) = if persistent {
            start_persistent_test_server_with_config(config).await
        } else {
            start_test_server_with_config(config).await
        };
        let client = reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(10)).build().unwrap();
        checked(client.post(format!("http://{addr}/me/ensure-human")).header("X-Mock-User-Id", HUMAN).json(&json!({}))).await;
        // bot-authority ruling T6d-1: the driver's
        // ownership is seeded through the TRUSTED
        // register surface (`register_with_ownership`
        // commits the authority owner edge) and the
        // driver RESUMES via its token; the mock-user
        // onboard never mints authority.
        let register_token = checked(
            client
                .get(format!("http://{addr}/register/token"))
                .header("X-Mock-User-Id", HUMAN),
        )
        .await["token"]
            .as_str()
            .expect("register token")
            .to_string();
        let registered = checked(
            client.post(format!(
                "http://{addr}/register?token={register_token}&bot-name=Loop-live-driver"
            )),
        )
        .await;
        let driver_bot_uuid = registered["bot_uuid"].as_str().expect("bot_uuid").to_string();
        let driver_bot_token = registered["bot_token"].as_str().expect("bot_token").to_string();
        let mut driver = MockBot::reconnect(addr, &driver_bot_token).await;
        driver.register("Loop live driver", &["drive"], addr).await;
        let provider = start_provider_webhook().await;
        let bot = register_provider_bot(&client, addr, provider.url(), "loop-live-worker", "worker").await;
        let definition = definition(scenario);
        let group = checked(client.post(format!("http://{addr}/groups")).header("X-Mock-User-Id", HUMAN)
            .json(&json!({"driver_bot": driver_bot_uuid, "label": format!("Loop live {scenario}"),
                "group_strategy": "state_machine", "collaboration_definition_yaml": definition, "start_initial_run": false,
                "participants": [{"bot_uuid": driver_bot_uuid}, {"bot_uuid": bot.bot_uuid}],
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
            server: Some(server), persistent_config, receiver, events, notifications, decisions, group_id }
    }

    async fn restart(&mut self, nested_execution_enabled: bool) {
        let server = self.server.take().expect("running persistent server");
        server.abort();
        match server.await {
            Err(error) if error.is_cancelled() => {}
            result => panic!("persistent test server did not stop after abort: {result:?}"),
        }
        let mut config = self.persistent_config.clone().expect("persistent server config");
        config.collaboration.nested_loop_execution_enabled = nested_execution_enabled;
        let (addr, server) = start_persistent_test_server_with_config(config.clone()).await;
        self.addr = addr;
        self.server = Some(server);
        self.persistent_config = Some(config);
    }

    async fn execute_persistent_sql(&self, sql: &str) {
        let path = &self.persistent_config.as_ref().expect("persistent server config")
            .database.sqlite.path;
        LocalSqliteDbPlugin::new_file(path).expect("open persistent story database")
            .execute(DbStatement::new(sql)).await.expect("execute persistent story SQL");
    }

    async fn raw_response(&self, pending: &Value, text: &str) -> reqwest::Response {
        self.client.post(format!("http://{}/state-machine-runs/{}/nodes/{}/respond",
            self.addr, self.run_id, pending["node_id"].as_str().unwrap()))
            .header("X-Mock-User-Id", HUMAN).json(&json!({"content": text}))
            .send().await.expect("Human response request")
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
        assert_eq!(request.body["bcn_group_id"], self.group_id);
        assert_eq!(request.body["session_id"], self.session_id);
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
        let response = self.raw_response(pending, text).await;
        let status = response.status();
        let body: Value = response.json().await.unwrap();
        assert!(status.is_success() || status == reqwest::StatusCode::CONFLICT,
            "HTTP {status}: {body}");
        if status == reqwest::StatusCode::CONFLICT {
            // Recovery can complete our accepted output before the request's
            // completion CAS. A 409 alone is not evidence of acceptance.
            assert!(body["message"].as_str().unwrap_or_default()
                .contains("state-machine transition changed concurrently")
                || body["message"].as_str().unwrap_or_default()
                    .contains("human Judge is already claimed or no longer active"), "{body}");
        }
        self.assert_committed_response(pending, text).await;
    }

    async fn assert_committed_response(&self, pending: &Value, text: &str) {
        let path = format!("/state-machine-runs/{}/nodes/{}", self.run_id,
            pending["node_id"].as_str().unwrap());
        // A recovery worker may still be finishing the accepted Judge input.
        // Observe completion without resubmitting the response.
        let mut current = self.get(&path).await;
        for _ in 0..200 {
            if current["node"]["status"] == "completed" { break; }
            tokio::time::sleep(Duration::from_millis(25)).await;
            current = self.get(&path).await;
        }
        assert_eq!(current["node"]["run_id"], self.run_id);
        assert_eq!(current["node"]["node_id"], pending["node_id"]);
        assert_eq!(current["execution"], pending["execution"]);
        assert_eq!(current["node"]["status"], "completed", "{current}");
        assert_eq!(current["node"]["artifact_text"], text);
        assert_eq!(current["node"]["responded_by"], format!("human_{HUMAN}"));
    }

    async fn notification(&self, pending: &Value) -> Value {
        for _ in 0..400 {
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

fn named_human_node(display_name: &str, instruction: &str) -> Value {
    let mut node = human_node();
    node["display_name"] = json!(display_name);
    node["instruction"] = json!(instruction);
    node
}

fn nested_human_definition() -> String {
    let inner_review = named_human_node("Inner review", "Review the innermost result.");
    let mut middle_gate = named_human_node("Middle gate", "Confirm the middle iteration.");
    middle_gate["transitions"] = json!({"complete": {"targets": ["inner"]}});
    let middle_result = named_human_node("Middle result", "Confirm the middle result.");
    let mut outer_gate = named_human_node("Outer gate", "Confirm the outer iteration.");
    outer_gate["transitions"] = json!({"complete": {"targets": ["middle"]}});
    let mut outer_result = named_human_node("Outer result", "Decide whether another outer iteration is required.");
    outer_result["judge"] = json!({"type": "llm", "criteria": ["Whether the overall review is complete"],
        "outcomes": ["repeat", "done"]});
    let mut finalize = bot_node();
    finalize["display_name"] = json!("Publish nested review");
    finalize["final_output"] = json!(true);
    let inner = json!({"kind": "loop", "display_name": "Inner review", "loop": {
        "mode": "fixed", "max_iterations": 2, "entry_node": "inner_review", "result_node": "inner_review",
        "continue_outcomes": ["complete"], "break_outcomes": [], "exhausted_outcome": "inner_exhausted",
        "nodes": {"inner_review": inner_review}
    }, "transitions": {"inner_exhausted": {"targets": ["middle_result"]}}});
    let middle = json!({"kind": "loop", "display_name": "Middle review", "loop": {
        "mode": "fixed", "max_iterations": 2, "entry_node": "middle_gate", "result_node": "middle_result",
        "continue_outcomes": ["complete"], "break_outcomes": [], "exhausted_outcome": "middle_exhausted",
        "nodes": {"middle_gate": middle_gate, "inner": inner, "middle_result": middle_result}
    }, "transitions": {"middle_exhausted": {"targets": ["outer_result"]}}});
    let outer = json!({"kind": "loop", "display_name": "Outer review", "loop": {
        "mode": "fixed", "max_iterations": 3, "entry_node": "outer_gate", "result_node": "outer_result",
        "continue_outcomes": ["repeat"], "break_outcomes": ["done"], "exhausted_outcome": "outer_exhausted",
        "nodes": {"outer_gate": outer_gate, "middle": middle, "outer_result": outer_result}
    }, "transitions": {"done": {"targets": ["finalize"]},
        "outer_exhausted": {"targets": ["finalize"]}}});
    json!({"name": "Nested Human live", "participants": {"worker": {"required": true}},
        "runtime": {"kind": "state_machine", "state_machine": {"version": 2, "graph_mode": "hierarchical",
            "human_input_channel": {"channel_type": "loop-live-im"},
            "nodes": {"outer": outer, "finalize": finalize}
        }}}
    ).to_string()
}

fn definition(scenario: &str) -> String {
    if scenario == "nested_human" { return nested_human_definition(); }
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

#[tokio::test]
async fn nested_human_story_preserves_parent_context_and_direct_notifications() {
    let story = LiveStory::start("nested_human", vec!["repeat", "done"]).await;
    let mut first_inner = BTreeMap::<(u64, u64), (String, String)>::new();
    let mut stale_node = None;
    for index in 0..20 {
        let pending = story.pending().await;
        let execution = &pending["execution"];
        let path = execution["loop_path"].as_array().expect("nested loop path");
        assert!((1..=3).contains(&path.len()));
        let notification = story.notification(&pending).await;
        let notification_text = notification["text"].as_str().unwrap();
        if !pending["loop_context"].is_null() {
            assert_eq!(pending["loop_context"]["loop_path"], execution["loop_path"]);
            assert!(notification_text.contains("路径："));
            for frame in path {
                assert!(notification_text.contains(&format!("{} {}/{}", frame["loop_id"].as_str().unwrap(),
                    frame["iteration"].as_u64().unwrap(), frame["max_iterations"].as_u64().unwrap())));
            }
        }
        let content = format!("nested-human-{index}");
        if execution["definition_node_id"] == "inner_review" {
            let parent = (path[0]["iteration"].as_u64().unwrap(), path[1]["iteration"].as_u64().unwrap());
            if path[2]["iteration"] == 1 {
                assert!(pending["loop_context"]["previous_result"].is_null());
                first_inner.insert(parent, (pending["node_id"].as_str().unwrap().into(), content.clone()));
            } else {
                let expected = first_inner.get(&parent).expect("first inner result for parent");
                assert_eq!(pending["loop_context"]["previous_result"]["execution_node_id"], expected.0);
                assert_eq!(pending["loop_context"]["previous_result"]["output"], expected.1);
                assert_eq!(notification_text.matches(&expected.1).count(), 1);
            }
        }
        story.respond(&pending, &content).await;
        if stale_node.is_none() && execution["definition_node_id"] == "inner_review" {
            let node_id = pending["node_id"].as_str().unwrap().to_owned();
            let stale = story.client.post(format!("http://{}/state-machine-runs/{}/nodes/{node_id}/respond",
                story.addr, story.run_id)).header("X-Mock-User-Id", HUMAN)
                .json(&json!({"content": "stale-nested-human"})).send().await.unwrap();
            assert_eq!(stale.status(), reqwest::StatusCode::CONFLICT);
            stale_node = Some(node_id);
        }
    }
    assert_eq!(first_inner.len(), 4);
    assert!(stale_node.is_some());
    story.finish_bot("final-live-output", Some("nested-human-19")).await;
    let graph = story.verify_completed(21, 10).await;
    assert_eq!(graph["loops"].as_object().unwrap().keys().cloned().collect::<Vec<_>>(),
        vec!["outer", "outer/middle", "outer/middle/inner"]);
    let skipped: Vec<_> = graph["nodes"].as_array().unwrap().iter()
        .filter(|node| node["status"] == "skipped").collect();
    assert!(skipped.iter().all(|node| node["execution"]["loop_path"][0]["iteration"] == 3));
}

#[tokio::test]
async fn nested_human_persistent_story_recovers_with_admission_disabled() {
    let mut story = LiveStory::start_persistent("nested_human", vec!["repeat", "done"]).await;
    let mut stale_node = None;
    for index in 0..20 {
        let mut pending = story.pending().await;
        if index == 0 {
            let original_notification = story.notification(&pending).await;
            let node_id = pending["node_id"].as_str().unwrap().to_owned();
            assert_eq!(story.notifications.lock().await.iter()
                .filter(|event| event["purpose"] == "HumanInputRequest"
                    && event["payload"]["node_id"] == node_id).count(), 1);
            story.restart(true).await;
            let recovered = story.pending().await;
            assert_eq!(recovered["node_id"], node_id);
            assert_eq!(recovered["execution"], pending["execution"]);
            let recovered_notification = story.notification(&recovered).await;
            assert_eq!(recovered_notification["payload"], original_notification["payload"]);
            assert_eq!(recovered_notification["text"], original_notification["text"]);
            assert_eq!(recovered_notification["recipient"], original_notification["recipient"]);
            tokio::time::sleep(Duration::from_millis(250)).await;
            assert_eq!(story.notifications.lock().await.iter()
                .filter(|event| event["purpose"] == "HumanInputRequest"
                    && event["payload"]["node_id"] == node_id).count(), 1,
                "restart must not repeat an accepted notification");
            pending = recovered;
        }
        if [4, 10, 19].contains(&index) {
            let node_id = pending["node_id"].as_str().unwrap().to_owned();
            story.restart(index < 10).await;
            let recovered = story.pending().await;
            assert_eq!(recovered["node_id"], node_id);
            assert_eq!(recovered["execution"], pending["execution"]);
            pending = recovered;
            if let Some(stale_node) = &stale_node {
                let stale = story.client.post(format!("http://{}/state-machine-runs/{}/nodes/{stale_node}/respond",
                    story.addr, story.run_id)).header("X-Mock-User-Id", HUMAN)
                    .json(&json!({"content": "stale-after-restart"})).send().await.unwrap();
                assert_eq!(stale.status(), reqwest::StatusCode::CONFLICT);
            }
        }
        assert!(!pending["execution"]["loop_path"].as_array().unwrap().is_empty());
        if index == 12 {
            story.execute_persistent_sql("CREATE TRIGGER reject_nested_history BEFORE INSERT ON bcs_messages BEGIN SELECT RAISE(ABORT,'injected nested history failure'); END").await;
            let failed = story.raw_response(&pending, "persistent-human-12").await;
            assert!(failed.status().is_server_error(), "history failure returned {}", failed.status());
            let accepted = story.get(&format!("/state-machine-runs/{}/nodes/{}", story.run_id,
                pending["node_id"].as_str().unwrap())).await;
            assert_eq!(accepted["node"]["artifact_text"], "persistent-human-12");
            story.execute_persistent_sql("DROP TRIGGER reject_nested_history").await;
            story.restart(false).await;
            let mut recovered_next = None;
            let mut last_retry = String::new();
            'history_retry: for _ in 0..20 {
                let retry = story.raw_response(&pending, "persistent-human-12").await;
                let status = retry.status();
                last_retry = retry.text().await.unwrap();
                assert!(status.is_success() || status == reqwest::StatusCode::CONFLICT,
                    "history retry returned {status}: {last_retry}");
                for _ in 0..10 {
                    let current = story.get(&format!("/state-machine-runs/{}/nodes/{}", story.run_id,
                        pending["node_id"].as_str().unwrap())).await;
                    if current["node"]["status"] == "completed" {
                        recovered_next = Some(story.pending().await);
                        break 'history_retry;
                    }
                    tokio::time::sleep(Duration::from_millis(50)).await;
                }
            }
            let recovered_next = recovered_next.unwrap_or_else(||
                panic!("history retry must advance to the next Human node: {last_retry}"));
            assert_ne!(recovered_next["node_id"], pending["node_id"]);
            let stale = story.raw_response(&pending, "stale-after-history-recovery").await;
            assert_eq!(stale.status(), reqwest::StatusCode::CONFLICT);
            continue;
        }
        if index == 19 {
            story.execute_persistent_sql("CREATE TRIGGER reject_nested_skip BEFORE UPDATE ON bcs_state_machine_node_runs WHEN NEW.status='skipped' AND OLD.status<>'skipped' BEGIN SELECT RAISE(ABORT,'injected nested skip failure'); END").await;
            let failed = story.raw_response(&pending, "persistent-human-19").await;
            let status = failed.status();
            let body: Value = failed.json().await.unwrap();
            assert!(status.is_server_error() || (status == reqwest::StatusCode::CONFLICT
                && (body["message"].as_str().unwrap_or_default()
                    .contains("human Judge is already claimed or no longer active")
                    || body["message"].as_str().unwrap_or_default()
                        .contains("state-machine transition changed concurrently"))),
                "skip failure returned {status}: {body}");
            story.assert_committed_response(&pending, "persistent-human-19").await;
            let blocked = story.run().await;
            assert_eq!(blocked["run"]["status"], "running");
            assert_eq!(blocked["nodes"].as_array().unwrap().iter()
                .filter(|node| node["status"] == "skipped").count(), 0,
                "the injected failure must prevent all skip writes");
            story.execute_persistent_sql("DROP TRIGGER reject_nested_skip").await;
            story.restart(false).await;
            let stale = story.raw_response(&pending, "stale-after-skip-restart").await;
            assert_eq!(stale.status(), reqwest::StatusCode::CONFLICT);
            continue;
        }
        story.respond(&pending, &format!("persistent-human-{index}")).await;
        stale_node.get_or_insert_with(|| pending["node_id"].as_str().unwrap().to_owned());
    }
    story.finish_bot("final-live-output", Some("persistent-human-19")).await;
    let graph = story.verify_completed(21, 10).await;
    assert_eq!(graph["loops"].as_object().unwrap().len(), 3);
    assert_eq!(story.persistent_config.as_ref().unwrap()
        .collaboration.nested_loop_execution_enabled, false);
}
