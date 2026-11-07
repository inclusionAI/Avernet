use assert_cmd::Command;
use serde_json::json;
use std::process::Output;
use std::time::Duration;
use tempfile::TempDir;
use wiremock::MockServer;

pub struct Cli {
    pub server: MockServer,
    pub home: TempDir,
}

impl Cli {
    pub async fn new() -> Self {
        let server = MockServer::start().await;
        let home = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(home.path().join(".bcs")).unwrap();
        std::fs::write(home.path().join(".bcs/session.json"), json!({
            "bot_uuid": "bot-source", "token": "test-command-token", "bcs_url": server.uri(),
        }).to_string()).unwrap();
        Self { server, home }
    }

    pub fn command(&self, flags: &[&str], args: &[&str]) -> Command {
        let mut command = Command::cargo_bin("bcs-cli").unwrap();
        command.env_clear();
        // Preserve instrumentation for the real CLI child process, not ambient
        // auth/session state. Without this, env_clear silently loses coverage.
        if let Some(profile) = std::env::var_os("LLVM_PROFILE_FILE") {
            command.env("LLVM_PROFILE_FILE", profile);
        }
        command.env("HOME", self.home.path())
            .env("BOT_DATA_DIR", self.home.path())
            .env("BCN_BOT_TOKEN", "test-command-token")
            .env("tc_sdb_nenv", "production")
            .env("NO_COLOR", "1")
            .args(["--url", &self.server.uri()])
            .args(flags).args(args).timeout(Duration::from_secs(10));
        command
    }

    pub fn ok(&self, flags: &[&str], args: &[&str]) -> Output {
        self.command(flags, args).assert().success().get_output().clone()
    }

    pub fn fail(&self, flags: &[&str], args: &[&str], needle: &str) {
        let output = self.command(flags, args).assert().failure().get_output().clone();
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(stderr.contains(needle), "args={args:?}, stderr={stderr}");
    }
}

pub fn stdout(output: &Output) -> String {
    String::from_utf8(output.stdout.clone()).unwrap()
}
