//! CLI --help and --version tests

use assert_cmd::Command;
use predicates::prelude::*;

#[test]
fn test_help_runs() {
    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.arg("--help");
    cmd.assert()
        .success()
        .stdout(predicate::str::contains("Bot Coordination Service"));
}

#[test]
fn test_version_runs() {
    let mut cmd = Command::cargo_bin("bcs-cli").unwrap();
    cmd.arg("--version");
    cmd.assert().success();
}
