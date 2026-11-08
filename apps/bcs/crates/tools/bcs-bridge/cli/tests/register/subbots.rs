//! Subbot registration through the public CLI with an explicit parent resolver.
use super::*;
use std::collections::VecDeque;

const CONFLICT: &str = r#"{"code":40900,"data":{"error_code":"registration_conflict"}}"#;

enum Reply { Success, Error(StatusCode, &'static str), BotId(&'static str) }
struct SubbotApi { base: String, calls: Calls, task: JoinHandle<()> }
impl Drop for SubbotApi { fn drop(&mut self) { self.task.abort(); } }
impl SubbotApi {
    async fn new(replies: Vec<Reply>) -> Self {
        let calls: Calls = Arc::default();
        let captured = calls.clone();
        let replies = Arc::new(Mutex::new(VecDeque::from(replies)));
        let router = Router::new().route("/openapi/v1/collaboration/register", post(move |Query(query): Query<HashMap<String, String>>| {
            let captured = captured.clone();
            let replies = replies.clone();
            async move {
                let mut calls = captured.lock().unwrap();
                calls.push(query.clone());
                let reply = replies.lock().unwrap().pop_front().unwrap_or(Reply::Error(StatusCode::INTERNAL_SERVER_ERROR, "unexpected request"));
                let id = match reply {
                    Reply::Success => format!("bot_registered_{:04}", calls.len()),
                    Reply::BotId(id) => id.to_owned(),
                    Reply::Error(status, body) => return (status, body.to_string()),
                };
                let (parent, slug) = query["provider_bot_ref"].rsplit_once(':').unwrap();
                (StatusCode::CREATED, json!({"code":20100,"data":{"bot_uuid":id,"bot_token":BOT_TOKEN,
                    "registration":{"provider_id":"prv_test","mode":query["mode"],"auth_mode":"agentpass-subbots",
                        "agent_code":parent,"bot_slug":slug,"provider_bot_ref":query["provider_bot_ref"]}}}).to_string())
            }
        }));
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap(); });
        Self { base, calls, task }
    }
    fn command(&self, home: &Path) -> Command {
        register_as(home, &["--provider-auth", "static-bearer", "--provider-bot-ref", "parent:region",
            "--api-url", &self.base, "--upstream-url", "ws://127.0.0.1:21000/ws/bot"], "agentpass-subbots")
    }
    fn calls(&self) -> Vec<HashMap<String, String>> { self.calls.lock().unwrap().clone() }
}

fn bootstrap(home: &Path) -> String {
    let path = home.join(".bcn-bridge/bridge.toml");
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let text = "[registration]\nprovider_auth='static-bearer'\n";
    std::fs::write(path, text).unwrap();
    text.into()
}

#[tokio::test]
async fn same_name_gets_five_character_slugs_and_distinct_full_uuid_workspaces() {
    let home = tempfile::tempdir().unwrap();
    let api = SubbotApi::new(vec![Reply::Success, Reply::Success]).await;
    for index in 1..=2 {
        let output = api.command(home.path()).output().await.unwrap();
        assert!(output.status.success(), "{}", diagnostic(&output));
        assert!(!diagnostic(&output).contains(BOT_TOKEN));
        let document = saved(home.path());
        assert_eq!(document["bot"].as_array().unwrap().len(), index);
        let bot = &document["bot"][index - 1];
        let slug = bot["bot_slug"].as_str().unwrap();
        assert_eq!(slug.len(), 5);
        assert!(slug.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit()));
        let expected = home.path().join(format!(".bcn-bridge/bots/bot_registered_{index:04}"));
        assert!(expected.is_dir());
        assert_eq!(Path::new(bot["cwd"].as_str().unwrap()), expected.canonicalize().unwrap());
        std::fs::write(expected.join("keep"), "Bot-owned data").unwrap();
    }
    let document = saved(home.path());
    assert_ne!(document["bot"][0]["bot_slug"], document["bot"][1]["bot_slug"]);
    assert_ne!(document["bot"][0]["cwd"], document["bot"][1]["cwd"]);
    assert_eq!(std::fs::read_to_string(home.path().join(".bcn-bridge/bots/bot_registered_0001/keep")).unwrap(), "Bot-owned data");
    assert!(!home.path().join("workspace").exists());
    assert_eq!(api.calls().len(), 2);
}

