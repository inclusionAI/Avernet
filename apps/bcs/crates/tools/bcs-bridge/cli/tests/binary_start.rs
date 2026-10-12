/// Task 15 — binary entrypoint smoke: the `bcs-bridge` binary loads config
/// from `BRIDGE_CONFIG`, binds `config.listen`, answers `bot.ping` with HTTP 200,
/// and exits 0 on SIGTERM (axum graceful shutdown → `RunRegistry::abort_all`).
///
/// Uses the fixed test port 21999 (pick-port-0 isn't reachable through the
/// config, which needs a literal `SocketAddr`). If 21999 is already taken the
/// child exits early at `bind`; the poll loop detects that via `try_wait` and
/// surfaces a clear `early exit` panic instead of silently dead-waiting 5s.
/// The subprocess's stdout/stderr are piped to null so a successful run emits
/// nothing into `cargo test`'s stream (pristine output).
#[allow(unsafe_code)] // libc::kill (deliver SIGTERM) is an unsafe extern call
#[tokio::test]
async fn binary_starts_and_serves_ping() {
    let dir = tempfile::tempdir().unwrap();
    let cfg_path = dir.path().join("bridge.toml");
    std::fs::write(
        &cfg_path,
        r#"
provider_id = "bridge-1"
listen = "127.0.0.1:21999"
[[bot]]
provider_bot_ref = "worker-1"
engine = "claude-code"
cwd = "/tmp"
"#,
    )
    .unwrap();
    let bin = env!("CARGO_BIN_EXE_bcs-bridge");
    let mut child = std::process::Command::new(bin)
        .arg("start")
        .env("BRIDGE_CONFIG", &cfg_path)
        .env("BCS_BRIDGE_PROVIDER_TOKENS", r#"{"bridge-1":"tok-b2p"}"#)
        .env("HOME", dir.path())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .unwrap();

    // 轮询直到端口就绪（最多 5s）。每次迭代先确认子进程仍在运行——若它在
    // bind 前就退出（端口被占用 / 配置非法），try_wait 会让测试明确失败。
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let mut ok = false;
    for _ in 0..50 {
        if let Some(status) = child.try_wait().unwrap() {
            panic!("bcs-bridge exited before binding 21999: {status} (port taken?)");
        }
        let resp = client
            .post("http://127.0.0.1:21999/webhook")
            .bearer_auth("tok-b2p")
            .json(&serde_json::json!({"type":"req","id":"p1","method":"bot.ping",
                "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"}}))
            .send()
            .await;
        if let Ok(r) = resp {
            if r.status() == 200 {
                ok = true;
                break;
            }
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    assert!(ok, "ping should succeed on port 21999 while bcs-bridge runs");

    // 优雅退出：SIGTERM 应让进程经 graceful-shutdown 路径干净退出（exit 0）。
    unsafe { libc::kill(child.id() as i32, libc::SIGTERM) };
    let status = tokio::time::timeout(
        std::time::Duration::from_secs(5),
        tokio::task::spawn_blocking(move || child.wait()),
    )
    .await
    .expect("process exits within 5s")
    .unwrap()
    .unwrap();
    assert!(status.success(), "graceful shutdown should yield exit 0, got {status}");
}
