use std::{path::PathBuf, sync::{Arc, Mutex}, time::Duration};
use bcs_bridge_core::{config::ProviderConfig, engine::{Engine, EngineFactory, EngineRegistry, TurnRequest, TurnOutcome, TurnError}, sse, AppState};
use bcs_protocol::stream::{StreamEvent, InteractionKind, InteractionPhase};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;

#[derive(Clone, Default)]
pub struct Mock {
    pub turns: Arc<Mutex<Vec<(String, Option<String>)>>>,
    pub release: CancellationToken,
    pub progress_start: CancellationToken,
    pub decisions: Arc<Mutex<Vec<Value>>>,
}
impl EngineFactory for Mock {
    fn id(&self) -> &str { "mock-a2a" }
    fn default_bin(&self) -> &str { "unused" }
    fn requires_bin(&self) -> bool { false }
    fn build(&self, _: PathBuf, _: &toml::Table) -> Result<Arc<dyn Engine>, String> { Ok(Arc::new(self.clone())) }
}
#[async_trait::async_trait]
impl Engine for Mock {
    async fn run_turn(&self, req: TurnRequest, events: tokio::sync::mpsc::Sender<StreamEvent>, abort: CancellationToken) -> Result<TurnOutcome, TurnError> {
        self.turns.lock().unwrap().push((req.prompt.clone(), req.engine_session_id.clone()));
        let session = req.engine_session_id.clone().unwrap_or_else(|| format!("engine-{}", req.run_id));
        req.session_observer.established(&session).await.map_err(TurnError::SessionStorage)?;
        if req.prompt == "interactive" {
            let (id, resolution) = req.interactions.register(&req.run_id, InteractionKind::Exec, "native-approval".into());
            let event = sse::interaction_event(&req.run_id, InteractionPhase::Requested, InteractionKind::Exec, &id, json!({}));
            events.send(event).await.map_err(|_| TurnError::Aborted)?;
            let decision = resolution.await.map_err(|_| TurnError::Protocol("approval receiver dropped".into()))?;
            self.decisions.lock().unwrap().push(decision);
            abort.cancelled().await;
            return Err(TurnError::Aborted);
        }
        let progress_case = matches!(req.prompt.as_str(), "codex-progress" | "claude-progress" | "empty-progress");
        if progress_case {
            tokio::select! {
                _ = abort.cancelled() => return Err(TurnError::Aborted),
                _ = self.progress_start.cancelled() => {},
            }
        }
        let deltas: Vec<String> = match req.prompt.as_str() {
            "codex-progress" => vec!["Checking the PR...".into()],
            "claude-progress" => vec!["正在".into(), "分析".into()],
            "empty-progress" => vec!["Working...".into()],
            "large" => vec!["x".repeat(1024 * 1024 + 1)],
            "large-progress-total" => vec!["x".repeat(512 * 1024), "x".repeat(512 * 1024 + 1)],
            _ => vec!["答复: ".into()],
        };
        for delta in deltas { events.send(sse::chat_delta(&req.run_id, &delta)).await.map_err(|_| TurnError::Aborted)?; }
        if progress_case || matches!(req.prompt.as_str(), "wait" | "hold" | "large" | "large-progress-total") {
            tokio::select! {
                _ = abort.cancelled() => return Err(TurnError::Aborted),
                _ = self.release.cancelled(), if req.prompt == "hold" || progress_case => {},
            }
        }
        if req.prompt == "fail" { return Err(TurnError::Protocol("private-engine-diagnostic".into())); }
        let answer = match req.prompt.as_str() {
            "codex-progress" => "Found two issues.".into(),
            "claude-progress" => "完成了".into(),
            "empty-progress" => String::new(),
            "large-final" => "x".repeat(1024 * 1024 + 1),
            _ => format!("答复: {}", req.prompt),
        };
        Ok(TurnOutcome { engine_session_id: Some(session), final_text: Some(answer) })
    }
}