#[tokio::test]
async fn retries_only_confirmed_conflicts_with_fresh_slugs_and_a_fixed_budget() {
    for succeeds in [false, true] {
        let home = tempfile::tempdir().unwrap();
        let before = bootstrap(home.path());
        let last = if succeeds { Reply::Success } else { Reply::Error(StatusCode::CONFLICT, CONFLICT) };
        let api = SubbotApi::new(vec![Reply::Error(StatusCode::CONFLICT, CONFLICT), Reply::Error(StatusCode::CONFLICT, CONFLICT), last]).await;
        let output = api.command(home.path()).output().await.unwrap();
        assert_eq!(output.status.success(), succeeds, "{}", diagnostic(&output));
        let calls = api.calls();
        assert_eq!(calls.len(), 3);
        let refs: std::collections::HashSet<_> = calls.iter().map(|call| &call["provider_bot_ref"]).collect();
        assert_eq!(refs.len(), 3);
        if succeeds {
            assert_eq!(saved(home.path())["bot"][0]["provider_bot_ref"].as_str(), Some(calls[2]["provider_bot_ref"].as_str()));
            assert!(home.path().join(".bcn-bridge/bots/bot_registered_0003").is_dir());
        } else {
            assert!(diagnostic(&output).contains("HTTP 409"));
            assert_eq!(std::fs::read_to_string(home.path().join(".bcn-bridge/bridge.toml")).unwrap(), before);
            assert_eq!(std::fs::read_dir(home.path().join(".bcn-bridge/bots")).unwrap().count(), 0);
        }
    }
}

#[tokio::test]
async fn uncertain_or_unrelated_errors_never_retry_or_replace_configuration() {
    for (status, body) in [
        (StatusCode::INTERNAL_SERVER_ERROR, CONFLICT),
        (StatusCode::UNAUTHORIZED, CONFLICT),
        (StatusCode::TOO_MANY_REQUESTS, CONFLICT),
        (StatusCode::CONFLICT, r#"{"code":40900,"data":{"error_code":"unrelated_conflict"}}"#),
        (StatusCode::CONFLICT, r#"{"code":40900,"message":"registration_conflict"}"#),
        (StatusCode::CONFLICT, "not JSON"),
        (StatusCode::CREATED, "truncated success"),
        (StatusCode::CREATED, r#"{"code":20100,"data":{}}"#),
    ] {
        let home = tempfile::tempdir().unwrap();
        let before = bootstrap(home.path());
        let api = SubbotApi::new(vec![Reply::Error(status, body), Reply::Success]).await;
        let output = api.command(home.path()).output().await.unwrap();
        assert!(!output.status.success(), "{status}: {}", diagnostic(&output));
        assert_eq!(api.calls().len(), 1);
        assert_eq!(std::fs::read_to_string(home.path().join(".bcn-bridge/bridge.toml")).unwrap(), before);
        assert!(!diagnostic(&output).contains(BOT_TOKEN));
    }
}

#[tokio::test]
async fn explicit_cwd_is_preserved_but_cannot_be_reused_by_a_sibling() {
    let home = tempfile::tempdir().unwrap();
    let project = home.path().join("project");
    std::fs::create_dir(&project).unwrap();
    let api = SubbotApi::new(vec![Reply::Success]).await;
    let output = api.command(home.path()).args(["--cwd", "project"]).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    assert_eq!(saved(home.path())["bot"][0]["cwd"].as_str(), project.canonicalize().unwrap().to_str());
    let before = std::fs::read_to_string(home.path().join(".bcn-bridge/bridge.toml")).unwrap();
    let output = api.command(home.path()).arg("--cwd").arg(&project).output().await.unwrap();
    assert!(!output.status.success());
    assert!(diagnostic(&output).contains("exclusive working directories"));
    #[cfg(unix)] {
        let alias = home.path().join("alias");
        std::os::unix::fs::symlink(&project, &alias).unwrap();
        let output = api.command(home.path()).arg("--cwd").arg(alias).output().await.unwrap();
        assert!(!output.status.success());
        assert!(diagnostic(&output).contains("exclusive working directories"));
    }
    assert_eq!(api.calls().len(), 1, "duplicate cwd must fail before contacting registration");
    assert_eq!(std::fs::read_to_string(home.path().join(".bcn-bridge/bridge.toml")).unwrap(), before);
    assert!(!home.path().join(".bcn-bridge/bots").exists());
}

#[tokio::test]
async fn appending_preserves_legacy_slugs_and_workspace_contents() {
    let home = tempfile::tempdir().unwrap();
    let api = SubbotApi::new(vec![Reply::Success, Reply::Success]).await;
    let old_cwd = home.path().join("workspace");
    std::fs::create_dir(&old_cwd).unwrap();
    let output = api.command(home.path()).arg("--cwd").arg(&old_cwd).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let mut document = saved(home.path());
    let slug = "a".repeat(32);
    document["bot"][0]["bot_slug"] = slug.clone().into();
    document["bot"][0]["provider_bot_ref"] = format!("parent:region:{slug}").into();
    std::fs::write(home.path().join(".bcn-bridge/bridge.toml"), toml::to_string(&document).unwrap()).unwrap();
    std::fs::write(old_cwd.join("keep"), "legacy data").unwrap();
    let output = api.command(home.path()).output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    assert_eq!(saved(home.path())["bot"][0], document["bot"][0]);
    assert_eq!(std::fs::read_to_string(old_cwd.join("keep")).unwrap(), "legacy data");
}

#[tokio::test]
async fn workspace_root_failure_happens_before_registration() {
    let home = tempfile::tempdir().unwrap();
    let before = bootstrap(home.path());
    std::fs::write(home.path().join(".bcn-bridge/bots"), "not a directory").unwrap();
    let api = SubbotApi::new(vec![]).await;
    let output = api.command(home.path()).output().await.unwrap();
    assert!(!output.status.success());
    assert!(api.calls().is_empty());
    assert_eq!(std::fs::read_to_string(home.path().join(".bcn-bridge/bridge.toml")).unwrap(), before);
}

#[tokio::test]
async fn post_registration_workspace_failure_keeps_private_recovery_and_existing_data() {
    let home = tempfile::tempdir().unwrap();
    let before = bootstrap(home.path());
    let cwd = home.path().join(".bcn-bridge/bots/bot_registered_0001");
    std::fs::create_dir_all(&cwd).unwrap();
    std::fs::write(cwd.join("keep"), "do not overwrite").unwrap();
    let api = SubbotApi::new(vec![Reply::Success]).await;
    let output = api.command(home.path()).output().await.unwrap();
    assert!(!output.status.success());
    assert!(diagnostic(&output).contains("private recovery configuration saved"));
    assert!(!diagnostic(&output).contains(BOT_TOKEN));
    assert_eq!(api.calls().len(), 1);
    assert_eq!(std::fs::read_to_string(home.path().join(".bcn-bridge/bridge.toml")).unwrap(), before);
    assert_eq!(std::fs::read_to_string(cwd.join("keep")).unwrap(), "do not overwrite");
    let recovery = std::fs::read_dir(home.path().join(".bcn-bridge")).unwrap().map(|entry| entry.unwrap().path())
        .find(|path| path.file_name().unwrap().to_string_lossy().starts_with(".bridge-registration-")).unwrap();
    let document: toml::Value = toml::from_str(&std::fs::read_to_string(&recovery).unwrap()).unwrap();
    assert_eq!(document["bot"][0]["token"].as_str(), Some(BOT_TOKEN));
    assert_eq!(document["bot"][0]["cwd"].as_str(), cwd.canonicalize().unwrap().to_str());
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(recovery).unwrap().permissions().mode() & 0o777, 0o600);
    }
}

