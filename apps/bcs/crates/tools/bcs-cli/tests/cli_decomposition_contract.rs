//! Binary-entry ordering regressions for the CLI decomposition.
use assert_cmd::Command;
use std::net::TcpListener;
use std::path::Path;
use std::time::Duration;

fn isolated(home: &Path) -> Command {
    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.env_clear()
        .env("HOME", home)
        .env("NO_COLOR", "1");
    cmd.timeout(Duration::from_secs(5));
    cmd
}

fn assert_no_request(listener: &TcpListener) {
    match listener.accept() {
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {}
        other => panic!("unexpected API/probe connection: {other:?}"),
    }
}

#[test]
fn preview_skips_caller_validation_auth_and_log_creation() {
    let home = tempfile::tempdir().unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let yaml = home.path().join("definition.yaml");
    std::fs::write(&yaml, "name: 离线预览\n").unwrap();
    let bot = home.path().join("bot");
    std::fs::create_dir_all(bot.join(".bcs")).unwrap();
    std::fs::write(bot.join(".bcs/session.json"), "not JSON").unwrap();
    let output = isolated(home.path())
        .env("BOT_DATA_DIR", &bot)
        .env("BCN_ACTOR_BOT_ID", " ")
        .env("BCN_AUTH_MODE", "agentpass-subbots")
        .env("tc_sdb_nenv", "office")
        .args(["--url", &url, "--json", "collaboration", "preview"])
        .arg(yaml)
        .args(["--document-id", "11111111-1111-4111-8111-111111111111",
               "--snapshot-id", "22222222-2222-4222-8222-222222222222"])
        .assert().success().get_output().clone();
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["snapshot"]["definitionYaml"], "name: 离线预览\n");
    assert!(output.stderr.is_empty());
    assert!(!home.path().join(".bcs/logs").exists());
    assert_no_request(&listener);
}

#[test]
fn handled_office_auth_failure_does_not_dispatch_health() {
    for mode in [None, Some("--json"), Some("--no-json")] {
        let home = tempfile::tempdir().unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let mut cmd = isolated(home.path());
        cmd.env("tc_sdb_nenv", "office").args(["--url", &url]);
        if let Some(flag) = mode { cmd.arg(flag); }
        cmd.arg("health");
        let assertion = if mode == Some("--no-json") {
            cmd.assert().failure().code(1)
        } else {
            cmd.assert().success()
        };
        let output = assertion.get_output();
        if mode == Some("--no-json") {
            assert!(output.stdout.is_empty());
            assert!(String::from_utf8_lossy(&output.stderr).contains("not available in the public build"));
        } else {
            let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
            assert_eq!(value["status"], "auth_failed");
            assert!(value["message"].as_str().unwrap().contains("not available in the public build"));
            assert!(output.stderr.is_empty());
        }
        assert_no_request(&listener);
    }
}