pub struct Server {
    pub url: String,
    pub client: reqwest::Client,
    pub mock: Mock,
    pub states: Vec<Arc<AppState>>,
    pub dir: tempfile::TempDir,
    stop: CancellationToken,
    task: Option<tokio::task::JoinHandle<()>>,
}
impl Server {
    pub async fn new(providers: &[(&str, &[&str])], enabled: bool) -> Self {
        let dir = tempfile::tempdir().unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let mock = Mock::default();
        let mut engines = EngineRegistry::new(); engines.register(mock.clone());
        let mut states = Vec::new();
        for (provider, bots) in providers {
            let credential = format!("token-{}", provider);
            let a2a = if enabled { format!("[a2a]\npublic_url='{url}'\n") } else { String::new() };
            let bots = bots.iter().map(|bot| format!("[[bot]]\nprovider_bot_ref='{bot}'\nengine='mock-a2a'\ncwd='{}'\n", dir.path().display())).collect::<String>();
            let config = ProviderConfig::from_toml(&format!("provider_id='{provider}'\nlisten='127.0.0.1:0'\nstate_path='{provider}.db'\nbcs_to_provider_token='{credential}'\n{a2a}{bots}"), &dir.path().join("bridge.toml")).unwrap();
            states.push(Arc::new(AppState::new(config, &engines).unwrap()));
        }
        let router = bcs_bridge_core::webhook::multi_router(states.clone());
        let stop = CancellationToken::new(); let signal = stop.clone(); let stopping = states.clone();
        let task = tokio::spawn(async move {
            axum::serve(listener, router).with_graceful_shutdown(async move {
                signal.cancelled().await;
                for state in stopping { state.runs.abort_all("shutdown").await; }
            }).await.unwrap();
        });
        Self { url, mock, states, dir, stop, task: Some(task), client: reqwest::Client::builder().no_proxy().timeout(Duration::from_secs(5)).build().unwrap() }
    }
    pub fn endpoint(&self, provider: &str, bot: &str) -> String {
        let mut url = url::Url::parse(&self.url).unwrap();
        url.path_segments_mut().unwrap().extend(["a2a", provider, bot]);
        url.into()
    }
    pub fn post(&self, provider: &str, bot: &str, path: &str) -> reqwest::RequestBuilder {
        self.client.post(format!("{}{path}", self.endpoint(provider, bot))).bearer_auth(format!("token-{provider}"))
    }
    pub fn get(&self, provider: &str, bot: &str, path: &str) -> reqwest::RequestBuilder {
        self.client.get(format!("{}{path}", self.endpoint(provider, bot))).bearer_auth(format!("token-{provider}"))
    }
    pub async fn task(&self, provider: &str, bot: &str, id: &str) -> a2a::Task {
        self.get(provider, bot, &format!("/tasks/{id}")).send().await.unwrap().error_for_status().unwrap().json().await.unwrap()
    }
    pub async fn terminal(&self, provider: &str, bot: &str, id: &str) -> a2a::Task {
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                let task = self.task(provider, bot, id).await;
                if task.status.state.is_terminal() { return task; }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }).await.unwrap()
    }
    pub async fn close(mut self) {
        self.stop.cancel();
        tokio::time::timeout(Duration::from_secs(5), self.task.take().unwrap()).await.unwrap().unwrap();
    }
}
impl Drop for Server { fn drop(&mut self) { self.stop.cancel(); } }

pub fn request(id: &str, text: &str) -> Value {
    json!({"message":{"messageId":id,"role":"ROLE_USER","parts":[{"text":text}]}})
}
pub fn asynchronous(id: &str, text: &str) -> Value {
    let mut value = request(id, text);
    value["configuration"] = json!({"returnImmediately":true});
    value
}
pub fn events(body: &str) -> Vec<Value> {
    body.lines().filter_map(|line| line.strip_prefix("data: ")).map(|line| serde_json::from_str(line).unwrap()).collect()
}