#[tokio::test]
async fn returned_bot_ids_cannot_escape_the_workspace_root() {
    for id in ["../escaped", "/tmp/escaped", "..", "parent\\escaped"] {
        let home = tempfile::tempdir().unwrap();
        let before = bootstrap(home.path());
        let api = SubbotApi::new(vec![Reply::BotId(id)]).await;
        let output = api.command(home.path()).output().await.unwrap();
        assert!(!output.status.success());
        assert!(diagnostic(&output).contains("invalid credentials or a mismatched binding"));
        assert_eq!(api.calls().len(), 1);
        assert_eq!(std::fs::read_to_string(home.path().join(".bcn-bridge/bridge.toml")).unwrap(), before);
        assert_eq!(std::fs::read_dir(home.path().join(".bcn-bridge/bots")).unwrap().count(), 0);
    }
}

#[tokio::test]
async fn gateway_subbots_use_the_same_uuid_workspace_policy() {
    let home = tempfile::tempdir().unwrap();
    let api = SubbotApi::new(vec![Reply::Success]).await;
    let output = register_as(home.path(), &["--provider-auth", "static-bearer", "--provider-bot-ref", "parent:region",
        "--api-url", &api.base, "--mode", "gateway", "--webhook-url", "https://hook.example/subbot"],
        "agentpass-subbots").output().await.unwrap();
    assert!(output.status.success(), "{}", diagnostic(&output));
    let bot = &saved(home.path())["bot"][0];
    let cwd = home.path().join(".bcn-bridge/bots/bot_registered_0001");
    assert_eq!(bot["cwd"].as_str(), cwd.canonicalize().unwrap().to_str());
    assert_eq!(api.calls()[0]["mode"], "gateway");
    assert!(!home.path().join("workspace").exists());
}

#[tokio::test]
async fn connection_loss_after_post_never_retries_an_unknown_outcome() {
    use tokio::io::AsyncReadExt;
    let home = tempfile::tempdir().unwrap();
    let before = bootstrap(home.path());
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let calls: Calls = Arc::default();
    let captured = calls.clone();
    let task = tokio::spawn(async move {
        loop {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                request.push(stream.read_u8().await.unwrap());
            }
            captured.lock().unwrap().push(HashMap::new());
            // The server may have created a Bot, but no response reaches the client.
            drop(stream);
        }
    });
    let api = SubbotApi { base, calls, task };
    let output = api.command(home.path()).output().await.unwrap();
    assert!(!output.status.success());
    assert!(diagnostic(&output).contains("outcome is unknown"));
    assert_eq!(api.calls().len(), 1);
    assert_eq!(std::fs::read_to_string(home.path().join(".bcn-bridge/bridge.toml")).unwrap(), before);
}
