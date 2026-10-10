#![cfg(unix)]

use std::{fs, net::TcpListener, os::unix::fs::PermissionsExt, path::{Path, PathBuf}, process::{Output, Stdio}, time::Duration};

use serde_json::{Value, json};
use tokio::{process::Command, time::{Instant, sleep, timeout}};

struct Fixture { home: tempfile::TempDir, config: PathBuf, address: String }

impl Fixture {
    fn new() -> Self {
        let home = tempfile::Builder::new().prefix("bridge-daemon-").tempdir_in("/tmp").unwrap();
        let socket = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = socket.local_addr().unwrap().to_string();
        let config = home.path().join("custom.toml");
        fs::write(&config, format!(r#"
provider_id = "test-provider"
listen = "{address}"
state_path = "state.sqlite3"
[[bot]]
provider_bot_ref = "test-agent"
engine = "claude-code"
cwd = {}
"#, json!(home.path()))).unwrap();
        Self { home, config, address }
    }
    fn command(&self) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_bcs-bridge"));
        command.current_dir(self.home.path()).env("HOME", self.home.path())
            .env_remove("BRIDGE_CONFIG")
            .env("BCS_BRIDGE_PROVIDER_TOKENS", r#"{"test-provider":"test-webhook-secret"}"#)
            .env("RUST_LOG", "info").kill_on_drop(true);
        command
    }
    fn runtime(&self, name: &str) -> PathBuf { self.home.path().join(".bcn-bridge").join(name) }
    async fn output(&self, args: &[&str]) -> Output {
        timeout(Duration::from_secs(20), self.command().args(args).output()).await
            .expect("CLI must return promptly").unwrap()
    }
    async fn daemon(&self, flag: &str) -> Output {
        self.output(&["start", flag, "--config", self.config.to_str().unwrap()]).await
    }
    fn record(&self) -> Value {
        serde_json::from_slice(&fs::read(self.runtime("bridge.pid.json")).unwrap()).unwrap()
    }
    async fn ping(&self) -> bool {
        let client = reqwest::Client::builder().no_proxy().timeout(Duration::from_millis(200)).build().unwrap();
        match client.post(format!("http://{}/webhook", self.address)).bearer_auth("test-webhook-secret")
            .json(&json!({"type":"req","id":"ping","method":"bot.ping",
                "to_bot":{"provider_id":"test-provider","provider_bot_ref":"test-agent"}})).send().await {
            Ok(response) => response.status().is_success(),
            Err(_) => false,
        }
    }
    async fn wait_ready(&self) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !self.ping().await {
            assert!(Instant::now() < deadline, "service did not start");
            sleep(Duration::from_millis(25)).await;
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        // Keep real background services scoped to their temporary HOME, including assertion failures.
        let _ = std::process::Command::new(env!("CARGO_BIN_EXE_bcs-bridge"))
            .env("HOME", self.home.path()).arg("stop")
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status();
    }
}

fn diagnostic(output: &Output) -> String {
    format!("{}{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr))
}
fn success(output: &Output) { assert!(output.status.success(), "{}", diagnostic(output)); }

#[tokio::test]
async fn lifecycle_help_and_stopped_commands_need_no_config_or_credentials() {
    let fixture = Fixture::new();
    let help = fixture.output(&["start", "--help"]).await;
    success(&help);
    assert!(diagnostic(&help).contains("--daemon"));
    for command in ["status", "stop"] {
        let output = fixture.command().arg(command).env("BRIDGE_CONFIG", "/missing/config.toml")
            .env("BCS_BRIDGE_PROVIDER_TOKENS", "invalid").output().await.unwrap();
        success(&output);
        assert!(diagnostic(&output).contains("stopped"));
    }
    assert!(!fixture.runtime("bridge.lock").exists());
}

#[allow(unsafe_code)] // Read the test-owned daemon's session ID without signalling it.
#[tokio::test]
async fn daemon_is_ready_detached_and_can_stop_after_config_is_deleted() {
    for flag in ["--daemon", "--deamon"] {
        let fixture = Fixture::new();
        let output = fixture.daemon(flag).await;
        success(&output);
        assert!(fixture.ping().await, "daemon returned before webhook became ready");
        let record = fixture.record();
        let pid = record["pid"].as_u64().unwrap() as i32;
        assert_eq!(unsafe { libc::getsid(pid) }, pid);
        assert_eq!(Path::new(record["config"].as_str().unwrap()), fixture.config.canonicalize().unwrap());
        for name in ["bridge.pid.json", "bridge.log", "bridge.lock", "bridge.sock"] {
            assert_eq!(fs::metadata(fixture.runtime(name)).unwrap().permissions().mode() & 0o777, 0o600);
        }
        fs::remove_file(&fixture.config).unwrap();
        let status = fixture.command().arg("status").env("BRIDGE_CONFIG", "/missing")
            .env("BCS_BRIDGE_PROVIDER_TOKENS", "invalid").output().await.unwrap();
        success(&status);
        assert!(diagnostic(&status).contains("running"));
        assert!(diagnostic(&status).contains(&pid.to_string()));
        success(&fixture.output(&["stop"]).await);
        assert!(!fixture.ping().await);
        assert!(!fixture.runtime("bridge.pid.json").exists());
        assert!(fixture.runtime("bridge.lock").exists(), "never unlink the lock inode");
        assert!(diagnostic(&fixture.output(&["status"]).await).contains("stopped"));
        assert!(!fs::read_to_string(fixture.runtime("bridge.log")).unwrap().contains("test-webhook-secret"));
    }
}

#[tokio::test]
async fn foreground_and_background_starts_share_one_lock_across_configurations() {
    let fixture = Fixture::new();
    let mut foreground = fixture.command().arg("start").arg("--config").arg(&fixture.config)
        .stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap();
    fixture.wait_ready().await;
    let other = fixture.home.path().join("other.toml");
    fs::copy(&fixture.config, &other).unwrap();
    let pid = fixture.record()["pid"].as_u64().unwrap();
    assert_eq!(pid, foreground.id().unwrap() as u64);
    for background in [false, true] {
        let mut cmd = fixture.command();
        cmd.arg("start").arg("--config").arg(&other);
        if background { cmd.arg("--daemon"); }
        let output = timeout(Duration::from_secs(10), cmd.output()).await.unwrap().unwrap();
        assert!(!output.status.success());
        assert!(diagnostic(&output).contains("already"), "{}", diagnostic(&output));
        assert_eq!(fixture.record()["pid"].as_u64().unwrap(), pid);
    }
    success(&fixture.output(&["stop"]).await);
    assert!(timeout(Duration::from_secs(5), foreground.wait()).await.unwrap().unwrap().success());
    success(&fixture.daemon("--daemon").await);
    let duplicate = fixture.output(&["start", "--config", other.to_str().unwrap()]).await;
    assert!(!duplicate.status.success());
    assert!(diagnostic(&duplicate).contains("already"));
    success(&fixture.output(&["stop"]).await);
}

#[tokio::test]
async fn concurrent_daemon_starts_allow_exactly_one_instance() {
    let fixture = Fixture::new();
    let (a, b) = tokio::join!(fixture.daemon("--daemon"), fixture.daemon("--daemon"));
    assert_ne!(a.status.success(), b.status.success(), "a={} b={}", diagnostic(&a), diagnostic(&b));
    assert!(fixture.ping().await);
    success(&fixture.output(&["stop"]).await);
}

#[tokio::test]
async fn failed_start_releases_lock_and_does_not_report_success() {
    let mut fixture = Fixture::new();
    let occupied = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = occupied.local_addr().unwrap().to_string();
    let config = fs::read_to_string(&fixture.config).unwrap().replace(&fixture.address, &address);
    fs::write(&fixture.config, config).unwrap();
    fixture.address = address;
    let output = fixture.daemon("--daemon").await;
    assert!(!output.status.success());
    assert!(diagnostic(&output).contains("bridge.log"), "{}", diagnostic(&output));
    assert!(diagnostic(&fixture.output(&["status"]).await).contains("stopped"));
    assert!(!fixture.runtime("bridge.pid.json").exists());
    drop(occupied);
    success(&fixture.daemon("--daemon").await);
    success(&fixture.output(&["stop"]).await);
}

#[tokio::test]
async fn stale_or_unverifiable_pid_never_causes_an_unrelated_process_to_be_signalled() {
    let fixture = Fixture::new();
    let mut unrelated = Command::new("sleep").arg("30").kill_on_drop(true).spawn().unwrap();
    fs::create_dir_all(fixture.runtime("")).unwrap();
    let lock = fs::OpenOptions::new().read(true).write(true).create(true).truncate(false)
        .open(fixture.runtime("bridge.lock")).unwrap();
    let stale = json!({"pid":unrelated.id().unwrap(),"instance_id":"old-instance", "started_at":0,"config":"/old/config"});
    fs::write(fixture.runtime("bridge.pid.json"), stale.to_string()).unwrap();
    success(&fixture.output(&["stop"]).await);
    assert!(diagnostic(&fixture.output(&["status"]).await).contains("stopped"));
    lock.try_lock().unwrap();
    assert!(!fixture.output(&["stop"]).await.status.success());
    let status = fixture.output(&["status"]).await;
    assert!(!status.status.success());
    assert!(diagnostic(&status).contains("unknown"), "{}", diagnostic(&status));
    assert!(unrelated.try_wait().unwrap().is_none());
    drop(lock);
    unrelated.kill().await.unwrap();
    success(&fixture.daemon("--daemon").await);
    success(&fixture.output(&["stop"]).await);
}

#[tokio::test]
async fn crashed_daemon_can_be_started_again_despite_stale_runtime_files() {
    let fixture = Fixture::new();
    success(&fixture.daemon("--daemon").await);
    let pid = fixture.record()["pid"].as_u64().unwrap();
    assert!(Command::new("kill").args(["-KILL", &pid.to_string()]).status().await.unwrap().success());
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let output = fixture.output(&["status"]).await;
        if output.status.success() && diagnostic(&output).contains("stopped") { break; }
        assert!(Instant::now() < deadline);
        sleep(Duration::from_millis(25)).await;
    }
    success(&fixture.daemon("--daemon").await);
    success(&fixture.output(&["stop"]).await);
}
